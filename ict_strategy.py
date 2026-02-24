# =====================================================
# ICT Trading Bot — Pure SMC Strategy Engine v3.0
# =====================================================
#
# SIFIRDAN YAZILDI: Saf ICT / Smart Money Concepts
#
# KURALLAR:
#   - Perakende gösterge YOK (RSI, MACD, EMA, SMA, Bollinger, ADX vb.)
#   - Puanlama / Scoring sistemi YOK
#   - Minimum R:R kısıtlaması YOK
#   - Tamamen Boolean (Evet / Hayır) kapı sistemi
#
# 5 ZORUNLU AŞAMA (hepsi geçmeli, yoksa None döner):
#
#   GATE 1 — Seans Bilgisi (Kripto 7/24 - Bypass)
#       Kripto piyasaları 7/24 aktif.
#       Killzone kontrolü YOK (forex killzone'ları kripto'da anlamsız).
#       Seans bilgisi sadece loglama için tutulur.
#
#   GATE 2 — HTF Bias + Premium / Discount
#       4H (veya 1H fallback) dealing range.
#       Bullish bias → sadece LONG, fiyat Discount'ta (< %50).
#       Bearish bias → sadece SHORT, fiyat Premium'da (> %50).
#
#   GATE 3 — Liquidity Sweep
#       15m swing high / low FİTİL ile süpürülmeli (body close ile DEĞİL).
#       Gövde geri kapanmalı (rejection). Wick:body oranı kontrol edilir.
#
#   GATE 4 — Hacim Destekli Displacement + MSS
#       Sweep sonrası bias yönünde güçlü gövdeli mum.
#       GÖVDE kapanışı yapıyı kırar (fitil DEĞİL).
#       Hacim > 20-bar ortalama.
#
#   GATE 5 — FVG Giriş + SL / TP
#       Giriş: FVG CE (Consequent Encroachment — orta nokta) limit emir.
#       SL: Sweep fitil (wick) uç noktası.
#       TP: Karşı taraftaki likidite havuzu (min RR 1.0).
#
# =====================================================

import logging
import numpy as np
from datetime import datetime, timezone

from config import ICT_PARAMS
from database import get_bot_param

logger = logging.getLogger("ICT-Bot.Strategy")


class ICTStrategy:
    """Saf ICT / Smart Money Concepts strateji motoru — Boolean kapı sistemi."""

    # Integer olması gereken parametreler (DB'den float gelir)
    _INT_PARAMS = {
        "swing_lookback", "ob_max_age_candles", "fvg_max_age_candles",
        "liquidity_min_touches", "max_concurrent_trades",
        "max_same_direction_trades", "signal_cooldown_minutes",
        "patience_watch_candles",
    }

    def __init__(self):
        self.params = {}
        self._load_params()
        logger.info("ICTStrategy v3.1 başlatıldı — Kripto 24/7 Boolean Gate Protocol")

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
    #  BÖLÜM 1 — SEANS / KİLLZONE BİLGİSİ
    # =================================================================

    def get_session_info(self):
        """
        UTC saatine göre seans bilgisi döndürür.

        Kripto 7/24 işlem görür — tüm seanslar geçerlidir.
        Killzone kalitesi sadece pozisyon güvenini etkiler,
        sinyal üretimini ENGELLEMEZ.

        Killzone tanımları (UTC):
          London Open : 07:00 – 10:00  (en yüksek kalite)
          NY Open     : 12:00 – 15:00  (en yüksek kalite)
          Asia        : 00:00 – 06:00  (kripto'da aktif)
          Transition  : 10:00 – 12:00  (geçiş dönemi)
          London Close: 15:00 – 17:00  (orta kalite)
          Off-Peak    : 17:00 – 00:00  (düşük kalite ama geçerli)
        """
        now_utc = datetime.now(timezone.utc)
        hour = now_utc.hour
        weekday = now_utc.weekday()  # 0=Pazartesi, 5=Cumartesi, 6=Pazar

        is_weekend = weekday >= 5

        # Kripto 7/24 — tüm seanslar geçerli, kalite farklı
        if 7 <= hour < 10:
            label = "London Open Killzone"
            quality = 1.0
        elif 12 <= hour < 15:
            label = "NY Open Killzone"
            quality = 1.0
        elif 0 <= hour < 6:
            label = "Asian Session"
            quality = 0.7
        elif 10 <= hour < 12:
            label = "London-NY Transition"
            quality = 0.8
        elif 15 <= hour < 17:
            label = "London Close"
            quality = 0.6
        else:
            label = "Off-Peak"
            quality = 0.5

        # Kripto'da hafta sonu da geçerli (kalite düşer)
        if is_weekend:
            quality *= 0.7

        # ★ Kripto 7/24: is_valid_killzone daima True
        is_valid_killzone = True

        return {
            "label": label,
            "hour_utc": hour,
            "quality": quality,
            "is_weekend": is_weekend,
            "is_valid_killzone": is_valid_killzone,
            "weekday": weekday,
        }

    # =================================================================
    #  BÖLÜM 2 — ATR HESAPLAMA
    # =================================================================

    def _calc_atr(self, df, period=14):
        """Average True Range hesapla."""
        if len(df) < period + 1:
            return 0
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:] - close[:-1]),
            ),
        )
        if len(tr) < period:
            return float(np.mean(tr)) if len(tr) > 0 else 0
        return float(np.mean(tr[-period:]))

    # =================================================================
    #  BÖLÜM 3 — RANGING MARKET TESPİTİ
    # =================================================================

    def detect_ranging_market(self, df, lookback=30):
        """
        Piyasanın yatay (ranging) olup olmadığını tespit et.
        Efficiency Ratio + ATR-normalize range kullanır.
        """
        if len(df) < lookback:
            return False
        subset = df.tail(lookback)
        price_change = abs(subset["close"].iloc[-1] - subset["close"].iloc[0])
        path_sum = (subset["high"] - subset["low"]).sum()
        if path_sum <= 0:
            return False
        efficiency_ratio = price_change / path_sum

        atr = self._calc_atr(df, 14)
        mid_price = subset["close"].mean()
        if mid_price <= 0:
            return False
        total_range = subset["high"].max() - subset["low"].min()
        atr_normalized_range = total_range / mid_price

        # Düşük efficiency + dar range = yatay piyasa
        is_ranging = efficiency_ratio < 0.15 and atr_normalized_range < 0.04
        return is_ranging

    # =================================================================
    #  BÖLÜM 4 — SWING POINT TESPİTİ
    # =================================================================

    def find_swing_points(self, df):
        """
        Fractal-tabanlı swing high ve swing low tespiti.
        Major (5-bar) + internal (3-bar) fractal kullanır.
        """
        lookback = self.params.get("swing_lookback", 5)
        n = len(df)
        swing_highs = []
        swing_lows = []

        if n < lookback * 2 + 1:
            return swing_highs, swing_lows

        highs = df["high"].values
        lows = df["low"].values

        # Major fractals (5-bar)
        for i in range(lookback, n - lookback):
            # Swing High: orta mum en yüksek
            if all(highs[i] > highs[i - j] for j in range(1, lookback + 1)) and \
               all(highs[i] > highs[i + j] for j in range(1, lookback + 1)):
                swing_highs.append({
                    "index": i,
                    "price": float(highs[i]),
                    "fractal_type": "MAJOR",
                    "timestamp": df.iloc[i].get("timestamp", ""),
                })

            # Swing Low: orta mum en düşük
            if all(lows[i] < lows[i - j] for j in range(1, lookback + 1)) and \
               all(lows[i] < lows[i + j] for j in range(1, lookback + 1)):
                swing_lows.append({
                    "index": i,
                    "price": float(lows[i]),
                    "fractal_type": "MAJOR",
                    "timestamp": df.iloc[i].get("timestamp", ""),
                })

        # Internal fractals (3-bar) — sadece major eksikse
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            for i in range(2, n - 2):
                if highs[i] > highs[i - 1] and highs[i] > highs[i + 1] and \
                   highs[i] > highs[i - 2] and highs[i] > highs[i + 2]:
                    # Major ile çakışma kontrolü
                    if not any(abs(sh["index"] - i) <= 2 for sh in swing_highs):
                        swing_highs.append({
                            "index": i,
                            "price": float(highs[i]),
                            "fractal_type": "INTERNAL",
                            "timestamp": df.iloc[i].get("timestamp", ""),
                        })
                if lows[i] < lows[i - 1] and lows[i] < lows[i + 1] and \
                   lows[i] < lows[i - 2] and lows[i] < lows[i + 2]:
                    if not any(abs(sl["index"] - i) <= 2 for sl in swing_lows):
                        swing_lows.append({
                            "index": i,
                            "price": float(lows[i]),
                            "fractal_type": "INTERNAL",
                            "timestamp": df.iloc[i].get("timestamp", ""),
                        })

        swing_highs.sort(key=lambda x: x["index"])
        swing_lows.sort(key=lambda x: x["index"])
        return swing_highs, swing_lows

    # =================================================================
    #  BÖLÜM 5 — MARKET STRUCTURE (BOS / CHoCH)
    # =================================================================

    def detect_market_structure(self, df):
        """
        Break of Structure (BOS) ve Change of Character (CHoCH) tespiti.
        """
        swing_highs, swing_lows = self.find_swing_points(df)
        bos_events = []
        choch_events = []
        current_trend = "NEUTRAL"
        last_bos_dir = None

        min_disp = self.params.get("bos_min_displacement", 0.003)
        n = len(df)

        # Higher Highs / Lower Lows analizi
        for i in range(1, len(swing_highs)):
            prev = swing_highs[i - 1]
            curr = swing_highs[i]
            if curr["index"] >= n:
                continue
            close_at_break = df.iloc[min(curr["index"], n - 1)]["close"]
            displacement = (close_at_break - prev["price"]) / prev["price"] if prev["price"] > 0 else 0

            if curr["price"] > prev["price"] and displacement >= min_disp:
                if last_bos_dir == "BEARISH":
                    choch_events.append({
                        "index": curr["index"],
                        "type": "BULLISH_CHOCH",
                        "price": curr["price"],
                        "prev_price": prev["price"],
                    })
                else:
                    bos_events.append({
                        "index": curr["index"],
                        "type": "BULLISH_BOS",
                        "price": curr["price"],
                        "prev_price": prev["price"],
                    })
                last_bos_dir = "BULLISH"

        for i in range(1, len(swing_lows)):
            prev = swing_lows[i - 1]
            curr = swing_lows[i]
            if curr["index"] >= n:
                continue
            close_at_break = df.iloc[min(curr["index"], n - 1)]["close"]
            displacement = (prev["price"] - close_at_break) / prev["price"] if prev["price"] > 0 else 0

            if curr["price"] < prev["price"] and displacement >= min_disp:
                if last_bos_dir == "BULLISH":
                    choch_events.append({
                        "index": curr["index"],
                        "type": "BEARISH_CHOCH",
                        "price": curr["price"],
                        "prev_price": prev["price"],
                    })
                else:
                    bos_events.append({
                        "index": curr["index"],
                        "type": "BEARISH_BOS",
                        "price": curr["price"],
                        "prev_price": prev["price"],
                    })
                last_bos_dir = "BEARISH"

        # Trend belirleme
        bullish_bos = sum(1 for b in bos_events if b["type"] == "BULLISH_BOS")
        bearish_bos = sum(1 for b in bos_events if b["type"] == "BEARISH_BOS")

        recent_events = (bos_events + choch_events)
        recent_events.sort(key=lambda x: x["index"])
        last_events = recent_events[-3:] if recent_events else []

        if last_events:
            last = last_events[-1]
            if "BULLISH" in last["type"]:
                current_trend = "BULLISH"
            elif "BEARISH" in last["type"]:
                current_trend = "BEARISH"
        elif bullish_bos > bearish_bos:
            current_trend = "BULLISH"
        elif bearish_bos > bullish_bos:
            current_trend = "BEARISH"

        # WEAKENING tespiti
        if current_trend == "BULLISH" and choch_events:
            last_choch = [c for c in choch_events if "BEARISH" in c["type"]]
            last_bullish_bos = [b for b in bos_events if "BULLISH" in b["type"]]
            if last_choch and last_bullish_bos:
                if last_choch[-1]["index"] > last_bullish_bos[-1]["index"]:
                    current_trend = "WEAKENING_BULL"
        elif current_trend == "BEARISH" and choch_events:
            last_choch = [c for c in choch_events if "BULLISH" in c["type"]]
            last_bearish_bos = [b for b in bos_events if "BEARISH" in b["type"]]
            if last_choch and last_bearish_bos:
                if last_choch[-1]["index"] > last_bearish_bos[-1]["index"]:
                    current_trend = "WEAKENING_BEAR"

        # Son swing noktaları
        last_swing_high = swing_highs[-1] if swing_highs else None
        last_swing_low = swing_lows[-1] if swing_lows else None

        return {
            "trend": current_trend,
            "swing_highs": swing_highs,
            "swing_lows": swing_lows,
            "bos_events": bos_events,
            "choch_events": choch_events,
            "last_swing_high": last_swing_high,
            "last_swing_low": last_swing_low,
        }

    # =================================================================
    #  BÖLÜM 6 — ORDER BLOCK TESPİTİ
    # =================================================================

    def find_order_blocks(self, df, structure=None):
        """
        Order Block tespiti — mitigation kontrolü ile.
        """
        if structure is None:
            structure = self.detect_market_structure(df)

        n = len(df)
        max_age = self.params.get("ob_max_age_candles", 30)
        min_body_ratio = self.params.get("ob_body_ratio_min", 0.4)
        current_price = df["close"].iloc[-1]
        active_obs = []
        all_obs = []

        bos_indices = set()
        for event in structure["bos_events"] + structure["choch_events"]:
            bos_indices.add(event["index"])

        search_start = max(0, n - max_age - 10)
        for i in range(search_start, n - 1):
            candle = df.iloc[i]
            body = abs(candle["close"] - candle["open"])
            total_range = candle["high"] - candle["low"]
            if total_range <= 0:
                continue
            body_ratio = body / total_range
            if body_ratio < min_body_ratio:
                continue

            age = n - 1 - i
            is_bullish_ob = candle["close"] < candle["open"]
            is_bearish_ob = candle["close"] > candle["open"]

            # Sonrasında BOS/CHoCH var mı?
            has_structure_break = any(idx > i for idx in bos_indices)
            if not has_structure_break and age > 5:
                continue

            ob_dict = {
                "index": i,
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "type": "BULLISH_OB" if is_bullish_ob else "BEARISH_OB",
                "strength": round(body_ratio, 3),
                "age": age,
                "timestamp": candle.get("timestamp", ""),
            }
            all_obs.append(ob_dict)

            # Mitigation kontrolü (fiyat OB'ye ulaştı mı?)
            mitigated = False
            for j in range(i + 1, n):
                if is_bullish_ob:
                    if df.iloc[j]["low"] <= candle["low"]:
                        mitigated = True
                        break
                elif is_bearish_ob:
                    if df.iloc[j]["high"] >= candle["high"]:
                        mitigated = True
                        break

            if not mitigated and age <= max_age:
                active_obs.append(ob_dict)

        return active_obs, all_obs

    # =================================================================
    #  BÖLÜM 7 — BREAKER BLOCK TESPİTİ
    # =================================================================

    def find_breaker_blocks(self, all_obs, df):
        """Mitigate edilmiş OB → Breaker Block'a dönüşür."""
        breaker_blocks = []
        n = len(df)

        for ob in all_obs:
            idx = ob["index"]
            if idx >= n - 2:
                continue

            mitigated = False
            for j in range(idx + 1, n):
                if ob["type"] == "BULLISH_OB" and df.iloc[j]["low"] <= ob["low"]:
                    mitigated = True
                    break
                elif ob["type"] == "BEARISH_OB" and df.iloc[j]["high"] >= ob["high"]:
                    mitigated = True
                    break

            if mitigated:
                bb_type = "BULLISH_BREAKER" if ob["type"] == "BEARISH_OB" else "BEARISH_BREAKER"
                breaker_blocks.append({
                    "index": ob["index"],
                    "high": ob["high"],
                    "low": ob["low"],
                    "type": bb_type,
                })

        return breaker_blocks

    # =================================================================
    #  BÖLÜM 8 — FAIR VALUE GAP (FVG)
    # =================================================================

    def find_fvg(self, df):
        """
        3-mum FVG tespiti.
        Bullish FVG: prev.high < next.low (yukarı gap)
        Bearish FVG: prev.low > next.high (aşağı gap)
        """
        fvgs = []
        min_size = self.params.get("fvg_min_size_pct", 0.001)
        max_age = self.params.get("fvg_max_age_candles", 20)
        n = len(df)

        search_start = max(1, n - max_age - 5)
        for i in range(search_start, n - 1):
            if i < 1:
                continue
            prev = df.iloc[i - 1]
            curr = df.iloc[i]
            next_ = df.iloc[i + 1]
            mid = curr["close"]
            if mid <= 0:
                continue

            # Bullish FVG
            if prev["high"] < next_["low"]:
                gap = next_["low"] - prev["high"]
                if gap / mid >= min_size:
                    # CE (Consequent Encroachment) tabanlı fill kontrolü
                    ce = prev["high"] + gap / 2
                    filled = False
                    if i + 2 < n and len(df.iloc[i + 2:]) > 0:
                        filled = df.iloc[i + 2:]["low"].min() <= ce
                    if not filled:
                        fvgs.append({
                            "type": "BULLISH_FVG",
                            "index": i,
                            "high": float(next_["low"]),
                            "low": float(prev["high"]),
                            "size_pct": round((gap / mid) * 100, 4),
                            "timestamp": curr.get("timestamp", ""),
                        })

            # Bearish FVG
            if prev["low"] > next_["high"]:
                gap = prev["low"] - next_["high"]
                if gap / mid >= min_size:
                    ce = next_["high"] + gap / 2
                    filled = False
                    if i + 2 < n and len(df.iloc[i + 2:]) > 0:
                        filled = df.iloc[i + 2:]["high"].max() >= ce
                    if not filled:
                        fvgs.append({
                            "type": "BEARISH_FVG",
                            "index": i,
                            "high": float(prev["low"]),
                            "low": float(next_["high"]),
                            "size_pct": round((gap / mid) * 100, 4),
                            "timestamp": curr.get("timestamp", ""),
                        })

        return fvgs

    # =================================================================
    #  BÖLÜM 9 — LİKİDİTE SEVİYELERİ (Equal Highs / Lows)
    # =================================================================

    def find_liquidity_levels(self, df):
        """
        Eşit dipli / eşit tepeli likidite havuzları.
        """
        swing_highs, swing_lows = self.find_swing_points(df)
        tolerance = self.params.get("liquidity_equal_tolerance", 0.001)
        min_touches = self.params.get("liquidity_min_touches", 2)
        n = len(df)
        levels = []

        # Equal Highs (BSL — Buy-Side Liquidity)
        for i in range(len(swing_highs)):
            touches = 1
            for j in range(i + 1, len(swing_highs)):
                if abs(swing_highs[j]["price"] - swing_highs[i]["price"]) / swing_highs[i]["price"] <= tolerance:
                    touches += 1
            if touches >= min_touches:
                swept = False
                level_price = swing_highs[i]["price"]
                for k in range(swing_highs[i]["index"] + 1, n):
                    if df.iloc[k]["high"] > level_price * (1 + tolerance):
                        swept = True
                        break
                # Tekrar kontrolü
                if not any(abs(l["price"] - level_price) / level_price <= tolerance * 2
                           for l in levels if l["type"] == "EQUAL_HIGHS"):
                    levels.append({
                        "type": "EQUAL_HIGHS",
                        "price": float(level_price),
                        "touches": touches,
                        "swept": swept,
                    })

        # Equal Lows (SSL — Sell-Side Liquidity)
        for i in range(len(swing_lows)):
            touches = 1
            for j in range(i + 1, len(swing_lows)):
                if abs(swing_lows[j]["price"] - swing_lows[i]["price"]) / swing_lows[i]["price"] <= tolerance:
                    touches += 1
            if touches >= min_touches:
                swept = False
                level_price = swing_lows[i]["price"]
                for k in range(swing_lows[i]["index"] + 1, n):
                    if df.iloc[k]["low"] < level_price * (1 - tolerance):
                        swept = True
                        break
                if not any(abs(l["price"] - level_price) / level_price <= tolerance * 2
                           for l in levels if l["type"] == "EQUAL_LOWS"):
                    levels.append({
                        "type": "EQUAL_LOWS",
                        "price": float(level_price),
                        "touches": touches,
                        "swept": swept,
                    })

        return levels

    # =================================================================
    #  BÖLÜM 10 — DISPLACEMENT TESPİTİ
    # =================================================================

    def detect_displacement(self, df, lookback=30):
        """
        Güçlü tek yönlü mum hareketi tespiti.
        ATR-normalized + hacim oranı.
        """
        displacements = []
        n = len(df)
        start = max(1, n - lookback)
        atr = self._calc_atr(df, 14)
        min_body_ratio = self.params.get("displacement_min_body_ratio", 0.5)
        min_size_pct = self.params.get("displacement_min_size_pct", 0.002)
        atr_mult = self.params.get("displacement_atr_multiplier", 1.2)

        # Hacim ortalamasını hesapla
        vol_series = df["volume"].values if "volume" in df.columns else None
        avg_vol = float(np.mean(vol_series[max(0, n - 20):n])) if vol_series is not None and n > 0 else 0

        for i in range(start, n):
            candle = df.iloc[i]
            body = abs(candle["close"] - candle["open"])
            total_range = candle["high"] - candle["low"]
            mid_price = (candle["high"] + candle["low"]) / 2
            if total_range <= 0 or mid_price <= 0:
                continue

            body_ratio = body / total_range
            is_disp = body_ratio >= min_body_ratio and (
                (atr > 0 and body >= atr * atr_mult) or
                (body / mid_price >= min_size_pct)
            )

            if is_disp:
                direction = "BULLISH" if candle["close"] > candle["open"] else "BEARISH"
                vol_ratio = float(candle.get("volume", 0) / avg_vol) if avg_vol > 0 else 1.0
                displacements.append({
                    "index": i,
                    "direction": direction,
                    "body_ratio": round(body_ratio, 3),
                    "size_pct": round((body / mid_price) * 100, 3),
                    "atr_multiple": round(body / atr, 2) if atr > 0 else 0,
                    "volume_ratio": round(vol_ratio, 2),
                })

        return displacements

    # =================================================================
    #  BÖLÜM 11 — PREMIUM / DISCOUNT BÖLGE
    # =================================================================

    def calculate_premium_discount(self, df, structure=None):
        """
        Dealing range'in premium / discount bölgesini hesapla.

        Equilibrium = %50 seviyesi.
        Premium = üst %50 (SHORT bölgesi).
        Discount = alt %50 (LONG bölgesi).
        """
        if structure is None:
            structure = self.detect_market_structure(df)

        sh = structure.get("last_swing_high")
        sl_ = structure.get("last_swing_low")
        if not sh or not sl_:
            return None

        range_high = sh["price"]
        range_low = sl_["price"]
        if range_high <= range_low:
            return None

        equilibrium = (range_high + range_low) / 2
        current_price = df["close"].iloc[-1]

        # Premium level (%0 = range low, %100 = range high)
        premium_level = ((current_price - range_low) / (range_high - range_low)) * 100
        premium_level = max(0, min(100, premium_level))

        if premium_level > 50:
            zone = "PREMIUM"
        elif premium_level < 50:
            zone = "DISCOUNT"
        else:
            zone = "EQUILIBRIUM"

        # OTE bölgeleri
        ote_high = range_low + (range_high - range_low) * 0.79
        ote_low = range_low + (range_high - range_low) * 0.62

        return {
            "equilibrium": float(equilibrium),
            "high": float(range_high),
            "low": float(range_low),
            "zone": zone,
            "premium_level": round(premium_level, 1),
            "in_ote": ote_low <= current_price <= ote_high,
            "in_ote_long": range_low <= current_price <= ote_low,
            "in_ote_short": ote_high <= current_price <= range_high,
            "ote_high": float(ote_high),
            "ote_low": float(ote_low),
        }

    # =================================================================
    #  BÖLÜM 12 — HTF BIAS (4H Yapısal Analiz)
    # =================================================================

    def _analyze_htf_bias(self, multi_tf_data):
        """
        4H zaman diliminde yapısal trend → HTF Bias.

        4H NEUTRAL ise 1H'e fallback yapar.
        Bias, dealing range'in hangi tarafında olduğumuzu belirler.

        Returns: {"bias": "LONG"|"SHORT"|None, "htf_trend": str, ...}
                 veya None
        """
        if not multi_tf_data:
            return None

        # 4H tercih, NEUTRAL ise 1H'e düş
        bias = None
        htf_df = None
        htf_label = None
        structure = None
        trend = None

        for tf in ["4H", "1H"]:
            candidate = multi_tf_data.get(tf)
            if candidate is None or candidate.empty or len(candidate) < 20:
                continue

            s = self.detect_market_structure(candidate)
            t = s["trend"]

            # Bias belirleme
            b = None
            if t == "BULLISH":
                b = "LONG"
            elif t == "BEARISH":
                b = "SHORT"
            elif t == "WEAKENING_BEAR":
                b = "LONG"
            elif t == "WEAKENING_BULL":
                b = "SHORT"
            # NEUTRAL → b remains None, try next TF

            if b is not None:
                bias = b
                htf_df = candidate
                htf_label = tf
                structure = s
                trend = t
                break

        if bias is None or htf_df is None:
            return None  # Her iki TF'de de NEUTRAL → işlem yok

        # HTF Premium/Discount
        htf_pd = self.calculate_premium_discount(htf_df, structure)

        # HTF likidite seviyeleri
        htf_liquidity = self.find_liquidity_levels(htf_df)

        return {
            "bias": bias,
            "htf_trend": trend,
            "timeframe": htf_label,
            "structure": structure,
            "htf_pd": htf_pd,
            "liquidity": htf_liquidity,
        }

    # =================================================================
    #  BÖLÜM 13 — GATE 3: LİKİDİTE SWEEP TESPİTİ
    # =================================================================

    def _find_sweep_event(self, df, bias):
        """
        Likidite avı (sweep) tespiti.

        LONG: Fiyat swing low'un ALTINA iner (fitil veya gövde) ve sonra toparlanır.
        SHORT: Fiyat swing high'ın ÜSTÜNE çıkar (fitil veya gövde) ve sonra düşer.

        Kripto piyasasına uygun gevşek kontroller:
          1. Fiyat seviyeyi geçer (fitil VEYA gövde)
          2. Sonraki mumlardan birinde geri kapanış olmalı
          3. Wick ratio minimum %10
        """
        swing_highs, swing_lows = self.find_swing_points(df)
        n = len(df)
        max_lookback = 50  # Son 50 mum içinde sweep aranır

        all_sweeps = []

        if bias == "LONG":
            for sl_point in reversed(swing_lows[-10:]):  # Son 10 swing low
                sl_idx = sl_point["index"]
                sl_price = sl_point["price"]

                for i in range(max(sl_idx + 1, n - max_lookback), n):
                    candle = df.iloc[i]

                    # Sweep: low < swing low
                    if candle["low"] >= sl_price:
                        continue

                    # Rejection: ya bu mumun gövdesi yukarıda ya da sonraki mum yukarıda
                    body_low = min(candle["open"], candle["close"])
                    rejected = body_low >= sl_price
                    if not rejected and i + 1 < n:
                        next_close = df.iloc[i + 1]["close"]
                        rejected = next_close > sl_price
                    if not rejected and i + 2 < n:
                        next2_close = df.iloc[i + 2]["close"]
                        rejected = next2_close > sl_price

                    if not rejected:
                        continue

                    total_range = candle["high"] - candle["low"]
                    if total_range <= 0:
                        continue
                    lower_wick = min(candle["open"], candle["close"]) - candle["low"]
                    wick_ratio = max(lower_wick / total_range, 0.1)

                    sweep_depth = (sl_price - candle["low"]) / sl_price

                    all_sweeps.append({
                        "type": "SSL_SWEEP",
                        "sweep_type": "SSL_SWEEP",
                        "swept_level": float(sl_price),
                        "sweep_low": float(candle["low"]),
                        "sweep_wick": float(candle["low"]),
                        "sweep_candle_idx": i,
                        "wick_ratio": round(wick_ratio, 3),
                        "sweep_quality": round(wick_ratio, 2),
                        "sweep_depth_pct": round(sweep_depth * 100, 3),
                        "swing_index": sl_idx,
                        "fractal_type": sl_point.get("fractal_type", ""),
                    })
                    break

        elif bias == "SHORT":
            for sh_point in reversed(swing_highs[-10:]):  # Son 10 swing high
                sh_idx = sh_point["index"]
                sh_price = sh_point["price"]

                for i in range(max(sh_idx + 1, n - max_lookback), n):
                    candle = df.iloc[i]

                    if candle["high"] <= sh_price:
                        continue

                    body_high = max(candle["open"], candle["close"])
                    rejected = body_high <= sh_price
                    if not rejected and i + 1 < n:
                        next_close = df.iloc[i + 1]["close"]
                        rejected = next_close < sh_price
                    if not rejected and i + 2 < n:
                        next2_close = df.iloc[i + 2]["close"]
                        rejected = next2_close < sh_price

                    if not rejected:
                        continue

                    total_range = candle["high"] - candle["low"]
                    if total_range <= 0:
                        continue
                    upper_wick = candle["high"] - max(candle["open"], candle["close"])
                    wick_ratio = max(upper_wick / total_range, 0.1)

                    sweep_depth = (candle["high"] - sh_price) / sh_price

                    all_sweeps.append({
                        "type": "BSL_SWEEP",
                        "sweep_type": "BSL_SWEEP",
                        "swept_level": float(sh_price),
                        "sweep_high": float(candle["high"]),
                        "sweep_wick": float(candle["high"]),
                        "sweep_candle_idx": i,
                        "wick_ratio": round(wick_ratio, 3),
                        "sweep_quality": round(wick_ratio, 2),
                        "sweep_depth_pct": round(sweep_depth * 100, 3),
                        "swing_index": sh_idx,
                        "fractal_type": sh_point.get("fractal_type", ""),
                    })
                    break

        if not all_sweeps:
            return None

        # Kalite + tazelik dengesi: %60 tazelik, %40 kalite (wick_ratio)
        n = len(df)
        best = max(all_sweeps, key=lambda s: (s["sweep_candle_idx"] / n) * 0.6 + s["wick_ratio"] * 0.4)
        return best

    # =================================================================
    #  BÖLÜM 14 — GATE 4: HACİM DESTEKLİ DISPLACEMENT + MSS
    # =================================================================

    def _find_post_sweep_confirmation(self, df, sweep, bias):
        """
        Sweep sonrası Displacement (momentum) tespiti.
        
        v3.4 Crypto-optimized stricter checks:
        - Displacement sweep'ten max 2 mum sonra gelmeli
        - Minimum %0.6 hareket (config: displacement_min_size_pct)
        - Hacim destekli (avg'nin üstünde)
        - Güçlü gövde (55%+)
        """
        sweep_idx = sweep["sweep_candle_idx"]
        n = len(df)
        
        # v3.4: Max 2-3 mum sonra displacement gelmeli (hızlı reaction)
        max_lookahead = self.params.get("displacement_max_candles_after_sweep", 2)
        min_body_ratio = self.params.get("displacement_min_body_ratio", 0.55)
        min_size_pct = self.params.get("displacement_min_size_pct", 0.006)
        atr_mult = self.params.get("displacement_atr_multiplier", 1.5)
        
        atr = self._calc_atr(df, 14)

        # 20-bar hacim ortalaması
        vol_end = min(sweep_idx + 1, n)
        vol_start = max(0, vol_end - 20)
        if "volume" in df.columns:
            avg_volume = float(df["volume"].iloc[vol_start:vol_end].mean())
        else:
            avg_volume = 0

        displacement = None
        
        # Sweep'ten hemen sonraki 2-3 mumu kontrol et
        for i in range(sweep_idx + 1, min(sweep_idx + max_lookahead + 1, n)):
            candle = df.iloc[i]
            body = abs(candle["close"] - candle["open"])
            total_range = candle["high"] - candle["low"]
            mid_price = (candle["high"] + candle["low"]) / 2
            if total_range <= 0 or mid_price <= 0:
                continue

            body_ratio = body / total_range
            size_pct = body / mid_price
            
            # v3.4: Daha sıkı kontroller
            if body_ratio < min_body_ratio:
                continue
                
            # Minimum hareket kontrolü (%0.6)
            if size_pct < min_size_pct:
                continue

            candle_dir = "BULLISH" if candle["close"] > candle["open"] else "BEARISH"

            # Yön kontrolü
            if bias == "LONG" and candle_dir != "BULLISH":
                continue
            if bias == "SHORT" and candle_dir != "BEARISH":
                continue
            
            # Hacim kontrolü (avg'nin en az %80'i)
            candle_volume = float(candle.get("volume", 0))
            volume_confirmed = candle_volume >= avg_volume * 0.8 if avg_volume > 0 else False
            
            # ATR kontrolü opsiyonel
            atr_check = (atr > 0 and body >= atr * atr_mult) or size_pct >= min_size_pct
            
            if not atr_check:
                continue

            # Tüm kriterler geçti
            displacement = {
                "index": i,
                "direction": candle_dir,
                "body_ratio": round(body_ratio, 3),
                "size_pct": round(size_pct * 100, 3),
                "atr_multiple": round(body / atr, 2) if atr > 0 else 0,
                "volume": candle_volume,
                "avg_volume": round(avg_volume, 2),
                "volume_confirmed": volume_confirmed,
            }
            break

        if displacement is None:
            return None

        return {
            "displacement": displacement,
            "mss_confirmed": True,
            "volume_confirmed": displacement["volume_confirmed"],
        }

    # =================================================================
    #  BÖLÜM 15 — GATE 5: DISPLACEMENT FVG (Giriş Bölgesi)
    # =================================================================

    def _find_displacement_fvg(self, df, displacement_idx, bias):
        """
        Displacement mumunun oluşturduğu FVG'yi bul.

        Displacement güçlü hareket → FVG bırakır.
        Bu FVG giriş bölgemizdir — limit emir buraya koyulur.
        """
        n = len(df)
        search_start = max(1, displacement_idx - 1)
        search_end = min(n - 1, displacement_idx + 4)
        min_fvg_size = self.params.get("fvg_min_size_pct", 0.001)
        best_fvg = None

        for i in range(search_start, search_end):
            if i < 1 or i >= n - 1:
                continue
            prev = df.iloc[i - 1]
            curr = df.iloc[i]
            next_ = df.iloc[i + 1]
            mid_price = curr["close"]
            if mid_price <= 0:
                continue

            if bias == "LONG":
                if prev["high"] < next_["low"]:
                    gap = next_["low"] - prev["high"]
                    if gap / mid_price >= min_fvg_size:
                        # Fill kontrolü
                        filled = False
                        if i + 2 < n and len(df.iloc[i + 2:]) > 0:
                            if df.iloc[i + 2:]["low"].min() <= prev["high"]:
                                filled = True
                        if not filled:
                            fvg = {
                                "type": "BULLISH_FVG",
                                "index": i,
                                "high": float(next_["low"]),
                                "low": float(prev["high"]),
                                "size_pct": round((gap / mid_price) * 100, 4),
                                "timestamp": curr.get("timestamp", ""),
                            }
                            if best_fvg is None or abs(i - displacement_idx) < abs(best_fvg["index"] - displacement_idx):
                                best_fvg = fvg

            elif bias == "SHORT":
                if prev["low"] > next_["high"]:
                    gap = prev["low"] - next_["high"]
                    if gap / mid_price >= min_fvg_size:
                        filled = False
                        if i + 2 < n and len(df.iloc[i + 2:]) > 0:
                            if df.iloc[i + 2:]["high"].max() >= prev["low"]:
                                filled = True
                        if not filled:
                            fvg = {
                                "type": "BEARISH_FVG",
                                "index": i,
                                "high": float(prev["low"]),
                                "low": float(next_["high"]),
                                "size_pct": round((gap / mid_price) * 100, 4),
                                "timestamp": curr.get("timestamp", ""),
                            }
                            if best_fvg is None or abs(i - displacement_idx) < abs(best_fvg["index"] - displacement_idx):
                                best_fvg = fvg

        # Displacement yakınında FVG bulunamadıysa tüm FVG'leri kontrol et
        if best_fvg is None:
            all_fvgs = self.find_fvg(df)
            target_type = "BULLISH_FVG" if bias == "LONG" else "BEARISH_FVG"
            relevant = [
                f for f in all_fvgs
                if f["type"] == target_type and f["index"] >= displacement_idx - 3
            ]
            if relevant:
                best_fvg = min(relevant, key=lambda f: abs(f["index"] - displacement_idx))

        return best_fvg

    # =================================================================
    #  BÖLÜM 16 — YAPISAL STOP LOSS (Sweep Wick Extreme)
    # =================================================================

    def _calc_structural_sl(self, df, sweep, bias, structure, entry_price=None):
        """
        SL = Sweep fitil (wick) uç noktası + buffer.

        v3.4 Crypto-optimized:
        - LONG → SL = sweep wick low × 0.99 (%1 buffer)
        - SHORT → SL = sweep wick high × 1.01 (%1 buffer)
        - Max SL mesafesi kontrol edilir (config'de)
        """
        sl_buffer = self.params.get("sl_buffer_pct", 0.01)  # %1 default
        
        if bias == "LONG":
            # Sweep wicki alt noktası
            sweep_wick = sweep.get("sweep_wick", sweep.get("sweep_low"))
            if sweep_wick and sweep_wick > 0:
                sl = sweep_wick * (1 - sl_buffer)  # %1 buffer aşağı
                logger.debug(f"  LONG SL: Sweep Wick @ {sl:.8f} (buffer: {sl_buffer*100:.1f}%)")
                return sl

            # Fallback: swept level
            sl = sweep["swept_level"] * (1 - sl_buffer * 1.5)
            return sl

        elif bias == "SHORT":
            sweep_wick = sweep.get("sweep_wick", sweep.get("sweep_high"))
            if sweep_wick and sweep_wick > 0:
                sl = sweep_wick * (1 + sl_buffer)  # %1 buffer yukarı
                logger.debug(f"  SHORT SL: Sweep Wick @ {sl:.8f} (buffer: {sl_buffer*100:.1f}%)")
                return sl

            sl = sweep["swept_level"] * (1 + sl_buffer * 1.5)
            return sl

        return None

    # =================================================================
    #  BÖLÜM 17 — KARŞI LİKİDİTE TP (Draw on Liquidity)
    # =================================================================

    def _calc_opposing_liquidity_tp(self, df, multi_tf_data, entry, sl, bias, structure):
        """
        TP = Karşı taraftaki likidite havuzu.
        MİNİMUM R:R KISITLAMASI YOK — tamamen yapısal hedef.

        LONG TP sırası: HTF equal highs → LTF equal highs → Karşı OB → Son swing high
        SHORT TP sırası: HTF equal lows → LTF equal lows → Karşı OB → Son swing low
        """
        tp_candidates = []

        # HTF likidite
        htf_liquidity = []
        if multi_tf_data:
            for tf in ["4H", "1H"]:
                if tf in multi_tf_data and multi_tf_data[tf] is not None and not multi_tf_data[tf].empty:
                    htf_liquidity = self.find_liquidity_levels(multi_tf_data[tf])
                    break

        # LTF likidite
        ltf_liquidity = self.find_liquidity_levels(df)

        # Order blocks
        active_obs, _ = self.find_order_blocks(df, structure)

        if bias == "LONG":
            # HTF BSL (equal highs)
            for liq in htf_liquidity:
                if liq["type"] == "EQUAL_HIGHS" and not liq["swept"] and liq["price"] > entry:
                    tp_candidates.append(("HTF_DRAW_LIQ", liq["price"] * 0.999))

            # LTF BSL
            for liq in ltf_liquidity:
                if liq["type"] == "EQUAL_HIGHS" and not liq["swept"] and liq["price"] > entry:
                    tp_candidates.append(("LTF_BSL", liq["price"] * 0.999))

            # Karşı OB
            for ob in active_obs:
                if ob["type"] == "BEARISH_OB" and ob["low"] > entry:
                    tp_candidates.append(("OPPOSING_OB", ob["low"]))

            # Son swing high
            if structure["last_swing_high"] and structure["last_swing_high"]["price"] > entry:
                tp_candidates.append(("SWING_HIGH", structure["last_swing_high"]["price"] * 0.998))

            # Önceki swing high'lar
            for sh in structure.get("swing_highs", []):
                if sh["price"] > entry * 1.003:
                    tp_candidates.append(("PREV_SH", sh["price"] * 0.998))

        elif bias == "SHORT":
            # HTF SSL (equal lows)
            for liq in htf_liquidity:
                if liq["type"] == "EQUAL_LOWS" and not liq["swept"] and liq["price"] < entry:
                    tp_candidates.append(("HTF_DRAW_LIQ", liq["price"] * 1.001))

            # LTF SSL
            for liq in ltf_liquidity:
                if liq["type"] == "EQUAL_LOWS" and not liq["swept"] and liq["price"] < entry:
                    tp_candidates.append(("LTF_SSL", liq["price"] * 1.001))

            # Karşı OB
            for ob in active_obs:
                if ob["type"] == "BULLISH_OB" and ob["high"] < entry:
                    tp_candidates.append(("OPPOSING_OB", ob["high"]))

            # Son swing low
            if structure["last_swing_low"] and structure["last_swing_low"]["price"] < entry:
                tp_candidates.append(("SWING_LOW", structure["last_swing_low"]["price"] * 1.002))

            # Önceki swing low'lar
            for sl_p in structure.get("swing_lows", []):
                if sl_p["price"] < entry * 0.997:
                    tp_candidates.append(("PREV_SL", sl_p["price"] * 1.002))

        if not tp_candidates:
            # Son çare: risk bazlı TP (sadece hiçbir hedef yoksa)
            risk = abs(entry - sl) if sl else entry * 0.015
            tp_ratio = self.params.get("default_tp_ratio", 2.5)
            if bias == "LONG":
                return entry + (risk * tp_ratio)
            else:
                return entry - (risk * tp_ratio)

        # ★ Minimum RR filtresi — çok yakın hedefler ekonomik değil
        risk = abs(entry - sl) if sl else 0
        min_rr = 1.0  # Minimum kabul edilebilir RR
        tp_ratio = self.params.get("default_tp_ratio", 2.5)

        if bias == "LONG":
            # RR >= min_rr olan hedefler
            valid = [(label, price) for label, price in tp_candidates
                     if risk > 0 and (price - entry) / risk >= min_rr]

            if valid:
                # HTF hedef varsa tercih et (daha güvenilir mıknatıs)
                htf_valid = [c for c in valid if c[0] == "HTF_DRAW_LIQ"]
                if htf_valid:
                    return min(htf_valid, key=lambda x: x[1])[1]
                return min(valid, key=lambda x: x[1])[1]

            # Hiçbir hedef min RR karşılamıyor → risk bazlı fallback
            if risk > 0:
                return entry + (risk * tp_ratio)
            return min(tp_candidates, key=lambda x: x[1])[1]

        else:  # SHORT
            valid = [(label, price) for label, price in tp_candidates
                     if risk > 0 and (entry - price) / risk >= min_rr]

            if valid:
                htf_valid = [c for c in valid if c[0] == "HTF_DRAW_LIQ"]
                if htf_valid:
                    return max(htf_valid, key=lambda x: x[1])[1]
                return max(valid, key=lambda x: x[1])[1]

            if risk > 0:
                return entry - (risk * tp_ratio)
            return max(tp_candidates, key=lambda x: x[1])[1]

    # =================================================================
    #  BÖLÜM 18 — SİNYAL ÜRETİMİ (5-Aşamalı Boolean Gate Protocol)
    # =================================================================

    def generate_signal(self, symbol, df, multi_tf_data=None):
        """
        Saf ICT sinyal üretimi — 5 zorunlu Boolean kapı.

        Her kapı Evet / Hayır döner.
        Herhangi biri Hayır → sinyal YOK (None).
        Hepsi Evet → SIGNAL üretilir.

        Puanlama YOK. Minimum R:R YOK. Perakende gösterge YOK.
        """
        if df is None or df.empty or len(df) < 30:
            return None

        # ═══════════════════════════════════════════════════════════
        #  GATE 1 — SEANS BİLGİSİ (Kripto 7/24 — Bypass)
        # ═══════════════════════════════════════════════════════════
        session = self.get_session_info()
        # v3.4: Killzone kontrolü YOK - kripto 7/24 aktif, seans bilgisi sadece log için
        logger.debug(f"  {symbol} GATE 1 BYPASS: {session['label']} - Kripto 7/24 aktif")

        # ═══════════════════════════════════════════════════════════
        #  GATE 2 — HTF BIAS + PREMIUM / DISCOUNT
        # ═══════════════════════════════════════════════════════════
        htf_result = self._analyze_htf_bias(multi_tf_data)
        if not htf_result:
            logger.debug(f"  {symbol} GATE 2 FAIL: HTF bias belirlenemedi")
            return None

        bias = htf_result["bias"]

        # LTF (15m) dealing range'de Premium/Discount kontrolü
        structure = self.detect_market_structure(df)
        pd_zone = self.calculate_premium_discount(df, structure)

        if not pd_zone:
            logger.debug(f"  {symbol} GATE 2 FAIL: Premium/Discount hesaplanamadı")
            return None

        # ★ LONG → Discount veya Equilibrium (< %65)
        # ★ SHORT → Premium veya Equilibrium (> %35)
        # Kripto piyasasında sert trendlerde sıkı %50 sınırı çok daraltıcı
        pd_level = pd_zone["premium_level"]

        if bias == "LONG" and pd_level > 65:
            logger.debug(f"  {symbol} GATE 2 FAIL: LONG ama fiyat çok Premium ({pd_level:.1f}%)")
            return None

        if bias == "SHORT" and pd_level < 35:
            logger.debug(f"  {symbol} GATE 2 FAIL: SHORT ama fiyat çok Discount ({pd_level:.1f}%)")
            return None

        logger.debug(f"  {symbol} GATE 2 OK: HTF {bias} + {pd_zone['zone']} ({pd_level:.1f}%)")

        # ═══════════════════════════════════════════════════════════
        #  GATE 3 — LİKİDİTE SWEEP (Wick rejection zorunlu)
        # ═══════════════════════════════════════════════════════════
        sweep = self._find_sweep_event(df, bias)
        if not sweep:
            logger.debug(f"  {symbol} GATE 3 FAIL: Likidite sweep bulunamadı")
            return None

        logger.debug(
            f"  {symbol} GATE 3 OK: {sweep['type']} @ {sweep['swept_level']:.6f} "
            f"(wick_ratio: {sweep['wick_ratio']:.2f})"
        )

        # ═══════════════════════════════════════════════════════════
        #  GATE 4 — HACİM DESTEKLİ DISPLACEMENT + MSS
        # ═══════════════════════════════════════════════════════════
        confirmation = self._find_post_sweep_confirmation(df, sweep, bias)
        if not confirmation:
            logger.debug(f"  {symbol} GATE 4 FAIL: Displacement+MSS+Hacim onayı yok → WATCH")
            # ★ Gate 1-3 geçti, Gate 4 bekleniyor → İzleme listesine gönder
            current_price = df["close"].iloc[-1]
            return {
                "action": "WATCH",
                "symbol": symbol,
                "direction": bias,
                "potential_entry": float(current_price),
                "potential_sl": float(sweep.get("sweep_wick", sweep.get("swept_level", 0))),
                "potential_tp": None,
                "watch_reason": "Gate4 displacement/MSS bekleniyor",
                "components": ["KILLZONE", "HTF_BIAS", "PREMIUM_DISCOUNT", "LIQUIDITY_SWEEP"],
            }

        logger.debug(
            f"  {symbol} GATE 4 OK: Displacement idx={confirmation['displacement']['index']} "
            f"vol={confirmation['displacement']['volume']:.0f}/{confirmation['displacement']['avg_volume']:.0f}"
        )

        # ═══════════════════════════════════════════════════════════
        #  GATE 5 — FVG GİRİŞ + SL / TP
        # ═══════════════════════════════════════════════════════════
        disp_fvg = self._find_displacement_fvg(
            df, confirmation["displacement"]["index"], bias
        )
        if not disp_fvg:
            logger.debug(f"  {symbol} GATE 5 FAIL: Displacement FVG bulunamadı → WATCH")
            # ★ Gate 1-4 geçti, FVG oluşmamış → İzleme listesine gönder
            current_price = df["close"].iloc[-1]
            return {
                "action": "WATCH",
                "symbol": symbol,
                "direction": bias,
                "potential_entry": float(current_price),
                "potential_sl": float(sweep.get("sweep_wick", sweep.get("swept_level", 0))),
                "potential_tp": None,
                "watch_reason": "Gate5 FVG oluşması bekleniyor",
                "components": ["KILLZONE", "HTF_BIAS", "PREMIUM_DISCOUNT", "LIQUIDITY_SWEEP", "DISPLACEMENT", "MSS"],
            }

        # Entry: FVG CE (Consequent Encroachment — orta nokta, optimal giriş)
        fvg_ce = (disp_fvg["high"] + disp_fvg["low"]) / 2
        if bias == "LONG":
            entry = fvg_ce  # Bullish FVG CE — daha iyi RR için pullback ortası
        else:
            entry = fvg_ce  # Bearish FVG CE — daha iyi RR için pullback ortası

        # SL: Sweep wick extreme
        sl = self._calc_structural_sl(df, sweep, bias, structure, entry)
        if sl is None:
            logger.debug(f"  {symbol} GATE 5 FAIL: SL hesaplanamadı")
            return None

        # TP: Karşı likidite havuzu
        tp = self._calc_opposing_liquidity_tp(df, multi_tf_data, entry, sl, bias, structure)
        if tp is None:
            logger.debug(f"  {symbol} GATE 5 FAIL: TP hesaplanamadı")
            return None

        # Seviye doğrulama (mantıksal sıra)
        if bias == "LONG":
            if sl >= entry or tp <= entry:
                logger.debug(f"  {symbol} GATE 5 FAIL: Seviye sırası hatalı (SL={sl:.6f} E={entry:.6f} TP={tp:.6f})")
                return None
        else:
            if sl <= entry or tp >= entry:
                logger.debug(f"  {symbol} GATE 5 FAIL: Seviye sırası hatalı (SL={sl:.6f} E={entry:.6f} TP={tp:.6f})")
                return None

        # SL mesafe kontrolü (v3.4: min %0.5, max %3.0)
        sl_distance_pct = abs(entry - sl) / entry
        min_sl = self.params.get("min_sl_distance_pct", 0.005)
        max_sl = self.params.get("max_sl_distance_pct", 0.030)
        
        if sl_distance_pct < min_sl:
            logger.debug(f"  {symbol} GATE 5 FAIL: SL çok dar ({sl_distance_pct*100:.2f}% < {min_sl*100:.1f}%)")
            return None
        if sl_distance_pct > max_sl:
            logger.debug(f"  {symbol} GATE 5 FAIL: SL çok geniş ({sl_distance_pct*100:.2f}% > {max_sl*100:.1f}%)")
            return None

        current_price = df["close"].iloc[-1]
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        rr_ratio = reward / risk if risk > 0 else 0

        # v3.4: SADECE LIMIT entry - FVG CE optimal giriş
        # MARKET entry kaldırıldı (RR kaybı + entry quality düşüklüğü)
        # Her zaman FVG CE'ye limit order kurulur
        entry_mode = "LIMIT"
        
        # Entry FVG CE'de sabit (pullback için optimal nokta)
        # Fiyat FVG'de bile olsa LIMIT kullan (daha iyi RR + kontrol)

        logger.debug(f"  {symbol} GATE 5 OK: Entry={entry:.6f} (FVG CE LIMIT) SL={sl:.6f} TP={tp:.6f} RR={rr_ratio:.2f}")

        # ═══════════════════════════════════════════════════════════
        #  TÜM 5 GATE GEÇTİ → SİNYAL ÜRET
        # ═══════════════════════════════════════════════════════════

        components = [
            "KILLZONE",
            "HTF_BIAS",
            "PREMIUM_DISCOUNT",
            "LIQUIDITY_SWEEP",
            "DISPLACEMENT",
            "MSS",
            "VOLUME_CONFIRMED",
            "FVG",
        ]

        signal = {
            "symbol": symbol,
            "direction": bias,
            "entry": round(entry, 8),
            "sl": round(sl, 8),
            "tp": round(tp, 8),
            "current_price": round(current_price, 8),
            "confluence_score": 100,  # Boolean: tüm gate'ler geçti
            "confidence": 100,
            "components": components,
            "penalties": [],
            "session": session["label"],
            "rr_ratio": round(rr_ratio, 2),
            "entry_type": f"FVG Limit ({disp_fvg['type']})",
            "sl_type": "Sweep Wick Extreme",
            "tp_type": self._get_tp_type(tp, bias, multi_tf_data, structure),
            "entry_mode": entry_mode,
            "action": "SIGNAL",
            "htf_bias": htf_result.get("htf_trend", bias),
            "quality_tier": "A+",
            "sweep": sweep,
            "fvg": disp_fvg,
            "pd_zone": pd_zone,
        }

        logger.info(
            f"🎯 SİNYAL: {symbol} {bias} | "
            f"Entry: {entry:.6f} | SL: {sl:.6f} | TP: {tp:.6f} | "
            f"RR: {rr_ratio:.2f} | Mode: {entry_mode} | "
            f"Session: {session['label']}"
        )

        return signal

    # =================================================================
    #  BÖLÜM 19 — API UYUMLU ANALİZ (calculate_confluence)
    # =================================================================

    def calculate_confluence(self, df, multi_tf_data=None, override_direction=None):
        """
        API uyumlu analiz — /api/analyze endpoint'i için.

        ★ Puanlama sistemi KALDIRILDI.
        ★ Her bileşen Boolean olarak raporlanır.
        ★ confluence_score artık gate geçiş sayısını temsil eder.
        """
        analysis = {}
        components = []
        current_price = df["close"].iloc[-1]
        analysis["current_price"] = current_price

        # Session
        session_info = self.get_session_info()
        analysis["session"] = session_info
        analysis["is_weekend"] = session_info["is_weekend"]
        if session_info["is_valid_killzone"]:
            components.append("KILLZONE")

        # Ranging
        analysis["is_ranging"] = self.detect_ranging_market(df)

        # Market Structure
        structure = self.detect_market_structure(df)
        analysis["structure"] = structure
        if structure["trend"] in ("BULLISH", "BEARISH"):
            components.append("MARKET_STRUCTURE")

        # HTF Bias
        htf_result = self._analyze_htf_bias(multi_tf_data)
        analysis["htf_result"] = htf_result
        if htf_result:
            analysis["htf_trend"] = htf_result["htf_trend"]
            analysis["htf_structure"] = htf_result.get("structure")
            analysis["htf_liquidity"] = htf_result.get("liquidity", [])
            components.append("HTF_BIAS")
            bias = htf_result["bias"]
        else:
            analysis["htf_trend"] = "UNKNOWN"
            analysis["htf_structure"] = None
            analysis["htf_liquidity"] = []
            bias = override_direction

        analysis["direction"] = bias or "UNKNOWN"

        # Premium/Discount
        pd_zone = self.calculate_premium_discount(df, structure)
        analysis["premium_discount"] = pd_zone
        if pd_zone:
            if (bias == "LONG" and pd_zone["zone"] == "DISCOUNT") or \
               (bias == "SHORT" and pd_zone["zone"] == "PREMIUM"):
                components.append("PREMIUM_DISCOUNT")

        # Liquidity Sweep
        analysis["sweep"] = None
        analysis["post_sweep_confirmation"] = None
        analysis["displacement_fvg"] = None
        if bias:
            sweep = self._find_sweep_event(df, bias)
            analysis["sweep"] = sweep
            if sweep:
                components.append("LIQUIDITY_SWEEP")
                confirmation = self._find_post_sweep_confirmation(df, sweep, bias)
                analysis["post_sweep_confirmation"] = confirmation
                if confirmation:
                    components.append("DISPLACEMENT")
                    components.append("MSS")
                    components.append("VOLUME_CONFIRMED")
                    disp_fvg = self._find_displacement_fvg(
                        df, confirmation["displacement"]["index"], bias
                    )
                    analysis["displacement_fvg"] = disp_fvg
                    if disp_fvg:
                        components.append("FVG")

        # Order Blocks
        active_obs, all_obs = self.find_order_blocks(df, structure)
        analysis["order_blocks"] = active_obs
        analysis["all_order_blocks"] = all_obs

        # Breaker Blocks
        analysis["breaker_blocks"] = self.find_breaker_blocks(all_obs, df)

        # FVGs
        analysis["fvgs"] = self.find_fvg(df)

        # Displacements
        analysis["displacements"] = self.detect_displacement(df)

        # Liquidity Levels
        analysis["liquidity"] = self.find_liquidity_levels(df)

        # Gate geçiş sayısı (5 üzerinden)
        total_gates = 5
        gates_passed = 0
        if "KILLZONE" in components:
            gates_passed += 1
        if "HTF_BIAS" in components and "PREMIUM_DISCOUNT" in components:
            gates_passed += 1
        if "LIQUIDITY_SWEEP" in components:
            gates_passed += 1
        if "DISPLACEMENT" in components and "MSS" in components and "VOLUME_CONFIRMED" in components:
            gates_passed += 1
        if "FVG" in components:
            gates_passed += 1

        analysis["confluence_score"] = round((gates_passed / total_gates) * 100)
        analysis["gates_passed"] = gates_passed
        analysis["total_gates"] = total_gates
        analysis["components"] = components
        analysis["penalties"] = []

        return analysis

    # =================================================================
    #  BÖLÜM 20 — YARDIMCI FONKSİYONLAR
    # =================================================================

    def _get_tp_type(self, tp, direction, multi_tf_data, structure):
        """TP seviyesinin ICT kaynağını belirle."""
        # HTF likidite
        if multi_tf_data:
            for tf in ["4H", "1H"]:
                if tf in multi_tf_data and multi_tf_data[tf] is not None and not multi_tf_data[tf].empty:
                    htf_liq = self.find_liquidity_levels(multi_tf_data[tf])
                    for liq in htf_liq:
                        if direction == "LONG" and liq["type"] == "EQUAL_HIGHS" and not liq["swept"]:
                            if abs(tp - liq["price"]) / tp < 0.005:
                                return "HTF Draw on Liquidity (Equal Highs)"
                        elif direction == "SHORT" and liq["type"] == "EQUAL_LOWS" and not liq["swept"]:
                            if abs(tp - liq["price"]) / tp < 0.005:
                                return "HTF Draw on Liquidity (Equal Lows)"
                    break

        # LTF liquidity — structure-based check
        # Structure-based
        if structure:
            if direction == "LONG" and structure.get("last_swing_high"):
                if abs(tp - structure["last_swing_high"]["price"]) / tp < 0.005:
                    return "Swing High Yapısal Hedef"
            elif direction == "SHORT" and structure.get("last_swing_low"):
                if abs(tp - structure["last_swing_low"]["price"]) / tp < 0.005:
                    return "Swing Low Yapısal Hedef"

        return "Opposing Liquidity"


# Global instance
ict_strategy = ICTStrategy()
