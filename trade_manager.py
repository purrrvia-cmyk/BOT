# =====================================================
# ICT Trading Bot — Trade Yönetim Modülü v4.0
# (Narrative → POI → Trigger Protocol)
# =====================================================
#
# SIFIRDAN YAZILDI: v3.x'le uyumsuz, yeni mimari
#
# v4.0 DEĞİŞİKLİKLER:
#   1. LİMİT EMİR KALKTI → MARKET giriş (trigger anında)
#   2. 15dk BEKLEME (3×5m) KALKTI → SIGNAL anında açılır
#   3. EMA-20 HTF bias check KALKTI → Yapısal BOS/CHoCH kontrolü
#   4. ERKEN KORUMA (%25) KALKTI → Noise'a yakalanıyordu
#   5. BREAKEVEN %40 → %50'ye yükseltildi (daha güvenli)
#   6. TRAİLİNG %60 → %75'e yükseltildi (daha geniş nefes)
#   7. WATCHLIST basitleştirildi → POI-trigger tabanlı
#
# AKIŞ:
#   SIGNAL → direkt _open_trade(MARKET) → ACTIVE
#   WATCH  → watchlist → periyodik re-check → trigger oluşunca PROMOTE
#
# SL YÖNETİMİ (2 Aşama — progresif değil, yapısal):
#   %50 TP mesafesi → SL entry'ye taşı (breakeven)
#   %75 TP mesafesi → Trailing SL (kârın %50'sinde kilitle)
#
# EARLY EXIT:
#   Max süre aşımı (4h — 15m TF için)
#   Yapısal bozulma (TP vs SL ters)
# =====================================================

import json
import logging
from datetime import datetime, timedelta
from data_fetcher import data_fetcher
from database import (
    get_active_signals, update_signal_status, activate_signal,
    get_active_trade_count, add_signal, add_to_watchlist,
    get_watching_items, update_watchlist_item, promote_watchlist_item,
    expire_watchlist_item, get_signal_history, get_bot_param,
    update_signal_sl, _execute
)
from config import ICT_PARAMS

logger = logging.getLogger("ICT-Bot.TradeManager")

# ─── SABITLER ────────────────────────────────────────
MAX_TRADE_DURATION_HOURS = 4      # 15m TF sinyalleri için max yaşam süresi
WATCH_MAX_CANDLES = 12            # Watchlist max izleme: 12 × 5m = 60dk
WATCH_TIMEFRAME = "5m"            # Watchlist izleme TF'si
WATCH_CHECK_INTERVAL_SEC = 60     # Watchlist kontrol aralığı


class TradeManager:
    """
    Trade yönetim motoru v4.0 — Narrative → POI → Trigger uyumlu.

    Akış:
      process_signal → SIGNAL → direkt MARKET giriş
      process_signal → WATCH  → watchlist → trigger bekle → MARKET giriş
      check_open_trades → ACTIVE SL/TP + BE/Trailing
      check_watchlist → check_trigger_for_watch (hafif) → promote veya expire

    Watchlist v4.0:
      - Stored narrative + POI ile hafif trigger kontrolü
      - Sadece 15m + 5m veri çekilir (4H/1H API tasarrufu)
      - POI invalidation: fiyat zone'u sweep ederse → expire
    """

    def __init__(self):
        self._trade_state = {}
        self._restore_trade_state()

    def _restore_trade_state(self):
        """Restart sonrası ACTIVE sinyallerin BE/trailing durumunu geri yükle."""
        try:
            active = get_active_signals()
            restored = 0
            for sig in active:
                sid = sig["id"]
                entry = sig.get("entry_price", 0)
                sl = sig.get("stop_loss", 0)
                tp = sig.get("take_profit", 0)
                direction = sig.get("direction", "LONG")

                if not entry or not sl:
                    continue

                be_moved = False

                if direction == "LONG" and sl >= entry:
                    if tp and tp > entry:
                        be_moved = True
                elif direction == "SHORT" and sl <= entry:
                    if tp and tp < entry and sl > tp:
                        be_moved = True

                if be_moved:
                    self._trade_state[sid] = {
                        "breakeven_moved": True,
                        "trailing_sl": sl,
                    }
                    restored += 1
                    logger.info(f"♻️ {sig.get('symbol','?')} state restored: BE=True, SL={sl}")

            if restored:
                logger.info(f"♻️ {restored} aktif sinyalin trade state'i geri yüklendi")
        except Exception as e:
            logger.error(f"Trade state geri yükleme hatası: {e}")

    def _param(self, name):
        """Parametre oku: DB varsa DB, yoksa config varsayılanı."""
        return get_bot_param(name, ICT_PARAMS.get(name))

    # =================================================================
    #  SİNYAL İŞLEME — SIGNAL direkt, WATCH izlemeye
    # =================================================================

    def process_signal(self, signal_result):
        """
        Strateji motorundan gelen sinyal sonucunu işle.

        v4.0 Akış:
          SIGNAL → direkt _open_trade (trigger zaten oluştu, MARKET giriş)
          WATCH  → watchlist'e ekle (POI tespit, trigger bekleniyor)

        ★ 15dk bekleme KALKTI — trigger = fiyat hareketi teyidi.
        ★ Puanlama / filtreleme YOK.
        """
        if signal_result is None:
            return None

        action = signal_result.get("action")
        symbol = signal_result.get("symbol", "")

        if not action or not symbol:
            return None

        # Aynı coinde zaten aktif/bekleyen işlem varsa → reddet
        active_signals = get_active_signals()
        for s in active_signals:
            if s["symbol"] == symbol and s["status"] in ("ACTIVE", "WAITING"):
                return {"status": "REJECTED", "reason": "Aktif/bekleyen işlem mevcut"}

        if action == "SIGNAL":
            # ═══ TRIGGER OLUŞTU → DİREKT MARKET GİRİŞ ═══
            trade_signal = self._normalize_signal(signal_result)
            return self._open_trade(trade_signal)

        elif action == "WATCH":
            # ═══ POI TESPİT → İZLEMEYE AL ═══
            return self._add_to_watchlist(signal_result)

        return None

    def _normalize_signal(self, raw):
        """Strateji motorundan gelen sinyali trade_manager formatına dönüştür."""
        return {
            "symbol": raw["symbol"],
            "direction": raw.get("direction", "LONG"),
            "entry": raw.get("entry_price") or raw.get("entry", 0),
            "sl": raw.get("stop_loss") or raw.get("sl", 0),
            "tp": raw.get("take_profit") or raw.get("tp", 0),
            "rr_ratio": raw.get("rr_ratio", 0),
            "entry_mode": "MARKET",
            "trigger_type": raw.get("trigger_type", "UNKNOWN"),
            "quality_tier": raw.get("quality_tier", ""),
            "components": raw.get("components", []),
            "narrative": raw.get("narrative", {}),
            "poi": raw.get("poi", {}),
            "atr": raw.get("atr", 0),
            "confidence": raw.get("confidence", 100),
            "confluence_score": raw.get("confluence_score", 100),
            "timeframe": raw.get("timeframe", "15m"),
        }

    def _add_to_watchlist(self, signal_result):
        """
        WATCH sinyalini izleme listesine ekle.

        v4.0: narrative + poi verisi components alanında saklanır.
        check_trigger_for_watch() bu verileri kullanarak sadece 15m
        data ile hafif trigger kontrolü yapar.
        """
        symbol = signal_result["symbol"]
        direction = signal_result.get("direction", "LONG")
        reason = signal_result.get("watch_reason", "POI tespit edildi, trigger bekleniyor")

        # Narrative + POI → components alanında sakla (JSON)
        watch_data = {
            "narrative": signal_result.get("narrative", {}),
            "poi": signal_result.get("poi", {}),
        }

        try:
            wl_id = add_to_watchlist(
                symbol=symbol,
                direction=direction,
                potential_entry=signal_result.get("entry_price") or signal_result.get("entry"),
                potential_sl=signal_result.get("stop_loss") or signal_result.get("sl"),
                potential_tp=signal_result.get("take_profit") or signal_result.get("tp"),
                watch_reason=reason,
                initial_score=0,
                components=watch_data,
                max_watch=WATCH_MAX_CANDLES,
            )
            if wl_id:
                logger.info(f"👁️ İZLEMEYE ALINDI: {symbol} ({direction}) — {reason}")
                return {
                    "status": "WATCHING",
                    "symbol": symbol,
                    "direction": direction,
                    "reason": reason,
                }
        except Exception as e:
            logger.error(f"Watchlist ekleme hatası ({symbol}): {e}")

        return None

    # =================================================================
    #  İŞLEM AÇMA — Sadece Risk Yönetimi, MARKET Giriş
    # =================================================================

    def _open_trade(self, signal):
        """
        Yeni işlem aç — MARKET giriş.

        ★ Puanlama kapısı YOK
        ★ LIMIT emir YOK → her zaman MARKET
        ★ Sadece risk yönetimi kontrolleri:
            - Max eşzamanlı işlem limiti
            - Aynı coinde aktif işlem kontrolü
            - Aynı yönde max işlem kontrolü
            - Cooldown (son kapanan işlemden bekleme)
        """
        symbol = signal["symbol"]
        direction = signal.get("direction", "LONG")

        # ══ MAX EŞZAMANLI İŞLEM ══
        max_concurrent = int(self._param("max_concurrent_trades") or 3)
        active_count = get_active_trade_count()
        if active_count >= max_concurrent:
            logger.warning(f"⛔ {symbol} reddedildi: Max eşzamanlı işlem limiti ({max_concurrent})")
            return {"status": "REJECTED", "reason": "Maksimum işlem limiti"}

        # ══ AYNI COİNDE AKTİF İŞLEM ══
        active_signals = get_active_signals()
        for s in active_signals:
            if s["symbol"] == symbol and s["status"] == "ACTIVE":
                logger.info(f"⏭️ {symbol} için zaten aktif işlem var, atlanıyor")
                return {"status": "REJECTED", "reason": "Aktif işlem mevcut"}

        # ══ AYNI YÖNDE MAX İŞLEM ══
        max_same_dir = int(self._param("max_same_direction_trades") or 2)
        same_dir_count = sum(
            1 for s in active_signals
            if s.get("direction") == direction and s["status"] == "ACTIVE"
        )
        if same_dir_count >= max_same_dir:
            logger.warning(f"⛔ {symbol} reddedildi: Aynı yönde ({direction}) max {max_same_dir} işlem limiti")
            return {"status": "REJECTED", "reason": f"Max {direction} işlem limiti ({max_same_dir})"}

        # ══ COOLDOWN KONTROLÜ ══
        cooldown_minutes = int(self._param("signal_cooldown_minutes") or 20)
        recent_history = get_signal_history(30)
        now = datetime.now()
        for s in recent_history:
            if s["symbol"] != symbol:
                continue
            if s.get("status") not in ("WON", "LOST", "CANCELLED"):
                continue
            close_time = s.get("close_time") or s.get("created_at", "")
            if close_time:
                try:
                    close_dt = datetime.fromisoformat(close_time)
                    if (now - close_dt).total_seconds() < cooldown_minutes * 60:
                        logger.info(f"⏳ {symbol} için {cooldown_minutes}dk cooldown aktif")
                        return {"status": "REJECTED", "reason": f"{cooldown_minutes}dk cooldown"}
                except Exception:
                    pass

        # ══ SİNYAL DOĞRULAMA ══
        entry = signal.get("entry", 0)
        sl = signal.get("sl", 0)
        tp = signal.get("tp", 0)

        if not entry or not sl or not tp:
            logger.warning(f"⛔ {symbol} reddedildi: entry/sl/tp eksik")
            return {"status": "REJECTED", "reason": "Eksik seviyeler"}

        # SL mesafe kontrolü
        sl_distance_pct = abs(entry - sl) / entry
        min_sl = float(self._param("min_sl_distance_pct") or 0.008)
        max_sl = float(self._param("max_sl_distance_pct") or 0.030)

        if sl_distance_pct < min_sl * 0.95:  # %5 tolerans (float precision)
            logger.warning(f"⛔ {symbol} reddedildi: SL çok dar ({sl_distance_pct:.4f} < {min_sl})")
            return {"status": "REJECTED", "reason": f"SL çok dar ({sl_distance_pct:.1%})"}

        if sl_distance_pct > max_sl:
            logger.warning(f"⛔ {symbol} reddedildi: SL çok geniş ({sl_distance_pct:.4f} > {max_sl})")
            return {"status": "REJECTED", "reason": f"SL çok geniş ({sl_distance_pct:.1%})"}

        # ══ MARKET GİRİŞ ══
        trigger_type = signal.get("trigger_type", "UNKNOWN")
        quality = signal.get("quality_tier", "")
        components = signal.get("components", [])

        entry_notes = (
            f"Mode: MARKET | "
            f"Trigger: {trigger_type} | "
            f"Quality: {quality} | "
            f"RR: {signal.get('rr_ratio', '?')} | "
            f"Components: {', '.join(components) if components else 'N/A'}"
        )

        signal_id = add_signal(
            symbol=symbol,
            direction=direction,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            confidence=signal.get("confidence", 100),
            confluence_score=signal.get("confluence_score", 100),
            components=components,
            timeframe=signal.get("timeframe", "15m"),
            status="ACTIVE",
            notes=entry_notes,
            entry_mode="MARKET",
            htf_bias=direction,
            rr_ratio=signal.get("rr_ratio"),
        )

        # MARKET → hemen aktif et
        activate_signal(signal_id)

        logger.info(
            f"✅ İŞLEM AÇILDI: #{signal_id} {symbol} {direction} | "
            f"Entry: {entry} | SL: {sl} | TP: {tp} | "
            f"RR: {signal.get('rr_ratio', '?')} | "
            f"Trigger: {trigger_type}"
        )

        return {
            "status": "OPENED",
            "signal_id": signal_id,
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "rr_ratio": signal.get("rr_ratio"),
            "trigger_type": trigger_type,
            "entry_mode": "MARKET",
        }

    # =================================================================
    #  AÇIK İŞLEM TAKİBİ — SADECE ACTIVE (WAITING YOK)
    # =================================================================

    def check_open_trades(self):
        """
        Aktif işlemleri kontrol et.

        v4.0: WAITING durumu yok (LIMIT kaldırıldı).
        Sadece ACTIVE sinyallerin SL/TP takibi + BE/Trailing.
        """
        active_signals = get_active_signals()
        results = []

        for signal in active_signals:
            status = signal["status"]

            # v4.0: WAITING sinyalleri olmamalı, ama varsa iptal et
            if status == "WAITING":
                update_signal_status(signal["id"], "CANCELLED",
                                     close_price=0, pnl_pct=0)
                logger.warning(f"⚠️ WAITING sinyal temizlendi: #{signal['id']} {signal['symbol']}")
                continue

            if status != "ACTIVE":
                continue

            symbol = signal["symbol"]
            ticker = data_fetcher.get_ticker(symbol)
            if not ticker:
                continue

            current_price = ticker["last"]
            result = self._check_active_signal(
                signal, current_price,
                signal["entry_price"], signal["stop_loss"],
                signal["take_profit"], signal["direction"],
                signal["id"]
            )
            if result:
                results.append(result)

        return results

    def _check_active_signal(self, signal, current_price, entry_price,
                              stop_loss, take_profit, direction, signal_id):
        """
        ACTIVE sinyal SL/TP takibi + Breakeven/Trailing SL.

        v4.0 SL Yönetimi (2 aşama):
          %50 TP → Breakeven (SL entry'ye)
          %75 TP → Trailing (SL kârın %50'sine)
        """
        symbol = signal["symbol"]
        result = {
            "signal_id": signal_id, "symbol": symbol,
            "direction": direction, "current_price": current_price,
            "entry_price": entry_price, "status": "ACTIVE",
        }

        # ══ MAX TRADE DURATION ══
        entry_time = signal.get("entry_time") or signal.get("created_at", "")
        if entry_time:
            try:
                entry_dt = datetime.fromisoformat(entry_time)
                trade_hours = (datetime.now() - entry_dt).total_seconds() / 3600
                if trade_hours > MAX_TRADE_DURATION_HOURS:
                    pnl_pct = self._calc_pnl(direction, entry_price, current_price)
                    status = "WON" if pnl_pct > 0 else "LOST"
                    update_signal_status(signal_id, status, close_price=current_price, pnl_pct=pnl_pct)
                    self._trade_state.pop(signal_id, None)
                    result["status"] = status
                    result["pnl_pct"] = round(pnl_pct, 2)
                    emoji = "🏆" if pnl_pct > 0 else "⏰"
                    logger.info(f"{emoji} MAX SÜRE: #{signal_id} {symbol} | {trade_hours:.1f}h | PnL: {pnl_pct:+.2f}%")
                    return result
            except Exception:
                pass

        # Trade state
        state = self._trade_state.get(signal_id, {
            "breakeven_moved": False,
            "trailing_sl": None,
        })
        is_be_trade = state.get("breakeven_moved", False)

        # ══ YAPISAL SEVİYE DOĞRULAMA ══
        # TP her zaman SL'nin "öbür tarafında" olmalı
        structurally_valid = True
        if direction == "LONG" and take_profit <= stop_loss:
            structurally_valid = False
        elif direction == "SHORT" and take_profit >= stop_loss:
            structurally_valid = False

        if not structurally_valid:
            logger.warning(f"⚠️ #{signal_id} {symbol} {direction} yapısal bozukluk (TP vs SL ters) — iptal")
            update_signal_status(signal_id, "CANCELLED", close_price=current_price, pnl_pct=0)
            self._trade_state.pop(signal_id, None)
            result["status"] = "CANCELLED"
            return result

        # BE olmayan trade'lerde SL/TP entry'nin doğru tarafında mı?
        if not is_be_trade:
            if direction == "LONG" and (stop_loss >= entry_price or take_profit <= entry_price):
                logger.warning(f"⚠️ #{signal_id} {symbol} LONG ters seviyeler — iptal")
                update_signal_status(signal_id, "CANCELLED", close_price=current_price, pnl_pct=0)
                self._trade_state.pop(signal_id, None)
                result["status"] = "CANCELLED"
                return result
            elif direction == "SHORT" and (stop_loss <= entry_price or take_profit >= entry_price):
                logger.warning(f"⚠️ #{signal_id} {symbol} SHORT ters seviyeler — iptal")
                update_signal_status(signal_id, "CANCELLED", close_price=current_price, pnl_pct=0)
                self._trade_state.pop(signal_id, None)
                result["status"] = "CANCELLED"
                return result

        # ══ SL YÖNETİMİ + TP/SL KONTROL ══
        effective_sl = stop_loss

        if direction == "LONG":
            effective_sl = self._manage_long_sl(
                signal_id, symbol, entry_price, current_price,
                stop_loss, take_profit, state
            )

            if current_price >= take_profit:
                pnl_pct = self._calc_pnl("LONG", entry_price, current_price)
                update_signal_status(signal_id, "WON", close_price=current_price, pnl_pct=pnl_pct)
                self._trade_state.pop(signal_id, None)
                result["status"] = "WON"
                result["pnl_pct"] = round(pnl_pct, 2)
                logger.info(f"🏆 KAZANDIK: #{signal_id} {symbol} LONG | PnL: +{pnl_pct:.2f}%")

            elif current_price <= effective_sl:
                pnl_pct = self._calc_pnl_with_slippage("LONG", entry_price, current_price, effective_sl)
                sl_type = self._get_sl_close_type(state)
                status = "WON" if pnl_pct > 0 else "LOST"
                update_signal_status(signal_id, status, close_price=current_price, pnl_pct=pnl_pct)
                self._trade_state.pop(signal_id, None)
                result["status"] = status
                result["pnl_pct"] = round(pnl_pct, 2)
                emoji = "🏆" if pnl_pct > 0 else "❌"
                logger.info(f"{emoji} {sl_type}: #{signal_id} {symbol} LONG | PnL: {pnl_pct:+.2f}%")

            else:
                unrealized = self._calc_pnl("LONG", entry_price, current_price)
                result["unrealized_pnl"] = round(unrealized, 2)
                if state.get("breakeven_moved") or state.get("trailing_sl"):
                    result["effective_sl"] = round(effective_sl, 8)

        elif direction == "SHORT":
            effective_sl = self._manage_short_sl(
                signal_id, symbol, entry_price, current_price,
                stop_loss, take_profit, state
            )

            if current_price <= take_profit:
                pnl_pct = self._calc_pnl("SHORT", entry_price, current_price)
                update_signal_status(signal_id, "WON", close_price=current_price, pnl_pct=pnl_pct)
                self._trade_state.pop(signal_id, None)
                result["status"] = "WON"
                result["pnl_pct"] = round(pnl_pct, 2)
                logger.info(f"🏆 KAZANDIK: #{signal_id} {symbol} SHORT | PnL: +{pnl_pct:.2f}%")

            elif current_price >= effective_sl:
                pnl_pct = self._calc_pnl_with_slippage("SHORT", entry_price, current_price, effective_sl)
                sl_type = self._get_sl_close_type(state)
                status = "WON" if pnl_pct > 0 else "LOST"
                update_signal_status(signal_id, status, close_price=current_price, pnl_pct=pnl_pct)
                self._trade_state.pop(signal_id, None)
                result["status"] = status
                result["pnl_pct"] = round(pnl_pct, 2)
                emoji = "🏆" if pnl_pct > 0 else "❌"
                logger.info(f"{emoji} {sl_type}: #{signal_id} {symbol} SHORT | PnL: {pnl_pct:+.2f}%")

            else:
                unrealized = self._calc_pnl("SHORT", entry_price, current_price)
                result["unrealized_pnl"] = round(unrealized, 2)
                if state.get("breakeven_moved") or state.get("trailing_sl"):
                    result["effective_sl"] = round(effective_sl, 8)

        # State kaydet
        self._trade_state[signal_id] = state

        # DB'ye yaz (restart koruması)
        if state.get("breakeven_moved") or state.get("trailing_sl"):
            update_signal_sl(signal_id, effective_sl)

        return result

    # =================================================================
    #  BREAKEVEN / TRAILING SL — 2 Aşamalı (v4.0)
    # =================================================================

    def _manage_long_sl(self, signal_id, symbol, entry_price, current_price,
                         stop_loss, take_profit, state):
        """
        LONG: Yapısal SL yönetimi.

        %50 TP mesafesi → Breakeven (SL = entry + buffer)
        %75 TP mesafesi → Trailing (SL = entry + kârın %50'si)
        """
        total_distance = take_profit - entry_price
        current_progress = current_price - entry_price
        effective_sl = stop_loss

        if total_distance > 0 and current_progress > 0:
            progress_pct = current_progress / total_distance

            # %75+ → Trailing SL (kârın %50'si)
            if progress_pct >= 0.75:
                trailing = entry_price + (current_progress * 0.50)
                prev_trailing = state.get("trailing_sl")
                if prev_trailing is None or trailing > prev_trailing:
                    state["trailing_sl"] = trailing
                    if not state.get("trailing_logged"):
                        logger.info(f"📈 #{signal_id} {symbol} TRAILING: {trailing:.6f} ({progress_pct:.0%})")
                        state["trailing_logged"] = True

                # Breakeven da aktif olmalı
                if not state.get("breakeven_moved"):
                    state["breakeven_moved"] = True
                    state["breakeven_sl"] = entry_price * 1.002

            # %60+ → Breakeven (v4.6: %50→%60, buffer %0.1→%0.2)
            elif progress_pct >= 0.60 and not state.get("breakeven_moved"):
                state["breakeven_moved"] = True
                be_sl = entry_price * 1.002  # Entry + %0.2 buffer
                state["breakeven_sl"] = be_sl
                logger.info(f"🔒 #{signal_id} {symbol} BREAKEVEN: SL → {be_sl:.6f} ({progress_pct:.0%})")

        # En iyi SL seviyesini kullan
        if state.get("trailing_sl"):
            effective_sl = max(effective_sl, state["trailing_sl"])
        if state.get("breakeven_sl"):
            effective_sl = max(effective_sl, state["breakeven_sl"])

        return effective_sl

    def _manage_short_sl(self, signal_id, symbol, entry_price, current_price,
                          stop_loss, take_profit, state):
        """
        SHORT: Yapısal SL yönetimi.

        %50 TP mesafesi → Breakeven (SL = entry - buffer)
        %75 TP mesafesi → Trailing (SL = entry - kârın %50'si)
        """
        total_distance = entry_price - take_profit
        current_progress = entry_price - current_price
        effective_sl = stop_loss

        if total_distance > 0 and current_progress > 0:
            progress_pct = current_progress / total_distance

            # %75+ → Trailing SL
            if progress_pct >= 0.75:
                trailing = entry_price - (current_progress * 0.50)
                prev_trailing = state.get("trailing_sl")
                if prev_trailing is None or trailing < prev_trailing:
                    state["trailing_sl"] = trailing
                    if not state.get("trailing_logged"):
                        logger.info(f"📉 #{signal_id} {symbol} TRAILING: {trailing:.6f} ({progress_pct:.0%})")
                        state["trailing_logged"] = True

                if not state.get("breakeven_moved"):
                    state["breakeven_moved"] = True
                    state["breakeven_sl"] = entry_price * 0.998

            # %60+ → Breakeven (v4.6: %50→%60, buffer %0.1→%0.2)
            elif progress_pct >= 0.60 and not state.get("breakeven_moved"):
                state["breakeven_moved"] = True
                be_sl = entry_price * 0.998  # Entry - %0.2 buffer
                state["breakeven_sl"] = be_sl
                logger.info(f"🔒 #{signal_id} {symbol} BREAKEVEN: SL → {be_sl:.6f} ({progress_pct:.0%})")

        # En iyi SL seviyesini kullan (SHORT: daha düşük = daha iyi)
        if state.get("trailing_sl"):
            effective_sl = min(effective_sl, state["trailing_sl"])
        if state.get("breakeven_sl"):
            effective_sl = min(effective_sl, state["breakeven_sl"])

        return effective_sl

    # =================================================================
    #  YARDIMCI FONKSİYONLAR
    # =================================================================

    @staticmethod
    def _calc_pnl(direction, entry_price, current_price):
        """PnL hesapla (%)."""
        if direction == "LONG":
            return ((current_price - entry_price) / entry_price) * 100
        else:
            return ((entry_price - current_price) / entry_price) * 100

    @staticmethod
    def _calc_pnl_with_slippage(direction, entry_price, current_price, effective_sl):
        """
        Slippage korumalı PnL hesapla.
        Gerçek PnL, SL seviyesindeki PnL'den %0.5'ten fazla kötüyse → SL PnL - 0.5 kullan.
        """
        if direction == "LONG":
            raw_pnl = ((current_price - entry_price) / entry_price) * 100
            sl_pnl = ((effective_sl - entry_price) / entry_price) * 100
        else:
            raw_pnl = ((entry_price - current_price) / entry_price) * 100
            sl_pnl = ((entry_price - effective_sl) / entry_price) * 100

        if raw_pnl < 0 and raw_pnl < sl_pnl - 0.5:
            return sl_pnl - 0.5
        return raw_pnl

    @staticmethod
    def _get_sl_close_type(state):
        """SL kapanış tipini belirle."""
        if state.get("trailing_sl"):
            return "TRAILING_SL"
        elif state.get("breakeven_moved"):
            return "BREAKEVEN"
        return "STRUCTURAL_SL"

    # =================================================================
    #  İZLEME LİSTESİ — POI-Trigger Tabanlı (v4.0)
    # =================================================================

    def check_watchlist(self, strategy_engine):
        """
        İzleme listesi kontrolü — POI-trigger tabanlı (hafif).

        v4.0 Akış:
          1. Watchlist'teki her item için 5m + 15m veri çek
          2. strategy_engine.check_trigger_for_watch() ile hafif trigger kontrolü
             (stored narrative + POI kullanılır → 4H/1H API çağrısı YAPILMAZ)
          3. POI invalidated → expire
          4. SIGNAL dönerse → promote → _open_trade
          5. SL kırıldıysa → expire
          6. Timeout → expire
          7. Yoksa → izlemeye devam
        """
        watching_items = get_watching_items()
        promoted = []

        for item in watching_items:
            symbol = item["symbol"]
            candles_watched = int(item.get("candles_watched", 0))
            max_watch = item.get("max_watch_candles", WATCH_MAX_CANDLES)
            stored_ts = item.get("last_5m_candle_ts") or ""

            # ── 5m VERİ ÇEK (mum sayımı + SL kontrolü) ──
            try:
                df_ltf = data_fetcher.get_candles(symbol, WATCH_TIMEFRAME, 15)
            except Exception as e:
                logger.debug(f"Watchlist veri hatası ({symbol}): {e}")
                continue

            if df_ltf is None or df_ltf.empty:
                continue

            # Son 5m mum timestamp'i — yeni mum kapanmadan tekrar kontrol etme
            current_ts = str(df_ltf.iloc[-1]["timestamp"])
            if current_ts == stored_ts:
                continue

            candles_watched += 1

            # ── SL İNVALIDATION ──
            potential_sl = item.get("potential_sl")
            direction = item["direction"]

            if potential_sl and not df_ltf.empty:
                last_candle = df_ltf.iloc[-1]
                if direction == "LONG" and float(last_candle.get("low", 0)) <= potential_sl:
                    expire_watchlist_item(item["id"], reason=f"SL kırıldı ({candles_watched}. mum)")
                    logger.info(f"❌ WATCH SL KIRILDI: {symbol} LONG")
                    continue
                elif direction == "SHORT" and float(last_candle.get("high", 0)) >= potential_sl:
                    expire_watchlist_item(item["id"], reason=f"SL kırıldı ({candles_watched}. mum)")
                    logger.info(f"❌ WATCH SL KIRILDI: {symbol} SHORT")
                    continue

            # ── TIMEOUT ──
            if candles_watched >= max_watch:
                expire_watchlist_item(item["id"], reason=f"Timeout ({candles_watched} mum, trigger oluşmadı)")
                logger.info(f"⏰ WATCH TIMEOUT: {symbol} ({candles_watched}/{max_watch})")
                continue

            # ── STORED NARRATIVE + POI ÇÖZÜMLE ──
            stored_narrative = {}
            stored_poi = {}
            try:
                components_raw = item.get("components", "{}")
                if isinstance(components_raw, str):
                    components_data = json.loads(components_raw)
                else:
                    components_data = components_raw or {}

                # v4.0 format: {"narrative": {...}, "poi": {...}}
                if isinstance(components_data, dict):
                    stored_narrative = components_data.get("narrative", {})
                    stored_poi = components_data.get("poi", {})
            except (json.JSONDecodeError, TypeError):
                logger.debug(f"{symbol} watchlist components parse hatası, expire ediliyor")
                expire_watchlist_item(item["id"], reason="Components parse hatası")
                continue

            if not stored_narrative or not stored_poi:
                # Eski format veya eksik veri → expire
                expire_watchlist_item(item["id"], reason="Narrative/POI verisi eksik (eski format)")
                logger.debug(f"{symbol} watchlist item expired: narrative/poi eksik")
                continue

            # ── TRIGGER KONTROLÜ — check_trigger_for_watch (hafif) ──
            try:
                df_15m = data_fetcher.get_candles(symbol, "15m", 100)
                signal_result = strategy_engine.check_trigger_for_watch(
                    symbol, df_15m, stored_narrative, stored_poi
                )
            except Exception as e:
                logger.debug(f"Watchlist trigger check hatası ({symbol}): {e}")
                update_watchlist_item(item["id"], candles_watched, 0,
                                     last_5m_candle_ts=current_ts)
                continue

            # POI invalidated → expire
            if signal_result and signal_result.get("_invalidated"):
                reason = signal_result.get("reason", "POI invalidated")
                expire_watchlist_item(item["id"], reason=reason)
                logger.info(f"🚫 WATCH POI INVALIDATED: {symbol} — {reason}")
                continue

            if signal_result and signal_result.get("action") == "SIGNAL":
                # ═══ TRIGGER OLUŞTU → PROMOTE ═══
                promote_watchlist_item(item["id"])
                logger.info(f"✅ TRIGGER OLUŞTU: {symbol} ({candles_watched}. mum) — işlem açılıyor")

                trade_signal = self._normalize_signal(signal_result)
                trade_result = self._open_trade(trade_signal)

                if trade_result and trade_result.get("status") != "REJECTED":
                    promoted.append({
                        "symbol": symbol,
                        "action": "PROMOTED",
                        "trade_result": trade_result,
                    })
                    logger.info(f"⬆️ İZLEMEDEN AKTİF SİNYALE: {symbol} (trigger tabanlı promote)")
            else:
                # Trigger yok → izlemeye devam
                update_watchlist_item(item["id"], candles_watched, 0,
                                     last_5m_candle_ts=current_ts)
                logger.debug(f"⏳ {symbol} trigger bekleniyor ({candles_watched}/{max_watch})")

        return promoted


# Global instance
trade_manager = TradeManager()
