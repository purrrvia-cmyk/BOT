# =====================================================
# ICT Trading Bot - Ana Flask Uygulaması
# =====================================================

import eventlet
eventlet.monkey_patch()

import logging
import time
import json
import threading
from datetime import datetime
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler

from config import (
    HOST, PORT, DEBUG,
    SCAN_INTERVAL_SECONDS, TRADE_CHECK_INTERVAL,
    OPTIMIZER_CONFIG, ICT_PARAMS, MIN_VOLUME_USDT
)
from database import (
    init_db, get_active_signals, get_signal_history,
    get_watching_items, get_optimization_logs,
    get_performance_summary, update_signal_status,
    get_bot_param, get_recently_expired
)
from data_fetcher import data_fetcher
from ict_strategy import ict_strategy
from trade_manager import trade_manager
from self_optimizer import self_optimizer
from market_regime import market_regime
from forex_ict import forex_ict, FOREX_INSTRUMENTS

# =================== LOGGING ===================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("ICT-Bot")

# =================== FLASK APP ===================

app = Flask(__name__)
app.config["SECRET_KEY"] = "ict-bot-secret-2024"
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# Bot durumu
bot_state = {
    "running": False,
    "last_scan": None,
    "last_trade_check": None,
    "last_optimization": None,
    "scan_count": 0,
    "symbols_scanned": 0,
    "errors": []
}

scan_lock = threading.Lock()


# =================== ARKA PLAN GÖREVLERİ ===================

def scan_markets():
    """OKX'ten gerçek zamanlı yüksek hacimli coinleri tara ve sinyal üret"""
    if not bot_state["running"]:
        return

    if not scan_lock.acquire(blocking=False):
        return

    try:
        logger.info("🔍 Piyasa taraması başlıyor...")
        bot_state["last_scan"] = datetime.now().isoformat()
        bot_state["scan_count"] += 1
        symbols_scanned = 0
        new_signals = []

        # OKX'ten hacimli coinleri gerçek zamanlı çek
        active_coins = data_fetcher.get_high_volume_coins()
        bot_state["active_coin_count"] = len(active_coins)

        if not active_coins:
            logger.warning("OKX'ten yüksek hacimli coin bulunamadı, bağlantı kontrol edin")
            return

        # ── Piyasa rejimi analizi (sadece bilgi amaçlı, ICT'yi filtrelemez) ──
        try:
            regime_result = market_regime.analyze_market(active_coins)
            regime = regime_result["regime"]
            bot_state["current_regime"] = regime
            bot_state["btc_bias"] = regime_result["btc_bias"]
            bot_state["long_candidates"] = len(regime_result["long_candidates"])
            bot_state["short_candidates"] = len(regime_result["short_candidates"])
            socketio.emit("regime_update", market_regime.get_regime_summary())
        except Exception as e:
            logger.warning(f"Rejim analizi hatası (tarama devam eder): {e}")
            regime = bot_state.get("current_regime", "UNKNOWN")

        # ── Tüm coinleri ICT ile tara (rejim filtresi yok) ──
        for symbol in active_coins:
            if market_regime._is_btc(symbol):
                continue  # BTC referans, sinyale gerek yok

            try:
                # Gerçek zamanlı çoklu zaman dilimi verisi çek
                multi_tf = data_fetcher.get_multi_timeframe_data(symbol)
                ltf_data = multi_tf.get("15m")

                if ltf_data is None or ltf_data.empty:
                    continue

                # ICT strateji analizi — tüm yönler serbest
                result = ict_strategy.generate_signal(symbol, ltf_data, multi_tf)

                if result:
                    trade_result = trade_manager.process_signal(result)
                    if trade_result:
                        trade_result["regime"] = regime
                        new_signals.append(trade_result)
                        socketio.emit("new_signal", trade_result)

                symbols_scanned += 1
                time.sleep(0.15)  # Rate limit

            except Exception as e:
                logger.error(f"Hata ({symbol}): {e}")
                bot_state["errors"].append({
                    "time": datetime.now().isoformat(),
                    "symbol": symbol,
                    "error": str(e)
                })
                bot_state["errors"] = bot_state["errors"][-20:]

        bot_state["symbols_scanned"] = symbols_scanned
        logger.info(f"✅ Tarama tamamlandı: {symbols_scanned} coin, {len(new_signals)} sinyal | Rejim: {regime}")

        # Dashboard güncelle
        socketio.emit("scan_complete", {
            "symbols_scanned": symbols_scanned,
            "new_signals": len(new_signals),
            "regime": regime,
            "timestamp": datetime.now().isoformat()
        })

    except Exception as e:
        logger.error(f"Tarama hatası: {e}")
    finally:
        scan_lock.release()


def check_trades():
    """Açık işlemleri kontrol et"""
    if not bot_state["running"]:
        return

    try:
        results = trade_manager.check_open_trades()
        bot_state["last_trade_check"] = datetime.now().isoformat()

        # Kapanan işlemleri bildir
        for r in results:
            if r["status"] in ["WON", "LOST"]:
                socketio.emit("trade_closed", r)

        # İzleme listesini kontrol et
        promoted = trade_manager.check_watchlist(ict_strategy)
        for p in promoted:
            socketio.emit("watch_promoted", p)

        # Dashboard güncelle
        socketio.emit("trades_updated", {
            "active_results": results,
            "timestamp": datetime.now().isoformat()
        })

    except Exception as e:
        logger.error(f"İşlem kontrol hatası: {e}")


def run_optimizer():
    """Otomatik optimizasyonu çalıştır — scan_lock gerektirmez."""
    if not bot_state["running"]:
        return

    try:
        # ICT Optimizer — DB okuma + param yazma, tarama ile çakışma riski yok
        result = self_optimizer.run_optimization()
        bot_state["last_optimization"] = datetime.now().isoformat()

        if result["changes"]:
            ict_strategy.reload_params()
            socketio.emit("optimization_done", result)
            logger.info(f"🧠 ICT Optimizasyon: {len(result['changes'])} değişiklik")

    except Exception as e:
        logger.error(f"Optimizasyon hatası: {e}")


# Scheduler - her start/stop döngüsünde yeniden oluşturulur
scheduler = None

def create_scheduler():
    """Yeni scheduler oluştur (shutdown sonrası yeniden kullanılamaz)"""
    global scheduler
    scheduler = BackgroundScheduler()
    return scheduler



# =================== API ROUTES ===================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def api_health():
    """Health check — Render uyku engelleme ve durum kontrolü"""
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})


@app.route("/api/status")
def api_status():
    """Bot durumu"""
    active_coins = data_fetcher.get_high_volume_coins()
    return jsonify({
        "running": bot_state["running"],
        "last_scan": bot_state["last_scan"],
        "last_trade_check": bot_state["last_trade_check"],
        "last_optimization": bot_state["last_optimization"],
        "scan_count": bot_state["scan_count"],
        "symbols_scanned": bot_state["symbols_scanned"],
        "watchlist_count": len(active_coins),
        "min_volume": MIN_VOLUME_USDT,
        "current_regime": bot_state.get("current_regime", "UNKNOWN"),
        "btc_bias": bot_state.get("btc_bias", "UNKNOWN"),
        "long_candidates": bot_state.get("long_candidates", 0),
        "short_candidates": bot_state.get("short_candidates", 0),
        "server_time": datetime.now().isoformat()
    })


@app.route("/api/start", methods=["POST"])
def api_start():
    """Botu başlat"""
    if bot_state["running"]:
        return jsonify({"status": "already_running"})

    bot_state["running"] = True

    # Yeni scheduler oluştur ve görevleri ekle
    create_scheduler()
    scheduler.add_job(scan_markets, "interval", seconds=SCAN_INTERVAL_SECONDS,
                     id="scan_markets", replace_existing=True)
    scheduler.add_job(check_trades, "interval", seconds=TRADE_CHECK_INTERVAL,
                     id="check_trades", replace_existing=True)
    scheduler.add_job(run_optimizer, "interval",
                     minutes=OPTIMIZER_CONFIG["optimization_interval_minutes"],
                     id="run_optimizer", replace_existing=True)
    scheduler.start()

    # İlk taramayı hemen yap
    threading.Thread(target=scan_markets, daemon=True).start()

    logger.info("🚀 Bot başlatıldı!")
    socketio.emit("bot_status", {"running": True})

    return jsonify({"status": "started"})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """Botu durdur"""
    bot_state["running"] = False

    global scheduler
    if scheduler and scheduler.running:
        try:
            scheduler.remove_all_jobs()
            scheduler.shutdown(wait=False)
        except Exception:
            pass
    scheduler = None

    logger.info("🛑 Bot durduruldu!")
    socketio.emit("bot_status", {"running": False})

    return jsonify({"status": "stopped"})


@app.route("/api/signals/active")
def api_active_signals():
    """Aktif sinyaller"""
    signals = get_active_signals()
    # Her sinyal için güncel fiyat ekle
    for s in signals:
        ticker = data_fetcher.get_ticker(s["symbol"])
        if ticker:
            s["current_price"] = ticker["last"]
            entry = s["entry_price"]
            if s["direction"] == "LONG":
                s["unrealized_pnl"] = round(((ticker["last"] - entry) / entry) * 100, 2)
            else:
                s["unrealized_pnl"] = round(((entry - ticker["last"]) / entry) * 100, 2)
        else:
            s["current_price"] = None
            s["unrealized_pnl"] = 0
    return jsonify(signals)


@app.route("/api/signals/history")
def api_signal_history():
    """Sinyal geçmişi"""
    limit = request.args.get("limit", 50, type=int)
    history = get_signal_history(limit)
    return jsonify(history)


@app.route("/api/watchlist")
def api_watchlist():
    """İzleme listesi + son expired"""
    items = get_watching_items()
    return jsonify(items)


@app.route("/api/watchlist/expired")
def api_watchlist_expired():
    """Son 30 dakikada expire edilen öğeler (neden bilgisiyle)"""
    minutes = request.args.get("minutes", 30, type=int)
    items = get_recently_expired(minutes)
    return jsonify(items)


@app.route("/api/performance")
def api_performance():
    """Performans istatistikleri"""
    stats = get_performance_summary()
    return jsonify(stats)


@app.route("/api/optimization/logs")
def api_optimization_logs():
    """Optimizasyon logları"""
    limit = request.args.get("limit", 30, type=int)
    logs = get_optimization_logs(limit)
    return jsonify(logs)


@app.route("/api/optimization/summary")
def api_optimization_summary():
    """Optimizasyon özeti"""
    summary = self_optimizer.get_optimization_summary()
    summary["last_optimization"] = bot_state.get("last_optimization")
    return jsonify(summary)


@app.route("/api/optimization/run", methods=["POST"])
def api_run_optimization():
    """Manuel optimizasyon tetikle — scan_lock BEKLEMEZ, ayrı thread'de çalışır."""
    # Optimizer kendi başına scan_lock gerektirmez — sadece DB okuyan ve param yazan bir işlem.
    # Tarama sırasında da güvenle çalışabilir çünkü:
    #   - DB okuma: get_completed_signals, get_performance_summary → thread-safe SQLite
    #   - Param yazma: save_bot_param → tek satır UPDATE, atomik
    #   - reload_params: Sonraki taramada yeni params kullanılır
    try:
        result = self_optimizer.run_optimization()
        bot_state["last_optimization"] = datetime.now().isoformat()
        if result["changes"]:
            ict_strategy.reload_params()
            socketio.emit("optimization_done", result)
            logger.info(f"🧠 Manuel Optimizasyon: {len(result['changes'])} değişiklik")
        else:
            logger.info(f"🧠 Manuel Optimizasyon: Değişiklik gerekli değil — {result.get('reason', '')}")
        return jsonify(result)
    except Exception as e:
        logger.error(f"Manuel optimizasyon hatası: {e}")
        return jsonify({"status": "ERROR", "reason": str(e), "changes": []}), 500


@app.route("/api/signal/<int:signal_id>/cancel", methods=["POST"])
def api_cancel_signal(signal_id):
    """Sinyali iptal et"""
    update_signal_status(signal_id, "CANCELLED")
    return jsonify({"status": "cancelled", "signal_id": signal_id})


@app.route("/api/analyze/<symbol>")
def api_analyze_symbol(symbol):
    """Tek bir coini analiz et"""
    try:
        multi_tf = data_fetcher.get_multi_timeframe_data(symbol)
        ltf_data = multi_tf.get("15m")

        if ltf_data is None or ltf_data.empty:
            return jsonify({"error": "Veri alınamadı"}), 400

        analysis = ict_strategy.calculate_confluence(ltf_data, multi_tf)

        # Timestamp'leri string'e çevir
        def serialize(obj):
            if hasattr(obj, "isoformat"):
                return obj.isoformat()
            elif hasattr(obj, "item"):
                return obj.item()
            return str(obj)

        return jsonify(json.loads(json.dumps(analysis, default=serialize)))

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chart-data/<symbol>")
def api_chart_data(symbol):
    """
    ICT Chart verisi: 15m mumları + tüm ICT çizim katmanları.
    Aktif sinyallerdeki coin'e çift tıklandığında chart açılır.
    """
    try:
        multi_tf = data_fetcher.get_multi_timeframe_data(symbol)
        ltf_data = multi_tf.get("15m")

        if ltf_data is None or ltf_data.empty:
            return jsonify({"error": "Veri alınamadı"}), 400

        # Mum verileri (Lightweight Charts formatı)
        candles = []
        for _, row in ltf_data.iterrows():
            candles.append({
                "time": int(row["timestamp"].timestamp()),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]) if "volume" in row else 0
            })

        # ICT bileşenleri hesapla
        structure = ict_strategy.detect_market_structure(ltf_data)
        active_obs, all_obs = ict_strategy.find_order_blocks(ltf_data, structure)
        breaker_blocks = ict_strategy.find_breaker_blocks(all_obs, ltf_data)
        fvgs = ict_strategy.find_fvg(ltf_data)
        displacements = ict_strategy.detect_displacement(ltf_data, lookback=30)
        liquidity_levels = ict_strategy.find_liquidity_levels(ltf_data)
        pd_zone = ict_strategy.calculate_premium_discount(ltf_data, structure)

        # EMA hesapla (21 ve 50 periyot)
        import numpy as np
        ema21 = ltf_data["close"].ewm(span=21, adjust=False).mean()
        ema50 = ltf_data["close"].ewm(span=50, adjust=False).mean()
        ema_21_data = []
        ema_50_data = []
        for idx_i, row in ltf_data.iterrows():
            t = int(row["timestamp"].timestamp())
            v21 = ema21.loc[idx_i]
            v50 = ema50.loc[idx_i]
            if not (isinstance(v21, float) and np.isnan(v21)):
                ema_21_data.append({"time": t, "value": round(float(v21), 8)})
            if not (isinstance(v50, float) and np.isnan(v50)):
                ema_50_data.append({"time": t, "value": round(float(v50), 8)})

        # HTF bias
        htf_result = ict_strategy._analyze_htf_bias(multi_tf)
        htf_bias = htf_result["bias"] if htf_result else None

        # Sweep event
        sweep = None
        if htf_bias:
            sweep = ict_strategy._find_sweep_event(ltf_data, htf_bias)

        # Aktif sinyal bilgisi (entry/sl/tp çizgileri için)
        active_signal = None
        active_signals = get_active_signals()
        for s in active_signals:
            if s["symbol"] == symbol:
                active_signal = {
                    "direction": s["direction"],
                    "entry": float(s["entry_price"]),
                    "sl": float(s["stop_loss"]),
                    "tp": float(s["take_profit"]),
                    "status": s["status"],
                    "confidence": s.get("confidence", 0)
                }
                break

        # Swing points
        swing_highs_data = []
        for sh in structure.get("swing_highs", []):
            if sh["index"] < len(ltf_data):
                swing_highs_data.append({
                    "time": int(ltf_data.iloc[sh["index"]]["timestamp"].timestamp()),
                    "price": float(sh["price"]),
                    "type": sh.get("fractal_type", "MAJOR")
                })

        swing_lows_data = []
        for sl_p in structure.get("swing_lows", []):
            if sl_p["index"] < len(ltf_data):
                swing_lows_data.append({
                    "time": int(ltf_data.iloc[sl_p["index"]]["timestamp"].timestamp()),
                    "price": float(sl_p["price"]),
                    "type": sl_p.get("fractal_type", "MAJOR")
                })

        # Order Blocks → dikdörtgen bölgeler
        obs_data = []
        for ob in active_obs:
            if ob["index"] < len(ltf_data):
                obs_data.append({
                    "time": int(ltf_data.iloc[ob["index"]]["timestamp"].timestamp()),
                    "high": float(ob["high"]),
                    "low": float(ob["low"]),
                    "type": ob["type"],
                    "strength": round(ob.get("strength", 0), 2)
                })

        # FVGs → dikdörtgen bölgeler
        fvgs_data = []
        for fvg in fvgs:
            if fvg["index"] < len(ltf_data):
                fvgs_data.append({
                    "time": int(ltf_data.iloc[fvg["index"]]["timestamp"].timestamp()),
                    "high": float(fvg["high"]),
                    "low": float(fvg["low"]),
                    "type": fvg["type"],
                    "size_pct": fvg.get("size_pct", 0)
                })

        # Displacement mumları
        disp_data = []
        for d in displacements:
            if d["index"] < len(ltf_data):
                disp_data.append({
                    "time": int(ltf_data.iloc[d["index"]]["timestamp"].timestamp()),
                    "direction": d["direction"],
                    "body_ratio": d.get("body_ratio", 0),
                    "atr_multiple": d.get("atr_multiple", 0)
                })

        # BOS/CHoCH yapısal kırılımlar
        bos_data = []
        for bos in structure.get("bos_events", []):
            if bos["index"] < len(ltf_data):
                bos_data.append({
                    "time": int(ltf_data.iloc[bos["index"]]["timestamp"].timestamp()),
                    "type": bos["type"],
                    "price": float(bos["price"]),
                    "prev_price": float(bos["prev_price"])
                })

        choch_data = []
        for ch in structure.get("choch_events", []):
            if ch["index"] < len(ltf_data):
                choch_data.append({
                    "time": int(ltf_data.iloc[ch["index"]]["timestamp"].timestamp()),
                    "type": ch["type"],
                    "price": float(ch["price"]),
                    "prev_price": float(ch["prev_price"])
                })

        # Sweep event
        sweep_data = None
        if sweep:
            sidx = sweep["sweep_candle_idx"]
            if sidx < len(ltf_data):
                sweep_data = {
                    "time": int(ltf_data.iloc[sidx]["timestamp"].timestamp()),
                    "swept_level": float(sweep["swept_level"]),
                    "sweep_wick": float(sweep.get("sweep_wick", sweep["swept_level"])),
                    "type": sweep["sweep_type"],
                    "quality": sweep.get("sweep_quality", 1.0)
                }

        # Premium/Discount bölgeleri
        pd_data = None
        if pd_zone:
            pd_data = {
                "equilibrium": float(pd_zone["equilibrium"]),
                "high": float(pd_zone["high"]),
                "low": float(pd_zone["low"]),
                "zone": pd_zone["zone"],
                "in_ote": pd_zone.get("in_ote", False),
                "ote_high": float(pd_zone.get("ote_high", 0)),
                "ote_low": float(pd_zone.get("ote_low", 0))
            }

        # Liquidity levels
        liq_data = []
        for liq in liquidity_levels:
            liq_data.append({
                "price": float(liq["price"]),
                "type": liq["type"],
                "touches": liq.get("touches", 2),
                "swept": liq.get("swept", False)
            })

        # Breaker blocks
        breaker_data = []
        for bb in breaker_blocks:
            if bb["index"] < len(ltf_data):
                breaker_data.append({
                    "time": int(ltf_data.iloc[bb["index"]]["timestamp"].timestamp()),
                    "high": float(bb["high"]),
                    "low": float(bb["low"]),
                    "type": bb["type"]
                })

        result = {
            "symbol": symbol,
            "candles": candles,
            "htf_bias": htf_bias,
            "ltf_trend": structure.get("trend", "NEUTRAL"),
            "swing_highs": swing_highs_data,
            "swing_lows": swing_lows_data,
            "order_blocks": obs_data,
            "fvgs": fvgs_data,
            "displacements": disp_data,
            "bos_events": bos_data,
            "choch_events": choch_data,
            "sweep": sweep_data,
            "premium_discount": pd_data,
            "liquidity_levels": liq_data,
            "breaker_blocks": breaker_data,
            "active_signal": active_signal,
            "ema_21": ema_21_data,
            "ema_50": ema_50_data,
            "current_price": float(ltf_data.iloc[-1]["close"]) if len(ltf_data) > 0 else None,
            "market_structure_trend": structure.get("trend", "NEUTRAL"),
            "structure_shift_count": len(structure.get("choch_events", [])),
            "bos_count": len(structure.get("bos_events", []))
        }

        # numpy tiplerini Python native'e çevir
        def _serialize(obj):
            if hasattr(obj, "item"):         # numpy scalar (int64, float64, bool_)
                return obj.item()
            if hasattr(obj, "isoformat"):     # datetime/Timestamp
                return obj.isoformat()
            return str(obj)

        return app.response_class(
            response=json.dumps(result, default=_serialize),
            status=200,
            mimetype="application/json"
        )

    except Exception as e:
        logger.error(f"Chart data hatası ({symbol}): {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/coin-detail/<symbol>")
def api_coin_detail(symbol):
    """
    Gelişmiş coin detay popup: çoklu TF teknik analiz.
    RSI, Stochastic RSI, MACD, Bollinger Bands, ADX, ATR,
    OBV, Volume, FVG, Support/Resistance, Diverjans, Order Book,
    trend yapısı ve ağırlıklı güven skoru ile genel yorum.
    """
    import numpy as np

    # ── TEMEL HESAPLAMA FONKSİYONLARI ──

    def _rsi(series, period=14):
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
        avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    def _macd(series, fast=12, slow=26, signal=9):
        ema_fast = series.ewm(span=fast, adjust=False).mean()
        ema_slow = series.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    def _stoch_rsi(series, rsi_period=14, stoch_period=14, k_period=3, d_period=3):
        """Stochastic RSI — RSI'nin RSI'si, daha hassas aşırı alım/satım"""
        rsi = _rsi(series, rsi_period)
        rsi_min = rsi.rolling(window=stoch_period).min()
        rsi_max = rsi.rolling(window=stoch_period).max()
        stoch = ((rsi - rsi_min) / (rsi_max - rsi_min).replace(0, np.nan)) * 100
        k = stoch.rolling(window=k_period).mean()
        d = k.rolling(window=d_period).mean()
        return k, d

    def _bollinger_bands(series, period=20, std_dev=2):
        """Bollinger Bands — volatilite ve fiyat pozisyonu"""
        sma = series.rolling(window=period).mean()
        std = series.rolling(window=period).std()
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)
        return upper, sma, lower

    def _adx(df, period=14):
        """ADX — Trend gücü ölçümü (0-100)"""
        high = df["high"]
        low = df["low"]
        close = df["close"]

        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = tr.ewm(alpha=1/period, min_periods=period).mean()
        plus_di = 100 * (plus_dm.ewm(alpha=1/period, min_periods=period).mean() / atr.replace(0, np.nan))
        minus_di = 100 * (minus_dm.ewm(alpha=1/period, min_periods=period).mean() / atr.replace(0, np.nan))

        dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)) * 100
        adx = dx.ewm(alpha=1/period, min_periods=period).mean()
        return adx, plus_di, minus_di

    def _atr(df, period=14):
        """ATR — Volatilite ölçümü"""
        high = df["high"]
        low = df["low"]
        close = df["close"]
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.ewm(alpha=1/period, min_periods=period).mean()

    def _obv(df):
        """OBV — On Balance Volume"""
        obv = [0]
        for i in range(1, len(df)):
            if df["close"].iloc[i] > df["close"].iloc[i-1]:
                obv.append(obv[-1] + df["volume"].iloc[i])
            elif df["close"].iloc[i] < df["close"].iloc[i-1]:
                obv.append(obv[-1] - df["volume"].iloc[i])
            else:
                obv.append(obv[-1])
        return pd.Series(obv, index=df.index)

    # ── YENİ ANA STRATEJI GÖSTERGELERİ ──

    def _donchian(df, period=20):
        """Donchian Channel — saf kırılım göstergesi"""
        high = df["high"]
        low = df["low"]
        upper = high.rolling(window=period).max()
        lower = low.rolling(window=period).min()
        middle = (upper + lower) / 2
        width = ((upper - lower) / middle).replace(0, np.nan) * 100  # kanal genişliği %
        return upper, middle, lower, width

    def _vwap_rolling(df, period=50):
        """Rolling VWAP — hacim ağırlıklı ortalama fiyat + standart sapma bantları"""
        typical_price = (df["high"] + df["low"] + df["close"]) / 3
        tp_vol = typical_price * df["volume"]
        cum_vol = df["volume"].rolling(window=period).sum()
        cum_tp_vol = tp_vol.rolling(window=period).sum()
        vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
        # VWAP standart sapma — uzama ölçümü
        vwap_sq = ((typical_price - vwap) ** 2 * df["volume"]).rolling(window=period).sum()
        vwap_std = (vwap_sq / cum_vol.replace(0, np.nan)).apply(lambda x: x ** 0.5 if x > 0 else 0)
        return vwap, vwap_std

    def _dpo(close, period=20):
        """Detrended Price Oscillator — döngüsel pozisyon, trendi çıkarır"""
        shift = period // 2 + 1
        sma = close.rolling(window=period).mean()
        dpo_val = close - sma.shift(shift)
        return dpo_val

    def _mfi(df, period=14):
        """Money Flow Index — hacim ağırlıklı RSI"""
        typical_price = (df["high"] + df["low"] + df["close"]) / 3
        money_flow = typical_price * df["volume"]
        tp_diff = typical_price.diff()
        pos_flow = money_flow.where(tp_diff > 0, 0.0)
        neg_flow = money_flow.where(tp_diff < 0, 0.0)
        pos_sum = pos_flow.rolling(window=period).sum()
        neg_sum = neg_flow.rolling(window=period).sum()
        mfr = pos_sum / neg_sum.replace(0, np.nan)
        mfi_val = 100 - (100 / (1 + mfr))
        return mfi_val

    def _cmf(df, period=20):
        """Chaikin Money Flow — kapanış pozisyonuna göre para akışı"""
        high = df["high"]
        low = df["low"]
        close = df["close"]
        vol = df["volume"]
        hl_range = (high - low).replace(0, np.nan)
        clv = ((close - low) - (high - close)) / hl_range  # Close Location Value [-1, +1]
        mf_vol = clv * vol
        cmf_val = mf_vol.rolling(window=period).sum() / vol.rolling(window=period).sum().replace(0, np.nan)
        return cmf_val

    def _find_support_resistance(df, lookback=50):
        """Pivot tabanlı destek/direnç seviyeleri"""
        if len(df) < lookback:
            lookback = len(df)
        recent = df.iloc[-lookback:]
        supports = []
        resistances = []
        for i in range(2, len(recent) - 2):
            # Pivot Low (destek)
            if (recent["low"].iloc[i] < recent["low"].iloc[i-1] and
                recent["low"].iloc[i] < recent["low"].iloc[i-2] and
                recent["low"].iloc[i] < recent["low"].iloc[i+1] and
                recent["low"].iloc[i] < recent["low"].iloc[i+2]):
                supports.append(recent["low"].iloc[i])
            # Pivot High (direnç)
            if (recent["high"].iloc[i] > recent["high"].iloc[i-1] and
                recent["high"].iloc[i] > recent["high"].iloc[i-2] and
                recent["high"].iloc[i] > recent["high"].iloc[i+1] and
                recent["high"].iloc[i] > recent["high"].iloc[i+2]):
                resistances.append(recent["high"].iloc[i])
        return supports, resistances

    def _detect_divergence(price_series, indicator_series, lookback=20):
        """RSI/MACD diverjans tespiti"""
        if len(price_series) < lookback or len(indicator_series) < lookback:
            return None

        price = price_series.iloc[-lookback:]
        ind = indicator_series.iloc[-lookback:]

        # Son 2 swing low/high bul
        price_lows = []
        price_highs = []
        for i in range(2, len(price) - 2):
            if price.iloc[i] < price.iloc[i-1] and price.iloc[i] < price.iloc[i+1]:
                price_lows.append((i, price.iloc[i], ind.iloc[i]))
            if price.iloc[i] > price.iloc[i-1] and price.iloc[i] > price.iloc[i+1]:
                price_highs.append((i, price.iloc[i], ind.iloc[i]))

        # Bullish divergence: Fiyat düşük dip, RSI yüksek dip
        if len(price_lows) >= 2:
            last = price_lows[-1]
            prev = price_lows[-2]
            if last[1] < prev[1] and last[2] > prev[2]:
                return {
                    "type": "BULLISH",
                    "label": "Boğa Diverjansı",
                    "desc": "Fiyat düşük dip yaparken gösterge yüksek dip yapıyor — gizli alım gücü, dönüş sinyali.",
                    "color": "green"
                }

        # Bearish divergence: Fiyat yüksek tepe, RSI düşük tepe
        if len(price_highs) >= 2:
            last = price_highs[-1]
            prev = price_highs[-2]
            if last[1] > prev[1] and last[2] < prev[2]:
                return {
                    "type": "BEARISH",
                    "label": "Ayı Diverjansı",
                    "desc": "Fiyat yüksek tepe yaparken gösterge düşük tepe yapıyor — gizli satış baskısı, dönüş sinyali.",
                    "color": "red"
                }

        return {"type": "NONE", "label": "Diverjans yok", "desc": "Fiyat ve göstergeler uyumlu hareket ediyor.", "color": "gray"}

    # ── YORUM FONKSİYONLARI ──

    def _interpret_rsi(val):
        if val is None or np.isnan(val):
            return {"value": None, "label": "Veri yok", "signal": "NEUTRAL", "color": "gray"}
        val = round(val, 2)
        if val >= 80:
            return {"value": val, "label": "Aşırı Alım (Güçlü)", "signal": "BEARISH", "color": "red",
                    "desc": f"RSI {val} — Çok güçlü aşırı alım. Fiyat sürdürülemez seviyede gerilmiş. Hacim düşüşü ve mum formasyonuyla birlikte geri çekilme olasılığı çok yüksek. Yeni LONG açmayın."}
        elif val >= 70:
            return {"value": val, "label": "Aşırı Alım", "signal": "BEARISH", "color": "red",
                    "desc": f"RSI {val} — Aşırı alım bölgesi. Güçlü trendlerde RSI 70+ kalabilir ancak momentum zayıflarsa düzeltme kaçınılmaz. Kısa vade dikkat."}
        elif val >= 60:
            return {"value": val, "label": "Boğa Momentumu", "signal": "BULLISH", "color": "green",
                    "desc": f"RSI {val} — Alıcılar güçlü pozisyonda. Fiyat-hacim uyumuna bakarak trendin sağlığını doğrulayın."}
        elif val >= 45:
            return {"value": val, "label": "Nötr Bölge", "signal": "NEUTRAL", "color": "gray",
                    "desc": f"RSI {val} — Denge bölgesi, piyasa kararsız. Tek başına RSI yön vermez, diğer göstergelerle birlikte değerlendirin."}
        elif val >= 30:
            return {"value": val, "label": "Ayı Momentumu", "signal": "BEARISH", "color": "orange",
                    "desc": f"RSI {val} — Satıcılar baskın. Düşüş trendi aktif, karşı yönde işlem riskli. Destek seviyelerini izleyin."}
        elif val >= 20:
            return {"value": val, "label": "Aşırı Satım", "signal": "BULLISH", "color": "green",
                    "desc": f"RSI {val} — Aşırı satım bölgesi. Destek seviyesiyle birleşirse alım fırsatı olabilir. Tek başına yeterli değil, hacim onayı şart."}
        else:
            return {"value": val, "label": "Aşırı Satım (Güçlü)", "signal": "BULLISH", "color": "green",
                    "desc": f"RSI {val} — Çok güçlü aşırı satım. Teknik tepki beklenir ancak düşüş devam edebilir. Hacim ve diverjans onayı olmadan körlemesine LONG açmayın."}

    def _interpret_stoch_rsi(k_val, d_val):
        if k_val is None or np.isnan(k_val):
            return {"k": None, "d": None, "label": "Veri yok", "signal": "NEUTRAL", "color": "gray"}
        k_r = round(k_val, 2)
        d_r = round(d_val, 2) if d_val is not None and not np.isnan(d_val) else None
        result = {"k": k_r, "d": d_r}

        if k_r >= 80 and d_r and k_r < d_r:
            result.update({"label": "Aşırı Alım + Çapraz Aşağı", "signal": "BEARISH", "color": "red",
                          "desc": f"StochRSI K:{k_r} D:{d_r} — K çizgisi D'yi 80 üzerinde aşağı kesti. Kısa vadeli dönüş olasılığı yüksek. RSI ve hacimle birlikte değerlendirin."})
        elif k_r >= 80:
            result.update({"label": "Aşırı Alım Bölgesi", "signal": "NEUTRAL", "color": "orange",
                          "desc": f"StochRSI K:{k_r} — Aşırı alım bölgesinde. Tek başına satış sinyali değil, çapraz aşağı kesimi bekleyin."})
        elif k_r <= 20 and d_r and k_r > d_r:
            result.update({"label": "Aşırı Satım + Çapraz Yukarı", "signal": "BULLISH", "color": "green",
                          "desc": f"StochRSI K:{k_r} D:{d_r} — K çizgisi D'yi 20 altında yukarı kesti. Teknik olarak güçlü alım sinyali, ancak trend yönüne karşıysa dikkat."})
        elif k_r <= 20:
            result.update({"label": "Aşırı Satım Bölgesi", "signal": "NEUTRAL", "color": "green",
                          "desc": f"StochRSI K:{k_r} — Aşırı satım. Çapraz yukarı bekleniyor. Hareket başlayana kadar sinyal net değil."})
        elif k_r > 55 and d_r and k_r > d_r:
            result.update({"label": "Boğa Momentumu", "signal": "BULLISH", "color": "green",
                          "desc": f"StochRSI K:{k_r} > D:{d_r} — Kısa vadeli momentum alıcı lehine, devam sinyali."})
        elif k_r < 45 and d_r and k_r < d_r:
            result.update({"label": "Ayı Momentumu", "signal": "BEARISH", "color": "orange",
                          "desc": f"StochRSI K:{k_r} < D:{d_r} — Kısa vadeli momentum satıcı lehine."})
        else:
            result.update({"label": "Nötr", "signal": "NEUTRAL", "color": "gray",
                          "desc": f"StochRSI K:{k_r} — Kararsız bölge. Net sinyal için aşırı bölgelerden çapraz bekleyin."})
        return result

    def _interpret_macd(macd_val, signal_val, hist_val, prev_hist=None):
        if macd_val is None or np.isnan(macd_val):
            return {"macd": None, "signal": None, "histogram": None,
                    "label": "Veri yok", "signal_type": "NEUTRAL", "color": "gray"}
        result = {
            "macd": round(macd_val, 6), "signal": round(signal_val, 6),
            "histogram": round(hist_val, 6)
        }
        if hist_val > 0 and (prev_hist is not None and prev_hist <= 0):
            result.update({"label": "Boğa Kesişimi ↑", "signal_type": "BULLISH", "color": "green",
                          "desc": "MACD histogram pozitife döndü — taze alım sinyali. En güvenilir MACD sinyallerinden biri. Hacim artışıyla desteklenirse güçlü."})
        elif hist_val < 0 and (prev_hist is not None and prev_hist >= 0):
            result.update({"label": "Ayı Kesişimi ↓", "signal_type": "BEARISH", "color": "red",
                          "desc": "MACD histogram negatife döndü — taze satım sinyali. Trend dönüşü veya düzeltme başlangıcı."})
        elif hist_val > 0:
            if prev_hist is not None and hist_val > prev_hist:
                result.update({"label": "Boğa Güçleniyor ↗", "signal_type": "BULLISH", "color": "green",
                              "desc": "MACD histogram pozitif ve büyüyor — momentum sağlıklı artıyor. Mevcut LONG pozisyon korunabilir."})
            else:
                result.update({"label": "Boğa Zayıflıyor ↘", "signal_type": "WEAKENING_BULL", "color": "orange",
                              "desc": "MACD histogram pozitif ama daralmaya başladı — yükseliş hız kesiyor. Yeni giriş için erken, çıkış planı hazırlayın."})
        elif hist_val < 0:
            if prev_hist is not None and hist_val < prev_hist:
                result.update({"label": "Ayı Güçleniyor ↘", "signal_type": "BEARISH", "color": "red",
                              "desc": "MACD histogram negatif ve büyüyor — düşüş ivmeleniyor. Kısa vadeli destek kırılabilir."})
            else:
                result.update({"label": "Ayı Zayıflıyor ↗", "signal_type": "WEAKENING_BEAR", "color": "orange",
                              "desc": "MACD histogram daralmaya başladı — satış baskısı azalıyor. Sabırlı ol, pozitife geçiş onayı bekle."})
        else:
            result.update({"label": "Nötr ─", "signal_type": "NEUTRAL", "color": "gray",
                          "desc": "MACD sıfır çizgisinde — yön kararı yaklaşıyor. İlk hareketi bekleyin."})
        return result

    def _interpret_bb(close_val, upper, middle, lower, bb_width, prev_width=None):
        """Bollinger Bands yorumu"""
        if close_val is None or np.isnan(close_val):
            return {"label": "Veri yok", "signal": "NEUTRAL", "color": "gray"}
        pct_b = ((close_val - lower) / (upper - lower)) * 100 if (upper - lower) > 0 else 50
        result = {
            "upper": round(upper, 8), "middle": round(middle, 8), "lower": round(lower, 8),
            "width": round(bb_width, 4), "pct_b": round(pct_b, 1)
        }

        # Squeeze tespiti
        is_squeeze = bb_width < 0.02  # Sıkışma (coin'e göre normalize edilmiş)
        if prev_width and bb_width > prev_width * 1.5:
            result["squeeze_status"] = "PATLAMA"
            result["squeeze_desc"] = f"Bantlar genişliyor (genişlik: {bb_width:.4f} → önceki: {prev_width:.4f}). Sıkışma patlaması sinyali — güçlü yönlü hareket başladı. Kırılım yönünde pozisyon almak için diğer göstergelerle onaylayın."
        elif is_squeeze:
            result["squeeze_status"] = "SIKILIK"
            result["squeeze_desc"] = f"Bant genişliği çok dar ({bb_width:.4f}). Patlama öncesi sıkışma — büyük bir hareket kapıda. Kırılım yönünü tahmin etmeyin, kırılım sonrası girin."
        else:
            result["squeeze_status"] = "NORMAL"
            result["squeeze_desc"] = f"Bantlar normal genişlikte ({bb_width:.4f}). Olağan volatilite — trend takip stratejileri uygulanabilir."

        if pct_b >= 95:
            result.update({"label": "Üst Bant Üzerinde", "signal": "BEARISH", "color": "red",
                          "desc": f"%B: {pct_b:.0f} — Fiyat üst bandın üzerine taştı. Aşırı alım bölgesi, geri çekilme olasılığı yüksek. Ancak güçlü trendlerde bant üzerinde yürüyüş ('walking the band') olabilir — ADX'e bakın."})
        elif pct_b >= 80:
            result.update({"label": "Üst Banda Yakın", "signal": "NEUTRAL", "color": "orange",
                          "desc": f"%B: {pct_b:.0f} — Fiyat üst bant bölgesinde. Trend güçlüyse (ADX>25) devam edebilir, trend zayıfsa geri çekilme riski var. Tek başına sinyal olarak yeterli değil."})
        elif pct_b <= 5:
            result.update({"label": "Alt Bant Altında", "signal": "BULLISH", "color": "green",
                          "desc": f"%B: {pct_b:.0f} — Fiyat alt bandın altına düştü. Aşırı satım bölgesi — tepki yükselişi gelebilir. Ama düşüş trendi güçlüyse bant altında yürüyüş de olabilir."})
        elif pct_b <= 20:
            result.update({"label": "Alt Banda Yakın", "signal": "NEUTRAL", "color": "green",
                          "desc": f"%B: {pct_b:.0f} — Fiyat alt bant bölgesinde. Potansiyel destek alanı ama körlemesine alım yapmayın — RSI ve hacim onayı gerekli."})
        else:
            result.update({"label": "Orta Bölge", "signal": "NEUTRAL", "color": "gray",
                          "desc": f"%B: {pct_b:.0f} — Fiyat bantların ortasında, BB'den anlamlı sinyal yok. Diğer göstergelere bakın."})
        return result

    def _interpret_adx(adx_val, plus_di, minus_di):
        """ADX yorumu — trend gücü ve yönü"""
        if adx_val is None or np.isnan(adx_val):
            return {"adx": None, "label": "Veri yok", "signal": "NEUTRAL", "color": "gray"}
        adx_r = round(adx_val, 1)
        pdi_r = round(plus_di, 1)
        mdi_r = round(minus_di, 1)
        result = {"adx": adx_r, "plus_di": pdi_r, "minus_di": mdi_r}

        # DI yönü — fark büyüklüğü de önemli
        di_diff = abs(pdi_r - mdi_r)
        if pdi_r > mdi_r:
            di_dir = "BULLISH"
            di_label = f"+DI ({pdi_r}) > -DI ({mdi_r}), fark: {di_diff:.1f}"
        else:
            di_dir = "BEARISH"
            di_label = f"-DI ({mdi_r}) > +DI ({pdi_r}), fark: {di_diff:.1f}"
        result["di_direction"] = di_dir
        result["di_label"] = di_label

        # DI farkı çok düşükse (< 5) yön güvenilir değil
        di_weak = di_diff < 5

        if adx_r >= 50:
            result.update({"label": f"Çok Güçlü Trend ({adx_r})", "signal": di_dir, "color": "green" if di_dir == "BULLISH" else "red",
                          "desc": f"ADX {adx_r} — Çok güçlü trend aktif. {di_label}. Trende KARŞI pozisyon almak çok riskli. Trend yönünde geri çekilmeleri fırsat olarak değerlendirin."})
        elif adx_r >= 25:
            if di_weak:
                result.update({"label": f"Trend Var, Yön Belirsiz ({adx_r})", "signal": "NEUTRAL", "color": "orange",
                              "desc": f"ADX {adx_r} — Trend gücü yeterli AMA +DI ve -DI çok yakın (fark: {di_diff:.1f}). Yön netleşene kadar bekleyin."})
            else:
                result.update({"label": f"Güçlü Trend ({adx_r})", "signal": di_dir, "color": "green" if di_dir == "BULLISH" else "red",
                              "desc": f"ADX {adx_r} — Belirgin trend var. {di_label}. Trend yönünde pozisyon alınabilir, trende karşı gidenler zarar eder."})
        elif adx_r >= 20:
            result.update({"label": f"Gelişen Trend ({adx_r})", "signal": "NEUTRAL", "color": "orange",
                          "desc": f"ADX {adx_r} — Trend oluşmaya başlıyor ama henüz olgunlaşmadı. {di_label}. DI kesişimi ve ADX>25 onayını bekleyin."})
        else:
            result.update({"label": f"Trendsiz/Yatay ({adx_r})", "signal": "NEUTRAL", "color": "gray",
                          "desc": f"ADX {adx_r} — Piyasada güçlü trend yok, yatay hareket. Trend takip stratejileri çalışmaz — range (destek-direnç arası) alım-satım stratejisi uygulayın."})
        return result

    def _interpret_atr(atr_val, close_val):
        """ATR yorumu — volatilite yüzdesi ve pozisyon boyutlandırma"""
        if atr_val is None or np.isnan(atr_val) or close_val == 0:
            return {"atr": None, "label": "Veri yok", "signal": "NEUTRAL", "color": "gray"}
        atr_pct = (atr_val / close_val) * 100
        suggested_sl = round(atr_val * 1.5, 8)  # 1.5x ATR stop-loss önerisi
        result = {"atr": round(atr_val, 8), "atr_pct": round(atr_pct, 2), "suggested_sl_distance": suggested_sl}
        if atr_pct >= 5:
            result.update({"label": f"Çok Yüksek Volatilite (%{atr_pct:.1f})", "signal": "HIGH", "color": "red",
                          "desc": f"ATR fiyatın %{atr_pct:.1f}'i — Çok yüksek volatilite! Pozisyon boyutunu normalin %50'sine düşürün. SL en az 1.5x ATR ({suggested_sl}) uzaklıkta olmalı. Likidasyon riski yüksek."})
        elif atr_pct >= 3:
            result.update({"label": f"Yüksek Volatilite (%{atr_pct:.1f})", "signal": "HIGH", "color": "orange",
                          "desc": f"ATR fiyatın %{atr_pct:.1f}'i — Yüksek volatilite, normal SL tetiklenebilir. Geniş SL kullanın (önerilen: {suggested_sl}). Kaldıraç düşük tutun."})
        elif atr_pct >= 1:
            result.update({"label": f"Normal Volatilite (%{atr_pct:.1f})", "signal": "NORMAL", "color": "gray",
                          "desc": f"ATR fiyatın %{atr_pct:.1f}'i — Normal piyasa koşulları. Standart pozisyon boyutu ve SL ({suggested_sl}) uygulanabilir."})
        else:
            result.update({"label": f"Düşük Volatilite (%{atr_pct:.1f})", "signal": "LOW", "color": "blue",
                          "desc": f"ATR fiyatın %{atr_pct:.1f}'i — Düşük volatilite, sıkışma patlaması yaklaşıyor olabilir. Dar SL ile kırılım pozisyonu planlanabilir."})
        return result

    def _interpret_obv(obv_series, price_series):
        """OBV yorumu — akıllı para akışı ve fiyat-hacim uyumsuzluğu"""
        if len(obv_series) < 10:
            return {"label": "Veri yok", "signal": "NEUTRAL", "color": "gray"}
        obv_sma = obv_series.rolling(10).mean()
        obv_now = obv_series.iloc[-1]
        obv_sma_now = obv_sma.iloc[-1]
        obv_5_ago = obv_series.iloc[-5] if len(obv_series) >= 5 else obv_now
        obv_10_ago = obv_series.iloc[-10] if len(obv_series) >= 10 else obv_now
        price_5_ago = price_series.iloc[-5] if len(price_series) >= 5 else price_series.iloc[-1]
        price_now = price_series.iloc[-1]

        obv_trend = "UP" if obv_now > obv_5_ago else "DOWN"
        obv_long_trend = "UP" if obv_now > obv_10_ago else "DOWN"
        price_trend = "UP" if price_now > price_5_ago else "DOWN"
        price_change_pct = round(((price_now - price_5_ago) / price_5_ago) * 100, 2) if price_5_ago > 0 else 0

        result = {
            "obv_trend": obv_trend,
            "obv_long_trend": obv_long_trend,
            "above_sma": bool(obv_now > obv_sma_now),
        }

        # Diverjans kontrolü — en güvenilir OBV sinyali
        if price_trend == "UP" and obv_trend == "DOWN":
            result.update({"label": "Ayı Diverjansı (OBV)", "signal": "BEARISH", "color": "red",
                          "desc": f"Fiyat %{price_change_pct:+.1f} yükselirken OBV düşüyor. Akıllı para yükselişe katılmıyor, ralli'ye satıyor. Bu bir gizli satış sinyali — yükseliş sürdürülebilir değil."})
        elif price_trend == "DOWN" and obv_trend == "UP":
            result.update({"label": "Boğa Diverjansı (OBV)", "signal": "BULLISH", "color": "green",
                          "desc": f"Fiyat %{price_change_pct:+.1f} düşerken OBV yükseliyor. Akıllı para düşüşte sessizce biriktiriyor. Bu bir gizli alım sinyali — dönüş yakın olabilir."})
        elif obv_now > obv_sma_now and obv_trend == "UP":
            result.update({"label": "Güçlü Alım Akışı", "signal": "BULLISH", "color": "green",
                          "desc": f"OBV ortalamanın üzerinde ve yükseliyor. Hacim fiyatı destekliyor — sağlıklı bir yükseliş. Para akışı boğa yönünde."})
        elif obv_now < obv_sma_now and obv_trend == "DOWN":
            result.update({"label": "Güçlü Satış Akışı", "signal": "BEARISH", "color": "red",
                          "desc": f"OBV ortalamanın altında ve düşüyor. Sürekli satış baskısı var — para bu coin'den çıkıyor. Long pozisyonlardan kaçının."})
        elif obv_now > obv_sma_now and obv_trend == "DOWN":
            result.update({"label": "Zayıflayan Alım", "signal": "NEUTRAL", "color": "orange",
                          "desc": f"OBV hala ortalamanın üzerinde ama düşüyor. Alım baskısı zayıflıyor — yükselişin sonu yaklaşıyor olabilir."})
        elif obv_now < obv_sma_now and obv_trend == "UP":
            result.update({"label": "Toparlanma Sinyali", "signal": "NEUTRAL", "color": "orange",
                          "desc": f"OBV ortalamanın altında ama yükseliyor. Satış baskısı azalıyor — henüz alım sinyali değil ama izlemeye değer."})
        else:
            result.update({"label": "Nötr Hacim Akışı", "signal": "NEUTRAL", "color": "gray",
                          "desc": "OBV dengede — belirgin bir para akışı yok. Büyük oyuncularda henüz net bir pozisyonlanma görülmüyor."})
        return result

    def _analyze_volume(df):
        if df.empty or "volume" not in df.columns or len(df) < 20:
            return {"label": "Veri yok", "signal": "NEUTRAL", "color": "gray"}
        vol = df["volume"]
        current_vol = vol.iloc[-1]
        avg_vol_20 = vol.iloc[-20:].mean()
        avg_vol_5 = vol.iloc[-5:].mean()
        ratio = round(current_vol / avg_vol_20, 2) if avg_vol_20 > 0 else 1.0
        trend_ratio = round(avg_vol_5 / avg_vol_20, 2) if avg_vol_20 > 0 else 1.0
        price_change = ((df["close"].iloc[-1] - df["close"].iloc[-5]) / df["close"].iloc[-5] * 100) if len(df) >= 5 else 0

        result = {"current": round(current_vol, 2), "avg_20": round(avg_vol_20, 2),
                  "ratio": ratio, "trend_ratio": trend_ratio}

        if ratio >= 2.5:
            result.update({"label": "Çok Yüksek Hacim", "signal": "HIGH", "color": "green",
                          "desc": f"Hacim ortalamanın {ratio}x katında — güçlü kurumsal hareket sinyali."})
        elif ratio >= 1.5:
            result.update({"label": "Yüksek Hacim", "signal": "HIGH", "color": "green",
                          "desc": f"Hacim normalin %{round((ratio-1)*100)} üzerinde — artan piyasa ilgisi."})
        elif ratio >= 0.8:
            result.update({"label": "Normal Hacim", "signal": "NEUTRAL", "color": "gray",
                          "desc": "Hacim ortalama seviyede — olağan piyasa aktivitesi."})
        else:
            result.update({"label": "Düşük Hacim", "signal": "LOW", "color": "orange",
                          "desc": f"Hacim ortalamanın %{round(ratio*100)}'i — zayıf ilgi, fake-out riski yüksek."})

        if trend_ratio >= 1.3:
            result["trend"] = "ARTIYOR"
            result["trend_desc"] = "Son 5 mum hacmi yükseliyor — momentum artıyor."
        elif trend_ratio <= 0.7:
            result["trend"] = "AZALIYOR"
            result["trend_desc"] = "Son 5 mum hacmi düşüyor — momentum zayıflıyor."
        else:
            result["trend"] = "STABİL"
            result["trend_desc"] = "Hacim dengeli — belirgin bir değişiklik yok."

        if price_change > 0 and ratio >= 1.5:
            result["price_vol_harmony"] = "Fiyat ↑ + Yüksek hacim = SAĞLIKLI YÜKSELİŞ ✓"
        elif price_change < 0 and ratio >= 1.5:
            result["price_vol_harmony"] = "Fiyat ↓ + Yüksek hacim = GÜÇLÜ SATIŞ BASKISI ✗"
        elif price_change > 0 and ratio < 0.8:
            result["price_vol_harmony"] = "Fiyat ↑ + Düşük hacim = ZAYIF RALLY ⚠ (dikkat!)"
        elif price_change < 0 and ratio < 0.8:
            result["price_vol_harmony"] = "Fiyat ↓ + Düşük hacim = İlgi kaybı, yatay beklentisi"
        else:
            result["price_vol_harmony"] = "Fiyat-hacim uyumu nötr"
        return result

    def _check_fvg(df):
        """ICT Fair Value Gap (FVG) analizi — kurumsal likidite boşlukları"""
        fvgs = {"bullish": [], "bearish": []}
        if len(df) < 3:
            return {"has_fvg": False, "label": "Veri yetersiz", "signal": "NEUTRAL", "color": "gray"}
        n = len(df)
        search_start = max(0, n - 30)  # Son 30 mum
        current_price = df["close"].iloc[-1]

        for i in range(search_start + 1, n - 1):
            prev = df.iloc[i - 1]
            curr = df.iloc[i]
            next_ = df.iloc[i + 1]

            if prev["high"] < next_["low"]:
                gap = next_["low"] - prev["high"]
                gap_pct = (gap / curr["close"]) * 100
                if gap_pct >= 0.05:
                    filled = False
                    if i + 2 < n:
                        if df.iloc[i + 2:]["low"].min() <= prev["high"]:
                            filled = True
                    fvgs["bullish"].append({
                        "index": i, "gap_pct": round(gap_pct, 3),
                        "high": round(next_["low"], 8), "low": round(prev["high"], 8),
                        "filled": filled, "distance_bars": n - 1 - i
                    })

            if prev["low"] > next_["high"]:
                gap = prev["low"] - next_["high"]
                gap_pct = (gap / curr["close"]) * 100
                if gap_pct >= 0.05:
                    filled = False
                    if i + 2 < n:
                        if df.iloc[i + 2:]["high"].max() >= prev["low"]:
                            filled = True
                    fvgs["bearish"].append({
                        "index": i, "gap_pct": round(gap_pct, 3),
                        "high": round(prev["low"], 8), "low": round(next_["high"], 8),
                        "filled": filled, "distance_bars": n - 1 - i
                    })

        unfilled_bull = [f for f in fvgs["bullish"] if not f["filled"]]
        unfilled_bear = [f for f in fvgs["bearish"] if not f["filled"]]
        total_unfilled = len(unfilled_bull) + len(unfilled_bear)

        # En yakın FVG'leri bul
        nearest_bull = min(unfilled_bull, key=lambda x: x["distance_bars"]) if unfilled_bull else None
        nearest_bear = min(unfilled_bear, key=lambda x: x["distance_bars"]) if unfilled_bear else None

        result = {
            "has_fvg": total_unfilled > 0,
            "bullish_count": len(fvgs["bullish"]),
            "bearish_count": len(fvgs["bearish"]),
            "unfilled_bullish": len(unfilled_bull),
            "unfilled_bearish": len(unfilled_bear),
            "nearest_bull_price": nearest_bull["low"] if nearest_bull else None,
            "nearest_bear_price": nearest_bear["high"] if nearest_bear else None,
        }

        # FVG'ler fiyata yakınlık kontrolü
        bull_near_price = nearest_bull and abs(nearest_bull["low"] - current_price) / current_price * 100 < 1.5
        bear_near_price = nearest_bear and abs(nearest_bear["high"] - current_price) / current_price * 100 < 1.5

        if len(unfilled_bull) > len(unfilled_bear):
            proximity_note = ""
            if bull_near_price:
                proximity_note = f" En yakın boğa FVG ({nearest_bull['low']}) fiyata çok yakın — fiyat bu bölgeye çekilebilir (alım fırsatı)."
            result.update({"label": f"{len(unfilled_bull)} Boğa FVG", "signal": "BULLISH", "color": "green",
                          "desc": f"{len(unfilled_bull)} doldurulmamış boğa FVG tespit edildi. ICT teorisine göre fiyat bu boşlukları doldurmaya eğilimlidir.{proximity_note} FVG bölgelerinde limit emir konabilir."})
        elif len(unfilled_bear) > len(unfilled_bull):
            proximity_note = ""
            if bear_near_price:
                proximity_note = f" En yakın ayı FVG ({nearest_bear['high']}) fiyata çok yakın — fiyat bu bölgeye yükselebilir (satış bölgesi)."
            result.update({"label": f"{len(unfilled_bear)} Ayı FVG", "signal": "BEARISH", "color": "red",
                          "desc": f"{len(unfilled_bear)} doldurulmamış ayı FVG tespit edildi. Fiyat yukarı doğru bu boşlukları doldurup sonra dönebilir.{proximity_note} FVG bölgelerinde SHORT planlanabilir."})
        elif total_unfilled > 0:
            result.update({"label": f"{total_unfilled} FVG (karışık)", "signal": "NEUTRAL", "color": "orange",
                          "desc": f"Hem boğa ({len(unfilled_bull)}) hem ayı ({len(unfilled_bear)}) FVG mevcut — yön belirsiz. FVG'ler birbirini nötralize ediyor, diğer göstergelere bakın."})
        else:
            result.update({"label": "FVG Yok", "signal": "NEUTRAL", "color": "gray",
                          "desc": "Doldurulmamış FVG bulunamadı — tüm boşluklar kapanmış. Piyasa dengelenmiş durumda, yeni impuls hareketi bekleyin."})
        return result

    def _interpret_sr(supports, resistances, current_price):
        """Destek/direnç yorumu — pozisyon planlama için kritik seviyeler"""
        result = {"supports": [], "resistances": [], "nearest_support": None, "nearest_resistance": None}

        if supports:
            unique_s = sorted(set([round(s, 8) for s in supports if s < current_price]), reverse=True)[:3]
            result["supports"] = unique_s
            if unique_s:
                result["nearest_support"] = unique_s[0]
                dist_pct = ((current_price - unique_s[0]) / current_price) * 100
                result["support_dist_pct"] = round(dist_pct, 2)

        if resistances:
            unique_r = sorted(set([round(r, 8) for r in resistances if r > current_price]))[:3]
            result["resistances"] = unique_r
            if unique_r:
                result["nearest_resistance"] = unique_r[0]
                dist_pct = ((unique_r[0] - current_price) / current_price) * 100
                result["resistance_dist_pct"] = round(dist_pct, 2)

        # Konum analizi
        if result["nearest_support"] and result["nearest_resistance"]:
            s_dist = result.get("support_dist_pct", 100)
            r_dist = result.get("resistance_dist_pct", 100)
            total = s_dist + r_dist
            position = (s_dist / total * 100) if total > 0 else 50
            result["position_pct"] = round(position, 1)
            rr_ratio = round(r_dist / s_dist, 2) if s_dist > 0 else 0
            result["risk_reward"] = rr_ratio

            if s_dist < 0.3:
                result.update({"label": "Destek Üzerinde", "signal": "BULLISH", "color": "green",
                              "desc": f"Fiyat en yakın desteğe çok yakın (%{s_dist:.2f}). Burada tutunursa R/R: {rr_ratio:.1f}x ile alım fırsatı. SL destek altına konmalı."})
            elif r_dist < 0.3:
                result.update({"label": "Direnç Altında", "signal": "BEARISH", "color": "red",
                              "desc": f"Fiyat en yakın dirence çok yakın (%{r_dist:.2f}). Kırılamazsa geri çekilir. Burada yeni LONG açmak riskli — kırılım onayı bekleyin."})
            elif position < 25:
                result.update({"label": "Desteğe Yakın", "signal": "BULLISH", "color": "green",
                              "desc": f"Fiyat destek bölgesine yakın (destek: %{s_dist:.1f}, direnç: %{r_dist:.1f}). R/R: {rr_ratio:.1f}x — {'iyi alım bölgesi' if rr_ratio >= 2 else 'R/R oranı düşük, dikkatli olun'}."})
            elif position > 75:
                result.update({"label": "Direce Yakın", "signal": "BEARISH", "color": "orange",
                              "desc": f"Fiyat direnç bölgesine yakın (direnç: %{r_dist:.1f}, destek: %{s_dist:.1f}). R/R kötü — burada LONG açmak riskli. Direnç kırılırsa farklı hikaye."})
            else:
                result.update({"label": "Orta Bölge", "signal": "NEUTRAL", "color": "gray",
                              "desc": f"Fiyat destek (%{s_dist:.1f}) ve direnç (%{r_dist:.1f}) arasında ortada. R/R: {rr_ratio:.1f}x. Net alım/satım bölgesi değil — kenar seviyelere yaklaşana kadar bekleyin."})
        elif result["nearest_support"]:
            s_dist = result.get("support_dist_pct", 100)
            result.update({"label": f"Destek: {result['nearest_support']}", "signal": "NEUTRAL", "color": "gray",
                          "desc": f"Üst direnç tespit edilemedi. En yakın destek %{s_dist:.1f} aşağıda. Yeni zirve bölgesinde veya konsolidasyonda — kırılım yönünü bekleyin."})
        elif result["nearest_resistance"]:
            r_dist = result.get("resistance_dist_pct", 100)
            result.update({"label": f"Direnç: {result['nearest_resistance']}", "signal": "NEUTRAL", "color": "gray",
                          "desc": f"Alt destek tespit edilemedi. En yakın direnç %{r_dist:.1f} yukarıda. Düşüş sürecinde yeni dip aranıyor — LONG için acele etmeyin."})
        else:
            result.update({"label": "S/R Tespit Edilemedi", "signal": "NEUTRAL", "color": "gray",
                          "desc": "Yeterli pivot noktası bulunamadı. Veri yetersiz veya çok yatay hareket — bu göstergeden sinyal türetilemiyor."})
        return result

    # ── YENİ ANA STRATEJI YORUM FONKSİYONLARI ──

    def _interpret_donchian(current_price, upper, middle, lower, width, prev_width=None, prev_close=None, prev_upper=None, prev_lower=None):
        """Donchian Channel — Kırılım + Kanal Pozisyonu + Squeeze"""
        if upper is None or np.isnan(upper):
            return {"label": "Veri yok", "signal": "NEUTRAL", "color": "gray", "score": 0}

        position = ((current_price - lower) / (upper - lower) * 100) if (upper - lower) > 0 else 50
        result = {
            "upper": round(upper, 8), "middle": round(middle, 8), "lower": round(lower, 8),
            "width_pct": round(width, 2), "position": round(position, 1)
        }

        # Taze kırılım tespiti: önceki mum kanalın içindeydi, şimdi dışında
        fresh_breakout_up = prev_close is not None and prev_upper is not None and prev_close <= prev_upper and current_price > upper
        fresh_breakout_down = prev_close is not None and prev_lower is not None and prev_close >= prev_lower and current_price < lower

        # Squeeze tespiti: kanal daralıyor
        is_squeeze = prev_width is not None and width < prev_width * 0.7

        if fresh_breakout_up:
            result.update({"label": "TAZE KIRILIM ↑↑", "signal": "STRONG_BULLISH", "color": "green", "score": 35,
                          "desc": f"Fiyat {upper:.2f} direncini kırarak yeni 20 periyot zirvesi yaptı! Taze kırılım — güçlü momentum. Geri çekilmelerde LONG giriş değerlendirin."})
        elif fresh_breakout_down:
            result.update({"label": "TAZE KIRILIM ↓↓", "signal": "STRONG_BEARISH", "color": "red", "score": 35,
                          "desc": f"Fiyat {lower:.2f} desteğini kırarak yeni 20 periyot dibi yaptı! Taze kırılım — güçlü satış. Yükselişlerde SHORT değerlendirin."})
        elif position > 95:
            result.update({"label": "Üst Bantta", "signal": "BULLISH", "color": "green", "score": 20,
                          "desc": f"Fiyat Donchian üst bandına yapışık — trend güçlü ama kırılım taze değil. Uzama riski var."})
        elif position < 5:
            result.update({"label": "Alt Bantta", "signal": "BEARISH", "color": "red", "score": 20,
                          "desc": f"Fiyat Donchian alt bandına yapışık — düşüş trendi güçlü ama kırılım taze değil."})
        elif 40 <= position <= 60:
            if is_squeeze:
                result.update({"label": "SQUEEZE — Patlama Yakın ⚡", "signal": "NEUTRAL", "color": "orange", "score": 10,
                              "desc": f"Kanal daralıyor (genişlik: %{width:.1f}) — sıkışma sonrası büyük hareket bekleniyor. Kırılım yönünü bekleyin."})
            else:
                result.update({"label": "Kanal Ortası — Yön Yok", "signal": "NEUTRAL", "color": "gray", "score": 0,
                              "desc": f"Fiyat kanalın ortasında (pozisyon: %{position:.0f}). Net yön yok — uç noktalara yaklaşmasını bekleyin."})
        elif position > 75:
            result.update({"label": "Üst Banda Yakın", "signal": "BULLISH", "color": "lightgreen", "score": 15,
                          "desc": f"Fiyat üst banda yaklaşıyor (pozisyon: %{position:.0f}). Boğa basılınca devamı → pullback'te giriş fırsatı."})
        elif position < 25:
            result.update({"label": "Alt Banda Yakın", "signal": "BEARISH", "color": "orange", "score": 15,
                          "desc": f"Fiyat alt banda yaklaşıyor (pozisyon: %{position:.0f}). Ayı baskılıysa devamı → yükselişte SHORT fırsatı."})
        else:
            # 25-40 veya 60-75 arası
            bias = "hafif boğa" if position > 50 else "hafif ayı"
            result.update({"label": f"Kanal İçi ({bias})", "signal": "NEUTRAL", "color": "gray", "score": 5,
                          "desc": f"Fiyat kanal içinde (pozisyon: %{position:.0f}). Belirgin kırılım yok — kenar bölgelere kadar bekleyin."})
        return result

    def _interpret_vwap_dpo(current_price, vwap_val, vwap_std, dpo_val, dpo_std):
        """VWAP + DPO birleşik yorumu — fiyat makul mü ve döngüsel pozisyon"""
        if vwap_val is None or np.isnan(vwap_val):
            return {"label": "Veri yok", "signal": "NEUTRAL", "color": "gray", "score": 0}

        # VWAP mesafesi (standart sapma cinsinden)
        vwap_dev = (current_price - vwap_val) / vwap_std if vwap_std > 0 else 0
        vwap_dist_pct = ((current_price - vwap_val) / vwap_val * 100) if vwap_val > 0 else 0

        # DPO standart sapma cinsinden pozisyon
        dpo_dev = dpo_val / dpo_std if dpo_std > 0 else 0

        result = {
            "vwap": round(vwap_val, 8),
            "vwap_dist_pct": round(vwap_dist_pct, 2),
            "vwap_dev": round(vwap_dev, 2),
            "dpo": round(dpo_val, 8),
            "dpo_dev": round(dpo_dev, 2),
        }

        # Aşırı uzama: VWAP'tan +2σ veya DPO +2σ
        if vwap_dev >= 2.0 or dpo_dev >= 2.0:
            result.update({"label": f"AŞIRI UZANMIŞ ↑ (VWAP +{vwap_dev:.1f}σ)", "signal": "OVEREXTENDED_BULL", "color": "red", "score": 3,
                          "desc": f"Fiyat VWAP'tan %{vwap_dist_pct:+.1f} uzakta ({vwap_dev:+.1f}σ), DPO {dpo_dev:+.1f}σ. Çok uzamış — buradan LONG açmak tavan avcılığı. Geri çekilme bekleyin."})
        elif vwap_dev <= -2.0 or dpo_dev <= -2.0:
            result.update({"label": f"AŞIRI DÜŞMÜŞ ↓ (VWAP {vwap_dev:.1f}σ)", "signal": "OVEREXTENDED_BEAR", "color": "red", "score": 3,
                          "desc": f"Fiyat VWAP'tan %{vwap_dist_pct:+.1f} uzakta ({vwap_dev:+.1f}σ), DPO {dpo_dev:+.1f}σ. Çok düşmüş — buradan SHORT açmak dip avcılığı. Tepki yükselişi bekleyin."})
        elif -0.5 <= vwap_dev <= 0.5 and -0.5 <= dpo_dev <= 0.5:
            result.update({"label": f"İDEAL GİRİŞ BÖLGESİ ✓", "signal": "IDEAL_ENTRY", "color": "green", "score": 35,
                          "desc": f"Fiyat VWAP'a çok yakın ({vwap_dev:+.1f}σ) ve DPO nötr ({dpo_dev:+.1f}σ). Adil fiyat bölgesi — yön belirlendiyse en iyi giriş noktası."})
        elif -1.0 <= vwap_dev <= 1.0 and -1.0 <= dpo_dev <= 1.0:
            result.update({"label": f"Makul Giriş Bölgesi", "signal": "FAIR_ENTRY", "color": "lightgreen", "score": 28,
                          "desc": f"Fiyat VWAP'a yakın ({vwap_dev:+.1f}σ), DPO normal ({dpo_dev:+.1f}σ). Kabul edilebilir giriş — R/R uygunsa pozisyon alınabilir."})
        elif vwap_dev > 1.0 and dpo_dev > 0:
            result.update({"label": f"Uzamaya Başlıyor ↑ (VWAP +{vwap_dev:.1f}σ)", "signal": "STRETCHING_BULL", "color": "orange", "score": 12,
                          "desc": f"Fiyat VWAP'ın üstüne çıkmaya başladı ({vwap_dev:+.1f}σ). Trend devam edebilir ama giriş noktası geçmekte — dikkatli olun."})
        elif vwap_dev < -1.0 and dpo_dev < 0:
            result.update({"label": f"Düşüş Uzaması ↓ (VWAP {vwap_dev:.1f}σ)", "signal": "STRETCHING_BEAR", "color": "orange", "score": 12,
                          "desc": f"Fiyat VWAP'ın altına düşmeye devam ediyor ({vwap_dev:+.1f}σ). Düşüş sürebilir ama SHORT için geç kalınmış olabilir."})
        elif vwap_dev < -0.5 and dpo_dev > 0:
            # Fiyat VWAP altında ama DPO toparlanıyor — dip oluşumu
            result.update({"label": "Dip Oluşumu Sinyali ↗", "signal": "BOTTOM_FORMING", "color": "green", "score": 25,
                          "desc": f"Fiyat VWAP altında ({vwap_dev:+.1f}σ) ama DPO yukarı dönüyor ({dpo_dev:+.1f}σ). Dip oluşuyor — LONG için hazırlık."})
        elif vwap_dev > 0.5 and dpo_dev < 0:
            # Fiyat VWAP üstünde ama DPO düşüyor — tepe oluşumu
            result.update({"label": "Tepe Oluşumu Sinyali ↘", "signal": "TOP_FORMING", "color": "orange", "score": 25,
                          "desc": f"Fiyat VWAP üstünde ({vwap_dev:+.1f}σ) ama DPO aşağı dönüyor ({dpo_dev:+.1f}σ). Tepe oluşuyor — LONG'lardan çıkış hazırlığı."})
        else:
            result.update({"label": f"Normal Bölge", "signal": "NEUTRAL", "color": "gray", "score": 18,
                          "desc": f"Fiyat normal aralıkta (VWAP: {vwap_dev:+.1f}σ, DPO: {dpo_dev:+.1f}σ). Belirgin uzama yok."})
        return result

    def _interpret_cmf(val):
        """CMF yorumu — para akışı yönü"""
        if val is None or np.isnan(val):
            return {"value": None, "label": "Veri yok", "signal": "NEUTRAL", "color": "gray"}
        val = round(val, 4)
        if val >= 0.15:
            return {"value": val, "label": f"Güçlü Para Girişi ({val:+.3f})", "signal": "BULLISH", "color": "green",
                    "desc": f"CMF {val:+.3f} — Kapanışlar sürekli mumun üst yarısında ve yüksek hacimle. Kurumsal alım aktif."}
        elif val >= 0.05:
            return {"value": val, "label": f"Para Girişi ({val:+.3f})", "signal": "BULLISH", "color": "lightgreen",
                    "desc": f"CMF {val:+.3f} — Alıcılar baskın ama yeşil ışık yakacak kadar güçlü değil. Trend yönüyle uyumluysa giriş destekler."}
        elif val <= -0.15:
            return {"value": val, "label": f"Güçlü Para Çıkışı ({val:+.3f})", "signal": "BEARISH", "color": "red",
                    "desc": f"CMF {val:+.3f} — Kapanışlar sürekli mumun alt yarısında. Kurumsal satış aktif — LONG pozisyon riskli."}
        elif val <= -0.05:
            return {"value": val, "label": f"Para Çıkışı ({val:+.3f})", "signal": "BEARISH", "color": "orange",
                    "desc": f"CMF {val:+.3f} — Satıcılar hafif baskın. Trend tersi bir hareket oluşabilir."}
        else:
            return {"value": val, "label": f"Dengeli ({val:+.3f})", "signal": "NEUTRAL", "color": "gray",
                    "desc": f"CMF {val:+.3f} — Para akışı dengede. Alıcı/satıcı baskınlığı yok — yönü belirleyecek katalizör bekleyin."}

    def _interpret_mfi(val):
        """MFI yorumu — hacim ağırlıklı RSI"""
        if val is None or np.isnan(val):
            return {"value": None, "label": "Veri yok", "signal": "NEUTRAL", "color": "gray"}
        val = round(val, 1)
        if val >= 80:
            return {"value": val, "label": f"Aşırı Alım ({val})", "signal": "OVERBOUGHT", "color": "red",
                    "desc": f"MFI {val} — Hacim ağırlıklı aşırı alım. RSI'dan daha güvenilir çünkü gerçek para akışını ölçer. Yeni LONG riskli."}
        elif val >= 60:
            return {"value": val, "label": f"Alım Baskısı ({val})", "signal": "BULLISH", "color": "green",
                    "desc": f"MFI {val} — Sağlıklı para girişi var. Alıcılar aktif ama aşırıya kaçmamış — ideal bölge."}
        elif val <= 20:
            return {"value": val, "label": f"Aşırı Satım ({val})", "signal": "OVERSOLD", "color": "green",
                    "desc": f"MFI {val} — Hacim ağırlıklı aşırı satım. Satış baskısı tükeniyor — dönüş sinyali olabilir."}
        elif val <= 40:
            return {"value": val, "label": f"Satış Baskısı ({val})", "signal": "BEARISH", "color": "orange",
                    "desc": f"MFI {val} — Para çıkışı var. Satıcılar baskın — LONG pozisyon için uygun değil."}
        else:
            return {"value": val, "label": f"Nötr ({val})", "signal": "NEUTRAL", "color": "gray",
                    "desc": f"MFI {val} — Para akışı dengede (40-60 bandı). Belirgin alıcı/satıcı baskınlığı yok."}

    # ── ANA ANALİZ FONKSİYONU ──

    def _analyze_tf(df, tf_label):
        """Tek TF: Yapısal Giriş Noktası Stratejisi
        Ana Sütunlar: Donchian(35) + VWAP/DPO(35) + CMF/MFI/OBV(30)
        Destek: RSI, MACD, ADX, BB, S/R, FVG, Diverjans (max ±15)
        """
        if df is None or df.empty or len(df) < 30:
            return {
                "timeframe": tf_label, "error": "Yetersiz veri",
                "donchian": {"label": "Veri yok", "signal": "NEUTRAL", "score": 0},
                "vwap_dpo": {"label": "Veri yok", "signal": "NEUTRAL", "score": 0},
                "cmf": {"label": "Veri yok", "signal": "NEUTRAL"},
                "mfi": {"label": "Veri yok", "signal": "NEUTRAL"},
                "obv": {"label": "Veri yok", "signal": "NEUTRAL"},
                "rsi": {"value": None, "label": "Veri yok", "signal": "NEUTRAL"},
                "stoch_rsi": {"label": "Veri yok", "signal": "NEUTRAL"},
                "macd": {"label": "Veri yok", "signal_type": "NEUTRAL"},
                "bollinger": {"label": "Veri yok", "signal": "NEUTRAL"},
                "adx": {"label": "Veri yok", "signal": "NEUTRAL"},
                "atr": {"label": "Veri yok", "signal": "NEUTRAL"},
                "volume": {"label": "Veri yok", "signal": "NEUTRAL"},
                "fvg": {"label": "Veri yok", "signal": "NEUTRAL"},
                "support_resistance": {"label": "Veri yok", "signal": "NEUTRAL"},
                "divergence": {"label": "Veri yok", "type": "NONE"},
                "verdict": "NEUTRAL", "verdict_label": "VERİ YOK",
                "verdict_color": "gray", "direction": "NONE",
                "bull_score": 0, "bear_score": 0, "net_score": 0, "confidence": 0,
                "pillar_scores": {
                    "donchian": {"score": 0, "max": 35, "label": "Kırılım & Yön"},
                    "vwap_dpo": {"score": 0, "max": 35, "label": "Fiyat Makul mü?"},
                    "money_flow": {"score": 0, "max": 30, "direction": "NEUTRAL", "label": "Para Akışı"},
                    "support_adj": {"score": 0, "max": 15, "label": "Destek Göstergeler"},
                }
            }

        close = df["close"]
        current_price = close.iloc[-1]
        prev_close = close.iloc[-2] if len(close) >= 2 else current_price

        # ════════════ ANA SÜTUN 1: DONCHIAN CHANNEL (35 puan) ════════════
        dc_upper, dc_middle, dc_lower, dc_width = _donchian(df, period=20)
        dc_u = dc_upper.iloc[-1] if not dc_upper.empty else None
        dc_m = dc_middle.iloc[-1] if not dc_middle.empty else None
        dc_l = dc_lower.iloc[-1] if not dc_lower.empty else None
        dc_w = dc_width.iloc[-1] if not dc_width.empty else None
        prev_dc_u = dc_upper.iloc[-2] if len(dc_upper) >= 2 else None
        prev_dc_l = dc_lower.iloc[-2] if len(dc_lower) >= 2 else None
        prev_dc_w = dc_width.iloc[-5] if len(dc_width) >= 5 else None
        donchian_result = _interpret_donchian(current_price, dc_u, dc_m, dc_l, dc_w, prev_dc_w, prev_close, prev_dc_u, prev_dc_l)

        # ════════════ ANA SÜTUN 2: VWAP + DPO (35 puan) ════════════
        vwap_series, vwap_std_series = _vwap_rolling(df, period=50)
        vwap_val = vwap_series.iloc[-1] if not vwap_series.empty and not np.isnan(vwap_series.iloc[-1]) else None
        vwap_std_val = vwap_std_series.iloc[-1] if not vwap_std_series.empty and not np.isnan(vwap_std_series.iloc[-1]) else 0

        dpo_series = _dpo(close, period=20)
        dpo_val = dpo_series.iloc[-1] if not dpo_series.empty and not np.isnan(dpo_series.iloc[-1]) else 0
        dpo_std_val = dpo_series.dropna().std() if len(dpo_series.dropna()) > 5 else 1

        vwap_dpo_result = _interpret_vwap_dpo(current_price, vwap_val, vwap_std_val, dpo_val, dpo_std_val)

        # ════════════ ANA SÜTUN 3: PARA AKIŞI — CMF + MFI + OBV (30 puan) ════════════
        cmf_series = _cmf(df, period=20)
        cmf_val = cmf_series.iloc[-1] if not cmf_series.empty and not np.isnan(cmf_series.iloc[-1]) else None
        cmf_result = _interpret_cmf(cmf_val)

        mfi_series = _mfi(df, period=14)
        mfi_val = mfi_series.iloc[-1] if not mfi_series.empty and not np.isnan(mfi_series.iloc[-1]) else None
        mfi_result = _interpret_mfi(mfi_val)

        obv_series = _obv(df)
        obv_result = _interpret_obv(obv_series, close)

        # ════════════ DESTEK GÖSTERGELERİ (bilgilendirici + max ±15 bonus) ════════════
        # RSI
        rsi_series = _rsi(close, 14)
        rsi_val = rsi_series.iloc[-1] if not rsi_series.empty else None
        rsi_result = _interpret_rsi(rsi_val)

        # Stochastic RSI
        stoch_k, stoch_d = _stoch_rsi(close)
        k_val = stoch_k.iloc[-1] if not stoch_k.empty and not np.isnan(stoch_k.iloc[-1]) else None
        d_val = stoch_d.iloc[-1] if not stoch_d.empty and not np.isnan(stoch_d.iloc[-1]) else None
        stoch_result = _interpret_stoch_rsi(k_val, d_val)

        # MACD
        macd_line, signal_line, histogram = _macd(close)
        macd_val = macd_line.iloc[-1] if not macd_line.empty else None
        sig_val = signal_line.iloc[-1] if not signal_line.empty else None
        hist_val = histogram.iloc[-1] if not histogram.empty else None
        prev_hist = histogram.iloc[-2] if len(histogram) >= 2 else None
        macd_result = _interpret_macd(macd_val, sig_val, hist_val, prev_hist)

        # Bollinger Bands
        bb_upper, bb_middle, bb_lower = _bollinger_bands(close)
        bb_width_series = ((bb_upper - bb_lower) / bb_middle).dropna()
        bb_width = bb_width_series.iloc[-1] if not bb_width_series.empty else 0
        prev_bb_width = bb_width_series.iloc[-5] if len(bb_width_series) >= 5 else None
        bb_result = _interpret_bb(current_price, bb_upper.iloc[-1], bb_middle.iloc[-1], bb_lower.iloc[-1], bb_width, prev_bb_width)

        # ADX
        adx_series, plus_di, minus_di = _adx(df)
        adx_val = adx_series.iloc[-1] if not adx_series.empty and not np.isnan(adx_series.iloc[-1]) else None
        pdi_val = plus_di.iloc[-1] if not plus_di.empty else None
        mdi_val = minus_di.iloc[-1] if not minus_di.empty else None
        adx_result = _interpret_adx(adx_val, pdi_val, mdi_val)

        # ATR
        atr_series = _atr(df)
        atr_val = atr_series.iloc[-1] if not atr_series.empty else None
        atr_result = _interpret_atr(atr_val, current_price)

        # Volume
        vol_result = _analyze_volume(df)

        # FVG
        fvg_result = _check_fvg(df)

        # Destek/Direnç
        supports, resistances = _find_support_resistance(df)
        sr_result = _interpret_sr(supports, resistances, current_price)

        # Diverjans
        div_result = _detect_divergence(close, rsi_series, lookback=25)
        if div_result is None:
            div_result = {"type": "NONE", "label": "Veri yetersiz", "desc": "", "color": "gray"}

        # ════════════ SKORLAMA: 3 ANA SÜTUN + DESTEK BONUS ════════════

        # --- Sütun 1: Donchian — yön ve kırılım (max 35) ---
        dc_signal = donchian_result.get("signal", "NEUTRAL")
        dc_score = donchian_result.get("score", 0)

        # Donchian yönü belirler
        if dc_signal in ("STRONG_BULLISH", "BULLISH"):
            direction = "LONG"
        elif dc_signal in ("STRONG_BEARISH", "BEARISH"):
            direction = "SHORT"
        else:
            direction = "NONE"

        # --- Sütun 2: VWAP + DPO — fiyat makul mü (max 35) ---
        vd_signal = vwap_dpo_result.get("signal", "NEUTRAL")
        vd_score = vwap_dpo_result.get("score", 0)

        # VWAP/DPO uyumsuzluk kontrolü: yön ve fiyat yapısı çelişiyorsa cezalandır
        if direction == "LONG" and vd_signal in ("OVEREXTENDED_BULL", "TOP_FORMING"):
            vd_score = min(vd_score, 5)  # uzamış/tepe piyasada LONG skoru düşür
        elif direction == "SHORT" and vd_signal in ("OVEREXTENDED_BEAR", "BOTTOM_FORMING"):
            vd_score = min(vd_score, 5)  # düşmüş/dip piyasada SHORT skoru düşür
        # Ters yönde de kontrol: direction=LONG ama fiyat aşırı düşük → VWAP skoru korunur (iyi giriş)
        # direction=SHORT ama fiyat aşırı yüksek → VWAP skoru korunur (iyi giriş)

        # --- Sütun 3: Para Akışı — CMF + MFI + OBV (max 30) ---
        flow_score = 0
        flow_bull = 0
        flow_bear = 0

        # CMF (max 12)
        cmf_sig = cmf_result.get("signal", "NEUTRAL")
        if cmf_sig == "BULLISH":
            s = 12 if (cmf_result.get("value") or 0) >= 0.15 else 8
            flow_bull += s
        elif cmf_sig == "BEARISH":
            s = 12 if (cmf_result.get("value") or 0) <= -0.15 else 8
            flow_bear += s

        # MFI (max 10)
        mfi_sig = mfi_result.get("signal", "NEUTRAL")
        mfi_v = mfi_result.get("value") or 50
        if mfi_sig == "BULLISH":
            flow_bull += 10
        elif mfi_sig == "BEARISH":
            flow_bear += 10
        elif mfi_sig == "OVERBOUGHT":
            flow_bear += 8  # aşırı alım → güçlü satış sinyali (BULLISH'ten az olamaz)
        elif mfi_sig == "OVERSOLD":
            flow_bull += 8  # aşırı satım → güçlü alım sinyali

        # OBV (max 8)
        obv_sig = obv_result.get("signal", "NEUTRAL")
        if obv_sig == "BULLISH":
            flow_bull += 8
        elif obv_sig == "BEARISH":
            flow_bear += 8

        # Net para akışı skoru
        if flow_bull > flow_bear:
            flow_score = flow_bull
            flow_direction = "BULL"
        elif flow_bear > flow_bull:
            flow_score = flow_bear
            flow_direction = "BEAR"
        else:
            flow_score = max(flow_bull, flow_bear)  # eşitlikte de skoru koru
            flow_direction = "NEUTRAL"

        # --- DESTEK GÖSTERGELERİ BONUS/CEZA (max ±15) ---
        support_bonus = 0

        # MACD (±3)
        macd_sig = macd_result.get("signal_type", "NEUTRAL")
        if macd_sig == "BULLISH":
            support_bonus += 3
        elif macd_sig == "BEARISH":
            support_bonus -= 3
        elif macd_sig == "WEAKENING_BULL":
            support_bonus -= 1
        elif macd_sig == "WEAKENING_BEAR":
            support_bonus += 1

        # RSI (±3) — sadece aşırı bölgelerde
        rsi_v = rsi_result.get("value") or 50
        if rsi_v >= 75:
            support_bonus -= 3  # aşırı alım
        elif rsi_v >= 65:
            support_bonus -= 1
        elif rsi_v <= 25:
            support_bonus += 3  # aşırı satım
        elif rsi_v <= 35:
            support_bonus += 1

        # ADX trend gücü (±2)
        adx_v = adx_result.get("adx") or 0
        if adx_v >= 30 and adx_result.get("signal") == "BULLISH":
            support_bonus += 2
        elif adx_v >= 30 and adx_result.get("signal") == "BEARISH":
            support_bonus -= 2

        # S/R Risk/Reward (±5)
        rr = sr_result.get("risk_reward")
        sr_sig = sr_result.get("signal", "NEUTRAL")
        if rr is not None:
            if direction == "LONG":
                if rr >= 3.0:
                    support_bonus += 4
                elif rr >= 2.0:
                    support_bonus += 2
                elif rr < 1.0:
                    support_bonus -= 5  # R/R kötü — LONG cezalandır
                elif rr < 1.5:
                    support_bonus -= 3
            elif direction == "SHORT":
                inv_rr = 1.0 / rr if rr > 0 else 0
                if inv_rr >= 3.0:
                    support_bonus -= 4  # SHORT için iyi R/R
                elif inv_rr >= 2.0:
                    support_bonus -= 2
                elif inv_rr < 1.0:
                    support_bonus += 5  # SHORT için kötü R/R
                elif inv_rr < 1.5:
                    support_bonus += 3
        # Direnç/destek yakınlık cezası
        if sr_sig == "BEARISH" and direction == "LONG":
            support_bonus -= 2  # Dirence yakınken LONG ceza
        elif sr_sig == "BULLISH" and direction == "SHORT":
            support_bonus += 2  # Desteğe yakınken SHORT ceza

        # Bollinger (±2)
        bb_sig = bb_result.get("signal", "NEUTRAL")
        if bb_sig == "BULLISH":
            support_bonus += 2
        elif bb_sig == "BEARISH":
            support_bonus -= 2

        # Diverjans (önemli contrarian sinyal, ±3)
        if div_result.get("type") == "BULLISH":
            support_bonus += 3
        elif div_result.get("type") == "BEARISH":
            support_bonus -= 3

        # Bonusu sınırla
        support_bonus = max(-15, min(15, support_bonus))

        # ════════════ TOPLAM SKOR HESAPLA ════════════
        # Bull tarafı
        bull_total = 0
        bear_total = 0

        if direction == "LONG":
            bull_total = dc_score + vd_score + (flow_score if flow_direction == "BULL" else 0) + max(support_bonus, 0)
            bear_total = (flow_score if flow_direction == "BEAR" else 0) + abs(min(support_bonus, 0))
        elif direction == "SHORT":
            bear_total = dc_score + vd_score + (flow_score if flow_direction == "BEAR" else 0) + abs(min(support_bonus, 0))
            bull_total = (flow_score if flow_direction == "BULL" else 0) + max(support_bonus, 0)
        else:
            # Yön yok — Donchian kırılım/yön vermedi
            # VWAP/DPO yön-bağımsız konumlandırma göstergesi:
            #   FAIR_ENTRY/IDEAL_ENTRY = "fiyat adil seviyede" → ne boğa ne ayı
            #   OVEREXTENDED/TOP/BOTTOM = yön bilgisi içerir → kısmi bonus
            # Sadece para akışı + destek göstergeler yön belirler
            
            # VWAP yönsel katkısı: aşırı durumlar yön verir, adil giriş nötr
            vwap_adj = 0
            if vd_signal in ("OVEREXTENDED_BULL", "TOP_FORMING", "STRETCHING_BULL"):
                vwap_adj = -min(vd_score * 0.3, 10)   # ayı yönünde max 10 puan
            elif vd_signal in ("OVEREXTENDED_BEAR", "BOTTOM_FORMING", "STRETCHING_BEAR"):
                vwap_adj = min(vd_score * 0.3, 10)    # boğa yönünde max 10 puan
            # FAIR_ENTRY, IDEAL_ENTRY, NEUTRAL → 0 (yön bilgisi yok)
            
            adj_support = support_bonus + vwap_adj
            
            if flow_direction == "BULL":
                bull_total = flow_score + max(adj_support, 0)
                bear_total = abs(min(adj_support, 0))
            elif flow_direction == "BEAR":
                bear_total = flow_score + abs(min(adj_support, 0))
                bull_total = max(adj_support, 0)
            else:
                # Para akışı da kararsız → tamamen nötr, sadece destek göstergeler
                bull_total = max(adj_support, 0)
                bear_total = abs(min(adj_support, 0))

        net_score = round(bull_total - bear_total, 1)
        confidence = round(max(bull_total, bear_total), 1)

        # ════════════ VERDİCT ════════════
        if net_score >= 60:
            verdict = "STRONG_BULLISH"
            verdict_label = "GÜÇLÜ LONG ✅"
            verdict_color = "green"
        elif net_score >= 35:
            verdict = "BULLISH"
            verdict_label = "LONG"
            verdict_color = "green"
        elif net_score >= 15:
            verdict = "LEANING_BULLISH"
            verdict_label = "HAFİF LONG"
            verdict_color = "lightgreen"
        elif net_score <= -60:
            verdict = "STRONG_BEARISH"
            verdict_label = "GÜÇLÜ SHORT ✅"
            verdict_color = "red"
        elif net_score <= -35:
            verdict = "BEARISH"
            verdict_label = "SHORT"
            verdict_color = "red"
        elif net_score <= -15:
            verdict = "LEANING_BEARISH"
            verdict_label = "HAFİF SHORT"
            verdict_color = "orange"
        else:
            verdict = "NEUTRAL"
            verdict_label = "BEKLE ⏳"
            verdict_color = "gray"

        # Pillar puanları (UI'da göstermek için)
        pillar_scores = {
            "donchian": {"score": dc_score, "max": 35, "label": "Kırılım & Yön"},
            "vwap_dpo": {"score": vd_score, "max": 35, "label": "Fiyat Makul mü?"},
            "money_flow": {"score": flow_score, "max": 30, "direction": flow_direction, "label": "Para Akışı"},
            "support_adj": {"score": support_bonus, "max": 15, "label": "Destek Göstergeler"},
        }

        # ════════════ FINAL DIRECTION: Verdict'ten türet ════════════
        # Donchian direction sadece iç SKORLAMA içindir.
        # Kullanıcıya gösterilen direction, verdikt ile tutarlı olmalı.
        if verdict in ("STRONG_BULLISH", "BULLISH", "LEANING_BULLISH"):
            final_direction = "LONG"
        elif verdict in ("STRONG_BEARISH", "BEARISH", "LEANING_BEARISH"):
            final_direction = "SHORT"
        else:
            final_direction = "NONE"

        return {
            "timeframe": tf_label,
            # Ana strateji göstergeleri
            "donchian": donchian_result,
            "vwap_dpo": vwap_dpo_result,
            "cmf": cmf_result,
            "mfi": mfi_result,
            "obv": obv_result,
            # Destek göstergeler
            "rsi": rsi_result,
            "stoch_rsi": stoch_result,
            "macd": macd_result,
            "bollinger": bb_result,
            "adx": adx_result,
            "atr": atr_result,
            "volume": vol_result,
            "fvg": fvg_result,
            "support_resistance": sr_result,
            "divergence": div_result,
            # Strateji sonucu
            "direction": final_direction,
            "verdict": verdict,
            "verdict_label": verdict_label,
            "verdict_color": verdict_color,
            "bull_score": round(bull_total, 1),
            "bear_score": round(bear_total, 1),
            "net_score": net_score,
            "confidence": confidence,
            "pillar_scores": pillar_scores,
        }

    try:
        import pandas as pd
        multi_tf = data_fetcher.get_multi_timeframe_data(symbol)

        # Her TF için gelişmiş analiz
        tf_results = {}
        for tf_key, tf_label in [("15m", "15 Dakika"), ("1H", "1 Saat"), ("4H", "4 Saat")]:
            df_tf = multi_tf.get(tf_key)
            tf_results[tf_key] = _analyze_tf(df_tf, tf_label)

        # Ticker bilgisi
        ticker = data_fetcher.get_ticker(symbol)
        price_info = {}
        if ticker:
            price_info = {
                "last": ticker["last"],
                "high24h": ticker.get("high24h", 0),
                "low24h": ticker.get("low24h", 0),
                "change24h": ticker.get("changePct24h", 0),
                "vol24h": ticker.get("vol24h", 0)
            }

        # Order Book analizi
        orderbook_result = {"label": "Veri yok", "signal": "NEUTRAL", "color": "gray"}
        try:
            book = data_fetcher.get_orderbook(symbol, depth=20)
            if book:
                total_bids = sum(b[1] for b in book["bids"])
                total_asks = sum(a[1] for a in book["asks"])
                imbalance = (total_bids / (total_bids + total_asks)) * 100 if (total_bids + total_asks) > 0 else 50

                # Büyük duvarlar
                avg_bid = total_bids / max(len(book["bids"]), 1)
                avg_ask = total_asks / max(len(book["asks"]), 1)
                bid_walls = sum(1 for b in book["bids"] if b[1] > avg_bid * 3)
                ask_walls = sum(1 for a in book["asks"] if a[1] > avg_ask * 3)

                orderbook_result = {
                    "bid_total": round(total_bids, 2),
                    "ask_total": round(total_asks, 2),
                    "imbalance": round(imbalance, 1),
                    "bid_walls": bid_walls,
                    "ask_walls": ask_walls,
                    "spread_pct": round(((book["asks"][0][0] - book["bids"][0][0]) / book["bids"][0][0]) * 100, 4) if book["bids"] and book["asks"] else 0,
                }

                if imbalance >= 65:
                    orderbook_result.update({"label": f"Güçlü Alım Baskısı (%{imbalance:.0f})", "signal": "BULLISH", "color": "green",
                                            "desc": f"Alım emirleri %{imbalance:.0f} ile baskın — alıcılar fiyatı yukarı itiyor."})
                elif imbalance >= 55:
                    orderbook_result.update({"label": f"Hafif Alım Baskısı (%{imbalance:.0f})", "signal": "BULLISH", "color": "lightgreen",
                                            "desc": f"Alım tarafı hafif baskın (%{imbalance:.0f}) — kısa vadeli destek mevcut."})
                elif imbalance <= 35:
                    orderbook_result.update({"label": f"Güçlü Satış Baskısı (%{imbalance:.0f})", "signal": "BEARISH", "color": "red",
                                            "desc": f"Satış emirleri %{100-imbalance:.0f} ile baskın — satıcılar fiyatı aşağı çekiyor."})
                elif imbalance <= 45:
                    orderbook_result.update({"label": f"Hafif Satış Baskısı (%{imbalance:.0f})", "signal": "BEARISH", "color": "orange",
                                            "desc": f"Satış tarafı hafif baskın (%{100-imbalance:.0f}) — kısa vadeli baskı var."})
                else:
                    orderbook_result.update({"label": f"Dengeli (%{imbalance:.0f})", "signal": "NEUTRAL", "color": "gray",
                                            "desc": "Alım-satım emirleri dengede — belirleyici bir taraf yok."})

                if bid_walls > 0:
                    orderbook_result["desc"] += f" | {bid_walls} büyük alım duvarı tespit edildi."
                if ask_walls > 0:
                    orderbook_result["desc"] += f" | {ask_walls} büyük satış duvarı tespit edildi."
        except Exception:
            pass

        # ── PİYASA VERİLERİ: Fonlama, Açık Faiz, Long/Short Ratio ──
        market_data = {"funding": None, "open_interest": None, "long_short_ratio": None}
        market_data_score = 0  # Genel karara katkı
        try:
            # Fonlama oranı
            funding = data_fetcher.get_funding_rate(symbol)
            if funding:
                fr = funding["current"]
                market_data["funding"] = {
                    "current": round(fr, 4),
                    "next": round(funding["next"], 4),
                    "next_time": funding["next_time"],
                }
                if fr > 0.05:
                    market_data["funding"]["signal"] = "BEARISH"
                    market_data["funding"]["label"] = f"Yüksek Pozitif ({fr:.4f}%)"
                    market_data["funding"]["desc"] = "Long'lar short'lara ödeme yapıyor. Aşırı long kalabalık — düşüş riski."
                    market_data_score -= 3
                elif fr > 0.01:
                    market_data["funding"]["signal"] = "NEUTRAL"
                    market_data["funding"]["label"] = f"Normal Pozitif ({fr:.4f}%)"
                    market_data["funding"]["desc"] = "Hafif long ağırlıklı piyasa — normal koşullar."
                elif fr < -0.05:
                    market_data["funding"]["signal"] = "BULLISH"
                    market_data["funding"]["label"] = f"Yüksek Negatif ({fr:.4f}%)"
                    market_data["funding"]["desc"] = "Short'lar long'lara ödeme yapıyor. Aşırı short kalabalık — yükseliş riski."
                    market_data_score += 3
                elif fr < -0.01:
                    market_data["funding"]["signal"] = "NEUTRAL"
                    market_data["funding"]["label"] = f"Normal Negatif ({fr:.4f}%)"
                    market_data["funding"]["desc"] = "Hafif short ağırlıklı — normal koşullar."
                else:
                    market_data["funding"]["signal"] = "NEUTRAL"
                    market_data["funding"]["label"] = f"Nötr ({fr:.4f}%)"
                    market_data["funding"]["desc"] = "Fonlama dengesinde — piyasa tarafsız."

            # Açık faiz
            oi = data_fetcher.get_open_interest(symbol)
            if oi and oi["oi"] > 0:
                oi_usdt = oi["oi_usdt"]
                oi_text = f"${oi_usdt/1_000_000:.1f}M" if oi_usdt >= 1_000_000 else f"${oi_usdt:,.0f}"
                market_data["open_interest"] = {
                    "value": oi["oi"],
                    "usdt": oi_usdt,
                    "display": oi_text,
                    "signal": "NEUTRAL",
                    "label": oi_text,
                    "desc": f"Açık pozisyon: {oi_text}. Yüksek OI + fiyat artışı = sağlıklı trend. Yüksek OI + düşüş = tasfiye riski."
                }

            # Long/Short oranı
            lsr = data_fetcher.get_long_short_ratio(symbol)
            if lsr:
                market_data["long_short_ratio"] = {}
                for period_key, ratio in lsr.items():
                    if ratio is None:
                        continue
                    long_pct = round(ratio / (1 + ratio) * 100, 1)
                    short_pct = round(100 - long_pct, 1)
                    if ratio > 2.0:
                        sig = "BEARISH"
                        lbl = f"Aşırı Long ({ratio:.2f})"
                        desc = f"Long %{long_pct} / Short %{short_pct} — Aşırı long kalabalık, tasfiye riski."
                        if period_key == "1D":
                            market_data_score -= 2
                    elif ratio > 1.3:
                        sig = "NEUTRAL"
                        lbl = f"Long Ağırlıklı ({ratio:.2f})"
                        desc = f"Long %{long_pct} / Short %{short_pct} — Hafif long baskın."
                    elif ratio < 0.5:
                        sig = "BULLISH"
                        lbl = f"Aşırı Short ({ratio:.2f})"
                        desc = f"Long %{long_pct} / Short %{short_pct} — Aşırı short kalabalık, short squeeze riski."
                        if period_key == "1D":
                            market_data_score += 2
                    elif ratio < 0.75:
                        sig = "NEUTRAL"
                        lbl = f"Short Ağırlıklı ({ratio:.2f})"
                        desc = f"Long %{long_pct} / Short %{short_pct} — Hafif short baskın."
                    else:
                        sig = "NEUTRAL"
                        lbl = f"Dengeli ({ratio:.2f})"
                        desc = f"Long %{long_pct} / Short %{short_pct} — Piyasa dengesinde."

                    market_data["long_short_ratio"][period_key] = {
                        "ratio": ratio, "long_pct": long_pct, "short_pct": short_pct,
                        "signal": sig, "label": lbl, "desc": desc
                    }
        except Exception as e:
            logger.debug(f"Piyasa verileri hatası ({symbol}): {e}")

        # ── GELİŞMİŞ GENEL YORUM (Ağırlıklı TF Kombinasyonu) ──
        # 4H: %50, 1H: %30, 15m: %20 ağırlık
        tf_weights = {"4H": 0.50, "1H": 0.30, "15m": 0.20}
        total_bull = 0
        total_bear = 0

        for tf_key, weight in tf_weights.items():
            tf = tf_results[tf_key]
            total_bull += tf.get("bull_score", 0) * weight
            total_bear += tf.get("bear_score", 0) * weight

        overall_net = round(total_bull - total_bear, 1)
        overall_confidence = round(max(total_bull, total_bear), 1)

        # TF uyum kontrolü (tüm TF'ler aynı yönde = ekstra güven)
        tf_verdicts = [tf_results[k].get("verdict", "NEUTRAL") for k in ["15m", "1H", "4H"]]
        all_bull = all(v in ("STRONG_BULLISH", "BULLISH", "LEANING_BULLISH") for v in tf_verdicts)
        all_bear = all(v in ("STRONG_BEARISH", "BEARISH", "LEANING_BEARISH") for v in tf_verdicts)

        # TF çelişki kontrolü — 4H ve 15m zıt yönde ise güveni düşür
        v_4h = tf_results["4H"].get("verdict", "NEUTRAL")
        v_1h = tf_results["1H"].get("verdict", "NEUTRAL")
        v_15m = tf_results["15m"].get("verdict", "NEUTRAL")
        bull_set = {"STRONG_BULLISH", "BULLISH", "LEANING_BULLISH"}
        bear_set = {"STRONG_BEARISH", "BEARISH", "LEANING_BEARISH"}
        tf_conflict = (v_4h in bull_set and v_15m in bear_set) or (v_4h in bear_set and v_15m in bull_set)

        # Ayrıca 4H-1H çelişkisi de kontrol et
        tf_conflict_4h_1h = (v_4h in bull_set and v_1h in bear_set) or (v_4h in bear_set and v_1h in bull_set)

        # ── Her TF'nin yön etiketini hazırla (açıklamada kullanılacak) ──
        def _tf_direction_label(verdict, net):
            if verdict in ("STRONG_BULLISH", "BULLISH"):
                return f"LONG (+{abs(net):.0f})"
            elif verdict == "LEANING_BULLISH":
                return f"Hafif LONG (+{abs(net):.0f})"
            elif verdict in ("STRONG_BEARISH", "BEARISH"):
                return f"SHORT ({net:.0f})"
            elif verdict == "LEANING_BEARISH":
                return f"Hafif SHORT ({net:.0f})"
            else:
                return f"Nötr ({net:+.0f})"

        tf_summary_4h = _tf_direction_label(v_4h, tf_results["4H"].get("net_score", 0))
        tf_summary_1h = _tf_direction_label(v_1h, tf_results["1H"].get("net_score", 0))
        tf_summary_15m = _tf_direction_label(v_15m, tf_results["15m"].get("net_score", 0))
        tf_breakdown = f"4H: {tf_summary_4h} | 1H: {tf_summary_1h} | 15m: {tf_summary_15m}"

        # ── MOMENTUM İVME ANALİZİ (Cross-TF MACD Histogram) ──
        # Her TF'nin MACD histogram yönünü analiz et
        momentum_accel = {"status": "NEUTRAL", "detail": "", "score_adj": 0}
        try:
            hist_data = {}
            for tf_key in ["15m", "1H", "4H"]:
                macd_info = tf_results[tf_key].get("macd", {})
                sig_type = macd_info.get("signal_type", "NEUTRAL")
                hist_val = macd_info.get("histogram")
                hist_data[tf_key] = {"signal_type": sig_type, "histogram": hist_val}

            h4_sig = hist_data["4H"]["signal_type"]
            h1_sig = hist_data["1H"]["signal_type"]
            m15_sig = hist_data["15m"]["signal_type"]

            # İvme hızlanıyor: Tüm TF'lerde aynı yönde ve güçleniyor
            bull_accel_types = {"BULLISH"}
            bear_accel_types = {"BEARISH"}
            bull_any = {"BULLISH", "WEAKENING_BULL"}
            bear_any = {"BEARISH", "WEAKENING_BEAR"}

            # Hızlanan boğa: 4H boğa + 1H boğa güçleniyor + 15m boğa
            if h4_sig in bull_any and h1_sig in bull_accel_types and m15_sig in bull_accel_types:
                momentum_accel = {"status": "BULL_ACCELERATING",
                                  "detail": "Tüm TF'lerde momentum hızlanıyor ↑↑ — güçlü yükseliş ivmesi.",
                                  "score_adj": 5}
            # Hızlanan ayı: 4H ayı + 1H ayı güçleniyor + 15m ayı
            elif h4_sig in bear_any and h1_sig in bear_accel_types and m15_sig in bear_accel_types:
                momentum_accel = {"status": "BEAR_ACCELERATING",
                                  "detail": "Tüm TF'lerde momentum düşüş yönünde hızlanıyor ↓↓ — güçlü satış ivmesi.",
                                  "score_adj": -5}
            # Zayıflayan boğa: 4H boğa ama 1H veya 15m zayıflıyor
            elif h4_sig in bull_any and (h1_sig == "WEAKENING_BULL" or m15_sig == "WEAKENING_BEAR"):
                momentum_accel = {"status": "BULL_FADING",
                                  "detail": "4H MACD boğa bölgede ama kısa vadede ivme zayıflıyor — geri çekilme riski.",
                                  "score_adj": -3}
            # Zayıflayan ayı: 4H ayı ama 1H veya 15m toparlanıyor
            elif h4_sig in bear_any and (h1_sig == "WEAKENING_BEAR" or m15_sig == "WEAKENING_BULL"):
                momentum_accel = {"status": "BEAR_FADING",
                                  "detail": "4H MACD ayı bölgede ama kısa vadede satış baskısı azalıyor — toparlanma olası.",
                                  "score_adj": 3}
            # Momentum dönüşü: 4H bir yönde ama 1H+15m ters yönde
            elif h4_sig in bull_any and h1_sig in bear_accel_types and m15_sig in bear_accel_types:
                momentum_accel = {"status": "BULL_REVERSAL_RISK",
                                  "detail": "4H MACD boğa ama 1H ve 15m düşüş ivmesinde — trend dönüşü riski!",
                                  "score_adj": -4}
            elif h4_sig in bear_any and h1_sig in bull_accel_types and m15_sig in bull_accel_types:
                momentum_accel = {"status": "BEAR_REVERSAL_RISK",
                                  "detail": "4H MACD ayı bölgede ama 1H ve 15m yükseliş ivmesinde — dip oluşuyor olabilir.",
                                  "score_adj": 4}
        except Exception:
            pass

        # Orderbook ekstra puan (azaltıldı: max ±2)
        orderbook_adj = 0
        if orderbook_result.get("signal") == "BULLISH":
            orderbook_adj = 2
        elif orderbook_result.get("signal") == "BEARISH":
            orderbook_adj = -2
        overall_net += orderbook_adj

        # Piyasa verileri ekstra puan (fonlama + long/short ratio)
        overall_net += market_data_score

        # Momentum ivme skoru
        overall_net += momentum_accel["score_adj"]

        confluence_adj = 0
        confluence_bonus = ""
        if all_bull and not tf_conflict:
            confluence_adj = 8
            overall_net += confluence_adj
            confluence_bonus = f" ✅ Tüm zaman dilimleri boğa yönünde uyumlu → güçlü sinyal."
        elif all_bear and not tf_conflict:
            confluence_adj = -8
            overall_net += confluence_adj
            confluence_bonus = f" ✅ Tüm zaman dilimleri ayı yönünde uyumlu → güçlü sinyal."
        elif tf_conflict or tf_conflict_4h_1h:
            # TF çelişkisi: skoru sıfıra çek — net yön yok
            pre_conflict = overall_net
            # 4H dominant, ama çelişki varken kesin yön vermek YANLIŞ
            overall_net = round(overall_net * 0.3, 1)  # %70 ceza (eskiden %40'tı)
            confluence_adj = round(overall_net - pre_conflict, 1)
            # Çelişkiyi net açıkla
            if v_4h in bull_set and (v_15m in bear_set or v_1h in bear_set):
                conflict_side = "1H" if v_1h in bear_set else "15m"
                confluence_bonus = f" ⚠️ ÇATIŞMA: 4H yükseliş yönünde ama {conflict_side} düşüş sinyali veriyor. Bu durumda pozisyon ALMAYIN — 4H kapanışında TF'lerin uyumunu bekleyin."
            elif v_4h in bear_set and (v_15m in bull_set or v_1h in bull_set):
                conflict_side = "1H" if v_1h in bull_set else "15m"
                confluence_bonus = f" ⚠️ ÇATIŞMA: 4H düşüş yönünde ama {conflict_side} yükseliş sinyali veriyor. Bu kısa vadeli tepki olabilir — ana trend (4H) hâlâ ayı, dikkat."
            else:
                confluence_bonus = f" ⚠️ ÇATIŞMA: Zaman dilimleri zıt sinyal veriyor — net yön yok, bekleyin."

        # Momentum ivme bilgisini açıklamaya ekle
        mom_note = ""
        if momentum_accel["status"] != "NEUTRAL":
            mom_note = f" 📈 İvme: {momentum_accel['detail']}"

        # 4H piyasa yapısı + gerçek rejim bilgisi
        adx_4h = tf_results["4H"].get("adx", {})
        adx_4h_val = adx_4h.get("adx")
        regime_note = ""
        if adx_4h_val is not None:
            if adx_4h_val >= 25:
                regime_note = " [Trend piyasası]"
            elif adx_4h_val < 20:
                regime_note = " [Yatay piyasa]"

        # Gerçek makro rejim bilgisini ekle
        cached_regime = market_regime.get_cached_regime()
        macro_regime_info = {}
        if cached_regime:
            macro_regime_info = {
                "regime": cached_regime["regime"],
                "regime_label": market_regime._regime_label(cached_regime["regime"]),
                "btc_bias": cached_regime["btc_bias"],
                "volatility": cached_regime["regime_details"].get("volatility", {}).get("state", "NORMAL"),
            }
            # Bu coinin RS skorunu bul
            all_rs = cached_regime.get("rs_rankings", [])
            rs_lookup = {r["symbol"]: r for r in all_rs}
            coin_rs = rs_lookup.get(symbol)
            if coin_rs:
                macro_regime_info["rs_score"] = coin_rs["rs_score"]
                macro_regime_info["rs_rank"] = list(rs_lookup.keys()).index(symbol) + 1
                macro_regime_info["rs_total"] = len(rs_lookup)
            elif symbol == "BTC-USDT-SWAP":
                # BTC referans coin, kendisiyle RS hesaplanmaz
                macro_regime_info["rs_score"] = 0
                macro_regime_info["rs_rank"] = "-"
                macro_regime_info["rs_total"] = len(rs_lookup)
            else:
                macro_regime_info["rs_score"] = None
                macro_regime_info["rs_rank"] = None
                macro_regime_info["rs_total"] = len(all_rs)
        else:
            macro_regime_info = {"regime": "UNKNOWN", "regime_label": "Veri Bekleniyor", "btc_bias": "UNKNOWN"}

        # Overall verdict — eşikler yükseltildi (false signal azaltmak için)
        # TF çelişkisi varken KESİNLİKLE yön verilmez
        if tf_conflict or tf_conflict_4h_1h:
            # Çelişki varsa → zorla NÖTR/BEKLE
            overall = "NEUTRAL"
            overall_label = "BEKLE ⏳"
            overall_emoji = "⚠️"
            overall_desc = (
                f"⚠️ ZAMAN DİLİMLERİ ÇATIŞIYOR — Pozisyon almayın!\n"
                f"📊 {tf_breakdown}\n"
                f"{confluence_bonus.strip()}\n"
                f"Ana trend (4H) {'yükseliş' if v_4h in bull_set else ('düşüş' if v_4h in bear_set else 'nötr')} yönünde, "
                f"ancak alt TF'ler zıt sinyal veriyor. TF'ler uyumlanana kadar bekleyin."
            )
            if mom_note:
                overall_desc += f"\n{mom_note.strip()}"
        elif overall_net >= 30:
            overall = "STRONG_BULLISH"
            overall_label = "GÜÇLÜ BOĞA"
            overall_emoji = "🟢🟢"
            overall_desc = (
                f"Güçlü yükseliş sinyali (skor: +{overall_net}).{regime_note}\n"
                f"📊 {tf_breakdown}\n"
                f"{confluence_bonus.strip()}"
                f"{mom_note}\n"
                f"{'Tüm TF' if all_bull else 'Ana'} göstergeler LONG yönünde — geri çekilmelerde değerlendirilebilir. SL kullanmayı unutmayın."
            )
        elif overall_net >= 15:
            overall = "BULLISH"
            overall_label = "BOĞA"
            overall_emoji = "🟢"
            overall_desc = (
                f"Yükseliş ağırlıklı (skor: +{overall_net}).{regime_note}\n"
                f"📊 {tf_breakdown}\n"
                f"{confluence_bonus.strip()}"
                f"{mom_note}\n"
                f"LONG yönünde eğilim var. 4H trend onayını kontrol edin."
            )
        elif overall_net >= 6:
            overall = "LEANING_BULLISH"
            overall_label = "HAFİF BOĞA"
            overall_emoji = "🟡"
            overall_desc = (
                f"Hafif yükseliş eğilimi (skor: +{overall_net}).{regime_note}\n"
                f"📊 {tf_breakdown}\n"
                f"{mom_note}\n"
                f"Sinyal zayıf — pozisyon almak için yetersiz. 4H kapanış ve hacim onayı bekleyin."
            )
        elif overall_net <= -30:
            overall = "STRONG_BEARISH"
            overall_label = "GÜÇLÜ AYI"
            overall_emoji = "🔴🔴"
            overall_desc = (
                f"Güçlü düşüş sinyali (skor: {overall_net}).{regime_note}\n"
                f"📊 {tf_breakdown}\n"
                f"{confluence_bonus.strip()}"
                f"{mom_note}\n"
                f"{'Tüm TF' if all_bear else 'Ana'} göstergeler SHORT yönünde — yükselişlerde değerlendirilebilir. SL kullanın."
            )
        elif overall_net <= -15:
            overall = "BEARISH"
            overall_label = "AYI"
            overall_emoji = "🔴"
            overall_desc = (
                f"Düşüş ağırlıklı (skor: {overall_net}).{regime_note}\n"
                f"📊 {tf_breakdown}\n"
                f"{confluence_bonus.strip()}"
                f"{mom_note}\n"
                f"SHORT yönünde eğilim var. LONG pozisyonlardan kaçının."
            )
        elif overall_net <= -6:
            overall = "LEANING_BEARISH"
            overall_label = "HAFİF AYI"
            overall_emoji = "🟠"
            overall_desc = (
                f"Hafif düşüş eğilimi (skor: {overall_net}).{regime_note}\n"
                f"📊 {tf_breakdown}\n"
                f"{mom_note}\n"
                f"Sinyal zayıf — kesin yön için 4H trend ve hacim onayı bekleyin."
            )
        else:
            overall = "NEUTRAL"
            overall_label = "NÖTR — BEKLE"
            overall_emoji = "⚪"
            overall_desc = (
                f"Net yön yok (skor: {overall_net}).{regime_note}\n"
                f"📊 {tf_breakdown}\n"
                f"{mom_note}\n"
                f"Göstergeler karışık — pozisyon almak yerine izlemeye alın."
            )

        # Ek uyarılar
        warnings = []
        atr_4h = tf_results["4H"].get("atr", {})
        if atr_4h.get("signal") == "HIGH":
            warnings.append("⚠ Yüksek volatilite — pozisyon boyutunu küçültün, geniş SL kullanın.")
        if tf_conflict:
            warnings.append(f"🚨 4H ve 15m ÇATIŞMA → 4H: {tf_summary_4h}, 15m: {tf_summary_15m}. Pozisyon almayın!")
        if tf_conflict_4h_1h and not tf_conflict:
            warnings.append(f"⚠ 4H ve 1H ÇATIŞMA → 4H: {tf_summary_4h}, 1H: {tf_summary_1h}. Dikkatli olun.")
        if any(tf_results[k].get("divergence", {}).get("type") in ("BULLISH", "BEARISH") for k in ["1H", "4H"]):
            div_tfs = [k for k in ["1H", "4H"] if tf_results[k].get("divergence", {}).get("type") in ("BULLISH", "BEARISH")]
            div_types = [tf_results[k]["divergence"]["type"] for k in div_tfs]
            warnings.append(f"⚠ {', '.join(div_tfs)}'de {'/'.join(div_types)} diverjansı — mevcut trend zayıflıyor olabilir!")
        if orderbook_result.get("bid_walls", 0) >= 2:
            warnings.append("🛡 Güçlü alım duvarları — aşağı yönlü destek güçlü.")
        if orderbook_result.get("ask_walls", 0) >= 2:
            warnings.append("🧱 Güçlü satış duvarları — yukarı yönlü direnç var.")
        
        # Düşük güven uyarısı
        if abs(overall_net) < 10:
            warnings.append("ℹ Skor düşük — yüksek güvenli sinyal için daha fazla gösterge uyumu gerekli.")

        # Piyasa verileri uyarıları
        if market_data.get("funding") and market_data["funding"].get("signal") == "BEARISH":
            warnings.append(f"💰 Fonlama oranı yüksek ({market_data['funding']['current']:.4f}%) — aşırı long kalabalık, düşüş riski.")
        elif market_data.get("funding") and market_data["funding"].get("signal") == "BULLISH":
            warnings.append(f"💰 Fonlama oranı negatif ({market_data['funding']['current']:.4f}%) — aşırı short kalabalık, short squeeze riski.")
        
        lsr_1d = (market_data.get("long_short_ratio") or {}).get("1D")
        if lsr_1d and lsr_1d.get("signal") == "BEARISH":
            warnings.append(f"📊 L/S Ratio aşırı long ({lsr_1d['ratio']:.2f}) — tasfiye riski yüksek.")
        elif lsr_1d and lsr_1d.get("signal") == "BULLISH":
            warnings.append(f"📊 L/S Ratio aşırı short ({lsr_1d['ratio']:.2f}) — short squeeze olasılığı.")

        # Momentum ivme uyarıları
        if momentum_accel["status"] in ("BULL_FADING", "BEAR_FADING"):
            warnings.append(f"📉 {momentum_accel['detail']}")
        elif momentum_accel["status"] in ("BULL_REVERSAL_RISK", "BEAR_REVERSAL_RISK"):
            warnings.append(f"🔄 {momentum_accel['detail']}")
        elif momentum_accel["status"] in ("BULL_ACCELERATING", "BEAR_ACCELERATING"):
            warnings.append(f"🚀 {momentum_accel['detail']}")

        response = {
            "symbol": symbol,
            "price": price_info,
            "timeframes": tf_results,
            "orderbook": orderbook_result,
            "market_data": market_data,
            "overall": {
                "verdict": overall,
                "label": f"{overall_emoji} {overall_label}",
                "description": overall_desc,
                "net_score": overall_net,
                "bull_total": round(total_bull, 1),
                "bear_total": round(total_bear, 1),
                "confidence": overall_confidence,
                "direction": "NONE" if (tf_conflict or tf_conflict_4h_1h) else ("LONG" if overall_net >= 15 else ("SHORT" if overall_net <= -15 else "NONE")),
                "verdict_color": "gray" if (tf_conflict or tf_conflict_4h_1h) else ("green" if overall_net >= 15 else ("red" if overall_net <= -15 else ("orange" if abs(overall_net) >= 6 else "gray"))),
                "warnings": warnings,
                "tf_breakdown": tf_breakdown,
                "tf_conflict": tf_conflict or tf_conflict_4h_1h,
                "tf_confluence": "ALL_BULL" if all_bull else ("ALL_BEAR" if all_bear else "MIXED"),
                "momentum": momentum_accel["status"],
                "momentum_detail": momentum_accel["detail"] if momentum_accel["status"] != "NEUTRAL" else None,
                "adjustments": {
                    "orderbook": orderbook_adj,
                    "market_data": market_data_score,
                    "momentum": momentum_accel["score_adj"],
                    "confluence": confluence_adj
                },
                "market_regime": regime_note.strip(" []") if regime_note else "Normal",
                "macro_regime": macro_regime_info
            },
            "timestamp": datetime.now().isoformat()
        }

        def serialize(obj):
            if hasattr(obj, "isoformat"):
                return obj.isoformat()
            elif hasattr(obj, "item"):
                return obj.item()
            return str(obj)

        return jsonify(json.loads(json.dumps(response, default=serialize)))
    except Exception as e:
        logger.error(f"Coin detay hatası ({symbol}): {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/ticker/<symbol>")
def api_ticker(symbol):
    """Anlık fiyat"""
    ticker = data_fetcher.get_ticker(symbol)
    if ticker:
        return jsonify(ticker)
    return jsonify({"error": "Fiyat alınamadı"}), 400

@app.route("/api/params")
def api_params():
    """Güncel bot parametreleri"""
    params = {}
    for key, default_val in ICT_PARAMS.items():
        current = get_bot_param(key)
        params[key] = {
            "current": current if current is not None else default_val,
            "default": default_val
        }
    return jsonify(params)


@app.route("/api/coins")
def api_coins():
    """OKX'ten yüksek hacimli aktif coin listesi (cache: 5dk)"""
    coins = data_fetcher.get_high_volume_coins()
    volumes = data_fetcher.get_all_coin_volumes()
    result = []
    for symbol in coins:
        info = volumes.get(symbol, {})
        result.append({
            "symbol": symbol,
            "volume_usdt": info.get("volume_usdt", 0),
            "last_price": info.get("last_price", 0),
            "change_pct": info.get("change_pct", 0)
        })
    return jsonify({
        "min_volume": MIN_VOLUME_USDT,
        "total_coins": len(result),
        "coins": result
    })



# =================== REGIME API ===================

@app.route("/api/regime")
def api_regime():
    """Piyasa rejimi detayları — BTC trend, BTC.D, USDT flow, RS sıralaması"""
    summary = market_regime.get_regime_summary()
    return jsonify(summary)


@app.route("/api/regime/refresh", methods=["POST"])
def api_regime_refresh():
    """Manuel rejim analizi tetikle (bot çalışmasa da) — cache bypass"""
    try:
        active_coins = data_fetcher.get_high_volume_coins()
        if not active_coins:
            return jsonify({"error": "Coin listesi alınamadı"}), 400
        # Cache'i sıfırla ki yeni analiz yapılsın
        market_regime._regime_ts = 0
        regime_result = market_regime.analyze_market(active_coins)
        bot_state["current_regime"] = regime_result["regime"]
        bot_state["btc_bias"] = regime_result["btc_bias"]
        bot_state["long_candidates"] = len(regime_result["long_candidates"])
        bot_state["short_candidates"] = len(regime_result["short_candidates"])
        socketio.emit("regime_update", market_regime.get_regime_summary())
        return jsonify(market_regime.get_regime_summary())
    except Exception as e:
        logger.error(f"Manuel rejim analizi hatası: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/regime/rankings")
def api_regime_rankings():
    """Tüm coinlerin rölatif güç sıralaması"""
    cached = market_regime.get_cached_regime()
    if not cached:
        return jsonify([])
    return jsonify(cached.get("rs_rankings", []))


# =================== FOREX / EMTİA ICT API ===================

@app.route("/api/forex/instruments")
def api_forex_instruments():
    """Desteklenen forex/emtia enstrümanları"""
    instruments = []
    for key, inst in FOREX_INSTRUMENTS.items():
        instruments.append({
            "key": key,
            "name": inst["name"],
            "category": inst["category"],
            "icon": inst["icon"],
            "desc": inst["desc"],
        })
    return jsonify(instruments)


@app.route("/api/forex/scan")
def api_forex_scan():
    """Tüm forex enstrümanlarını ICT ile tara"""
    tf = request.args.get("tf", "1h")
    try:
        results = forex_ict.scan_all(timeframe=tf)
        return jsonify({"results": results, "timeframe": tf, "count": len(results)})
    except Exception as e:
        logger.error(f"Forex tarama hatası: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/forex/signal/<instrument>")
def api_forex_signal(instrument):
    """Tek enstrüman ICT sinyal analizi"""
    tf = request.args.get("tf", "1h")
    instrument = instrument.upper()
    if instrument not in FOREX_INSTRUMENTS:
        return jsonify({"error": f"Bilinmeyen enstrüman: {instrument}"}), 400
    try:
        result = forex_ict.generate_signal(instrument, timeframe=tf)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Forex sinyal hatası ({instrument}): {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/forex/kill-zones")
def api_forex_kill_zones():
    """Aktif Kill Zone bilgisi"""
    return jsonify(forex_ict.detect_kill_zones())


# =================== WEBSOCKET ===================

@socketio.on("connect")
def handle_connect():
    logger.info("WebSocket client bağlandı")
    socketio.emit("bot_status", {"running": bot_state["running"]})


@socketio.on("disconnect")
def handle_disconnect():
    logger.info("WebSocket client ayrıldı")


@socketio.on("request_update")
def handle_request_update():
    """Frontend'den anlık güncelleme isteği"""
    stats = get_performance_summary()
    signals = get_active_signals()
    watching = get_watching_items()

    socketio.emit("full_update", {
        "stats": stats,
        "active_signals": signals,
        "watching": watching,
        "bot_state": bot_state,
        "timestamp": datetime.now().isoformat()
    })


# =================== BAŞLATMA ===================

import os

# Render/Gunicorn ile çalışırken de DB'yi başlat
init_db()
logger.info("ICT Trading Bot v1.0 - Veritabanı hazır")

# =================== SELF-PING (Render uyku engelleme) ===================

def self_ping():
    """Render free tier'da uyumayı engelle — her 10 dakikada bir health endpoint'i çağır"""
    try:
        render_url = os.environ.get("RENDER_EXTERNAL_URL")
        if render_url:
            import requests
            resp = requests.get(f"{render_url}/api/health", timeout=10)
            logger.debug(f"Self-ping OK: {resp.status_code}")
    except Exception as e:
        logger.debug(f"Self-ping hata (önemsiz): {e}")


# Render'da otomatik başlat (gunicorn ile)
if os.environ.get("RENDER"):
    # Render ortamında botu otomatik başlat
    import atexit
    def auto_start_bot():
        """Gunicorn worker başladığında botu otomatik başlat"""
        if not bot_state["running"]:
            bot_state["running"] = True
            create_scheduler()
            scheduler.add_job(scan_markets, "interval", seconds=SCAN_INTERVAL_SECONDS,
                             id="scan_markets", replace_existing=True)
            scheduler.add_job(check_trades, "interval", seconds=TRADE_CHECK_INTERVAL,
                             id="check_trades", replace_existing=True)
            scheduler.add_job(run_optimizer, "interval",
                             minutes=OPTIMIZER_CONFIG["optimization_interval_minutes"],
                             id="run_optimizer", replace_existing=True)
            # Self-ping: her 10 dakikada Render'ı uyanık tut
            scheduler.add_job(self_ping, "interval", minutes=10,
                             id="self_ping", replace_existing=True)
            scheduler.start()
            threading.Thread(target=scan_markets, daemon=True).start()
            logger.info("🚀 Bot Render'da otomatik başlatıldı! (Self-ping aktif)")

    # İlk request'te değil, uygulama başlarken çalıştır
    auto_start_timer = threading.Timer(5.0, auto_start_bot)
    auto_start_timer.daemon = True
    auto_start_timer.start()


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("  ICT Trading Bot v1.0 - GERÇEK VERİ")
    logger.info("  OKX Public API | Dinamik Coin Filtresi")
    logger.info("=" * 60)
    logger.info(f"  Min 24h Hacim: ${MIN_VOLUME_USDT:,.0f} USDT")
    logger.info(f"  Tarama aralığı: {SCAN_INTERVAL_SECONDS}s")
    logger.info(f"  Web arayüz: http://localhost:{PORT}")
    logger.info("=" * 60)

    coins = data_fetcher.get_high_volume_coins(force_refresh=True)
    logger.info(f"  Başlangıç: {len(coins)} coin (min ${MIN_VOLUME_USDT:,.0f} hacim) tespit edildi")
    logger.info("=" * 60)

    socketio.run(app, host=HOST, port=PORT, debug=DEBUG)
