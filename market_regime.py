"""
Market Regime Engine — Piyasa Rejimi, Piyasa Nabzı ve Rölatif Güç Analizi
==========================================================================
KATMAN 0: Piyasa nabzı (Altcoin sağlığı, Fear&Greed proxy, genel yorum)
KATMAN 1: Makro rejim tespiti (BTC trend + BTC.D proxy + USDT.D proxy)
KATMAN 2: Rölatif güç sıralaması (her coin vs BTC)
KATMAN 3: Fırsat filtreleme (Top adaylar → ICT'ye gönder)

OKX Public API kullanır, API key gerekmez.
"""

import logging
import time
import numpy as np
from data_fetcher import data_fetcher
from config import INST_TYPE

logger = logging.getLogger("ICT-Bot.Regime")

# ─────────────────────────────────────────────────────
# REGIME CONFIG
# ─────────────────────────────────────────────────────
REGIME_CONFIG = {
    # BTC trend tespiti
    "btc_trend_fast_period": 8,      # Hızlı EMA (kısa vadeli yön)
    "btc_trend_slow_period": 21,     # Yavaş EMA (orta vadeli yön)
    "btc_trend_lookback": 6,         # Son N mumu kontrol et (momentum)

    # BTC.D proxy (BTC vs Altcoin performans farkı)
    "btc_d_lookback_candles": 16,    # Son N mum karşılaştırma (4H → 16 mum = ~2.5 gün)
    "btc_d_threshold": 1.5,          # BTC %1.5 daha iyi → dominans artıyor

    # USDT.D proxy (Hacim + fiyat yönü)
    "usdt_d_volume_drop_pct": -15,   # Ortalamadan %15 düşüş → para çıkıyor
    "usdt_d_volume_surge_pct": 20,   # Ortalamadan %20 artış → ilgi artıyor

    # Rölatif Güç
    "rs_periods": [4, 16, 48],       # 1h, 4h, 12h (15m mumları bazında)
    "rs_weights": [0.5, 0.3, 0.2],   # Kısa vade ağırlıklı
    "rs_min_candles": 50,            # Minimum mum sayısı

    # Fırsat filtreleme
    "max_long_candidates": 3,        # Max LONG aday
    "max_short_candidates": 3,       # Max SHORT aday
    "rs_long_threshold": 0.5,        # RS > 0.5 = güçlü (LONG aday)
    "rs_short_threshold": -0.5,      # RS < -0.5 = zayıf (SHORT aday)
    "min_volume_confirmation": 0.8,  # Hacim ortalamanın %80'i üstünde olmalı

    # Rejim geçerlilik
    "regime_cache_seconds": 120,     # Rejim tespiti 2dk cache

    # Volatilite rejimi
    "atr_period": 14,                # ATR hesaplama periyodu
    "vol_high_threshold": 1.5,       # ATR > ortalamanın 1.5 katı → yüksek volatilite
    "vol_low_threshold": 0.6,        # ATR < ortalamanın 0.6 katı → düşük volatilite

    # Altcoin endeks proxy
    "large_cap_alts": ["ETH", "SOL", "XRP", "ADA", "AVAX", "DOT", "LINK", "MATIC"],

    # Fear & Greed proxy eşikler
    "fg_extreme_fear": 20,
    "fg_fear": 40,
    "fg_greed": 60,
    "fg_extreme_greed": 80,
}

# Rejim tipleri
REGIME_RISK_ON = "RISK_ON"           # BTC↑ + Altlar güçlü → LONG fırsatları
REGIME_RISK_OFF = "RISK_OFF"         # BTC↓ + Altlar zayıf → SHORT fırsatları
REGIME_ALT_SEASON = "ALT_SEASON"     # Altlar BTC'den iyi → Altcoin LONG
REGIME_CAPITULATION = "CAPITULATION" # Her şey düşüyor, altlar çöküyor → Dikkatli SHORT
REGIME_NEUTRAL = "NEUTRAL"           # Belirsiz → Coin bazlı fırsat ara


class MarketRegime:
    """Piyasa rejimi tespit ve rölatif güç sıralama motoru"""

    def __init__(self):
        self._regime_cache = None
        self._regime_ts = 0

    @staticmethod
    def _btc_symbol():
        """Aktif enstrüman tipine göre BTC sembolü döndür"""
        return "BTC-USDT-SWAP" if INST_TYPE == "SWAP" else "BTC-USDT"

    @staticmethod
    def _is_btc(symbol):
        """Sembol BTC mi kontrol et"""
        return symbol in ("BTC-USDT", "BTC-USDT-SWAP")

    # ═════════════════════════════════════════════════
    # ANA FONKSİYON: Tam analiz döngüsü
    # ═════════════════════════════════════════════════
    def analyze_market(self, coin_list):
        """
        Tam piyasa analizi yap. scan_markets() her çalıştığında çağrılır.

        Returns:
            dict: {
                "regime": str,                # RISK_ON / RISK_OFF / ALT_SEASON / CAPITULATION / NEUTRAL
                "regime_details": dict,        # BTC trend, BTC.D, USDT.D detayları
                "btc_bias": "LONG" | "SHORT" | "NEUTRAL",
                "rs_rankings": list[dict],     # Tüm coinler RS skoru ile sıralı
                "long_candidates": list[str],  # LONG aday coinler (en güçlüler)
                "short_candidates": list[str], # SHORT aday coinler (en zayıflar)
                "filtered_coins": dict,        # {symbol: {"allowed_directions": ["LONG"], "rs_score": 2.1, ...}}
            }
        """
        now = time.time()
        cfg = REGIME_CONFIG

        # ── Cache kontrol — 120s içinde tekrar hesaplama ──
        if (self._regime_cache is not None
                and now - self._regime_ts < cfg["regime_cache_seconds"]):
            logger.debug("Rejim cache geçerli, tekrar hesaplanmıyor")
            return self._regime_cache

        # ── 1. BTC verilerini çek ──
        btc_symbol = self._btc_symbol()
        btc_4h = data_fetcher.get_candles(btc_symbol, "4H", 100)
        btc_1h = data_fetcher.get_candles(btc_symbol, "1H", 100)
        btc_15m = data_fetcher.get_candles(btc_symbol, "15m", 100)

        if btc_15m is None or len(btc_15m) < cfg["rs_min_candles"]:
            logger.warning("BTC verisi yetersiz, rejim tespiti yapılamıyor")
            return self._neutral_result(coin_list)

        # ── 2. BTC Trend Analizi ──
        btc_trend = self._analyze_btc_trend(btc_4h, btc_1h, btc_15m)

        # ── 3. BTC Dominans Proxy ──
        btc_d_signal = self._analyze_btc_dominance_proxy(btc_15m, coin_list)

        # ── 4. USDT.D Proxy (hacim bazlı para akışı) ──
        usdt_d_signal = self._analyze_usdt_flow_proxy(btc_15m, coin_list)

        # ── 5. Volatilite Durumu ──
        volatility = self._analyze_volatility(btc_4h, btc_15m)

        # ── 6. Rejim Tespiti ──
        regime = self._determine_regime(btc_trend, btc_d_signal, usdt_d_signal)

        # ── 7. Rölatif Güç Hesaplama ──
        rs_rankings = self._calculate_all_relative_strength(btc_15m, coin_list)

        # ── 8. Fırsat Filtreleme ──
        long_candidates, short_candidates = self._filter_opportunities(
            rs_rankings, regime
        )

        # ── 9. Her coin için izin verilen yönleri belirle ──
        filtered_coins = self._build_filtered_map(
            coin_list, rs_rankings, long_candidates, short_candidates, regime
        )

        # ── 10. Altcoin piyasa sağlığı (TOTAL2/3/OTHERS proxy) ──
        altcoin_health = self._analyze_altcoin_health(rs_rankings, coin_list, btc_15m)

        # ── 11. Fear & Greed proxy ──
        fear_greed = self._calculate_fear_greed(
            btc_trend, usdt_d_signal, volatility, rs_rankings, altcoin_health
        )

        # ── 12. Piyasa yorumu ──
        market_commentary = self._generate_market_commentary(
            regime, btc_trend, btc_d_signal, usdt_d_signal,
            volatility, altcoin_health, fear_greed, rs_rankings
        )

        result = {
            "regime": regime,
            "regime_details": {
                "btc_trend": btc_trend,
                "btc_dominance": btc_d_signal,
                "usdt_flow": usdt_d_signal,
                "volatility": volatility,
            },
            "btc_bias": btc_trend["bias"],
            "altcoin_health": altcoin_health,
            "fear_greed": fear_greed,
            "market_commentary": market_commentary,
            "rs_rankings": rs_rankings,
            "long_candidates": long_candidates,
            "short_candidates": short_candidates,
            "filtered_coins": filtered_coins,
            "timestamp": time.time(),
        }

        self._regime_cache = result
        self._regime_ts = now

        # Logla
        n_long = len(long_candidates)
        n_short = len(short_candidates)
        logger.info(
            f"📊 Rejim: {regime} | BTC: {btc_trend['bias']} ({btc_trend['strength']}) | "
            f"BTC.D: {btc_d_signal['direction']} | Para Akışı: {usdt_d_signal['direction']} | "
            f"Fırsatlar: {n_long} LONG, {n_short} SHORT aday"
        )

        return result

    # ═════════════════════════════════════════════════
    # KATMAN 1: BTC TREND ANALİZİ
    # ═════════════════════════════════════════════════
    def _analyze_btc_trend(self, btc_4h, btc_1h, btc_15m):
        """
        BTC'nin multi-timeframe trend yönünü belirle.
        EMA cross + fiyat değişimi hibrit sistemi — gecikmeyi azaltır.
        """
        cfg = REGIME_CONFIG
        result = {"bias": "NEUTRAL", "strength": "WEAK", "momentum": 0, "change_pcts": {}}

        try:
            # 4H trend (ana yön)
            if btc_4h is not None and len(btc_4h) >= cfg["btc_trend_slow_period"] + 5:
                closes_4h = btc_4h["close"].values.astype(float)
                ema_fast_4h = self._ema(closes_4h, cfg["btc_trend_fast_period"])
                ema_slow_4h = self._ema(closes_4h, cfg["btc_trend_slow_period"])
                # EMA cross yönü (-1 veya +1)
                ema_trend_4h = 1 if ema_fast_4h > ema_slow_4h else -1
                # EMA yakınlık: çok yakınsa güçlü sinyal değil
                ema_gap_4h = abs(ema_fast_4h - ema_slow_4h) / ema_slow_4h * 100

                # 4H fiyat değişimi (son 6 mum ≈ 1 gün)
                period_4h = min(6, len(closes_4h) - 1)
                change_4h = ((closes_4h[-1] - closes_4h[-period_4h - 1]) / closes_4h[-period_4h - 1]) * 100
                result["change_pcts"]["4h"] = round(change_4h, 2)

                # Hibrit 4H skor: EMA yönü + fiyat değişimi
                # Fiyat değişimi büyükse EMA gecikmesini telafi et
                price_trend_4h = np.clip(change_4h / 1.5, -1, 1)  # ±1.5% → ±1 skor
                trend_4h = ema_trend_4h * 0.6 + price_trend_4h * 0.4
            else:
                trend_4h = 0
                change_4h = 0

            # 1H trend (orta vade)
            if btc_1h is not None and len(btc_1h) >= cfg["btc_trend_slow_period"] + 5:
                closes_1h = btc_1h["close"].values.astype(float)
                ema_fast_1h = self._ema(closes_1h, cfg["btc_trend_fast_period"])
                ema_slow_1h = self._ema(closes_1h, cfg["btc_trend_slow_period"])
                ema_trend_1h = 1 if ema_fast_1h > ema_slow_1h else -1

                change_1h = ((closes_1h[-1] - closes_1h[-5]) / closes_1h[-5]) * 100
                result["change_pcts"]["1h"] = round(change_1h, 2)

                price_trend_1h = np.clip(change_1h / 0.8, -1, 1)  # ±0.8% → ±1 skor
                trend_1h = ema_trend_1h * 0.5 + price_trend_1h * 0.5
            else:
                trend_1h = 0
                change_1h = 0

            # 15m momentum (kısa vade)
            closes_15m = btc_15m["close"].values.astype(float)
            lookback = cfg["btc_trend_lookback"]
            momentum_pct = ((closes_15m[-1] - closes_15m[-lookback - 1]) / closes_15m[-lookback - 1]) * 100
            result["change_pcts"]["15m_momentum"] = round(momentum_pct, 2)
            result["momentum"] = momentum_pct

            # Ağırlıklı trend skoru: 4H en önemli
            # Artık -1 ile +1 arası sürekli (continuous) değerler
            trend_score = (trend_4h * 0.5) + (trend_1h * 0.3) + (np.clip(momentum_pct / 0.5, -1, 1) * 0.2)

            if trend_score > 0.25:
                result["bias"] = "LONG"
                result["strength"] = "STRONG" if trend_score > 0.6 else "MODERATE"
            elif trend_score < -0.25:
                result["bias"] = "SHORT"
                result["strength"] = "STRONG" if trend_score < -0.6 else "MODERATE"
            else:
                result["bias"] = "NEUTRAL"
                result["strength"] = "WEAK"

            result["trend_score"] = round(trend_score, 3)

        except Exception as e:
            logger.error(f"BTC trend analiz hatası: {e}")

        return result

    # ═════════════════════════════════════════════════
    # KATMAN 1: BTC DOMİNANS PROXY
    # ═════════════════════════════════════════════════
    def _analyze_btc_dominance_proxy(self, btc_15m, coin_list):
        """
        BTC.D direkt çekilemez (OKX'te yok).
        PROXY: BTC performansı vs ortalama altcoin performansı
        BTC daha iyi → BTC.D yükseliyor (para BTC'ye akıyor)
        Altlar daha iyi → BTC.D düşüyor (para altlara akıyor)
        """
        cfg = REGIME_CONFIG
        lookback = cfg["btc_d_lookback_candles"]
        result = {"direction": "NEUTRAL", "spread": 0, "btc_change": 0, "alt_avg_change": 0}

        try:
            btc_closes = btc_15m["close"].values.astype(float)
            if len(btc_closes) < lookback + 1:
                return result

            btc_change = ((btc_closes[-1] - btc_closes[-lookback - 1]) / btc_closes[-lookback - 1]) * 100

            # Top 5-6 büyük altcoini karşılaştır (hız için hepsini değil)
            major_alts = [s for s in coin_list if s != self._btc_symbol()][:8]
            alt_changes = []

            for alt_symbol in major_alts:
                try:
                    alt_df = data_fetcher.get_candles(alt_symbol, "15m", lookback + 10)
                    if alt_df is not None and len(alt_df) >= lookback + 1:
                        alt_c = alt_df["close"].values.astype(float)
                        alt_chg = ((alt_c[-1] - alt_c[-lookback - 1]) / alt_c[-lookback - 1]) * 100
                        alt_changes.append(alt_chg)
                except Exception:
                    continue

            if not alt_changes:
                return result

            alt_avg = np.mean(alt_changes)
            spread = btc_change - alt_avg  # Pozitif → BTC daha iyi → BTC.D yükseliyor

            result["btc_change"] = round(btc_change, 2)
            result["alt_avg_change"] = round(alt_avg, 2)
            result["spread"] = round(spread, 2)

            threshold = cfg["btc_d_threshold"]
            if spread > threshold:
                result["direction"] = "RISING"  # BTC.D artıyor
            elif spread < -threshold:
                result["direction"] = "FALLING"  # BTC.D düşüyor (alt season sinyali)
            else:
                result["direction"] = "NEUTRAL"

        except Exception as e:
            logger.error(f"BTC.D proxy hatası: {e}")

        return result

    # ═════════════════════════════════════════════════
    # KATMAN 1: USDT AKIŞI PROXY
    # ═════════════════════════════════════════════════
    def _analyze_usdt_flow_proxy(self, btc_15m, coin_list):
        """
        USDT.D direkt çekilemez.
        PROXY: Toplam piyasa hacmi + fiyat yönü analizi
        - Hacim düşüyor + fiyatlar düşüyor → Para çıkıyor (USDT.D yükseliyor)
        - Hacim artıyor + fiyatlar çıkıyor → Para giriyor (USDT.D düşüyor)
        """
        result = {"direction": "NEUTRAL", "volume_change_pct": 0, "price_direction": "NEUTRAL"}

        try:
            # BTC hacim analizi (piyasa proxy'si)
            volumes = btc_15m["volume"].values.astype(float)
            closes = btc_15m["close"].values.astype(float)

            if len(volumes) < 20:
                return result

            # Son 4 mum hacmi vs son 20 mum ortalaması
            recent_vol = np.mean(volumes[-4:])
            avg_vol = np.mean(volumes[-20:])
            vol_change_pct = ((recent_vol - avg_vol) / avg_vol) * 100

            # Fiyat yönü (son 8 mum)
            price_change = ((closes[-1] - closes[-8]) / closes[-8]) * 100

            result["volume_change_pct"] = round(vol_change_pct, 1)

            if price_change > 0.3:
                result["price_direction"] = "UP"
            elif price_change < -0.3:
                result["price_direction"] = "DOWN"

            # Para akışı tespiti
            cfg = REGIME_CONFIG
            if vol_change_pct < cfg["usdt_d_volume_drop_pct"] and price_change < -0.2:
                # Hacim düşüyor + fiyat düşüyor → Likidite azalıyor → para çıkış
                result["direction"] = "OUTFLOW"  # USDT.D yükseliyor
            elif vol_change_pct > cfg["usdt_d_volume_surge_pct"] and price_change > 0.2:
                # Hacim artıyor + fiyat çıkıyor → Para giriyor
                result["direction"] = "INFLOW"  # USDT.D düşüyor
            elif vol_change_pct > cfg["usdt_d_volume_surge_pct"] and price_change < -0.5:
                # Hacim artıyor + fiyat düşüyor → Panik satışı
                result["direction"] = "PANIC_SELL"
            else:
                result["direction"] = "NEUTRAL"

        except Exception as e:
            logger.error(f"USDT flow proxy hatası: {e}")

        return result

    # ═════════════════════════════════════════════════
    # REJİM TESPİTİ
    # ═════════════════════════════════════════════════
    def _determine_regime(self, btc_trend, btc_d, usdt_flow):
        """
        3 sinyali birleştirip piyasa rejimini belirle.

        REJİMLER:
        RISK_ON       → Piyasa sağlıklı yükseliyor (BTC↑, para giriyor)
        ALT_SEASON    → Altcoinler BTC'den iyi (BTC.D↓, altlar güçlü)
        RISK_OFF      → Piyasa düşüşte (BTC↓, para çıkıyor)
        CAPITULATION  → Her şey çöküyor, panik (BTC↓↓, panik satış, BTC.D↑)
        NEUTRAL       → Belirsiz, karışık sinyaller
        """
        btc_bias = btc_trend["bias"]
        btc_strength = btc_trend["strength"]
        btc_d_dir = btc_d["direction"]
        flow_dir = usdt_flow["direction"]
        trend_score = btc_trend.get("trend_score", 0)

        # KAPITÜLASYON: BTC düşüyor + panik satış + BTC.D yükseliyor
        if btc_bias == "SHORT" and flow_dir == "PANIC_SELL":
            return REGIME_CAPITULATION
        if btc_bias == "SHORT" and btc_strength == "STRONG" and btc_d_dir == "RISING":
            return REGIME_CAPITULATION

        # ALT SEASON: BTC.D düşüyor (altlar BTC'den iyi)
        if btc_d_dir == "FALLING" and btc_bias != "SHORT":
            return REGIME_ALT_SEASON
        if btc_d_dir == "FALLING" and btc_bias == "SHORT" and btc_strength == "WEAK":
            return REGIME_ALT_SEASON

        # RISK ON: BTC yükseliyor + para giriyor
        if btc_bias == "LONG" and flow_dir in ("INFLOW", "NEUTRAL"):
            return REGIME_RISK_ON
        if btc_bias == "LONG" and btc_strength == "STRONG":
            return REGIME_RISK_ON
        # GÜÇLENDİRME: BTC nötr/hafif pozitif ama güçlü INFLOW → RISK_ON
        # Hacim %20+ artış + fiyat yükseliyor = piyasa toparlanıyor, EMA henüz gecikmeli
        if btc_bias == "NEUTRAL" and flow_dir == "INFLOW" and trend_score > 0:
            return REGIME_RISK_ON

        # RISK OFF: BTC düşüyor + para çıkıyor
        if btc_bias == "SHORT" and flow_dir == "OUTFLOW":
            return REGIME_RISK_OFF
        if btc_bias == "SHORT" and btc_strength in ("STRONG", "MODERATE"):
            return REGIME_RISK_OFF
        # GÜÇLENDİRME: BTC nötr ama para çıkıyor + trend negatif → RISK_OFF
        if btc_bias == "NEUTRAL" and flow_dir == "OUTFLOW" and trend_score < 0:
            return REGIME_RISK_OFF

        # Belirsiz
        return REGIME_NEUTRAL

    # ═════════════════════════════════════════════════
    # VOLATİLİTE ANALİZİ
    # ═════════════════════════════════════════════════
    def _analyze_volatility(self, btc_4h, btc_15m):
        """
        BTC ATR bazlı volatilite durumu.
        Yüksek volatilite → daha geniş SL, düşük kaldıraç
        Düşük volatilite → sıkışma, yakında patlama beklenir
        """
        cfg = REGIME_CONFIG
        result = {"state": "NORMAL", "atr_ratio": 1.0, "btc_range_pct": 0}

        try:
            # 4H ATR (daha güvenilir)
            df = btc_4h if btc_4h is not None and len(btc_4h) >= 30 else btc_15m
            if df is None or len(df) < 30:
                return result

            highs = df["high"].values.astype(float)
            lows = df["low"].values.astype(float)
            closes = df["close"].values.astype(float)

            # ATR hesapla (14 periyot)
            period = cfg["atr_period"]
            trs = []
            for i in range(1, len(closes)):
                tr = max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1])
                )
                trs.append(tr)

            if len(trs) < period * 2:
                return result

            # Son ATR vs uzun dönem ortalaması
            recent_atr = np.mean(trs[-period:])
            long_atr = np.mean(trs)  # Tüm mevcut veriyi uzun dönem olarak kullan
            atr_ratio = recent_atr / long_atr if long_atr > 0 else 1.0

            # Son 24 saat fiyat aralığı (%)
            range_close = closes[-1]
            range_high = max(highs[-6:]) if len(highs) >= 6 else highs[-1]
            range_low = min(lows[-6:]) if len(lows) >= 6 else lows[-1]
            range_pct = ((range_high - range_low) / range_close * 100) if range_close > 0 else 0

            result["atr_ratio"] = round(atr_ratio, 2)
            result["btc_range_pct"] = round(range_pct, 2)

            if atr_ratio >= cfg["vol_high_threshold"]:
                result["state"] = "HIGH"  # Yüksek oynaklık
            elif atr_ratio <= cfg["vol_low_threshold"]:
                result["state"] = "LOW"   # Sıkışma / düşük oynaklık
            else:
                result["state"] = "NORMAL"

        except Exception as e:
            logger.debug(f"Volatilite analiz hatası: {e}")

        return result

    # ═════════════════════════════════════════════════
    # KATMAN 2: RÖLATİF GÜÇ HESAPLAMA
    # ═════════════════════════════════════════════════
    def _calculate_all_relative_strength(self, btc_15m, coin_list):
        """
        Her coinin BTC'ye göre rölatif gücünü hesapla.
        RS > 0 → BTC'den güçlü
        RS < 0 → BTC'den zayıf
        """
        cfg = REGIME_CONFIG
        btc_closes = btc_15m["close"].values.astype(float)
        rankings = []

        for symbol in coin_list:
            if self._is_btc(symbol):
                continue

            try:
                coin_df = data_fetcher.get_candles(symbol, "15m", 100)
                if coin_df is None or len(coin_df) < cfg["rs_min_candles"]:
                    continue

                coin_closes = coin_df["close"].values.astype(float)
                coin_volumes = coin_df["volume"].values.astype(float)

                # Multi-period RS skoru
                rs_score = 0
                valid_periods = 0

                for period, weight in zip(cfg["rs_periods"], cfg["rs_weights"]):
                    if len(coin_closes) > period and len(btc_closes) > period:
                        coin_chg = ((coin_closes[-1] - coin_closes[-period - 1]) / coin_closes[-period - 1]) * 100
                        btc_chg = ((btc_closes[-1] - btc_closes[-period - 1]) / btc_closes[-period - 1]) * 100
                        rs_score += (coin_chg - btc_chg) * weight
                        valid_periods += 1

                if valid_periods == 0:
                    continue

                # Hacim doğrulaması
                recent_vol = float(np.asarray(coin_volumes[-4:], dtype=float).mean())
                avg_vol = float(np.asarray(coin_volumes[-20:], dtype=float).mean())
                vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 0

                # Hacim artıyorsa RS güçlenir, düşüyorsa zayıflar
                vol_multiplier = 1.0
                if vol_ratio > 1.5:
                    vol_multiplier = 1.2  # Hacim patlıyor → sinyal güçlü
                elif vol_ratio < 0.5:
                    vol_multiplier = 0.7  # Hacim çok düşük → sinyal zayıf

                adjusted_rs = rs_score * vol_multiplier

                # Momentum hız farkı (son 4 mum, ~1 saat)
                if len(coin_closes) >= 5 and len(btc_closes) >= 5:
                    coin_mom = ((coin_closes[-1] - coin_closes[-5]) / coin_closes[-5]) * 100
                    btc_mom = ((btc_closes[-1] - btc_closes[-5]) / btc_closes[-5]) * 100
                    short_term_rs = coin_mom - btc_mom
                else:
                    short_term_rs = 0

                rankings.append({
                    "symbol": symbol,
                    "rs_score": round(adjusted_rs, 3),
                    "raw_rs": round(rs_score, 3),
                    "vol_ratio": round(vol_ratio, 2),
                    "vol_multiplier": vol_multiplier,
                    "short_term_rs": round(short_term_rs, 3),
                    "price_change_1h": round(
                        ((coin_closes[-1] - coin_closes[-5]) / coin_closes[-5]) * 100, 2
                    ) if len(coin_closes) >= 5 else 0,
                })

            except Exception as e:
                logger.debug(f"RS hesaplama hatası {symbol}: {e}")
                continue

        # RS skoruna göre sırala (en güçlü başta)
        rankings.sort(key=lambda x: x["rs_score"], reverse=True)
        return rankings

    # ═════════════════════════════════════════════════
    # KATMAN 3: FIRSAT FİLTRELEME
    # ═════════════════════════════════════════════════
    def _filter_opportunities(self, rs_rankings, regime):
        """
        Rejime göre LONG ve SHORT adaylarını belirle.
        Çorba yerine sadece gerçek fırsatları seç.
        """
        cfg = REGIME_CONFIG
        long_candidates = []
        short_candidates = []

        for coin in rs_rankings:
            symbol = coin["symbol"]
            rs = coin["rs_score"]
            vol = coin["vol_ratio"]

            # Hacim kontrolü — düşük hacimli coinleri atla
            if vol < cfg["min_volume_confirmation"]:
                continue

            # ── LONG adayları ──
            if rs > cfg["rs_long_threshold"]:
                if regime in (REGIME_RISK_ON, REGIME_ALT_SEASON, REGIME_NEUTRAL):
                    long_candidates.append(symbol)
                elif regime == REGIME_RISK_OFF and rs > 2.0:
                    # Risk-off'ta sadece ÇOK güçlü olanlar (BTC'ye rağmen yükselen)
                    long_candidates.append(symbol)
                # CAPITULATION'da LONG yok (çok riskli)

            # ── SHORT adayları ──
            if rs < cfg["rs_short_threshold"]:
                if regime in (REGIME_RISK_OFF, REGIME_CAPITULATION, REGIME_NEUTRAL):
                    short_candidates.append(symbol)
                elif regime == REGIME_RISK_ON and rs < -2.0:
                    # Risk-on'da sadece ÇOK zayıf olanlar (BTC çıkarken düşen)
                    short_candidates.append(symbol)
                # ALT_SEASON'da SHORT yok (altlar güçlü)

        # Limit uygula
        long_candidates = long_candidates[:cfg["max_long_candidates"]]
        short_candidates = short_candidates[-cfg["max_short_candidates"]:]  # En zayıflar

        return long_candidates, short_candidates

    # ═════════════════════════════════════════════════
    # FİLTRE HARİTASI OLUŞTUR
    # ═════════════════════════════════════════════════
    def _build_filtered_map(self, coin_list, rs_rankings, long_cands, short_cands, regime):
        """
        Her coin için izin verilen yönleri belirle.
        Stratejiler (ICT) bu haritaya bakarak sadece izinli yönde sinyal üretecek.
        """
        rs_lookup = {r["symbol"]: r for r in rs_rankings}
        filtered = {}

        for symbol in coin_list:
            if self._is_btc(symbol):
                continue

            rs_data = rs_lookup.get(symbol)
            rs_score = rs_data["rs_score"] if rs_data else 0

            allowed = []
            if symbol in long_cands:
                allowed.append("LONG")
            if symbol in short_cands:
                allowed.append("SHORT")

            # Eğer hiçbir listeye girmediyse → bu coinde sinyal yok
            # Ama NEUTRAL rejimde RS skoru ortalama ise yine de şans ver
            if not allowed and regime == REGIME_NEUTRAL:
                if abs(rs_score) < 0.3:
                    # Nötr coin, nötr rejim — her iki yöne de bakılabilir (düşük öncelik)
                    allowed = ["LONG", "SHORT"]

            filtered[symbol] = {
                "allowed_directions": allowed,
                "rs_score": rs_score,
                "rs_data": rs_data,
                "is_candidate": len(allowed) > 0,
            }

        return filtered

    # ═════════════════════════════════════════════════
    # KATMAN 0: ALTCOİN PİYASA SAĞLIĞI (TOTAL2/3/OTHERS PROXY)
    # ═════════════════════════════════════════════════
    def _analyze_altcoin_health(self, rs_rankings, coin_list, btc_15m):
        """
        TOTAL2 / TOTAL3 / OTHERS proxy analizi.
        OKX'te bu endeksler yok → coinlerin performansından türetiyoruz.

        TOTAL2: Tüm piyasa – BTC (büyük altlar ağırlıklı)
        TOTAL3: Tüm piyasa – BTC – ETH (mid-cap'ler)
        OTHERS: Küçük/orta coinler (BTC+ETH+top5 hariç)

        Returns: {
            total2_proxy, total3_proxy, others_proxy,
            alt_performance, green_ratio, avg_change, market_breadth
        }
        """
        cfg = REGIME_CONFIG
        result = {
            "total2_proxy": 0, "total2_label": "Nötr",
            "total3_proxy": 0, "total3_label": "Nötr",
            "others_proxy": 0, "others_label": "Nötr",
            "green_ratio": 50, "avg_change_1h": 0,
            "top_gainers": [], "top_losers": [],
            "market_breadth": "NEUTRAL",
            "breadth_detail": "",
        }

        if not rs_rankings:
            return result

        try:
            # ── Coin kategorileri ──
            large_cap_set = set(f"{a}-USDT-SWAP" for a in cfg["large_cap_alts"])
            eth_symbol = "ETH-USDT-SWAP" if INST_TYPE == "SWAP" else "ETH-USDT"

            total2_data = []      # TOTAL2 proxy: BTC hariç tüm altcoinler
            total3_data = []      # TOTAL3 proxy: BTC + ETH hariç
            others_data = []      # OTHERS: top 15 hariç herkes
            all_changes = []

            # RS rankings zaten tüm coinlerin BTC'ye göre performansını tutar
            for i, coin in enumerate(rs_rankings):
                sym = coin["symbol"]
                change = coin.get("price_change_1h", 0)
                all_changes.append(change)
                total2_data.append(change)  # TOTAL2 = BTC hariç herkes

                if sym == eth_symbol:
                    pass  # ETH sadece TOTAL2'ye dahil, TOTAL3'e dahil değil
                elif sym in large_cap_set:
                    total3_data.append(change)  # Büyük altlar (ETH hariç)
                else:
                    total3_data.append(change)  # Orta/küçük coinler
                    # İlk 15'ten sonrakiler OTHERS
                    if i >= 15:
                        others_data.append(change)

            # ── TOTAL2 Proxy (BTC hariç genel) ──
            if total2_data:
                t2 = np.mean(total2_data)
                result["total2_proxy"] = round(t2, 2)
                result["total2_label"] = self._trend_label(t2)

            # ── TOTAL3 Proxy (BTC+ETH hariç) ──
            if total3_data:
                t3 = np.mean(total3_data)
                result["total3_proxy"] = round(t3, 2)
                result["total3_label"] = self._trend_label(t3)

            # ── OTHERS Proxy (küçük coinler) ──
            if others_data:
                ot = np.mean(others_data)
                result["others_proxy"] = round(ot, 2)
                result["others_label"] = self._trend_label(ot)

            # ── Genel sağlık metrikleri ──
            if all_changes:
                greens = sum(1 for c in all_changes if c > 0)
                result["green_ratio"] = round(greens / len(all_changes) * 100)
                result["avg_change_1h"] = round(np.mean(all_changes), 2)

            # ── Market Breadth (piyasa genişliği) ──
            green_r = result["green_ratio"]
            if green_r >= 75:
                result["market_breadth"] = "STRONG_BULLISH"
                result["breadth_detail"] = f"Coinlerin %{green_r}'i yükselişte — geniş tabanlı ralli"
            elif green_r >= 60:
                result["market_breadth"] = "BULLISH"
                result["breadth_detail"] = f"Coinlerin %{green_r}'i yükselişte — sağlıklı piyasa"
            elif green_r <= 25:
                result["market_breadth"] = "STRONG_BEARISH"
                result["breadth_detail"] = f"Coinlerin sadece %{green_r}'i yükselişte — yaygın düşüş"
            elif green_r <= 40:
                result["market_breadth"] = "BEARISH"
                result["breadth_detail"] = f"Coinlerin %{green_r}'i yükselişte — baskı altında"
            else:
                result["market_breadth"] = "NEUTRAL"
                result["breadth_detail"] = f"Coinlerin %{green_r}'i yükselişte — karışık piyasa"

            # ── Top gainers / losers ──
            sorted_by_change = sorted(rs_rankings, key=lambda x: x.get("price_change_1h", 0), reverse=True)
            result["top_gainers"] = [
                {"symbol": c["symbol"].split("-")[0], "change": c.get("price_change_1h", 0)}
                for c in sorted_by_change[:3]
            ]
            result["top_losers"] = [
                {"symbol": c["symbol"].split("-")[0], "change": c.get("price_change_1h", 0)}
                for c in sorted_by_change[-3:]
            ]

        except Exception as e:
            logger.error(f"Altcoin health analiz hatası: {e}")

        return result

    @staticmethod
    def _trend_label(change_pct):
        """Yüzde değişime göre etiket"""
        if change_pct >= 2:
            return "Güçlü Yükseliş"
        elif change_pct >= 0.5:
            return "Yükseliş"
        elif change_pct <= -2:
            return "Güçlü Düşüş"
        elif change_pct <= -0.5:
            return "Düşüş"
        return "Nötr"

    # ═════════════════════════════════════════════════
    # KATMAN 0: FEAR & GREED PROXY
    # ═════════════════════════════════════════════════
    def _calculate_fear_greed(self, btc_trend, usdt_flow, volatility, rs_rankings, alt_health):
        """
        Fear & Greed Index proxy — gerçek endeks API'si yerine
        OKX verilerinden türetilmiş piyasa duygu analizi.

        Bileşenler:
          1. BTC Momentum (25%) — Trend skoru + değişim yüzdeleri
          2. Piyasa Hacmi (20%) — Para akışı durumu
          3. Volatilite (15%) — Düşük vol = açgözlülük, yüksek vol = korku
          4. Piyasa Genişliği (25%) — Yükselen/düşen coin oranı
          5. Altcoin Performansı (15%) — Altlar güçlüyse açgözlülük

        Sonuç: 0-100 (0=Extreme Fear, 100=Extreme Greed)
        """
        cfg = REGIME_CONFIG
        score = 50  # Başlangıç: Nötr

        try:
            # ── 1. BTC Momentum (25%) ──
            trend_score = btc_trend.get("trend_score", 0)
            # trend_score: -1 ile +1 arası → 0-100'e çevir
            btc_component = (trend_score + 1) / 2 * 100  # -1→0, 0→50, +1→100
            btc_component = np.clip(btc_component, 0, 100)

            # ── 2. Hacim / Para Akışı (20%) ──
            flow_dir = usdt_flow.get("direction", "NEUTRAL")
            vol_change = usdt_flow.get("volume_change_pct", 0)
            if flow_dir == "INFLOW":
                vol_component = min(70 + vol_change * 0.3, 100)  # Para girişi → Greed
            elif flow_dir == "OUTFLOW":
                vol_component = max(30 + vol_change * 0.3, 0)   # Para çıkışı → Fear
            elif flow_dir == "PANIC_SELL":
                vol_component = 10  # Panik = Extreme Fear
            else:
                vol_component = 50

            # ── 3. Volatilite (15%) ──
            vol_state = volatility.get("state", "NORMAL")
            atr_ratio = volatility.get("atr_ratio", 1.0)
            if vol_state == "HIGH":
                # Yüksek volatilite → genellikle korku (ani hareketler)
                vol_comp = max(25 - (atr_ratio - 1.5) * 20, 5)
            elif vol_state == "LOW":
                # Düşük volatilite → sıkışma, genellikle sakinlik → greed
                vol_comp = min(65 + (0.6 - atr_ratio) * 50, 85)
            else:
                vol_comp = 50

            # ── 4. Market Breadth (25%) — en önemli ──
            green_ratio = alt_health.get("green_ratio", 50)
            breadth_component = green_ratio  # Doğrudan: %75 green = 75 puan

            # ── 5. Altcoin Performansı (15%) ──
            avg_change = alt_health.get("avg_change_1h", 0)
            alt_comp = np.clip((avg_change + 3) / 6 * 100, 0, 100)  # -3%→0, 0→50, +3%→100

            # ── Ağırlıklı toplam ──
            score = (
                btc_component * 0.25 +
                vol_component * 0.20 +
                vol_comp * 0.15 +
                breadth_component * 0.25 +
                alt_comp * 0.15
            )
            score = round(np.clip(score, 0, 100))

        except Exception as e:
            logger.error(f"Fear/Greed hesaplama hatası: {e}")

        # Etiketleme
        if score <= cfg["fg_extreme_fear"]:
            label = "Aşırı Korku"
            emoji = "😱"
            color = "#ef4444"
        elif score <= cfg["fg_fear"]:
            label = "Korku"
            emoji = "😰"
            color = "#f97316"
        elif score <= cfg["fg_greed"]:
            label = "Nötr"
            emoji = "😐"
            color = "#94a3b8"
        elif score <= cfg["fg_extreme_greed"]:
            label = "Açgözlülük"
            emoji = "😏"
            color = "#22c55e"
        else:
            label = "Aşırı Açgözlülük"
            emoji = "🤑"
            color = "#16a34a"

        return {
            "score": score,
            "label": label,
            "emoji": emoji,
            "color": color,
            "components": {
                "btc_momentum": round(btc_component),
                "volume_flow": round(vol_component),
                "volatility": round(vol_comp),
                "market_breadth": round(breadth_component),
                "altcoin_perf": round(alt_comp),
            }
        }

    # ═════════════════════════════════════════════════
    # KATMAN 0: PİYASA YORUM MOTORU
    # ═════════════════════════════════════════════════
    def _generate_market_commentary(self, regime, btc_trend, btc_d, usdt_flow,
                                     volatility, alt_health, fear_greed, rs_rankings):
        """
        Piyasa durumunu analiz eden detaylı Türkçe yorum üret.
        Profesyonel analist gibi, data-driven yorum.
        """
        sections = []

        try:
            # ═══ 1. GENEL DURUM ═══
            bias = btc_trend.get("bias", "NEUTRAL")
            trend_score = btc_trend.get("trend_score", 0)
            changes = btc_trend.get("change_pcts", {})
            fg = fear_greed.get("score", 50)
            fg_label = fear_greed.get("label", "Nötr")

            if regime == REGIME_RISK_ON:
                headline = "📈 Piyasa Risk-On modunda — yükseliş trendi aktif"
            elif regime == REGIME_RISK_OFF:
                headline = "📉 Piyasa Risk-Off modunda — düşüş baskısı hakim"
            elif regime == REGIME_ALT_SEASON:
                headline = "🚀 Alt Season sinyali — altcoinler BTC'den güçlü"
            elif regime == REGIME_CAPITULATION:
                headline = "⚠️ Kapitülasyon riski — piyasada panik satış işaretleri"
            else:
                headline = "⏸️ Piyasa kararsız — net bir yön oluşmamış"
            sections.append({"title": "Genel Durum", "icon": "fa-globe", "text": headline})

            # ═══ 2. BTC ANALİZİ ═══
            btc_lines = []
            c4h = changes.get("4h", 0)
            c1h = changes.get("1h", 0)
            mom = changes.get("15m_momentum", 0)

            if bias == "LONG":
                btc_lines.append(f"Bitcoin yükseliş trendinde (skor: {trend_score:+.2f}).")
                if c4h > 1:
                    btc_lines.append(f"4 saatlik dilimde %{c4h:.1f} yükseldi — momentum güçlü.")
                elif c4h > 0:
                    btc_lines.append(f"4 saatlik dilimde %{c4h:.1f} artı — kontrollü yükseliş.")
            elif bias == "SHORT":
                btc_lines.append(f"Bitcoin düşüş trendinde (skor: {trend_score:+.2f}).")
                if c4h < -1:
                    btc_lines.append(f"4 saatlik dilimde %{abs(c4h):.1f} geriledi — satış baskısı yoğun.")
                elif c4h < 0:
                    btc_lines.append(f"4 saatlik dilimde %{abs(c4h):.1f} geriledi — zayıf seyir.")
            else:
                btc_lines.append(f"Bitcoin net bir yön vermemiş (skor: {trend_score:+.2f}).")
                if abs(c1h) < 0.2:
                    btc_lines.append("Fiyat yatay seyrediyor — kırılım bekleniyor.")
                elif c1h > 0:
                    btc_lines.append(f"Son 1 saatte %{c1h:.1f} yükselmiş ancak trend henüz onaylanmadı.")
                else:
                    btc_lines.append(f"Son 1 saatte %{abs(c1h):.1f} gevşeme var — kontrol edilmeli.")

            if mom > 0.3:
                btc_lines.append(f"Kısa vadeli momentum pozitif (+%{mom:.1f}), alıcılar aktif.")
            elif mom < -0.3:
                btc_lines.append(f"Kısa vadeli momentum negatif (%{mom:.1f}), satıcılar baskın.")

            sections.append({"title": "Bitcoin", "icon": "fab fa-bitcoin", "text": " ".join(btc_lines)})

            # ═══ 3. PARA AKIŞI ═══
            flow_dir = usdt_flow.get("direction", "NEUTRAL")
            vol_chg = usdt_flow.get("volume_change_pct", 0)
            price_dir = usdt_flow.get("price_direction", "NEUTRAL")

            flow_lines = []
            if flow_dir == "INFLOW":
                flow_lines.append(f"Piyasaya para girişi tespit edildi (hacim +%{vol_chg:.0f}).")
                flow_lines.append("Bu genellikle fiyatlarda yükseliş öncesi görülür.")
                if price_dir == "UP":
                    flow_lines.append("Fiyat da yukarı yönlü — sağlıklı alım baskısı.")
            elif flow_dir == "OUTFLOW":
                flow_lines.append(f"Piyasadan para çıkışı gözleniyor (hacim %{vol_chg:.0f}).")
                flow_lines.append("Yatırımcılar risk almak istemiyor, temkinli ol.")
            elif flow_dir == "PANIC_SELL":
                flow_lines.append("⚠️ Panik satış sinyalleri! Hacim artarken fiyat düşüyor.")
                flow_lines.append("Bu durum genellikle dip oluşumu veya daha derin düşüş anlamına gelir — dikkatli ol.")
            else:
                flow_lines.append("Para akışında belirgin bir yön bulunmuyor.")
                if abs(vol_chg) < 10:
                    flow_lines.append("Hacim ortalama seviyede, piyasa sakin.")

            sections.append({"title": "Para Akışı", "icon": "fa-money-bill-transfer", "text": " ".join(flow_lines)})

            # ═══ 4. ALTCOİN PİYASASI ═══
            green_r = alt_health.get("green_ratio", 50)
            avg_ch = alt_health.get("avg_change_1h", 0)
            t2 = alt_health.get("total2_proxy", 0)
            t3 = alt_health.get("total3_proxy", 0)
            ot = alt_health.get("others_proxy", 0)

            alt_lines = []
            breadth = alt_health.get("market_breadth", "NEUTRAL")
            if breadth in ("STRONG_BULLISH", "BULLISH"):
                alt_lines.append(f"Altcoin piyasası güçlü — coinlerin %{green_r}'i yükselişte.")
            elif breadth in ("STRONG_BEARISH", "BEARISH"):
                alt_lines.append(f"Altcoin piyasası baskı altında — coinlerin sadece %{green_r}'i yeşil.")
            else:
                alt_lines.append(f"Altcoin piyasası karışık — %{green_r} yükselişte.")

            # TOTAL2/3/OTHERS proxy
            alt_lines.append(
                f"Büyük altcoinler (TOTAL2): %{t2:+.1f} | "
                f"Orta seviye (TOTAL3): %{t3:+.1f} | "
                f"Küçük coinler (OTHERS): %{ot:+.1f}."
            )

            # Dominans yorumu
            dom_dir = btc_d.get("direction", "NEUTRAL")
            spread = btc_d.get("spread", 0)
            if dom_dir == "RISING":
                alt_lines.append(
                    f"BTC dominansı artıyor (spread: {spread:+.1f}%). "
                    "Para BTC'ye akıyor, altcoinlerden çıkış var — altlardan uzak dur."
                )
            elif dom_dir == "FALLING":
                alt_lines.append(
                    f"BTC dominansı düşüyor (spread: {spread:+.1f}%). "
                    "Para altcoinlere kayıyor — altcoin fırsatları artabilir."
                )
            else:
                alt_lines.append("BTC dominansı stabil — belirgin bir rotasyon yok.")

            # Top gainers/losers
            gainers = alt_health.get("top_gainers", [])
            losers = alt_health.get("top_losers", [])
            if gainers:
                g_txt = ", ".join(f"{g['symbol']} (+%{g['change']:.1f})" for g in gainers)
                alt_lines.append(f"En çok yükselenler: {g_txt}.")
            if losers:
                l_txt = ", ".join(f"{l['symbol']} (%{l['change']:.1f})" for l in losers)
                alt_lines.append(f"En çok düşenler: {l_txt}.")

            sections.append({"title": "Altcoin Piyasası", "icon": "fa-coins", "text": " ".join(alt_lines)})

            # ═══ 5. VOLATİLİTE & RİSK ═══
            vol_state = volatility.get("state", "NORMAL")
            atr_ratio = volatility.get("atr_ratio", 1.0)
            btc_range = volatility.get("btc_range_pct", 0)

            vol_lines = []
            if vol_state == "HIGH":
                vol_lines.append(
                    f"Volatilite yüksek (ATR x{atr_ratio:.1f}). "
                    f"Son 24 saatte BTC %{btc_range:.1f} aralığında hareket etti. "
                    "Geniş stop-loss kullan, kaldıracı düşür."
                )
            elif vol_state == "LOW":
                vol_lines.append(
                    f"Volatilite düşük (ATR x{atr_ratio:.1f}). "
                    "Piyasa sıkışmış durumda — bu genellikle yakında güçlü bir kırılım anlamına gelir. "
                    "Yönü belirlemeden büyük pozisyon alma."
                )
            else:
                vol_lines.append(
                    f"Volatilite normal seviyede (ATR x{atr_ratio:.1f}). "
                    f"Son 24 saatte %{btc_range:.1f} aralık. "
                    "Standart risk yönetimi yeterli."
                )

            sections.append({"title": "Volatilite & Risk", "icon": "fa-shield-halved", "text": " ".join(vol_lines)})

            # ═══ 6. DUYGU DURUMU ═══
            sentiment_lines = []
            fg_emoji = fear_greed.get("emoji", "😐")
            sentiment_lines.append(f"Piyasa duygusu: {fg_emoji} {fg_label} ({fg}/100).")

            components = fear_greed.get("components", {})
            if fg <= 20:
                sentiment_lines.append(
                    "Aşırı korku bölgesi — tarihsel olarak bu seviyeler iyi alım fırsatları sunmuştur. "
                    "\"Herkes korkarken cesur ol\" prensibi geçerli olabilir, ancak düşüş devam edebilir."
                )
            elif fg <= 35:
                sentiment_lines.append(
                    "Piyasada korku hakim. Fiyatlar düşük olabilir ama dipte mi yoksa devam mı belirsiz. "
                    "Kademeli alım düşünülebilir."
                )
            elif fg >= 80:
                sentiment_lines.append(
                    "Aşırı açgözlülük bölgesi — fiyatlar aşırı ısınmış olabilir. "
                    "\"Herkes açgözlüyken korkak ol.\" Pozisyon büyüklüğünü azalt."
                )
            elif fg >= 65:
                sentiment_lines.append(
                    "Piyasa iyimser ve alıcılar aktif. Yükseliş devam edebilir "
                    "ancak ani geri çekilmelere hazırlıklı ol."
                )
            else:
                sentiment_lines.append("Duygu nötr bölgede — ne korku ne açgözlülük hakim.")

            sections.append({"title": "Piyasa Duygusu", "icon": "fa-face-smile", "text": " ".join(sentiment_lines)})

            # ═══ 7. STRATEJİK ÖNERİ ═══
            strategy_lines = []
            if regime == REGIME_RISK_ON:
                strategy_lines.append("✅ Piyasa LONG'a uygun. Güçlü RS'li coinlerde geri çekilmelerde alım fırsatı ara.")
                if vol_state == "HIGH":
                    strategy_lines.append("Ancak volatilite yüksek — daha geniş SL ve düşük kaldıraç kullan.")
            elif regime == REGIME_RISK_OFF:
                strategy_lines.append("⛔ Piyasa SHORT lehine. Zayıf coinlerde yükselişlerde satış fırsatı ara.")
                strategy_lines.append("Riskli LONG pozisyonlardan uzak dur.")
            elif regime == REGIME_ALT_SEASON:
                strategy_lines.append("🚀 Altcoinlerde fırsat dönemi. BTC'ye göre güçlü altlarda LONG pozisyonlar değerlendirilebilir.")
                strategy_lines.append("BTC dominansı düştükçe altcoin rallisi devam edebilir.")
            elif regime == REGIME_CAPITULATION:
                strategy_lines.append("💀 Kapitülasyon ortamı — çok dikkatli ol! Panik satışlar dip oluşturabilir ama henüz erken.")
                strategy_lines.append("Sadece küçük pozisyonlarla hareket et veya kenarda bekle.")
            else:
                strategy_lines.append("🔄 Nötr piyasa — net yön yok. Coin bazlı fırsatları RS sıralamasından takip et.")
                if vol_state == "LOW":
                    strategy_lines.append("Sıkışma kırılacak — kırılım yönünü bekle, erken girme.")

            # Long/Short adayları
            long_count = len([r for r in rs_rankings if r["rs_score"] > 0.5])
            short_count = len([r for r in rs_rankings if r["rs_score"] < -0.5])
            if long_count > short_count * 2:
                strategy_lines.append(f"RS analizi: {long_count} coin BTC'den güçlü, sadece {short_count} coin zayıf — genel eğilim yukarı.")
            elif short_count > long_count * 2:
                strategy_lines.append(f"RS analizi: {short_count} coin BTC'den zayıf, sadece {long_count} coin güçlü — genel eğilim aşağı.")

            sections.append({"title": "Strateji Notu", "icon": "fa-chess", "text": " ".join(strategy_lines)})

        except Exception as e:
            logger.error(f"Piyasa yorumu üretme hatası: {e}")
            sections.append({"title": "Genel Durum", "icon": "fa-globe", "text": "Piyasa yorumu oluşturulurken hata oluştu."})

        return sections

    # ═════════════════════════════════════════════════
    # CACHE'Lİ REJİM OKUMA
    # ═════════════════════════════════════════════════
    def get_cached_regime(self):
        """Son analiz sonucunu döndür (cache)"""
        return self._regime_cache

    def get_regime_summary(self):
        """UI için özet bilgi"""
        if not self._regime_cache:
            return {
                "regime": "UNKNOWN",
                "regime_label": "Veri Bekleniyor",
                "regime_emoji": "❓",
                "btc_bias": "UNKNOWN",
                "btc_details": {"bias": "UNKNOWN", "strength": "WEAK", "momentum": 0, "change_pcts": {}},
                "btc_dominance": {"direction": "UNKNOWN", "spread": 0, "btc_change": 0, "alt_avg_change": 0},
                "usdt_flow": {"direction": "UNKNOWN", "volume_change_pct": 0, "price_direction": "NEUTRAL"},
                "volatility": {"state": "NORMAL", "atr_ratio": 1.0, "btc_range_pct": 0},
                "altcoin_health": {
                    "total2_proxy": 0, "total2_label": "Nötr",
                    "total3_proxy": 0, "total3_label": "Nötr",
                    "others_proxy": 0, "others_label": "Nötr",
                    "green_ratio": 50, "avg_change_1h": 0,
                    "top_gainers": [], "top_losers": [],
                    "market_breadth": "NEUTRAL", "breadth_detail": "",
                },
                "fear_greed": {"score": 50, "label": "Nötr", "emoji": "😐", "color": "#94a3b8", "components": {}},
                "market_commentary": [],
                "long_candidates": [],
                "short_candidates": [],
                "long_count": 0,
                "short_count": 0,
                "rs_rankings": [],
                "rs_bottom": [],
                "total_coins": 0,
                "regime_reason": "",
                "timestamp": 0,
            }

        r = self._regime_cache
        all_rs = r["rs_rankings"]
        regime = r["regime"]

        # Rejim nedeni açıklaması
        reason = self._build_regime_reason(r)

        return {
            "regime": regime,
            "regime_label": self._regime_label(regime),
            "regime_emoji": self._regime_emoji(regime),
            "btc_bias": r["btc_bias"],
            "btc_details": r["regime_details"]["btc_trend"],
            "btc_dominance": r["regime_details"]["btc_dominance"],
            "usdt_flow": r["regime_details"]["usdt_flow"],
            "volatility": r["regime_details"].get("volatility", {"state": "NORMAL", "atr_ratio": 1.0, "btc_range_pct": 0}),
            "altcoin_health": r.get("altcoin_health", {}),
            "fear_greed": r.get("fear_greed", {"score": 50, "label": "Nötr", "emoji": "😐", "color": "#94a3b8"}),
            "market_commentary": r.get("market_commentary", []),
            "long_candidates": r["long_candidates"],
            "short_candidates": r["short_candidates"],
            "long_count": len(r["long_candidates"]),
            "short_count": len(r["short_candidates"]),
            "rs_rankings": all_rs[:10],    # Top 10 (en güçlü)
            "rs_bottom": all_rs[-5:] if len(all_rs) > 10 else [],  # Bottom 5 (en zayıf)
            "total_coins": len(all_rs),
            "regime_reason": reason,
            "timestamp": r.get("timestamp", 0),
        }

    def _build_regime_reason(self, r):
        """Rejimin neden belirlendiğini açıkla"""
        regime = r["regime"]
        btc = r["regime_details"]["btc_trend"]
        flow = r["regime_details"]["usdt_flow"]
        dom = r["regime_details"]["btc_dominance"]
        vol = r["regime_details"].get("volatility", {})

        parts = []

        # BTC trend açıklama
        bias = btc["bias"]
        score = btc.get("trend_score", 0)
        if bias == "LONG":
            parts.append(f"BTC yükseliş trendinde (skor: {score:+.2f})")
        elif bias == "SHORT":
            parts.append(f"BTC düşüş trendinde (skor: {score:+.2f})")
        else:
            parts.append(f"BTC yön belirsiz (skor: {score:+.2f})")

        # Para akışı
        if flow["direction"] == "INFLOW":
            parts.append(f"piyasaya para giriyor (hacim +%{flow['volume_change_pct']:.0f})")
        elif flow["direction"] == "OUTFLOW":
            parts.append(f"piyasadan para çıkıyor (hacim %{flow['volume_change_pct']:.0f})")
        elif flow["direction"] == "PANIC_SELL":
            parts.append("panik satış tespit edildi")

        # Dominans
        if dom["direction"] == "RISING":
            parts.append(f"BTC dominansı artıyor (spread: {dom['spread']:+.1f}%)")
        elif dom["direction"] == "FALLING":
            parts.append(f"altcoinler BTC'den iyi (spread: {dom['spread']:+.1f}%)")

        # Volatilite
        if vol.get("state") == "HIGH":
            parts.append(f"yüksek oynaklık (ATR x{vol['atr_ratio']:.1f})")
        elif vol.get("state") == "LOW":
            parts.append(f"düşük oynaklık — sıkışma (ATR x{vol['atr_ratio']:.1f})")

        return " → ".join(parts) if parts else ""

    # ═════════════════════════════════════════════════
    # YARDIMCI FONKSİYONLAR
    # ═════════════════════════════════════════════════
    def _ema(self, data, period):
        """Exponential Moving Average — son değeri döndürür"""
        if len(data) < period:
            return data[-1]
        multiplier = 2 / (period + 1)
        ema_val = data[0]
        for price in data[1:]:
            ema_val = (price - ema_val) * multiplier + ema_val
        return ema_val

    def _neutral_result(self, coin_list):
        """Veri yetersizse nötr sonuç döndür — tüm coinlere izin ver ama aday listesi boş"""
        filtered = {}
        for symbol in coin_list:
            if not self._is_btc(symbol):
                filtered[symbol] = {
                    "allowed_directions": ["LONG", "SHORT"],
                    "rs_score": 0,
                    "rs_data": None,
                    "is_candidate": False,
                }
        return {
            "regime": REGIME_NEUTRAL,
            "regime_details": {
                "btc_trend": {"bias": "NEUTRAL", "strength": "WEAK", "momentum": 0, "change_pcts": {}},
                "btc_dominance": {"direction": "NEUTRAL", "spread": 0, "btc_change": 0, "alt_avg_change": 0},
                "usdt_flow": {"direction": "NEUTRAL", "volume_change_pct": 0, "price_direction": "NEUTRAL"},
            },
            "btc_bias": "NEUTRAL",
            "rs_rankings": [],
            "long_candidates": [],
            "short_candidates": [],
            "filtered_coins": filtered,
            "timestamp": time.time(),
        }

    @staticmethod
    def _regime_label(regime):
        labels = {
            REGIME_RISK_ON: "Risk-On (Yükseliş)",
            REGIME_RISK_OFF: "Risk-Off (Düşüş)",
            REGIME_ALT_SEASON: "Alt Season",
            REGIME_CAPITULATION: "Kapitülasyon",
            REGIME_NEUTRAL: "Nötr",
        }
        return labels.get(regime, regime)

    @staticmethod
    def _regime_emoji(regime):
        emojis = {
            REGIME_RISK_ON: "🟢",
            REGIME_RISK_OFF: "🔴",
            REGIME_ALT_SEASON: "🚀",
            REGIME_CAPITULATION: "☠️",
            REGIME_NEUTRAL: "⚪",
        }
        return emojis.get(regime, "❓")


# Singleton
market_regime = MarketRegime()
