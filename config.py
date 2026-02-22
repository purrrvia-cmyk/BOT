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

# ICT Strateji Parametreleri (başlangıç değerleri - optimizer tarafından güncellenir)
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
    
    # Sinyal Üretimi
    "min_confluence_score": 50,     # Minimum confluent skor (0-100)
    "min_confidence": 55,           # Minimum güven skoru (0-100)
    
    # Risk Yönetimi
    "default_sl_pct": 0.015,       # Varsayılan stop loss (%1.5)
    "default_tp_ratio": 2.0,       # TP/SL oranı (Risk-Reward)
    "max_concurrent_trades": 5,    # Maksimum eşzamanlı işlem
    "min_sl_distance_pct": 0.008,  # Minimum SL mesafesi (%0.8) - noise koruması
    "signal_cooldown_minutes": 10, # Aynı coinde sinyal arası bekleme (dakika) — sadece kapanmış işlemler
    
    # Sabırlı Mod
    "patience_watch_candles": 3,    # Sinyal öncesi izlenecek mum sayısı
    "patience_confirm_threshold": 0.6,  # Onay eşiği
    
    # Displacement
    "displacement_min_body_ratio": 0.6,  # Displacement mumu min gövde oranı (0.7→0.6: daha fazla displacement tespit edilir)
    "displacement_min_size_pct": 0.003,  # Min displacement boyutu (%0.3) (0.5→0.3: daha hassas)
}

# Limit Emir Ayarları
LIMIT_ORDER_EXPIRY_HOURS = 2  # Limit emir geçerlilik süresi (saat)
                              # FVG'ye limit emir koyulduğunda max bekleme zamanı
MAX_TRADE_DURATION_HOURS = 8  # Aktif işlem max yaşam süresi (saat)
                              # 15m TF sinyal geçerliliği: uzun süren işlemler kaybetme eğiliminde

# Optimizer Parametreleri
OPTIMIZER_CONFIG = {
    "min_trades_for_optimization": 5,    # Optimizasyon için minimum işlem sayısı (10→5: daha hızlı öğrenme)
    "optimization_interval_minutes": 30, # Optimizasyon aralığı (dakika)
    "learning_rate": 0.05,              # Öğrenme hızı
    "max_param_change_pct": 0.15,       # Tek seferde max parametre değişimi (%15)
    "win_rate_target": 0.60,            # Hedef kazanma oranı (%60)
}

# Optimizer Parametre Sınırları (death spiral koruması)
OPTIMIZER_PARAM_BOUNDS = {
    "swing_lookback": (3, 7),
    "fvg_min_size_pct": (0.0005, 0.003),
    "displacement_min_body_ratio": (0.45, 0.75),
    "liquidity_equal_tolerance": (0.0005, 0.002),
    "ob_body_ratio_min": (0.3, 0.6),
    "min_confidence": (40, 80),
    "min_confluence_score": (35, 75),
    "default_sl_pct": (0.008, 0.025),
    "default_tp_ratio": (1.5, 3.0),
    "displacement_min_size_pct": (0.002, 0.006),
    "ob_max_age_candles": (15, 40),
}

# Tarama Aralıkları
SCAN_INTERVAL_SECONDS = 180  # Tarama aralığı (100 coin × 4 TF ≈ 165s, 180s güvenli)
TRADE_CHECK_INTERVAL = 10   # Açık işlem kontrolü (saniye) — 30→10: slippage azaltma

# QPA Tarama (ICT ile eşzamanlı ama bağımsız)
QPA_SCAN_ENABLED = True     # QPA stratejisi aktif mi?

# İzleme Onay Akışı (zorunlu)
WATCH_CONFIRM_TIMEFRAME = "5m"          # İzleme zaman dilimi
WATCH_CONFIRM_CANDLES = 3               # Kaç mum kapanışı izlenecek
WATCH_REQUIRED_CONFIRMATIONS = 2        # 3 mum içinde 2 onay gerekli
# v2 kriterler: NEUTRAL trend → otomatik onay değil, mum gövde filtresi,
# hacim doğrulaması (%80 ort.), entry mesafe kontrolü (max %2)

# Web Server
HOST = "0.0.0.0"
PORT = 5000
DEBUG = False
