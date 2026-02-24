# =====================================================
# ICT Trading Bot - Veritabanı İşlemleri
# SQLite (yerel geliştirme) & PostgreSQL (Render/üretim)
# =====================================================
# DATABASE_URL ortam değişkeni ayarlıysa PostgreSQL kullanır,
# yoksa yerel SQLite dosyasına yazar.
# =====================================================

import os
import json
import threading
from datetime import datetime, date

# =================== BACKEND SEÇİMİ ===================

DATABASE_URL = os.environ.get("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    # Render bazen postgres:// verir, psycopg2 postgresql:// ister
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    import psycopg2
    import psycopg2.extras
else:
    import sqlite3
    from config import DB_PATH

_local = threading.local()


# =================== BAĞLANTI YÖNETİMİ ===================

def _create_connection():
    """Yeni veritabanı bağlantısı oluştur"""
    if USE_POSTGRES:
        kwargs = {}
        if "sslmode" not in DATABASE_URL:
            kwargs["sslmode"] = "require"
        conn = psycopg2.connect(DATABASE_URL, **kwargs)
        conn.autocommit = True
        return conn
    else:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn


def get_db():
    """Thread-safe veritabanı bağlantısı"""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = _create_connection()

    # PostgreSQL bağlantı canlılık kontrolü
    if USE_POSTGRES:
        try:
            with _local.conn.cursor() as cur:
                cur.execute("SELECT 1")
        except Exception:
            try:
                _local.conn.close()
            except Exception:
                pass
            _local.conn = _create_connection()

    return _local.conn


# =================== SORGU YARDIMCILARI ===================

def _q(sql):
    """SQLite ? → PostgreSQL %s parametre dönüşümü"""
    if USE_POSTGRES:
        return sql.replace("?", "%s")
    return sql


def _execute(sql, params=None):
    """SQL çalıştır ve commit yap"""
    conn = get_db()
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute(_q(sql), params or ())
    else:
        conn.execute(sql, params or ())
        conn.commit()


def _execute_returning_id(sql, params=None):
    """INSERT çalıştır, yeni satır ID'sini döndür"""
    conn = get_db()
    if USE_POSTGRES:
        with conn.cursor() as cur:
            cur.execute(_q(sql) + " RETURNING id", params or ())
            row = cur.fetchone()
            return row[0]
    else:
        cursor = conn.execute(sql, params or ())
        conn.commit()
        return cursor.lastrowid


def _fetchall(sql, params=None):
    """Tüm satırları dict listesi olarak döndür"""
    conn = get_db()
    if USE_POSTGRES:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_q(sql), params or ())
            rows = cur.fetchall()
            return [_serialize_row(dict(r)) for r in rows]
    else:
        rows = conn.execute(sql, params or ()).fetchall()
        return [dict(row) for row in rows]


def _fetchone(sql, params=None):
    """Tek satır döndür (dict veya None)"""
    conn = get_db()
    if USE_POSTGRES:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_q(sql), params or ())
            row = cur.fetchone()
            return _serialize_row(dict(row)) if row else None
    else:
        row = conn.execute(sql, params or ()).fetchone()
        return dict(row) if row else None


def _serialize_row(row):
    """datetime/date nesnelerini ISO string'e çevir (JSON uyumluluğu)"""
    if row is None:
        return None
    for key, val in row.items():
        if isinstance(val, (datetime, date)):
            row[key] = val.isoformat()
    return row


def _get_existing_columns(table_name):
    """Tablodaki mevcut sütun isimlerini getir"""
    if USE_POSTGRES:
        rows = _fetchall(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table_name,)
        )
        return {r["column_name"] for r in rows}
    else:
        conn = get_db()
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {row["name"] for row in rows}


# =================== SQL TİP UYUMLULUĞU ===================

_AUTO_ID = "SERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
_TS_DEFAULT = "TIMESTAMPTZ DEFAULT NOW()" if USE_POSTGRES else "TEXT DEFAULT CURRENT_TIMESTAMP"
_FLOAT = "DOUBLE PRECISION" if USE_POSTGRES else "REAL"


# =================== TABLO OLUŞTURMA ===================

def init_db():
    """Veritabanı tablolarını oluştur"""

    # Sinyaller tablosu
    _execute(f"""
        CREATE TABLE IF NOT EXISTS signals (
            id {_AUTO_ID},
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_price {_FLOAT} NOT NULL,
            stop_loss {_FLOAT} NOT NULL,
            take_profit {_FLOAT} NOT NULL,
            confidence {_FLOAT} NOT NULL,
            confluence_score {_FLOAT} NOT NULL,
            status TEXT DEFAULT 'WAITING',
            components TEXT,
            timeframe TEXT,
            entry_time TEXT,
            close_time TEXT,
            close_price {_FLOAT},
            pnl_pct {_FLOAT},
            notes TEXT,
            entry_mode TEXT DEFAULT 'MARKET',
            htf_bias TEXT,
            rr_ratio {_FLOAT},
            created_at {_TS_DEFAULT},
            updated_at {_TS_DEFAULT}
        )
    """)

    # İzleme listesi (sabırlı mod)
    _execute(f"""
        CREATE TABLE IF NOT EXISTS watchlist (
            id {_AUTO_ID},
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            potential_entry {_FLOAT},
            potential_sl {_FLOAT},
            potential_tp {_FLOAT},
            watch_reason TEXT,
            candles_watched INTEGER DEFAULT 0,
            confirmation_count INTEGER DEFAULT 0,
            last_5m_candle_ts TEXT,
            max_watch_candles INTEGER DEFAULT 24,
            initial_score {_FLOAT},
            current_score {_FLOAT},
            status TEXT DEFAULT 'WATCHING',
            components TEXT,
            expire_reason TEXT,
            created_at {_TS_DEFAULT},
            updated_at {_TS_DEFAULT}
        )
    """)

    # Sütun göçleri (eski veritabanları için)
    _migrate_columns()

    # Optimizasyon logları
    _execute(f"""
        CREATE TABLE IF NOT EXISTS optimization_logs (
            id {_AUTO_ID},
            param_name TEXT NOT NULL,
            old_value {_FLOAT} NOT NULL,
            new_value {_FLOAT} NOT NULL,
            reason TEXT,
            win_rate_before {_FLOAT},
            win_rate_after {_FLOAT},
            total_trades_analyzed INTEGER,
            created_at {_TS_DEFAULT}
        )
    """)

    # Bot parametreleri (güncel değerler)
    _execute(f"""
        CREATE TABLE IF NOT EXISTS bot_params (
            param_name TEXT PRIMARY KEY,
            param_value {_FLOAT} NOT NULL,
            default_value {_FLOAT} NOT NULL,
            last_updated {_TS_DEFAULT}
        )
    """)

    # Performans istatistikleri
    _execute(f"""
        CREATE TABLE IF NOT EXISTS performance_stats (
            id {_AUTO_ID},
            date TEXT NOT NULL,
            total_signals INTEGER DEFAULT 0,
            winning_trades INTEGER DEFAULT 0,
            losing_trades INTEGER DEFAULT 0,
            total_pnl_pct {_FLOAT} DEFAULT 0,
            avg_confidence {_FLOAT} DEFAULT 0,
            avg_confluence {_FLOAT} DEFAULT 0,
            best_component TEXT,
            worst_component TEXT,
            created_at {_TS_DEFAULT}
        )
    """)

    # =================== AME TABLOLARI ===================

    # AME Sinyalleri (ICT'den tamamen bağımsız)
    _execute(f"""
        CREATE TABLE IF NOT EXISTS ame_signals (
            id {_AUTO_ID},
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_price {_FLOAT} NOT NULL,
            stop_loss {_FLOAT} NOT NULL,
            take_profit {_FLOAT} NOT NULL,
            score {_FLOAT} NOT NULL,
            rr_ratio {_FLOAT},
            regime TEXT,
            mode TEXT DEFAULT 'balanced',
            impulse_score {_FLOAT},
            velocity {_FLOAT},
            status TEXT DEFAULT 'ACTIVE',
            entry_time TEXT,
            close_time TEXT,
            close_price {_FLOAT},
            pnl_pct {_FLOAT},
            notes TEXT,
            created_at {_TS_DEFAULT},
            updated_at {_TS_DEFAULT}
        )
    """)


def _migrate_columns():
    """Eski tablolara eksik sütunları ekle"""
    # watchlist göçleri
    watch_cols = _get_existing_columns("watchlist")
    if "confirmation_count" not in watch_cols:
        _execute("ALTER TABLE watchlist ADD COLUMN confirmation_count INTEGER DEFAULT 0")
    if "last_5m_candle_ts" not in watch_cols:
        _execute("ALTER TABLE watchlist ADD COLUMN last_5m_candle_ts TEXT")
    if "expire_reason" not in watch_cols:
        _execute("ALTER TABLE watchlist ADD COLUMN expire_reason TEXT")

    # signals göçleri
    signal_cols = _get_existing_columns("signals")
    if "entry_mode" not in signal_cols:
        _execute("ALTER TABLE signals ADD COLUMN entry_mode TEXT DEFAULT 'MARKET'")
    if "htf_bias" not in signal_cols:
        _execute("ALTER TABLE signals ADD COLUMN htf_bias TEXT")
    if "rr_ratio" not in signal_cols:
        _execute(f"ALTER TABLE signals ADD COLUMN rr_ratio {_FLOAT}")


# =================== SİNYAL İŞLEMLERİ ===================

def add_signal(symbol, direction, entry_price, stop_loss, take_profit,
               confidence, confluence_score, components, timeframe, status="WAITING",
               notes="", entry_mode="MARKET", htf_bias=None, rr_ratio=None):
    """Yeni sinyal kaydet — tüm ICT meta verileri dahil."""
    return _execute_returning_id("""
        INSERT INTO signals (symbol, direction, entry_price, stop_loss, take_profit,
                           confidence, confluence_score, components, timeframe, status,
                           notes, entry_mode, htf_bias, rr_ratio)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (symbol, direction, entry_price, stop_loss, take_profit,
          confidence, confluence_score, json.dumps(components), timeframe, status,
          notes, entry_mode, htf_bias, rr_ratio))


def update_signal_status(signal_id, status, close_price=None, pnl_pct=None):
    now = datetime.now().isoformat()
    if close_price is not None:
        _execute("""
            UPDATE signals SET status=?, close_price=?, pnl_pct=?, close_time=?, updated_at=?
            WHERE id=?
        """, (status, close_price, pnl_pct, now, now, signal_id))
    else:
        _execute("""
            UPDATE signals SET status=?, updated_at=? WHERE id=?
        """, (status, now, signal_id))


def activate_signal(signal_id):
    now = datetime.now().isoformat()
    _execute("""
        UPDATE signals SET status='ACTIVE', entry_time=?, updated_at=? WHERE id=?
    """, (now, now, signal_id))


def update_signal_sl(signal_id, new_sl):
    """Breakeven/Trailing SL güncellemesini DB'ye yaz (restart koruması)."""
    now = datetime.now().isoformat()
    _execute("""
        UPDATE signals SET stop_loss=?, updated_at=? WHERE id=?
    """, (new_sl, now, signal_id))


def get_active_signals():
    return _fetchall("""
        SELECT * FROM signals WHERE status IN ('ACTIVE', 'WAITING') ORDER BY created_at DESC
    """)


def get_signal_history(limit=50):
    return _fetchall("""
        SELECT * FROM signals WHERE status IN ('WON', 'LOST', 'CANCELLED')
        ORDER BY close_time DESC, created_at DESC LIMIT ?
    """, (limit,))


def get_completed_signals(limit=100):
    return _fetchall("""
        SELECT * FROM signals WHERE status IN ('WON', 'LOST') ORDER BY close_time DESC LIMIT ?
    """, (limit,))


def get_active_trade_count():
    row = _fetchone("SELECT COUNT(*) as cnt FROM signals WHERE status = 'ACTIVE'")
    return row["cnt"] if row else 0


# =================== İZLEME LİSTESİ ===================

def add_to_watchlist(symbol, direction, potential_entry, potential_sl, potential_tp,
                     watch_reason, initial_score, components, max_watch=24):
    # Aynı sembol ve yönde zaten izleniyor mu?
    existing = _fetchone("""
        SELECT id FROM watchlist WHERE symbol=? AND direction=? AND status='WATCHING'
    """, (symbol, direction))
    if existing:
        return existing["id"]

    # Son 15 dk içinde expire edilmişse tekrar ekleme (flip-flop engeli)
    # 60dk → 15dk: Kripto hızlı hareket eder, uzun cooldown fırsatları kaçırır
    if USE_POSTGRES:
        recent = _fetchone("""
            SELECT id FROM watchlist
            WHERE symbol=? AND direction=? AND status='EXPIRED'
              AND updated_at > NOW() - INTERVAL '15 minutes'
        """, (symbol, direction))
    else:
        recent = _fetchone("""
            SELECT id FROM watchlist
            WHERE symbol=? AND direction=? AND status='EXPIRED'
              AND updated_at > datetime('now', '-15 minutes')
        """, (symbol, direction))

    if recent:
        return None

    return _execute_returning_id("""
        INSERT INTO watchlist (symbol, direction, potential_entry, potential_sl, potential_tp,
                         watch_reason, candles_watched, confirmation_count, last_5m_candle_ts,
                         initial_score, current_score, components, max_watch_candles)
          VALUES (?, ?, ?, ?, ?, ?, 0, 0, NULL, ?, ?, ?, ?)
    """, (symbol, direction, potential_entry, potential_sl, potential_tp,
            watch_reason, initial_score, initial_score, json.dumps(components), max_watch))


def update_watchlist_item(item_id, candles_watched, current_score,
                         confirmation_count=None, last_5m_candle_ts=None, status="WATCHING"):
    now = datetime.now().isoformat()
    if confirmation_count is None and last_5m_candle_ts is None:
        _execute("""
            UPDATE watchlist SET candles_watched=?, current_score=?, status=?, updated_at=?
            WHERE id=?
        """, (candles_watched, current_score, status, now, item_id))
    else:
        _execute("""
            UPDATE watchlist
            SET candles_watched=?, current_score=?, confirmation_count=?,
                last_5m_candle_ts=?, status=?, updated_at=?
            WHERE id=?
        """, (candles_watched, current_score,
              confirmation_count if confirmation_count is not None else 0,
              last_5m_candle_ts, status, now, item_id))


def get_watching_items():
    return _fetchall("""
        SELECT * FROM watchlist WHERE status='WATCHING' ORDER BY current_score DESC
    """)


def promote_watchlist_item(item_id):
    now = datetime.now().isoformat()
    _execute("""
        UPDATE watchlist SET status='PROMOTED', updated_at=? WHERE id=?
    """, (now, item_id))


def expire_watchlist_item(item_id, reason=None):
    now = datetime.now().isoformat()
    _execute("""
        UPDATE watchlist SET status='EXPIRED', expire_reason=?, updated_at=? WHERE id=?
    """, (reason, now, item_id))


def get_recently_expired(minutes=30):
    """Son N dakikada expire edilen watchlist öğeleri"""
    if USE_POSTGRES:
        return _fetchall("""
            SELECT * FROM watchlist
            WHERE status='EXPIRED'
              AND updated_at > NOW() + CAST(? || ' minutes' AS INTERVAL)
            ORDER BY updated_at DESC LIMIT 20
        """, (str(-minutes),))
    else:
        return _fetchall("""
            SELECT * FROM watchlist
            WHERE status='EXPIRED'
              AND updated_at > datetime('now', ? || ' minutes')
            ORDER BY updated_at DESC LIMIT 20
        """, (str(-minutes),))


# =================== OPTİMİZASYON ===================

def add_optimization_log(param_name, old_value, new_value, reason,
                         win_rate_before, win_rate_after, total_trades):
    _execute("""
        INSERT INTO optimization_logs (param_name, old_value, new_value, reason,
                                      win_rate_before, win_rate_after, total_trades_analyzed)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (param_name, old_value, new_value, reason, win_rate_before, win_rate_after, total_trades))


def get_optimization_logs(limit=30):
    return _fetchall("""
        SELECT * FROM optimization_logs ORDER BY created_at DESC LIMIT ?
    """, (limit,))


# =================== BOT PARAMETRELERİ ===================

def save_bot_param(param_name, param_value, default_value=None):
    now = datetime.now().isoformat()
    if default_value is None:
        default_value = param_value
    if USE_POSTGRES:
        _execute("""
            INSERT INTO bot_params (param_name, param_value, default_value, last_updated)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (param_name) DO UPDATE SET
                param_value = EXCLUDED.param_value,
                last_updated = EXCLUDED.last_updated
        """, (param_name, param_value, default_value, now))
    else:
        _execute("""
            INSERT OR REPLACE INTO bot_params (param_name, param_value, default_value, last_updated)
            VALUES (?, ?, ?, ?)
        """, (param_name, param_value, default_value, now))


def get_bot_param(param_name, default=None):
    row = _fetchone("""
        SELECT param_value FROM bot_params WHERE param_name=?
    """, (param_name,))
    return row["param_value"] if row else default


def get_all_bot_params():
    rows = _fetchall("SELECT * FROM bot_params")
    return {row["param_name"]: row["param_value"] for row in rows}


# =================== İSTATİSTİKLER ===================

def get_performance_summary():
    stats = {}

    # Toplam işlem
    row = _fetchone("SELECT COUNT(*) as cnt FROM signals WHERE status IN ('WON','LOST')")
    stats["total_trades"] = row["cnt"] if row else 0

    # Kazananlar
    row = _fetchone("SELECT COUNT(*) as cnt FROM signals WHERE status='WON'")
    stats["winning_trades"] = row["cnt"] if row else 0

    # Kaybedenler
    row = _fetchone("SELECT COUNT(*) as cnt FROM signals WHERE status='LOST'")
    stats["losing_trades"] = row["cnt"] if row else 0

    # Win rate
    if stats["total_trades"] > 0:
        stats["win_rate"] = round(stats["winning_trades"] / stats["total_trades"] * 100, 1)
    else:
        stats["win_rate"] = 0

    # Toplam PnL
    row = _fetchone("SELECT COALESCE(SUM(pnl_pct), 0) as total FROM signals WHERE status IN ('WON','LOST')")
    stats["total_pnl"] = round(row["total"], 2) if row else 0

    # Ortalama güven skoru
    row = _fetchone("SELECT AVG(confidence) as avg_conf FROM signals WHERE status IN ('WON','LOST')")
    stats["avg_confidence"] = round(row["avg_conf"], 1) if row and row["avg_conf"] else 0

    # Aktif sinyaller
    row = _fetchone("SELECT COUNT(*) as cnt FROM signals WHERE status='ACTIVE'")
    stats["active_trades"] = row["cnt"] if row else 0

    # Bekleyen sinyaller
    row = _fetchone("SELECT COUNT(*) as cnt FROM signals WHERE status='WAITING'")
    stats["waiting_signals"] = row["cnt"] if row else 0

    # İzlenen coinler
    row = _fetchone("SELECT COUNT(*) as cnt FROM watchlist WHERE status='WATCHING'")
    stats["watching_count"] = row["cnt"] if row else 0

    # Ortalama RR
    row = _fetchone("SELECT AVG(ABS(pnl_pct)) as avg_pnl FROM signals WHERE status='WON'")
    avg_win = row["avg_pnl"] if row and row["avg_pnl"] else 0

    row = _fetchone("SELECT AVG(ABS(pnl_pct)) as avg_pnl FROM signals WHERE status='LOST'")
    avg_loss = row["avg_pnl"] if row and row["avg_pnl"] else 1

    stats["avg_rr"] = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0

    # Bileşen bazlı performans
    stats["component_performance"] = get_component_performance()

    return stats


def get_component_performance():
    """Her ICT bileşeninin başarı oranını hesapla"""
    rows = _fetchall("""
        SELECT components, status, notes, pnl_pct FROM signals
        WHERE status IN ('WON', 'LOST') AND components IS NOT NULL
    """)

    comp_stats = {}
    for row in rows:
        try:
            components = json.loads(row["components"])
        except (json.JSONDecodeError, TypeError):
            continue
        for comp in components:
            if comp not in comp_stats:
                comp_stats[comp] = {"wins": 0, "losses": 0, "total": 0, "pnl_sum": 0.0}
            comp_stats[comp]["total"] += 1
            pnl = row["pnl_pct"] if row["pnl_pct"] else 0
            comp_stats[comp]["pnl_sum"] += pnl
            if row["status"] == "WON":
                comp_stats[comp]["wins"] += 1
            else:
                comp_stats[comp]["losses"] += 1

    # Win rate + ortalama PnL hesapla
    for comp in comp_stats:
        total = comp_stats[comp]["total"]
        if total > 0:
            comp_stats[comp]["win_rate"] = round(comp_stats[comp]["wins"] / total * 100, 1)
            comp_stats[comp]["avg_pnl"] = round(comp_stats[comp]["pnl_sum"] / total, 3)
        else:
            comp_stats[comp]["win_rate"] = 0
            comp_stats[comp]["avg_pnl"] = 0

    return comp_stats


# =================== ANALİZ FONKSİYONLARI (Optimizer İçin) ===================

def get_confluence_profitability_analysis():
    """
    Confluence Score ile kârlılık arasındaki korelasyonu analiz et.
    Optimizer'ın "Hangi skor aralığı daha kârlı?" sorusuna cevap verir.
    """
    rows = _fetchall("""
        SELECT confluence_score, pnl_pct, status FROM signals
        WHERE status IN ('WON','LOST') AND confluence_score IS NOT NULL
    """)

    if not rows:
        return {"buckets": {}, "optimal_min_score": None}

    # Skor aralıklarına böl: 40-50, 50-60, 60-70, 70-80, 80-90, 90-100
    buckets = {}
    for lo in range(40, 100, 10):
        hi = lo + 10
        label = f"{lo}-{hi}"
        bucket_rows = [r for r in rows if lo <= (r['confluence_score'] or 0) < hi]
        if bucket_rows:
            wins = sum(1 for r in bucket_rows if r['status'] == 'WON')
            total = len(bucket_rows)
            avg_pnl = sum(r['pnl_pct'] or 0 for r in bucket_rows) / total
            buckets[label] = {
                "total": total, "wins": wins,
                "win_rate": round(wins / total * 100, 1),
                "avg_pnl": round(avg_pnl, 3)
            }

    # En kârlı minimum skoru bul
    optimal = None
    best_combined = -999
    for label, b in buckets.items():
        combined = b["win_rate"] * 0.6 + b["avg_pnl"] * 40  # Ağırlıklı skor
        if combined > best_combined and b["total"] >= 3:
            best_combined = combined
            optimal = int(label.split("-")[0])

    return {"buckets": buckets, "optimal_min_score": optimal}


def get_entry_mode_performance():
    """
    LIMIT vs MARKET giriş modlarının performans karşılaştırması.
    Optimizer hangi modun daha kârlı olduğunu öğrenir.
    """
    result = {}
    for mode in ("LIMIT", "MARKET"):
        rows = _fetchall("""
            SELECT status, pnl_pct FROM signals
            WHERE status IN ('WON','LOST') AND entry_mode = ?
        """, (mode,))
        if rows:
            wins = sum(1 for r in rows if r['status'] == 'WON')
            total = len(rows)
            avg_pnl = sum(r['pnl_pct'] or 0 for r in rows) / total
            result[mode] = {
                "total": total, "wins": wins,
                "win_rate": round(wins / total * 100, 1),
                "avg_pnl": round(avg_pnl, 3)
            }
    return result


def get_htf_bias_accuracy():
    """
    HTF Bias doğruluk analizi — 4H yönü doğru çıkma oranı.
    """
    rows = _fetchall("""
        SELECT htf_bias, status, pnl_pct FROM signals
        WHERE status IN ('WON','LOST') AND htf_bias IS NOT NULL
    """)
    result = {}
    for bias in ("BULLISH", "BEARISH", "WEAKENING_BULL", "WEAKENING_BEAR"):
        bias_rows = [r for r in rows if r['htf_bias'] == bias]
        if bias_rows:
            wins = sum(1 for r in bias_rows if r['status'] == 'WON')
            total = len(bias_rows)
            result[bias] = {
                "total": total, "wins": wins,
                "win_rate": round(wins / total * 100, 1)
            }
    return result


def get_loss_analysis(limit=30):
    """
    Kaybeden işlemlerin detaylı analizini çıkar.
    Bot buradan öğrenecek: neden kaybettik?
    """
    rows = _fetchall("""
        SELECT symbol, direction, components, notes, pnl_pct, confidence,
               confluence_score, created_at, close_time, entry_mode, htf_bias, rr_ratio
        FROM signals
        WHERE status = 'LOST'
        ORDER BY close_time DESC LIMIT ?
    """, (limit,))

    analysis = {
        "total_losses": len(rows),
        "avg_loss_pct": 0,
        "common_components": {},
        "missing_components": {},
        "low_confidence_losses": 0,
        "lesson_summary": []
    }

    if not rows:
        return analysis

    all_possible = [
        "MARKET_STRUCTURE", "ORDER_BLOCK", "FVG", "DISPLACEMENT",
        "LIQUIDITY_SWEEP", "OTE", "HTF_CONFIRMATION", "KILLZONE_ACTIVE",
        "DISCOUNT_ZONE", "PREMIUM_ZONE", "BREAKER_BLOCK",
        # Ek bileşenler (eksik olan 11 bileşen eklendi)
        "MTF_CONFIRMATION", "MTF_OB_CONFLUENCE", "MTF_FVG_CONFLUENCE",
        "HIGH_QUALITY_SWEEP", "SWEEP_MSS_A_PLUS",
        "HIGH_VOLUME_DISPLACEMENT", "ABOVE_AVG_VOLUME",
        "CRYPTO_ACTIVE_SESSION", "TRIPLE_TF_ALIGNMENT",
        "CORE_GATE_MULTIPLIER", "HTF_SWEEP_DISP_MULTIPLIER"
    ]
    total_pnl = 0

    for row in rows:
        total_pnl += abs(row["pnl_pct"]) if row["pnl_pct"] else 0

        if row["confidence"] and row["confidence"] < 65:
            analysis["low_confidence_losses"] += 1

        try:
            comps = json.loads(row["components"]) if row["components"] else []
        except (json.JSONDecodeError, TypeError):
            comps = []

        for c in comps:
            analysis["common_components"][c] = analysis["common_components"].get(c, 0) + 1

        for possible in all_possible:
            if possible not in comps:
                analysis["missing_components"][possible] = \
                    analysis["missing_components"].get(possible, 0) + 1

    analysis["avg_loss_pct"] = round(total_pnl / len(rows), 3) if rows else 0

    # Ders çıkar
    if analysis["low_confidence_losses"] > len(rows) * 0.4:
        analysis["lesson_summary"].append(
            "Kayıpların çoğu düşük güvenle açılmış — min_confidence yükseltilmeli"
        )

    # En çok eksik olan bileşeni bul
    if analysis["missing_components"]:
        worst_missing = max(analysis["missing_components"], key=analysis["missing_components"].get)
        miss_pct = analysis["missing_components"][worst_missing] / len(rows)
        if miss_pct > 0.6:
            analysis["lesson_summary"].append(
                f"Kayıpların %{miss_pct*100:.0f}'inde {worst_missing} eksikti — bu bileşen zorunlu tutulabilir"
            )

    # En çok bulunan ama yine kaybeden bileşeni bul
    if analysis["common_components"]:
        worst_present = max(analysis["common_components"], key=analysis["common_components"].get)
        present_pct = analysis["common_components"][worst_present] / len(rows)
        if present_pct > 0.6:
            analysis["lesson_summary"].append(
                f"{worst_present} bileşeni kayıpların %{present_pct*100:.0f}'inde vardı — "
                f"tek başına güvenilir değil, ek onay gerekli"
            )

    return analysis


# =================== AME SİNYAL İŞLEMLERİ ===================

def add_ame_signal(symbol, direction, entry_price, stop_loss, take_profit,
                   score, rr_ratio=None, regime="", mode="balanced",
                   impulse_score=0, velocity=0, notes=""):
    """Yeni AME sinyal kaydet — ICT'den tamamen bağımsız."""
    now = datetime.now().isoformat()
    return _execute_returning_id("""
        INSERT INTO ame_signals (symbol, direction, entry_price, stop_loss, take_profit,
                           score, rr_ratio, regime, mode, impulse_score, velocity,
                           status, entry_time, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
    """, (symbol, direction, entry_price, stop_loss, take_profit,
          score, rr_ratio, regime, mode, impulse_score, velocity, now, notes))


def update_ame_signal_status(signal_id, status, close_price=None, pnl_pct=None):
    now = datetime.now().isoformat()
    if close_price is not None:
        _execute("""
            UPDATE ame_signals SET status=?, close_price=?, pnl_pct=?, close_time=?, updated_at=?
            WHERE id=?
        """, (status, close_price, pnl_pct, now, now, signal_id))
    else:
        _execute("""
            UPDATE ame_signals SET status=?, updated_at=? WHERE id=?
        """, (status, now, signal_id))


def get_ame_active_signals():
    return _fetchall("""
        SELECT * FROM ame_signals WHERE status = 'ACTIVE' ORDER BY created_at DESC
    """)


def get_ame_signal_history(limit=50):
    return _fetchall("""
        SELECT * FROM ame_signals WHERE status IN ('WON', 'LOST', 'CANCELLED')
        ORDER BY close_time DESC, created_at DESC LIMIT ?
    """, (limit,))


def get_ame_all_signals(limit=100):
    return _fetchall("""
        SELECT * FROM ame_signals ORDER BY created_at DESC LIMIT ?
    """, (limit,))


def get_ame_active_trade_count():
    row = _fetchone("SELECT COUNT(*) as cnt FROM ame_signals WHERE status = 'ACTIVE'")
    return row["cnt"] if row else 0


def get_ame_performance_summary():
    """AME performans istatistikleri."""
    stats = {}

    row = _fetchone("SELECT COUNT(*) as cnt FROM ame_signals WHERE status IN ('WON','LOST')")
    stats["total_trades"] = row["cnt"] if row else 0

    row = _fetchone("SELECT COUNT(*) as cnt FROM ame_signals WHERE status='WON'")
    stats["winning_trades"] = row["cnt"] if row else 0

    row = _fetchone("SELECT COUNT(*) as cnt FROM ame_signals WHERE status='LOST'")
    stats["losing_trades"] = row["cnt"] if row else 0

    if stats["total_trades"] > 0:
        stats["win_rate"] = round(stats["winning_trades"] / stats["total_trades"] * 100, 1)
    else:
        stats["win_rate"] = 0

    row = _fetchone("SELECT COALESCE(SUM(pnl_pct), 0) as total FROM ame_signals WHERE status IN ('WON','LOST')")
    stats["total_pnl"] = round(row["total"], 2) if row else 0

    row = _fetchone("SELECT COUNT(*) as cnt FROM ame_signals WHERE status='ACTIVE'")
    stats["active_trades"] = row["cnt"] if row else 0

    # Ortalama RR
    row = _fetchone("SELECT AVG(ABS(pnl_pct)) as avg_pnl FROM ame_signals WHERE status='WON'")
    avg_win = row["avg_pnl"] if row and row["avg_pnl"] else 0
    row = _fetchone("SELECT AVG(ABS(pnl_pct)) as avg_pnl FROM ame_signals WHERE status='LOST'")
    avg_loss = row["avg_pnl"] if row and row["avg_pnl"] else 1
    stats["avg_rr"] = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0

    # Ortalama skor
    row = _fetchone("SELECT AVG(score) as avg_score FROM ame_signals WHERE status IN ('WON','LOST')")
    stats["avg_score"] = round(row["avg_score"], 1) if row and row["avg_score"] else 0

    return stats


# =================== BAŞLATMA ===================
init_db()
