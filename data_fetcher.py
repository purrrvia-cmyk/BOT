# =====================================================
# ICT Trading Bot - OKX Gerçek Zamanlı Veri Modülü
# =====================================================
# OKX Public API üzerinden gerçek piyasa verileri çeker.
# 24 saatlik hacmi MIN_VOLUME_USDT üzerindeki coinleri dinamik filtreler.
# Hiçbir demo/mock/test verisi kullanılmaz.
# =====================================================

import requests
import pandas as pd
import numpy as np
import time
import logging
from datetime import datetime, timedelta
from config import OKX_API_V5, MIN_VOLUME_USDT, MAX_COINS_TO_SCAN, VOLUME_REFRESH_INTERVAL, INST_TYPE

logger = logging.getLogger("ICT-Bot.DataFetcher")


class OKXDataFetcher:
    """OKX Public API'den gerçek zamanlı veri çeken sınıf"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "ICT-Trading-Bot/1.0"
        })
        self._cache = {}
        self._cache_ttl = 45  # saniye (100 coin taraması ~165s, kısa TTL cache'i etkisiz kılar)
        self._active_coins = []           # Dinamik coin listesi
        self._coins_last_refresh = 0      # Son yenileme zamanı
        self._coin_volumes = {}           # Coin -> hacim bilgisi

    def _make_request(self, endpoint, params=None, max_retries=3):
        """API isteği gönder (rate limit retry + exponential backoff)"""
        url = f"{OKX_API_V5}{endpoint}"
        for attempt in range(max_retries):
            try:
                response = self.session.get(url, params=params, timeout=10)

                # HTTP 429 Rate Limit
                if response.status_code == 429:
                    wait = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning(f"⚠️ OKX Rate Limit (429) — {wait}s bekleniyor... (deneme {attempt+1}/{max_retries})")
                    time.sleep(wait)
                    continue

                response.raise_for_status()
                data = response.json()
                code = data.get("code", "")

                # OKX özel rate limit hata kodu (50011)
                if code == "50011":
                    wait = 2 ** attempt
                    logger.warning(f"⚠️ OKX Too Many Requests (50011) — {wait}s bekleniyor... (deneme {attempt+1}/{max_retries})")
                    time.sleep(wait)
                    continue

                if code == "0":
                    return data.get("data", [])
                else:
                    logger.warning(f"OKX API hatası [code={code}]: {data.get('msg', 'Bilinmeyen hata')}")
                    return []

            except requests.exceptions.Timeout:
                wait = 2 ** attempt
                logger.warning(f"⏱️ OKX API timeout — {wait}s bekleniyor... (deneme {attempt+1}/{max_retries})")
                time.sleep(wait)
                continue
            except requests.exceptions.RequestException as e:
                logger.error(f"OKX API bağlantı hatası: {e}")
                return []

        logger.error(f"OKX API {max_retries} denemede başarısız: {endpoint}")
        return []

    def get_candles(self, symbol, timeframe="15m", limit=100):
        """
        Mum verilerini çek
        Returns: DataFrame [timestamp, open, high, low, close, volume]
        """
        cache_key = f"candles_{symbol}_{timeframe}_{limit}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        params = {
            "instId": symbol,
            "bar": timeframe,
            "limit": str(limit)
        }
        data = self._make_request("/market/candles", params)

        if not data:
            return pd.DataFrame()

        # OKX formatı: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        df = pd.DataFrame(data, columns=[
            "timestamp", "open", "high", "low", "close",
            "volume", "volCcy", "volCcyQuote", "confirm"
        ])

        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(float), unit="ms")
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        df = df[["timestamp", "open", "high", "low", "close", "volume"]]
        df = df.sort_values("timestamp").reset_index(drop=True)

        self._set_cache(cache_key, df)
        return df

    def get_ticker(self, symbol):
        """Anlık fiyat bilgisi"""
        cache_key = f"ticker_{symbol}"
        cached = self._get_cached(cache_key, ttl=5)
        if cached is not None:
            return cached

        params = {"instId": symbol}
        data = self._make_request("/market/ticker", params)

        if data:
            ticker = {
                "symbol": data[0].get("instId"),
                "last": float(data[0].get("last", 0)),
                "bid": float(data[0].get("bidPx", 0)),
                "ask": float(data[0].get("askPx", 0)),
                "high24h": float(data[0].get("high24h", 0)),
                "low24h": float(data[0].get("low24h", 0)),
                "vol24h": float(data[0].get("vol24h", 0)),
                "change24h": float(data[0].get("last", 0)) - float(data[0].get("open24h", 0)),
                "changePct24h": ((float(data[0].get("last", 0)) - float(data[0].get("open24h", 1))) /
                                 float(data[0].get("open24h", 1))) * 100
                                 if float(data[0].get("open24h", 0)) > 0 else 0,
                "timestamp": datetime.now().isoformat()
            }
            self._set_cache(cache_key, ticker, ttl=5)
            return ticker

        return None

    def get_all_tickers(self, inst_type=None):
        """Tüm USDT çiftlerinin gerçek zamanlı fiyat ve hacim verilerini çek"""
        if inst_type is None:
            inst_type = INST_TYPE
        cache_key = f"all_tickers_{inst_type}"
        cached = self._get_cached(cache_key, ttl=10)
        if cached is not None:
            return cached

        params = {"instType": inst_type}
        data = self._make_request("/market/tickers", params)

        # SWAP suffix: BTC-USDT-SWAP, SPOT: BTC-USDT
        suffix = "-USDT-SWAP" if inst_type == "SWAP" else "-USDT"

        tickers = {}
        for item in data:
            symbol = item.get("instId", "")
            if not symbol.endswith(suffix):
                continue

            last_price = float(item.get("last", 0))
            vol_coin = float(item.get("vol24h", 0))           # Coin cinsinden hacim (SWAP: kontrat sayısı)
            vol_ccy = float(item.get("volCcy24h", 0))         # SWAP: underlying asset cinsinden, SPOT: quote (USDT)
            open_24h = float(item.get("open24h", 0))

            # ── Hacim hesaplama ──
            # SWAP: volCcy24h underlying asset cinsinden (BTC, ETH, SOL vb.)
            #       USDT'ye çevirmek için last_price ile çarpmak lazım
            # SPOT: volCcy24h zaten quote currency (USDT) cinsinden
            if inst_type == "SWAP":
                volume_usdt = vol_ccy * last_price if vol_ccy > 0 else 0
            else:
                volume_usdt = vol_ccy if vol_ccy > 0 else (vol_coin * last_price)

            change_pct = 0
            if open_24h > 0:
                change_pct = ((last_price - open_24h) / open_24h) * 100

            tickers[symbol] = {
                "symbol": symbol,
                "last": last_price,
                "vol24h": vol_coin,
                "vol24h_usdt": volume_usdt,
                "changePct24h": round(change_pct, 2)
            }

        self._set_cache(cache_key, tickers, ttl=10)
        return tickers

    # =================== DİNAMİK COİN LİSTESİ ===================

    def get_high_volume_coins(self, force_refresh=False):
        """
        OKX'ten 24 saatlik hacmi MIN_VOLUME_USDT üzerindeki USDT çiftlerini çek.
        Sonuçlar hacme göre sıralanır. Her VOLUME_REFRESH_INTERVAL saniyede yenilenir.
        Sabit/hardcoded liste YOKTUR - tamamen gerçek zamanlı.
        """
        now = time.time()
        if not force_refresh and self._active_coins and (now - self._coins_last_refresh) < VOLUME_REFRESH_INTERVAL:
            return self._active_coins

        logger.info(f"📊 OKX'ten yüksek hacimli {INST_TYPE} coinler çekiliyor (min ${MIN_VOLUME_USDT:,.0f})...")

        tickers = self.get_all_tickers(INST_TYPE)
        if not tickers:
            logger.warning("OKX ticker verisi alınamadı!")
            return self._active_coins if self._active_coins else []

        # Hacme göre filtrele ve sırala
        qualified = []
        for symbol, data in tickers.items():
            vol_usdt = data.get("vol24h_usdt", 0)
            if vol_usdt >= MIN_VOLUME_USDT:
                qualified.append({
                    "symbol": symbol,
                    "volume_usdt": vol_usdt,
                    "last_price": data["last"],
                    "change_pct": data["changePct24h"]
                })

        # Hacme göre büyükten küçüğe sırala
        qualified.sort(key=lambda x: x["volume_usdt"], reverse=True)

        # Maksimum coin sayısını uygula
        qualified = qualified[:MAX_COINS_TO_SCAN]

        self._active_coins = [c["symbol"] for c in qualified]
        self._coin_volumes = {c["symbol"]: c for c in qualified}
        self._coins_last_refresh = now

        logger.info(f"✅ {len(self._active_coins)} coin bulundu (24h hacim ≥ ${MIN_VOLUME_USDT:,.0f})")
        if self._active_coins:
            top3 = self._active_coins[:3]
            top3_info = [f"{s} (${self._coin_volumes[s]['volume_usdt']:,.0f})" for s in top3]
            logger.info(f"   En yüksek hacimli: {', '.join(top3_info)}")

        return self._active_coins

    def get_coin_volume_info(self, symbol):
        """Belirli bir coinin hacim bilgisini döndür"""
        return self._coin_volumes.get(symbol, None)

    def get_all_coin_volumes(self):
        """Tüm aktif coinlerin hacim bilgilerini döndür"""
        return self._coin_volumes

    def get_multi_timeframe_data(self, symbol):
        """
        Birden fazla zaman diliminde veri çek
        HTF (4H)  -> Yapı analizi + HTF Bias Gate
        MTF (1H)  -> Sinyal onayı + MTF trend kontrolü
        LTF (15m) -> Giriş noktası + Sweep/Displacement/FVG tespiti
        5m        -> Watchlist onay akışı (5 dakikalık mum takibi)
        
        Optimizasyon: 15m (en kritik TF) önce çekilir.
        Boşsa diğer TF'ler atlanır (gereksiz API çağrısı önlenir).
        """
        data = {}

        # 15 dakikalık - LTF (en kritik, giriş noktası)
        data["15m"] = self.get_candles(symbol, "15m", 100)
        if data["15m"] is None or data["15m"].empty:
            # 15m yoksa diğer TF'leri çekmeye gerek yok
            data["4h"] = pd.DataFrame()
            data["1h"] = pd.DataFrame()
            data["5m"] = pd.DataFrame()
            return data

        time.sleep(0.1)  # Rate limit

        # 4 saatlik - HTF Bias (yapı analizi)
        data["4h"] = self.get_candles(symbol, "4H", 100)
        time.sleep(0.1)

        # 1 saatlik - MTF (sinyal onayı)
        data["1h"] = self.get_candles(symbol, "1H", 100)
        time.sleep(0.1)

        # 5 dakikalık - Watchlist onay akışı
        data["5m"] = self.get_candles(symbol, "5m", 120)

        return data

    def get_orderbook(self, symbol, depth=20):
        """Order book verisi (destek/direnç seviyeleri için)"""
        params = {
            "instId": symbol,
            "sz": str(depth)
        }
        data = self._make_request("/market/books", params)

        if data:
            book = {
                "asks": [[float(x[0]), float(x[1])] for x in data[0].get("asks", [])],
                "bids": [[float(x[0]), float(x[1])] for x in data[0].get("bids", [])],
                "timestamp": datetime.now().isoformat()
            }
            return book
        return None

    # =================== PİYASA VERİLERİ (Funding, OI, LS Ratio) ===================

    def get_funding_rate(self, symbol):
        """Fonlama oranını çek (SWAP perpetual)"""
        cache_key = f"funding_{symbol}"
        cached = self._get_cached(cache_key, ttl=30)
        if cached is not None:
            return cached

        # Güncel fonlama oranı
        params = {"instId": symbol}
        data = self._make_request("/public/funding-rate", params)

        result = {"current": 0, "next": 0, "next_time": None}
        if data:
            fr_str = data[0].get("fundingRate", "0") or "0"
            nfr_str = data[0].get("nextFundingRate", "0") or "0"
            result["current"] = float(fr_str) * 100  # % olarak
            result["next"] = float(nfr_str) * 100
            next_ts = data[0].get("nextFundingTime")
            if next_ts:
                result["next_time"] = datetime.fromtimestamp(int(next_ts) / 1000).strftime("%H:%M")

        self._set_cache(cache_key, result, ttl=30)
        return result

    def get_open_interest(self, symbol):
        """Açık faiz verisi (SWAP)"""
        cache_key = f"oi_{symbol}"
        cached = self._get_cached(cache_key, ttl=30)
        if cached is not None:
            return cached

        params = {"instId": symbol}
        data = self._make_request("/public/open-interest", params)

        result = {"oi": 0, "oi_usdt": 0}
        if data:
            oi_val = float(data[0].get("oi", 0) or 0)
            oi_usd = float(data[0].get("oiUsd", 0) or 0)
            result["oi"] = oi_val
            result["oi_usdt"] = oi_usd

        self._set_cache(cache_key, result, ttl=30)
        return result

    def get_long_short_ratio(self, symbol):
        """Long/Short oranı (5m, 1H, 24H)"""
        cache_key = f"lsr_{symbol}"
        cached = self._get_cached(cache_key, ttl=60)
        if cached is not None:
            return cached

        # OKX instId -> ccy (BTC-USDT-SWAP -> BTC)
        ccy = symbol.split("-")[0]
        result = {}
        for period in ["5m", "1H", "1D"]:
            params = {"ccy": ccy, "period": period}
            data = self._make_request("/rubik/stat/contracts/long-short-account-ratio", params)
            if data and len(data) > 0:
                latest = data[0]
                # API döndürür: [[timestamp, ratio], ...] veya [{"ts":..., "longShortRatio":...}, ...]
                if isinstance(latest, list) and len(latest) >= 2:
                    lsr_val = latest[1]
                elif isinstance(latest, dict):
                    lsr_val = latest.get("longShortRatio", "")
                else:
                    lsr_val = None
                result[period] = round(float(lsr_val), 4) if lsr_val else None
            else:
                result[period] = None

            time.sleep(0.1)

        self._set_cache(cache_key, result, ttl=60)
        return result

    # =================== CACHE ===================

    def _get_cached(self, key, ttl=None):
        if ttl is None:
            ttl = self._cache_ttl
        if key in self._cache:
            entry = self._cache[key]
            if time.time() - entry["time"] < ttl:
                return entry["data"]
        return None

    def _set_cache(self, key, data, ttl=None):
        self._cache[key] = {
            "data": data,
            "time": time.time()
        }

    def clear_cache(self):
        self._cache.clear()


# Global instance
data_fetcher = OKXDataFetcher()
