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

# ICT Strateji Parametreleri (başlangıç değerleri - optimizer tarafından güncenlenir)
# v3.4: Crypto-optimized, killzone bypass, RR-free, quality-first
ICT_PARAMS = {
    # Market Structure
    "swing_lookback": 5,            # Swing high/low tespiti için bakılacak mum sayısı
    "bos_min_displacement": 0.003,  # BOS için minimum kırılım oranı (%0.3)
    
    # Order Block
    "ob_max_age_candles": 30,       # Order Block'un geçerlilik süresi (mum sayısı)
    "ob_body_ratio_min": 0.4,      # OB mumunun minimum gövde/fitil oranı
    
    # Fair Value Gap
    "fvg_min_size_pct": 0.001,     # Minimum FVG boyutu (fiyatın %'si)
    "fvg_max_age_candles": 20,     # FVG'nin geçerlilik süresi
    
    # Liquidity
    "liquidity_equal_tolerance": 0.001,  # Eşit dip/tepe toleransı (%0.1)
    "liquidity_min_touches": 2,          # Minimum dokunma sayısı
    
    # Risk Yönetimi (RR kontrolsüz - manuel exit)
    "default_sl_pct": 0.020,       # Varsayılan SL (%2.0) - sadece fallback, yapısal SL öncelikli
    "max_sl_distance_pct": 0.030,  # Maximum SL mesafesi (%3.0) - risk limiti
    "sl_buffer_pct": 0.01,         # SL buffer wick extreme'den (%1)
    "max_concurrent_trades": 2,    # Maksimum eşzamanlı işlem
    "max_same_direction_trades": 1,  # Aynı yönde max işlem
    "min_sl_distance_pct": 0.005,  # Minimum SL mesafesi (%0.5) - çok dar SL'ye izin verme
    "signal_cooldown_minutes": 20, # Aynı coinde sinyal arası bekleme
    
    # Displacement (v3.4: crypto volatility için yükseltildi)
    "displacement_min_body_ratio": 0.55,  # 0.5→0.55: daha güçlü displacement
    "displacement_min_size_pct": 0.006,   # 0.002→0.006: %0.6 minimum (%0.2 noise yakalıyordu)
    "displacement_atr_multiplier": 1.5,   # 1.2→1.5: ATR × 1.5 (gerçek displacement)
    "displacement_max_candles_after_sweep": 2,  # Sweep sonrası max 2 mum içinde displacement
}

# Limit Emir Ayarları
LIMIT_ORDER_EXPIRY_HOURS = 3.0   # Limit emir geçerlilik süresi (saat) — FVG pullback için yeterli süre
                                 # FVG'ye limit emir koyulduğunda max bekleme zamanı
MAX_TRADE_DURATION_HOURS = 4     # Aktif işlem max yaşam süresi (saat) — 8h→4h: 15m TF için yeterli
                                 # 15m TF sinyal geçerliliği: uzun süren işlemler kaybetme eğiliminde

# Optimizer Parametreleri (v3.0 — SMC Threshold Optimizer)
OPTIMIZER_CONFIG = {
    "min_trades_for_optimization": 20,   # Optimizasyon için minimum işlem — 15→20: daha güvenilir istatistik
    "optimization_interval_minutes": 30, # Optimizasyon aralığı (dakika)
    "learning_rate": 0.03,              # Öğrenme hızı — küçük adımlarla yakınsama
    "max_param_change_pct": 0.10,       # Tek seferde max parametre değişimi (%10)
    "win_rate_target": 0.55,            # Hedef kazanma oranı (%55)
}

# Optimizer Parametre Sınırları (v3.0 — SMC Threshold Optimizer)
# Her parametre için [min, max] güvenli aralık.
# Death spiral koruması: optimizer asla bu sınırların dışına çıkamaz.
# NOT: min_confidence ve min_confluence_score artık v3.0'da optimize EDİLMEZ
#      (Boolean gate sistemi — tüm sinyaller 100/100 ile gelir).
OPTIMIZER_PARAM_BOUNDS = {
    # Gate 4: Displacement
    "displacement_min_body_ratio": (0.40, 0.75),
    "displacement_min_size_pct": (0.001, 0.005),
    "displacement_atr_multiplier": (0.80, 2.00),
    # Gate 5: FVG
    "fvg_min_size_pct": (0.0003, 0.004),
    "fvg_max_age_candles": (10, 40),
    # Gate 3: Liquidity
    "liquidity_equal_tolerance": (0.0003, 0.003),
    # Yapısal: OB & Swing
    "ob_body_ratio_min": (0.25, 0.65),
    "ob_max_age_candles": (15, 50),
    "swing_lookback": (3, 8),
    # Risk: SL & TP
    "default_sl_pct": (0.006, 0.025),
    "default_tp_ratio": (1.50, 4.00),
}

# Tarama Aralıkları
SCAN_INTERVAL_SECONDS = 180  # Tarama aralığı (100 coin × 4 TF ≈ 165s, 180s güvenli)
TRADE_CHECK_INTERVAL = 5    # Açık işlem kontrolü (saniye) — 10→5: daha hızlı SL/TP tepkisi

# İzleme Onay Akışı (v3.6: 3×5m mum = 15dk)
WATCH_CONFIRM_TIMEFRAME = "5m"          # İzleme zaman dilimi: 5 dakikalık grafikte izle
WATCH_CONFIRM_CANDLES = 3               # 3 × 5m mum bekle = toplam 15dk
WATCH_CHECK_INTERVAL = 60               # İzleme kontrolü aralığı (saniye) — 5m mum için 60s yeterli
WATCH_REQUIRED_CONFIRMATIONS = 1        # Onay gerekli
# Akış: ICT WATCH sinyali → 3 × 5m mum bekle → hâlâ geçerliyse SIGNAL'e promote

# Web Server
HOST = "0.0.0.0"
PORT = 5000
DEBUG = False
