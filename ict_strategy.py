# =====================================================
# ICT Trading Bot - Akıllı Para Strateji Motoru v2.0
# (Smart Money Concepts - Sequential Gate Protocol)
# =====================================================
#
# PROTOKOL — KATI SIRALI ICT MODELİ:
# ====================================
# Adım 1  HTF Bias (4H)          → HARD GATE
#          4 saatlik grafikteki BOS/CHoCH ile yön belirlenir.
#          BULLISH yapı → SADECE LONG,  BEARISH yapı → SADECE SHORT.
#
# Adım 2  Liquidity Sweep (15m)  → HARD GATE
#          LTF'de eski bir Swing High/Low seviyesinin
#          fitille temizlenip (wick beyond) geri kapanmasını bekle.
#          Bu "stop hunt / likidite avı" paternidir.
#
# Adım 3  Displacement + MSS     → HARD GATE
#          Sweep sonrası ters yöne güçlü hacimli mum (displacement)
#          ve Market Structure Shift (BOS veya CHoCH) tespit et.
#
# Adım 4  FVG Entry Zone         → GİRİŞ BELİRLEME
#          Displacement mumunun oluşturduğu Fair Value Gap
#          tespit edilir. Bu FVG "Giriş Bölgesi" olur.
#          Entry = FVG'nin CE (Consequent Encroachment = orta noktası).
#
# SL → Sweep yapısının invalidation noktası (yapısal seviye)
# TP → Karşı taraf likidite havuzu (Draw on Liquidity)
#
# RSI, MACD gibi retail indikatörler KULLANILMAZ.
# Tüm kararlar Price Action & Market Structure üzerine kuruludur.
# =====================================================

import numpy as np
import pandas as pd
import logging
from datetime import datetime, timezone
from config import ICT_PARAMS
from database import get_bot_param

logger = logging.getLogger("ICT-Bot.Strategy")


class ICTStrategy:
    """
    Akıllı Para (Smart Money) Strateji Motoru.
    Yukarıda anlatılan 4 adımlı katı sıralı protokolü uygular.
    """

    def __init__(self):
        self.params = self._load_params()

    def _load_params(self):
        """Veritabanından güncel parametreleri yükle, yoksa config varsayılanı kullan."""
        params = {}
        for key, default_val in ICT_PARAMS.items():
            db_val = get_bot_param(key)
            params[key] = db_val if db_val is not None else default_val
        return params

    def reload_params(self):
        """Parametreleri yeniden yükle (optimizer güncellemesi sonrası)."""
        self.params = self._load_params()

    # =================================================================
    #  BÖLÜM 1 — SESSION / KILLZONE
    # =================================================================

    def get_session_info(self):
        """
        Kripto-optimize oturum bilgisi.
        Kripto 7/24 işlem görür — forex killzone'ları aynen geçerli ama
        Asya oturumu kripto için ÇOK AKTİF bir dönemdir (ceza yok).
          London Killzone  07-10 UTC  (kurumsal, yüksek volatilite)
          NY Killzone      12-15 UTC  (kurumsal, trend devamı)
          London-NY Geçiş  10-12 UTC  (overlap hazırlık)
          London Kapanış   15-17 UTC  (geri çekilmeler)
          Asya Oturumu     00-07 UTC  (kripto aktif, likidite oluşumu)
          Geçiş Saatleri   17-00 UTC  (daha az momentum, hâlâ aktif)
        """
        now = datetime.now(timezone.utc)
        hour = now.hour

        # ICT Session Saatleri (UTC):
        #   London KZ:     07-10 (kurumsal açılış, yüksek volatilite)
        #   Geçiş:         10-12 (London aktif, NY hazırlık)
        #   NY KZ/Overlap: 12-15 (London + NY aktif = en yüksek likidite)
        #   London Close:  15-17 (geri çekilme, reversal riski)
        #   Asya:          00-07 (kripto için aktif dönem)
        #   Off-peak:      17-24 (düşük momentum)
        if 7 <= hour < 10:
            return {"session": "LONDON_KILLZONE", "quality": 1.0, "label": "London Killzone"}
        elif 12 <= hour < 15:
            # London hâlâ açık + NY açılışı = gerçek overlap ve en güçlü dönem
            return {"session": "NY_KILLZONE_OVERLAP", "quality": 1.0, "label": "NY KZ / London-NY Overlap"}
        elif 10 <= hour < 12:
            return {"session": "LONDON_CONTINUATION", "quality": 0.9, "label": "London Devam / NY Hazırlık"}
        elif 15 <= hour < 17:
            return {"session": "LONDON_CLOSE", "quality": 0.8, "label": "London Kapanış"}
        elif 0 <= hour < 7:
            return {"session": "ASIAN", "quality": 0.85, "label": "Asya Oturumu (Kripto Aktif)"}
        else:
            return {"session": "OFF_HOURS", "quality": 0.7, "label": "Geçiş Saatleri"}

    # =================================================================
    #  BÖLÜM 2 — YATAY PİYASA TESPİTİ
    # =================================================================

    def _calc_atr(self, df, period=14):
        """
        ATR (Average True Range) hesapla.
        Volatilite normalizasyonu için tüm modüllerde kullanılır.
        """
        if len(df) < period + 1:
            # Yeterli veri yoksa basit range ortalaması
            ranges = (df["high"] - df["low"]).values
            return float(np.mean(ranges)) if len(ranges) > 0 else 0.0

        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values

        tr_list = []
        for i in range(1, len(df)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1])
            )
            tr_list.append(tr)

        if len(tr_list) < period:
            return float(np.mean(tr_list)) if tr_list else 0.0

        # EMA-based ATR
        atr = np.mean(tr_list[:period])
        multiplier = 2.0 / (period + 1)
        for tr in tr_list[period:]:
            atr = (tr - atr) * multiplier + atr

        return float(atr)

    def detect_ranging_market(self, df, lookback=20):
        """
        ATR-adaptif yatay piyasa tespiti.
        Sabit eşik yerine ATR tabanlı dinamik threshold kullanır.
        Akümülasyon sonrası trend başlangıçlarını kaçırmamak için
        slope analizi de eklendi.
        Returns: True = ranging → sinyal üretme.
        """
        if len(df) < lookback:
            return False

        recent = df.tail(lookback)
        closes = recent["close"].values
        highs = recent["high"].values
        lows = recent["low"].values
        avg_price = np.mean(closes)

        # Net hareket / toplam hareket (efficiency ratio)
        net_move = abs(closes[-1] - closes[0])
        total_move = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
        efficiency = net_move / total_move if total_move > 0 else 0

        # ATR tabanlı dinamik range threshold
        atr = self._calc_atr(df)
        atr_pct = atr / avg_price if avg_price > 0 else 0.01
        # Range genişliği ATR'nin kaç katı? < 1.5 ATR = çok dar = ranging
        total_range = np.max(highs) - np.min(lows)
        range_atr_ratio = total_range / atr if atr > 0 else 999

        # Slope analizi: son kapanışlara lineer regresyon
        # Eğim > 0 = trend başlıyor olabilir (akümülasyon çıkışı)
        x = np.arange(len(closes))
        if len(closes) >= 5:
            slope = np.polyfit(x, closes, 1)[0]
            slope_pct = abs(slope * lookback) / avg_price if avg_price > 0 else 0
        else:
            slope_pct = 0

        # Adaptif ranging tespiti:
        # 1) Efficiency çok düşük + range dar (ATR tabanlı) = ranging
        # 2) AMA slope güçlüyse (>0.8%) = trend başlıyor, ranging değil
        if slope_pct >= 0.008:
            # Güçlü eğim = akümülasyon çıkışı olabilir → ranging değil
            return False

        is_ranging = (
            (efficiency < 0.08 and range_atr_ratio < 2.0) or
            (efficiency < 0.04 and range_atr_ratio < 3.0)
        )

        if is_ranging:
            logger.debug(f"  📊 Ranging market: eff={efficiency:.3f}, "
                         f"range/ATR={range_atr_ratio:.1f}, slope={slope_pct:.4f}")

        return is_ranging

    # =================================================================
    #  BÖLÜM 3 — SWING POINTS (Yapı Taşları)
    # =================================================================

    def find_swing_points(self, df, lookback=None):
        """
        Hibrit Swing Point tespiti:
        1) Ana pivotlar: lookback (5) mumluk standart swing tespiti
        2) Internal fractals: 3 mumluk hızlı yapı tespiti (repainting riski azaltır)
        Internal fractal'lar MSS ve displacement tespitinde lag'ı önler.
        """
        if lookback is None:
            lookback = int(self.params["swing_lookback"])

        highs = df["high"].values
        lows = df["low"].values
        n = len(df)
        swing_highs = []
        swing_lows = []
        seen_highs = set()
        seen_lows = set()

        # 1) Ana pivotlar (standart lookback)
        for i in range(lookback, n - lookback):
            is_sh = all(highs[i] > highs[i - j] and highs[i] > highs[i + j]
                        for j in range(1, lookback + 1))
            if is_sh:
                swing_highs.append({
                    "index": i, "price": highs[i],
                    "timestamp": df["timestamp"].iloc[i],
                    "fractal_type": "MAJOR"
                })
                seen_highs.add(i)

            is_sl = all(lows[i] < lows[i - j] and lows[i] < lows[i + j]
                        for j in range(1, lookback + 1))
            if is_sl:
                swing_lows.append({
                    "index": i, "price": lows[i],
                    "timestamp": df["timestamp"].iloc[i],
                    "fractal_type": "MAJOR"
                })
                seen_lows.add(i)

        # 2) Internal fractals (3 mumluk) — sadece son 15 mumda
        #    Bu, displacement/MSS tespitinde lag'ı önler
        internal_lookback = 2  # 2 mum sağ-sol (toplam 5, ama daha hızlı onaylanır)
        start_idx = max(internal_lookback, n - 15)
        for i in range(start_idx, n - internal_lookback):
            if i in seen_highs:
                continue
            is_sh = all(highs[i] > highs[i - j] and highs[i] > highs[i + j]
                        for j in range(1, internal_lookback + 1))
            if is_sh:
                swing_highs.append({
                    "index": i, "price": highs[i],
                    "timestamp": df["timestamp"].iloc[i],
                    "fractal_type": "INTERNAL"
                })

            if i in seen_lows:
                continue
            is_sl = all(lows[i] < lows[i - j] and lows[i] < lows[i + j]
                        for j in range(1, internal_lookback + 1))
            if is_sl:
                swing_lows.append({
                    "index": i, "price": lows[i],
                    "timestamp": df["timestamp"].iloc[i],
                    "fractal_type": "INTERNAL"
                })

        # Index sırasına göre sırala
        swing_highs.sort(key=lambda x: x["index"])
        swing_lows.sort(key=lambda x: x["index"])

        return swing_highs, swing_lows

    # =================================================================
    #  BÖLÜM 4 — MARKET STRUCTURE (BOS / CHoCH)
    # =================================================================

    def detect_market_structure(self, df):
        """
        Piyasa yapısını analiz et:
        - BOS  (Break of Structure):  Mevcut trend yönünde yapı kırılımı.
        - CHoCH (Change of Character): Trendden ters yöne yapı değişimi.
        - Trend tespiti: HH+HL = Bullish,  LH+LL = Bearish.
        """
        swing_highs, swing_lows = self.find_swing_points(df)

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return {
                "trend": "NEUTRAL", "bos_events": [], "choch_events": [],
                "swing_highs": swing_highs, "swing_lows": swing_lows,
                "last_swing_high": None, "last_swing_low": None
            }

        bos_events = []
        choch_events = []
        current_trend = "NEUTRAL"
        min_displacement = self.params["bos_min_displacement"]

        # Tüm swing noktalarını index sırasına göre birleştir
        all_swings = []
        for sh in swing_highs:
            all_swings.append({"type": "HIGH", **sh})
        for sl in swing_lows:
            all_swings.append({"type": "LOW", **sl})
        all_swings.sort(key=lambda x: x["index"])

        # Yapı kırılımlarını tespit et
        for i in range(2, len(all_swings)):
            current = all_swings[i]
            # Aynı türden bir önceki swing'i bul
            prev_same = None
            for j in range(i - 1, -1, -1):
                if all_swings[j]["type"] == current["type"]:
                    prev_same = all_swings[j]
                    break
            if prev_same is None:
                continue

            if current["type"] == "HIGH":
                if current["price"] > prev_same["price"]:
                    displacement = (current["price"] - prev_same["price"]) / prev_same["price"]
                    if displacement > min_displacement:
                        if current_trend == "BEARISH":
                            choch_events.append({
                                "type": "BULLISH_CHOCH", "index": current["index"],
                                "price": current["price"], "prev_price": prev_same["price"],
                                "timestamp": current["timestamp"]
                            })
                        else:
                            bos_events.append({
                                "type": "BULLISH_BOS", "index": current["index"],
                                "price": current["price"], "prev_price": prev_same["price"],
                                "timestamp": current["timestamp"]
                            })
                        current_trend = "BULLISH"
                else:
                    if current_trend == "BULLISH":
                        current_trend = "WEAKENING_BULL"

            elif current["type"] == "LOW":
                if current["price"] < prev_same["price"]:
                    displacement = (prev_same["price"] - current["price"]) / prev_same["price"]
                    if displacement > min_displacement:
                        if current_trend == "BULLISH":
                            choch_events.append({
                                "type": "BEARISH_CHOCH", "index": current["index"],
                                "price": current["price"], "prev_price": prev_same["price"],
                                "timestamp": current["timestamp"]
                            })
                        else:
                            bos_events.append({
                                "type": "BEARISH_BOS", "index": current["index"],
                                "price": current["price"], "prev_price": prev_same["price"],
                                "timestamp": current["timestamp"]
                            })
                        current_trend = "BEARISH"
                else:
                    if current_trend == "BEARISH":
                        current_trend = "WEAKENING_BEAR"

        return {
            "trend": current_trend,
            "bos_events": bos_events[-5:] if bos_events else [],
            "choch_events": choch_events[-3:] if choch_events else [],
            "swing_highs": swing_highs,
            "swing_lows": swing_lows,
            "last_swing_high": swing_highs[-1] if swing_highs else None,
            "last_swing_low": swing_lows[-1] if swing_lows else None
        }

    # =================================================================
    #  BÖLÜM 5 — ORDER BLOCKS
    # =================================================================

    def find_order_blocks(self, df, structure):
        """
        Order Block tespiti:
        - Bullish OB: BOS/CHoCH öncesi son bearish mum
          (Kurumlar burada büyük alım yaptı → fiyat geri gelirse destek olur)
        - Bearish OB: BOS/CHoCH öncesi son bullish mum
          (Kurumlar burada büyük satış yaptı → fiyat geri gelirse direnç olur)
        """
        order_blocks = []
        max_age = int(self.params["ob_max_age_candles"])
        min_body_ratio = self.params["ob_body_ratio_min"]
        current_idx = len(df) - 1
        events = structure.get("bos_events", []) + structure.get("choch_events", [])

        for event in events:
            event_idx = event["index"]
            if current_idx - event_idx > max_age:
                continue

            is_bullish_event = "BULLISH" in event["type"]

            # Olay öncesi karşı yönlü mumu bul
            for j in range(event_idx - 1, max(event_idx - 10, 0), -1):
                if j >= len(df):
                    continue
                candle = df.iloc[j]
                body = abs(candle["close"] - candle["open"])
                total_range = candle["high"] - candle["low"]
                if total_range <= 0:
                    continue
                body_ratio = body / total_range

                if is_bullish_event:
                    # Bullish event öncesi bearish mum
                    if candle["close"] < candle["open"] and body_ratio >= min_body_ratio:
                        order_blocks.append({
                            "type": "BULLISH_OB", "index": j,
                            "high": candle["high"], "low": candle["low"],
                            "open": candle["open"], "close": candle["close"],
                            "timestamp": candle["timestamp"],
                            "mitigated": False, "strength": body_ratio
                        })
                        break
                else:
                    # Bearish event öncesi bullish mum
                    if candle["close"] > candle["open"] and body_ratio >= min_body_ratio:
                        order_blocks.append({
                            "type": "BEARISH_OB", "index": j,
                            "high": candle["high"], "low": candle["low"],
                            "open": candle["open"], "close": candle["close"],
                            "timestamp": candle["timestamp"],
                            "mitigated": False, "strength": body_ratio
                        })
                        break

        # Mitigated kontrol — fiyat OB bölgesinden geçtiyse artık geçersiz
        for ob in order_blocks:
            after_candles = df.iloc[ob["index"] + 1:]
            if len(after_candles) == 0:
                continue
            if ob["type"] == "BULLISH_OB" and after_candles["low"].min() < ob["low"]:
                ob["mitigated"] = True
            elif ob["type"] == "BEARISH_OB" and after_candles["high"].max() > ob["high"]:
                ob["mitigated"] = True

        active_obs = [ob for ob in order_blocks if not ob["mitigated"]]
        return active_obs, order_blocks

    # =================================================================
    #  BÖLÜM 6 — BREAKER BLOCKS
    # =================================================================

    def find_breaker_blocks(self, all_order_blocks, df):
        """
        Breaker Block: Mitigate olmuş OB'nin karşı yönde güçlü S/R haline gelmesi.
        - Kırılmış Bullish OB → Bearish Breaker (direnç)
        - Kırılmış Bearish OB → Bullish Breaker (destek)
        ICT'de yüksek olasılıklı setup'lardan biri.
        """
        breaker_blocks = []
        current_price = df["close"].iloc[-1]
        current_idx = len(df) - 1

        for ob in all_order_blocks:
            if not ob["mitigated"]:
                continue
            if current_idx - ob["index"] > 40:
                continue

            if ob["type"] == "BULLISH_OB":
                if current_price >= ob["low"] * 0.998 and current_price <= ob["high"] * 1.005:
                    breaker_blocks.append({
                        "type": "BEARISH_BREAKER",
                        "high": ob["high"], "low": ob["low"],
                        "index": ob["index"], "timestamp": ob["timestamp"]
                    })
            elif ob["type"] == "BEARISH_OB":
                if current_price >= ob["low"] * 0.995 and current_price <= ob["high"] * 1.002:
                    breaker_blocks.append({
                        "type": "BULLISH_BREAKER",
                        "high": ob["high"], "low": ob["low"],
                        "index": ob["index"], "timestamp": ob["timestamp"]
                    })

        return breaker_blocks

    # =================================================================
    #  BÖLÜM 7 — FAIR VALUE GAPS (FVG)
    # =================================================================

    def find_fvg(self, df):
        """
        Fair Value Gap tespiti (3 mumlu imbalance paterni):
        - Bullish FVG: mum[i-1].high < mum[i+1].low → arada boşluk (fiyat geri dönüp doldurmaya çalışır)
        - Bearish FVG: mum[i-1].low > mum[i+1].high → arada boşluk (fiyat geri çıkıp doldurmaya çalışır)
        Doldurulmamış FVG'ler güçlü giriş noktalarıdır — kurumsal emir boşluğu.
        """
        fvgs = []
        max_age = int(self.params["fvg_max_age_candles"])
        min_size_pct = self.params["fvg_min_size_pct"]
        n = len(df)
        current_idx = n - 1

        for i in range(1, n - 1):
            if current_idx - i > max_age:
                continue

            prev_c = df.iloc[i - 1]
            curr_c = df.iloc[i]
            next_c = df.iloc[i + 1]
            mid_price = curr_c["close"]
            if mid_price <= 0:
                continue

            # Bullish FVG
            if prev_c["high"] < next_c["low"]:
                gap = next_c["low"] - prev_c["high"]
                if gap / mid_price >= min_size_pct:
                    filled = False
                    if i + 2 < n:
                        # ICT: FVG'nin CE (orta nokta) noktasını geçtiyse filled
                        # Sadece alt sınıra (prev_c.high) dokunmak = "tested", hâlâ geçerli
                        ce_point = (prev_c["high"] + next_c["low"]) / 2
                        if df.iloc[i + 2:]["low"].min() <= ce_point:
                            filled = True
                    if not filled:
                        fvgs.append({
                            "type": "BULLISH_FVG", "index": i,
                            "high": next_c["low"], "low": prev_c["high"],
                            "size_pct": round((gap / mid_price) * 100, 4),
                            "timestamp": curr_c["timestamp"], "filled": False
                        })

            # Bearish FVG
            if prev_c["low"] > next_c["high"]:
                gap = prev_c["low"] - next_c["high"]
                if gap / mid_price >= min_size_pct:
                    filled = False
                    if i + 2 < n:
                        # ICT: FVG'nin CE (orta nokta) noktasını geçtiyse filled
                        ce_point = (next_c["high"] + prev_c["low"]) / 2
                        if df.iloc[i + 2:]["high"].max() >= ce_point:
                            filled = True
                    if not filled:
                        fvgs.append({
                            "type": "BEARISH_FVG", "index": i,
                            "high": prev_c["low"], "low": next_c["high"],
                            "size_pct": round((gap / mid_price) * 100, 4),
                            "timestamp": curr_c["timestamp"], "filled": False
                        })

        return fvgs

    # =================================================================
    #  BÖLÜM 8 — LIQUIDITY LEVELS (Equal Highs / Lows)
    # =================================================================

    def find_liquidity_levels(self, df):
        """
        Likidite seviyelerini tespit et:
        - Equal Highs: Aynı seviyede biriken tepeler → üstte BSL (buy-side liq.)
        - Equal Lows:  Aynı seviyede biriken dipler  → altta SSL (sell-side liq.)
        Bu seviyeler, kurumların stopları temizlemek için hedeflediği bölgelerdir.
        """
        tolerance = self.params["liquidity_equal_tolerance"]
        min_touches = int(self.params["liquidity_min_touches"])
        swing_highs, swing_lows = self.find_swing_points(df)
        liquidity_levels = []

        # Equal Highs → BSL (Buy-Side Liquidity)
        for i, sh in enumerate(swing_highs):
            touches = 1
            touched_indices = [sh["index"]]
            for j in range(i + 1, len(swing_highs)):
                if abs(swing_highs[j]["price"] - sh["price"]) / sh["price"] <= tolerance:
                    touches += 1
                    touched_indices.append(swing_highs[j]["index"])
            if touches >= min_touches:
                exists = any(
                    ll["type"] == "EQUAL_HIGHS" and
                    abs(ll["price"] - sh["price"]) / sh["price"] <= tolerance
                    for ll in liquidity_levels
                )
                if not exists:
                    swept = False
                    max_idx = max(touched_indices)
                    if max_idx + 1 < len(df):
                        if df.iloc[max_idx + 1:]["high"].max() > sh["price"] * (1 + tolerance):
                            swept = True
                    liquidity_levels.append({
                        "type": "EQUAL_HIGHS", "price": sh["price"],
                        "touches": touches, "indices": touched_indices,
                        "swept": swept, "side": "SELL"
                    })

        # Equal Lows → SSL (Sell-Side Liquidity)
        for i, sl in enumerate(swing_lows):
            touches = 1
            touched_indices = [sl["index"]]
            for j in range(i + 1, len(swing_lows)):
                if abs(swing_lows[j]["price"] - sl["price"]) / sl["price"] <= tolerance:
                    touches += 1
                    touched_indices.append(swing_lows[j]["index"])
            if touches >= min_touches:
                exists = any(
                    ll["type"] == "EQUAL_LOWS" and
                    abs(ll["price"] - sl["price"]) / sl["price"] <= tolerance
                    for ll in liquidity_levels
                )
                if not exists:
                    swept = False
                    max_idx = max(touched_indices)
                    if max_idx + 1 < len(df):
                        if df.iloc[max_idx + 1:]["low"].min() < sl["price"] * (1 - tolerance):
                            swept = True
                    liquidity_levels.append({
                        "type": "EQUAL_LOWS", "price": sl["price"],
                        "touches": touches, "indices": touched_indices,
                        "swept": swept, "side": "BUY"
                    })

        return liquidity_levels

    # =================================================================
    #  BÖLÜM 9 — DISPLACEMENT (Güçlü Momentum Mumları)
    # =================================================================

    def detect_displacement(self, df, lookback=10):
        """
        ATR-Normalized Displacement tespiti.
        Sabit %0.5 threshold yerine 1.5 × ATR(14) kullanır.
        Bu sayede:
        - BTC gibi volatil coinlerde küçük mumlar yanlışlıkla displacement sayılmaz
        - Düşük volatiliteli coinlerde gerçek displacement kaçırılmaz
        Hacim analizi: Yüksek hacimli displacement daha güvenilirdir.
        """
        displacements = []
        min_body_ratio = self.params["displacement_min_body_ratio"]
        n = len(df)

        # ATR tabanlı dinamik displacement threshold
        atr = self._calc_atr(df, period=14)
        atr_multiplier = 1.5  # Mum gövdesi en az 1.5 × ATR olmalı

        # Fallback: ATR hesaplanamadıysa sabit threshold kullan
        min_size_pct = self.params["displacement_min_size_pct"]

        # Ortalama hacim
        has_volume = "volume" in df.columns and df["volume"].sum() > 0
        avg_volume = df["volume"].rolling(20).mean().iloc[-1] if has_volume else 0

        for i in range(max(0, n - lookback), n):
            candle = df.iloc[i]
            body = abs(candle["close"] - candle["open"])
            total_range = candle["high"] - candle["low"]
            mid_price = (candle["high"] + candle["low"]) / 2
            if total_range <= 0 or mid_price <= 0:
                continue
            body_ratio = body / total_range

            # ★ ATR-Normalized: gövde >= 1.5 × ATR VEYA sabit threshold
            is_displacement = body_ratio >= min_body_ratio and (
                (atr > 0 and body >= atr * atr_multiplier) or
                (body / mid_price >= min_size_pct)
            )

            if is_displacement:
                direction = "BULLISH" if candle["close"] > candle["open"] else "BEARISH"

                # Hacim analizi
                vol_ratio = 1.0
                if has_volume and avg_volume > 0:
                    vol_ratio = round(candle["volume"] / avg_volume, 2)

                # ATR katı bilgisi (debugging için)
                atr_multiple = round(body / atr, 2) if atr > 0 else 0

                displacements.append({
                    "type": f"{direction}_DISPLACEMENT", "index": i,
                    "body_ratio": round(body_ratio, 3),
                    "size_pct": round((body / mid_price) * 100, 3),
                    "atr_multiple": atr_multiple,
                    "direction": direction, "timestamp": candle["timestamp"],
                    "volume_ratio": vol_ratio
                })

        return displacements

    # =================================================================
    #  BÖLÜM 10 — PREMIUM / DISCOUNT + OTE
    # =================================================================

    def calculate_premium_discount(self, df, structure):
        """
        Premium/Discount bölgeleri:
        Son swing high-low arasının %50 seviyesi = Equilibrium (denge noktası).
        Premium (üst yarı) = Satış bölgesi — SHORT için ideal.
        Discount (alt yarı) = Alış bölgesi — LONG için ideal.
        OTE (Optimal Trade Entry) = Fibonacci 0.618-0.786 arası → en ideal giriş.
        """
        last_high = structure.get("last_swing_high")
        last_low = structure.get("last_swing_low")
        if not last_high or not last_low:
            return None

        high_price = last_high["price"]
        low_price = last_low["price"]
        equilibrium = (high_price + low_price) / 2
        current_price = df["close"].iloc[-1]

        fib_range = high_price - low_price
        ote_high = low_price + fib_range * 0.786
        ote_low = low_price + fib_range * 0.618

        zone = "PREMIUM" if current_price > equilibrium else "DISCOUNT"

        return {
            "high": high_price, "low": low_price,
            "equilibrium": equilibrium, "current_price": current_price,
            "zone": zone, "ote_high": ote_high, "ote_low": ote_low,
            "in_ote": ote_low <= current_price <= ote_high,
            "premium_level": round(
                (current_price - low_price) / (high_price - low_price) * 100, 1
            ) if high_price != low_price else 50
        }

    # =================================================================
    #  BÖLÜM 10.5 — MTF (1H) DOĞRULAMA KATMANI
    # =================================================================

    def _analyze_mtf_confirmation(self, multi_tf_data, ltf_structure, direction):
        """
        ★ MTF (1H) Doğrulama Katmanı.

        4H bias ile 15m entry arasında köprü görevi görür.
        Kontroller:
          1. 1H trend yönü, 15m (LTF) ile aynı mı?
          2. 1H'deki aktif Order Block'lar LTF entry bölgesiyle çakışıyor mu?
          3. 1H'deki doldurulmamış FVG'ler LTF entry bölgesinde mi?

        Sonuç:
          - bias_aligned=True  → 1H onaylıyor (+10 puan)
          - ob_confluence=True → 1H OB çakışması (+5 bonus)
          - fvg_confluence=True → 1H FVG çakışması (+3 bonus)
          - bias_conflict=True → 1H karşı yönde (-8 ceza)
        """
        if not multi_tf_data or "1H" not in multi_tf_data:
            return None

        mtf_df = multi_tf_data["1H"]
        if mtf_df is None or mtf_df.empty or len(mtf_df) < 20:
            return None

        # 1H yapı analizi
        mtf_structure = self.detect_market_structure(mtf_df)
        mtf_trend = mtf_structure["trend"]

        # Bias uyumu
        bias_aligned = False
        bias_conflict = False

        if direction == "LONG":
            if mtf_trend in ["BULLISH", "WEAKENING_BEAR"]:
                bias_aligned = True
            elif mtf_trend == "BEARISH":
                bias_conflict = True
        elif direction == "SHORT":
            if mtf_trend in ["BEARISH", "WEAKENING_BULL"]:
                bias_aligned = True
            elif mtf_trend == "BULLISH":
                bias_conflict = True

        # 1H Order Block çakışma kontrolü
        ob_confluence = False
        active_obs, all_obs = self.find_order_blocks(mtf_df, mtf_structure)
        current_price = mtf_df["close"].iloc[-1]

        for ob in active_obs:
            if direction == "LONG" and ob["type"] == "BULLISH_OB":
                # Fiyat 1H bullish OB bölgesinde veya yakınında mı?
                if ob["low"] * 0.995 <= current_price <= ob["high"] * 1.01:
                    ob_confluence = True
                    break
            elif direction == "SHORT" and ob["type"] == "BEARISH_OB":
                if ob["low"] * 0.99 <= current_price <= ob["high"] * 1.005:
                    ob_confluence = True
                    break

        # 1H FVG çakışma kontrolü
        fvg_confluence = False
        mtf_fvgs = self.find_fvg(mtf_df)

        for fvg in mtf_fvgs:
            if direction == "LONG" and fvg["type"] == "BULLISH_FVG":
                if fvg["low"] * 0.998 <= current_price <= fvg["high"] * 1.005:
                    fvg_confluence = True
                    break
            elif direction == "SHORT" and fvg["type"] == "BEARISH_FVG":
                if fvg["low"] * 0.995 <= current_price <= fvg["high"] * 1.002:
                    fvg_confluence = True
                    break

        result = {
            "mtf_trend": mtf_trend,
            "bias_aligned": bias_aligned,
            "bias_conflict": bias_conflict,
            "ob_confluence": ob_confluence,
            "fvg_confluence": fvg_confluence,
            "structure": mtf_structure,
            "active_obs": len(active_obs),
            "active_fvgs": len(mtf_fvgs),
        }

        if bias_aligned:
            logger.debug(f"  📊 MTF (1H) ONAYLADI: trend={mtf_trend}, OB={ob_confluence}, FVG={fvg_confluence}")
        elif bias_conflict:
            logger.debug(f"  ⚠️ MTF (1H) UYUMSUZ: trend={mtf_trend} vs direction={direction}")

        return result

    # =================================================================
    #  BÖLÜM 11 — GATE 1: HTF BIAS (4 Saatlik Yapı Analizi)
    # =================================================================

    def _analyze_htf_bias(self, multi_tf_data):
        """
        ★ GATE 1 — HTF (4H) yapısından KESİN yön tespiti.

        4H BOS/CHoCH yukarıysa → SADECE LONG aranır.
        4H BOS/CHoCH aşağıysa → SADECE SHORT aranır.
        Belirsizse (NEUTRAL) → İŞLEM YAPILMAZ.

        ★ v3.0: 4H Premium/Discount matrisi eklendi.
        4H Bullish ama fiyat 4H Premium bölgesindeyse → riskli LONG.
        4H Bearish ama fiyat 4H Discount bölgesindeyse → riskli SHORT.

        Returns: {"bias": "LONG"/"SHORT", "htf_trend": str, "structure": dict,
                  "htf_pd": dict, "htf_extreme": bool}
                 veya None (belirsiz → işlem yok)
        """
        if not multi_tf_data or "4H" not in multi_tf_data:
            return None

        htf_df = multi_tf_data["4H"]
        if htf_df is None or htf_df.empty or len(htf_df) < 30:
            return None

        structure = self.detect_market_structure(htf_df)
        htf_liquidity = self.find_liquidity_levels(htf_df)

        # ── 4H Premium/Discount Matrisi ──
        htf_pd = self.calculate_premium_discount(htf_df, structure)

        result_base = {
            "structure": structure,
            "liquidity": htf_liquidity,
            "htf_pd": htf_pd,
            "htf_extreme": False,
        }

        if structure["trend"] == "BULLISH":
            # 4H Bullish + Fiyat 4H Extreme Premium (%80+) → riskli LONG
            if htf_pd and htf_pd["premium_level"] > 80:
                result_base["htf_extreme"] = True
                logger.debug(f"  ⚠️ 4H Bullish ama Extreme Premium ({htf_pd['premium_level']:.0f}%)")
            return {**result_base, "bias": "LONG", "htf_trend": "BULLISH", "weak": False}

        elif structure["trend"] == "BEARISH":
            # 4H Bearish + Fiyat 4H Extreme Discount (%20-) → riskli SHORT
            if htf_pd and htf_pd["premium_level"] < 20:
                result_base["htf_extreme"] = True
                logger.debug(f"  ⚠️ 4H Bearish ama Extreme Discount ({htf_pd['premium_level']:.0f}%)")
            return {**result_base, "bias": "SHORT", "htf_trend": "BEARISH", "weak": False}

        elif structure["trend"] in ("WEAKENING_BEAR", "WEAKENING_BULL"):
            # WEAKENING trendler güvenilmez — net yapı kırılımı yok → İŞLEM YAPILMAZ
            logger.debug(f"  ⚠️ 4H trend WEAKENING ({structure['trend']}) — net yön yok, atlanıyor")
            return None

        return None  # NEUTRAL → NET YÖN YOK → İŞLEM YAPILMAZ

    # =================================================================
    #  BÖLÜM 12 — GATE 2: LIQUIDITY SWEEP (Likidite Avı)
    # =================================================================

    def _find_sweep_event(self, df, bias, lookback=20):
        """
        ★ GATE 2 — Likidite Avı (Stop Hunt) Tespiti.

        Bu ICT'nin kalbindeki kavramdır: Kurumlar, bireysel yatırımcıların
        stop-loss emirlerini tetiklemek için fiyatı kasıtlı olarak eski
        swing noktalarının ötesine iter, sonra asıl yöne döner.

        LONG bias → Fiyat eski bir Swing Low'un ALTINA fitil atar ve
                     ÜSTÜNDE kapanır (stop hunt → smart money alım)
        SHORT bias → Fiyat eski bir Swing High'ın ÜSTÜNE fitil atar ve
                      ALTINDA kapanır (stop hunt → smart money satış)

        lookback=20 (15m'de ~5 saat): Daha eski sweep'ler "soğumuş" olur,
        kurumsal aktiviteyle zamansal ilişkisi zayıflar.

        Returns: {"swept_level": float, "sweep_candle_idx": int, ...}
                 veya None (sweep yok)
        """
        swing_highs, swing_lows = self.find_swing_points(df)
        liquidity_levels = self.find_liquidity_levels(df)
        n = len(df)

        # Hacim verisi mevcut mu?
        has_volume = "volume" in df.columns and df["volume"].sum() > 0
        avg_volume = df["volume"].rolling(20).mean().iloc[-1] if has_volume else 0

        # Session kalitesi (killzone sweep'leri daha değerli)
        session = self.get_session_info()
        session_quality = session.get("quality", 0.7)

        # ── PDH/PDL (Previous Day High/Low) ve Session Range Seviyeleri ──
        # 15m veride ~96 mum = 1 gün. Son 96-192 arası = önceki gün
        major_levels = self._calc_major_levels(df)

        if bias == "LONG":
            # LONG → SSL (Sell-Side Liquidity) avı → eski swing low altına fitil
            for sw in reversed(swing_lows):
                sw_price = sw["price"]
                sw_idx = sw["index"]
                # Sweep sonraki mumlarda olmalı
                for i in range(sw_idx + 1, min(sw_idx + lookback + 1, n)):
                    candle = df.iloc[i]
                    # Wick sw_price altına inip, close sw_price üstünde mi?
                    if candle["low"] < sw_price and candle["close"] > sw_price:
                        # Sweep çok eski olmamalı
                        if n - 1 - i <= lookback:
                            # === SWEEP KALİTE SKORU ===
                            sweep_quality = self._calc_sweep_quality(
                                sw_price, candle, i, n, bias,
                                liquidity_levels, has_volume, avg_volume,
                                session_quality, df=df
                            )
                            # ── Major Level Bonusu ──
                            is_major = self._is_major_level_sweep(
                                sw_price, bias, major_levels
                            )
                            if is_major:
                                sweep_quality = min(2.5, sweep_quality + 0.4)
                            # Minimum kalite filtresi: düşük kalite sweep kabul edilmez
                            if sweep_quality >= 1.0:
                                return {
                                    "swept_level": sw_price,
                                    "sweep_candle_idx": i,
                                    "sweep_wick": candle["low"],
                                    "sweep_type": "SSL_SWEEP",
                                    "swing_index": sw_idx,
                                    "sweep_quality": sweep_quality,
                                    "major_level": is_major,
                                }

        elif bias == "SHORT":
            # SHORT → BSL (Buy-Side Liquidity) avı → eski swing high üstüne fitil
            for sw in reversed(swing_highs):
                sw_price = sw["price"]
                sw_idx = sw["index"]
                for i in range(sw_idx + 1, min(sw_idx + lookback + 1, n)):
                    candle = df.iloc[i]
                    # Wick sw_price üstüne çıkıp, close sw_price altında mı?
                    if candle["high"] > sw_price and candle["close"] < sw_price:
                        if n - 1 - i <= lookback:
                            sweep_quality = self._calc_sweep_quality(
                                sw_price, candle, i, n, bias,
                                liquidity_levels, has_volume, avg_volume,
                                session_quality, df=df
                            )
                            is_major = self._is_major_level_sweep(
                                sw_price, bias, major_levels
                            )
                            if is_major:
                                sweep_quality = min(2.5, sweep_quality + 0.4)
                            # Minimum kalite filtresi: düşük kalite sweep kabul edilmez
                            if sweep_quality >= 1.0:
                                return {
                                    "swept_level": sw_price,
                                    "sweep_candle_idx": i,
                                    "sweep_wick": candle["high"],
                                    "sweep_type": "BSL_SWEEP",
                                    "swing_index": sw_idx,
                                    "sweep_quality": sweep_quality,
                                    "major_level": is_major,
                                }

        return None

    def _calc_major_levels(self, df):
        """
        PDH/PDL (Previous Day High/Low) ve Session Range seviyelerini hesapla.
        15m veride ~96 mum = 1 gün.
        
        Returns: {"pdh": float, "pdl": float, "session_high": float, "session_low": float}
        """
        n = len(df)
        result = {}

        # PDH/PDL: Son 96-192 arası (önceki gün)
        if n >= 192:
            prev_day = df.iloc[-192:-96]
            result["pdh"] = prev_day["high"].max()
            result["pdl"] = prev_day["low"].min()
        elif n >= 96:
            # Yeterli veri yoksa mevcut günün önceki yarısını kullan
            half = n // 2
            prev_half = df.iloc[:half]
            result["pdh"] = prev_half["high"].max()
            result["pdl"] = prev_half["low"].min()

        # Session Range: Son 28 mum (~7 saat = yaklaşık 1 session)
        session_candles = min(28, n)
        session_data = df.iloc[-session_candles:]
        result["session_high"] = session_data["high"].max()
        result["session_low"] = session_data["low"].min()

        return result

    def _is_major_level_sweep(self, swept_level, bias, major_levels):
        """
        Sweep edilen seviye PDH/PDL veya Session High/Low mu?
        ICT'de bu seviyeler en güçlü likidite havuzlarıdır.
        """
        tolerance = 0.003  # %0.3 tolerans

        if bias == "LONG":
            # SSL sweep → PDL veya Session Low yakınında mı?
            pdl = major_levels.get("pdl")
            session_low = major_levels.get("session_low")
            if pdl and abs(swept_level - pdl) / pdl <= tolerance:
                return "PDL"
            if session_low and abs(swept_level - session_low) / session_low <= tolerance:
                return "SESSION_LOW"
        elif bias == "SHORT":
            # BSL sweep → PDH veya Session High yakınında mı?
            pdh = major_levels.get("pdh")
            session_high = major_levels.get("session_high")
            if pdh and abs(swept_level - pdh) / pdh <= tolerance:
                return "PDH"
            if session_high and abs(swept_level - session_high) / session_high <= tolerance:
                return "SESSION_HIGH"

        return None

    # =================================================================
    #  BÖLÜM 12.5 — UNICORN SETUP (OB + FVG Geometrik Çakışma)
    # =================================================================

    def _detect_unicorn_setup(self, df, fvg, bias, structure):
        """
        Unicorn Setup: Order Block + FVG geometrik çakışma.

        ICT'nin en güçlü setup'larından biri — iki kurumsal ayak izi
        aynı fiyat bölgesinde üst üste gelir:
          • Order Block: Kurumsal emir bölgesi (önceki güçlü mum)
          • FVG: Emir boşluğu (3 mumlu imbalance)

        Bu çakışma, kurumların aynı bölgede hem emir bıraktığını hem de
        fiyat boşluğu yarattığını gösterir → çok yüksek olasılıklı giriş.

        LONG: Bullish OB ∩ Bullish FVG → entry overlap'in alt kenarı
        SHORT: Bearish OB ∩ Bearish FVG → entry overlap'in üst kenarı

        Returns: dict veya None
        """
        if not fvg:
            return None

        active_obs, _ = self.find_order_blocks(df, structure)
        target_ob_type = "BULLISH_OB" if bias == "LONG" else "BEARISH_OB"

        best_unicorn = None

        for ob in active_obs:
            if ob["type"] != target_ob_type:
                continue

            # Geometrik çakışma: OB ve FVG arasında overlap var mı?
            overlap_low = max(ob["low"], fvg["low"])
            overlap_high = min(ob["high"], fvg["high"])

            if overlap_low >= overlap_high:
                continue  # Çakışma yok

            # Overlap bölgesinin boyutunu kontrol et
            fvg_size = fvg["high"] - fvg["low"]
            if fvg_size <= 0:
                continue

            overlap_size = overlap_high - overlap_low
            overlap_ratio = overlap_size / fvg_size

            # Minimum %20 overlap olmalı
            if overlap_ratio < 0.20:
                continue

            # Junction entry: LONG → overlap'in alt kısmı (discount giriş)
            #                  SHORT → overlap'in üst kısmı (premium giriş)
            if bias == "LONG":
                junction_entry = overlap_low
            else:
                junction_entry = overlap_high

            candidate = {
                "ob": ob,
                "fvg": fvg,
                "overlap_low": overlap_low,
                "overlap_high": overlap_high,
                "overlap_ratio": round(overlap_ratio, 3),
                "junction_entry": junction_entry,
            }

            # En büyük overlap'i tercih et
            if best_unicorn is None or overlap_ratio > best_unicorn["overlap_ratio"]:
                best_unicorn = candidate

        if best_unicorn:
            logger.info(
                f"🦄 UNICORN SETUP: OB({target_ob_type}) ∩ FVG → "
                f"Overlap: {best_unicorn['overlap_ratio']:.0%} | "
                f"Junction entry: {best_unicorn['junction_entry']:.8f}"
            )

        return best_unicorn

    def _calc_sweep_quality(self, swept_level, candle, candle_idx, n, bias,
                            liquidity_levels, has_volume, avg_volume, session_quality,
                            df=None):
        """
        Geliştirilmiş Sweep kalite skoru (0.0 - 2.5 arası çarpan).

        Kaliteyi belirleyen faktörler:
        1. Equal highs/lows sweep'i mi? (çok dokunuşlu seviye = daha güçlü)
        2. Sweep sırasında hacim yüksek mi? (kurumsal onay)
        3. Killzone sırasında mı? (kurumsal aktivite saatleri)
        4. Sweep ne kadar taze? (yeni sweep > eski sweep)
        5. ★ YENİ: Sweep öncesi compression (sıkışma) var mı?
        6. ★ YENİ: Time-in-liquidity (fitil oranı kontrolü)
        7. ★ YENİ: OB + liquidity alignment
        """
        quality = 1.0  # Baz kalite

        # 1) Equal highs/lows seviyesi mi? (çok dokunuşlu = çok stop birikmiş)
        tolerance = self.params.get("liquidity_equal_tolerance", 0.001)
        for liq in liquidity_levels:
            if abs(liq["price"] - swept_level) / swept_level <= tolerance * 2:
                touches = liq.get("touches", 2)
                if touches >= 3:
                    quality += 0.4  # 3+ dokunuş = güçlü likidite havuzu
                else:
                    quality += 0.2  # 2 dokunuş = normal equal level
                break

        # 2) Hacim analizi: Sweep mumunda ortalama üstü hacim
        if has_volume and avg_volume > 0:
            sweep_vol = candle["volume"]
            vol_ratio = sweep_vol / avg_volume
            if vol_ratio >= 2.0:
                quality += 0.3  # 2x hacim = güçlü kurumsal aktivite
            elif vol_ratio >= 1.5:
                quality += 0.15
        else:
            # Hacimsiz sweep = potansiyel fake → ceza
            quality -= 0.15

        # 3) Killzone sırasında mı?
        if session_quality >= 1.0:
            quality += 0.2  # London/NY killzone = en kaliteli sweep
        elif session_quality >= 0.85:
            quality += 0.1  # Asya aktif dönem

        # 4) Tazelik: Son 5 mum içinde = taze, 10+ = soğumuş
        age = n - 1 - candle_idx
        if age <= 3:
            quality += 0.1
        elif age >= 15:
            quality -= 0.2

        # 5) ★ Sweep öncesi compression (sıkışma) kontrolü
        #    Sweep öncesi 5+ mum dar range'de sıkışmışsa → birikmiş enerji
        #    Bu gerçek kurumsal manipulation işaretidir
        if df is not None and candle_idx >= 6:
            pre_sweep = df.iloc[max(0, candle_idx - 6):candle_idx]
            if len(pre_sweep) >= 4:
                pre_ranges = (pre_sweep["high"] - pre_sweep["low"]).values
                sweep_range = candle["high"] - candle["low"]
                avg_pre_range = np.mean(pre_ranges)
                if avg_pre_range > 0 and sweep_range > 0:
                    # Sweep öncesi range ortalaması sweep mumunun %60'ından azsa = compression
                    compression_ratio = avg_pre_range / sweep_range
                    if compression_ratio < 0.6:
                        quality += 0.25  # Compression sonrası sweep = güçlü

        # 6) ★ Time-in-liquidity: Fitil oranı kontrolü
        #    Gerçek sweep = uzun fitil + geri kapanış
        #    Fake sweep = çok küçük fitil, anlamsız dokunma
        body = abs(candle["close"] - candle["open"])
        total_range = candle["high"] - candle["low"]
        if total_range > 0:
            if bias == "LONG":
                # SSL sweep: alt fitil oranı
                lower_wick = min(candle["open"], candle["close"]) - candle["low"]
                wick_ratio = lower_wick / total_range
            else:
                # BSL sweep: üst fitil oranı
                upper_wick = candle["high"] - max(candle["open"], candle["close"])
                wick_ratio = upper_wick / total_range

            if wick_ratio >= 0.4:
                quality += 0.2  # Belirgin fitil = gerçek sweep
            elif wick_ratio < 0.1:
                quality -= 0.3  # Neredeyse fitilsiz = muhtemelen fake

        # 7) ★ OB + liquidity alignment: Sweep seviyesinde OB var mı?
        # NOT: structure dışarıdan alınmaya çalışılır (performans için)
        # Her sweep quality hesabında detect_market_structure tekrar çağırmak gereksiz
        if df is not None:
            # structure zaten generate_signal'de hesaplandı, burada basit OB arama yap
            # Sadece sweep civarındaki mumları kontrol ederek yaklaşık OB bul
            active_obs = []
            sweep_idx = getattr(candle, 'name', None) or (len(df) - 1)
            search_start = max(0, sweep_idx - 20)
            for idx in range(search_start, min(sweep_idx, len(df))):
                c = df.iloc[idx]
                c_body = abs(c["close"] - c["open"])
                c_range = c["high"] - c["low"]
                if c_range <= 0:
                    continue
                c_body_ratio = c_body / c_range
                if c_body_ratio < 0.4:
                    continue
                if bias == "LONG" and c["close"] < c["open"]:  # Bearish candle before bullish move
                    active_obs.append({"type": "BULLISH_OB", "low": c["low"], "high": c["high"]})
                elif bias == "SHORT" and c["close"] > c["open"]:  # Bullish candle before bearish move
                    active_obs.append({"type": "BEARISH_OB", "low": c["low"], "high": c["high"]})
            for ob in active_obs:
                if bias == "LONG" and ob["type"] == "BULLISH_OB":
                    if ob["low"] <= swept_level <= ob["high"] * 1.005:
                        quality += 0.2  # OB + sweep alignment
                        break
                elif bias == "SHORT" and ob["type"] == "BEARISH_OB":
                    if ob["low"] * 0.995 <= swept_level <= ob["high"]:
                        quality += 0.2  # OB + sweep alignment
                        break

        return round(max(0.3, min(2.5, quality)), 2)

    # =================================================================
    #  BÖLÜM 13 — GATE 3: DISPLACEMENT + MSS (Onay)
    # =================================================================

    def _find_post_sweep_confirmation(self, df, sweep, bias):
        """
        ★ GATE 3 — Sweep Sonrası Displacement + Market Structure Shift.

        Sweep tek başına yeterli değil — ardından gerçek dönüş onayı gerekli:

        1. DISPLACEMENT: Sweep'ten sonra bias yönünde güçlü hacimli mum.
           Bu mum kurumsal aktivitenin "ayak izi"dir.

        2. MSS (Market Structure Shift): Displacement sonrası yapı kırılımı.
           LONG → Son swing high kırılır (Bullish BOS/CHoCH)
           SHORT → Son swing low kırılır (Bearish BOS/CHoCH)

        Minimum displacement ZORUNLU. MSS güven bonus'u sağlar.

        Returns: {"displacement": dict, "mss_confirmed": bool}
                 veya None (onay yok)
        """
        sweep_idx = sweep["sweep_candle_idx"]
        n = len(df)
        max_lookahead = 15  # Sweep sonrası max 15 mum içinde olmalı

        # --- ATR-Normalized Displacement Tespiti ---
        min_body_ratio = self.params.get("displacement_min_body_ratio", 0.7)
        min_size_pct = self.params.get("displacement_min_size_pct", 0.005)
        atr = self._calc_atr(df, period=14)
        displacement = None

        for i in range(sweep_idx + 1, min(sweep_idx + max_lookahead + 1, n)):
            candle = df.iloc[i]
            body = abs(candle["close"] - candle["open"])
            total_range = candle["high"] - candle["low"]
            mid_price = (candle["high"] + candle["low"]) / 2
            if total_range <= 0 or mid_price <= 0:
                continue

            body_ratio = body / total_range

            # ★ ATR-Normalized: gövde >= 1.5 × ATR VEYA sabit threshold
            is_disp = body_ratio >= min_body_ratio and (
                (atr > 0 and body >= atr * 1.5) or
                (body / mid_price >= min_size_pct)
            )

            if is_disp:
                candle_dir = "BULLISH" if candle["close"] > candle["open"] else "BEARISH"
                atr_mult = round(body / atr, 2) if atr > 0 else 0
                if bias == "LONG" and candle_dir == "BULLISH":
                    displacement = {
                        "index": i, "direction": "BULLISH",
                        "body_ratio": round(body_ratio, 3),
                        "size_pct": round((body / mid_price) * 100, 3),
                        "atr_multiple": atr_mult
                    }
                    break
                elif bias == "SHORT" and candle_dir == "BEARISH":
                    displacement = {
                        "index": i, "direction": "BEARISH",
                        "body_ratio": round(body_ratio, 3),
                        "size_pct": round((body / mid_price) * 100, 3),
                        "atr_multiple": atr_mult
                    }
                    break

        if displacement is None:
            return None

        # --- MSS (Market Structure Shift) Tespiti ---
        # Displacement mumunun sweep öncesi yapıyı kırıp kırmadığını kontrol et
        mss_confirmed = False
        disp_idx = displacement["index"]

        if bias == "LONG":
            # Sweep öncesi son swing high bulunmalı ve kırılmalı
            pre_sweep_highs = [sh for sh in self.find_swing_points(df)[0]
                               if sh["index"] < sweep_idx]
            if pre_sweep_highs:
                target_high = pre_sweep_highs[-1]["price"]
                for i in range(disp_idx, min(disp_idx + 8, n)):
                    if df.iloc[i]["high"] > target_high:
                        mss_confirmed = True
                        break
        elif bias == "SHORT":
            pre_sweep_lows = [sl for sl in self.find_swing_points(df)[1]
                              if sl["index"] < sweep_idx]
            if pre_sweep_lows:
                target_low = pre_sweep_lows[-1]["price"]
                for i in range(disp_idx, min(disp_idx + 8, n)):
                    if df.iloc[i]["low"] < target_low:
                        mss_confirmed = True
                        break

        return {
            "displacement": displacement,
            "mss_confirmed": mss_confirmed,
            "confidence_boost": 10 if mss_confirmed else 0
        }

    # =================================================================
    #  BÖLÜM 14 — GATE 4: DISPLACEMENT FVG (Giriş Bölgesi)
    # =================================================================

    def _find_displacement_fvg(self, df, displacement_idx, bias):
        """
        ★ GATE 4 — Displacement mumunun oluşturduğu FVG'yi bul.

        Displacement mumu tek yönlü güçlü hareket yapar ve MUTLAKA
        bir FVG (Fair Value Gap) bırakır. Bu FVG kurumsal emir
        boşluğudur — fiyat buraya geri döner (fill) ve bu bizim
        GİRİŞ BÖLGEMİZDİR.

        Arama: displacement mumunun kendisi ve çevresindeki 3 mumda FVG ara.
        Bulamazsa, displacement sonrası oluşan tüm FVG'leri kontrol et.

        Returns: FVG dict veya None
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
                # Bullish FVG: prev.high < next.low
                if prev["high"] < next_["low"]:
                    gap = next_["low"] - prev["high"]
                    if gap / mid_price >= min_fvg_size:
                        filled = False
                        if i + 2 < n and df.iloc[i + 2:]["low"].min() <= prev["high"]:
                            filled = True
                        if not filled:
                            fvg = {
                                "type": "BULLISH_FVG", "index": i,
                                "high": next_["low"], "low": prev["high"],
                                "size_pct": round((gap / mid_price) * 100, 4),
                                "timestamp": curr["timestamp"]
                            }
                            if best_fvg is None or abs(i - displacement_idx) < abs(best_fvg["index"] - displacement_idx):
                                best_fvg = fvg

            elif bias == "SHORT":
                # Bearish FVG: prev.low > next.high
                if prev["low"] > next_["high"]:
                    gap = prev["low"] - next_["high"]
                    if gap / mid_price >= min_fvg_size:
                        filled = False
                        if i + 2 < n and df.iloc[i + 2:]["high"].max() >= prev["low"]:
                            filled = True
                        if not filled:
                            fvg = {
                                "type": "BEARISH_FVG", "index": i,
                                "high": prev["low"], "low": next_["high"],
                                "size_pct": round((gap / mid_price) * 100, 4),
                                "timestamp": curr["timestamp"]
                            }
                            if best_fvg is None or abs(i - displacement_idx) < abs(best_fvg["index"] - displacement_idx):
                                best_fvg = fvg

        # Displacement yakınında FVG bulunamadıysa, displacement sonrası tüm FVG'leri kontrol et
        if best_fvg is None:
            all_fvgs = self.find_fvg(df)
            target_type = "BULLISH_FVG" if bias == "LONG" else "BEARISH_FVG"
            relevant = [f for f in all_fvgs
                        if f["type"] == target_type and f["index"] >= displacement_idx - 3]
            if relevant:
                best_fvg = min(relevant, key=lambda f: abs(f["index"] - displacement_idx))

        return best_fvg

    # =================================================================
    #  BÖLÜM 15 — YAPISAL STOP LOSS HESAPLAMA
    # =================================================================

    def _calc_structural_sl(self, df, sweep, bias, structure):
        """
        Yapısal (Structural) Stop Loss hesaplama.
        SABIT YÜZDE KULLANILMAZ — her zaman piyasa yapısına göre hesaplanır.

        LONG SL sırası:
          1. Sweep mumunun wick'inin altı (sweep invalidation)
          2. Sweep edilen swing low'un altı
          3. Son swing low'un altı
        SHORT SL sırası:
          1. Sweep mumunun wick'inin üstü (sweep invalidation)
          2. Sweep edilen swing high'ın üstü
          3. Son swing high'ın üstü

        SL mesafesi çok uzaksa → sinyal üretilmez (None döner).
        """
        candidates = []

        if bias == "LONG":
            # 1. Sweep mumunun wick altı (en kesin invalidation noktası)
            sweep_wick = sweep.get("sweep_wick", sweep.get("sweep_low"))
            if sweep_wick:
                candidates.append(("SWEEP_WICK", sweep_wick * 0.998))

            # 2. Sweep edilen seviyenin altı
            candidates.append(("SWEPT_LEVEL", sweep["swept_level"] * 0.997))

            # 3. Son swing low
            if structure["last_swing_low"]:
                candidates.append(("SWING_LOW", structure["last_swing_low"]["price"] * 0.997))

            # En yakın (entry'ye en yakın) geçerli SL'yi seç
            valid = [(name, price) for name, price in candidates if price > 0]
            if not valid:
                return None

            # En yakın olanı seç (unnecessarily geniş SL'den kaçın)
            best = max(valid, key=lambda x: x[1])

            # NOT: ATR floor KALDIRILDI — yapısal SL'yi bozarak genişletmek yanlış.
            # Yapısal SL volatiliteye göre çok darsa, generate_signal()'deki
            # effective_min_sl kontrolü sinyali reddeder (doğru davranış).

            logger.debug(f"  LONG SL: {best[0]} @ {best[1]:.8f}")
            return best[1]

        elif bias == "SHORT":
            sweep_wick = sweep.get("sweep_wick", sweep.get("sweep_high"))
            if sweep_wick:
                candidates.append(("SWEEP_WICK", sweep_wick * 1.002))

            candidates.append(("SWEPT_LEVEL", sweep["swept_level"] * 1.003))

            if structure["last_swing_high"]:
                candidates.append(("SWING_HIGH", structure["last_swing_high"]["price"] * 1.003))

            valid = [(name, price) for name, price in candidates if price > 0]
            if not valid:
                return None

            best = min(valid, key=lambda x: x[1])

            # NOT: ATR floor KALDIRILDI — yapısal SL'yi bozarak genişletmek yanlış.
            # Yapısal SL volatiliteye göre çok darsa, generate_signal()'deki
            # effective_min_sl kontrolü sinyali reddeder (doğru davranış).

            logger.debug(f"  SHORT SL: {best[0]} @ {best[1]:.8f}")
            return best[1]

        return None

    # =================================================================
    #  BÖLÜM 16 — KARŞI LİKİDİTE TP HESAPLAMA (Draw on Liquidity)
    # =================================================================

    def _calc_opposing_liquidity_tp(self, df, multi_tf_data, entry, sl, bias, structure):
        """
        Draw on Liquidity — Karşı taraftaki likidite havuzunu hedefle.
        SABİT R:R KULLANILMAZ — her zaman yapısal hedef aranır.

        LONG TP sırası:
          1. HTF (4H) equal highs → en güçlü mıknatıs
          2. LTF (15m) equal highs → ana hedef
          3. Karşı (bearish) Order Block → fiyat burada tepki verir
          4. Son swing high → yapısal direnç
          5. Minimum R:R fallback (sadece hiçbir hedef bulunamazsa)

        SHORT TP sırası:
          1. HTF (4H) equal lows
          2. LTF (15m) equal lows
          3. Karşı (bullish) Order Block
          4. Son swing low
          5. Minimum R:R fallback
        """
        tp_candidates = []
        risk = entry * 0.015  # Fallback risk

        # HTF likidite
        htf_liquidity = []
        if multi_tf_data and "4H" in multi_tf_data and not multi_tf_data["4H"].empty:
            htf_liquidity = self.find_liquidity_levels(multi_tf_data["4H"])

        # LTF likidite
        ltf_liquidity = self.find_liquidity_levels(df)

        # LTF yapı ve order blocks
        ltf_structure = self.detect_market_structure(df)
        active_obs, _ = self.find_order_blocks(df, ltf_structure)

        if bias == "LONG":
            risk = entry - sl if sl and sl < entry else entry * 0.015

            # HTF Draw on Liquidity
            for liq in htf_liquidity:
                if liq["type"] == "EQUAL_HIGHS" and not liq["swept"] and liq["price"] > entry:
                    tp_candidates.append(("HTF_DRAW_LIQ", liq["price"] * 0.999))

            # LTF BSL (equal highs)
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
                if sh["price"] > entry * 1.005:
                    tp_candidates.append(("PREV_SH", sh["price"] * 0.998))

        elif bias == "SHORT":
            risk = sl - entry if sl and sl > entry else entry * 0.015

            for liq in htf_liquidity:
                if liq["type"] == "EQUAL_LOWS" and not liq["swept"] and liq["price"] < entry:
                    tp_candidates.append(("HTF_DRAW_LIQ", liq["price"] * 1.001))

            for liq in ltf_liquidity:
                if liq["type"] == "EQUAL_LOWS" and not liq["swept"] and liq["price"] < entry:
                    tp_candidates.append(("LTF_SSL", liq["price"] * 1.001))

            for ob in active_obs:
                if ob["type"] == "BULLISH_OB" and ob["high"] < entry:
                    tp_candidates.append(("OPPOSING_OB", ob["high"]))

            if structure["last_swing_low"] and structure["last_swing_low"]["price"] < entry:
                tp_candidates.append(("SWING_LOW", structure["last_swing_low"]["price"] * 1.002))

            for sl_p in structure.get("swing_lows", []):
                if sl_p["price"] < entry * 0.995:
                    tp_candidates.append(("PREV_SL", sl_p["price"] * 1.002))

        if not tp_candidates:
            # Son çare: minimum R:R ile hesapla — ama bu ideal DEĞİL
            min_rr = self.params.get("default_tp_ratio", 2.5)
            if bias == "LONG":
                return entry + (risk * min_rr)
            else:
                return entry - (risk * min_rr)

        # Minimum 1.5 R:R sağlayan hedefleri filtrele + HTF öncelikli seçim
        min_reward = risk * 1.5

        if bias == "LONG":
            valid = [(n, p) for n, p in tp_candidates if (p - entry) >= min_reward]
            if valid:
                # HTF ve LTF ayır
                htf_valid = [(n, p) for n, p in valid if n == "HTF_DRAW_LIQ"]
                ltf_valid = [(n, p) for n, p in valid if n != "HTF_DRAW_LIQ"]
                nearest_ltf = min(ltf_valid, key=lambda x: x[1]) if ltf_valid else None

                # HTF hedef varsa ve makul mesafedeyse (LTF en yakının 3x'i içinde) tercih et
                if htf_valid and nearest_ltf:
                    nearest_htf = min(htf_valid, key=lambda x: x[1])
                    ltf_dist = nearest_ltf[1] - entry
                    htf_dist = nearest_htf[1] - entry
                    if htf_dist <= ltf_dist * 3.0:
                        best = nearest_htf
                        logger.debug(f"  LONG TP: HTF öncelikli → {best[0]} @ {best[1]:.8f}")
                        return best[1]
                elif htf_valid and not nearest_ltf:
                    best = min(htf_valid, key=lambda x: x[1])
                    logger.debug(f"  LONG TP: Sadece HTF → {best[0]} @ {best[1]:.8f}")
                    return best[1]

                # HTF tercih edilmediyse en yakın geçerli hedef
                best = min(valid, key=lambda x: x[1])
                logger.debug(f"  LONG TP: {best[0]} @ {best[1]:.8f}")
                return best[1]
            # 2.0 RR sağlayan hedef yoksa en uzak olanı dene
            if tp_candidates:
                best = max(tp_candidates, key=lambda x: x[1])
                if (best[1] - entry) > risk:
                    return best[1]
        else:
            valid = [(n, p) for n, p in tp_candidates if (entry - p) >= min_reward]
            if valid:
                # HTF ve LTF ayır
                htf_valid = [(n, p) for n, p in valid if n == "HTF_DRAW_LIQ"]
                ltf_valid = [(n, p) for n, p in valid if n != "HTF_DRAW_LIQ"]
                nearest_ltf = max(ltf_valid, key=lambda x: x[1]) if ltf_valid else None

                # HTF hedef varsa ve makul mesafedeyse tercih et
                if htf_valid and nearest_ltf:
                    nearest_htf = max(htf_valid, key=lambda x: x[1])
                    ltf_dist = entry - nearest_ltf[1]
                    htf_dist = entry - nearest_htf[1]
                    if htf_dist <= ltf_dist * 3.0:
                        best = nearest_htf
                        logger.debug(f"  SHORT TP: HTF öncelikli → {best[0]} @ {best[1]:.8f}")
                        return best[1]
                elif htf_valid and not nearest_ltf:
                    best = max(htf_valid, key=lambda x: x[1])
                    logger.debug(f"  SHORT TP: Sadece HTF → {best[0]} @ {best[1]:.8f}")
                    return best[1]

                # HTF tercih edilmediyse en yakın geçerli hedef
                best = max(valid, key=lambda x: x[1])
                logger.debug(f"  SHORT TP: {best[0]} @ {best[1]:.8f}")
                return best[1]
            if tp_candidates:
                best = min(tp_candidates, key=lambda x: x[1])
                if (entry - best[1]) > risk:
                    return best[1]

        # Gerçekten hiçbir hedef yoksa minimum R:R
        min_rr = self.params.get("default_tp_ratio", 2.5)
        if bias == "LONG":
            return entry + (risk * min_rr)
        return entry - (risk * min_rr)

    # =================================================================
    #  BÖLÜM 17 — CONFLUENCE SCORING (Geriye Uyumlu)
    # =================================================================

    def calculate_confluence(self, df, multi_tf_data=None, override_direction=None):
        """
        Tüm ICT bileşenlerini analiz edip confluent skor hesapla.

        Bu metod hem generate_signal() tarafından hem de
        izleme listesi onayı (check_watchlist) ve API tarafından kullanılır.

        override_direction: generate_signal()'den çağrıldığında HTF-tabanlı
        bias aktarılır. None ise LTF yapısından türetilir (API çağrıları).

        Sıralı Ağırlıklandırma:
          HTF Bias uyumu:       25 puan (veya -15 ceza)
          Liquidity Sweep:      20 puan
          Displacement:         15 puan (yoksa -8 ceza)
          FVG giriş bölgesi:    15 puan
          Market Structure:     10 puan
          Premium/Discount:     10 puan
          Session (Killzone):    5 puan
          Order Block:           5 bonus
          Breaker Block:         5 bonus
          Sweep+MSS (A+):      10 bonus
        """
        analysis = {}
        components = []
        score = 0
        penalties = []

        current_price = df["close"].iloc[-1]
        current_idx = len(df) - 1
        analysis["current_price"] = current_price

        # === RANGING MARKET ===
        is_ranging = self.detect_ranging_market(df)
        analysis["is_ranging"] = is_ranging

        # === SESSION / KILLZONE ===
        session_info = self.get_session_info()
        analysis["session"] = session_info

        # === LTF MARKET STRUCTURE (15m) ===
        structure = self.detect_market_structure(df)
        analysis["structure"] = structure

        if structure["trend"] in ["BULLISH", "BEARISH"]:
            score += 10
            components.append("MARKET_STRUCTURE")
        elif structure["trend"] in ["WEAKENING_BULL", "WEAKENING_BEAR"]:
            score += 3
            penalties.append("WEAKENING_TREND(-7)")

        # === HTF BIAS (4H) ===
        htf_bias_block = False
        htf_result = self._analyze_htf_bias(multi_tf_data)
        analysis["htf_result"] = htf_result

        if htf_result:
            analysis["htf_trend"] = htf_result["htf_trend"]
            analysis["htf_structure"] = htf_result["structure"]
            analysis["htf_liquidity"] = htf_result.get("liquidity", [])

            # HTF ve LTF aynı yönde mi?
            if htf_result["bias"] == "LONG" and structure["trend"] in ["BULLISH", "WEAKENING_BEAR"]:
                score += 25
                components.append("HTF_CONFIRMATION")
            elif htf_result["bias"] == "SHORT" and structure["trend"] in ["BEARISH", "WEAKENING_BULL"]:
                score += 25
                components.append("HTF_CONFIRMATION")
            elif htf_result["bias"] == "LONG" and structure["trend"] == "BEARISH":
                # HTF LONG ama LTF BEARISH → HARD BLOCK
                htf_bias_block = True
                score -= 15
                penalties.append("HTF_BIAS_BLOCK(-15)")
            elif htf_result["bias"] == "SHORT" and structure["trend"] == "BULLISH":
                htf_bias_block = True
                score -= 15
                penalties.append("HTF_BIAS_BLOCK(-15)")
            else:
                # HTF var ama kısmi uyum
                score += 10

            # ── 4H Premium/Discount Matrisi Cezası ──
            # 4H Bullish ama fiyat 4H extreme premium → LONG riskli
            # 4H Bearish ama fiyat 4H extreme discount → SHORT riskli
            htf_pd = htf_result.get("htf_pd")
            htf_extreme = htf_result.get("htf_extreme", False)
            analysis["htf_pd"] = htf_pd
            analysis["htf_extreme"] = htf_extreme

            if htf_extreme:
                score -= 12
                penalties.append("HTF_EXTREME_ZONE(-12)")
            elif htf_pd:
                pd_level = htf_pd["premium_level"]
                # LONG ideal: Discount (%0-40), uyarı: Premium (%60-80)
                # SHORT ideal: Premium (%60-100), uyarı: Discount (%20-40)
                if htf_result["bias"] == "LONG":
                    if pd_level < 40:
                        score += 5
                        components.append("HTF_DISCOUNT_ZONE")
                    elif pd_level > 65:
                        score -= 5
                        penalties.append("HTF_PREMIUM_WARNING(-5)")
                elif htf_result["bias"] == "SHORT":
                    if pd_level > 60:
                        score += 5
                        components.append("HTF_PREMIUM_ZONE")
                    elif pd_level < 35:
                        score -= 5
                        penalties.append("HTF_DISCOUNT_WARNING(-5)")
        else:
            analysis["htf_trend"] = "UNKNOWN"
            analysis["htf_structure"] = None
            analysis["htf_liquidity"] = []

        analysis["htf_bias_block"] = htf_bias_block

        # === YÖN ===
        # override_direction: generate_signal() HTF bias'ını aktarır.
        # Bu sayede LTF WEAKENING_BULL + HTF LONG durumunda confluence
        # doğru yönde (LONG) puanlanır.
        if override_direction:
            direction = override_direction
        else:
            direction = None
            if structure["trend"] in ["BULLISH", "WEAKENING_BEAR"]:
                direction = "LONG"
            elif structure["trend"] in ["BEARISH", "WEAKENING_BULL"]:
                direction = "SHORT"
        analysis["direction"] = direction

        # === MTF (1H) ONAY — GÜÇLENDİRİLMİŞ ===
        mtf_result = self._analyze_mtf_confirmation(multi_tf_data, structure, direction)
        analysis["mtf_result"] = mtf_result

        if mtf_result:
            analysis["mtf_trend"] = mtf_result["mtf_trend"]
            analysis["mtf_ob_confluence"] = mtf_result.get("ob_confluence", False)
            analysis["mtf_fvg_confluence"] = mtf_result.get("fvg_confluence", False)

            if mtf_result["bias_aligned"]:
                score += 10
                components.append("MTF_CONFIRMATION")

                # 1H OB veya FVG ile çakışma bonusu
                if mtf_result.get("ob_confluence"):
                    score += 5
                    components.append("MTF_OB_CONFLUENCE")
                if mtf_result.get("fvg_confluence"):
                    score += 3
                    components.append("MTF_FVG_CONFLUENCE")
            elif mtf_result.get("bias_conflict"):
                # 1H aktif olarak karşı yönde → ceza
                score -= 8
                penalties.append("MTF_BIAS_CONFLICT(-8)")
            else:
                # 1H nötr → küçük bonus
                score += 2
        else:
            analysis["mtf_trend"] = "UNKNOWN"
            analysis["mtf_ob_confluence"] = False
            analysis["mtf_fvg_confluence"] = False

        # === LIQUIDITY SWEEP ===
        bias_for_sweep = direction or (htf_result["bias"] if htf_result else None)
        sweep_detected = False
        sweep_mss_detected = False

        if bias_for_sweep:
            sweep = self._find_sweep_event(df, bias_for_sweep)
            analysis["sweep"] = sweep
            if sweep:
                # Sweep kalite çarpanı: Düşük kalite sweep daha az puan alır
                sweep_quality = sweep.get("sweep_quality", 1.0)
                sweep_base = 20
                sweep_score = round(sweep_base * sweep_quality)
                score += min(sweep_score, 35)  # Max 35 puan (yüksek kalite sweep bonusu)
                components.append("LIQUIDITY_SWEEP")
                if sweep_quality >= 1.5:
                    components.append("HIGH_QUALITY_SWEEP")
                analysis["sweep_quality"] = sweep_quality
                sweep_detected = True

                # Sweep sonrası displacement + MSS?
                confirmation = self._find_post_sweep_confirmation(df, sweep, bias_for_sweep)
                analysis["post_sweep_confirmation"] = confirmation
                if confirmation:
                    score += 15
                    components.append("DISPLACEMENT")

                    if confirmation["mss_confirmed"]:
                        score += 10
                        components.append("SWEEP_MSS_A_PLUS")
                        sweep_mss_detected = True

                    # Displacement FVG?
                    disp_fvg = self._find_displacement_fvg(df, confirmation["displacement"]["index"], bias_for_sweep)
                    analysis["displacement_fvg"] = disp_fvg
                    if disp_fvg:
                        score += 15
                        components.append("FVG")
                else:
                    analysis["post_sweep_confirmation"] = None
                    analysis["displacement_fvg"] = None
            else:
                analysis["sweep"] = None
                analysis["post_sweep_confirmation"] = None
                analysis["displacement_fvg"] = None
        else:
            analysis["sweep"] = None
            analysis["post_sweep_confirmation"] = None
            analysis["displacement_fvg"] = None

        analysis["sweep_mss"] = sweep_mss_detected

        # === ORDER BLOCKS (bonus) ===
        active_obs, all_obs = self.find_order_blocks(df, structure)
        analysis["order_blocks"] = active_obs
        analysis["all_order_blocks"] = all_obs

        relevant_obs = []
        for ob in active_obs:
            age = current_idx - ob["index"]
            recency = 1.0 if age <= 5 else (0.8 if age <= 15 else 0.5)

            if direction == "LONG" and ob["type"] == "BULLISH_OB":
                if ob["low"] <= current_price <= ob["high"] * 1.005:
                    relevant_obs.append(ob)
                    score += 5 * recency
                    components.append("ORDER_BLOCK")
                    break
            elif direction == "SHORT" and ob["type"] == "BEARISH_OB":
                if ob["low"] <= current_price <= ob["high"] * 1.005:
                    relevant_obs.append(ob)
                    score += 5 * recency
                    components.append("ORDER_BLOCK")
                    break
        analysis["relevant_obs"] = relevant_obs

        # === UNICORN SETUP (OB + FVG Çakışma) ===
        disp_fvg_for_unicorn = analysis.get("displacement_fvg")
        unicorn = None
        if disp_fvg_for_unicorn and direction:
            unicorn = self._detect_unicorn_setup(df, disp_fvg_for_unicorn, direction, structure)
        analysis["unicorn_setup"] = unicorn
        if unicorn:
            score += 8
            components.append("UNICORN_SETUP")

        # === BREAKER BLOCKS (bonus) ===
        breaker_blocks = self.find_breaker_blocks(all_obs, df)
        analysis["breaker_blocks"] = breaker_blocks
        for bb in breaker_blocks:
            if (direction == "LONG" and bb["type"] == "BULLISH_BREAKER") or \
               (direction == "SHORT" and bb["type"] == "BEARISH_BREAKER"):
                score += 5
                components.append("BREAKER_BLOCK")
                break

        # === GENEL FVG KONTROLÜ (displacement FVG bulunamadıysa) ===
        fvgs = self.find_fvg(df)
        analysis["fvgs"] = fvgs

        relevant_fvgs = []
        if "FVG" not in components:
            for fvg in fvgs:
                fvg_age = current_idx - fvg["index"]
                fvg_recency = 1.0 if fvg_age <= 8 else 0.6
                if direction == "LONG" and fvg["type"] == "BULLISH_FVG":
                    if fvg["low"] * 0.998 <= current_price <= fvg["high"] * 1.002:
                        relevant_fvgs.append(fvg)
                        score += 10 * fvg_recency
                        components.append("FVG")
                        break
                elif direction == "SHORT" and fvg["type"] == "BEARISH_FVG":
                    if fvg["low"] * 0.998 <= current_price <= fvg["high"] * 1.002:
                        relevant_fvgs.append(fvg)
                        score += 10 * fvg_recency
                        components.append("FVG")
                        break
        analysis["relevant_fvgs"] = relevant_fvgs

        # === LIQUIDITY LEVELS ===
        liquidity = self.find_liquidity_levels(df)
        analysis["liquidity"] = liquidity

        # === DISPLACEMENT (genel — sweep bağımsız + hacim bonusu) ===
        if "DISPLACEMENT" not in components:
            displacements = self.detect_displacement(df)
            analysis["displacements"] = displacements
            if displacements:
                last_d = displacements[-1]
                if (direction == "LONG" and last_d["direction"] == "BULLISH") or \
                   (direction == "SHORT" and last_d["direction"] == "BEARISH"):
                    score += 8
                    components.append("DISPLACEMENT")
                    # Hacim bonusu: Yüksek hacimli displacement daha güvenilir
                    vol_ratio = last_d.get("volume_ratio", 1.0)
                    if vol_ratio >= 2.0:
                        score += 5
                        components.append("HIGH_VOLUME_DISPLACEMENT")
                    elif vol_ratio >= 1.5:
                        score += 3
                        components.append("ABOVE_AVG_VOLUME")
            if "DISPLACEMENT" not in components:
                score -= 8
                penalties.append("NO_DISPLACEMENT(-8)")
        else:
            analysis["displacements"] = self.detect_displacement(df)

        # === PREMIUM / DISCOUNT + OTE ===
        pd_zone = self.calculate_premium_discount(df, structure)
        analysis["premium_discount"] = pd_zone
        if pd_zone:
            if direction == "LONG" and pd_zone["zone"] == "DISCOUNT":
                score += 7
                components.append("DISCOUNT_ZONE")
                if pd_zone["in_ote"]:
                    score += 3
                    components.append("OTE")
            elif direction == "SHORT" and pd_zone["zone"] == "PREMIUM":
                score += 7
                components.append("PREMIUM_ZONE")
                if pd_zone["in_ote"]:
                    score += 3
                    components.append("OTE")

        # === SESSION KALİTESİ (KRİPTO OPTİMİZE — CEZA YOK) ===
        # Kripto 7/24 işlem görür. Session cezası kaldırıldı.
        # Sadece killzone ve aktif saatler için bonus verilir.
        if session_info["quality"] >= 1.0:
            # London/NY Killzone — kurumsal aktivite zirvesi
            score += 8
            components.append("KILLZONE_ACTIVE")
        elif session_info["quality"] >= 0.85:
            # Asian session — kripto için çok aktif
            score += 5
            components.append("CRYPTO_ACTIVE_SESSION")
        elif session_info["quality"] >= 0.8:
            # London Close / London-NY geçiş
            score += 4
            components.append("KILLZONE_ACTIVE")
        elif session_info["quality"] >= 0.7:
            # Off-peak — kripto hâlâ aktif, küçük bonus
            score += 2

        # === RANGING CEZASI ===
        if is_ranging:
            score -= 15
            penalties.append("RANGING_MARKET(-15)")

        # === TRIPLE TF ALIGNMENT ===
        if "HTF_CONFIRMATION" in components and "MTF_CONFIRMATION" in components:
            score += 3
            components.append("TRIPLE_TF_ALIGNMENT")

        # === TRIPLE TF ALIGNMENT (bonus zaten yukarıda eklendi) ===
        # NOT: Non-linear çarpanlar (CORE_GATE_MULTIPLIER vb.) KALDIRILDI.
        # Skor enflasyonuna neden olup confidence'u anlamsız kılıyordu.
        # Tüm gate'ler eşit ağırlıkta değerlendirilir.

        # Normalize (0-100)
        # Teorik max: HTF(25) + Sweep(35 cap) + Disp(15)
        #   + FVG(15) + Structure(10) + MTF(10) + MTF_OB(5) + MTF_FVG(3)
        #   + PD(7) + OTE(3) + Session(8) + OB(5) + Breaker(5) + MSS(10)
        #   + Triple_TF(3) + VolDisp(5) + Unicorn(8) + HTF_PD_Zone(5) = 177
        max_possible = 177
        score = max(0, score)
        confluence_score = min(100, round((score / max_possible) * 100, 1))

        analysis["confluence_score"] = confluence_score
        analysis["components"] = list(set(components))
        analysis["penalties"] = penalties

        return analysis

    # =================================================================
    #  BÖLÜM 18 — GÜVEN SKORU HESAPLAMA
    # =================================================================

    def _calculate_confidence(self, analysis):
        """
        Güven skoru (0-100).
        Confluence score'ın gerçekçi düzeltmesi.
        Bonus enflasyonu YOKTUR — skor gerçek kaliteyi yansıtır.
        """
        base = analysis["confluence_score"]
        components = analysis.get("components", [])

        # Küçük kalite bonusu (max +10)
        bonus = 0
        if "SWEEP_MSS_A_PLUS" in components:
            bonus += 5   # A+ setup (sweep + MSS)
        if "UNICORN_SETUP" in components:
            bonus += 5   # OB+FVG geometrik çakışma

        # Eksik kritik bileşen cezaları
        penalty = 0
        if "DISPLACEMENT" not in components:
            penalty += 10
        if "FVG" not in components:
            penalty += 8
        if "LIQUIDITY_SWEEP" not in components:
            penalty += 10
        if "HTF_CONFIRMATION" not in components:
            penalty += 8

        # Premium/Discount uyumsuzluğu
        if "DISCOUNT_ZONE" not in components and "PREMIUM_ZONE" not in components:
            penalty += 5

        confidence = max(0, min(100, base + bonus - penalty))
        return round(confidence, 1)

    # =================================================================
    #  BÖLÜM 19 — SİNYAL ÜRETİMİ (Sequential Gate Protocol)
    # =================================================================

    def generate_signal(self, symbol, df, multi_tf_data=None):
        """
        ★ ANA SİNYAL ÜRETİMİ — Katı Sıralı ICT Protokolü.

        Her adım bir GATE'tir:
          Gate 1: HTF Bias → 4H trend yönü belirler
          Gate 2: Liquidity Sweep → Eski swing seviyesinin temizlenmesi
          Gate 3: Displacement + MSS → Tersine dönüş onayı
          Gate 4: FVG Entry Zone → Giriş bölgesi tespiti

        Tüm gate'ler geçerse → SIGNAL (FVG'ye limit emir)
        Kısmi gate'ler → WATCH (izlemeye al)
        Hiçbir gate geçmezse → None (sinyal yok)

        Returns: signal dict veya None
        """
        if df.empty or len(df) < 30:
            return None

        current_price = df["close"].iloc[-1]

        # ===== GATE 0: Ranging Market → Sinyal üretme =====
        if self.detect_ranging_market(df):
            logger.debug(f"🚫 {symbol}: RANGING market → atlandı")
            return None

        # ===== GATE 1: HTF Bias (4H) → Yön tayini =====
        htf_result = self._analyze_htf_bias(multi_tf_data)
        if htf_result is None:
            logger.debug(f"🚫 {symbol}: HTF belirsiz (NEUTRAL) → atlandı")
            return None  # HTF belirsiz → İŞLEM YOK
        bias = htf_result["bias"]  # "LONG" veya "SHORT"

        # LTF (15m) yapı analizi
        structure = self.detect_market_structure(df)

        # LTF trend HTF bias'a KARŞI mı?
        # ICT'de bu SETUP OLUŞUM AŞAMASI olabilir:
        #   4H BULLISH + 15m BEARISH = fiyat discount'a çekiliyor → sweep bekle
        #   4H BEARISH + 15m BULLISH = fiyat premium'a çıkıyor → sweep bekle
        # Tamamen reddetmek yerine WATCH'a al — sweep olursa A+ setup olur.
        if bias == "LONG" and structure["trend"] == "BEARISH":
            # Potansiyel setup oluşum aşaması: Sweep + MSS ile 15m dönebilir
            analysis = self.calculate_confluence(df, multi_tf_data, override_direction=bias)
            confidence = self._calculate_confidence(analysis)
            # Sadece HTF güçlü + bazı bileşenler varsa WATCH'a al
            if confidence >= 25 and "HTF_CONFIRMATION" in analysis.get("components", []):
                logger.debug(f"👀 {symbol}: LTF BEARISH vs HTF LONG → Setup oluşum WATCH")
                return self._build_signal_dict(
                    symbol, bias, current_price, analysis, confidence,
                    action="WATCH",
                    watch_reason="HTF LONG ama LTF henüz bearish — sweep + MSS ile dönüş bekleniyor"
                )
            logger.debug(f"🚫 {symbol}: LTF BEARISH vs HTF LONG → yetersiz çakışma")
            return None
        if bias == "SHORT" and structure["trend"] == "BULLISH":
            analysis = self.calculate_confluence(df, multi_tf_data, override_direction=bias)
            confidence = self._calculate_confidence(analysis)
            if confidence >= 25 and "HTF_CONFIRMATION" in analysis.get("components", []):
                logger.debug(f"👀 {symbol}: LTF BULLISH vs HTF SHORT → Setup oluşum WATCH")
                return self._build_signal_dict(
                    symbol, bias, current_price, analysis, confidence,
                    action="WATCH",
                    watch_reason="HTF SHORT ama LTF henüz bullish — sweep + MSS ile dönüş bekleniyor"
                )
            logger.debug(f"🚫 {symbol}: LTF BULLISH vs HTF SHORT → yetersiz çakışma")
            return None

        # ===== GATE 1.5: MTF (1H) Doğrulama =====
        direction_for_mtf = bias
        mtf_result = self._analyze_mtf_confirmation(multi_tf_data, structure, direction_for_mtf)
        if mtf_result and mtf_result.get("bias_conflict"):
            # 1H aktif olarak karşı yönde → dikkatli ol, WATCH olarak devam et
            logger.debug(f"⚠️ {symbol}: 1H bias uyumsuz ({mtf_result['mtf_trend']} vs {bias}), WATCH'a yönlendiriliyor")
            analysis = self.calculate_confluence(df, multi_tf_data, override_direction=bias)
            confidence = self._calculate_confidence(analysis)
            if confidence < 50:  # 1H conflict + düşük güven → sinyal yok
                return None
            return self._build_signal_dict(
                symbol, bias, current_price, analysis, confidence,
                action="WATCH",
                watch_reason=f"1H trend uyumsuz ({mtf_result['mtf_trend']}), doğrulama bekleniyor"
            )

        # ===== GATE 2: Liquidity Sweep → Stop hunt tespiti =====
        sweep = self._find_sweep_event(df, bias)
        if sweep is None:
            # Sweep yok → potansiyel WATCH sinyali kontrol et
            return self._build_watch_from_potential(symbol, df, multi_tf_data, htf_result, structure, bias)

        # ===== GATE 3: Displacement + MSS → Dönüş onayı =====
        confirmation = self._find_post_sweep_confirmation(df, sweep, bias)
        if confirmation is None:
            # Sweep var ama displacement yok → WATCH
            analysis = self.calculate_confluence(df, multi_tf_data, override_direction=bias)
            confidence = self._calculate_confidence(analysis)
            return self._build_signal_dict(
                symbol, bias, current_price, analysis, confidence,
                action="WATCH",
                watch_reason="Sweep tespit edildi, displacement bekleniyor"
            )

        # ===== GATE 4: Displacement FVG → Giriş bölgesi =====
        disp_idx = confirmation["displacement"]["index"]
        entry_fvg = self._find_displacement_fvg(df, disp_idx, bias)
        if entry_fvg is None:
            analysis = self.calculate_confluence(df, multi_tf_data, override_direction=bias)
            confidence = self._calculate_confidence(analysis)
            return self._build_signal_dict(
                symbol, bias, current_price, analysis, confidence,
                action="WATCH",
                watch_reason="Displacement sonrası FVG bekleniyor"
            )

        # ===== TÜM GATE'LER GEÇTİ — SİNYAL OLUŞTUR =====
        logger.info(f"🎯 {symbol}: Tüm ICT gate'leri geçti: HTF={htf_result['htf_trend']}, "
                    f"Sweep={sweep['sweep_type']}, Displacement+{'MSS' if confirmation['mss_confirmed'] else 'noMSS'}")

        # ===== UNICORN SETUP — OB + FVG Çakışma Kontrolü =====
        unicorn = self._detect_unicorn_setup(df, entry_fvg, bias, structure)
        if unicorn:
            # Unicorn Setup → entry'yi FVG+OB junction'a kaydır (daha hassas giriş)
            entry = unicorn["junction_entry"]
            logger.info(f"🦄 {symbol}: UNICORN SETUP — OB+FVG çakışma → Entry: {entry:.8f}")
        else:
            # Normal CE entry (Consequent Encroachment = orta nokta)
            entry = (entry_fvg["high"] + entry_fvg["low"]) / 2

        # Yapısal SL
        sl = self._calc_structural_sl(df, sweep, bias, structure)
        if sl is None:
            return None

        # Draw on Liquidity TP
        tp = self._calc_opposing_liquidity_tp(df, multi_tf_data, entry, sl, bias, structure)
        if tp is None:
            return None

        # Seviye doğrulama
        if bias == "LONG":
            if sl >= entry or tp <= entry:
                logger.warning(f"❌ {symbol} LONG seviyeleri ters: E={entry} SL={sl} TP={tp}")
                return None
            risk = entry - sl
            reward = tp - entry
        else:
            if sl <= entry or tp >= entry:
                logger.warning(f"❌ {symbol} SHORT seviyeleri ters: E={entry} SL={sl} TP={tp}")
                return None
            risk = sl - entry
            reward = entry - tp

        if risk <= 0:
            return None

        rr_ratio = reward / risk
        if rr_ratio < 1.5:
            return None

        # SL mesafesi kontrolleri
        sl_distance_pct = risk / entry
        atr_val = self._calc_atr(df, 14)
        min_sl_by_atr = atr_val / entry if atr_val > 0 and entry > 0 else 0.008
        effective_min_sl = max(0.008, min_sl_by_atr)
        if sl_distance_pct < effective_min_sl:
            return None  # SL ATR floor altında → volatilitede vurulur
        if sl_distance_pct > 0.05:
            return None  # SL çok uzak → risk çok yüksek

        # Entry modu: Fiyat FVG bölgesinde mi?
        if bias == "LONG":
            price_at_fvg = entry_fvg["low"] * 0.998 <= current_price <= entry_fvg["high"] * 1.002
        else:
            price_at_fvg = entry_fvg["low"] * 0.998 <= current_price <= entry_fvg["high"] * 1.002
        entry_mode = "MARKET" if price_at_fvg else "LIMIT"

        # Confluence ve confidence hesapla (HTF bias'ı override olarak gönder)
        analysis = self.calculate_confluence(df, multi_tf_data, override_direction=bias)
        confluence_score = analysis["confluence_score"]
        confidence = self._calculate_confidence(analysis)

        # Minimum eşikler (config varsayılanlarıyla tutarlı)
        min_confluence = self.params.get("min_confluence_score", 60)
        min_confidence = self.params.get("min_confidence", 65)

        session = self.get_session_info()
        components = analysis.get("components", [])

        # Quality Tier belirleme (optimizer öğrensin)
        if confirmation["mss_confirmed"]:
            quality_tier = "A+"  # Sweep + Displacement + MSS = en güçlü
        else:
            quality_tier = "A"   # Sweep + Displacement = güçlü

        result = {
            "symbol": symbol,
            "direction": bias,
            "entry": round(entry, 8),
            "sl": round(sl, 8),
            "tp": round(tp, 8),
            "current_price": round(current_price, 8),
            "confluence_score": confluence_score,
            "confidence": confidence,
            "components": components,
            "penalties": analysis.get("penalties", []),
            "session": session.get("label", ""),
            "rr_ratio": round(rr_ratio, 2),
            "entry_type": f"FVG Limit ({entry_fvg['type']})" if entry_mode == "LIMIT" else f"FVG Market ({entry_fvg['type']})",
            "sl_type": "Yapısal Seviye (Sweep Invalidation)",
            "tp_type": self._get_tp_type(analysis, tp, bias),
            "entry_mode": entry_mode,
            "htf_bias": htf_result["htf_trend"],
            "sweep_level": sweep["swept_level"],
            "quality_tier": quality_tier,
            "analysis": analysis
        }

        # Sinyal mi, izleme mi?
        if confluence_score >= min_confluence and confidence >= min_confidence:
            result["action"] = "SIGNAL"
            logger.info(
                f"🎯 SİNYAL: {symbol} {bias} | Entry: {entry:.8f} | SL: {sl:.8f} | TP: {tp:.8f} | "
                f"RR: {rr_ratio:.1f} | Score: {confluence_score} | Conf: {confidence}% | "
                f"Mode: {entry_mode} | Session: {session['label']}"
            )
        elif confluence_score >= min_confluence * 0.5:
            result["action"] = "WATCH"
            result["watch_reason"] = self._get_watch_reason(analysis)
            logger.info(
                f"👀 İZLEME: {symbol} {bias} | Score: {confluence_score} | "
                f"Conf: {confidence}% | Sebep: {result['watch_reason']}"
            )
        else:
            return None

        return result

    # =================================================================
    #  BÖLÜM 20 — YARDIMCI METODLAR
    # =================================================================

    def _build_watch_from_potential(self, symbol, df, multi_tf_data, htf_result, structure, bias):
        """
        Sweep henüz olmadığında potansiyel WATCH veya B-tier SIGNAL oluştur.

        Sweep olmadan da sinyal üretilebilir (Tier-B) — ancak şartlar:
          1. HTF bias uyumlu (Gate 1 geçti)
          2. MTF (1H) onaylıyor
          3. Displacement tespit edildi
          4. FVG mevcut ve fiyat yakınında
          5. OB desteği var (tercihen)

        Bu sayede optimizer yeterli veri toplayıp öğrenebilir.
        Sweep'li sinyaller hâlâ A/A+ tier olarak en yüksek öncelikli kalır.
        """
        analysis = self.calculate_confluence(df, multi_tf_data, override_direction=bias)
        confluence_score = analysis["confluence_score"]
        confidence = self._calculate_confidence(analysis)
        min_confluence = self.params.get("min_confluence_score", 60)
        current_price = df["close"].iloc[-1]
        components = analysis.get("components", [])

        # === B-Tier SIGNAL: Sweep yok ama yeterli çakışma var ===
        # HTF zorunlu + destekleyici bileşenlerden en az 2 tanesi
        has_htf = "HTF_CONFIRMATION" in components
        has_displacement = "DISPLACEMENT" in components
        has_fvg = "FVG" in components
        has_mtf = "MTF_CONFIRMATION" in components
        has_ob = "ORDER_BLOCK" in components
        has_structure = "MARKET_STRUCTURE" in components

        # HTF + herhangi 2 destekleyici bileşen
        support_count = sum([has_displacement, has_fvg, has_mtf, has_ob, has_structure])
        
        if has_htf and support_count >= 2 and confidence >= min_confluence * 0.5:
            # B-tier sinyal: yapısal giriş noktası hesapla
            # NOT: B-tier sinyaller HER ZAMAN WATCH olarak kalır.
            # Sweep olmadan trade açmak ICT'ye aykırıdır.
            # Bu sinyaller optimizer'ın öğrenmesi ve kullanıcıya bilgi
            # vermesi içindir — otomatik trade'e dönüşmez.
            b_signal = self._build_no_sweep_signal(
                symbol, df, multi_tf_data, bias, structure, analysis, confidence
            )
            if b_signal:
                # B-tier'ı zorla WATCH yap (hiçbir zaman SIGNAL olmamalı)
                b_signal["action"] = "WATCH"
                parts = [s for s in ['DISP' if has_displacement else '', 'FVG' if has_fvg else '', 'MTF' if has_mtf else '', 'OB' if has_ob else ''] if s]
                b_signal["watch_reason"] = (
                    f"B-tier: Sweep yok — {'|'.join(parts)}"
                    f" | Trade için sweep + displacement gerekli"
                )
                return b_signal

        # Yetersiz çakışma → standart WATCH
        # min_confluence'ın %50'si altındaysa hiç WATCH bile yapma
        if confluence_score < min_confluence * 0.5:
            return None

        return self._build_signal_dict(
            symbol, bias, current_price, analysis, confidence,
            action="WATCH",
            watch_reason="HTF bias uyumlu, likidite avı bekleniyor"
        )

    def _build_no_sweep_signal(self, symbol, df, multi_tf_data, bias, structure, analysis, confidence):
        """
        Sweep olmadan B-tier sinyal oluştur.
        FVG'den giriş, yapısal SL, likidite TP hesaplar.
        """
        current_price = df["close"].iloc[-1]
        components = analysis.get("components", [])

        # En yakın uygun FVG'yi bul
        fvgs = self.find_fvg(df)
        target_type = "BULLISH_FVG" if bias == "LONG" else "BEARISH_FVG"
        entry_fvg = None

        for fvg in fvgs:
            if fvg["type"] == target_type:
                # Fiyat FVG bölgesinde veya yakınında mı?
                if fvg["low"] * 0.995 <= current_price <= fvg["high"] * 1.005:
                    entry_fvg = fvg
                    break

        if entry_fvg is None:
            return None

        # Entry = FVG CE
        entry = (entry_fvg["high"] + entry_fvg["low"]) / 2

        # Yapısal SL (sweep yok → swing seviyelerinden hesapla)
        sl = self._calc_no_sweep_sl(df, bias, structure, entry)
        if sl is None:
            return None

        # TP hesapla
        tp = self._calc_opposing_liquidity_tp(df, multi_tf_data, entry, sl, bias, structure)
        if tp is None:
            return None

        # Seviye doğrulama
        if bias == "LONG":
            if sl >= entry or tp <= entry:
                return None
            risk = entry - sl
            reward = tp - entry
        else:
            if sl <= entry or tp >= entry:
                return None
            risk = sl - entry
            reward = entry - tp

        if risk <= 0:
            return None

        rr_ratio = reward / risk
        if rr_ratio < 1.5:
            return None

        sl_distance_pct = risk / entry
        if sl_distance_pct < 0.008 or sl_distance_pct > 0.05:
            return None

        # Entry modu
        price_at_fvg = entry_fvg["low"] * 0.998 <= current_price <= entry_fvg["high"] * 1.002
        entry_mode = "MARKET" if price_at_fvg else "LIMIT"

        session = self.get_session_info()
        min_confluence = self.params.get("min_confluence_score", 60)
        min_confidence = self.params.get("min_confidence", 65)

        result = {
            "symbol": symbol,
            "direction": bias,
            "entry": round(entry, 8),
            "sl": round(sl, 8),
            "tp": round(tp, 8),
            "current_price": round(current_price, 8),
            "confluence_score": analysis["confluence_score"],
            "confidence": confidence,
            "components": components,
            "penalties": analysis.get("penalties", []),
            "session": session.get("label", ""),
            "rr_ratio": round(rr_ratio, 2),
            "entry_type": f"FVG NoSweep ({entry_fvg['type']})",
            "sl_type": "Yapısal Seviye (Swing Point)",
            "tp_type": self._get_tp_type(analysis, tp, bias),
            "entry_mode": entry_mode,
            "htf_bias": analysis.get("htf_trend", ""),
            "quality_tier": "B",
            "analysis": analysis
        }

        # B-tier sinyaller HER ZAMAN WATCH olarak kalır
        # Sweep olmadan ICT trade'i açılmaz
        result["action"] = "WATCH"
        result["watch_reason"] = (
            f"B-tier: Sweep yok, trade için sweep + MSS gerekli | "
            f"Score: {analysis['confluence_score']} | Conf: {confidence}%"
        )
        logger.info(
            f"👀 B-TIER İZLEME: {symbol} {bias} | Entry: {entry:.8f} | "
            f"RR: {rr_ratio:.1f} | Score: {analysis['confluence_score']} | "
            f"Conf: {confidence}% | (sweep bekleniyor)"
        )

        return result

    def _calc_no_sweep_sl(self, df, bias, structure, entry):
        """
        Sweep olmadan yapısal SL hesapla.
        Son swing low/high'dan hesaplar.
        """
        if bias == "LONG":
            candidates = []
            if structure["last_swing_low"]:
                candidates.append(structure["last_swing_low"]["price"] * 0.997)
            # Son 20 mumun en düşüğü (fallback)
            recent_low = df.tail(20)["low"].min()
            candidates.append(recent_low * 0.997)

            valid = [p for p in candidates if 0 < p < entry]
            if not valid:
                return None
            return max(valid)  # Entry'ye en yakın geçerli SL

        elif bias == "SHORT":
            candidates = []
            if structure["last_swing_high"]:
                candidates.append(structure["last_swing_high"]["price"] * 1.003)
            recent_high = df.tail(20)["high"].max()
            candidates.append(recent_high * 1.003)

            valid = [p for p in candidates if p > entry]
            if not valid:
                return None
            return min(valid)  # Entry'ye en yakın geçerli SL

        return None

    def _build_signal_dict(self, symbol, bias, current_price, analysis, confidence,
                           action="WATCH", watch_reason=""):
        """WATCH sinyalleri için ortak dict oluşturucu.
        
        DİKKAT: Bu metod ICT gate'lerini GEÇMEMİŞ sinyaller içindir.
        quality_tier = "POTENTIAL" → trade_manager'da ASLA trade açılmaz.
        Sadece bilgilendirme amaçlı WATCH'a alınabilir.
        """
        # Basit SL/TP tahmini (WATCH için yaklaşık — trade'e dönüşmeyecek)
        structure = analysis.get("structure", {})
        sl_pct = self.params.get("default_sl_pct", 0.015)
        tp_ratio = self.params.get("default_tp_ratio", 2.5)

        if bias == "LONG":
            sl = current_price * (1 - sl_pct)
            tp = current_price * (1 + sl_pct * tp_ratio)
        else:
            sl = current_price * (1 + sl_pct)
            tp = current_price * (1 - sl_pct * tp_ratio)

        risk = abs(current_price - sl)
        reward = abs(tp - current_price)
        rr_ratio = reward / risk if risk > 0 else 1.0

        session = self.get_session_info()

        result = {
            "symbol": symbol,
            "direction": bias,
            "entry": round(current_price, 8),
            "sl": round(sl, 8),
            "tp": round(tp, 8),
            "current_price": round(current_price, 8),
            "confluence_score": analysis.get("confluence_score", 0),
            "confidence": confidence,
            "components": analysis.get("components", []),
            "penalties": analysis.get("penalties", []),
            "session": session.get("label", ""),
            "rr_ratio": round(rr_ratio, 2),
            "entry_type": "Potansiyel (onay bekleniyor)",
            "sl_type": "Tahmini (onay sonrası kesinleşecek)",
            "tp_type": "Tahmini (onay sonrası kesinleşecek)",
            "entry_mode": "PENDING",
            "action": action,
            "watch_reason": watch_reason,
            "analysis": analysis,
            # ── KRİTİK: Gate'leri geçmemiş → POTENTIAL tier
            # trade_manager bu tier ile ASLA trade açmaz
            "quality_tier": "POTENTIAL",
            "htf_bias": analysis.get("htf_bias", "?"),
        }

        return result

    def _get_watch_reason(self, analysis):
        """İzleme sebebini açıkla (hangi gate eksik)."""
        reasons = []
        components = analysis.get("components", [])
        penalties = analysis.get("penalties", [])

        if "HTF_CONFIRMATION" not in components:
            reasons.append("HTF onayı bekleniyor")
        if "LIQUIDITY_SWEEP" not in components:
            reasons.append("Likidite avı bekleniyor")
        if "DISPLACEMENT" not in components:
            reasons.append("Displacement bekleniyor")
        if "FVG" not in components:
            reasons.append("FVG dolumu bekleniyor")
        if "MARKET_STRUCTURE" not in components:
            reasons.append("Yapı onayı bekleniyor")

        for p in penalties:
            if "RANGING" in p:
                reasons.append("Yatay piyasa")

        if not reasons:
            reasons.append("Skor yetersiz, ek onay bekleniyor")

        return " | ".join(reasons[:3])

    def _get_tp_type(self, analysis, tp, direction):
        """TP seviyesinin ICT kaynağını belirle."""
        # HTF Draw on Liquidity?
        htf_liq = analysis.get("htf_liquidity", [])
        for liq in htf_liq:
            if direction == "LONG" and liq["type"] == "EQUAL_HIGHS" and not liq["swept"]:
                if abs(tp - liq["price"]) / tp < 0.005:
                    return "HTF Draw on Liquidity (4H Equal Highs)"
            elif direction == "SHORT" and liq["type"] == "EQUAL_LOWS" and not liq["swept"]:
                if abs(tp - liq["price"]) / tp < 0.005:
                    return "HTF Draw on Liquidity (4H Equal Lows)"

        # LTF liquidity?
        liq_levels = analysis.get("liquidity", [])
        for liq in liq_levels:
            if direction == "LONG" and liq["type"] == "EQUAL_HIGHS":
                if abs(tp - liq["price"]) / tp < 0.005:
                    return "Karşı Likidite (Equal Highs)"
            elif direction == "SHORT" and liq["type"] == "EQUAL_LOWS":
                if abs(tp - liq["price"]) / tp < 0.005:
                    return "Karşı Likidite (Equal Lows)"

        # Order Block?
        obs = analysis.get("order_blocks", [])
        for ob in obs:
            if direction == "LONG" and ob["type"] == "BEARISH_OB":
                if abs(tp - ob["low"]) / tp < 0.005:
                    return "Karşı Order Block (Bearish OB)"
            elif direction == "SHORT" and ob["type"] == "BULLISH_OB":
                if abs(tp - ob["high"]) / tp < 0.005:
                    return "Karşı Order Block (Bullish OB)"

        # Swing yapısı?
        structure = analysis.get("structure", {})
        if direction == "LONG":
            for sh in structure.get("swing_highs", []):
                if abs(tp - sh["price"]) / tp < 0.005:
                    return "Swing High Yapısal Hedef"
        else:
            for sl_p in structure.get("swing_lows", []):
                if abs(tp - sl_p["price"]) / tp < 0.005:
                    return "Swing Low Yapısal Hedef"

        return "Minimum R:R Hedefi"


# Global instance
ict_strategy = ICTStrategy()
