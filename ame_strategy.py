# =====================================================
# Adaptive Microstructure Engine (AME) v2.0
# =====================================================
#
# ICT'den TAMAMEN BAĞIMSIZ strateji motoru.
# Klasik retail indikatörleri (RSI, MACD, BB, MA) KULLANILMAZ.
#
# 6 Bileşen:
#   1. Akıllı Rejim Motoru — Hurst + Kaufman Efficiency + Realized Vol
#   2. Order Flow Mikroyapı — CVD divergence, Emilim, Sweep (Stop Avı)
#   3. Multi-TF Confluence — 4H + 1H + 15M yön uyumu
#   4. BTC Korelasyon Filtresi — Altcoin bağımsızlık analizi
#   5. Dinamik Skor Motoru — 100 puanlık bileşik değerlendirme
#   6. Kademeli Risk Yönetimi — 3-seviye TP, Session-Aware SL
#
# v1.0'dan farklar:
#   - Multi-timeframe analiz (tek TF yerine 3 TF)
#   - Order flow (CVD divergence, absorption, sweep tespiti)
#   - BTC korelasyon filtresi
#   - Hurst bazlı trend/mean-revert rejim ayrımı
#   - 3 kademeli TP (40%/30%/30%) — tek TP yerine
#   - Session-aware risk (Asya/Londra/NY)
# =====================================================

import numpy as np
import logging
from datetime import datetime, timezone

logger = logging.getLogger("AME.Strategy")


def _to_native(obj):
    """numpy/bool → Python native dönüşüm (JSON serialization fix)."""
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_native(v) for v in obj]
    return obj


class AMEStrategyV2:
    """
    Adaptive Microstructure Engine v2.0

    Multi-timeframe, order-flow-aware, BTC-filtered sinyal motoru.
    Sinyaller sadece mum kapanışında kesinleşir (repaint yok).
    """

    def __init__(self, mode="balanced"):
        self.mode = mode
        self._apply_params()

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    #   MODE PARAMS
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    def _apply_params(self):
        if self.mode == "aggressive":
            self.p = {
                # Regime
                "hurst_period": 40,
                "hurst_trend_threshold": 0.53,
                "hurst_mr_threshold": 0.47,
                "efficiency_window": 15,
                "efficiency_trend_threshold": 0.25,
                # Order Flow
                "cvd_lookback": 25,
                "cvd_divergence_threshold": 0.5,
                "absorption_vol_mult": 1.8,
                "absorption_body_max_ratio": 0.35,
                "sweep_lookback": 15,
                "sweep_wick_min_ratio": 0.55,
                # Multi-TF
                "trend_lookback_4h": 15,
                "trend_lookback_1h": 25,
                "trend_lookback_15m": 20,
                "min_confluence": 1,
                # BTC
                "btc_corr_window": 25,
                "btc_corr_high": 0.75,
                # Signal
                "min_signal_score": 45,
                # Risk
                "sl_atr_mult": 1.3,
                "tp1_rr": 1.0,
                "tp2_rr": 1.5,
                "tp3_rr": 2.5,
                "tp1_size": 0.40,
                "tp2_size": 0.30,
                "tp3_size": 0.30,
                "session_adjust": False,
            }
        elif self.mode == "conservative":
            self.p = {
                "hurst_period": 60,
                "hurst_trend_threshold": 0.58,
                "hurst_mr_threshold": 0.42,
                "efficiency_window": 25,
                "efficiency_trend_threshold": 0.35,
                "cvd_lookback": 35,
                "cvd_divergence_threshold": 0.7,
                "absorption_vol_mult": 2.2,
                "absorption_body_max_ratio": 0.25,
                "sweep_lookback": 25,
                "sweep_wick_min_ratio": 0.65,
                "trend_lookback_4h": 25,
                "trend_lookback_1h": 35,
                "trend_lookback_15m": 25,
                "min_confluence": 2,
                "btc_corr_window": 35,
                "btc_corr_high": 0.65,
                "min_signal_score": 65,
                "sl_atr_mult": 1.8,
                "tp1_rr": 1.5,
                "tp2_rr": 2.5,
                "tp3_rr": 4.0,
                "tp1_size": 0.35,
                "tp2_size": 0.30,
                "tp3_size": 0.35,
                "session_adjust": True,
            }
        else:  # balanced
            self.p = {
                "hurst_period": 50,
                "hurst_trend_threshold": 0.55,
                "hurst_mr_threshold": 0.45,
                "efficiency_window": 20,
                "efficiency_trend_threshold": 0.30,
                "cvd_lookback": 30,
                "cvd_divergence_threshold": 0.6,
                "absorption_vol_mult": 2.0,
                "absorption_body_max_ratio": 0.30,
                "sweep_lookback": 20,
                "sweep_wick_min_ratio": 0.60,
                "trend_lookback_4h": 20,
                "trend_lookback_1h": 30,
                "trend_lookback_15m": 20,
                "min_confluence": 2,
                "btc_corr_window": 30,
                "btc_corr_high": 0.70,
                "min_signal_score": 55,
                "sl_atr_mult": 1.5,
                "tp1_rr": 1.0,
                "tp2_rr": 2.0,
                "tp3_rr": 3.5,
                "tp1_size": 0.40,
                "tp2_size": 0.30,
                "tp3_size": 0.30,
                "session_adjust": True,
            }

    def set_mode(self, mode):
        if mode in ("aggressive", "balanced", "conservative"):
            self.mode = mode
            self._apply_params()
            logger.info(f"AME v2 modu değiştirildi: {mode}")

    # ===========================================================
    #  1. REGIME ENGINE — Hurst + Kaufman Efficiency + Vol
    # ===========================================================

    def _calc_atr(self, highs, lows, closes, period=14):
        """Average True Range (EMA-smoothed)."""
        n = len(closes)
        if n < 2:
            return 0.0
        if n < period + 1:
            ranges = highs[:n] - lows[:n]
            return float(np.mean(ranges)) if len(ranges) > 0 else 0.0
        tr = np.zeros(n)
        tr[0] = highs[0] - lows[0]
        for i in range(1, n):
            tr[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        atr = float(np.mean(tr[:period]))
        m = 2.0 / (period + 1)
        for i in range(period, n):
            atr = (tr[i] - atr) * m + atr
        return float(atr)

    def _calc_hurst(self, closes, max_lag=None):
        """
        Hurst Exponent (R/S analizi).
          H > 0.55 → Trend (persistent)
          H ≈ 0.50 → Random walk
          H < 0.45 → Mean-reverting (anti-persistent)
        """
        n = len(closes)
        if max_lag is None:
            max_lag = min(n // 3, self.p["hurst_period"] // 2)
        if n < 20 or max_lag < 4:
            return 0.5

        lags = range(4, max_lag + 1)
        log_rs = []

        for lag in lags:
            rs_list = []
            for start in range(0, n - lag, lag):
                segment = closes[start : start + lag]
                if len(segment) < lag:
                    continue
                mean = np.mean(segment)
                deviations = np.cumsum(segment - mean)
                R = float(np.max(deviations) - np.min(deviations))
                S = float(np.std(segment, ddof=1))
                if S > 1e-10:
                    rs_list.append(R / S)
            if rs_list:
                log_rs.append((np.log(lag), np.log(np.mean(rs_list))))

        if len(log_rs) < 3:
            return 0.5

        x = np.array([v[0] for v in log_rs])
        y = np.array([v[1] for v in log_rs])
        H = float(np.polyfit(x, y, 1)[0])
        return max(0.0, min(1.0, H))

    def _calc_efficiency(self, closes, window=None):
        """
        Kaufman Efficiency Ratio: abs(net move) / total path.
        High = trending, Low = choppy.
        """
        if window is None:
            window = self.p["efficiency_window"]
        n = len(closes)
        if n < window + 1:
            return 0.5
        segment = closes[-window:]
        direction = abs(segment[-1] - segment[0])
        path = sum(abs(segment[i] - segment[i - 1]) for i in range(1, len(segment)))
        return float(direction / path) if path > 0 else 0.0

    def _calc_realized_vol(self, closes, window=20):
        """Realized volatility (annualized, 15m base)."""
        n = len(closes)
        if n < window + 1:
            return 0.0
        returns = np.diff(np.log(closes[-window - 1 :]))
        return float(np.std(returns) * np.sqrt(365 * 24 * 4))

    def detect_regime(self, df_1h):
        """
        Akıllı rejim tespiti: Hurst + Efficiency + Realized Vol.

        Rejimler:
          TREND_BULL  — Güçlü yukarı trend
          TREND_BEAR  — Güçlü aşağı trend
          MEAN_REVERT — Bant içi geri dönüş
          TRANSITION  — Rejim değişimi
          CHAOS       — Random walk, sinyal güvenilmez
        """
        if df_1h is None or df_1h.empty or len(df_1h) < 30:
            return {
                "regime": "UNKNOWN",
                "hurst": 0.5,
                "efficiency": 0.5,
                "realized_vol": 0,
                "direction": "NEUTRAL",
                "slope": 0,
                "atr": 0,
                "details": "Yetersiz veri",
            }

        closes = df_1h["close"].values.astype(float)
        highs = df_1h["high"].values.astype(float)
        lows = df_1h["low"].values.astype(float)

        hurst = self._calc_hurst(closes)
        efficiency = self._calc_efficiency(closes, self.p["efficiency_window"])
        r_vol = self._calc_realized_vol(closes)

        # Son 20 bar slope (per-mille normalized)
        n = min(20, len(closes))
        x = np.arange(n, dtype=float)
        slope = float(np.polyfit(x, closes[-n:], 1)[0])
        avg_price = float(np.mean(closes[-n:]))
        norm_slope = slope / avg_price * 1000 if avg_price > 0 else 0

        direction = (
            "BULLISH" if norm_slope > 0.3 else "BEARISH" if norm_slope < -0.3 else "NEUTRAL"
        )

        h_trend = self.p["hurst_trend_threshold"]
        h_mr = self.p["hurst_mr_threshold"]
        e_trend = self.p["efficiency_trend_threshold"]

        if hurst >= h_trend and efficiency >= e_trend:
            regime = f"TREND_{direction}" if direction != "NEUTRAL" else "TREND_BULL"
        elif hurst <= h_mr:
            regime = "MEAN_REVERT"
        elif hurst >= h_trend and efficiency < e_trend:
            regime = "TRANSITION"
        elif abs(hurst - 0.5) < 0.05:
            regime = "CHAOS"
        else:
            regime = "TRANSITION"

        atr = self._calc_atr(highs, lows, closes)

        return {
            "regime": regime,
            "hurst": round(hurst, 3),
            "efficiency": round(efficiency, 3),
            "realized_vol": round(r_vol, 4),
            "direction": direction,
            "slope": round(norm_slope, 3),
            "atr": round(atr, 8),
            "details": f"H={hurst:.3f} E={efficiency:.3f} RV={r_vol:.3f} Slope={norm_slope:.2f}‰",
        }

    # ===========================================================
    #  2. ORDER FLOW — CVD, Absorption, Sweep
    # ===========================================================

    def _calc_volume_delta(self, high, low, close, open_, volume):
        """Her mum için volume delta (agresif alıcı-satıcı tahmini)."""
        n = len(close)
        deltas = np.zeros(n)
        for i in range(n):
            rng = high[i] - low[i]
            if rng > 0:
                buy_pct = (close[i] - low[i]) / rng
                deltas[i] = volume[i] * (2 * buy_pct - 1)
        return deltas

    def _calc_cvd(self, df):
        """Cumulative Volume Delta — agresif alıcı vs satıcı akışı."""
        vol = df["volume"].values.astype(float) if "volume" in df.columns else np.ones(len(df))
        deltas = self._calc_volume_delta(
            df["high"].values.astype(float),
            df["low"].values.astype(float),
            df["close"].values.astype(float),
            df["open"].values.astype(float),
            vol,
        )
        return np.cumsum(deltas), deltas

    def _detect_cvd_divergence(self, df_15m):
        """
        CVD Divergence: Fiyat ve agresif akış ayrışması.
          Fiyat ↑ + CVD ↓ → Hidden weakness (SHORT bias)
          Fiyat ↓ + CVD ↑ → Hidden strength (LONG bias)
          Aligned → Trend devam
        """
        n = len(df_15m)
        lookback = min(self.p["cvd_lookback"], n - 1)
        if lookback < 10:
            return {
                "divergence": "NONE",
                "price_direction": "NEUTRAL",
                "cvd_direction": "NEUTRAL",
                "cvd_normalized": 0,
                "score": 0,
            }

        closes = df_15m["close"].values.astype(float)
        cvd, _ = self._calc_cvd(df_15m)

        price_change = (closes[-1] - closes[-lookback]) / closes[-lookback] if closes[-lookback] > 0 else 0
        cvd_change = cvd[-1] - cvd[-lookback]
        cvd_range = np.max(cvd[-lookback:]) - np.min(cvd[-lookback:])
        cvd_norm = cvd_change / cvd_range if cvd_range > 0 else 0

        threshold = self.p["cvd_divergence_threshold"]

        price_dir = "UP" if price_change > 0.005 else "DOWN" if price_change < -0.005 else "FLAT"
        cvd_dir = "UP" if cvd_norm > threshold else "DOWN" if cvd_norm < -threshold else "FLAT"

        if price_dir == "UP" and cvd_dir == "DOWN":
            div = "BEARISH_DIVERGENCE"
            score = min(1.0, abs(cvd_norm))
        elif price_dir == "DOWN" and cvd_dir == "UP":
            div = "BULLISH_DIVERGENCE"
            score = min(1.0, abs(cvd_norm))
        elif price_dir == cvd_dir and price_dir != "FLAT":
            div = "CONFIRMING"
            score = min(1.0, abs(cvd_norm) * 0.7)
        else:
            div = "NONE"
            score = 0

        return {
            "divergence": div,
            "price_direction": price_dir,
            "cvd_direction": cvd_dir,
            "cvd_normalized": round(float(cvd_norm), 3),
            "score": round(float(score), 3),
        }

    def _detect_absorption(self, df_15m):
        """
        Emilim Tespiti: Yüksek hacim + küçük gövde = güçlü destek/direnç.
        Kurumsal al-sat emilimi, trend dönüşü sinyali.
        """
        n = len(df_15m)
        lookback = 20
        if n < lookback + 1:
            return None

        volume = df_15m["volume"].values.astype(float) if "volume" in df_15m.columns else np.ones(n)
        high = df_15m["high"].values.astype(float)
        low = df_15m["low"].values.astype(float)
        close = df_15m["close"].values.astype(float)
        open_ = df_15m["open"].values.astype(float)

        avg_vol = float(np.mean(volume[-lookback - 1 : -1]))
        last = n - 1
        rng = high[last] - low[last]
        body = abs(close[last] - open_[last])
        body_ratio = body / rng if rng > 0 else 0.5
        vol_ratio = volume[last] / avg_vol if avg_vol > 0 else 1.0

        if vol_ratio >= self.p["absorption_vol_mult"] and body_ratio < self.p["absorption_body_max_ratio"]:
            close_pct = (close[last] - low[last]) / rng if rng > 0 else 0.5
            direction = "BULLISH" if close_pct > 0.5 else "BEARISH"
            return {
                "type": f"{direction}_ABSORPTION",
                "vol_ratio": round(float(vol_ratio), 2),
                "body_ratio": round(float(body_ratio), 3),
                "score": round(float(min(1.0, (vol_ratio - 1.5) / 2)), 3),
            }
        return None

    def _detect_sweep(self, df_15m):
        """
        Stop Avı (Sweep) Tespiti.
        Wick ile recent high/low kırılıp geri dönüş.
        Çok güçlü sinyal — kurumsal likidite toplama.
        """
        n = len(df_15m)
        lb = min(self.p["sweep_lookback"], n - 2)
        if lb < 5:
            return None

        high = df_15m["high"].values.astype(float)
        low = df_15m["low"].values.astype(float)
        close = df_15m["close"].values.astype(float)
        open_ = df_15m["open"].values.astype(float)

        recent_high = float(np.max(high[-lb - 1 : -1]))
        recent_low = float(np.min(low[-lb - 1 : -1]))

        last = n - 1
        last_rng = high[last] - low[last]
        if last_rng <= 0:
            return None

        min_wick = self.p["sweep_wick_min_ratio"]

        # Bullish sweep: wick below recent low, close above
        if low[last] < recent_low and close[last] > recent_low:
            lower_wick = min(open_[last], close[last]) - low[last]
            wick_ratio = lower_wick / last_rng
            if wick_ratio >= min_wick:
                return {
                    "type": "BULLISH_SWEEP",
                    "level": round(float(recent_low), 8),
                    "wick_ratio": round(float(wick_ratio), 3),
                    "score": round(float(min(1.0, wick_ratio)), 3),
                }

        # Bearish sweep: wick above recent high, close below
        elif high[last] > recent_high and close[last] < recent_high:
            upper_wick = high[last] - max(open_[last], close[last])
            wick_ratio = upper_wick / last_rng
            if wick_ratio >= min_wick:
                return {
                    "type": "BEARISH_SWEEP",
                    "level": round(float(recent_high), 8),
                    "wick_ratio": round(float(wick_ratio), 3),
                    "score": round(float(min(1.0, wick_ratio)), 3),
                }

        return None

    def analyze_orderflow(self, df_15m):
        """Tüm order flow bileşenlerini birleştir → bias + strength."""
        if df_15m is None or df_15m.empty or len(df_15m) < 20:
            return {
                "cvd": {"divergence": "NONE", "score": 0},
                "absorption": None,
                "sweep": None,
                "bias": "NEUTRAL",
                "strength": 0,
                "bull_points": 0,
                "bear_points": 0,
            }

        cvd_result = self._detect_cvd_divergence(df_15m)
        absorption = self._detect_absorption(df_15m)
        sweep = self._detect_sweep(df_15m)

        bull_points = 0.0
        bear_points = 0.0

        # CVD divergence
        if cvd_result["divergence"] == "BULLISH_DIVERGENCE":
            bull_points += 2
        elif cvd_result["divergence"] == "BEARISH_DIVERGENCE":
            bear_points += 2
        elif cvd_result["divergence"] == "CONFIRMING":
            if cvd_result["price_direction"] == "UP":
                bull_points += 1.5
            elif cvd_result["price_direction"] == "DOWN":
                bear_points += 1.5

        # Absorption
        if absorption:
            if "BULLISH" in absorption["type"]:
                bull_points += 1.5
            else:
                bear_points += 1.5

        # Sweep (strongest signal)
        if sweep:
            if "BULLISH" in sweep["type"]:
                bull_points += 3
            else:
                bear_points += 3

        total = bull_points + bear_points
        if bull_points > bear_points and bull_points >= 1.5:
            bias = "LONG"
            strength = bull_points / max(total, 1)
        elif bear_points > bull_points and bear_points >= 1.5:
            bias = "SHORT"
            strength = bear_points / max(total, 1)
        else:
            bias = "NEUTRAL"
            strength = 0

        return {
            "cvd": cvd_result,
            "absorption": absorption,
            "sweep": sweep,
            "bias": bias,
            "strength": round(float(strength), 3),
            "bull_points": round(float(bull_points), 1),
            "bear_points": round(float(bear_points), 1),
        }

    # ===========================================================
    #  3. MULTI-TF CONFLUENCE — 4H + 1H + 15M
    # ===========================================================

    def _calc_trend(self, df, lookback):
        """
        Indikatörsüz trend yönü:
        1. Linear regression slope (per-mille normalized)
        2. Bar consistency (% of bars in trend direction)
        3. HH/HL or LH/LL structure
        """
        n = len(df)
        if n < lookback:
            lookback = n
        if lookback < 6:
            return {"direction": "NEUTRAL", "strength": 0, "slope": 0, "consistency": 0.5}

        closes = df["close"].values[-lookback:].astype(float)
        highs = df["high"].values[-lookback:].astype(float)
        lows = df["low"].values[-lookback:].astype(float)

        # 1. Slope
        x = np.arange(lookback, dtype=float)
        slope = float(np.polyfit(x, closes, 1)[0])
        avg_p = float(np.mean(closes))
        norm_slope = slope / avg_p * 1000 if avg_p > 0 else 0

        # 2. Consistency
        returns = np.diff(closes)
        if norm_slope > 0:
            consistency = float(np.sum(returns > 0) / len(returns))
        elif norm_slope < 0:
            consistency = float(np.sum(returns < 0) / len(returns))
        else:
            consistency = 0.5

        # 3. Structure: divide into 3 segments, check HH/HL or LH/LL
        seg = max(2, lookback // 3)
        seg_count = min(3, lookback // seg)
        s_highs = [float(np.max(highs[i * seg : min((i + 1) * seg, lookback)])) for i in range(seg_count)]
        s_lows = [float(np.min(lows[i * seg : min((i + 1) * seg, lookback)])) for i in range(seg_count)]

        hh = sum(1 for i in range(1, seg_count) if s_highs[i] > s_highs[i - 1])
        hl = sum(1 for i in range(1, seg_count) if s_lows[i] > s_lows[i - 1])
        lh = sum(1 for i in range(1, seg_count) if s_highs[i] < s_highs[i - 1])
        ll = sum(1 for i in range(1, seg_count) if s_lows[i] < s_lows[i - 1])

        max_struct = max(1, seg_count - 1)

        bull_score = (1 if norm_slope > 0.3 else 0) * 35 + consistency * 35 + (hh / max_struct) * 15 + (hl / max_struct) * 15
        bear_score = (1 if norm_slope < -0.3 else 0) * 35 + consistency * 35 + (lh / max_struct) * 15 + (ll / max_struct) * 15

        if bull_score > 45:
            return {
                "direction": "BULLISH",
                "strength": round(min(float(bull_score) / 100, 1.0), 3),
                "slope": round(norm_slope, 3),
                "consistency": round(consistency, 3),
            }
        elif bear_score > 45:
            return {
                "direction": "BEARISH",
                "strength": round(min(float(bear_score) / 100, 1.0), 3),
                "slope": round(norm_slope, 3),
                "consistency": round(consistency, 3),
            }
        else:
            return {
                "direction": "NEUTRAL",
                "strength": 0,
                "slope": round(norm_slope, 3),
                "consistency": round(consistency, 3),
            }

    def analyze_confluence(self, df_4h, df_1h, df_15m):
        """
        Multi-TF yön uyumu.
        4H = makro trend, 1H = ara yön, 15M = giriş zamanlaması.
        min_confluence TF aynı yönde olursa sinyal.
        """
        t_4h = self._calc_trend(df_4h, self.p["trend_lookback_4h"]) if df_4h is not None and len(df_4h) >= 10 else {"direction": "NEUTRAL", "strength": 0, "slope": 0, "consistency": 0.5}
        t_1h = self._calc_trend(df_1h, self.p["trend_lookback_1h"]) if df_1h is not None and len(df_1h) >= 10 else {"direction": "NEUTRAL", "strength": 0, "slope": 0, "consistency": 0.5}
        t_15m = self._calc_trend(df_15m, self.p["trend_lookback_15m"]) if df_15m is not None and len(df_15m) >= 10 else {"direction": "NEUTRAL", "strength": 0, "slope": 0, "consistency": 0.5}

        directions = [t_4h["direction"], t_1h["direction"], t_15m["direction"]]
        bull_count = sum(1 for d in directions if d == "BULLISH")
        bear_count = sum(1 for d in directions if d == "BEARISH")

        min_conf = self.p["min_confluence"]

        if bull_count >= min_conf:
            conf_dir = "LONG"
            conf_count = bull_count
            strengths = [t["strength"] for t in [t_4h, t_1h, t_15m] if t["direction"] == "BULLISH"]
            avg_strength = float(np.mean(strengths)) if strengths else 0
        elif bear_count >= min_conf:
            conf_dir = "SHORT"
            conf_count = bear_count
            strengths = [t["strength"] for t in [t_4h, t_1h, t_15m] if t["direction"] == "BEARISH"]
            avg_strength = float(np.mean(strengths)) if strengths else 0
        else:
            conf_dir = "NEUTRAL"
            conf_count = 0
            avg_strength = 0

        return {
            "direction": conf_dir,
            "count": conf_count,
            "avg_strength": round(float(avg_strength), 3),
            "tf_4h": t_4h,
            "tf_1h": t_1h,
            "tf_15m": t_15m,
            "details": f"4H:{t_4h['direction']} 1H:{t_1h['direction']} 15M:{t_15m['direction']} → {conf_dir}({conf_count})",
        }

    # ===========================================================
    #  4. BTC CORRELATION FILTER
    # ===========================================================

    def calc_btc_correlation(self, df_symbol, df_btc, window=None):
        """Altcoin-BTC Pearson korelasyonu (return bazlı)."""
        if window is None:
            window = self.p["btc_corr_window"]
        if df_symbol is None or df_btc is None:
            return 0.5

        n1 = len(df_symbol)
        n2 = len(df_btc)
        usable = min(n1, n2, window + 1)
        if usable < 10:
            return 0.5

        c1 = df_symbol["close"].values[-usable:].astype(float)
        c2 = df_btc["close"].values[-usable:].astype(float)

        r1 = np.diff(np.log(c1 + 1e-10))
        r2 = np.diff(np.log(c2 + 1e-10))

        min_len = min(len(r1), len(r2))
        r1 = r1[-min_len:]
        r2 = r2[-min_len:]

        if np.std(r1) < 1e-10 or np.std(r2) < 1e-10:
            return 0.0

        corr = float(np.corrcoef(r1, r2)[0, 1])
        return round(max(-1.0, min(1.0, corr)), 3)

    def get_btc_trend(self, df_btc):
        """BTC makro trend yönü."""
        if df_btc is None or df_btc.empty or len(df_btc) < 15:
            return {"direction": "NEUTRAL", "strength": 0, "slope": 0, "consistency": 0.5}
        return self._calc_trend(df_btc, min(30, len(df_btc)))

    def check_btc_alignment(self, direction, btc_corr, btc_trend_dir):
        """
        BTC korelasyon filtresi:
        - Yüksek korelasyon + BTC ters yön → tehlike, sinyal RED
        - Düşük korelasyon → bağımsız hareket, bonus puan
        """
        high_corr = self.p["btc_corr_high"]

        if btc_corr >= high_corr:
            if btc_trend_dir == "BULLISH" and direction == "LONG":
                return {"aligned": True, "risk": "LOW", "score": 8}
            elif btc_trend_dir == "BEARISH" and direction == "SHORT":
                return {"aligned": True, "risk": "LOW", "score": 8}
            elif btc_trend_dir == "NEUTRAL":
                return {"aligned": True, "risk": "MEDIUM", "score": 5}
            else:
                return {"aligned": False, "risk": "HIGH", "score": 0}
        else:
            return {"aligned": True, "risk": "LOW", "score": 10}

    # ===========================================================
    #  4B. LIQUIDITY POOL DETECTION
    # ===========================================================

    def detect_liquidity_pools(self, df, tolerance_pct=0.0015, min_touches=2, lookback=50):
        """
        Likidite havuzlarını tespit et:
          - Eşit high/low kümeleri (stop hunt hedefleri)
          - Yuvarlak sayı seviyeleri
          - Fiyatın bu seviyelere olan mesafesi

        Returns: {
            pools: [{level, type, touches, distance_pct}],
            nearest_above: ...,
            nearest_below: ...,
            sweep_risk: "HIGH"/"MEDIUM"/"LOW"
        }
        """
        if df is None or df.empty or len(df) < 10:
            return {"pools": [], "nearest_above": None, "nearest_below": None, "sweep_risk": "LOW"}

        highs = df["high"].values.astype(float)[-lookback:]
        lows = df["low"].values.astype(float)[-lookback:]
        current = float(df["close"].iloc[-1])

        pools = []

        # ── Equal Highs ──
        for i in range(len(highs)):
            touches = 0
            for j in range(len(highs)):
                if i != j and abs(highs[i] - highs[j]) / highs[i] < tolerance_pct:
                    touches += 1
            if touches >= min_touches:
                level = float(highs[i])
                dist = (level - current) / current * 100
                if not any(abs(p["level"] - level) / level < tolerance_pct for p in pools):
                    pools.append({"level": round(level, 8), "type": "EQH", "touches": touches + 1,
                                  "distance_pct": round(dist, 3)})

        # ── Equal Lows ──
        for i in range(len(lows)):
            touches = 0
            for j in range(len(lows)):
                if i != j and abs(lows[i] - lows[j]) / lows[i] < tolerance_pct:
                    touches += 1
            if touches >= min_touches:
                level = float(lows[i])
                dist = (level - current) / current * 100
                if not any(abs(p["level"] - level) / level < tolerance_pct for p in pools):
                    pools.append({"level": round(level, 8), "type": "EQL", "touches": touches + 1,
                                  "distance_pct": round(dist, 3)})

        # ── Round Numbers ──
        price_magnitude = 10 ** max(0, int(np.log10(current)) - 1)
        round_levels = []
        base = int(current / price_magnitude) * price_magnitude
        for m in range(-3, 4):
            rl = base + m * price_magnitude
            if rl > 0:
                round_levels.append(rl)
            # Half levels too
            rl_half = base + m * price_magnitude + price_magnitude * 0.5
            if rl_half > 0:
                round_levels.append(rl_half)

        for rl in round_levels:
            dist = (rl - current) / current * 100
            if abs(dist) < 3.0:  # Within 3%
                pools.append({"level": round(float(rl), 8), "type": "ROUND", "touches": 0,
                              "distance_pct": round(dist, 3)})

        # Sort by distance
        pools.sort(key=lambda x: abs(x["distance_pct"]))

        # Nearest above/below
        above = [p for p in pools if p["distance_pct"] > 0.05]
        below = [p for p in pools if p["distance_pct"] < -0.05]
        nearest_above = above[0] if above else None
        nearest_below = below[0] if below else None

        # Sweep risk (how close is nearest EQH/EQL?)
        eq_pools = [p for p in pools if p["type"] in ("EQH", "EQL")]
        if eq_pools and abs(eq_pools[0]["distance_pct"]) < 0.5:
            sweep_risk = "HIGH"
        elif eq_pools and abs(eq_pools[0]["distance_pct"]) < 1.5:
            sweep_risk = "MEDIUM"
        else:
            sweep_risk = "LOW"

        return {
            "pools": pools[:10],  # Top 10 nearest
            "nearest_above": nearest_above,
            "nearest_below": nearest_below,
            "sweep_risk": sweep_risk,
            "count": len([p for p in pools if p["type"] in ("EQH", "EQL")]),
        }

    # ===========================================================
    #  4C. VOLUME PROFILE (VPOC / VAH / VAL)
    # ===========================================================

    def calc_volume_profile(self, df, bins=50, value_area_pct=0.70):
        """
        Hacim profili hesapla:
          VPOC (Volume Point of Control) — en yoğun işlem seviyesi
          VAH  (Value Area High) — hacmin %70'inin üst sınırı
          VAL  (Value Area Low)  — hacmin %70'inin alt sınırı

        Returns: {vpoc, vah, val, profile: [{price, volume}], position}
        """
        if df is None or df.empty or len(df) < 10:
            return {"vpoc": 0, "vah": 0, "val": 0, "profile": [], "position": "UNKNOWN"}

        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        closes = df["close"].values.astype(float)
        volumes = df["volume"].values.astype(float) if "volume" in df.columns else np.ones(len(df))

        price_min = float(np.min(lows))
        price_max = float(np.max(highs))

        if price_max <= price_min:
            return {"vpoc": float(closes[-1]), "vah": float(closes[-1]), "val": float(closes[-1]),
                    "profile": [], "position": "UNKNOWN"}

        # Create price bins
        bin_edges = np.linspace(price_min, price_max, bins + 1)
        bin_volumes = np.zeros(bins)

        # Distribute volume across price bins (using typical price range)
        for i in range(len(df)):
            low_i = lows[i]
            high_i = highs[i]
            vol_i = volumes[i]
            for b in range(bins):
                bin_low = bin_edges[b]
                bin_high = bin_edges[b + 1]
                # Overlap between candle range and bin
                overlap_low = max(low_i, bin_low)
                overlap_high = min(high_i, bin_high)
                if overlap_high > overlap_low:
                    candle_range = high_i - low_i if high_i > low_i else 1e-10
                    proportion = (overlap_high - overlap_low) / candle_range
                    bin_volumes[b] += vol_i * proportion

        # VPOC = bin with max volume
        vpoc_idx = int(np.argmax(bin_volumes))
        vpoc = float((bin_edges[vpoc_idx] + bin_edges[vpoc_idx + 1]) / 2)

        # Value Area (70% of total volume centered on VPOC)
        total_vol = float(np.sum(bin_volumes))
        target_vol = total_vol * value_area_pct

        # Expand from VPOC outward
        va_low_idx = vpoc_idx
        va_high_idx = vpoc_idx
        accumulated = float(bin_volumes[vpoc_idx])

        while accumulated < target_vol and (va_low_idx > 0 or va_high_idx < bins - 1):
            expand_low = bin_volumes[va_low_idx - 1] if va_low_idx > 0 else 0
            expand_high = bin_volumes[va_high_idx + 1] if va_high_idx < bins - 1 else 0

            if expand_low >= expand_high and va_low_idx > 0:
                va_low_idx -= 1
                accumulated += expand_low
            elif va_high_idx < bins - 1:
                va_high_idx += 1
                accumulated += expand_high
            else:
                va_low_idx -= 1
                accumulated += expand_low

        val = float(bin_edges[va_low_idx])
        vah = float(bin_edges[va_high_idx + 1])

        # Current price position relative to value area
        current = float(closes[-1])
        if current > vah:
            position = "ABOVE_VA"  # Potential short (unfair high)
        elif current < val:
            position = "BELOW_VA"  # Potential long (unfair low)
        else:
            position = "INSIDE_VA"  # Fair value, wait for breakout

        # Build simplified profile for frontend
        profile = []
        max_bin_vol = float(np.max(bin_volumes)) if total_vol > 0 else 1
        for b in range(bins):
            if bin_volumes[b] > total_vol * 0.005:  # Only significant bins
                profile.append({
                    "price": round(float((bin_edges[b] + bin_edges[b + 1]) / 2), 8),
                    "volume_pct": round(float(bin_volumes[b] / max_bin_vol * 100), 1),
                })

        return {
            "vpoc": round(vpoc, 8),
            "vah": round(vah, 8),
            "val": round(val, 8),
            "position": position,
            "profile": profile[:20],  # Top 20 for frontend
            "distance_to_vpoc_pct": round((current - vpoc) / vpoc * 100, 3) if vpoc > 0 else 0,
        }

    # ===========================================================
    #  4D. FUNDING RATE + OPEN INTEREST SENTIMENT
    # ===========================================================

    def analyze_funding_oi(self, symbol, funding_data, oi_data_current, oi_data_prev=None):
        """
        Funding rate + OI sentiment analizi:
          - Extreme funding → crowded trade, fade it
          - OI rising + price rising → trend confirmation
          - OI rising + price falling → potential squeeze
          - OI falling → exit/unwinding

        Args:
            symbol: Trading pair
            funding_data: from data_fetcher.get_funding_rate()
            oi_data_current: from data_fetcher.get_open_interest()
            oi_data_prev: previous OI snapshot (optional, for delta)

        Returns: {sentiment, funding_extreme, oi_trend, squeeze_risk, score_adj}
        """
        result = {
            "sentiment": "NEUTRAL",
            "funding_rate": 0,
            "funding_extreme": False,
            "oi_usdt": 0,
            "oi_change_pct": 0,
            "squeeze_risk": "LOW",
            "score_adj": 0,  # -10 to +10 score adjustment
        }

        if not funding_data:
            return result

        fr = funding_data.get("current", 0)  # Already in %
        result["funding_rate"] = round(fr, 4)

        # Extreme funding detection (> 0.03% or < -0.03% per 8h = very high)
        if abs(fr) > 0.05:
            result["funding_extreme"] = True
            # Extreme positive = too many longs → fade (SHORT bias)
            # Extreme negative = too many shorts → fade (LONG bias)
            result["sentiment"] = "BEARISH" if fr > 0 else "BULLISH"
            result["score_adj"] = -5  # Contrarian signal, adds caution
            result["squeeze_risk"] = "HIGH"
        elif abs(fr) > 0.02:
            result["sentiment"] = "BEARISH" if fr > 0 else "BULLISH"
            result["score_adj"] = 3  # Mild contrarian opportunity
            result["squeeze_risk"] = "MEDIUM"
        else:
            result["sentiment"] = "NEUTRAL"
            result["score_adj"] = 0

        # OI Analysis
        if oi_data_current:
            result["oi_usdt"] = oi_data_current.get("oi_usdt", 0)

        return result

    def check_funding_alignment(self, direction, funding_oi):
        """
        Funding rate alignment check:
          - LONG sinyal + extreme positive funding → risk (herkes long)
          - SHORT sinyal + extreme negative funding → risk (herkes short)
          - Karşı taraf → potansiyel squeeze avantajı
        """
        if not funding_oi or not funding_oi.get("funding_extreme"):
            return {"aligned": True, "risk": "LOW", "score": 5}

        sentiment = funding_oi.get("sentiment", "NEUTRAL")

        # Going WITH the crowd into extreme = dangerous
        if (direction == "LONG" and sentiment == "BULLISH") or \
           (direction == "SHORT" and sentiment == "BEARISH"):
            # We're fading the crowd — good
            return {"aligned": True, "risk": "LOW", "score": 8}
        elif (direction == "LONG" and sentiment == "BEARISH") or \
             (direction == "SHORT" and sentiment == "BULLISH"):
            # We're going with the crowd which is extreme — risky
            return {"aligned": False, "risk": "HIGH", "score": 0}
        else:
            return {"aligned": True, "risk": "MEDIUM", "score": 5}

    # ===========================================================
    #  4E. WYCKOFF ACCUMULATION / DISTRIBUTION
    # ===========================================================

    def detect_wyckoff(self, df, lookback=40):
        """
        Wyckoff Spring/Upthrust tespiti:
          - Spring: Dip fiyat destek kırıyor ama hızla geri dönüyor (LONG)
          - Upthrust: Fiyat direnç kırıyor ama hızla geri dönüyor (SHORT)
          - Accumulation: Dar range + düşen hacim + spring = breakout LONG
          - Distribution: Dar range + düşen hacim + upthrust = breakdown SHORT

        Returns: {
            phase: "ACCUMULATION"/"DISTRIBUTION"/"MARKUP"/"MARKDOWN"/"NONE",
            spring: bool, upthrust: bool,
            range_tightening: bool,
            volume_declining: bool,
            signal: "LONG"/"SHORT"/"NEUTRAL",
            strength: 0.0-1.0
        }
        """
        if df is None or df.empty or len(df) < lookback:
            return {"phase": "NONE", "spring": False, "upthrust": False,
                    "range_tightening": False, "volume_declining": False,
                    "signal": "NEUTRAL", "strength": 0}

        highs = df["high"].values.astype(float)[-lookback:]
        lows = df["low"].values.astype(float)[-lookback:]
        closes = df["close"].values.astype(float)[-lookback:]
        opens = df["open"].values.astype(float)[-lookback:]
        volumes = df["volume"].values.astype(float)[-lookback:] if "volume" in df.columns else np.ones(lookback)

        current = closes[-1]
        n = len(closes)
        half = n // 2

        # ── Range Analysis ──
        recent_range = float(np.mean(highs[-10:] - lows[-10:]))
        older_range = float(np.mean(highs[:half] - lows[:half]))
        range_tightening = recent_range < older_range * 0.7

        # ── Volume Analysis ──
        recent_vol = float(np.mean(volumes[-10:]))
        older_vol = float(np.mean(volumes[:half]))
        volume_declining = recent_vol < older_vol * 0.75

        # ── Support / Resistance ──
        support = float(np.min(lows[-20:]))
        resistance = float(np.max(highs[-20:]))
        mid_range = (support + resistance) / 2

        # ── Spring Detection (last 5 bars) ──
        spring = False
        for i in range(-5, 0):
            if i + n < 0:
                continue
            idx = n + i
            if lows[idx] < support and closes[idx] > support:
                # Wick below support but closed above = spring
                body = abs(closes[idx] - opens[idx])
                lower_wick = min(opens[idx], closes[idx]) - lows[idx]
                if lower_wick > body * 1.5:
                    spring = True
                    break

        # ── Upthrust Detection (last 5 bars) ──
        upthrust = False
        for i in range(-5, 0):
            if i + n < 0:
                continue
            idx = n + i
            if highs[idx] > resistance and closes[idx] < resistance:
                body = abs(closes[idx] - opens[idx])
                upper_wick = highs[idx] - max(opens[idx], closes[idx])
                if upper_wick > body * 1.5:
                    upthrust = True
                    break

        # ── Phase Classification ──
        phase = "NONE"
        signal = "NEUTRAL"
        strength = 0.0

        if range_tightening and volume_declining:
            if current < mid_range:
                phase = "ACCUMULATION"
                if spring:
                    signal = "LONG"
                    strength = 0.9
                else:
                    signal = "LONG"
                    strength = 0.5
            else:
                phase = "DISTRIBUTION"
                if upthrust:
                    signal = "SHORT"
                    strength = 0.9
                else:
                    signal = "SHORT"
                    strength = 0.5
        elif spring:
            phase = "ACCUMULATION"
            signal = "LONG"
            strength = 0.7
        elif upthrust:
            phase = "DISTRIBUTION"
            signal = "SHORT"
            strength = 0.7
        else:
            # Check if in markup or markdown
            slope_20 = (closes[-1] - closes[-20]) / closes[-20] * 100 if len(closes) >= 20 else 0
            if slope_20 > 3:
                phase = "MARKUP"
                signal = "LONG"
                strength = 0.4
            elif slope_20 < -3:
                phase = "MARKDOWN"
                signal = "SHORT"
                strength = 0.4

        return {
            "phase": phase,
            "spring": spring,
            "upthrust": upthrust,
            "range_tightening": range_tightening,
            "volume_declining": volume_declining,
            "signal": signal,
            "strength": round(strength, 2),
            "support": round(float(support), 8),
            "resistance": round(float(resistance), 8),
        }

    # ===========================================================
    #  5. SCORING ENGINE (v2.1 — expanded 120pt → normalized to 100)
    # ===========================================================

    def _calc_signal_score(self, regime, orderflow, confluence, btc_alignment,
                           liquidity=None, volume_profile=None, funding_oi=None, wyckoff=None):
        """
        0–100 bileşik sinyal skoru (v2.1 genişletilmiş).

        Max breakdown (raw → her biri normalize edilir):
          Regime:        20pt
          Confluence:    20pt
          OrderFlow:     15pt
          BTC:            8pt
          Session:        7pt
          Liquidity:      8pt  (NEW)
          Volume Profile: 8pt  (NEW)
          Funding/OI:     7pt  (NEW)
          Wyckoff:        7pt  (NEW)
          ──────────────────
          TOTAL:        100pt
        """
        score = 0.0

        # ── Regime (0-20) ──
        r = regime.get("regime", "UNKNOWN")
        if "TREND" in r:
            score += 17 + regime.get("efficiency", 0) * 3
        elif r == "MEAN_REVERT":
            score += 12
        elif r == "TRANSITION":
            score += 6
        elif r == "CHAOS":
            score += 0
        else:
            score += 4

        # ── Confluence (0-20) ──
        conf_count = confluence.get("count", 0)
        conf_strength = confluence.get("avg_strength", 0)
        if conf_count >= 3:
            score += 17 + conf_strength * 3
        elif conf_count == 2:
            score += 12 + conf_strength * 3
        elif conf_count == 1:
            score += 7

        # ── OrderFlow (0-15) ──
        if orderflow.get("sweep"):
            score += 7
        if orderflow.get("absorption"):
            score += 3
        cvd = orderflow.get("cvd", {})
        if cvd.get("divergence") == "CONFIRMING":
            score += 3
        elif "DIVERGENCE" in cvd.get("divergence", ""):
            score += 5
        score += min(orderflow.get("strength", 0) * 3, 3)

        # ── BTC (0-8) ──
        btc_score = btc_alignment.get("score", 5)
        score += min(btc_score, 8)

        # ── Session (0-7) ──
        session = self.get_current_session()
        session_scores = {"LONDON_NY": 7, "LONDON": 6, "NEW_YORK": 5, "ASIA": 3}
        score += session_scores.get(session, 4)

        # ── Liquidity Pool (0-8) ── NEW
        if liquidity:
            eq_count = liquidity.get("count", 0)
            sweep_risk = liquidity.get("sweep_risk", "LOW")
            if sweep_risk == "HIGH" and eq_count >= 2:
                score += 8  # Very close to liquidity pool → high probability setup
            elif sweep_risk == "MEDIUM":
                score += 5
            elif eq_count >= 1:
                score += 3

        # ── Volume Profile (0-8) ── NEW
        if volume_profile:
            position = volume_profile.get("position", "UNKNOWN")
            vpoc_dist = abs(volume_profile.get("distance_to_vpoc_pct", 99))
            if position == "BELOW_VA":
                score += 6  # Below value → potential long value
            elif position == "ABOVE_VA":
                score += 6  # Above value → potential short value
            elif position == "INSIDE_VA" and vpoc_dist < 0.3:
                score += 3  # Near VPOC → wait
            # Extra for clear VA positioning
            if vpoc_dist > 1.0:
                score += 2

        # ── Funding/OI (0-7) ── NEW
        if funding_oi:
            score += max(-5, min(7, funding_oi.get("score_adj", 0) + 2))

        # ── Wyckoff (0-7) ── NEW
        if wyckoff:
            w_strength = wyckoff.get("strength", 0)
            if wyckoff.get("spring") or wyckoff.get("upthrust"):
                score += 7  # Clear Wyckoff pattern
            elif wyckoff.get("range_tightening") and wyckoff.get("volume_declining"):
                score += 5  # Accumulation/Distribution forming
            elif w_strength >= 0.4:
                score += 3

        return round(min(100, max(0, score)), 1)

    # ===========================================================
    #  6. RISK ENGINE — Dynamic SL / 3-Level TP / Session
    # ===========================================================

    def get_current_session(self):
        """Mevcut trading session (UTC)."""
        hour = datetime.now(timezone.utc).hour
        if 13 <= hour < 16:
            return "LONDON_NY"
        elif 8 <= hour < 16:
            return "LONDON"
        elif 13 <= hour < 21:
            return "NEW_YORK"
        else:
            return "ASIA"

    def calc_dynamic_risk(self, df_15m, direction, entry_price, regime):
        """
        Rejim-bazlı dinamik SL/TP.
        TREND → geniş TP, normal SL
        MEAN_REVERT → dar TP, dar SL

        Returns: {sl, tp1, tp2, tp3, rr_ratio, atr, session, session_mult, tp_mult}
        """
        if df_15m is None or df_15m.empty or len(df_15m) < 15:
            return None

        highs = df_15m["high"].values.astype(float)
        lows = df_15m["low"].values.astype(float)
        closes = df_15m["close"].values.astype(float)

        atr = self._calc_atr(highs, lows, closes, 14)
        if atr <= 0 or entry_price <= 0:
            return None

        # Session multiplier
        session = self.get_current_session()
        session_mult = 1.0
        if self.p.get("session_adjust"):
            mults = {"ASIA": 0.85, "LONDON": 1.0, "LONDON_NY": 1.1, "NEW_YORK": 1.05}
            session_mult = mults.get(session, 1.0)

        # Regime multiplier for TP
        regime_type = regime.get("regime", "UNKNOWN") if isinstance(regime, dict) else "UNKNOWN"
        tp_mult = 1.0
        if "TREND" in regime_type:
            tp_mult = 1.2
        elif regime_type == "MEAN_REVERT":
            tp_mult = 0.8

        sl_dist = atr * self.p["sl_atr_mult"] * session_mult
        tp1_dist = sl_dist * self.p["tp1_rr"] * tp_mult
        tp2_dist = sl_dist * self.p["tp2_rr"] * tp_mult
        tp3_dist = sl_dist * self.p["tp3_rr"] * tp_mult

        if direction == "LONG":
            sl = entry_price - sl_dist
            tp1 = entry_price + tp1_dist
            tp2 = entry_price + tp2_dist
            tp3 = entry_price + tp3_dist
        else:
            sl = entry_price + sl_dist
            tp1 = entry_price - tp1_dist
            tp2 = entry_price - tp2_dist
            tp3 = entry_price - tp3_dist

        risk = abs(entry_price - sl)
        reward = abs(tp3 - entry_price)
        rr = reward / risk if risk > 0 else 0

        # Sanity
        if direction == "LONG" and (sl >= entry_price or tp1 <= entry_price):
            return None
        if direction == "SHORT" and (sl <= entry_price or tp1 >= entry_price):
            return None

        return {
            "sl": round(float(sl), 8),
            "tp1": round(float(tp1), 8),
            "tp2": round(float(tp2), 8),
            "tp3": round(float(tp3), 8),
            "rr_ratio": round(float(rr), 2),
            "atr": round(float(atr), 8),
            "session": session,
            "session_mult": round(session_mult, 2),
            "tp_mult": round(tp_mult, 2),
        }

    # ===========================================================
    #  7. QUICK SCREEN (Two-Pass Scan)
    # ===========================================================

    def quick_screen(self, df_15m):
        """
        Hızlı ön eleme (sadece 15m verisi).
        True dönerse, coin deep analysis'e geçer.
        """
        if df_15m is None or df_15m.empty or len(df_15m) < 30:
            return False

        closes = df_15m["close"].values.astype(float)
        volumes = df_15m["volume"].values.astype(float) if "volume" in df_15m.columns else np.ones(len(df_15m))
        ranges = df_15m["high"].values.astype(float) - df_15m["low"].values.astype(float)

        # Recent 5 bars vs last 30 bars
        recent_range = float(np.mean(ranges[-5:]))
        med_range = float(np.median(ranges[-30:]))

        recent_vol = float(np.mean(volumes[-5:]))
        med_vol = float(np.median(volumes[-30:]))

        pct_change = abs(closes[-1] - closes[-5]) / closes[-5] if closes[-5] > 0 else 0

        # Pass if there's SOME activity
        vol_ok = recent_range >= med_range * 0.85 or recent_vol >= med_vol * 0.9
        move_ok = pct_change > 0.001

        return vol_ok and move_ok

    # ===========================================================
    #  8. MAIN SIGNAL GENERATOR
    # ===========================================================

    def generate_signal(self, symbol, df_15m, df_1h=None, df_4h=None, df_btc_1h=None,
                        funding_data=None, oi_data=None):
        """
        Ana sinyal üretici — tüm 10 bileşeni birleştirir (v2.1).

        Args:
            symbol:       Trading pair (e.g. "BTC-USDT-SWAP")
            df_15m:       15-minute candles (required, ≥30 bars)
            df_1h:        1-hour candles (strongly recommended)
            df_4h:        4-hour candles (recommended)
            df_btc_1h:    BTC 1H candles (for correlation filter)
            funding_data: from data_fetcher.get_funding_rate() (optional)
            oi_data:      from data_fetcher.get_open_interest() (optional)

        Returns: signal dict or None
        """
        if df_15m is None or df_15m.empty or len(df_15m) < 30:
            return None

        entry_price = float(df_15m["close"].iloc[-1])

        # ═══ 1. Regime ═══
        regime = (
            self.detect_regime(df_1h)
            if df_1h is not None and len(df_1h) >= 20
            else {"regime": "UNKNOWN", "hurst": 0.5, "efficiency": 0.5, "direction": "NEUTRAL", "slope": 0, "atr": 0}
        )

        # CHAOS rejiminde sinyal üretme
        if regime["regime"] == "CHAOS":
            return None

        # ═══ 2. Order Flow ═══
        orderflow = self.analyze_orderflow(df_15m)

        # ═══ 3. Multi-TF Confluence ═══
        confluence = self.analyze_confluence(df_4h, df_1h, df_15m)

        # ═══ 4a. BTC Correlation ═══
        btc_corr = self.calc_btc_correlation(df_15m, df_btc_1h) if df_btc_1h is not None else 0.5
        btc_trend = self.get_btc_trend(df_btc_1h) if df_btc_1h is not None else {"direction": "NEUTRAL", "strength": 0}

        # ═══ 4b. Liquidity Pools ═══
        liquidity = self.detect_liquidity_pools(df_15m)

        # ═══ 4c. Volume Profile ═══
        volume_profile = self.calc_volume_profile(df_1h if df_1h is not None and len(df_1h) >= 20 else df_15m)

        # ═══ 4d. Funding + OI ═══
        funding_oi = self.analyze_funding_oi(symbol, funding_data, oi_data) if funding_data else None

        # ═══ 4e. Wyckoff ═══
        wyckoff = self.detect_wyckoff(df_1h if df_1h is not None and len(df_1h) >= 40 else df_15m)

        # ═══ Direction Decision ═══
        # Priority: Confluence > OrderFlow > Wyckoff > Regime
        direction = None

        if confluence["direction"] in ("LONG", "SHORT"):
            candidate_dir = confluence["direction"]
            of_bias = orderflow.get("bias", "NEUTRAL")
            opposing = (candidate_dir == "LONG" and of_bias == "SHORT") or (
                candidate_dir == "SHORT" and of_bias == "LONG"
            )

            if not opposing:
                direction = candidate_dir
            elif orderflow.get("strength", 0) < 0.5:
                if confluence["count"] >= 2:
                    direction = candidate_dir

        # OrderFlow can override if confluence is neutral
        if direction is None and orderflow["bias"] in ("LONG", "SHORT"):
            if orderflow.get("strength", 0) >= 0.6:
                direction = orderflow["bias"]

        # Wyckoff spring/upthrust can provide direction when others are neutral
        if direction is None and wyckoff.get("signal") in ("LONG", "SHORT"):
            if wyckoff.get("strength", 0) >= 0.7:
                direction = wyckoff["signal"]

        if direction is None:
            return None

        # ═══ BTC Filter ═══
        btc_alignment = self.check_btc_alignment(direction, btc_corr, btc_trend.get("direction", "NEUTRAL"))
        if not btc_alignment["aligned"]:
            return None

        # ═══ Funding Filter ═══
        if funding_oi:
            funding_align = self.check_funding_alignment(direction, funding_oi)
            if not funding_align["aligned"]:
                return None

        # ═══ Score (expanded v2.1) ═══
        score = self._calc_signal_score(
            regime, orderflow, confluence, btc_alignment,
            liquidity=liquidity, volume_profile=volume_profile,
            funding_oi=funding_oi, wyckoff=wyckoff
        )
        if score < self.p["min_signal_score"]:
            return None

        # ═══ Risk Calculation ═══
        risk = self.calc_dynamic_risk(df_15m, direction, entry_price, regime)
        if risk is None:
            return None

        # ═══ BUILD SIGNAL ═══
        signal = {
            "symbol": symbol,
            "direction": direction,
            "entry": round(entry_price, 8),
            "sl": risk["sl"],
            "tp": risk["tp3"],
            "tp1": risk["tp1"],
            "tp2": risk["tp2"],
            "tp3": risk["tp3"],
            "rr_ratio": risk["rr_ratio"],
            "score": score,
            "mode": self.mode,
            "regime": regime["regime"],
            "hurst": regime.get("hurst", 0.5),
            "efficiency": regime.get("efficiency", 0.5),
            "impulse_score": orderflow.get("cvd", {}).get("cvd_normalized", 0),
            "velocity": regime.get("slope", 0),
            "btc_correlation": btc_corr,
            "confluence_count": confluence["count"],
            "orderflow_bias": orderflow["bias"],
            "wyckoff_phase": wyckoff.get("phase", "NONE"),
            "funding_rate": funding_oi.get("funding_rate", 0) if funding_oi else 0,
            "vpoc": volume_profile.get("vpoc", 0),
            "session": risk["session"],
            "components": {
                "regime": regime,
                "orderflow": orderflow,
                "confluence": confluence,
                "btc": {"correlation": btc_corr, "trend": btc_trend, "alignment": btc_alignment},
                "liquidity": liquidity,
                "volume_profile": volume_profile,
                "funding_oi": funding_oi,
                "wyckoff": wyckoff,
                "risk": risk,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        wk_tag = f" | Wyckoff: {wyckoff.get('phase', '-')}" if wyckoff.get("phase", "NONE") != "NONE" else ""
        fr_tag = f" | FR: {funding_oi.get('funding_rate', 0):.3f}%" if funding_oi else ""

        logger.info(
            f"🚀 AME v2.1 SİNYAL: {symbol} {direction} | Entry: {entry_price:.4f} | "
            f"SL: {risk['sl']:.4f} | TP1: {risk['tp1']:.4f} TP2: {risk['tp2']:.4f} TP3: {risk['tp3']:.4f} | "
            f"RR: {risk['rr_ratio']:.1f} | Score: {score:.0f} | Regime: {regime['regime']} | "
            f"Conf: {confluence['count']}/3 | BTC.corr: {btc_corr:.2f}{wk_tag}{fr_tag}"
        )

        return _to_native(signal)

    # ===========================================================
    #  9. ANALYZE (API endpoint)
    # ===========================================================

    def analyze(self, symbol, df_15m, df_1h=None, df_4h=None, df_btc_1h=None,
                funding_data=None, oi_data=None):
        """Tam analiz (sinyal üretmeden tüm bileşenleri döndür) — v2.1."""
        result = {
            "symbol": symbol,
            "mode": self.mode,
            "current_price": float(df_15m["close"].iloc[-1]) if df_15m is not None and len(df_15m) > 0 else 0,
        }

        # Regime
        if df_1h is not None and len(df_1h) >= 20:
            result["regime"] = self.detect_regime(df_1h)
        else:
            result["regime"] = {"regime": "UNKNOWN", "hurst": 0.5, "efficiency": 0.5, "details": "1H veri yok"}

        # Order Flow
        result["orderflow"] = self.analyze_orderflow(df_15m) if df_15m is not None and len(df_15m) >= 20 else {}

        # Confluence
        result["confluence"] = self.analyze_confluence(df_4h, df_1h, df_15m)

        # BTC
        if df_btc_1h is not None:
            btc_corr = self.calc_btc_correlation(df_15m, df_btc_1h) if df_15m is not None else 0.5
            btc_trend = self.get_btc_trend(df_btc_1h)
            result["btc"] = {"correlation": btc_corr, "trend": btc_trend}
        else:
            result["btc"] = {"correlation": 0.5, "trend": {"direction": "NEUTRAL", "strength": 0}}

        # Liquidity Pools (NEW)
        result["liquidity"] = self.detect_liquidity_pools(df_15m) if df_15m is not None else {}

        # Volume Profile (NEW)
        vp_df = df_1h if df_1h is not None and len(df_1h) >= 20 else df_15m
        result["volume_profile"] = self.calc_volume_profile(vp_df) if vp_df is not None else {}

        # Funding + OI (NEW)
        result["funding_oi"] = self.analyze_funding_oi(symbol, funding_data, oi_data) if funding_data else {
            "sentiment": "NEUTRAL", "funding_rate": 0, "funding_extreme": False, "squeeze_risk": "LOW"
        }

        # Wyckoff (NEW)
        wk_df = df_1h if df_1h is not None and len(df_1h) >= 40 else df_15m
        result["wyckoff"] = self.detect_wyckoff(wk_df) if wk_df is not None else {}

        # Session
        result["session"] = self.get_current_session()

        # Score (use confluence direction as candidate)
        conf_dir = result["confluence"].get("direction", "NEUTRAL")
        if conf_dir in ("LONG", "SHORT"):
            btc_align = self.check_btc_alignment(
                conf_dir,
                result["btc"]["correlation"],
                result["btc"]["trend"].get("direction", "NEUTRAL"),
            )
            result["score"] = self._calc_signal_score(
                result["regime"], result.get("orderflow", {}), result["confluence"], btc_align,
                liquidity=result.get("liquidity"),
                volume_profile=result.get("volume_profile"),
                funding_oi=result.get("funding_oi"),
                wyckoff=result.get("wyckoff"),
            )
        else:
            result["score"] = 0

        return _to_native(result)


# ═══ Global Instance ═══
ame_strategy = AMEStrategyV2("balanced")
