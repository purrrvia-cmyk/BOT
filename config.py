# =====================================================
# ICT Trading Bot - Konfigürasyon Dosyası
# =====================================================
# Tüm veriler OKX Public API'den gerçek zamanlı çekilir.
# Sabit coin listesi YOKTUR. 24 saatlik USDT hacmi
# MIN_VOLUME_USDT üzerindeki coinler otomatik taranır.
# =====================================================

import os

# OKX API (Ücretsiz Public Endpoints - API Key gerektirmez)
OKX_BASE_URL = "https://www.okx.com"
OKX_API_V5 = f"{OKX_BASE_URL}/api/v5"

# Veritabanı
DB_PATH = os.path.join(os.path.dirname(__file__), "ict_bot.db")

# =====================================================
# DİNAMİK COİN FİLTRESİ
# OKX'ten 24h hacmi MIN_VOLUME_USDT üzerindeki
# SWAP (perpetual futures) çiftleri gerçek zamanlı çekilir
# =====================================================
INST_TYPE = "SWAP"                # Enstrüman tipi: SWAP (vadeli), SPOT (spot)
MIN_VOLUME_USDT = 1_000_000      # Minimum 24 saatlik USDT hacmi (1 milyon $)
MAX_COINS_TO_SCAN = 100          # Tek seferde taranacak maksimum coin sayısı
VOLUME_REFRESH_INTERVAL = 300    # Hacim listesi yenileme aralığı (saniye = 5dk)

# Zaman Dilimleri
TIMEFRAMES = {
    "htf": "4H",      # Higher Time Frame - yapı analizi
    "mtf": "1H",      # Medium Time Frame - sinyal onayı
    "ltf": "15m",     # Lower Time Frame - giriş noktası
}

# ICT Strateji Parametreleri (v4.0 — Narrative → POI → Trigger)
# Optimizer tarafından güncellenir — sınırlar OPTIMIZER_PARAM_BOUNDS'ta
ICT_PARAMS = {
    # Market Structure
    "swing_lookback": 5,            # Swing high/low tespiti için bakılacak mum sayısı
    "bos_min_displacement": 0.003,  # BOS için minimum kırılım oranı (%0.3)
    
    # Order Block (v4.4: v4.2 gevşemesi geri alındı)
    "ob_max_age_candles": 30,       # v4.4: 35→30 — taze OB daha güvenilir
    "ob_body_ratio_min": 0.40,     # v4.4: 0.35→0.40 — güçlü gövdeli OB şart
    
    # Fair Value Gap (v4.4: v4.2 gevşemesi geri alındı)
    "fvg_min_size_pct": 0.001,     # v4.4: 0.0008→0.001 — mikro FVG güvenilmez
    "fvg_max_age_candles": 20,     # v4.4: 25→20 — eski FVG dolmuş olabilir
    
    # Liquidity
    "liquidity_equal_tolerance": 0.001,  # Eşit dip/tepe toleransı (%0.1)
    "liquidity_min_touches": 2,          # Minimum dokunma sayısı
    
    # Risk Yönetimi (v4.4: yapısal SL, min %1.2)
    "default_sl_pct": 0.020,       # Varsayılan SL (%2.0) - sadece fallback, yapısal SL öncelikli
    "max_sl_distance_pct": 0.030,  # Maximum SL mesafesi (%3.0) - risk limiti
    "sl_buffer_pct": 0.01,         # SL buffer wick extreme'den (%1)
    "max_concurrent_trades": 3,    # v4.0: 2→3 eşzamanlı işlem
    "max_same_direction_trades": 2,  # v4.0: 1→2 aynı yönde max işlem
    "min_sl_distance_pct": 0.012,  # v4.4: %0.8→%1.2 — kripto noise'a dayanıklı
    "signal_cooldown_minutes": 20, # Aynı coinde sinyal arası bekleme
    
    # Displacement (v4.4: v4.2 gevşemesi geri alındı — kalite öncelikli)
    "displacement_min_body_ratio": 0.55,  # v4.4: 0.50→0.55 — güçlü displacement şart
    "displacement_min_size_pct": 0.006,   # v4.4: 0.005→0.006 — zayıf hareketleri ele
    "displacement_atr_multiplier": 1.5,   # v4.4: 1.3→1.5 — gerçek displacement ATR×1.5 olmalı
    "displacement_max_candles_after_sweep": 3,  # v4.0: 2→3 ardışık mum izni
    
    # POI Confluence (v4.4: kalite öncelikli)
    "poi_max_distance_pct": 0.010,        # v4.4: 0.012→0.010 — daha yakın POI = daha iyi giriş
    "obstacle_proximity_pct": 0.003,      # Engel yakınlık eşiği (%0.3)
    "min_rr_ratio": 2.0,                  # v4.4: 1.4→2.0 — BE ile bile kârlı olacak şekilde RR şart
}

# İşlem Süre Ayarları (v4.0: LIMIT kaldırıldı → MARKET only)
MAX_TRADE_DURATION_HOURS = 6     # v4.4: 4→6 saat — TP'ye ulaşmak için yeterli süre

# Optimizer Parametreleri (v3.0 — SMC Threshold Optimizer)
OPTIMIZER_CONFIG = {
    "min_trades_for_optimization": 20,   # Optimizasyon için minimum işlem — 15→20: daha güvenilir istatistik
    "optimization_interval_minutes": 30, # Optimizasyon aralığı (dakika)
    "learning_rate": 0.03,              # Öğrenme hızı — küçük adımlarla yakınsama
    "max_param_change_pct": 0.10,       # Tek seferde max parametre değişimi (%10)
    "win_rate_target": 0.55,            # Hedef kazanma oranı (%55)
}

# Optimizer Parametre Sınırları (v4.0 — Narrative/POI/Trigger Optimizer)
# Her parametre için [min, max] güvenli aralık.
# Death spiral koruması: optimizer asla bu sınırların dışına çıkamaz.
OPTIMIZER_PARAM_BOUNDS = {
    # Displacement
    "displacement_min_body_ratio": (0.40, 0.75),
    "displacement_min_size_pct": (0.002, 0.010),
    "displacement_atr_multiplier": (1.00, 2.50),
    # FVG
    "fvg_min_size_pct": (0.0003, 0.004),
    "fvg_max_age_candles": (10, 40),
    # Liquidity
    "liquidity_equal_tolerance": (0.0003, 0.003),
    # Yapısal: OB & Swing
    "ob_body_ratio_min": (0.25, 0.65),
    "ob_max_age_candles": (15, 50),
    "swing_lookback": (3, 8),
    # Risk: SL
    "default_sl_pct": (0.008, 0.025),
    # POI
    "poi_max_distance_pct": (0.005, 0.020),
    "min_rr_ratio": (1.80, 4.00),  # v4.4: alt sınır 1.8 (BE ile bile kârlı olmalı)
}

# Tarama Aralıkları
SCAN_INTERVAL_SECONDS = 180  # Tarama aralığı (100 coin × 4 TF ≈ 165s, 180s güvenli)
TRADE_CHECK_INTERVAL = 5    # Açık işlem kontrolü (saniye) — 10→5: daha hızlı SL/TP tepkisi

# İzleme Akışı (v4.0: POI-trigger tabanlı, mum sayma yok)
# SIGNAL → direkt MARKET giriş (bekleme yok)
# WATCH → watchlist → trigger oluşunca promote
WATCH_CONFIRM_TIMEFRAME = "5m"          # Watchlist kontrol TF'si
WATCH_CONFIRM_CANDLES = 12              # v4.1: max 12×5m = 60dk (1 saat) watchlist süresi
WATCH_CHECK_INTERVAL = 60               # Watchlist kontrol aralığı (saniye)
WATCH_REQUIRED_CONFIRMATIONS = 1        # Uyumluluk için kalıyor

# Web Server
HOST = "0.0.0.0"
PORT = 5000
DEBUG = False
