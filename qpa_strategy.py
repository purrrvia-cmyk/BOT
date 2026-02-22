# =====================================================
# QPA (Quantitative Price Action) Strategy Engine v1.0
# =====================================================
# Tamamen bağımsız sinyal üretim motoru.
# Lagging gösterge KULLANMAZ (RSI, EMA, MACD yok).
# Saf matematiksel fiyat + hacim analizi.
#
# 6 Ana Bileşen:
#   1. Volatilite Rejimi (sıkışma → patlama tespiti)
#   2. Price Action Kalıpları (engulfing, pin bar, inside bar)
#   3. Hacim Profili (POC, hacim dengesizliği)
#   4. Momentum Hızlanma (rate-of-change acceleration)
#   5. Destek/Direnç Kümeleme (pivot tabanlı)
#   6. Mum Yapısal Analizi (gövde/fitil oranları)
#
# ICT'den TAMAMEN bağımsız çalışır.
# Kendi confluence skoru, kendi sinyal üretimi.
# =====================================================

import logging
import numpy as np
import pandas as pd
from datetime import datetime

logger = logging.getLogger("ICT-Bot.QPA")


# =====================================================
# QPA PARAMETRELER (Optimizer tarafından güncellenir)
# =====================================================
QPA_PARAMS = {
    # Volatilite Rejim Tespiti
    "vol_squeeze_period": 20,          # Sıkışma ölçüm periyodu
    "vol_squeeze_threshold": 0.6,      # ATR ratio: anlık / ortalama < bu → sıkışma
    "vol_expansion_threshold": 1.5,    # ATR ratio > bu → genişleme başladı

    # Price Action Kalıpları
    "pa_pin_bar_wick_ratio": 2.0,      # Pin bar: uzun fitil / gövde min oranı
    "pa_engulfing_body_pct": 0.6,      # Engulfing mum min gövde oranı
    "pa_inside_bar_tolerance": 0.001,  # Inside bar high/low toleransı

    # Hacim Profili
    "vp_lookback": 40,                 # Hacim profili lookback periyodu
    "vp_poc_zone_pct": 0.002,          # POC etrafı kontrol bölgesi (%0.2)
    "vp_imbalance_threshold": 1.5,     # Buy/sell hacim dengesizlik eşiği

    # Momentum
    "mom_roc_period": 5,               # Rate of change periyodu (mum)
    "mom_accel_period": 3,             # İvmelenme periyodu
    "mom_strong_threshold": 0.008,     # Güçlü momentum eşiği (%0.8)

    # Destek/Direnç
    "sr_pivot_lookback": 5,            # Pivot tespiti lookback
    "sr_cluster_pct": 0.003,           # Kümeleme toleransı (%0.3)
    "sr_min_touches": 2,               # Minimum dokunma sayısı
    "sr_proximity_pct": 0.004,         # Fiyat S/R'ye yakınlık (%0.4)

    # Mum Yapısal
    "cs_consec_candles": 3,            # Ardışık analiz mum sayısı
    "cs_strong_body_pct": 0.65,        # Güçlü gövde eşiği

    # Sinyal Üretimi
    "min_qpa_score": 55,               # Minimum QPA skoru (0-100)
    "min_qpa_confidence": 60,          # Minimum güven
    "qpa_sl_pct": 0.012,              # Stop loss (%1.2)
    "qpa_tp_ratio": 2.5,              # TP/SL oranı
    "qpa_max_concurrent": 5,
    "qpa_cooldown_minutes": 10,

    # Ağırlıklar (optimizer calibre eder)
    "w_volatility": 20,               # Volatilite bileşen ağırlığı
    "w_price_action": 25,             # Price action ağırlığı
    "w_volume": 20,                   # Hacim profili ağırlığı
    "w_momentum": 15,                 # Momentum ağırlığı
    "w_sr_level": 15,                 # S/R seviye ağırlığı
    "w_candle_struct": 5,             # Mum yapısal ağırlığı
}


class QPAStrategy:
    """
    Quantitative Price Action Strategy Engine.
    15-dakikalık grafiklerde çalışır.
    ICT'den tamamen bağımsızdır.
    """

    def __init__(self):
        self.params = dict(QPA_PARAMS)
        self._load_saved_params()

    def _load_saved_params(self):
        """DB'den kaydedilmiş QPA parametrelerini yükle"""
        try:
            from database import get_bot_param
            for key, default in QPA_PARAMS.items():
                qpa_key = f"qpa_{key}"
                saved = get_bot_param(qpa_key)
                if saved is not None:
                    self.params[key] = saved
        except Exception:
            pass

    def reload_params(self):
        """Parametreleri yeniden yükle (optimizer sonrası)"""
        self.params = dict(QPA_PARAMS)
        self._load_saved_params()
        logger.info("QPA parametreleri yeniden yüklendi")

    # =====================================================
    # ANA SİNYAL ÜRETİMİ
    # =====================================================

    def generate_signal(self, symbol, df_15m, multi_tf=None):
        """
        15m veri üzerinde QPA analizi yap ve sinyal üret.
        
        Returns:
            dict veya None
        """
        if df_15m is None or len(df_15m) < 50:
            return None

        try:
            analysis = self.calculate_qpa_confluence(df_15m, multi_tf)

            score = analysis["qpa_score"]
            confidence = analysis["confidence"]
            direction = analysis["direction"]

            if direction == "NEUTRAL":
                return None

            min_score = self.params["min_qpa_score"]
            min_conf = self.params["min_qpa_confidence"]

            if score < min_score or confidence < min_conf:
                return None

            # Entry, SL, TP hesapla
            close = float(df_15m["close"].iloc[-1])
            sl_pct = self.params["qpa_sl_pct"]
            tp_ratio = self.params["qpa_tp_ratio"]

            # Dinamik SL: volatiliteye göre ayarla
            atr = self._calc_atr(df_15m, 14)
            if atr > 0:
                atr_sl = (atr / close) * 1.5
                sl_pct = max(sl_pct, min(atr_sl, 0.03))

            if direction == "LONG":
                sl = close * (1 - sl_pct)
                tp = close * (1 + sl_pct * tp_ratio)
            else:
                sl = close * (1 + sl_pct)
                tp = close * (1 - sl_pct * tp_ratio)

            rr = tp_ratio

            # Tier belirle
            if score >= 80 and confidence >= 75:
                tier = "A+"
            elif score >= 65 and confidence >= 65:
                tier = "A"
            else:
                tier = "B"

            result = {
                "strategy": "QPA",
                "symbol": symbol,
                "direction": direction,
                "entry_price": round(close, 8),
                "stop_loss": round(sl, 8),
                "take_profit": round(tp, 8),
                "confidence": round(confidence, 1),
                "confluence_score": round(score, 1),
                "components": analysis["active_components"],
                "tier": tier,
                "rr_ratio": round(rr, 2),
                "timeframe": "15m",
                "notes": self._build_notes(analysis),
                "analysis": analysis,
                "timestamp": datetime.now().isoformat()
            }

            logger.info(
                f"🎯 QPA Sinyal: {symbol} {direction} | "
                f"Skor:{score:.1f} Güven:{confidence:.1f} Tier:{tier}"
            )

            return result

        except Exception as e:
            logger.error(f"QPA sinyal hatası ({symbol}): {e}")
            return None

    # =====================================================
    # CONFLUENCE HESAPLAMA
    # =====================================================

    def calculate_qpa_confluence(self, df, multi_tf=None):
        """
        6 bileşenden QPA confluence skoru hesapla.
        Her bileşen: skor (0-100) + yön (LONG/SHORT/NEUTRAL)
        """
        results = {}
        components = []

        # 1. Volatilite Rejimi
        vol = self._analyze_volatility(df)
        results["volatility"] = vol

        # 2. Price Action Kalıpları
        pa = self._analyze_price_action(df)
        results["price_action"] = pa

        # 3. Hacim Profili
        vp = self._analyze_volume_profile(df)
        results["volume_profile"] = vp

        # 4. Momentum Hızlanma
        mom = self._analyze_momentum(df)
        results["momentum"] = mom

        # 5. Destek/Direnç Seviyeleri
        sr = self._analyze_sr_levels(df)
        results["sr_levels"] = sr

        # 6. Mum Yapısal Analizi
        cs = self._analyze_candle_structure(df)
        results["candle_structure"] = cs

        # Multi-TF onay (opsiyonel bonus)
        mtf_bonus = 0
        if multi_tf:
            mtf_bonus = self._multi_tf_alignment(multi_tf, results)

        # Ağırlıklı skor hesapla
        w = self.params
        total_weight = (w["w_volatility"] + w["w_price_action"] + w["w_volume"] +
                        w["w_momentum"] + w["w_sr_level"] + w["w_candle_struct"])

        weighted_score = (
            vol["score"] * w["w_volatility"] +
            pa["score"] * w["w_price_action"] +
            vp["score"] * w["w_volume"] +
            mom["score"] * w["w_momentum"] +
            sr["score"] * w["w_sr_level"] +
            cs["score"] * w["w_candle_struct"]
        ) / total_weight

        weighted_score = min(100, weighted_score + mtf_bonus)

        # Aktif bileşenleri topla
        for name, data in results.items():
            if data["score"] >= 40:
                comp_name = f"QPA_{name.upper()}"
                components.append(comp_name)

        # Yön belirleme: her bileşenin yönüne göre oy ver
        long_votes = 0
        short_votes = 0
        for name, data in results.items():
            weight = w.get(f"w_{name}", 10)
            if data["direction"] == "LONG":
                long_votes += weight * (data["score"] / 100)
            elif data["direction"] == "SHORT":
                short_votes += weight * (data["score"] / 100)

        if long_votes > short_votes * 1.2:
            direction = "LONG"
            dir_strength = long_votes / max(long_votes + short_votes, 1)
        elif short_votes > long_votes * 1.2:
            direction = "SHORT"
            dir_strength = short_votes / max(long_votes + short_votes, 1)
        else:
            direction = "NEUTRAL"
            dir_strength = 0

        # Güven: skor + yön gücü
        confidence = weighted_score * 0.6 + dir_strength * 100 * 0.4

        return {
            "qpa_score": round(weighted_score, 1),
            "confidence": round(confidence, 1),
            "direction": direction,
            "direction_strength": round(dir_strength, 3),
            "active_components": components,
            "component_details": results,
            "mtf_bonus": mtf_bonus,
            "long_votes": round(long_votes, 2),
            "short_votes": round(short_votes, 2),
        }

    # =====================================================
    # 1. VOLATİLİTE REJİM ANALİZİ
    # =====================================================

    def _analyze_volatility(self, df):
        """
        ATR sıkışma → patlama tespiti.
        Sıkışma sonrası genişleme = güçlü sinyal.
        """
        period = self.params["vol_squeeze_period"]
        squeeze_th = self.params["vol_squeeze_threshold"]
        expansion_th = self.params["vol_expansion_threshold"]

        atr_fast = self._calc_atr(df, 5)
        atr_slow = self._calc_atr(df, period)

        if atr_slow == 0:
            return {"score": 0, "direction": "NEUTRAL", "regime": "UNKNOWN", "ratio": 0}

        ratio = atr_fast / atr_slow

        # Son N mumdaki ATR değişimi
        atrs = []
        for i in range(min(10, len(df) - period)):
            idx = len(df) - 1 - i
            a = self._calc_atr_at(df, period, idx)
            if a > 0:
                atrs.append(a)

        # Sıkışma → Patlama tespiti
        was_squeezed = False
        if len(atrs) >= 5:
            recent_atrs = atrs[:3]
            older_atrs = atrs[3:6]
            if older_atrs:
                avg_old = np.mean(older_atrs)
                avg_recent = np.mean(recent_atrs)
                if avg_old > 0:
                    was_squeezed = (avg_recent / avg_old) > 1.3

        score = 0
        regime = "NORMAL"

        if ratio < squeeze_th:
            # Sıkışma: patlama bekleniyor → yüksek potansiyel
            score = 70
            regime = "SQUEEZE"
        elif ratio > expansion_th:
            if was_squeezed:
                # Sıkışmadan patlamaya geçiş → EN İYİ SENARYO
                score = 95
                regime = "BREAKOUT"
            else:
                # Normal genişleme
                score = 60
                regime = "EXPANSION"
        else:
            score = 30
            regime = "NORMAL"

        # Yön: son mumların yönüne göre
        last_3 = df.tail(3)
        up_count = (last_3["close"] > last_3["open"]).sum()
        down_count = (last_3["close"] < last_3["open"]).sum()

        if up_count >= 2:
            direction = "LONG"
        elif down_count >= 2:
            direction = "SHORT"
        else:
            direction = "NEUTRAL"

        return {
            "score": score,
            "direction": direction,
            "regime": regime,
            "ratio": round(ratio, 3),
            "was_squeezed": was_squeezed
        }

    # =====================================================
    # 2. PRICE ACTION KALIPLARI
    # =====================================================

    def _analyze_price_action(self, df):
        """
        Pin Bar, Engulfing, Inside Bar tespiti.
        İstatistiksel olarak doğrulanmış kalıplar.
        """
        patterns = []
        score = 0
        direction = "NEUTRAL"

        if len(df) < 5:
            return {"score": 0, "direction": "NEUTRAL", "patterns": []}

        # Son 5 muma bak
        for i in range(-3, 0):
            idx = len(df) + i
            if idx < 1:
                continue

            candle = df.iloc[idx]
            prev = df.iloc[idx - 1]

            o, h, l, c = float(candle["open"]), float(candle["high"]), float(candle["low"]), float(candle["close"])
            po, ph, pl, pc = float(prev["open"]), float(prev["high"]), float(prev["low"]), float(prev["close"])

            body = abs(c - o)
            full_range = h - l
            if full_range == 0:
                continue

            upper_wick = h - max(o, c)
            lower_wick = min(o, c) - l
            body_ratio = body / full_range

            # === PIN BAR ===
            wick_ratio = self.params["pa_pin_bar_wick_ratio"]
            if body > 0:
                # Bullish Pin Bar (alt fitil uzun)
                if lower_wick > body * wick_ratio and lower_wick > upper_wick * 2:
                    weight = min(1.0, lower_wick / (body * wick_ratio))
                    patterns.append({"type": "BULLISH_PIN_BAR", "weight": weight, "idx": i})
                # Bearish Pin Bar (üst fitil uzun)
                elif upper_wick > body * wick_ratio and upper_wick > lower_wick * 2:
                    weight = min(1.0, upper_wick / (body * wick_ratio))
                    patterns.append({"type": "BEARISH_PIN_BAR", "weight": weight, "idx": i})

            # === ENGULFING ===
            min_body = self.params["pa_engulfing_body_pct"]
            if body_ratio >= min_body:
                prev_body = abs(pc - po)
                # Bullish Engulfing
                if c > o and pc < po and body > prev_body and l <= pl:
                    weight = min(1.0, body / max(prev_body, 0.0001))
                    patterns.append({"type": "BULLISH_ENGULFING", "weight": weight, "idx": i})
                # Bearish Engulfing
                elif c < o and pc > po and body > prev_body and h >= ph:
                    weight = min(1.0, body / max(prev_body, 0.0001))
                    patterns.append({"type": "BEARISH_ENGULFING", "weight": weight, "idx": i})

            # === INSIDE BAR ===
            tol = self.params["pa_inside_bar_tolerance"] * float(candle["close"])
            if h <= ph + tol and l >= pl - tol and full_range < (ph - pl) * 0.7:
                patterns.append({"type": "INSIDE_BAR", "weight": 0.6, "idx": i})

        # Son mum: doğrudan önemli
        last = df.iloc[-1]
        lo, lh, ll, lc = float(last["open"]), float(last["high"]), float(last["low"]), float(last["close"])
        last_body = abs(lc - lo)
        last_range = lh - ll
        if last_range > 0:
            last_body_ratio = last_body / last_range
            # Hammer (bullish doji benzeri alt fitil) as current candle
            lower_w = min(lo, lc) - ll
            upper_w = lh - max(lo, lc)
            if lower_w > last_body * 1.5 and lower_w > upper_w * 2 and last_body_ratio < 0.4:
                patterns.append({"type": "HAMMER", "weight": 0.8, "idx": 0})
            elif upper_w > last_body * 1.5 and upper_w > lower_w * 2 and last_body_ratio < 0.4:
                patterns.append({"type": "SHOOTING_STAR", "weight": 0.8, "idx": 0})

        # Skor ve yön hesapla
        if not patterns:
            return {"score": 0, "direction": "NEUTRAL", "patterns": []}

        bullish_score = 0
        bearish_score = 0
        for p in patterns:
            w = p["weight"]
            # Yakın mumlar daha önemli
            recency = 1.0 - abs(p["idx"]) * 0.15
            w *= max(recency, 0.5)

            if p["type"] in ("BULLISH_PIN_BAR", "BULLISH_ENGULFING", "HAMMER"):
                bullish_score += w
            elif p["type"] in ("BEARISH_PIN_BAR", "BEARISH_ENGULFING", "SHOOTING_STAR"):
                bearish_score += w
            elif p["type"] == "INSIDE_BAR":
                # Inside bar: yön bağımsız, sıkışma sinyali
                bullish_score += w * 0.3
                bearish_score += w * 0.3

        total_pa = bullish_score + bearish_score
        if total_pa > 0:
            score = min(100, total_pa * 40)
            if bullish_score > bearish_score * 1.3:
                direction = "LONG"
            elif bearish_score > bullish_score * 1.3:
                direction = "SHORT"

        return {
            "score": round(score, 1),
            "direction": direction,
            "patterns": [p["type"] for p in patterns],
            "bullish_score": round(bullish_score, 2),
            "bearish_score": round(bearish_score, 2)
        }

    # =====================================================
    # 3. HACİM PROFİLİ ANALİZİ
    # =====================================================

    def _analyze_volume_profile(self, df):
        """
        - POC (en çok işlem gören fiyat seviyesi)
        - Fiyatın POC'ye göre konumu
        - Alış/satış hacim dengesizliği
        """
        lookback = min(self.params["vp_lookback"], len(df) - 1)
        data = df.tail(lookback)

        if len(data) < 10 or "volume" not in data.columns:
            return {"score": 0, "direction": "NEUTRAL", "poc": 0, "imbalance": 0}

        # Fiyat aralığını bölgelere ayır
        price_high = float(data["high"].max())
        price_low = float(data["low"].min())
        price_range = price_high - price_low

        if price_range == 0:
            return {"score": 0, "direction": "NEUTRAL", "poc": 0, "imbalance": 0}

        n_bins = 20
        bin_size = price_range / n_bins
        volume_at_price = np.zeros(n_bins)

        for _, row in data.iterrows():
            mid = (float(row["high"]) + float(row["low"])) / 2
            vol = float(row["volume"]) if not pd.isna(row["volume"]) else 0
            bin_idx = min(int((mid - price_low) / bin_size), n_bins - 1)
            volume_at_price[bin_idx] += vol

        # POC (Point of Control)
        poc_idx = np.argmax(volume_at_price)
        poc_price = price_low + (poc_idx + 0.5) * bin_size

        current_price = float(data["close"].iloc[-1])

        # Fiyat POC'nin altında mı üstünde mi?
        poc_zone_pct = self.params["vp_poc_zone_pct"]
        above_poc = current_price > poc_price * (1 + poc_zone_pct)
        below_poc = current_price < poc_price * (1 - poc_zone_pct)
        at_poc = not above_poc and not below_poc

        # Hacim dengesizliği: Son mumların alış vs satış tahmini
        imbalance_th = self.params["vp_imbalance_threshold"]
        recent = data.tail(10)
        buy_vol = 0
        sell_vol = 0
        for _, row in recent.iterrows():
            vol = float(row["volume"]) if not pd.isna(row["volume"]) else 0
            body = float(row["close"]) - float(row["open"])
            rng = float(row["high"]) - float(row["low"])
            if rng > 0:
                buy_pct = max(0, body / rng + 0.5)
                buy_vol += vol * buy_pct
                sell_vol += vol * (1 - buy_pct)

        total_vol = buy_vol + sell_vol
        if total_vol > 0:
            imbalance = buy_vol / max(sell_vol, 1)
        else:
            imbalance = 1.0

        # Skor ve yön
        score = 0
        direction = "NEUTRAL"

        if below_poc and imbalance > imbalance_th:
            # Fiyat POC altında + alış baskısı → LONG
            score = 80
            direction = "LONG"
        elif above_poc and imbalance < (1 / imbalance_th):
            # Fiyat POC üstünde + satış baskısı → SHORT
            score = 80
            direction = "SHORT"
        elif below_poc:
            score = 50
            direction = "LONG"
        elif above_poc:
            score = 50
            direction = "SHORT"
        elif at_poc:
            # POC'de: hacim dengesizliğine bak
            if imbalance > imbalance_th:
                score = 55
                direction = "LONG"
            elif imbalance < (1 / imbalance_th):
                score = 55
                direction = "SHORT"
            else:
                score = 20

        # Hacim spike: son mum ortalamanın 2x üstünde
        avg_vol = float(data["volume"].mean()) if "volume" in data.columns else 0
        last_vol = float(data["volume"].iloc[-1]) if "volume" in data.columns else 0
        if avg_vol > 0 and last_vol > avg_vol * 2:
            score = min(100, score + 15)

        return {
            "score": round(score, 1),
            "direction": direction,
            "poc": round(poc_price, 8),
            "imbalance": round(imbalance, 3),
            "above_poc": above_poc,
            "below_poc": below_poc
        }

    # =====================================================
    # 4. MOMENTUM HIZLANMA ANALİZİ
    # =====================================================

    def _analyze_momentum(self, df):
        """
        Rate of Change + İvmelenme.
        RSI/MACD değil — ham fiyat değişim hızı.
        """
        roc_period = self.params["mom_roc_period"]
        accel_period = self.params["mom_accel_period"]
        strong_th = self.params["mom_strong_threshold"]

        if len(df) < roc_period + accel_period + 5:
            return {"score": 0, "direction": "NEUTRAL", "roc": 0, "acceleration": 0}

        closes = df["close"].astype(float).values

        # Rate of Change serileri
        rocs = []
        for i in range(roc_period + accel_period + 5, len(closes)):
            if closes[i - roc_period] > 0:
                roc = (closes[i] - closes[i - roc_period]) / closes[i - roc_period]
                rocs.append(roc)

        if len(rocs) < accel_period + 2:
            return {"score": 0, "direction": "NEUTRAL", "roc": 0, "acceleration": 0}

        current_roc = rocs[-1]

        # İvmelenme: RoC'nin RoC'si
        accels = []
        for i in range(accel_period, len(rocs)):
            accel = rocs[i] - rocs[i - accel_period]
            accels.append(accel)

        current_accel = accels[-1] if accels else 0

        # Yön ve skor
        score = 0
        direction = "NEUTRAL"

        abs_roc = abs(current_roc)
        abs_accel = abs(current_accel)

        if abs_roc > strong_th:
            # Güçlü momentum
            if current_roc > 0 and current_accel > 0:
                # Yukarı momentum hızlanıyor → LONG
                score = min(100, 60 + (abs_roc / strong_th) * 20 + (abs_accel / strong_th) * 20)
                direction = "LONG"
            elif current_roc < 0 and current_accel < 0:
                # Aşağı momentum hızlanıyor → SHORT
                score = min(100, 60 + (abs_roc / strong_th) * 20 + (abs_accel / strong_th) * 20)
                direction = "SHORT"
            elif current_roc > 0 and current_accel < 0:
                # Yukarı ama yavaşlıyor → zayıf LONG
                score = 30
                direction = "LONG"
            elif current_roc < 0 and current_accel > 0:
                # Aşağı ama yavaşlıyor → zayıf SHORT / reversal?
                score = 30
                direction = "SHORT"
        elif abs_roc > strong_th * 0.5:
            # Orta momentum
            if current_roc > 0:
                score = 40
                direction = "LONG"
            else:
                score = 40
                direction = "SHORT"
        else:
            # Zayıf / düz
            score = 10

        # Momentum divergence: fiyat yeni high ama momentum düşüyor
        if len(closes) >= 20:
            recent_high = np.max(closes[-10:])
            prev_high = np.max(closes[-20:-10])
            recent_max_roc = max(rocs[-5:]) if len(rocs) >= 5 else current_roc
            prev_max_roc = max(rocs[-10:-5]) if len(rocs) >= 10 else current_roc

            if recent_high > prev_high and recent_max_roc < prev_max_roc * 0.8:
                # Bearish divergence
                if direction == "LONG":
                    score = max(0, score - 20)
                else:
                    score = min(100, score + 15)
                    direction = "SHORT"
            elif recent_high < prev_high and recent_max_roc > prev_max_roc * 0.8:
                # Bullish divergence (tersi)
                recent_low = np.min(closes[-10:])
                prev_low = np.min(closes[-20:-10])
                if recent_low < prev_low:
                    if direction == "SHORT":
                        score = max(0, score - 20)
                    else:
                        score = min(100, score + 15)
                        direction = "LONG"

        return {
            "score": round(score, 1),
            "direction": direction,
            "roc": round(current_roc, 6),
            "acceleration": round(current_accel, 6),
            "abs_roc": round(abs_roc, 6)
        }

    # =====================================================
    # 5. DESTEK / DİRENÇ SEVİYE ANALİZİ
    # =====================================================

    def _analyze_sr_levels(self, df):
        """
        Pivot noktalarından otomatik S/R tespiti.
        Kümeleme ile güçlü bölgeler bulunur.
        """
        lookback = self.params["sr_pivot_lookback"]
        cluster_pct = self.params["sr_cluster_pct"]
        min_touches = self.params["sr_min_touches"]
        proximity = self.params["sr_proximity_pct"]

        if len(df) < lookback * 2 + 5:
            return {"score": 0, "direction": "NEUTRAL", "levels": [], "nearest": None}

        highs = df["high"].astype(float).values
        lows = df["low"].astype(float).values
        closes = df["close"].astype(float).values

        # Pivot yüksek ve düşükler bul
        pivots = []
        for i in range(lookback, len(df) - lookback):
            # Pivot High
            if highs[i] == max(highs[i - lookback:i + lookback + 1]):
                pivots.append({"price": highs[i], "type": "resistance"})
            # Pivot Low
            if lows[i] == min(lows[i - lookback:i + lookback + 1]):
                pivots.append({"price": lows[i], "type": "support"})

        if not pivots:
            return {"score": 0, "direction": "NEUTRAL", "levels": [], "nearest": None}

        # Kümeleme: yakın pivotları grupla
        pivot_prices = sorted([p["price"] for p in pivots])
        clusters = []
        used = set()

        for i, price in enumerate(pivot_prices):
            if i in used:
                continue
            cluster = [price]
            used.add(i)
            for j in range(i + 1, len(pivot_prices)):
                if j in used:
                    continue
                if abs(pivot_prices[j] - price) / price < cluster_pct:
                    cluster.append(pivot_prices[j])
                    used.add(j)
            if len(cluster) >= min_touches:
                avg_price = np.mean(cluster)
                clusters.append({
                    "price": round(avg_price, 8),
                    "touches": len(cluster),
                    "strength": len(cluster) / max(len(pivots), 1)
                })

        current = closes[-1]
        score = 0
        direction = "NEUTRAL"
        nearest = None
        nearest_dist = float('inf')

        for level in clusters:
            dist = abs(current - level["price"]) / current
            if dist < nearest_dist:
                nearest_dist = dist
                nearest = level

        if nearest and nearest_dist < proximity:
            # Fiyat güçlü bir S/R seviyesine yakın
            strength_bonus = nearest["touches"] * 10
            proximity_bonus = (1 - nearest_dist / proximity) * 30

            if current > nearest["price"]:
                # Fiyat seviyenin üstünde → support test
                direction = "LONG"
                score = min(100, 40 + strength_bonus + proximity_bonus)
            else:
                # Fiyat seviyenin altında → resistance test
                direction = "SHORT"
                score = min(100, 40 + strength_bonus + proximity_bonus)
        elif nearest and nearest_dist < proximity * 2:
            # Orta yakınlık
            score = 25
            if current > nearest["price"]:
                direction = "LONG"
            else:
                direction = "SHORT"

        levels_info = [{"price": c["price"], "touches": c["touches"]} for c in clusters[:5]]

        return {
            "score": round(score, 1),
            "direction": direction,
            "levels": levels_info,
            "nearest": nearest,
            "distance_pct": round(nearest_dist * 100, 3) if nearest else 0
        }

    # =====================================================
    # 6. MUM YAPISAL ANALİZİ
    # =====================================================

    def _analyze_candle_structure(self, df):
        """
        Ardışık mum yapıları:
        - Ardışık güçlü gövdeler → trend gücü
        - Küçülen gövdeler → bitkinlik
        - Gövde/fitil oranı trendleri
        """
        n = self.params["cs_consec_candles"]
        strong_body = self.params["cs_strong_body_pct"]

        if len(df) < n + 2:
            return {"score": 0, "direction": "NEUTRAL", "pattern": "NONE"}

        recent = df.tail(n + 2)
        bodies = []
        directions = []
        body_ratios = []

        for i in range(len(recent)):
            row = recent.iloc[i]
            o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
            body = abs(c - o)
            rng = h - l
            bodies.append(body)
            directions.append(1 if c > o else -1)
            body_ratios.append(body / rng if rng > 0 else 0)

        score = 0
        direction = "NEUTRAL"
        pattern = "NONE"

        # Son N mum aynı yönde mi?
        last_n_dirs = directions[-n:]
        all_up = all(d == 1 for d in last_n_dirs)
        all_down = all(d == -1 for d in last_n_dirs)

        # Son N mumun gövde oranları güçlü mü?
        last_n_ratios = body_ratios[-n:]
        avg_ratio = np.mean(last_n_ratios)
        strong_candles = sum(1 for r in last_n_ratios if r >= strong_body)

        # Gövde büyüklüğü trendi (büyüyor mu küçülüyor mu?)
        last_n_bodies = bodies[-n:]
        body_growing = all(last_n_bodies[i] >= last_n_bodies[i-1] * 0.9 for i in range(1, len(last_n_bodies)))
        body_shrinking = all(last_n_bodies[i] <= last_n_bodies[i-1] * 1.1 for i in range(1, len(last_n_bodies)))

        if all_up and strong_candles >= n - 1:
            pattern = "STRONG_BULL_PUSH"
            score = min(100, 60 + strong_candles * 10)
            direction = "LONG"
            if body_growing:
                score = min(100, score + 10)
        elif all_down and strong_candles >= n - 1:
            pattern = "STRONG_BEAR_PUSH"
            score = min(100, 60 + strong_candles * 10)
            direction = "SHORT"
            if body_growing:
                score = min(100, score + 10)
        elif all_up:
            pattern = "BULL_TREND"
            score = 45
            direction = "LONG"
        elif all_down:
            pattern = "BEAR_TREND"
            score = 45
            direction = "SHORT"
        elif body_shrinking and avg_ratio < 0.3:
            # Gövdeler küçülüyor → bitkinlik / reversal
            pattern = "EXHAUSTION"
            score = 35
            # Ters yön: mevcut trende karşı
            if directions[-1] == 1:
                direction = "SHORT"
            else:
                direction = "LONG"
        else:
            score = 10
            pattern = "MIXED"

        return {
            "score": round(score, 1),
            "direction": direction,
            "pattern": pattern,
            "avg_body_ratio": round(avg_ratio, 3),
            "strong_candles": strong_candles
        }

    # =====================================================
    # MULTI-TF HIZALAMA (Bonus)
    # =====================================================

    def _multi_tf_alignment(self, multi_tf, analysis_15m):
        """
        1H ve 4H verileriyle 15m sinyalinin aynı yönde olup olmadığını kontrol et.
        Bonus puan verir ama kendi başına sinyal üretmez.
        """
        bonus = 0
        direction_15m = "NEUTRAL"

        # 15m yönü bul
        long_v = sum(1 for _, d in analysis_15m.items() if d.get("direction") == "LONG")
        short_v = sum(1 for _, d in analysis_15m.items() if d.get("direction") == "SHORT")
        if long_v > short_v:
            direction_15m = "LONG"
        elif short_v > long_v:
            direction_15m = "SHORT"

        for tf_name in ("1H", "4H"):
            tf_data = multi_tf.get(tf_name)
            if tf_data is None or len(tf_data) < 20:
                continue

            closes = tf_data["close"].astype(float).values
            # Basit trend: son 10 mum kapanışlarının ortalaması vs son fiyat
            recent_avg = np.mean(closes[-10:])
            older_avg = np.mean(closes[-20:-10]) if len(closes) >= 20 else recent_avg

            if recent_avg > older_avg:
                htf_dir = "LONG"
            elif recent_avg < older_avg:
                htf_dir = "SHORT"
            else:
                htf_dir = "NEUTRAL"

            if htf_dir == direction_15m and direction_15m != "NEUTRAL":
                bonus += 5
            elif htf_dir != "NEUTRAL" and htf_dir != direction_15m and direction_15m != "NEUTRAL":
                bonus -= 3

        return bonus

    # =====================================================
    # YARDIMCI METODLAR
    # =====================================================

    def _calc_atr(self, df, period):
        """Average True Range hesapla (son değer)"""
        if len(df) < period + 1:
            return 0
        highs = df["high"].astype(float).values
        lows = df["low"].astype(float).values
        closes = df["close"].astype(float).values

        trs = []
        for i in range(1, len(df)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1])
            )
            trs.append(tr)

        if len(trs) < period:
            return np.mean(trs) if trs else 0

        return np.mean(trs[-period:])

    def _calc_atr_at(self, df, period, end_idx):
        """Belirli bir indeks noktasında ATR hesapla"""
        if end_idx < period + 1:
            return 0
        highs = df["high"].astype(float).values
        lows = df["low"].astype(float).values
        closes = df["close"].astype(float).values

        trs = []
        start = max(1, end_idx - period)
        for i in range(start, end_idx + 1):
            if i >= len(df):
                break
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1])
            )
            trs.append(tr)

        return np.mean(trs) if trs else 0

    def _build_notes(self, analysis):
        """Sinyal notlarını oluştur"""
        notes = []
        details = analysis.get("component_details", {})

        vol = details.get("volatility", {})
        if vol.get("regime") == "BREAKOUT":
            notes.append(f"🔥 Sıkışmadan patlama (ATR: {vol.get('ratio', 0):.2f}x)")
        elif vol.get("regime") == "SQUEEZE":
            notes.append("⏳ Volatilite sıkışması (patlama bekleniyor)")

        pa = details.get("price_action", {})
        if pa.get("patterns"):
            patterns_str = ", ".join(pa["patterns"][:3])
            notes.append(f"📊 Kalıplar: {patterns_str}")

        vp = details.get("volume_profile", {})
        if vp.get("imbalance", 1) > 1.5:
            notes.append(f"📈 Alış hacim baskısı ({vp['imbalance']:.1f}x)")
        elif vp.get("imbalance", 1) < 0.67:
            notes.append(f"📉 Satış hacim baskısı ({1/vp['imbalance']:.1f}x)")

        mom = details.get("momentum", {})
        if abs(mom.get("roc", 0)) > 0.008:
            notes.append(f"🚀 Güçlü momentum (RoC: {mom['roc']*100:.2f}%)")

        sr = details.get("sr_levels", {})
        if sr.get("nearest") and sr.get("distance_pct", 100) < 0.4:
            level = sr["nearest"]
            notes.append(f"🎯 S/R seviyesi: {level['price']:.4f} ({level['touches']} dokunma)")

        cs = details.get("candle_structure", {})
        if cs.get("pattern") in ("STRONG_BULL_PUSH", "STRONG_BEAR_PUSH"):
            notes.append(f"💪 {cs['pattern']} ({cs.get('strong_candles', 0)} güçlü mum)")

        return " | ".join(notes) if notes else "QPA standart sinyal"


# Singleton
qpa_strategy = QPAStrategy()
