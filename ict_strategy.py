# =====================================================
# ICT Trading Bot — Pure SMC Strategy Engine v4.0
# =====================================================
#
# SIFIRDAN YAZILDI: Narrative → POI → Trigger mimarisi
#
# v3.x'ten FARKLAR:
#   - 5 sıralı gate YOK → Bağlamsal (contextual) analiz
#   - Mum sayısı bekleme YOK → Fiyat POI'ye geldiğinde tetiklenir
#   - LIMIT emir YOK → MARKET giriş (trigger anında)
#   - Perakende gösterge YOK (RSI, MACD, EMA, SMA, Bollinger, ADX)
#   - Puanlama / Scoring sistemi YOK
#   - Tamamen Boolean (Evet / Hayır) mantık
#
# MİMARİ (3 Katman):
#
#   KATMAN 1 — NARRATIVE (Hikaye)
#       4H yapı analizi → LONG / SHORT / NÖTR
#       Draw on Liquidity hedefi (fiyat nereye çekilecek?)
#       Daily ile uyum kontrolü
#
#   KATMAN 2 — POI TESPİTİ (Points of Interest)
#       OB + FVG + Likidite çakışma bölgeleri
#       Yolda engel taraması (karşı OB, FVG, psikolojik seviye)
#       RR ön hesaplaması (engeller dahil)
#
#   KATMAN 3 — TRIGGER (Tetik)
#       Fiyat POI'ye yaklaştığında:
#         a) Sweep + rejection (mum KAPANIŞI ile doğrulanır)
#         b) MSS (micro structure shift — CHoCH)
#         c) 2-3 ardışık displacement mumu (tek dev mum = GİRME)
#       Trigger → anında SIGNAL döner → trade_manager MARKET giriş yapar
#
# FAKE WICK KORUMALARI:
#   - Mum kapanışı doğru tarafta mı? (sweep seviyesinin ters tarafı)
#   - Tek mum > 3x ATR → anormal → BEKLEyin
#   - Body/wick oranı rejection mi gösteriyor?
#
# ENGEL TARAMASI:
#   - TP yolunda karşı OB, FVG, psikolojik seviye (round number)
#   - Engel yakınsa → TP öne çekilir veya trade reddedilir
#
# =====================================================

import logging
import numpy as np
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any, Tuple

from config import ICT_PARAMS
from database import get_bot_param

logger = logging.getLogger("ICT-Bot.Strategy")


# ═════════════════════════════════════════════════════
#  ANA SINIF
# ═════════════════════════════════════════════════════

class ICTStrategy:
    """
    Saf ICT / Smart Money Concepts v4.0 — Narrative → POI → Trigger.
    
    Perakende gösterge SIFIR. Puanlama SIFIR. Boolean mantık.
    """

    _INT_PARAMS = {
        "swing_lookback", "ob_max_age_candles", "fvg_max_age_candles",
        "liquidity_min_touches", "max_concurrent_trades",
        "max_same_direction_trades", "signal_cooldown_minutes",
    }

    def __init__(self):
        self.params = {}
        self._load_params()
        # Aktif POI listesi (coin bazında)
        self._active_pois: Dict[str, List[Dict]] = {}
        logger.info("ICTStrategy v4.0 başlatıldı — Narrative → POI → Trigger Protocol")

    def _load_params(self):
        for key, default in ICT_PARAMS.items():
            val = get_bot_param(key, default)
            if key in self._INT_PARAMS:
                val = int(val)
            self.params[key] = val

    def reload_params(self):
        self._load_params()
        logger.info("ICT parametreleri yeniden yüklendi")

    # =================================================================
    #  BÖLÜM 1 — YARDIMCI FONKSİYONLAR
    # =================================================================

    def _calc_atr(self, df, period: int = 14) -> float:
        """ATR (Average True Range) — volatilite ölçümü."""
        if df is None or len(df) < period + 1:
            return 0.0
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        tr = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(
                np.abs(highs[1:] - closes[:-1]),
                np.abs(lows[1:] - closes[:-1])
            )
        )
        if len(tr) < period:
            return float(np.mean(tr)) if len(tr) > 0 else 0.0
        return float(np.mean(tr[-period:]))

    def _find_swing_points(self, df, lookback: int = 5) -> Tuple[List[Dict], List[Dict]]:
        """
        Swing High ve Swing Low noktalarını bul.
        
        Fractal yöntemi: bir mum, sol ve sağ komşularının hepsinden
        yüksek (swing high) veya düşük (swing low) ise swing noktası sayılır.
        
        Lookback=5: Her iki tarafta 5 mum kontrol (toplam 11 mum pencere).
        """
        if df is None or len(df) < lookback * 2 + 1:
            return [], []

        swing_highs = []
        swing_lows = []
        highs = df["high"].values
        lows = df["low"].values
        
        for i in range(lookback, len(df) - lookback):
            # Swing High: tüm komşulardan yüksek
            is_high = True
            for j in range(1, lookback + 1):
                if highs[i] <= highs[i - j] or highs[i] <= highs[i + j]:
                    is_high = False
                    break
            if is_high:
                swing_highs.append({
                    "index": i,
                    "price": float(highs[i]),
                    "timestamp": str(df.iloc[i].get("timestamp", "")),
                })

            # Swing Low: tüm komşulardan düşük
            is_low = True
            for j in range(1, lookback + 1):
                if lows[i] >= lows[i - j] or lows[i] >= lows[i + j]:
                    is_low = False
                    break
            if is_low:
                swing_lows.append({
                    "index": i,
                    "price": float(lows[i]),
                    "timestamp": str(df.iloc[i].get("timestamp", "")),
                })

        return swing_highs, swing_lows

    def _detect_structure(self, swing_highs: List[Dict], swing_lows: List[Dict]) -> Dict:
        """
        Market Structure analizi — BOS ve CHoCH tespiti.
        
        Bullish structure:
          - Higher High (HH) + Higher Low (HL)
          - BOS = son swing high'ın body ile kırılması
          
        Bearish structure:
          - Lower Low (LL) + Lower High (LH)
          - BOS = son swing low'un body ile kırılması
          
        CHoCH (Change of Character):
          - Bullish trend'de ilk Lower Low = Bearish CHoCH
          - Bearish trend'de ilk Higher High = Bullish CHoCH
          - CHoCH = trend dönüşü sinyali
        
        Returns:
            {"bias": "LONG"/"SHORT"/"NEUTRAL", 
             "bos_count": int, "choch_detected": bool,
             "last_bos_price": float, "last_swing_high": float,
             "last_swing_low": float, "structure_quality": "STRONG"/"WEAK"/"NEUTRAL"}
        """
        result = {
            "bias": "NEUTRAL",
            "bos_count": 0,
            "choch_detected": False,
            "last_bos_price": 0.0,
            "last_swing_high": 0.0,
            "last_swing_low": 0.0,
            "structure_quality": "NEUTRAL",
        }

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return result

        # Son swing noktalarını zamana göre sırala
        sh = sorted(swing_highs, key=lambda x: x["index"])
        sl = sorted(swing_lows, key=lambda x: x["index"])

        result["last_swing_high"] = sh[-1]["price"]
        result["last_swing_low"] = sl[-1]["price"]

        # Son 8 swing noktasını analiz et
        all_swings = []
        for s in sh:
            all_swings.append({"type": "high", **s})
        for s in sl:
            all_swings.append({"type": "low", **s})
        all_swings.sort(key=lambda x: x["index"])

        recent = all_swings[-8:] if len(all_swings) >= 8 else all_swings

        # HH/HL/LH/LL dizisi
        hh_count = 0
        hl_count = 0
        ll_count = 0
        lh_count = 0

        prev_highs = [s for s in recent if s["type"] == "high"]
        prev_lows = [s for s in recent if s["type"] == "low"]

        for i in range(1, len(prev_highs)):
            if prev_highs[i]["price"] > prev_highs[i-1]["price"]:
                hh_count += 1
            elif prev_highs[i]["price"] < prev_highs[i-1]["price"]:
                lh_count += 1

        for i in range(1, len(prev_lows)):
            if prev_lows[i]["price"] > prev_lows[i-1]["price"]:
                hl_count += 1
            elif prev_lows[i]["price"] < prev_lows[i-1]["price"]:
                ll_count += 1

        # Bias belirleme
        bull_score = hh_count + hl_count
        bear_score = ll_count + lh_count

        if bull_score >= 2 and bull_score > bear_score:
            result["bias"] = "LONG"
            result["bos_count"] = hh_count
            if bull_score >= 3:
                result["structure_quality"] = "STRONG"
            else:
                result["structure_quality"] = "WEAK"
        elif bear_score >= 2 and bear_score > bull_score:
            result["bias"] = "SHORT"
            result["bos_count"] = ll_count
            if bear_score >= 3:
                result["structure_quality"] = "STRONG"
            else:
                result["structure_quality"] = "WEAK"

        # CHoCH tespiti: son swing'de trend kırıldı mı?
        # NOT: CHoCH bias'ı NEUTRAL yapmaz, sadece kaliteyi düşürür
        # Çünkü tek bir geri çekilme tüm yapıyı geçersiz kılmamalı
        if len(prev_highs) >= 2 and len(prev_lows) >= 2:
            if result["bias"] == "LONG" and prev_lows[-1]["price"] < prev_lows[-2]["price"]:
                result["choch_detected"] = True
                result["structure_quality"] = "WEAK"  # bias korunur, kalite düşer
            elif result["bias"] == "SHORT" and prev_highs[-1]["price"] > prev_highs[-2]["price"]:
                result["choch_detected"] = True
                result["structure_quality"] = "WEAK"  # bias korunur, kalite düşer

        # BOS price
        if result["bias"] == "LONG" and len(prev_highs) >= 2:
            result["last_bos_price"] = prev_highs[-2]["price"]
        elif result["bias"] == "SHORT" and len(prev_lows) >= 2:
            result["last_bos_price"] = prev_lows[-2]["price"]

        return result

    def _find_order_blocks(self, df, bias: str, max_age: int = 30) -> List[Dict]:
        """
        Order Block tespiti — kurumsal alım/satım bölgeleri.
        
        Bullish OB: Son düşüş mumundan SONRA güçlü yükseliş (sweep bölgesinde)
                    → O düşüş mumunun range'i = Bullish OB (destek bölgesi)
        Bearish OB: Son yükseliş mumundan SONRA güçlü düşüş
                    → O yükseliş mumunun range'i = Bearish OB (direnç bölgesi)
        
        Mitigation kontrolü: OB daha önce test edildiyse → geçersiz.
        """
        if df is None or len(df) < 10:
            return []

        obs = []
        opens = df["open"].values
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        
        start_idx = max(0, len(df) - max_age)
        min_body_ratio = self.params.get("ob_body_ratio_min", 0.4)

        for i in range(start_idx + 1, len(df) - 1):
            body = abs(closes[i] - opens[i])
            total_range = highs[i] - lows[i]
            if total_range == 0:
                continue
            body_ratio = body / total_range

            # Sonraki mum analizi
            next_body = abs(closes[i+1] - opens[i+1])
            next_range = highs[i+1] - lows[i+1]
            next_body_ratio = (next_body / next_range) if next_range > 0 else 0

            curr_bullish = closes[i] > opens[i]
            curr_bearish = closes[i] < opens[i]

            # Bullish OB: bearish mum → sonrasında güçlü bullish displacement
            if bias in ("LONG", "NEUTRAL") and curr_bearish and body_ratio >= min_body_ratio:
                if closes[i+1] > opens[i+1] and next_body_ratio >= 0.5:
                    if closes[i+1] > highs[i]:
                        mitigated = False
                        for j in range(i + 2, len(df)):
                            if lows[j] <= lows[i]:
                                mitigated = True
                                break
                        if not mitigated:
                            obs.append({
                                "type": "BULLISH",
                                "high": float(highs[i]),
                                "low": float(lows[i]),
                                "ce": float((highs[i] + lows[i]) / 2),
                                "index": i,
                                "age": len(df) - 1 - i,
                                "mitigated": False,
                            })

            # Bearish OB: bullish mum → sonrasında güçlü bearish displacement
            if bias in ("SHORT", "NEUTRAL") and curr_bullish and body_ratio >= min_body_ratio:
                if closes[i+1] < opens[i+1] and next_body_ratio >= 0.5:
                    if closes[i+1] < lows[i]:
                        mitigated = False
                        for j in range(i + 2, len(df)):
                            if highs[j] >= highs[i]:
                                mitigated = True
                                break
                        if not mitigated:
                            obs.append({
                                "type": "BEARISH",
                                "high": float(highs[i]),
                                "low": float(lows[i]),
                                "ce": float((highs[i] + lows[i]) / 2),
                                "index": i,
                                "age": len(df) - 1 - i,
                                "mitigated": False,
                            })

        return obs

    def _find_fvg(self, df, max_age: int = 20) -> List[Dict]:
        """
        Fair Value Gap (FVG) tespiti — 3 mumlu boşluk.
        
        Bullish FVG: mum[i-1].high < mum[i+1].low → arada boşluk
        Bearish FVG: mum[i-1].low > mum[i+1].high → arada boşluk
        
        CE (Consequent Encroachment) = FVG'nin %50 seviyesi (optimal entry).
        
        Mitigation: Fiyat FVG'ye ulaştıysa = partially/fully mitigated.
        """
        if df is None or len(df) < 5:
            return []

        fvgs = []
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        min_size_pct = self.params.get("fvg_min_size_pct", 0.001)
        start_idx = max(1, len(df) - max_age - 1)

        for i in range(start_idx, len(df) - 1):
            price_ref = closes[i]
            if price_ref == 0:
                continue

            # Bullish FVG
            if highs[i-1] < lows[i+1]:
                gap_size = lows[i+1] - highs[i-1]
                if gap_size / price_ref >= min_size_pct:
                    fvg_high = float(lows[i+1])
                    fvg_low = float(highs[i-1])
                    ce = (fvg_high + fvg_low) / 2

                    mitigated = "FRESH"
                    for j in range(i + 2, len(df)):
                        if lows[j] <= fvg_low:
                            mitigated = "FULL"
                            break
                        elif lows[j] <= ce:
                            mitigated = "PARTIAL"

                    if mitigated != "FULL":
                        fvgs.append({
                            "type": "BULLISH",
                            "high": fvg_high,
                            "low": fvg_low,
                            "ce": float(ce),
                            "index": i,
                            "age": len(df) - 1 - i,
                            "mitigated": mitigated,
                            "size_pct": float(gap_size / price_ref),
                        })

            # Bearish FVG
            if lows[i-1] > highs[i+1]:
                gap_size = lows[i-1] - highs[i+1]
                if gap_size / price_ref >= min_size_pct:
                    fvg_high = float(lows[i-1])
                    fvg_low = float(highs[i+1])
                    ce = (fvg_high + fvg_low) / 2

                    mitigated = "FRESH"
                    for j in range(i + 2, len(df)):
                        if highs[j] >= fvg_high:
                            mitigated = "FULL"
                            break
                        elif highs[j] >= ce:
                            mitigated = "PARTIAL"

                    if mitigated != "FULL":
                        fvgs.append({
                            "type": "BEARISH",
                            "high": fvg_high,
                            "low": fvg_low,
                            "ce": float(ce),
                            "index": i,
                            "age": len(df) - 1 - i,
                            "mitigated": mitigated,
                            "size_pct": float(gap_size / price_ref),
                        })

        return fvgs

    def _find_liquidity_pools(self, swing_highs: List[Dict], swing_lows: List[Dict],
                               current_price: float) -> Dict:
        """
        Likidite havuzları — EQH, EQL, Swing H/L.
        
        Eşit zirveler/dipler = stop-loss yoğunlaşma bölgesi.
        Kurumlar bu bölgeleri hedef alır (likidite avı).
        """
        tolerance = self.params.get("liquidity_equal_tolerance", 0.001)
        result = {"bsl": [], "ssl": [], "nearest_bsl": 0.0, "nearest_ssl": 0.0}

        if not swing_highs or not swing_lows or current_price == 0:
            return result

        # BSL: Fiyatın ÜZERİNDEKİ likidite havuzları
        for sh in swing_highs:
            price = sh["price"]
            if price > current_price:
                eq_count = sum(1 for s in swing_highs
                               if abs(s["price"] - price) / price <= tolerance
                               and s["index"] != sh["index"])
                result["bsl"].append({
                    "price": price,
                    "type": "EQH" if eq_count >= 1 else "SWING_HIGH",
                    "strength": min(eq_count + 1, 5),
                })

        # SSL: Fiyatın ALTINDAKİ likidite havuzları
        for sl_point in swing_lows:
            price = sl_point["price"]
            if price < current_price:
                eq_count = sum(1 for s in swing_lows
                               if abs(s["price"] - price) / price <= tolerance
                               and s["index"] != sl_point["index"])
                result["ssl"].append({
                    "price": price,
                    "type": "EQL" if eq_count >= 1 else "SWING_LOW",
                    "strength": min(eq_count + 1, 5),
                })

        # En yakın hedefler
        if result["bsl"]:
            result["bsl"].sort(key=lambda x: x["price"])
            result["nearest_bsl"] = result["bsl"][0]["price"]
        if result["ssl"]:
            result["ssl"].sort(key=lambda x: -x["price"])
            result["nearest_ssl"] = result["ssl"][0]["price"]

        return result

    def _calculate_premium_discount(self, swing_highs: List[Dict],
                                      swing_lows: List[Dict],
                                      current_price: float) -> Dict:
        """
        Premium / Discount Zone hesaplaması.
        
        Dealing Range = Son swing high ile son swing low arasındaki alan.
        %50 seviyesi (Equilibrium) = (high + low) / 2
        
        Discount Zone (< %50): LONG için uygun
        Premium Zone (> %50): SHORT için uygun
        
        OTE (Optimal Trade Entry): Fib 0.618 — 0.786 bölgesi
        """
        if not swing_highs or not swing_lows:
            return {"zone": "NEUTRAL", "pct": 50.0, "equilibrium": 0.0,
                    "range_high": 0.0, "range_low": 0.0, "in_ote": False}

        range_high = max(s["price"] for s in swing_highs)
        range_low = min(s["price"] for s in swing_lows)
        dealing_range = range_high - range_low

        if dealing_range <= 0:
            return {"zone": "NEUTRAL", "pct": 50.0, "equilibrium": 0.0,
                    "range_high": range_high, "range_low": range_low, "in_ote": False}

        equilibrium = (range_high + range_low) / 2
        position_pct = ((current_price - range_low) / dealing_range) * 100

        # OTE (Fibonacci 0.618 — 0.786 — LONG için dealing range'in altından)
        # LONG OTE: fiyat range_low + 21.4% ile range_low + 38.2% arasında
        # SHORT OTE: fiyat range_high - 21.4% ile range_high - 38.2% arasında
        ote_long_low = range_low + dealing_range * (1 - 0.786)
        ote_long_high = range_low + dealing_range * (1 - 0.618)
        ote_short_low = range_low + dealing_range * 0.618
        ote_short_high = range_low + dealing_range * 0.786

        in_ote_long = ote_long_low <= current_price <= ote_long_high
        in_ote_short = ote_short_low <= current_price <= ote_short_high

        # Zone belirleme
        if position_pct <= 30:
            zone = "DEEP_DISCOUNT"
        elif position_pct <= 50:
            zone = "DISCOUNT"
        elif position_pct >= 70:
            zone = "DEEP_PREMIUM"
        elif position_pct >= 50:
            zone = "PREMIUM"
        else:
            zone = "NEUTRAL"

        return {
            "zone": zone,
            "pct": round(position_pct, 1),
            "equilibrium": float(equilibrium),
            "range_high": float(range_high),
            "range_low": float(range_low),
            "in_ote": in_ote_long or in_ote_short,
            "in_ote_long": in_ote_long,
            "in_ote_short": in_ote_short,
        }

    def _detect_sweep(self, df, swing_highs: List[Dict], swing_lows: List[Dict],
                       bias: str, lookback: int = 30) -> Optional[Dict]:
        """
        Likidite Süpürme (Stop Hunt) tespiti.
        
        ICT'nin kalbi: Kurumlar stop-loss emirlerini tetiklemek için
        fiyatı eski swing noktalarının ötesine iter, sonra asıl yöne döner.
        
        FAKE WICK KORUMASI:
        - Mum KAPANIŞI sweep seviyesinin doğru tarafında olmalı
        - Wick > body * 0.5 (rejection işareti)
        """
        if df is None or len(df) < 5:
            return None

        highs = df["high"].values
        lows = df["low"].values
        opens = df["open"].values
        closes = df["close"].values

        recent_start = max(0, len(df) - lookback)
        best_sweep = None

        if bias == "LONG":
            for sl_point in swing_lows:
                level = sl_point["price"]
                for i in range(recent_start, len(df)):
                    # Fitil seviyenin altına inmiş ama mum üstünde kapanmış
                    if lows[i] < level and closes[i] > level:
                        body = abs(closes[i] - opens[i])
                        lower_wick = min(opens[i], closes[i]) - lows[i]
                        
                        if lower_wick > body * 0.5:
                            sweep_depth = (level - lows[i]) / level
                            
                            if best_sweep is None or i > best_sweep["index"]:
                                best_sweep = {
                                    "direction": "LONG",
                                    "level": float(level),
                                    "sweep_price": float(lows[i]),
                                    "rejection_close": float(closes[i]),
                                    "sweep_depth_pct": float(sweep_depth * 100),
                                    "wick_body_ratio": float(lower_wick / body) if body > 0 else 999,
                                    "index": i,
                                    "candles_ago": len(df) - 1 - i,
                                }

        elif bias == "SHORT":
            for sh_point in swing_highs:
                level = sh_point["price"]
                for i in range(recent_start, len(df)):
                    if highs[i] > level and closes[i] < level:
                        body = abs(closes[i] - opens[i])
                        upper_wick = highs[i] - max(opens[i], closes[i])

                        if upper_wick > body * 0.5:
                            sweep_depth = (highs[i] - level) / level

                            if best_sweep is None or i > best_sweep["index"]:
                                best_sweep = {
                                    "direction": "SHORT",
                                    "level": float(level),
                                    "sweep_price": float(highs[i]),
                                    "rejection_close": float(closes[i]),
                                    "sweep_depth_pct": float(sweep_depth * 100),
                                    "wick_body_ratio": float(upper_wick / body) if body > 0 else 999,
                                    "index": i,
                                    "candles_ago": len(df) - 1 - i,
                                }

        return best_sweep

    def _detect_mss(self, df, bias: str, after_index: int = 0) -> Optional[Dict]:
        """
        MSS (Market Structure Shift) — Micro CHoCH tespiti.
        
        15m'de küçük yapı kırılımı. Sweep sonrası yön değişiminin ilk işareti.
        
        LONG MSS: Son micro swing high'ın body ile kırılması
        SHORT MSS: Son micro swing low'un body ile kırılması
        """
        if df is None or len(df) < 10:
            return None

        micro_highs, micro_lows = self._find_swing_points(df, lookback=3)

        if bias == "LONG":
            relevant_highs = [s for s in micro_highs if s["index"] >= after_index]
            if not relevant_highs:
                return None
            
            target = relevant_highs[-1]
            for i in range(target["index"] + 1, len(df)):
                close_val = float(df.iloc[i]["close"])
                if close_val > target["price"]:
                    return {
                        "direction": "LONG",
                        "break_price": target["price"],
                        "confirm_close": close_val,
                        "index": i,
                        "candles_ago": len(df) - 1 - i,
                    }

        elif bias == "SHORT":
            relevant_lows = [s for s in micro_lows if s["index"] >= after_index]
            if not relevant_lows:
                return None
            
            target = relevant_lows[-1]
            for i in range(target["index"] + 1, len(df)):
                close_val = float(df.iloc[i]["close"])
                if close_val < target["price"]:
                    return {
                        "direction": "SHORT",
                        "break_price": target["price"],
                        "confirm_close": close_val,
                        "index": i,
                        "candles_ago": len(df) - 1 - i,
                    }

        return None

    def _detect_displacement(self, df, bias: str, atr: float,
                              after_index: int = 0) -> Optional[Dict]:
        """
        Displacement tespiti — kurumsal güçlü hareket.
        
        Tek bir dev mum DEĞİL → 2-3 ardışık güçlü mum aranır.
        Tek mum > 3x ATR = anormal volatilite → GİRME (fake olabilir).
        """
        if df is None or len(df) < 5 or atr <= 0:
            return None

        opens = df["open"].values
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        volumes = df["volume"].values if "volume" in df.columns else None

        min_body_ratio = self.params.get("displacement_min_body_ratio", 0.50)
        atr_multiplier = self.params.get("displacement_atr_multiplier", 1.3)

        search_start = max(after_index, len(df) - 20)

        for i in range(search_start, len(df) - 1):
            # Tek dev mum kontrolü (FAKE WICK koruma)
            candle_range = highs[i] - lows[i]
            if candle_range > 3 * atr:
                continue  # 3x ATR'den büyük tek mum = anormal → ATLA

            body = abs(closes[i] - opens[i])
            total_range = highs[i] - lows[i]
            if total_range == 0:
                continue
            body_ratio = body / total_range

            if bias == "LONG" and closes[i] > opens[i] and body_ratio >= min_body_ratio:
                consecutive = 1
                total_move = closes[i] - opens[i]
                start_open = opens[i]
                end_close = closes[i]

                for j in range(i + 1, min(i + 3, len(df))):
                    if closes[j] > opens[j]:
                        b = abs(closes[j] - opens[j])
                        r = highs[j] - lows[j]
                        if r > 0 and b / r >= 0.45:
                            consecutive += 1
                            end_close = closes[j]
                            total_move = end_close - start_open
                        else:
                            break
                    else:
                        break

                if total_move >= atr * atr_multiplier:
                    vol_confirmed = True
                    if volumes is not None and len(volumes) > 20:
                        avg_vol = np.mean(volumes[max(0, i-20):i])
                        curr_vol = volumes[i]
                        vol_confirmed = avg_vol > 0 and curr_vol > avg_vol * 0.8

                    if vol_confirmed:
                        return {
                            "direction": "LONG",
                            "start_index": i,
                            "end_index": min(i + consecutive - 1, len(df) - 1),
                            "consecutive_candles": consecutive,
                            "total_move_pct": float(total_move / start_open * 100) if start_open > 0 else 0,
                            "atr_ratio": float(total_move / atr),
                            "candles_ago": len(df) - 1 - i,
                            "displacement_low": float(lows[i]),
                            "displacement_high": float(end_close),
                        }

            elif bias == "SHORT" and closes[i] < opens[i] and body_ratio >= min_body_ratio:
                consecutive = 1
                total_move = opens[i] - closes[i]
                start_open = opens[i]
                end_close = closes[i]

                for j in range(i + 1, min(i + 3, len(df))):
                    if closes[j] < opens[j]:
                        b = abs(closes[j] - opens[j])
                        r = highs[j] - lows[j]
                        if r > 0 and b / r >= 0.45:
                            consecutive += 1
                            end_close = closes[j]
                            total_move = start_open - end_close
                        else:
                            break
                    else:
                        break

                if total_move >= atr * atr_multiplier:
                    vol_confirmed = True
                    if volumes is not None and len(volumes) > 20:
                        avg_vol = np.mean(volumes[max(0, i-20):i])
                        curr_vol = volumes[i]
                        vol_confirmed = avg_vol > 0 and curr_vol > avg_vol * 0.8

                    if vol_confirmed:
                        return {
                            "direction": "SHORT",
                            "start_index": i,
                            "end_index": min(i + consecutive - 1, len(df) - 1),
                            "consecutive_candles": consecutive,
                            "total_move_pct": float(total_move / start_open * 100) if start_open > 0 else 0,
                            "atr_ratio": float(total_move / atr),
                            "candles_ago": len(df) - 1 - i,
                            "displacement_high": float(highs[i]),
                            "displacement_low": float(end_close),
                        }

        return None

    def _scan_obstacles(self, bias: str, entry: float, tp: float,
                        obs_1h: List[Dict], fvgs_1h: List[Dict],
                        current_price: float) -> Dict:
        """
        TP yolundaki engelleri tara.
        
        LONG: Entry ile TP arasında bearish OB/FVG var mı?
        SHORT: Entry ile TP arasında bullish OB/FVG var mı?
        Psikolojik seviyeler (round number): xx,000 — xx,500
        
        İlk engel TP yolunun ilk %30'undaysa → TP öne çekilir.
        """
        result = {
            "has_obstacle": False,
            "obstacles": [],
            "adjusted_tp": tp,
            "obstacle_distance_pct": 100.0,
        }

        if entry == 0 or tp == 0:
            return result

        tp_distance = abs(tp - entry)
        if tp_distance == 0:
            return result

        obstacles = []

        if bias == "LONG":
            for ob in obs_1h:
                if ob["type"] == "BEARISH" and not ob["mitigated"]:
                    if entry < ob["low"] < tp:
                        dist_from_entry = ob["low"] - entry
                        pct_of_tp = (dist_from_entry / tp_distance) * 100
                        obstacles.append({
                            "type": "BEARISH_OB",
                            "price": ob["low"],
                            "pct_of_tp_distance": round(pct_of_tp, 1),
                        })

            for fvg in fvgs_1h:
                if fvg["type"] == "BEARISH" and fvg["mitigated"] != "FULL":
                    if entry < fvg["low"] < tp:
                        dist_from_entry = fvg["low"] - entry
                        pct_of_tp = (dist_from_entry / tp_distance) * 100
                        obstacles.append({
                            "type": "BEARISH_FVG",
                            "price": fvg["low"],
                            "pct_of_tp_distance": round(pct_of_tp, 1),
                            "age": fvg["age"],
                        })

            step = self._round_number_step(current_price)
            if step > 0:
                low_round = int(entry / step) * step + step
                while low_round < tp:
                    dist_from_entry = low_round - entry
                    pct_of_tp = (dist_from_entry / tp_distance) * 100
                    if 20 < pct_of_tp < 90:
                        obstacles.append({
                            "type": "ROUND_NUMBER",
                            "price": float(low_round),
                            "pct_of_tp_distance": round(pct_of_tp, 1),
                        })
                    low_round += step

        elif bias == "SHORT":
            for ob in obs_1h:
                if ob["type"] == "BULLISH" and not ob["mitigated"]:
                    if tp < ob["high"] < entry:
                        dist_from_entry = entry - ob["high"]
                        pct_of_tp = (dist_from_entry / tp_distance) * 100
                        obstacles.append({
                            "type": "BULLISH_OB",
                            "price": ob["high"],
                            "pct_of_tp_distance": round(pct_of_tp, 1),
                        })

            for fvg in fvgs_1h:
                if fvg["type"] == "BULLISH" and fvg["mitigated"] != "FULL":
                    if tp < fvg["high"] < entry:
                        dist_from_entry = entry - fvg["high"]
                        pct_of_tp = (dist_from_entry / tp_distance) * 100
                        obstacles.append({
                            "type": "BULLISH_FVG",
                            "price": fvg["high"],
                            "pct_of_tp_distance": round(pct_of_tp, 1),
                            "age": fvg["age"],
                        })

            step = self._round_number_step(current_price)
            if step > 0:
                high_round = int(entry / step) * step
                while high_round > tp:
                    dist_from_entry = entry - high_round
                    pct_of_tp = (dist_from_entry / tp_distance) * 100
                    if 20 < pct_of_tp < 90:
                        obstacles.append({
                            "type": "ROUND_NUMBER",
                            "price": float(high_round),
                            "pct_of_tp_distance": round(pct_of_tp, 1),
                        })
                    high_round -= step

        if obstacles:
            obstacles.sort(key=lambda x: x["pct_of_tp_distance"])
            result["has_obstacle"] = True
            result["obstacles"] = obstacles
            result["obstacle_distance_pct"] = obstacles[0]["pct_of_tp_distance"]

            first_obstacle = obstacles[0]
            if first_obstacle["pct_of_tp_distance"] < 30:
                buffer = tp_distance * 0.02
                if bias == "LONG":
                    result["adjusted_tp"] = first_obstacle["price"] - buffer
                else:
                    result["adjusted_tp"] = first_obstacle["price"] + buffer

        return result

    def _round_number_step(self, price: float) -> float:
        """Psikolojik seviye adımı (fiyata göre dinamik)."""
        if price >= 50000:
            return 1000
        elif price >= 10000:
            return 500
        elif price >= 1000:
            return 100
        elif price >= 100:
            return 50
        elif price >= 10:
            return 5
        elif price >= 1:
            return 0.5
        else:
            return 0.05

    def _is_volatile_candle(self, candle_range: float, atr: float) -> bool:
        """Tek mum > 3x ATR = anormal volatilite."""
        return atr > 0 and candle_range > 3 * atr

    # =================================================================
    #  BÖLÜM 1B — OVEREXTENSION + 4H ENGEL KONTROLLERI
    # =================================================================

    def _check_overextension(self, df_1h, bias: str) -> bool:
        """
        1H'da fiyat tek yöne aşırı mı gitmiş? (Geri çekilme olmadan)

        Kripto'da coin %5-10 tek yöne gidip sıfır geri çekilme olmuşsa,
        o yönde giriş = tepeye/dibe giriş. Düzeltme gelecek.

        Kontrol:
          - Son 6 adet 1H mumdan en az 5'i aynı yönde mi?
          - Toplam hareket 1H ATR'nin 3 katından fazla mı?
          - Hiç anlamlı geri çekilme (ATR'nin %40'ı) yok mu?

        True = overextended (aşırı gitmiş, girme)
        False = normal, girilebilir
        """
        if df_1h is None or len(df_1h) < 10:
            return False

        atr_1h = self._calc_atr(df_1h, 14)
        if atr_1h <= 0:
            return False

        recent = df_1h.tail(6)
        opens = recent["open"].values
        closes = recent["close"].values
        highs = recent["high"].values
        lows = recent["low"].values

        # Aynı yönde kaç mum var?
        if bias == "LONG":
            same_dir = sum(1 for o, c in zip(opens, closes) if c > o)
        else:
            same_dir = sum(1 for o, c in zip(opens, closes) if c < o)

        if same_dir < 5:
            return False  # 6 mumdan 5'i aynı yönde değil → normal

        # Toplam hareket
        if bias == "LONG":
            total_move = float(closes[-1]) - float(lows[0])
        else:
            total_move = float(highs[0]) - float(closes[-1])

        if total_move < atr_1h * 3:
            return False  # ATR'nin 3 katından az → normal

        # Geri çekilme var mı? (herhangi bir mumda ATR'nin %40'ı kadar ters hareket)
        pullback_threshold = atr_1h * 0.4
        has_pullback = False
        for i in range(len(opens)):
            if bias == "LONG":
                # Yeşil mum serisinde küçük kırmızı mum veya uzun alt fitil
                wick_down = float(min(opens[i], closes[i])) - float(lows[i])
                if closes[i] < opens[i] or wick_down > pullback_threshold:
                    has_pullback = True
                    break
            else:
                wick_up = float(highs[i]) - float(max(opens[i], closes[i]))
                if closes[i] > opens[i] or wick_up > pullback_threshold:
                    has_pullback = True
                    break

        if has_pullback:
            return False  # Geri çekilme var → sağlıklı hareket

        # 5/6 mum aynı yön + 3x ATR hareket + sıfır geri çekilme = OVEREXTENDED
        return True

    def _check_4h_obstacle_for_fallback(self, df_4h, bias: str,
                                         entry: float, tp: float) -> bool:
        """
        1H fallback durumunda 4H'da entry→TP yolunda engel var mı?

        4H NEUTRAL olduğunda 1H yön veriyor. Ama 4H'da kocaman bearish
        FVG veya OB olabilir — fiyat oraya çarpıp geri döner.

        True = 4H'da engel var, girme
        False = yol temiz
        """
        if df_4h is None or len(df_4h) < 20 or entry <= 0 or tp <= 0:
            return False

        # 4H OB'leri bul (her iki yönde)
        obs_4h_bull = self._find_order_blocks(df_4h, "LONG", 20)
        obs_4h_bear = self._find_order_blocks(df_4h, "SHORT", 20)
        obs_4h = obs_4h_bull + obs_4h_bear

        # 4H FVG'leri bul
        fvgs_4h = self._find_fvg(df_4h, 15)

        tp_distance = abs(tp - entry)
        if tp_distance == 0:
            return False

        if bias == "LONG":
            # Entry→TP yolunda mitigated olmamış bearish OB/FVG var mı?
            for ob in obs_4h:
                if ob["type"] == "BEARISH" and not ob["mitigated"]:
                    if entry < ob["low"] < tp:
                        dist_pct = (ob["low"] - entry) / tp_distance * 100
                        if dist_pct < 60:  # TP yolunun ilk %60'ında
                            logger.debug(
                                f"4H engel: BEARISH OB @ {ob['low']:.5f} "
                                f"(TP yolunun %{dist_pct:.0f}'inde)"
                            )
                            return True

            for fvg in fvgs_4h:
                if fvg["type"] == "BEARISH" and fvg["mitigated"] != "FULL":
                    if entry < fvg["low"] < tp:
                        dist_pct = (fvg["low"] - entry) / tp_distance * 100
                        if dist_pct < 60:
                            logger.debug(
                                f"4H engel: BEARISH FVG @ {fvg['low']:.5f} "
                                f"(TP yolunun %{dist_pct:.0f}'inde)"
                            )
                            return True

        elif bias == "SHORT":
            for ob in obs_4h:
                if ob["type"] == "BULLISH" and not ob["mitigated"]:
                    if tp < ob["high"] < entry:
                        dist_pct = (entry - ob["high"]) / tp_distance * 100
                        if dist_pct < 60:
                            logger.debug(
                                f"4H engel: BULLISH OB @ {ob['high']:.5f} "
                                f"(TP yolunun %{dist_pct:.0f}'inde)"
                            )
                            return True

            for fvg in fvgs_4h:
                if fvg["type"] == "BULLISH" and fvg["mitigated"] != "FULL":
                    if tp < fvg["high"] < entry:
                        dist_pct = (entry - fvg["high"]) / tp_distance * 100
                        if dist_pct < 60:
                            logger.debug(
                                f"4H engel: BULLISH FVG @ {fvg['high']:.5f} "
                                f"(TP yolunun %{dist_pct:.0f}'inde)"
                            )
                            return True

        return False

    # =================================================================
    #  BÖLÜM 2 — KATMAN 1: NARRATIVE (4H Yapı Analizi)
    # =================================================================

    def analyze_narrative(self, df_4h, df_1h=None) -> Dict:
        """
        HTF Narrative — Piyasa nereye gitmek istiyor?
        
        4H yapı analizi:
          - BOS → Trend yönü
          - CHoCH → Trend dönüşü
          - Structure quality (STRONG/WEAK)
        
        1H fallback: 4H NEUTRAL ise 1H'ya bakılır (otomatik WEAK).
        """
        result = {
            "bias": "NEUTRAL",
            "quality": "NEUTRAL",
            "choch": False,
            "confidence_note": "",
            "htf_swing_high": 0.0,
            "htf_swing_low": 0.0,
        }

        if df_4h is None or len(df_4h) < 20:
            result["confidence_note"] = "4H veri yetersiz"
            return result

        sh_4h, sl_4h = self._find_swing_points(df_4h, lookback=self.params.get("swing_lookback", 5))
        structure_4h = self._detect_structure(sh_4h, sl_4h)

        result["bias"] = structure_4h["bias"]
        result["quality"] = structure_4h["structure_quality"]
        result["choch"] = structure_4h["choch_detected"]
        result["htf_swing_high"] = structure_4h["last_swing_high"]
        result["htf_swing_low"] = structure_4h["last_swing_low"]

        if structure_4h["choch_detected"]:
            result["confidence_note"] = "4H CHoCH tespit edildi — kalite düşürüldü"
            # CHoCH bias'ı öldürmez, sadece bilgi amaçlı

        elif structure_4h["bias"] != "NEUTRAL":
            result["confidence_note"] = f"4H {structure_4h['bias']} yapı — {structure_4h['structure_quality']}"

        # 4H NEUTRAL ise 1H fallback
        if result["bias"] == "NEUTRAL" and not result["choch"] and df_1h is not None and len(df_1h) >= 20:
            sh_1h, sl_1h = self._find_swing_points(df_1h, lookback=self.params.get("swing_lookback", 5))
            structure_1h = self._detect_structure(sh_1h, sl_1h)

            if structure_1h["bias"] != "NEUTRAL":
                result["bias"] = structure_1h["bias"]
                result["quality"] = "WEAK"
                result["confidence_note"] = f"1H fallback — {structure_1h['bias']} yapı (4H nötr, 1H {structure_1h['structure_quality']})"

        if result["bias"] == "NEUTRAL":
            result["confidence_note"] = "HTF yapı belirsiz — trade açılmayacak"

        return result

    # =================================================================
    #  BÖLÜM 3 — KATMAN 2: POI TESPİTİ
    # =================================================================

    def find_poi_zones(self, df_15m, df_1h, bias: str,
                       current_price: float) -> List[Dict]:
        """
        POI (Point of Interest) bölgeleri tespit et.
        
        POI = OB + FVG + Likidite çakışma bölgesi.
        Fiyat bu bölgelere geldiğinde trade fırsatı doğar.
        """
        if df_15m is None or len(df_15m) < 30 or bias == "NEUTRAL":
            return []

        # 15m analiz
        sh_15m, sl_15m = self._find_swing_points(df_15m, lookback=self.params.get("swing_lookback", 5))
        obs_15m = self._find_order_blocks(df_15m, bias, self.params.get("ob_max_age_candles", 30))
        fvgs_15m = self._find_fvg(df_15m, self.params.get("fvg_max_age_candles", 20))
        liquidity = self._find_liquidity_pools(sh_15m, sl_15m, current_price)

        # 1H analiz (engel taraması için)
        obs_1h = self._find_order_blocks(df_1h, bias, 50) if df_1h is not None and len(df_1h) >= 20 else []
        fvgs_1h = self._find_fvg(df_1h, 30) if df_1h is not None and len(df_1h) >= 20 else []

        pd_zone = self._calculate_premium_discount(sh_15m, sl_15m, current_price)

        pois = []

        # Candidate zone'ları topla
        candidate_zones = []

        if bias == "LONG":
            for ob in obs_15m:
                if ob["type"] == "BULLISH" and ob["low"] < current_price:
                    candidate_zones.append({
                        "source": "OB", "high": ob["high"],
                        "low": ob["low"], "ce": ob["ce"],
                    })
            for fvg in fvgs_15m:
                if fvg["type"] == "BULLISH" and fvg["low"] < current_price:
                    candidate_zones.append({
                        "source": "FVG", "high": fvg["high"],
                        "low": fvg["low"], "ce": fvg["ce"],
                    })
        elif bias == "SHORT":
            for ob in obs_15m:
                if ob["type"] == "BEARISH" and ob["high"] > current_price:
                    candidate_zones.append({
                        "source": "OB", "high": ob["high"],
                        "low": ob["low"], "ce": ob["ce"],
                    })
            for fvg in fvgs_15m:
                if fvg["type"] == "BEARISH" and fvg["high"] > current_price:
                    candidate_zones.append({
                        "source": "FVG", "high": fvg["high"],
                        "low": fvg["low"], "ce": fvg["ce"],
                    })

        for zone in candidate_zones:
            # Çakışma analizi
            confluence_count = 1
            confluence_sources = [zone["source"]]

            for other in candidate_zones:
                if other is zone:
                    continue
                overlap = min(zone["high"], other["high"]) - max(zone["low"], other["low"])
                if overlap > 0:
                    confluence_count += 1
                    confluence_sources.append(other["source"])

            # Likidite çakışması
            liq_list = liquidity["ssl"] if bias == "LONG" else liquidity["bsl"]
            for liq_level in liq_list:
                if zone["low"] <= liq_level["price"] <= zone["high"]:
                    confluence_count += 1
                    confluence_sources.append(f"LIQ_{liq_level['type']}")

            # Entry, SL, TP hesaplama
            entry = zone["ce"]

            if bias == "LONG":
                sl = zone["low"] - (zone["high"] - zone["low"]) * 0.2
                tp = liquidity["nearest_bsl"] if liquidity["nearest_bsl"] > entry else entry * 1.02
                in_correct_zone = pd_zone["zone"] in ("DISCOUNT", "DEEP_DISCOUNT")
            else:
                sl = zone["high"] + (zone["high"] - zone["low"]) * 0.2
                tp = liquidity["nearest_ssl"] if liquidity["nearest_ssl"] > 0 and liquidity["nearest_ssl"] < entry else entry * 0.98
                in_correct_zone = pd_zone["zone"] in ("PREMIUM", "DEEP_PREMIUM")

            # Min/Max SL kontrolü
            min_sl_pct = self.params.get("min_sl_distance_pct", 0.008)
            max_sl_pct = self.params.get("max_sl_distance_pct", 0.025)
            sl_distance_pct = abs(entry - sl) / entry if entry > 0 else 0

            if sl_distance_pct < min_sl_pct:
                sl = entry * (1 - min_sl_pct) if bias == "LONG" else entry * (1 + min_sl_pct)
            elif sl_distance_pct > max_sl_pct:
                sl = entry * (1 - max_sl_pct) if bias == "LONG" else entry * (1 + max_sl_pct)

            # Engel taraması
            obstacle_info = self._scan_obstacles(bias, entry, tp, obs_1h, fvgs_1h, current_price)
            if obstacle_info["adjusted_tp"] != tp:
                tp = obstacle_info["adjusted_tp"]

            # RR hesaplaması
            risk = abs(entry - sl)
            reward = abs(tp - entry)
            rr = reward / risk if risk > 0 else 0

            # Distance from current price
            distance_pct = abs(current_price - entry) / current_price * 100 if current_price > 0 else 0

            pois.append({
                "bias": bias,
                "entry": float(entry),
                "sl": float(sl),
                "tp": float(tp),
                "rr": round(rr, 2),
                "zone_high": float(zone["high"]),
                "zone_low": float(zone["low"]),
                "confluence_count": confluence_count,
                "confluence_sources": confluence_sources,
                "in_correct_zone": in_correct_zone,
                "in_ote": pd_zone.get("in_ote", False),
                "distance_from_price_pct": round(distance_pct, 2),
                "obstacles": obstacle_info["obstacles"],
                "has_obstacle": obstacle_info["has_obstacle"],
                "pd_zone": pd_zone,
            })

        # Sıralama: RR >= min_rr önce, sonra confluence, sonra fiyata yakınlık
        _min_rr = self.params.get("min_rr_ratio", 1.4)
        pois.sort(key=lambda p: (
            -(1 if p["rr"] >= _min_rr else 0),
            -p["confluence_count"],
            p["distance_from_price_pct"],
        ))

        return pois

    # =================================================================
    #  BÖLÜM 4 — KATMAN 3: TRIGGER
    # =================================================================

    def check_trigger(self, df_15m, bias: str, poi: Dict,
                      current_price: float, atr: float,
                      proximity_pct: float = 0.01) -> Optional[Dict]:
        """
        POI bölgesinde trigger oluştu mu?
        
        Trigger tipleri (herhangi biri yeterli):
          A) Sweep + Rejection
          B) MSS (Micro Structure Shift)
          C) Displacement (2-3 ardışık güçlü mum)
        
        RR >= min_rr_ratio (config, default 1.4) zorunlu. Tek dev mum (>3x ATR) = REDDET.
        
        Args:
            proximity_pct: POI zone'a yakınlık eşiği.
                           generate_signal: 0.01 (1%) — trigger henüz oluşuyor
                           check_trigger_for_watch: 0.025 (2.5%) — fiyat bounce etmiş olabilir
        """
        if df_15m is None or len(df_15m) < 10 or poi is None:
            return None

        zone_high = poi["zone_high"]
        zone_low = poi["zone_low"]
        tp = poi["tp"]

        # Fiyat POI'ye yeterince yakın mı?
        if bias == "LONG":
            price_in_or_near_zone = current_price <= zone_high * (1 + proximity_pct)
        elif bias == "SHORT":
            price_in_or_near_zone = current_price >= zone_low * (1 - proximity_pct)
        else:
            return None

        if not price_in_or_near_zone:
            return None

        min_sl_pct = self.params.get("min_sl_distance_pct", 0.008)
        max_sl_pct = self.params.get("max_sl_distance_pct", 0.025)
        min_rr = self.params.get("min_rr_ratio", 1.4)

        # === TRIGGER A: Sweep + Rejection ===
        sh_15m, sl_15m = self._find_swing_points(df_15m, lookback=3)
        sweep = self._detect_sweep(df_15m, sh_15m, sl_15m, bias, lookback=10)

        if sweep is not None and sweep["candles_ago"] <= 6:
            if bias == "LONG":
                sweep_sl = sweep["sweep_price"] * (1 - 0.002)
            else:
                sweep_sl = sweep["sweep_price"] * (1 + 0.002)

            sl_dist = abs(current_price - sweep_sl) / current_price if current_price > 0 else 0
            if sl_dist < min_sl_pct:
                sweep_sl = current_price * (1 - min_sl_pct) if bias == "LONG" else current_price * (1 + min_sl_pct)
            elif sl_dist > max_sl_pct:
                sweep_sl = current_price * (1 - max_sl_pct) if bias == "LONG" else current_price * (1 + max_sl_pct)

            risk = abs(current_price - sweep_sl)
            reward = abs(tp - current_price)
            actual_rr = reward / risk if risk > 0 else 0

            if actual_rr >= min_rr:
                return {
                    "trigger_type": "SWEEP_REJECTION",
                    "direction": bias,
                    "entry": float(current_price),
                    "sl": float(sweep_sl),
                    "tp": float(tp),
                    "rr": round(actual_rr, 2),
                    "sweep_data": sweep,
                    "entry_mode": "MARKET",
                    "quality": "A+" if poi["confluence_count"] >= 3 else "A" if poi["confluence_count"] >= 2 else "B",
                    "components": ["HTF_BIAS", "POI_ZONE", "SWEEP", "REJECTION"],
                    "poi": poi,
                }

        # === TRIGGER B: MSS ===
        mss = self._detect_mss(df_15m, bias, after_index=max(0, len(df_15m) - 10))

        if mss is not None and mss["candles_ago"] <= 4:
            sl = poi["sl"]

            # Min/Max SL kontrolü (Trigger A ve C'de var, burada eksikti)
            sl_dist = abs(current_price - sl) / current_price if current_price > 0 else 0
            if sl_dist < min_sl_pct:
                sl = current_price * (1 - min_sl_pct) if bias == "LONG" else current_price * (1 + min_sl_pct)
            elif sl_dist > max_sl_pct:
                sl = current_price * (1 - max_sl_pct) if bias == "LONG" else current_price * (1 + max_sl_pct)

            risk = abs(current_price - sl)
            reward = abs(tp - current_price)
            actual_rr = reward / risk if risk > 0 else 0

            if actual_rr >= min_rr:
                return {
                    "trigger_type": "MSS",
                    "direction": bias,
                    "entry": float(current_price),
                    "sl": float(sl),
                    "tp": float(tp),
                    "rr": round(actual_rr, 2),
                    "mss_data": mss,
                    "entry_mode": "MARKET",
                    "quality": "A" if poi["confluence_count"] >= 2 else "B",
                    "components": ["HTF_BIAS", "POI_ZONE", "MSS"],
                    "poi": poi,
                }

        # === TRIGGER C: Displacement ===
        displacement = self._detect_displacement(df_15m, bias, atr, after_index=max(0, len(df_15m) - 8))

        if displacement is not None and displacement["candles_ago"] <= 4:
            if bias == "LONG":
                disp_sl = displacement["displacement_low"] * (1 - 0.002)
            else:
                disp_sl = displacement["displacement_high"] * (1 + 0.002)

            sl_dist = abs(current_price - disp_sl) / current_price if current_price > 0 else 0
            if sl_dist < min_sl_pct:
                disp_sl = current_price * (1 - min_sl_pct) if bias == "LONG" else current_price * (1 + min_sl_pct)
            elif sl_dist > max_sl_pct:
                disp_sl = current_price * (1 - max_sl_pct) if bias == "LONG" else current_price * (1 + max_sl_pct)

            risk = abs(current_price - disp_sl)
            reward = abs(tp - current_price)
            actual_rr = reward / risk if risk > 0 else 0

            if actual_rr >= min_rr:
                return {
                    "trigger_type": "DISPLACEMENT",
                    "direction": bias,
                    "entry": float(current_price),
                    "sl": float(disp_sl),
                    "tp": float(tp),
                    "rr": round(actual_rr, 2),
                    "displacement_data": displacement,
                    "entry_mode": "MARKET",
                    "quality": "B" if displacement["consecutive_candles"] >= 2 else "C",
                    "components": ["HTF_BIAS", "POI_ZONE", "DISPLACEMENT"],
                    "poi": poi,
                }

        return None

    # =================================================================
    #  BÖLÜM 5 — ANA FONKSİYON: generate_signal()
    # =================================================================

    def generate_signal(self, symbol: str, multi_tf: Dict,
                        market_data: Optional[Dict] = None) -> Optional[Dict]:
        """
        Ana sinyal üretici — Narrative → POI → Trigger.
        
        v3.x'teki sıralı 5-gate sistemi YOK.
        3 katmanlı bağlamsal analiz.
        
        Returns:
            None — sinyal yok
            {"action": "WATCH", ...} — POI tespit edildi, trigger bekleniyor
            {"action": "SIGNAL", ...} — Trigger oluştu, MARKET giriş yapılacak
        """
        df_15m = multi_tf.get("15m")
        df_1h = multi_tf.get("1h")
        df_4h = multi_tf.get("4h")

        if df_15m is None or len(df_15m) < 50:
            return None

        current_price = float(df_15m.iloc[-1]["close"])
        if current_price <= 0:
            return None

        atr_15m = self._calc_atr(df_15m, 14)

        # ═══ VOLATİLİTE FİLTRESİ ═══
        last_candle = df_15m.iloc[-1]
        last_range = float(last_candle["high"]) - float(last_candle["low"])
        if self._is_volatile_candle(last_range, atr_15m):
            logger.debug(f"{symbol}: Son mum anormal volatilite — bekleniyor")
            return None

        # ═══ KATMAN 1: NARRATIVE ═══
        narrative = self.analyze_narrative(df_4h, df_1h)
        bias = narrative["bias"]

        if bias == "NEUTRAL":
            return None

        # CHoCH artık sinyali engellemez — sadece triggerda kalite düşürür

        # ═══ OVEREXTENSION KONTROLÜ (1H) ═══
        # Coin tek yöne aşırı gitmişse → sinyali SIGNAL yerine WATCH'a düşür
        force_watch_overextended = False
        if df_1h is not None and len(df_1h) >= 10:
            if self._check_overextension(df_1h, bias):
                force_watch_overextended = True
                logger.info(
                    f"⚠️ {symbol}: 1H overextended ({bias}) — "
                    f"sinyal WATCH'a düşürülecek"
                )

        # ═══ KATMAN 2: POI TESPİTİ ═══
        pois = self.find_poi_zones(df_15m, df_1h, bias, current_price)

        if not pois:
            return None

        # RR filtresi (config'den)
        _min_rr = self.params.get("min_rr_ratio", 1.4)
        valid_pois = [p for p in pois if p["rr"] >= _min_rr]
        if not valid_pois:
            return None

        best_poi = valid_pois[0]

        # ═══ 4H ENGEL KONTROLÜ (sadece 1H fallback durumunda) ═══
        # 4H NEUTRAL idi, 1H yön verdi → ama 4H'da engel var mı?
        if narrative.get("quality") == "WEAK" and df_4h is not None:
            entry_est = best_poi.get("entry", current_price)
            tp_est = best_poi.get("tp", 0)
            if tp_est > 0 and self._check_4h_obstacle_for_fallback(
                df_4h, bias, entry_est, tp_est
            ):
                logger.info(
                    f"🚫 {symbol}: 1H fallback ({bias}) ama 4H'da engel var "
                    f"— sinyal iptal"
                )
                return None

        # ═══ KATMAN 3: TRIGGER ═══
        trigger = self.check_trigger(df_15m, bias, best_poi, current_price, atr_15m)

        if trigger is not None:
            # Overextended kontrolü: Trigger oluştu ama coin aşırı gitmişse → WATCH
            if force_watch_overextended:
                logger.info(
                    f"⚠️ {symbol} OVEREXTENDED → WATCH'a düşürüldü: "
                    f"{trigger['direction']} | Entry: {trigger['entry']:.5f}"
                )
                return {
                    "action": "WATCH",
                    "symbol": symbol,
                    "direction": trigger["direction"],
                    "entry_price": trigger["entry"],
                    "current_price": current_price,
                    "stop_loss": trigger["sl"],
                    "take_profit": trigger["tp"],
                    "rr_ratio": trigger["rr"],
                    "watch_reason": "1H overextended — geri çekilme bekleniyor",
                    "quality_tier": "WATCH",
                    "components": trigger["components"],
                    "narrative": narrative,
                    "poi": best_poi,
                    "trigger_data": trigger,
                    "atr": atr_15m,
                    "confidence": 100,
                    "confluence_score": 100,
                    "entry_mode": "MARKET",
                    "timeframe": "15m",
                }

            # TRIGGER OLUŞTU → SIGNAL
            logger.info(
                f"🎯 {symbol} SIGNAL: {trigger['direction']} | "
                f"Trigger: {trigger['trigger_type']} | "
                f"Entry: {trigger['entry']:.5f} | "
                f"SL: {trigger['sl']:.5f} | TP: {trigger['tp']:.5f} | "
                f"RR: {trigger['rr']} | Quality: {trigger['quality']}"
            )

            return {
                "action": "SIGNAL",
                "symbol": symbol,
                "direction": trigger["direction"],
                "entry_price": trigger["entry"],
                "current_price": current_price,
                "stop_loss": trigger["sl"],
                "take_profit": trigger["tp"],
                "rr_ratio": trigger["rr"],
                "entry_mode": "MARKET",
                "trigger_type": trigger["trigger_type"],
                "quality_tier": trigger["quality"],
                "components": trigger["components"],
                "narrative": narrative,
                "poi": best_poi,
                "trigger_data": trigger,
                "atr": atr_15m,
                # Uyumluluk (eski frontend/API için)
                "confidence": 100,
                "confluence_score": 100,
                "timeframe": "15m",
            }

        # Trigger yok ama POI var ve fiyat yakınsa → WATCH
        if best_poi["distance_from_price_pct"] <= 1.0:
            logger.debug(
                f"👁️ {symbol} WATCH: {bias} | "
                f"POI: {best_poi['zone_low']:.5f}-{best_poi['zone_high']:.5f} | "
                f"RR: {best_poi['rr']} | Dist: {best_poi['distance_from_price_pct']:.2f}%"
            )

            return {
                "action": "WATCH",
                "symbol": symbol,
                "direction": bias,
                "entry_price": best_poi["entry"],
                "current_price": current_price,
                "stop_loss": best_poi["sl"],
                "take_profit": best_poi["tp"],
                "rr_ratio": best_poi["rr"],
                "watch_reason": f"POI yakın ({best_poi['distance_from_price_pct']:.1f}%), trigger bekleniyor",
                "quality_tier": "WATCH",
                "components": ["HTF_BIAS", "POI_ZONE"],
                "narrative": narrative,
                "poi": best_poi,
                "atr": atr_15m,
                "confidence": 100,
                "confluence_score": 100,
                "entry_mode": "MARKET",
                "timeframe": "15m",
            }

        return None

    # =================================================================
    #  BÖLÜM 5B — WATCHLIST TRIGGER KONTROLÜ (Dual TF)
    # =================================================================

    def _check_zone_touched(self, df, bias: str, poi: Dict,
                            lookback: int = 12) -> bool:
        """
        Son N mum içinde herhangi birinin POI zone'a dokunup dokunmadığını kontrol et.

        ICT Prensibi: Trigger'ın anlamlı olabilmesi için fiyatın zone'a
        DOKUNMUŞ olması gerekir. Zone'a hiç ulaşmamış fiyatta sweep/MSS
        tespit etmek false positive üretir.

        LONG:  Mumun low'u <= zone_high (zone'a veya içine girdi)
        SHORT: Mumun high'ı >= zone_low (zone'a veya içine girdi)
        """
        zone_high = poi.get("zone_high", 0)
        zone_low = poi.get("zone_low", 0)

        if not zone_high or not zone_low:
            return False

        recent = df.tail(min(lookback, len(df)))

        for _, candle in recent.iterrows():
            low = float(candle.get("low", 0))
            high = float(candle.get("high", 0))

            if bias == "LONG" and low <= zone_high:
                return True
            elif bias == "SHORT" and high >= zone_low:
                return True

        return False

    def check_trigger_for_watch(self, symbol: str, df_15m,
                                stored_narrative: Dict,
                                stored_poi: Dict,
                                df_5m=None) -> Optional[Dict]:
        """
        Watchlist item'ı için dual-TF trigger kontrolü.

        v4.1 — ICT Sniper Entry yaklaşımı:
          1. Narrative tekrar hesaplanmaz (stored kullanılır, 4H/1H API tasarrufu)
          2. POI tekrar aranmaz (stored kullanılır)
          3. POI invalidation: 2 ardışık 15m close teyitli (%1.2 eşik)
          4. 15m'de trigger ara (standart — proximity %2.5)
          5. 15m bulamazsa + 5m zone'a dokunmuşsa → 5m sniper trigger ara

        ICT Prensibi:
          Institutional order flow POI zone'larda birikir. Fiyat zone'a dokunup
          yapısal shift verdiğinde (5m MSS/displacement), bu en hassas giriş
          noktasıdır. 15m'de henüz görünmeyen micro yapılar 5m'de tespit edilir.

        Returns:
            None — trigger yok
            {"_invalidated": True, ...} — POI geçersiz
            {"action": "SIGNAL", ...} — trigger oluştu, işlem açılacak
        """
        if df_15m is None or len(df_15m) < 20:
            return None

        if not stored_narrative or not stored_poi:
            return None

        bias = stored_narrative.get("bias", "NEUTRAL")
        if bias == "NEUTRAL":
            return None

        current_price = float(df_15m.iloc[-1]["close"])
        if current_price <= 0:
            return None

        atr_15m = self._calc_atr(df_15m, 14)

        # ── VOLATİLİTE FİLTRESİ ──
        last_candle = df_15m.iloc[-1]
        last_range = float(last_candle["high"]) - float(last_candle["low"])
        if self._is_volatile_candle(last_range, atr_15m):
            return None

        # ══════════════════════════════════════════════
        #  POI İNVALIDATION — Close teyitli (%1.2 eşik)
        # ══════════════════════════════════════════════
        #
        # ICT Prensibi: POI zone "kırıldı" demek için tek wick yetmez.
        # Ardışık 2 mumun KAPANIŞI zone'un ötesinde olmalı.
        # Tek wick = sweep (giriş fırsatı), ardışık close = zone öldü.

        zone_high = stored_poi.get("zone_high", 0)
        zone_low = stored_poi.get("zone_low", 0)

        recent_closes = [float(row["close"]) for _, row in df_15m.tail(2).iterrows()]

        if bias == "LONG":
            invalidation_level = zone_low * 0.988
            if len(recent_closes) >= 2 and all(c < invalidation_level for c in recent_closes):
                logger.debug(f"{symbol} WATCH: POI invalidated (2 ardışık 15m close zone altında)")
                return {"_invalidated": True, "reason": "POI aşağı kırıldı (2x close teyit)"}
        elif bias == "SHORT":
            invalidation_level = zone_high * 1.012
            if len(recent_closes) >= 2 and all(c > invalidation_level for c in recent_closes):
                logger.debug(f"{symbol} WATCH: POI invalidated (2 ardışık 15m close zone üstünde)")
                return {"_invalidated": True, "reason": "POI yukarı kırıldı (2x close teyit)"}

        # ══════════════════════════════════════════════
        #  15M TRIGGER KONTROLÜ (Standart)
        # ══════════════════════════════════════════════
        #
        # generate_signal: proximity %1 (trigger anlık oluşuyor)
        # Watchlist: proximity %2.5 (fiyat bounce etmiş olabilir)

        trigger = self.check_trigger(
            df_15m, bias, stored_poi, current_price, atr_15m,
            proximity_pct=0.025
        )

        if trigger is not None:
            logger.info(
                f"🎯 {symbol} WATCH→SIGNAL (15m): {trigger['direction']} | "
                f"Trigger: {trigger['trigger_type']} | "
                f"Entry: {trigger['entry']:.5f} | "
                f"SL: {trigger['sl']:.5f} | TP: {trigger['tp']:.5f} | "
                f"RR: {trigger['rr']}"
            )

            return {
                "action": "SIGNAL",
                "symbol": symbol,
                "direction": trigger["direction"],
                "entry_price": trigger["entry"],
                "current_price": current_price,
                "stop_loss": trigger["sl"],
                "take_profit": trigger["tp"],
                "rr_ratio": trigger["rr"],
                "entry_mode": "MARKET",
                "trigger_type": trigger["trigger_type"],
                "quality_tier": trigger["quality"],
                "components": trigger["components"],
                "narrative": stored_narrative,
                "poi": stored_poi,
                "trigger_data": trigger,
                "atr": atr_15m,
                "confidence": 100,
                "confluence_score": 100,
                "timeframe": "15m",
            }

        # ══════════════════════════════════════════════
        #  5M SNİPER TRIGGER KONTROLÜ (ICT LTF Entry)
        # ══════════════════════════════════════════════
        #
        # ICT Prensibi: En hassas giriş, fiyat POI zone'a dokunduktan sonra
        # 5m (veya 1m) yapısal shift ile gelir. 15m'de henüz görünmeyen
        # micro sweep veya MSS burada yakalanır.
        #
        # Koşullar:
        #   1. 5m data mevcut ve yeterli
        #   2. Son 12 adet 5m mum zone'a dokunmuş olmalı (zone_touched)
        #   3. 5m volatilite filtresi
        #   4. check_trigger ile 5m'de sweep/MSS/displacement ara

        if df_5m is not None and len(df_5m) >= 15:
            zone_touched = self._check_zone_touched(df_5m, bias, stored_poi, lookback=12)

            if zone_touched:
                atr_5m = self._calc_atr(df_5m, 14)
                current_price_5m = float(df_5m.iloc[-1]["close"])

                # 5m volatilite filtresi
                last_5m = df_5m.iloc[-1]
                range_5m = float(last_5m["high"]) - float(last_5m["low"])

                if not self._is_volatile_candle(range_5m, atr_5m):
                    trigger_5m = self.check_trigger(
                        df_5m, bias, stored_poi, current_price_5m, atr_5m,
                        proximity_pct=0.03
                    )

                    if trigger_5m is not None:
                        logger.info(
                            f"🎯 {symbol} WATCH→SIGNAL (5m SNIPER): "
                            f"{trigger_5m['direction']} | "
                            f"Trigger: {trigger_5m['trigger_type']} | "
                            f"Entry: {trigger_5m['entry']:.5f} | "
                            f"SL: {trigger_5m['sl']:.5f} | "
                            f"TP: {trigger_5m['tp']:.5f} | "
                            f"RR: {trigger_5m['rr']}"
                        )

                        return {
                            "action": "SIGNAL",
                            "symbol": symbol,
                            "direction": trigger_5m["direction"],
                            "entry_price": trigger_5m["entry"],
                            "current_price": current_price_5m,
                            "stop_loss": trigger_5m["sl"],
                            "take_profit": trigger_5m["tp"],
                            "rr_ratio": trigger_5m["rr"],
                            "entry_mode": "MARKET",
                            "trigger_type": trigger_5m["trigger_type"],
                            "quality_tier": "SNIPER",
                            "components": trigger_5m["components"] + ["5M_SNIPER"],
                            "narrative": stored_narrative,
                            "poi": stored_poi,
                            "trigger_data": trigger_5m,
                            "atr": atr_5m,
                            "confidence": 100,
                            "confluence_score": 100,
                            "timeframe": "5m",
                        }

        return None

    # =================================================================
    #  BÖLÜM 6 — YARDIMCI ANALİZ FONKSİYONLARI (Dashboard)
    # =================================================================

    def full_analysis(self, symbol: str, multi_tf: Dict) -> Dict:
        """Dashboard için tam analiz — sinyal üretmez, sadece bilgi verir."""
        df_15m = multi_tf.get("15m")
        df_1h = multi_tf.get("1h")
        df_4h = multi_tf.get("4h")

        result = {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "narrative": {},
            "pois": [],
            "swing_points": {"highs": [], "lows": []},
            "order_blocks": [],
            "fvgs": [],
            "liquidity": {},
            "pd_zone": {},
            "atr": 0,
        }

        if df_15m is None or len(df_15m) < 30:
            return result

        current_price = float(df_15m.iloc[-1]["close"])
        atr = self._calc_atr(df_15m, 14)
        result["atr"] = atr

        narrative = self.analyze_narrative(df_4h, df_1h)
        result["narrative"] = narrative

        sh, sl_pts = self._find_swing_points(df_15m, lookback=self.params.get("swing_lookback", 5))
        result["swing_points"] = {"highs": sh[-10:], "lows": sl_pts[-10:]}
        result["order_blocks"] = self._find_order_blocks(df_15m, narrative["bias"])
        result["fvgs"] = self._find_fvg(df_15m)
        result["liquidity"] = self._find_liquidity_pools(sh, sl_pts, current_price)
        result["pd_zone"] = self._calculate_premium_discount(sh, sl_pts, current_price)

        if narrative["bias"] != "NEUTRAL":
            result["pois"] = self.find_poi_zones(df_15m, df_1h, narrative["bias"], current_price)

        return result

    def calculate_confluence(self, symbol: str, multi_tf: Dict) -> Dict:
        """Uyumluluk metodu — eski API/frontend ile çalışması için."""
        analysis = self.full_analysis(symbol, multi_tf)
        narrative = analysis.get("narrative", {})
        pois = analysis.get("pois", [])

        direction = narrative.get("bias", "NEUTRAL")
        components = []
        if direction != "NEUTRAL":
            components.append("HTF_BIAS")
        if pois:
            components.append("POI_ZONE")
            if any(p.get("confluence_count", 0) >= 2 for p in pois):
                components.append("MULTI_CONFLUENCE")

        return {
            "symbol": symbol,
            "direction": direction,
            "confluence_score": 100 if direction != "NEUTRAL" and pois else 0,
            "components": components,
            "narrative": narrative,
            "poi_count": len(pois),
            "best_rr": pois[0]["rr"] if pois else 0,
        }


# Global instance
ict_strategy = ICTStrategy()
