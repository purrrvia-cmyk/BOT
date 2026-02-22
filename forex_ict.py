# =====================================================
# ICT Forex/Commodities - TAM ICT UYUMLU Motor v2
# =====================================================
# Inner Circle Trader (ICT) metodolojisi ile
# Forex ve emtia piyasalarinda sinyal uretir.
#
# TAM ICT KAVRAMLARI:
#   1.  Market Structure (BOS / CHoCH / Swing H-L)
#   2.  Order Blocks (OB) + Mitigation tracking
#   3.  Breaker Blocks (basarisiz OB -> destek/direnc)
#   4.  Fair Value Gaps (FVG) + Consequent Encroachment
#   5.  Displacement (buyuk momentum mumlari)
#   6.  Liquidity Sweeps (EQH/EQL + stop hunt)
#   7.  Inducement (kucuk likidite kapanlari)
#   8.  Optimal Trade Entry (OTE) - Fib 0.618-0.786
#   9.  Premium / Discount Zones
#  10.  Kill Zones (London/NY/Asian sessions)
#  11.  ICT Silver Bullet windows
#  12.  Power of 3 / AMD pattern
#  13.  Judas Swing (fake move in Kill Zone)
#  14.  Daily Bias (HTF trend confirmation)
#  15.  Asian Range + London Breakout
#  16.  Smart Money Trap detection
# =====================================================

import yfinance as yf
import pandas as pd
import numpy as np
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("ICT-Bot.ForexICT")

# ── Enstruman haritasi ──
FOREX_INSTRUMENTS = {
    "EURUSD": {
        "yf_symbol": "EURUSD=X",
        "name": "EUR/USD",
        "category": "forex",
        "pip_size": 0.0001,
        "icon": "\u20ac",
        "desc": "Euro / ABD Dolari"
    },
    "GBPUSD": {
        "yf_symbol": "GBPUSD=X",
        "name": "GBP/USD",
        "category": "forex",
        "pip_size": 0.0001,
        "icon": "\u00a3",
        "desc": "Ingiliz Sterlini / ABD Dolari"
    },
    "USDJPY": {
        "yf_symbol": "USDJPY=X",
        "name": "USD/JPY",
        "category": "forex",
        "pip_size": 0.01,
        "icon": "\u00a5",
        "desc": "ABD Dolari / Japon Yeni"
    },
    "XAUUSD": {
        "yf_symbol": "GC=F",
        "name": "XAU/USD",
        "category": "commodity",
        "pip_size": 0.01,
        "icon": "\U0001f947",
        "desc": "Altin / ABD Dolari"
    },
    "XAGUSD": {
        "yf_symbol": "SI=F",
        "name": "XAG/USD",
        "category": "commodity",
        "pip_size": 0.001,
        "icon": "\U0001f948",
        "desc": "Gumus / ABD Dolari"
    },
    "USDCHF": {
        "yf_symbol": "USDCHF=X",
        "name": "USD/CHF",
        "category": "forex",
        "pip_size": 0.0001,
        "icon": "\u20a3",
        "desc": "ABD Dolari / Isvicre Frangi"
    },
}

TF_MAP = {
    "15m": {"interval": "15m", "period": "5d",  "label": "15 Dakika"},
    "1h":  {"interval": "1h",  "period": "30d", "label": "1 Saat"},
    "4h":  {"interval": "1h",  "period": "60d", "label": "4 Saat"},
    "1d":  {"interval": "1d",  "period": "6mo", "label": "Gunluk"},
}


class ForexICTEngine:
    """Tam ICT uyumlu Forex/Emtia analiz motoru"""

    def __init__(self):
        self._cache = {}
        self._cache_ttl = 30

    # ================================================================
    #  VERI CEKME
    # ================================================================

    def get_candles(self, instrument_key, timeframe="1h"):
        """yfinance'den mum verileri cek"""
        cache_key = f"fx_{instrument_key}_{timeframe}"
        now = datetime.now().timestamp()
        if cache_key in self._cache:
            ts, data = self._cache[cache_key]
            if now - ts < self._cache_ttl:
                return data

        inst = FOREX_INSTRUMENTS.get(instrument_key)
        if not inst:
            return pd.DataFrame()

        tf_cfg = TF_MAP.get(timeframe, TF_MAP["1h"])

        try:
            ticker = yf.Ticker(inst["yf_symbol"])

            if timeframe == "4h":
                raw = ticker.history(period=tf_cfg["period"], interval="1h", auto_adjust=True)
                if raw.empty:
                    return pd.DataFrame()
                raw = raw.reset_index()
                raw.columns = [c.lower().replace(" ", "_") for c in raw.columns]
                if "datetime" in raw.columns:
                    raw = raw.rename(columns={"datetime": "timestamp"})
                elif "date" in raw.columns:
                    raw = raw.rename(columns={"date": "timestamp"})
                # 4 saatlik pencerelere göre grupla (hafta sonu boşluklarını doğru işler)
                raw["timestamp"] = pd.to_datetime(raw["timestamp"], utc=True)
                raw["group"] = raw["timestamp"].dt.floor("4h")
                df = raw.groupby("group").agg({
                    "timestamp": "first", "open": "first",
                    "high": "max", "low": "min",
                    "close": "last", "volume": "sum"
                }).reset_index(drop=True)
            else:
                raw = ticker.history(period=tf_cfg["period"], interval=tf_cfg["interval"], auto_adjust=True)
                if raw.empty:
                    return pd.DataFrame()
                raw = raw.reset_index()
                raw.columns = [c.lower().replace(" ", "_") for c in raw.columns]
                if "datetime" in raw.columns:
                    raw = raw.rename(columns={"datetime": "timestamp"})
                elif "date" in raw.columns:
                    raw = raw.rename(columns={"date": "timestamp"})
                df = raw[["timestamp", "open", "high", "low", "close", "volume"]].copy()

            df = df.dropna(subset=["close"]).reset_index(drop=True)
            self._cache[cache_key] = (now, df)
            return df
        except Exception as e:
            logger.error(f"Forex veri hatasi ({instrument_key}): {e}")
            return pd.DataFrame()

    def get_price(self, instrument_key):
        """Anlik fiyat bilgisi"""
        inst = FOREX_INSTRUMENTS.get(instrument_key)
        if not inst:
            return None
        try:
            ticker = yf.Ticker(inst["yf_symbol"])
            info = ticker.fast_info

            def _safe_get(obj, *keys):
                """fast_info'dan güvenli değer oku (dict veya attribute)"""
                for k in keys:
                    try:
                        if hasattr(obj, 'get'):
                            v = obj.get(k, None)
                            if v is not None and v != 0:
                                return float(v)
                        v = getattr(obj, k, None)
                        if v is not None and v != 0:
                            return float(v)
                    except Exception:
                        continue
                return 0.0

            last = _safe_get(info, "lastPrice", "last_price", "regularMarketPrice")
            prev = _safe_get(info, "previousClose", "previous_close", "regularMarketPreviousClose")
            if last == 0:
                last = prev

            return {
                "last": round(last, 5),
                "prev_close": round(prev, 5),
                "open": round(_safe_get(info, "open", "regularMarketOpen"), 5),
                "day_high": round(_safe_get(info, "dayHigh", "day_high", "regularMarketDayHigh"), 5),
                "day_low": round(_safe_get(info, "dayLow", "day_low", "regularMarketDayLow"), 5),
            }
        except Exception as e:
            logger.error(f"Fiyat hatasi ({instrument_key}): {e}")
            # Fallback: son mum kapanışını kullan
            try:
                df = self.get_candles(instrument_key, "15m")
                if not df.empty:
                    cp = float(df["close"].iloc[-1])
                    return {"last": round(cp, 5), "prev_close": round(cp, 5),
                            "open": round(cp, 5), "day_high": round(cp, 5), "day_low": round(cp, 5)}
            except Exception:
                pass
            return None

    # ================================================================
    #  1. MARKET STRUCTURE  (BOS / CHoCH / Swing)
    # ================================================================

    def detect_market_structure(self, df):
        """Swing High/Low, BOS, CHoCH tespiti"""
        if len(df) < 20:
            return {"trend": "NEUTRAL", "bos": [], "choch": None,
                    "swing_highs": [], "swing_lows": []}

        highs = df["high"].values
        lows = df["low"].values
        close = df["close"].values
        lookback = 5

        swing_highs = []
        swing_lows = []
        for i in range(lookback, len(df) - lookback):
            if highs[i] == max(highs[i - lookback:i + lookback + 1]):
                swing_highs.append({"idx": i, "price": float(highs[i])})
            if lows[i] == min(lows[i - lookback:i + lookback + 1]):
                swing_lows.append({"idx": i, "price": float(lows[i])})

        trend = "NEUTRAL"
        bos_list = []
        choch = None

        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            hh = swing_highs[-1]["price"] > swing_highs[-2]["price"]
            hl = swing_lows[-1]["price"] > swing_lows[-2]["price"]
            lh = swing_highs[-1]["price"] < swing_highs[-2]["price"]
            ll = swing_lows[-1]["price"] < swing_lows[-2]["price"]

            if hh and hl:
                trend = "BULLISH"
            elif lh and ll:
                trend = "BEARISH"

            cur_price = close[-1]
            if swing_highs and cur_price > swing_highs[-1]["price"]:
                bos_list.append({"type": "BULLISH_BOS", "level": swing_highs[-1]["price"]})
            if swing_lows and cur_price < swing_lows[-1]["price"]:
                bos_list.append({"type": "BEARISH_BOS", "level": swing_lows[-1]["price"]})

            # CHoCH
            if len(swing_highs) >= 3 and len(swing_lows) >= 3:
                if (swing_highs[-3]["price"] < swing_highs[-2]["price"] and
                        swing_highs[-1]["price"] < swing_highs[-2]["price"]):
                    choch = {"type": "BEARISH_CHOCH", "level": swing_lows[-2]["price"],
                             "desc": "Yukselis trendinden dususe donus sinyali (CHoCH)"}
                if (swing_lows[-3]["price"] > swing_lows[-2]["price"] and
                        swing_lows[-1]["price"] > swing_lows[-2]["price"]):
                    choch = {"type": "BULLISH_CHOCH", "level": swing_highs[-2]["price"],
                             "desc": "Dusus trendinden yukselise donus sinyali (CHoCH)"}

        return {
            "trend": trend, "bos": bos_list, "choch": choch,
            "swing_highs": swing_highs[-4:] if swing_highs else [],
            "swing_lows": swing_lows[-4:] if swing_lows else [],
        }

    # ================================================================
    #  2. ORDER BLOCKS  (OB) + Mitigation
    # ================================================================

    def detect_order_blocks(self, df, cur_price=None):
        """OB tespiti + mitigate olup olmadigini izle"""
        if len(df) < 10:
            return []

        opens = df["open"].values
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        blocks = []

        for i in range(2, len(df) - 2):
            candle_range = highs[i] - lows[i]
            if candle_range == 0:
                continue
            avg_range = np.mean([highs[j] - lows[j] for j in range(max(0, i - 10), i)])
            if avg_range == 0:
                continue
            next_move = abs(closes[min(i + 2, len(df) - 1)] - closes[i])

            if next_move > avg_range * 2:
                if closes[i] < opens[i] and closes[min(i + 2, len(df) - 1)] > closes[i]:
                    mitigated = False
                    if cur_price is not None:
                        for k in range(i + 3, len(df)):
                            if lows[k] <= closes[i]:
                                mitigated = True
                                break
                    blocks.append({
                        "type": "BULLISH_OB", "high": float(opens[i]),
                        "low": float(closes[i]), "idx": i,
                        "strength": round(next_move / avg_range, 1),
                        "mitigated": mitigated
                    })
                elif closes[i] > opens[i] and closes[min(i + 2, len(df) - 1)] < closes[i]:
                    mitigated = False
                    if cur_price is not None:
                        for k in range(i + 3, len(df)):
                            if highs[k] >= closes[i]:
                                mitigated = True
                                break
                    blocks.append({
                        "type": "BEARISH_OB", "high": float(closes[i]),
                        "low": float(opens[i]), "idx": i,
                        "strength": round(next_move / avg_range, 1),
                        "mitigated": mitigated
                    })

        blocks.sort(key=lambda x: x["strength"], reverse=True)
        return blocks[:8]

    # ================================================================
    #  3. BREAKER BLOCKS  (basarisiz OB -> S/R)
    # ================================================================

    def detect_breaker_blocks(self, df):
        """Basarisiz OB'leri Breaker Block olarak isle"""
        if len(df) < 15:
            return []

        obs = self.detect_order_blocks(df, cur_price=float(df["close"].iloc[-1]))
        breakers = []
        cur_price = float(df["close"].iloc[-1])

        for ob in obs:
            if ob["mitigated"]:
                mid = (ob["high"] + ob["low"]) / 2
                if ob["type"] == "BULLISH_OB":
                    if cur_price < mid:
                        breakers.append({
                            "type": "BEARISH_BREAKER",
                            "high": ob["high"], "low": ob["low"],
                            "desc": "Basarisiz Bullish OB -> Bearish Breaker (direnc)"
                        })
                elif ob["type"] == "BEARISH_OB":
                    if cur_price > mid:
                        breakers.append({
                            "type": "BULLISH_BREAKER",
                            "high": ob["high"], "low": ob["low"],
                            "desc": "Basarisiz Bearish OB -> Bullish Breaker (destek)"
                        })
        return breakers[:3]

    # ================================================================
    #  4. FVG + Consequent Encroachment (CE)
    # ================================================================

    def detect_fvg(self, df):
        """FVG tespiti + %50 seviyesine geri donus (CE) kontrolu"""
        if len(df) < 5:
            return []

        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        gaps = []

        for i in range(2, len(df)):
            # Bullish FVG
            if lows[i] > highs[i - 2]:
                gap_size = lows[i] - highs[i - 2]
                ce_level = (lows[i] + highs[i - 2]) / 2  # %50 seviye
                filled = False
                ce_tested = False
                for k in range(i + 1, len(df)):
                    if lows[k] <= highs[i - 2]:
                        filled = True
                        break
                    if lows[k] <= ce_level:
                        ce_tested = True
                status = "Dolduruldu" if filled else ("CE test edildi" if ce_tested else "Aktif")
                gaps.append({
                    "type": "BULLISH_FVG",
                    "top": float(lows[i]), "bottom": float(highs[i - 2]),
                    "ce_level": float(ce_level),
                    "size": float(gap_size), "idx": i,
                    "filled": filled, "ce_tested": ce_tested,
                    "desc": f"Yukari FVG: {float(highs[i-2]):.5f} - {float(lows[i]):.5f} arasi bosluk (CE: {float(ce_level):.5f}) [{status}]",
                })
            # Bearish FVG
            if highs[i] < lows[i - 2]:
                gap_size = lows[i - 2] - highs[i]
                ce_level = (lows[i - 2] + highs[i]) / 2
                filled = False
                ce_tested = False
                for k in range(i + 1, len(df)):
                    if highs[k] >= lows[i - 2]:
                        filled = True
                        break
                    if highs[k] >= ce_level:
                        ce_tested = True
                status = "Dolduruldu" if filled else ("CE test edildi" if ce_tested else "Aktif")
                gaps.append({
                    "type": "BEARISH_FVG",
                    "top": float(lows[i - 2]), "bottom": float(highs[i]),
                    "ce_level": float(ce_level),
                    "size": float(gap_size), "idx": i,
                    "filled": filled, "ce_tested": ce_tested,
                    "desc": f"Asagi FVG: {float(highs[i]):.5f} - {float(lows[i-2]):.5f} arasi bosluk (CE: {float(ce_level):.5f}) [{status}]",
                })

        return gaps[-12:]

    # ================================================================
    #  5. DISPLACEMENT  (buyuk momentum mumlari)
    # ================================================================

    def detect_displacement(self, df):
        """Buyuk govdeli momentum mumlari (displacement candles)"""
        if len(df) < 20:
            return []

        opens = df["open"].values
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        displacements = []

        avg_body = np.mean([abs(closes[i] - opens[i]) for i in range(max(0, len(df) - 20), len(df))])
        if avg_body == 0:
            return []

        for i in range(len(df) - 15, len(df)):
            if i < 0:
                continue
            body = abs(closes[i] - opens[i])
            candle_range = highs[i] - lows[i]
            if candle_range == 0:
                continue
            body_ratio = body / candle_range

            if body > avg_body * 2.5 and body_ratio > 0.7:
                direction = "BULLISH" if closes[i] > opens[i] else "BEARISH"
                displacements.append({
                    "type": f"{direction}_DISPLACEMENT",
                    "idx": i,
                    "body_mult": round(body / avg_body, 1),
                    "body_ratio": round(body_ratio, 2),
                    "desc": f"{direction} Displacement - {round(body / avg_body, 1)}x ortalama govde"
                })

        return displacements[-5:]

    # ================================================================
    #  6. LIQUIDITY SWEEPS  (EQH/EQL + Stop Hunt)
    # ================================================================

    def detect_liquidity_sweeps(self, df):
        """Equal Highs/Lows uzerinde likidite avi"""
        if len(df) < 20:
            return []

        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        tolerance = 0.001
        sweeps = []

        for i in range(5, len(df) - 1):
            # Sell-side sweep (EQH uzerine cikip donus)
            for j in range(max(0, i - 15), i - 3):
                if abs(highs[j] - highs[i - 1]) / max(highs[j], 0.0001) < tolerance:
                    if highs[i] > highs[j] * 1.001 and closes[i] < highs[j]:
                        sweeps.append({
                            "type": "SELL_SIDE_SWEEP",
                            "level": float(highs[j]),
                            "sweep_price": float(highs[i]),
                            "idx": i,
                            "desc": "Ust likidite supuruldu (EQH) — fiyat zirveler uzerine cikip geri dondu, dusus donusu beklenir"
                        })
                        break
            # Buy-side sweep (EQL altina inip donus)
            for j in range(max(0, i - 15), i - 3):
                if abs(lows[j] - lows[i - 1]) / max(lows[j], 0.0001) < tolerance:
                    if lows[i] < lows[j] * 0.999 and closes[i] > lows[j]:
                        sweeps.append({
                            "type": "BUY_SIDE_SWEEP",
                            "level": float(lows[j]),
                            "sweep_price": float(lows[i]),
                            "idx": i,
                            "desc": "Alt likidite supuruldu (EQL) — fiyat dipler altina inip geri dondu, yukselis donusu beklenir"
                        })
                        break

        return sweeps[-5:]

    # ================================================================
    #  7. INDUCEMENT  (kucuk likidite kapanlari)
    # ================================================================

    def detect_inducement(self, df, swing_data):
        """
        Kucuk internal yapilarda likidite birikimi.
        Ana swing icinde olusmus minor high/low kirilmalari.
        """
        if len(df) < 20 or not swing_data.get("swing_highs") or not swing_data.get("swing_lows"):
            return []

        highs = df["high"].values
        lows = df["low"].values
        inducements = []

        last_sh = swing_data["swing_highs"][-1] if swing_data["swing_highs"] else None
        last_sl = swing_data["swing_lows"][-1] if swing_data["swing_lows"] else None

        if last_sh and last_sl:
            start_idx = min(last_sh["idx"], last_sl["idx"])
            end_idx = max(last_sh["idx"], last_sl["idx"])

            mini_highs = []
            mini_lows = []
            for i in range(start_idx + 1, min(end_idx, len(df) - 1)):
                if i >= 2 and i < len(df) - 2:
                    if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
                        mini_highs.append({"idx": i, "price": float(highs[i])})
                    if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
                        mini_lows.append({"idx": i, "price": float(lows[i])})

            for mh in mini_highs:
                for k in range(mh["idx"] + 1, min(mh["idx"] + 5, len(df))):
                    if highs[k] > mh["price"]:
                        inducements.append({
                            "type": "BULLISH_INDUCEMENT",
                            "level": mh["price"], "idx": k,
                            "desc": "Minor high kirildi - buy-side likidite toplandi"
                        })
                        break

            for ml in mini_lows:
                for k in range(ml["idx"] + 1, min(ml["idx"] + 5, len(df))):
                    if lows[k] < ml["price"]:
                        inducements.append({
                            "type": "BEARISH_INDUCEMENT",
                            "level": ml["price"], "idx": k,
                            "desc": "Minor low kirildi - sell-side likidite toplandi"
                        })
                        break

        return inducements[-3:]

    # ================================================================
    #  8. OTE  (Optimal Trade Entry - Fib 0.618-0.786)
    # ================================================================

    def calc_ote(self, df, ms):
        """Fibonacci 0.618-0.786 geri cekilme bolgesi"""
        if not ms["swing_highs"] or not ms["swing_lows"]:
            return None

        sh = ms["swing_highs"][-1]
        sl = ms["swing_lows"][-1]
        swing_range = sh["price"] - sl["price"]
        if swing_range <= 0:
            return None

        if ms["trend"] == "BULLISH":
            fib_618 = sh["price"] - swing_range * 0.618
            fib_786 = sh["price"] - swing_range * 0.786
            return {
                "direction": "LONG",
                "ote_top": round(fib_618, 5), "ote_bottom": round(fib_786, 5),
                "swing_high": sh["price"], "swing_low": sl["price"],
                "desc": f"OTE alim bolgesi: {round(fib_786,5)} - {round(fib_618,5)}"
            }
        elif ms["trend"] == "BEARISH":
            fib_618 = sl["price"] + swing_range * 0.618
            fib_786 = sl["price"] + swing_range * 0.786
            return {
                "direction": "SHORT",
                "ote_top": round(fib_786, 5), "ote_bottom": round(fib_618, 5),
                "swing_high": sh["price"], "swing_low": sl["price"],
                "desc": f"OTE satis bolgesi: {round(fib_618,5)} - {round(fib_786,5)}"
            }
        return None

    # ================================================================
    #  9. PREMIUM / DISCOUNT ZONES
    # ================================================================

    def calc_premium_discount(self, df):
        """Equilibrium ustu premium, alti discount"""
        if len(df) < 20:
            return {"zone": "NEUTRAL", "eq": 0, "range_high": 0, "range_low": 0}

        lookback = min(50, len(df))
        range_high = float(df["high"].iloc[-lookback:].max())
        range_low = float(df["low"].iloc[-lookback:].min())
        eq = (range_high + range_low) / 2
        cur_price = float(df["close"].iloc[-1])

        if cur_price > eq:
            pct = (cur_price - eq) / (range_high - eq) * 100 if range_high != eq else 50
            zone = "PREMIUM"
        else:
            pct = (eq - cur_price) / (eq - range_low) * 100 if eq != range_low else 50
            zone = "DISCOUNT"

        return {
            "zone": zone, "zone_pct": round(min(pct, 100), 1),
            "equilibrium": round(eq, 5),
            "range_high": round(range_high, 5),
            "range_low": round(range_low, 5),
            "current": round(cur_price, 5),
        }

    # ================================================================
    # 10. KILL ZONES
    # ================================================================

    def detect_kill_zones(self):
        """London / NY / Asian Kill Zone tespiti (EST bazli, DST uyumlu)"""
        now = datetime.utcnow()
        hour = now.hour
        minute = now.minute
        active = None
        zones = []

        # ICT Kill Zone'lari EST/EDT bazlidir
        # UTC'den EST'e cevirme: kis UTC-5, yaz UTC-4
        # Basit DST tespiti: Mart 2. Pazar - Kasim 1. Pazar
        month = now.month
        is_dst = 3 < month < 11  # Yaklasik DST (Mart sonu - Kasim basi)
        if month == 3 and now.day >= 8:  # Mart 2. hafta sonrasi
            is_dst = True
        elif month == 11 and now.day < 7:  # Kasim 1. hafta oncesi
            is_dst = True
        est_offset = 4 if is_dst else 5

        kz_defs = [
            ("Asian",     0,  3,  "00:00-03:00 UTC",  "Asya piyasalari acik, dusuk volatilite, range olusumu"),
            ("London",    7, 10,  "07:00-10:00 UTC",   "En yuksek likidite, trend baslangici, buyuk hacimliler"),
            ("New York", 12, 15,  "12:00-15:00 UTC",   "NY acilisi, London ile overlap, en volatil donem"),
        ]

        for name, start, end, hours_str, zone_desc in kz_defs:
            is_active = start <= hour < end
            remaining = ""
            if is_active:
                active = name.upper().replace(" ", "_")
                mins_left = (end - hour - 1) * 60 + (60 - minute)
                remaining = f" ({mins_left} dk kaldi)"
            zones.append({
                "name": f"{name} Kill Zone",
                "active": is_active,
                "hours": hours_str,
                "est_hours": f"{(start - est_offset) % 24:02d}:00-{(end - est_offset) % 24:02d}:00 {'EDT' if is_dst else 'EST'}",
                "desc": zone_desc,
            })

        # Sonraki KZ'yi bul
        next_kz = None
        for name, start, end, _, _ in kz_defs:
            if hour < start:
                mins_until = (start - hour) * 60 - minute
                next_kz = f"{name} KZ {mins_until} dk sonra basliyor"
                break
        if not next_kz:
            # Bugün bittiyse yarın Asian
            mins_until = (24 - hour) * 60 - minute
            next_kz = f"Asian KZ {mins_until} dk sonra basliyor"

        if active:
            active_zone = [z for z in zones if z["active"]][0]
            mins_left = 0
            for name, start, end, _, _ in kz_defs:
                if name.upper().replace(" ", "_") == active:
                    mins_left = (end - hour - 1) * 60 + (60 - minute)
                    break
            desc = f"{active.replace('_', ' ').title()} Kill Zone aktif - {mins_left} dk kaldi | Yuksek volatilite beklenir"
        else:
            desc = f"Kill Zone disinda - dusuk volatilite | {next_kz}"

        return {
            "active_zone": active, "zones": zones,
            "is_kill_zone": active is not None,
            "next_kz": next_kz,
            "desc": desc,
        }

    # ================================================================
    # 11. ICT SILVER BULLET
    # ================================================================

    def detect_silver_bullet(self):
        """
        ICT Silver Bullet pencereleri (DST uyumlu):
        - London SB: 03:00-04:00 EST (08:00-09:00 UTC / yaz: 07:00-08:00 UTC)
        - NY AM SB:  10:00-11:00 EST (15:00-16:00 UTC / yaz: 14:00-15:00 UTC)
        - NY PM SB:  14:00-15:00 EST (19:00-20:00 UTC / yaz: 18:00-19:00 UTC)
        """
        now = datetime.utcnow()
        hour = now.hour
        minute = now.minute

        # DST tespiti (Kill Zone ile ayni mantik)
        month = now.month
        is_dst = 3 < month < 11
        if month == 3 and now.day >= 8:
            is_dst = True
        elif month == 11 and now.day < 7:
            is_dst = True
        est_offset = 4 if is_dst else 5

        # EST bazli pencereler → UTC'ye cevir
        sb_windows = [
            {"name": "London Silver Bullet", "est_start": 3, "est_end": 4,
             "desc": "FVG giris firsati - London acilis"},
            {"name": "NY AM Silver Bullet", "est_start": 10, "est_end": 11,
             "desc": "FVG giris firsati - New York sabah"},
            {"name": "NY PM Silver Bullet", "est_start": 14, "est_end": 15,
             "desc": "FVG giris firsati - New York ogleden sonra"},
        ]

        active_sb = None
        for sb in sb_windows:
            sb["start_h"] = sb["est_start"] + est_offset
            sb["end_h"] = sb["est_end"] + est_offset
            sb["hours"] = f"{sb['start_h']:02d}:00-{sb['end_h']:02d}:00 UTC"
            sb["est_hours"] = f"{sb['est_start']:02d}:00-{sb['est_end']:02d}:00 {'EDT' if is_dst else 'EST'}"
            sb["active"] = sb["start_h"] <= hour < sb["end_h"]
            if sb["active"]:
                mins_left = (sb["end_h"] - hour - 1) * 60 + (60 - minute)
                sb["remaining"] = f"{mins_left} dk kaldi"
                active_sb = sb

        if active_sb:
            desc = f"[AKTIF] {active_sb['name']} - {active_sb['desc']} ({active_sb['remaining']})"
        else:
            # Sonraki SB'yi bul
            next_sb = None
            for sb in sb_windows:
                if hour < sb["start_h"]:
                    mins_until = (sb["start_h"] - hour) * 60 - minute
                    next_sb = f"{sb['name']} {mins_until} dk sonra"
                    break
            desc = f"Silver Bullet disinda" + (f" | {next_sb}" if next_sb else "")

        return {
            "windows": sb_windows,
            "active": active_sb,
            "is_active": active_sb is not None,
            "desc": desc,
        }

    # ================================================================
    # 12. POWER OF 3 / AMD  (Accumulation-Manipulation-Distribution)
    # ================================================================

    def detect_amd_pattern(self, df):
        """
        Son N mumda AMD patern arama:
        - Accumulation: dusuk volatiliteli daralma
        - Manipulation: ani spike (likidite avi)
        - Distribution: gercek yon hareketi
        """
        if len(df) < 20:
            return None

        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values

        lookback = min(20, len(df) - 1)
        recent = df.iloc[-lookback:]

        # Accumulation: ilk %40 dusuk volatilite
        acc_end = int(lookback * 0.4)
        acc_range = highs[-lookback:-lookback + acc_end].max() - lows[-lookback:-lookback + acc_end].min()
        full_range = highs[-lookback:].max() - lows[-lookback:].min()

        if full_range == 0:
            return None

        acc_ratio = acc_range / full_range

        # Manipulation: ortadaki %30'da spike
        man_start = acc_end
        man_end = int(lookback * 0.7)
        if man_end <= man_start:
            return None

        man_high = highs[-lookback + man_start:-lookback + man_end].max() if man_start < man_end else 0
        man_low = lows[-lookback + man_start:-lookback + man_end].min() if man_start < man_end else 0

        # Distribution: son %30 gercek hareket
        dist_close = closes[-1]
        dist_open = closes[-lookback + man_end] if man_end < lookback else closes[-1]

        if acc_ratio < 0.4:  # Accumulation yeterince dar
            direction = "BULLISH" if dist_close > dist_open else "BEARISH"

            # Manipulation yonu: gercek yonun tersi olmali
            man_mid = (man_high + man_low) / 2
            acc_mid = (highs[-lookback:-lookback + acc_end].max() + lows[-lookback:-lookback + acc_end].min()) / 2

            if direction == "BULLISH" and man_low < lows[-lookback:-lookback + acc_end].min():
                return {
                    "pattern": "AMD_BULLISH",
                    "phase": "DISTRIBUTION",
                    "direction": "LONG",
                    "desc": "AMD Bullish: Birikme -> asagi manipulasyon -> yukari dagitim",
                    "acc_range": round(float(acc_range), 5),
                }
            elif direction == "BEARISH" and man_high > highs[-lookback:-lookback + acc_end].max():
                return {
                    "pattern": "AMD_BEARISH",
                    "phase": "DISTRIBUTION",
                    "direction": "SHORT",
                    "desc": "AMD Bearish: Birikme -> yukari manipulasyon -> asagi dagitim",
                    "acc_range": round(float(acc_range), 5),
                }
        return None

    # ================================================================
    # 13. JUDAS SWING
    # ================================================================

    def detect_judas_swing(self, df):
        """Kill Zone'da ilk hareketin tersi yonde sinyal
        Mum sayısı TF'ye göre ayarlanır:
        - 15m: 8 mum (2 saat KZ penceresi)
        - 1h:  4 mum
        - 4h:  2 mum
        - 1d:  sadece son 2 mum
        """
        if len(df) < 10:
            return None

        kill = self.detect_kill_zones()
        if not kill["is_kill_zone"]:
            return None

        # TF'ye göre mum sayısını ayarla (ilk hareket ve geri dönüş)
        opens = df["open"].values
        closes = df["close"].values

        # Toplam pencere: TF'ye göre adaptif
        total_candles = min(8, len(df) - 1)  # default 15m: 8 mum
        split = max(2, total_candles // 3)     # ilk %33 sahte hareket

        first_move = closes[-total_candles] - opens[-total_candles]
        # Son mumların toplam hareketi
        last_move = closes[-1] - closes[-(total_candles - split)]

        kz_name = kill["active_zone"].replace("_", " ").title()

        if first_move > 0 and last_move < 0 and abs(last_move) > abs(first_move) * 0.8:
            return {
                "type": "BEARISH_JUDAS",
                "desc": f"Judas Swing: {kz_name} acilisinda yukari sahte hareket, ardindan gercek yon asagi",
                "kill_zone": kill["active_zone"]
            }
        elif first_move < 0 and last_move > 0 and abs(last_move) > abs(first_move) * 0.8:
            return {
                "type": "BULLISH_JUDAS",
                "desc": f"Judas Swing: {kz_name} acilisinda asagi sahte hareket, ardindan gercek yon yukari",
                "kill_zone": kill["active_zone"]
            }
        return None

    # ================================================================
    # 14. DAILY BIAS  (HTF yon teyidi)
    # ================================================================

    def calc_daily_bias(self, instrument_key):
        """Gunluk TF'den trend yonu (HTF bias)"""
        df_daily = self.get_candles(instrument_key, "1d")
        if df_daily.empty or len(df_daily) < 20:
            return {"bias": "NEUTRAL", "desc": "Yetersiz gunluk veri"}

        ms = self.detect_market_structure(df_daily)
        pd_zone = self.calc_premium_discount(df_daily)

        bias = "NEUTRAL"
        desc = "Gunluk bias belirsiz"

        if ms["trend"] == "BULLISH":
            bias = "BULLISH"
            desc = "Gunluk trend yukselis - LONG oncelikli"
            if pd_zone["zone"] == "PREMIUM" and pd_zone["zone_pct"] > 70:
                desc += " (DIKKAT: Premium bolgede, geri cekilme riski)"
        elif ms["trend"] == "BEARISH":
            bias = "BEARISH"
            desc = "Gunluk trend dusus - SHORT oncelikli"
            if pd_zone["zone"] == "DISCOUNT" and pd_zone["zone_pct"] > 70:
                desc += " (DIKKAT: Discount bolgede, toparlanma riski)"

        if ms["choch"]:
            if ms["choch"]["type"] == "BULLISH_CHOCH":
                desc += " | CHoCH: Yukselise donus sinyali!"
            elif ms["choch"]["type"] == "BEARISH_CHOCH":
                desc += " | CHoCH: Dususe donus sinyali!"

        return {"bias": bias, "desc": desc, "trend": ms["trend"],
                "zone": pd_zone["zone"], "zone_pct": pd_zone["zone_pct"]}

    # ================================================================
    # 15. ASIAN RANGE + LONDON BREAKOUT
    # ================================================================

    def detect_asian_range_breakout(self, df):
        """Asian session (00:00-08:00 UTC) range + London kirilim tespiti"""
        if len(df) < 10:
            return None

        # Timestamp sütunu varsa gerçek UTC saatlerine göre filtrele
        has_ts = "timestamp" in df.columns

        if has_ts:
            try:
                ts = pd.to_datetime(df["timestamp"], utc=True)
                today = ts.iloc[-1].normalize()  # bugünün 00:00 UTC
                yesterday = today - pd.Timedelta(days=1)

                # Son Asian session: bugünün 00:00-08:00 UTC arası mumlar
                asian_mask = (ts >= today) & (ts.dt.hour < 8)
                if asian_mask.sum() < 2:
                    # Bugün yeterli mum yoksa dünün Asian'ını kullan
                    asian_mask = (ts >= yesterday) & (ts < yesterday + pd.Timedelta(hours=8))

                if asian_mask.sum() >= 2:
                    asian_df = df[asian_mask]
                    asian_high = float(asian_df["high"].max())
                    asian_low = float(asian_df["low"].min())
                else:
                    # Fallback: son 8 mum
                    n = min(8, len(df) - 2)
                    asian_high = float(df["high"].iloc[-n-8:-n].max()) if len(df) > n + 8 else float(df["high"].iloc[:8].max())
                    asian_low = float(df["low"].iloc[-n-8:-n].min()) if len(df) > n + 8 else float(df["low"].iloc[:8].min())
            except Exception:
                # Parse hatası olursa fallback
                n = min(20, len(df))
                mid = n // 2
                asian_high = float(df["high"].iloc[-n:-n+mid].max())
                asian_low = float(df["low"].iloc[-n:-n+mid].min())
        else:
            # Timestamp yoksa eski mantık
            if len(df) < 20:
                return None
            asian_high = float(df["high"].iloc[-20:-12].max())
            asian_low = float(df["low"].iloc[-20:-12].min())

        if asian_high <= asian_low:
            return None

        cur_price = float(df["close"].iloc[-1])
        asian_range_pips = asian_high - asian_low

        if cur_price > asian_high:
            breakout = {
                "type": "BULLISH_BREAKOUT",
                "asian_high": round(asian_high, 5),
                "asian_low": round(asian_low, 5),
                "desc": f"Asian Range (00:00-08:00 UTC) yukari kirildi. Range: {round(asian_low,5)} - {round(asian_high,5)}"
            }
        elif cur_price < asian_low:
            breakout = {
                "type": "BEARISH_BREAKOUT",
                "asian_high": round(asian_high, 5),
                "asian_low": round(asian_low, 5),
                "desc": f"Asian Range (00:00-08:00 UTC) asagi kirildi. Range: {round(asian_low,5)} - {round(asian_high,5)}"
            }
        else:
            breakout = {
                "type": "INSIDE_RANGE",
                "asian_high": round(asian_high, 5),
                "asian_low": round(asian_low, 5),
                "desc": f"Fiyat Asian Range icinde. Range: {round(asian_low,5)} - {round(asian_high,5)}"
            }

        return breakout

    # ================================================================
    # 16. SMART MONEY TRAP
    # ================================================================

    def detect_smart_money_trap(self, df, ms):
        """
        Retail trader'larin tuzaga dusuruldugu noktalar:
        - Breakout sonrasi hizli geri donus
        - Stop hunt + reversal
        """
        if len(df) < 10:
            return None

        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values

        # Son 5 mumda: bir yonde kirilim (wick) + ters kapnis
        for i in range(len(df) - 3, len(df)):
            if i < 2:
                continue
            wick_up = highs[i] - max(closes[i], df["open"].iloc[i])
            wick_down = min(closes[i], df["open"].iloc[i]) - lows[i]
            body = abs(closes[i] - df["open"].iloc[i])
            candle_range = highs[i] - lows[i]

            if candle_range == 0:
                continue

            # Yukari trap: uzun ust fitil + ayissi kapnis
            if wick_up > body * 2 and wick_up > candle_range * 0.6 and closes[i] < df["open"].iloc[i]:
                return {
                    "type": "BULL_TRAP",
                    "idx": i,
                    "desc": "Smart Money Trap: Yukari sahte kirilim + geri donus - SHORT sinyali",
                    "trap_level": float(highs[i])
                }
            # Asagi trap: uzun alt fitil + bogaci kapnis
            if wick_down > body * 2 and wick_down > candle_range * 0.6 and closes[i] > df["open"].iloc[i]:
                return {
                    "type": "BEAR_TRAP",
                    "idx": i,
                    "desc": "Smart Money Trap: Asagi sahte kirilim + geri donus - LONG sinyali",
                    "trap_level": float(lows[i])
                }
        return None

    # ================================================================
    #  TEKNIK GOSTERGELER (RSI, EMA, ATR)
    # ================================================================

    def calc_indicators(self, df):
        """Destekleyici teknik gostergeler"""
        close = df["close"]
        high = df["high"]
        low = df["low"]

        # RSI
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss_s = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1 / 14, min_periods=14).mean()
        avg_loss = loss_s.ewm(alpha=1 / 14, min_periods=14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))

        # EMA
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        ema200 = close.ewm(span=200, adjust=False).mean() if len(close) >= 200 else pd.Series([np.nan] * len(close))

        # ATR
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1 / 14, min_periods=14).mean()

        return {
            "rsi": float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else 50,
            "ema20": float(ema20.iloc[-1]),
            "ema50": float(ema50.iloc[-1]),
            "ema200": float(ema200.iloc[-1]) if not np.isnan(ema200.iloc[-1]) else None,
            "atr": float(atr.iloc[-1]) if not np.isnan(atr.iloc[-1]) else 0,
            "atr_pct": round(float(atr.iloc[-1]) / float(close.iloc[-1]) * 100, 3) if not np.isnan(atr.iloc[-1]) else 0,
        }

    # ================================================================
    #  ANA SINYAL URETICISI  (tam ICT uyumlu confluence)
    # ================================================================

    def generate_signal(self, instrument_key, timeframe="1h"):
        """Tum ICT bilesenleri birlestir -> LONG/SHORT/WAIT"""
        df = self.get_candles(instrument_key, timeframe)
        if df.empty or len(df) < 30:
            return {"error": "Yetersiz veri"}

        inst = FOREX_INSTRUMENTS[instrument_key]
        cur_price = float(df["close"].iloc[-1])

        # Tum ICT analiz
        ms = self.detect_market_structure(df)
        obs = self.detect_order_blocks(df, cur_price)
        breakers = self.detect_breaker_blocks(df)
        fvgs = self.detect_fvg(df)
        displacements = self.detect_displacement(df)
        sweeps = self.detect_liquidity_sweeps(df)
        inducements = self.detect_inducement(df, ms)
        ote = self.calc_ote(df, ms)
        pd_zone = self.calc_premium_discount(df)
        kill = self.detect_kill_zones()
        silver_bullet = self.detect_silver_bullet()
        amd = self.detect_amd_pattern(df)
        judas = self.detect_judas_swing(df)
        daily_bias = self.calc_daily_bias(instrument_key)
        asian_bo = self.detect_asian_range_breakout(df)
        smt = self.detect_smart_money_trap(df, ms)
        indicators = self.calc_indicators(df)

        # ── CONFLUENCE SKOR SISTEMI ──
        bull_score = 0
        bear_score = 0
        reasons_bull = []
        reasons_bear = []
        confluence_count = {"bull": 0, "bear": 0}

        # 1. Market Structure (30 puan)
        if ms["trend"] == "BULLISH":
            bull_score += 30
            confluence_count["bull"] += 1
            reasons_bull.append("Piyasa yapisi yukselis trendinde (HH + HL)")
        elif ms["trend"] == "BEARISH":
            bear_score += 30
            confluence_count["bear"] += 1
            reasons_bear.append("Piyasa yapisi dusus trendinde (LH + LL)")

        for bos in ms["bos"]:
            if bos["type"] == "BULLISH_BOS":
                bull_score += 10
                confluence_count["bull"] += 1
                reasons_bull.append(f"Bullish BOS @ {round(bos['level'], 5)}")
            elif bos["type"] == "BEARISH_BOS":
                bear_score += 10
                confluence_count["bear"] += 1
                reasons_bear.append(f"Bearish BOS @ {round(bos['level'], 5)}")

        if ms["choch"]:
            if ms["choch"]["type"] == "BULLISH_CHOCH":
                bull_score += 15
                confluence_count["bull"] += 1
                reasons_bull.append("Bullish CHoCH - trend donusu!")
            elif ms["choch"]["type"] == "BEARISH_CHOCH":
                bear_score += 15
                confluence_count["bear"] += 1
                reasons_bear.append("Bearish CHoCH - trend donusu!")

        # 2. Order Blocks (20 puan) - sadece mitigate olmamislar
        active_obs = [ob for ob in obs if not ob["mitigated"]]
        bull_ob = [ob for ob in active_obs
                   if ob["type"] == "BULLISH_OB" and ob["low"] <= cur_price <= ob["high"]]
        bear_ob = [ob for ob in active_obs
                   if ob["type"] == "BEARISH_OB" and ob["low"] <= cur_price <= ob["high"]]

        if bull_ob:
            bull_score += 20
            confluence_count["bull"] += 1
            reasons_bull.append(f"Aktif Bullish OB icinde (guc: {bull_ob[0]['strength']}x)")
        if bear_ob:
            bear_score += 20
            confluence_count["bear"] += 1
            reasons_bear.append(f"Aktif Bearish OB icinde (guc: {bear_ob[0]['strength']}x)")

        # 3. Breaker Blocks (10 puan - tip başına tek seferlik)
        has_bull_breaker = any(bb["type"] == "BULLISH_BREAKER" for bb in breakers)
        has_bear_breaker = any(bb["type"] == "BEARISH_BREAKER" for bb in breakers)
        if has_bull_breaker:
            bull_score += 10
            confluence_count["bull"] += 1
            reasons_bull.append(f"Bullish Breaker Block destegi ({sum(1 for bb in breakers if bb['type']=='BULLISH_BREAKER')} adet)")
        if has_bear_breaker:
            bear_score += 10
            confluence_count["bear"] += 1
            reasons_bear.append(f"Bearish Breaker Block direnci ({sum(1 for bb in breakers if bb['type']=='BEARISH_BREAKER')} adet)")

        # 4. FVG (15 puan) - doldurulmamis, CE test edilmis olanlar daha guclu
        active_fvgs = [f for f in fvgs if not f["filled"] and f["idx"] >= len(df) - 15]
        bull_fvg = [f for f in active_fvgs if f["type"] == "BULLISH_FVG"]
        bear_fvg = [f for f in active_fvgs if f["type"] == "BEARISH_FVG"]
        ce_bull = [f for f in bull_fvg if f["ce_tested"]]
        ce_bear = [f for f in bear_fvg if f["ce_tested"]]

        if bull_fvg:
            pts = 15 if ce_bull else 10
            bull_score += pts
            confluence_count["bull"] += 1
            ce_txt = " (CE test edildi!)" if ce_bull else ""
            reasons_bull.append(f"{len(bull_fvg)} Bullish FVG{ce_txt}")
        if bear_fvg:
            pts = 15 if ce_bear else 10
            bear_score += pts
            confluence_count["bear"] += 1
            ce_txt = " (CE test edildi!)" if ce_bear else ""
            reasons_bear.append(f"{len(bear_fvg)} Bearish FVG{ce_txt}")

        # 5. Displacement (10 puan)
        recent_disp = [d for d in displacements if d["idx"] >= len(df) - 5]
        for d in recent_disp:
            if "BULLISH" in d["type"]:
                bull_score += 10
                confluence_count["bull"] += 1
                reasons_bull.append(f"Bullish Displacement ({d['body_mult']}x)")
            else:
                bear_score += 10
                confluence_count["bear"] += 1
                reasons_bear.append(f"Bearish Displacement ({d['body_mult']}x)")

        # 6. Liquidity Sweeps (15 puan)
        recent_sweeps = [s for s in sweeps if s["idx"] >= len(df) - 5]
        for sw in recent_sweeps:
            if sw["type"] == "BUY_SIDE_SWEEP":
                bull_score += 15
                confluence_count["bull"] += 1
                reasons_bull.append("Buy-side likidite avi - donus beklentisi")
            elif sw["type"] == "SELL_SIDE_SWEEP":
                bear_score += 15
                confluence_count["bear"] += 1
                reasons_bear.append("Sell-side likidite avi - dusus beklentisi")

        # 7. Inducement (5 puan - tip başına tek seferlik)
        bull_ind_count = sum(1 for ind in inducements if ind["type"] == "BULLISH_INDUCEMENT")
        bear_ind_count = sum(1 for ind in inducements if ind["type"] == "BEARISH_INDUCEMENT")
        if bull_ind_count > 0:
            bull_score += 5
            reasons_bull.append(f"Bullish Inducement - likidite toplandi ({bull_ind_count} adet)")
        if bear_ind_count > 0:
            bear_score += 5
            reasons_bear.append(f"Bearish Inducement - likidite toplandi ({bear_ind_count} adet)")

        # 8. OTE (10 puan)
        if ote:
            if ote["direction"] == "LONG" and ote["ote_bottom"] <= cur_price <= ote["ote_top"]:
                bull_score += 10
                confluence_count["bull"] += 1
                reasons_bull.append("Fiyat OTE alim bolgesinde (Fib 0.618-0.786)")
            elif ote["direction"] == "SHORT" and ote["ote_bottom"] <= cur_price <= ote["ote_top"]:
                bear_score += 10
                confluence_count["bear"] += 1
                reasons_bear.append("Fiyat OTE satis bolgesinde (Fib 0.618-0.786)")

        # 9. Premium/Discount (10 puan)
        if pd_zone["zone"] == "DISCOUNT" and pd_zone["zone_pct"] > 60:
            bull_score += 10
            confluence_count["bull"] += 1
            reasons_bull.append(f"Discount bolgesi (%{pd_zone['zone_pct']})")
        elif pd_zone["zone"] == "PREMIUM" and pd_zone["zone_pct"] > 60:
            bear_score += 10
            confluence_count["bear"] += 1
            reasons_bear.append(f"Premium bolgesi (%{pd_zone['zone_pct']})")

        # 10. Kill Zone bonus (5 puan)
        if kill["is_kill_zone"]:
            if bull_score > bear_score:
                bull_score += 5
                reasons_bull.append(f"{kill['active_zone']} Kill Zone aktif")
            elif bear_score > bull_score:
                bear_score += 5
                reasons_bear.append(f"{kill['active_zone']} Kill Zone aktif")

        # 11. Silver Bullet (5 puan)
        if silver_bullet["is_active"]:
            if bull_fvg or bear_fvg:
                if bull_score > bear_score:
                    bull_score += 5
                    reasons_bull.append(f"Silver Bullet + FVG confluence")
                else:
                    bear_score += 5
                    reasons_bear.append(f"Silver Bullet + FVG confluence")

        # 12. AMD Pattern (10 puan)
        if amd:
            if amd["direction"] == "LONG":
                bull_score += 10
                confluence_count["bull"] += 1
                reasons_bull.append(f"AMD Bullish pattern (Manipulation -> Distribution)")
            elif amd["direction"] == "SHORT":
                bear_score += 10
                confluence_count["bear"] += 1
                reasons_bear.append(f"AMD Bearish pattern (Manipulation -> Distribution)")

        # 13. Judas Swing (10 puan)
        if judas:
            if judas["type"] == "BULLISH_JUDAS":
                bull_score += 10
                confluence_count["bull"] += 1
                reasons_bull.append(f"Judas Swing: sahte dusus -> gercek yukselis")
            elif judas["type"] == "BEARISH_JUDAS":
                bear_score += 10
                confluence_count["bear"] += 1
                reasons_bear.append(f"Judas Swing: sahte yukselis -> gercek dusus")

        # 14. Daily Bias (15 puan)
        if daily_bias["bias"] == "BULLISH":
            bull_score += 15
            confluence_count["bull"] += 1
            reasons_bull.append(f"Gunluk Bias: YUKSELIS ({daily_bias['desc']})")
        elif daily_bias["bias"] == "BEARISH":
            bear_score += 15
            confluence_count["bear"] += 1
            reasons_bear.append(f"Gunluk Bias: DUSUS ({daily_bias['desc']})")

        # 15. Asian Range Breakout (5 puan)
        if asian_bo and asian_bo["type"] != "INSIDE_RANGE":
            if asian_bo["type"] == "BULLISH_BREAKOUT":
                bull_score += 5
                reasons_bull.append(f"Asian Range yukari kirildi")
            elif asian_bo["type"] == "BEARISH_BREAKOUT":
                bear_score += 5
                reasons_bear.append(f"Asian Range asagi kirildi")

        # 16. Smart Money Trap (15 puan)
        if smt:
            if smt["type"] == "BEAR_TRAP":
                bull_score += 15
                confluence_count["bull"] += 1
                reasons_bull.append(f"Smart Money Trap: LONG ({smt['desc']})")
            elif smt["type"] == "BULL_TRAP":
                bear_score += 15
                confluence_count["bear"] += 1
                reasons_bear.append(f"Smart Money Trap: SHORT ({smt['desc']})")

        # RSI konfirmasyon (5 puan)
        if indicators["rsi"] < 35:
            bull_score += 5
            reasons_bull.append(f"RSI asiri satim ({indicators['rsi']:.1f})")
        elif indicators["rsi"] > 65:
            bear_score += 5
            reasons_bear.append(f"RSI asiri alim ({indicators['rsi']:.1f})")

        # ── SINYAL KARARI ──
        net_score = bull_score - bear_score
        max_conf = max(confluence_count["bull"], confluence_count["bear"])

        # Confluence sayisi + skor birlikte degerlendirilir
        if net_score >= 50 and confluence_count["bull"] >= 4:
            signal = "STRONG_LONG"
            label = "GUCLU ALIS"
            desc = "ICT tam confluence: Market Structure + OB + FVG + Displacement uyumlu"
        elif net_score >= 25 and confluence_count["bull"] >= 3:
            signal = "LONG"
            label = "ALIS"
            desc = "ICT gostergeleri alis yonunu destekliyor"
        elif net_score <= -50 and confluence_count["bear"] >= 4:
            signal = "STRONG_SHORT"
            label = "GUCLU SATIS"
            desc = "ICT tam confluence: Market Structure + OB + FVG + Displacement uyumlu"
        elif net_score <= -25 and confluence_count["bear"] >= 3:
            signal = "SHORT"
            label = "SATIS"
            desc = "ICT gostergeleri satis yonunu destekliyor"
        else:
            signal = "WAIT"
            label = "BEKLE"
            desc = "Net bir ICT confluence yok. Daha iyi setup icin bekleyin."

        # Kill zone notu
        if kill["is_kill_zone"]:
            desc += f" | {kill['active_zone']} Kill Zone aktif."
        if silver_bullet["is_active"]:
            desc += f" | {silver_bullet['active']['name']} penceresi acik."

        # SL / TP hesapla (ATR + Swing/OB bazli)
        atr = indicators["atr"]
        sl_tp = None

        # Swing high/low bul (son 20 mum)
        swing_lookback = min(20, len(df) - 1)
        recent_highs = df["high"].iloc[-swing_lookback:].values
        recent_lows = df["low"].iloc[-swing_lookback:].values
        recent_swing_high = float(np.max(recent_highs))
        recent_swing_low = float(np.min(recent_lows))

        # OB sinirlari (varsa)
        ob_top = None
        ob_bottom = None
        if obs:
            active_obs = [o for o in obs if not o.get("mitigated", False)]
            if active_obs:
                last_ob = active_obs[-1]
                ob_top = last_ob.get("top")
                ob_bottom = last_ob.get("bottom")

        if signal in ("STRONG_LONG", "LONG"):
            # LONG SL: en yakın swing low veya OB alt sınırının altı
            atr_sl = cur_price - atr * 1.5
            swing_sl = recent_swing_low - atr * 0.2  # swing low'un biraz altı
            candidates = [atr_sl, swing_sl]
            if ob_bottom:
                candidates.append(ob_bottom - atr * 0.1)
            sl = round(max(candidates), 5)  # en yakın (en yüksek) SL
            # SL çok yakınsa ATR bazlıyı kullan
            if abs(cur_price - sl) < atr * 0.5:
                sl = round(atr_sl, 5)

            risk = abs(cur_price - sl)
            tp1 = round(cur_price + risk * 1.5, 5)   # R:R 1:1.5
            tp2 = round(cur_price + risk * 2.5, 5)   # R:R 1:2.5
            rr1 = round(abs(tp1 - cur_price) / risk, 2) if risk > 0 else 0
            rr2 = round(abs(tp2 - cur_price) / risk, 2) if risk > 0 else 0
            sl_tp = {"sl": sl, "tp1": tp1, "tp2": tp2, "direction": "LONG",
                     "rr1": rr1, "rr2": rr2, "method": "swing+ATR"}

        elif signal in ("STRONG_SHORT", "SHORT"):
            # SHORT SL: en yakın swing high veya OB üst sınırının üstü
            atr_sl = cur_price + atr * 1.5
            swing_sl = recent_swing_high + atr * 0.2  # swing high'un biraz üstü
            candidates = [atr_sl, swing_sl]
            if ob_top:
                candidates.append(ob_top + atr * 0.1)
            sl = round(min(candidates), 5)  # en yakın (en düşük) SL
            if abs(sl - cur_price) < atr * 0.5:
                sl = round(atr_sl, 5)

            risk = abs(sl - cur_price)
            tp1 = round(cur_price - risk * 1.5, 5)
            tp2 = round(cur_price - risk * 2.5, 5)
            rr1 = round(abs(cur_price - tp1) / risk, 2) if risk > 0 else 0
            rr2 = round(abs(cur_price - tp2) / risk, 2) if risk > 0 else 0
            sl_tp = {"sl": sl, "tp1": tp1, "tp2": tp2, "direction": "SHORT",
                     "rr1": rr1, "rr2": rr2, "method": "swing+ATR"}

        # ── ICT TEKNIK YORUM METNI ──
        commentary = self._generate_commentary(
            inst, cur_price, timeframe, signal, label, desc,
            net_score, bull_score, bear_score,
            confluence_count, reasons_bull, reasons_bear,
            ms, obs, breakers, fvgs, active_fvgs, bull_fvg, bear_fvg, ce_bull, ce_bear,
            displacements, sweeps, inducements, ote, pd_zone,
            kill, silver_bullet, amd, judas, daily_bias, asian_bo, smt,
            indicators, sl_tp
        )

        return {
            "instrument": instrument_key,
            "name": inst["name"],
            "category": inst["category"],
            "icon": inst["icon"],
            "desc": inst["desc"],
            "price": cur_price,
            "timeframe": timeframe,
            "signal": signal,
            "label": label,
            "description": desc,
            "commentary": commentary,
            "net_score": net_score,
            "bull_score": bull_score,
            "bear_score": bear_score,
            "confluence_bull": confluence_count["bull"],
            "confluence_bear": confluence_count["bear"],
            "reasons_bull": reasons_bull,
            "reasons_bear": reasons_bear,
            "sl_tp": sl_tp,
            # ICT detaylari
            "market_structure": ms,
            "order_blocks": [ob for ob in obs if not ob["mitigated"]][:5],
            "breaker_blocks": breakers,
            "fvg": {
                "bull": len(bull_fvg), "bear": len(bear_fvg),
                "ce_bull": len(ce_bull), "ce_bear": len(ce_bear),
                "active": active_fvgs[:6],
            },
            "displacement": displacements,
            "liquidity_sweeps": sweeps,
            "inducement": inducements,
            "ote": ote,
            "premium_discount": pd_zone,
            "kill_zones": kill,
            "silver_bullet": silver_bullet,
            "amd": amd,
            "judas": judas,
            "daily_bias": daily_bias,
            "asian_breakout": asian_bo,
            "smart_money_trap": smt,
            "indicators": indicators,
            "timestamp": datetime.now().isoformat(),
        }

    # ================================================================
    #  ICT TEKNIK YORUM MOTORU
    # ================================================================

    def _generate_commentary(self, inst, price, tf, signal, label, desc,
                             net_score, bull_score, bear_score,
                             conf_count, reasons_bull, reasons_bear,
                             ms, obs, breakers, fvgs, active_fvgs,
                             bull_fvg, bear_fvg, ce_bull, ce_bear,
                             displacements, sweeps, inducements, ote, pd_zone,
                             kill, silver_bullet, amd, judas, daily_bias,
                             asian_bo, smt, indicators, sl_tp):
        """
        Tum ICT verilerini yorumlayarak detayli Turkce teknik analiz metni uretir.
        Her parametre icin ne anlama geldigini, fiyatin nereye gidebilecegini,
        hangi seviyelerde ne beklendigini aciklar.
        """
        name = inst["name"]
        cat = inst["category"]
        fp = lambda v: f"{v:.5f}" if v < 10 else (f"{v:.4f}" if v < 100 else f"{v:.2f}")
        paragraphs = []

        # ── 1. GENEL BAKIS ──
        trend_tr = {"BULLISH": "yukselis", "BEARISH": "dusus", "NEUTRAL": "notr/kararsiz"}
        p1 = f"{name} su an {fp(price)} seviyesinde islem goruyor ({tf} zaman dilimi). "
        p1 += f"ICT analizi {conf_count['bull']} boga ve {conf_count['bear']} ayi uyum noktasi tespit etti. "
        p1 += f"Genel skor: Boga {bull_score} / Ayi {bear_score} (net: {'+' if net_score > 0 else ''}{net_score}). "
        if signal != "WAIT":
            p1 += f"Sonuc: {label} sinyali."
        else:
            p1 += "Henuz net bir sinyal olusmuyor, bekleme konumunda."
        paragraphs.append(("Genel Bakis", p1))

        # ── 2. PIYASA YAPISI ──
        p2 = f"Piyasa yapisi: {trend_tr.get(ms['trend'], 'belirsiz')} trendde. "
        if ms["trend"] == "BULLISH":
            p2 += "Fiyat daha yuksek zirveler (HH) ve daha yuksek dipler (HL) olusturuyor — trend saglam gorunuyor. "
        elif ms["trend"] == "BEARISH":
            p2 += "Fiyat daha dusuk zirveler (LH) ve daha dusuk dipler (LL) yapiyor — satis baskisi hakim. "
        else:
            p2 += "Belirgin bir HH/HL veya LH/LL serisi yok — piyasa yatay veya gecis doneminde. "

        if ms["bos"]:
            last_bos = ms["bos"][-1]
            bos_dir = "yukari" if "BULL" in last_bos["type"] else "asagi"
            p2 += f"Son BOS (Break of Structure) {bos_dir} yonde {fp(last_bos['level'])} seviyesinde gerceklesti. "
            if "BULL" in last_bos["type"]:
                p2 += "Bu, boga tarafinin kontrolu ele aldigini gosteriyor. "
            else:
                p2 += "Bu, ayilarin yapi kirilimini saglad igini gosteriyor. "

        if ms["choch"]:
            choch_dir = "yukari" if "BULL" in ms["choch"]["type"] else "asagi"
            p2 += f"ONEMLI: CHoCH (Character of Change) tespit edildi — trend {choch_dir} donusu sinyali! "
            p2 += "Bu, piyasanin yon degistirdiginin guclu bir isaretcisidir. "
        paragraphs.append(("Piyasa Yapisi", p2))

        # ── 3. EMIR BLOKLARI (OB) ──
        active_obs_list = [ob for ob in obs if not ob.get("mitigated", False)]
        if active_obs_list:
            p3 = f"{len(active_obs_list)} aktif Emir Blogu (OB) tespit edildi. "
            bull_obs = [ob for ob in active_obs_list if ob["type"] == "BULLISH_OB"]
            bear_obs = [ob for ob in active_obs_list if ob["type"] == "BEARISH_OB"]

            if bull_obs:
                nearest_bull_ob = bull_obs[-1]
                p3 += f"Bullish OB: {fp(nearest_bull_ob['low'])} - {fp(nearest_bull_ob['high'])} bolgesi (guc: {nearest_bull_ob['strength']}x). "
                if nearest_bull_ob["low"] <= price <= nearest_bull_ob["high"]:
                    p3 += "Fiyat su an BU BLOGUN ICINDE — alis tepkisi beklenebilir. "
                elif price > nearest_bull_ob["high"]:
                    dist_pct = abs(price - nearest_bull_ob["high"]) / price * 100
                    p3 += f"Fiyat blogun {dist_pct:.2f}% uzerinde; geri cekilirse bu bolge destek olabilir. "
                else:
                    p3 += f"Fiyat blogun altinda — bloga ulasirsa guclu alis tepkisi beklenir. "

            if bear_obs:
                nearest_bear_ob = bear_obs[-1]
                p3 += f"Bearish OB: {fp(nearest_bear_ob['low'])} - {fp(nearest_bear_ob['high'])} bolgesi (guc: {nearest_bear_ob['strength']}x). "
                if nearest_bear_ob["low"] <= price <= nearest_bear_ob["high"]:
                    p3 += "Fiyat su an BU BLOGUN ICINDE — satis baskisi beklenebilir. "
                elif price < nearest_bear_ob["low"]:
                    dist_pct = abs(nearest_bear_ob["low"] - price) / price * 100
                    p3 += f"Fiyat blogun {dist_pct:.2f}% altinda; yukselirse bu bolge direnc olabilir. "
        else:
            p3 = "Aktif Emir Blogu (OB) tespit edilemedi. "
            p3 += "Bu, piyasanin guclu bir kurumsal emirsiz bolgede oldugunu gosterebilir. "
        paragraphs.append(("Emir Bloklari (OB)", p3))

        # ── 4. FVG (Fair Value Gaps) ──
        if active_fvgs:
            p4 = f"{len(bull_fvg)} yukselis ve {len(bear_fvg)} dusus FVG tespit edildi. "
            if ce_bull:
                p4 += f"{len(ce_bull)} Bullish FVG'nin CE seviyesi (orta noktasi) test edildi — bu FVG'ler daha guclu tepki verebilir. "
            if ce_bear:
                p4 += f"{len(ce_bear)} Bearish FVG'nin CE seviyesi test edildi. "

            # En yakin FVG'yi bul
            nearest_fvg = None
            min_dist = float('inf')
            for fvg in active_fvgs[:6]:
                mid = (fvg["top"] + fvg["bottom"]) / 2
                dist = abs(price - mid)
                if dist < min_dist:
                    min_dist = dist
                    nearest_fvg = fvg

            if nearest_fvg:
                fvg_dir = "Yukaris" if "BULL" in nearest_fvg["type"] else "Asagi"
                ce = nearest_fvg["ce_level"]
                p4 += f"En yakin FVG: {fvg_dir} — {fp(nearest_fvg['bottom'])} ile {fp(nearest_fvg['top'])} arasi. "
                p4 += f"CE (Consequent Encroachment) seviyesi: {fp(ce)}. "
                if nearest_fvg["bottom"] <= price <= nearest_fvg["top"]:
                    p4 += "Fiyat su an bu FVG icinde — boslugun doldurulmasi devam ediyor. "
                elif "BULL" in nearest_fvg["type"] and price > nearest_fvg["top"]:
                    p4 += f"Fiyat FVG'nin uzerinde; geri cekilirse {fp(nearest_fvg['top'])} - {fp(ce)} arasi destek bolgesi olabilir. "
                elif "BEAR" in nearest_fvg["type"] and price < nearest_fvg["bottom"]:
                    p4 += f"Fiyat FVG'nin altinda; yukselirse {fp(nearest_fvg['bottom'])} - {fp(ce)} arasi direnc bolgesi olabilir. "
        else:
            p4 = "Aktif FVG (fiyat boslugu) tespit edilemedi. Piyasa dengeli fiyatlamayla hareket ediyor. "
        paragraphs.append(("Adil Deger Bosluklari (FVG)", p4))

        # ── 5. LIKIDITE SUPURME ──
        if sweeps:
            p5 = f"Son {len(sweeps)} likidite supurme hareketi tespit edildi. "
            last_sweep = sweeps[-1]
            if last_sweep["type"] == "SELL_SIDE_SWEEP":
                p5 += f"Son hareket: Ust likidite supuruldu (EQH) — {fp(last_sweep['sweep_price'])} seviyesine kadar cikip geri dondu. "
                p5 += f"Esit zirve seviyesi {fp(last_sweep['level'])} idi. "
                p5 += "Bu, kurumsallarin yukaridaki stop-loss'lari supurdugunu ve dusus yonu icin pozisyon aldigini gosterebilir. "
                p5 += f"Fiyat {fp(last_sweep['level'])} altinda kalirsa dusus devam edebilir. "
            else:
                p5 += f"Son hareket: Alt likidite supuruldu (EQL) — {fp(last_sweep['sweep_price'])} seviyesine kadar inip geri dondu. "
                p5 += f"Esit dip seviyesi {fp(last_sweep['level'])} idi. "
                p5 += "Bu, kurumsallarin asagidaki stop-loss'lari supurdugunu ve yukselis yonu icin pozisyon aldigini gosterebilir. "
                p5 += f"Fiyat {fp(last_sweep['level'])} uzerinde kalirsa yukselis devam edebilir. "
        else:
            p5 = "Yakin zamanda belirgin bir likidite supurme hareketi tespit edilemedi. "
        paragraphs.append(("Likidite Analizi", p5))

        # ── 6. DISPLACEMENT (Momentum) ──
        if displacements:
            p6 = f"{len(displacements)} displacement (guclu momentum) mumu tespit edildi. "
            last_disp = displacements[-1]
            disp_dir = "yukari" if "BULL" in last_disp["type"] else "asagi"
            p6 += f"Son displacement {disp_dir} yonde, ortalama govdenin {last_disp['body_mult']}x buyuklugunde. "
            p6 += f"Govde/fitil orani: %{int(last_disp['body_ratio'] * 100)} — "
            if last_disp["body_ratio"] > 0.85:
                p6 += "cok guclu momentum, kurumsal taraf agresif pozisyon aliyor. "
            elif last_disp["body_ratio"] > 0.7:
                p6 += "belirgin momentum, trend yonunde islem mantiksiz degil. "
            else:
                p6 += "orta seviye momentum. "
        else:
            p6 = "Belirgin bir displacement (momentum patlamasi) mumu tespit edilemedi. Piyasa sakin hareket ediyor. "
        paragraphs.append(("Momentum / Displacement", p6))

        # ── 7. PREMIUM / DISCOUNT & OTE ──
        p7 = f"Fiyat su an {pd_zone['zone']} bolgesinde (%{pd_zone['zone_pct']}). "
        p7 += f"Swing araligi: {fp(pd_zone['range_low'])} - {fp(pd_zone['range_high'])} | Denge (Equilibrium): {fp(pd_zone['equilibrium'])}. "
        if pd_zone["zone"] == "DISCOUNT":
            p7 += "INDIRIM bolgesinde — alis firsatlari icin ideal. Fiyat gercek degerinin altinda islem goruyor. "
        elif pd_zone["zone"] == "PREMIUM":
            p7 += "PREMIUM bolgesinde — satis firsatlari icin ideal. Fiyat gercek degerinin uzerinde islem goruyor. "
        else:
            p7 += "Denge bolgesinde — ne premium ne indirim, net yonlendirme zayif. "

        if ote:
            ote_dir = "ALIS" if ote["direction"] == "LONG" else "SATIS"
            p7 += f"OTE (Optimal Trade Entry): {ote_dir} yonu icin {fp(ote['ote_bottom'])} - {fp(ote['ote_top'])} arasi ideal giris bolgesi (Fib 0.618-0.786). "
            if ote["ote_bottom"] <= price <= ote["ote_top"]:
                p7 += "DIKKAT: Fiyat su an OTE bolgesinde — giris icin cok uygun bir konum! "
        paragraphs.append(("Premium/Indirim & OTE", p7))

        # ── 8. GUNLUK YON & OZEL PATERNLER ──
        p8 = ""
        if daily_bias and daily_bias.get("bias") != "NEUTRAL":
            bias_tr = "yukselis" if daily_bias["bias"] == "BULLISH" else "dusus"
            p8 += f"Gunluk bias: {bias_tr.upper()} — {daily_bias.get('desc', '')}. "
            p8 += f"Ust zaman dilimi (HTF) fiyatin {bias_tr} yonunde ilerleyecegini destekliyor. "
        else:
            p8 += "Gunluk bias notr — HTF'den net bir yonlendirme yok. "

        if amd:
            amd_dir = "yukari" if amd.get("direction") == "LONG" else "asagi"
            p8 += f"AMD (Accumulation-Manipulation-Distribution) paterni tespit edildi — {amd_dir} yonde dagitim bekleniyor. "
            p8 += "Piyasa birikim ve manipulasyon asamalarini tamamlamis, gercek hareket baslamis olabilir. "

        if judas:
            judas_dir = "dusus" if "BEAR" in judas["type"] else "yukselis"
            p8 += f"Judas Swing: {judas['desc']}. "
            p8 += f"Kill Zone acilisindaki ilk hareket sahte idi — gercek yon {judas_dir}. "

        if asian_bo:
            if asian_bo["type"] == "BULLISH_BREAKOUT":
                p8 += f"Asian Range ({fp(asian_bo['asian_low'])} - {fp(asian_bo['asian_high'])}) yukari kirildi. London seansinda yukselis devam edebilir. "
            elif asian_bo["type"] == "BEARISH_BREAKOUT":
                p8 += f"Asian Range ({fp(asian_bo['asian_low'])} - {fp(asian_bo['asian_high'])}) asagi kirildi. London seansinda dusus devam edebilir. "
            else:
                p8 += f"Fiyat henuz Asian Range ({fp(asian_bo['asian_low'])} - {fp(asian_bo['asian_high'])}) icinde — kirilim bekleniyor. "

        if smt:
            smt_dir = "yukselis" if smt["type"] == "BEAR_TRAP" else "dusus"
            p8 += f"SMART MONEY TRAP tespit edildi: {smt['desc']}. Kurumsallar {smt_dir} yonunde tuzak kurmus olabilir. "

        if not p8:
            p8 = "Gunluk yon veya ozel ICT paterni tespit edilemedi. "
        paragraphs.append(("Gunluk Yon & Ozel Paternler", p8))

        # ── 9. SEANS & ZAMANLAMA ──
        p9 = ""
        if kill["is_kill_zone"]:
            p9 += f"{kill['desc']}. "
            p9 += "Kill Zone icinde olmak, islem acisindan yuksek onem tasiyor — volatilite ve likidite bu donemde zirvede. "
        else:
            p9 += f"{kill['desc']}. "
            p9 += "Kill Zone disinda islem riski artar, yanlis sinyaller olusabilir. Ideal islem zamanini beklemek mantikli olabilir. "

        if silver_bullet["is_active"]:
            p9 += f"{silver_bullet['desc']}. Silver Bullet penceresinde FVG bazli girisler cok guclu olabilir. "

        paragraphs.append(("Seans & Zamanlama", p9))

        # ── 10. TEKNIK GOSTERGELER ──
        p10 = f"RSI(14): {indicators['rsi']:.1f} — "
        if indicators["rsi"] > 70:
            p10 += "asiri alim bolgesinde, geri cekilme riski yuksek. "
        elif indicators["rsi"] > 60:
            p10 += "alim bolgesine yaklesiyor, dikkatli olunmali. "
        elif indicators["rsi"] < 30:
            p10 += "asiri satim bolgesinde, toparlanma beklentisi artabilir. "
        elif indicators["rsi"] < 40:
            p10 += "satim bolgesine yaklesiyor. "
        else:
            p10 += "notr bolgede. "

        p10 += f"ATR(14): {fp(indicators['atr'])} (volatilite %{indicators['atr_pct']}). "
        if float(indicators["atr_pct"]) > 1.5:
            p10 += "Volatilite cok yuksek — genis stop loss kullanmak gerekebilir. "
        elif float(indicators["atr_pct"]) > 0.8:
            p10 += "Normal volatilite — standart risk yonetimi uygulanabilir. "
        else:
            p10 += "Dusuk volatilite — dar range'de islem, kirilim beklenebilir. "

        ema_list = []
        if indicators.get("ema20"): ema_list.append(("EMA20", indicators["ema20"]))
        if indicators.get("ema50"): ema_list.append(("EMA50", indicators["ema50"]))
        if indicators.get("ema200"): ema_list.append(("EMA200", indicators["ema200"]))
        if ema_list:
            above = [n for n, v in ema_list if price > v]
            below = [n for n, v in ema_list if price <= v]
            if above:
                p10 += f"Fiyat {', '.join(above)} uzerinde. "
            if below:
                p10 += f"Fiyat {', '.join(below)} altinda. "
            if len(above) == len(ema_list):
                p10 += "Tum EMA'lar altinda — guclu boga kontrolu. "
            elif len(below) == len(ema_list):
                p10 += "Tum EMA'larin altinda — ayi hakim. "
        paragraphs.append(("Teknik Gostergeler", p10))

        # ── 11. SONUC & ONERI ──
        p11 = ""
        if signal in ("STRONG_LONG", "LONG"):
            p11 += f"SONUC: {label} — {name} icin yukselis beklentisi hakim. "
            p11 += f"{conf_count['bull']} ICT konsepti alis yonunu destekliyor. "
            if sl_tp:
                risk_pct = abs(price - sl_tp["sl"]) / price * 100
                p11 += f"Onerilen giris: {fp(price)}, Stop Loss: {fp(sl_tp['sl'])} (risk %{risk_pct:.2f}), "
                p11 += f"TP1: {fp(sl_tp['tp1'])} (R:R 1:{sl_tp['rr1']}), TP2: {fp(sl_tp['tp2'])} (R:R 1:{sl_tp['rr2']}). "
            if pd_zone["zone"] == "DISCOUNT":
                p11 += "Fiyatin indirim bolgesinde olmasi alis tezini destekliyor. "
            if ote and ote["direction"] == "LONG":
                p11 += "OTE bolgesinde olunmasi giris kalitesini artiriyor. "

        elif signal in ("STRONG_SHORT", "SHORT"):
            p11 += f"SONUC: {label} — {name} icin dusus beklentisi hakim. "
            p11 += f"{conf_count['bear']} ICT konsepti satis yonunu destekliyor. "
            if sl_tp:
                risk_pct = abs(sl_tp["sl"] - price) / price * 100
                p11 += f"Onerilen giris: {fp(price)}, Stop Loss: {fp(sl_tp['sl'])} (risk %{risk_pct:.2f}), "
                p11 += f"TP1: {fp(sl_tp['tp1'])} (R:R 1:{sl_tp['rr1']}), TP2: {fp(sl_tp['tp2'])} (R:R 1:{sl_tp['rr2']}). "
            if pd_zone["zone"] == "PREMIUM":
                p11 += "Fiyatin premium bolgesinde olmasi satis tezini destekliyor. "
            if ote and ote["direction"] == "SHORT":
                p11 += "OTE bolgesinde olunmasi giris kalitesini artiriyor. "

        else:
            p11 += f"SONUC: BEKLE — {name} icin net bir ICT confluence olusmuyor. "
            p11 += f"Boga: {conf_count['bull']} uyum, Ayi: {conf_count['bear']} uyum — karar icin yetersiz. "
            p11 += "Kill Zone icinde guclu bir BOS, CHoCH veya displacement olusana kadar beklenmesi oneriliyor. "

            # Beklenen senaryo
            if bull_score > bear_score:
                p11 += f"Hafif boga egilimi var (skor: +{net_score}). "
                if active_fvgs:
                    for fvg in active_fvgs[:2]:
                        if "BULL" in fvg["type"]:
                            p11 += f"Fiyat {fp(fvg['ce_level'])} CE seviyesine cekilirse alis firsati olusabilir. "
                            break
            elif bear_score > bull_score:
                p11 += f"Hafif ayi egilimi var (skor: {net_score}). "
                if active_fvgs:
                    for fvg in active_fvgs[:2]:
                        if "BEAR" in fvg["type"]:
                            p11 += f"Fiyat {fp(fvg['ce_level'])} CE seviyesine yukselirse satis firsati olusabilir. "
                            break

        if kill["is_kill_zone"]:
            p11 += "Kill Zone aktif — islem zamani uygun. "
        else:
            p11 += f"Kill Zone disinda — bir sonraki pencere icin beklenebilir ({kill.get('next_kz', '')}). "

        paragraphs.append(("Sonuc & Oneri", p11))

        return {
            "sections": [{"title": t, "text": txt} for t, txt in paragraphs],
            "summary": p11.strip(),
        }

    def scan_all(self, timeframe="1h"):
        """Tum enstrumanlari tara"""
        results = []
        for key in FOREX_INSTRUMENTS:
            try:
                sig = self.generate_signal(key, timeframe)
                if "error" not in sig:
                    results.append(sig)
            except Exception as e:
                logger.error(f"Forex tarama hatasi ({key}): {e}")
        return results


# Singleton
forex_ict = ForexICTEngine()
