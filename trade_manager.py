# =====================================================
# ICT Trading Bot — Trade Yönetim Modülü v3.0
# (Pure SMC — Boolean Gate Protocol)
# =====================================================
#
# SIFIRDAN YAZILDI: Saf ICT / Smart Money Concepts
#
# DEĞİŞİKLİKLER (v3.0):
#   1. PUANLAMA KAPISI KALDIRILDI:
#      Strateji motoru 5 Boolean gate'in tamamını geçen
#      sinyaller üretir. Trade manager sadece risk yönetimi yapar.
#
#   2. TIER SİSTEMİ KALDIRILDI:
#      Tüm sinyaller A+ tier (5/5 gate geçmiş). B-tier yok.
#
#   3. FONKSİYONLAR:
#      process_signal → SIGNAL veya WATCH → hepsi watchlist'e
#      check_watchlist → 3×5m mum (15dk) izle → geçerliyse işlem aç
#      check_open_trades → WAITING limit + ACTIVE SL/TP takibi
#
#   4. BREAKEVEN / TRAILING SL:
#      %25 → SL entry altına taşı (erken koruma)
#      %40 → SL entry'ye taşı (breakeven)
#      %60 → SL kârın %50'sinde tut (trailing)
# =====================================================

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
from config import (
    ICT_PARAMS,
    WATCH_CONFIRM_TIMEFRAME,
    WATCH_CONFIRM_CANDLES,
    WATCH_REQUIRED_CONFIRMATIONS,
    LIMIT_ORDER_EXPIRY_HOURS,
    MAX_TRADE_DURATION_HOURS
)

logger = logging.getLogger("ICT-Bot.TradeManager")


class TradeManager:
    """
    Açık işlemlerin yönetimi — saf Boolean gate protocol.

    Akış:
      process_signal → SIGNAL ise → _open_trade (doğrudan)
      check_open_trades → WAITING→ACTIVE + SL/TP takibi
      check_watchlist → Basitleştirilmiş izleme
    """

    def __init__(self):
        self._trade_state = {}
        self._restore_trade_state()

    def _restore_trade_state(self):
        """Restart sonrası ACTIVE sinyallerin breakeven/trailing durumunu geri yükle.

        ★ Dikkat: SL < entry (SHORT) veya SL > entry (LONG) olması
        her zaman breakeven demek değil — ters seviyeli sinyal de olabilir.
        TP yönü ile çapraz kontrol yapılır.
        """
        try:
            active = get_active_signals()
            for sig in active:
                sid = sig["id"]
                entry = sig.get("entry_price", 0)
                sl = sig.get("stop_loss", 0)
                tp = sig.get("take_profit", 0)
                direction = sig.get("direction", "LONG")
                entry_time = sig.get("entry_time")

                if not entry or not sl:
                    continue

                be_moved = False

                if direction == "LONG" and sl >= entry:
                    # LONG BE: SL entry üstüne taşınmış → kârı kilitlemiş
                    # Doğrulama: TP hâlâ entry üstünde olmalı (yapısal bütünlük)
                    if tp and tp > entry:
                        be_moved = True
                    else:
                        logger.warning(f"⚠️ #{sid} {sig.get('symbol','?')} LONG ters seviyeler tespit edildi — BE olarak yüklenmedi")

                elif direction == "SHORT" and sl <= entry:
                    # SHORT BE: SL entry altına taşınmış → kârı kilitlemiş
                    # Doğrulama: TP hâlâ entry altında ve SL > TP olmalı (yapısal bütünlük)
                    if tp and tp < entry and sl > tp:
                        be_moved = True
                    else:
                        logger.warning(f"⚠️ #{sid} {sig.get('symbol','?')} SHORT ters seviyeler tespit edildi — BE olarak yüklenmedi")

                if be_moved:
                    self._trade_state[sid] = {
                        "breakeven_moved": True,
                        "trailing_sl": sl,
                        "breakeven_sl": sl,
                        "early_protect_sl": None,
                    }
                    logger.info(f"♻️ {sig.get('symbol','?')} trade state restored: BE=True, SL={sl}")

            if self._trade_state:
                logger.info(f"♻️ {len(self._trade_state)} aktif sinyalin trade state'i geri yüklendi")
        except Exception as e:
            logger.error(f"Trade state geri yükleme hatası: {e}")

    def _param(self, name):
        """Parametre oku: DB varsa DB, yoksa config varsayılanı."""
        return get_bot_param(name, ICT_PARAMS.get(name))

    # =================================================================
    #  SİNYAL İŞLEME — Puanlama Kapısı YOK
    # =================================================================

    def process_signal(self, signal_result):
        """
        Strateji motorundan gelen sinyal sonucunu işle.

        ★ ICT %100 uyumlu akış:
          SIGNAL veya WATCH → hepsi önce İZLEME listesine girer.
          15 dakika (3 × 5m mum) izleme sonrası hâlâ geçerliyse → işlem açılır.
          Direkt işlem açılmaz. Skor/filtreleme yok.
        """
        if signal_result is None:
            return None

        action = signal_result.get("action")

        if action in ("SIGNAL", "WATCH"):
            symbol = signal_result["symbol"]
            direction = signal_result["direction"]

            # Aynı coinde zaten aktif işlem varsa watchlist'e de ekleme
            active_signals = get_active_signals()
            for s in active_signals:
                if s["symbol"] == symbol and s["status"] in ("ACTIVE", "WAITING"):
                    return {"status": "REJECTED", "reason": "Aktif/bekleyen işlem mevcut"}

            # Watchlist'e ekle (SIGNAL dahil — hepsi 15dk izleme sonrası açılır)
            reason = "Tüm gate'ler geçti, 15dk izleme başladı" if action == "SIGNAL" else signal_result.get("watch_reason", "Gate teyit bekleniyor")
            # SIGNAL (tamamlanmış) → 3×5m = 15dk; WATCH (eksik gate) → 9×5m = 45dk bekleme süresi
            max_watch_candles = WATCH_CONFIRM_CANDLES if action == "SIGNAL" else WATCH_CONFIRM_CANDLES * 3
            try:
                wl_id = add_to_watchlist(
                    symbol=symbol,
                    direction=direction,
                    potential_entry=signal_result.get("entry") or signal_result.get("potential_entry"),
                    potential_sl=signal_result.get("sl") or signal_result.get("potential_sl"),
                    potential_tp=signal_result.get("tp") or signal_result.get("potential_tp"),
                    watch_reason=reason,
                    initial_score=100 if action == "SIGNAL" else 60,
                    components=signal_result.get("components", []),
                    max_watch=max_watch_candles,
                )
                if wl_id:
                    logger.info(f"👁️ İZLEMEYE ALINDI: {symbol} ({direction}) — {reason}")
                    return {"status": "WATCHING", "symbol": symbol, "direction": direction, "reason": reason}
            except Exception as e:
                logger.error(f"Watchlist ekleme hatası ({symbol}): {e}")
            return None

        return None

    # =================================================================
    #  İŞLEM AÇMA — Sadece Risk Yönetimi Kontrolleri
    # =================================================================

    def _open_trade(self, signal):
        """
        Yeni işlem aç.

        ★ Puanlama kapısı YOK (strateji motoru 5 gate'i zaten geçirdi).
        ★ Tier kapısı YOK (tüm sinyaller A+ = 5/5 gate).
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
            if s["symbol"] == symbol and s["status"] in ("ACTIVE", "WAITING"):
                logger.info(f"⏭️ {symbol} için zaten aktif/bekleyen işlem var, atlanıyor")
                return {"status": "REJECTED", "reason": "Aktif/bekleyen işlem mevcut"}

        # ══ AYNI YÖNDE MAX İŞLEM ══
        max_same_dir = int(self._param("max_same_direction_trades") or 2)
        same_dir_count = sum(
            1 for s in active_signals
            if s.get("direction") == direction and s["status"] in ("ACTIVE", "WAITING")
        )
        if same_dir_count >= max_same_dir:
            logger.warning(f"⛔ {symbol} reddedildi: Aynı yönde ({direction}) max {max_same_dir} işlem limiti")
            return {"status": "REJECTED", "reason": f"Max {direction} işlem limiti ({max_same_dir})"}

        # ══ COOLDOWN KONTROLÜ ══
        recent_history = get_signal_history(30)
        cooldown_minutes = int(self._param("signal_cooldown_minutes") or 30)
        now = datetime.now()
        for s in recent_history:
            if s["symbol"] == symbol:
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

        # ══ ENTRY MODU ══
        entry_mode = signal.get("entry_mode", "MARKET")
        if entry_mode == "PENDING":
            entry_mode = "MARKET"

        initial_status = "WAITING" if entry_mode == "LIMIT" else "ACTIVE"

        # Giriş notları
        components = signal.get("components", [])
        entry_reasons = (
            f"Mode: {entry_mode} | "
            f"RR: {signal.get('rr_ratio', '?')} | "
            f"HTF: {signal.get('htf_bias', '?')} | "
            f"Session: {signal.get('session', '')} | "
            f"Entry: {signal.get('entry_type', '?')} | "
            f"SL: {signal.get('sl_type', '?')} | "
            f"TP: {signal.get('tp_type', '?')} | "
            f"Gates: {', '.join(components)}"
        )

        signal_id = add_signal(
            symbol=symbol,
            direction=direction,
            entry_price=signal["entry"],
            stop_loss=signal["sl"],
            take_profit=signal["tp"],
            confidence=signal.get("confidence", 100),
            confluence_score=signal.get("confluence_score", 100),
            components=components,
            timeframe="15m",
            status=initial_status,
            notes=entry_reasons,
            entry_mode=entry_mode,
            htf_bias=signal.get("htf_bias"),
            rr_ratio=signal.get("rr_ratio")
        )

        if initial_status == "ACTIVE":
            activate_signal(signal_id)
            logger.info(
                f"✅ İŞLEM AÇILDI (MARKET): #{signal_id} {symbol} {direction} | "
                f"Entry: {signal['entry']} | SL: {signal['sl']} | TP: {signal['tp']} | "
                f"RR: {signal.get('rr_ratio', '?')}"
            )
        else:
            logger.info(
                f"⏳ LİMİT EMİR KURULDU: #{signal_id} {symbol} {direction} | "
                f"FVG Entry: {signal['entry']} | SL: {signal['sl']} | TP: {signal['tp']} | "
                f"RR: {signal.get('rr_ratio', '?')} | Max bekle: {LIMIT_ORDER_EXPIRY_HOURS}h"
            )

        return {
            "status": "OPENED" if initial_status == "ACTIVE" else "LIMIT_PLACED",
            "signal_id": signal_id,
            "symbol": symbol,
            "direction": direction,
            "entry": signal["entry"],
            "sl": signal["sl"],
            "tp": signal["tp"],
            "entry_mode": entry_mode,
        }

    # =================================================================
    #  AÇIK İŞLEM TAKİBİ (WAITING → ACTIVE + SL/TP)
    # =================================================================

    def check_open_trades(self):
        """
        Açık ve bekleyen işlemleri kontrol et.

        1. WAITING → Fiyat FVG entry'ye ulaştı mı? → ACTIVE
        2. ACTIVE → SL/TP takibi + Breakeven/Trailing SL
        """
        active_signals = get_active_signals()
        results = []

        for signal in active_signals:
            symbol = signal["symbol"]
            ticker = data_fetcher.get_ticker(symbol)
            if not ticker:
                continue

            current_price = ticker["last"]
            entry_price = signal["entry_price"]
            stop_loss = signal["stop_loss"]
            take_profit = signal["take_profit"]
            direction = signal["direction"]
            signal_id = signal["id"]
            status = signal["status"]

            if status == "WAITING":
                result = self._check_waiting_signal(
                    signal, current_price, entry_price, stop_loss,
                    direction, signal_id
                )
                if result:
                    results.append(result)
                continue

            if status == "ACTIVE":
                result = self._check_active_signal(
                    signal, current_price, entry_price, stop_loss,
                    take_profit, direction, signal_id
                )
                if result:
                    results.append(result)

        return results

    def _check_waiting_signal(self, signal, current_price, entry_price,
                               stop_loss, direction, signal_id):
        """
        WAITING (limit emir) kontrol.

        Fiyat FVG entry'ye geldi mi? Zaman aşımı? SL ihlali?
        """
        symbol = signal["symbol"]

        # ══ ZAMAN AŞIMI ══
        created_at = signal.get("created_at", "")
        if created_at:
            try:
                created_dt = datetime.fromisoformat(created_at)
                elapsed_hours = (datetime.now() - created_dt).total_seconds() / 3600
                if elapsed_hours > LIMIT_ORDER_EXPIRY_HOURS:
                    update_signal_status(signal_id, "CANCELLED", close_price=current_price, pnl_pct=0)
                    logger.info(f"⏰ LİMİT EMİR ZAMAN AŞIMI: #{signal_id} {symbol} ({elapsed_hours:.1f}h)")
                    return {
                        "signal_id": signal_id, "symbol": symbol,
                        "direction": direction, "status": "CANCELLED",
                        "reason": "Limit emir zaman aşımı",
                    }
            except Exception:
                pass

        # ══ SL İHLALİ (entry olmadan) ══
        if direction == "LONG" and current_price <= stop_loss:
            update_signal_status(signal_id, "CANCELLED", close_price=current_price, pnl_pct=0)
            logger.info(f"❌ LİMİT İPTAL: #{signal_id} {symbol} LONG | Fiyat SL'ye ulaştı")
            return {
                "signal_id": signal_id, "symbol": symbol,
                "direction": direction, "status": "CANCELLED",
                "reason": "SL ihlali (entry olmadan)",
            }
        elif direction == "SHORT" and current_price >= stop_loss:
            update_signal_status(signal_id, "CANCELLED", close_price=current_price, pnl_pct=0)
            logger.info(f"❌ LİMİT İPTAL: #{signal_id} {symbol} SHORT | Fiyat SL'ye ulaştı")
            return {
                "signal_id": signal_id, "symbol": symbol,
                "direction": direction, "status": "CANCELLED",
                "reason": "SL ihlali (entry olmadan)",
            }

        # ══ TP GEÇİLMİŞ (Setup artık geçersiz) ══
        # ICT mantığı: displacement → pullback (FVG) → devam
        # TP'ye pullback beklenmeden ulaşıldıysa setup bozulmuş demektir
        take_profit = signal.get("take_profit", 0)
        if take_profit:
            if direction == "LONG" and current_price >= take_profit:
                update_signal_status(signal_id, "CANCELLED", close_price=current_price, pnl_pct=0)
                move_pct = ((current_price - entry_price) / entry_price) * 100
                logger.info(f"⏭️ TP GEÇİLDİ (entry olmadan): #{signal_id} {symbol} LONG (+{move_pct:.2f}%) → iptal")
                return {
                    "signal_id": signal_id, "symbol": symbol,
                    "direction": direction, "status": "CANCELLED",
                    "reason": f"TP seviyesi geçildi (entry olmadan +{move_pct:.2f}%)",
                }
            elif direction == "SHORT" and current_price <= take_profit:
                update_signal_status(signal_id, "CANCELLED", close_price=current_price, pnl_pct=0)
                move_pct = ((entry_price - current_price) / entry_price) * 100
                logger.info(f"⏭️ TP GEÇİLDİ (entry olmadan): #{signal_id} {symbol} SHORT (-{move_pct:.2f}%) → iptal")
                return {
                    "signal_id": signal_id, "symbol": symbol,
                    "direction": direction, "status": "CANCELLED",
                    "reason": f"TP seviyesi geçildi (entry olmadan -{move_pct:.2f}%)",
                }

        # ══ FİYAT FVG ENTRY'YE ULAŞTI MI? ══
        entry_buffer = entry_price * 0.0005  # %0.05 buffer (eskiden %0.2 → erken tetikleme)

        if direction == "LONG" and current_price <= entry_price + entry_buffer:
            activate_signal(signal_id)
            logger.info(f"🎯 LİMİT GERÇEKLEŞTİ: #{signal_id} {symbol} LONG @ {current_price:.8f}")
            return {
                "signal_id": signal_id, "symbol": symbol,
                "direction": direction, "status": "ACTIVATED",
                "current_price": current_price,
            }
        elif direction == "SHORT" and current_price >= entry_price - entry_buffer:
            activate_signal(signal_id)
            logger.info(f"🎯 LİMİT GERÇEKLEŞTİ: #{signal_id} {symbol} SHORT @ {current_price:.8f}")
            return {
                "signal_id": signal_id, "symbol": symbol,
                "direction": direction, "status": "ACTIVATED",
                "current_price": current_price,
            }

        return None

    def _check_active_signal(self, signal, current_price, entry_price,
                              stop_loss, take_profit, direction, signal_id):
        """
        ACTIVE sinyal SL/TP takibi + Breakeven/Trailing SL.
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
                    if direction == "LONG":
                        pnl_pct = ((current_price - entry_price) / entry_price) * 100
                    else:
                        pnl_pct = ((entry_price - current_price) / entry_price) * 100
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
            "breakeven_sl": None,
            "early_protect_sl": None,
        })
        is_be_trade = state.get("breakeven_moved", False)

        # ══ TEMEL SEVİYE DOĞRULAMA ══
        # ★ BE/non-BE fark etmez: TP her zaman SL'nin "öbür tarafında" olmalı
        # LONG: TP > SL (kâr hedefi stop üstünde)
        # SHORT: TP < SL (kâr hedefi stop altında)
        structurally_valid = True
        if direction == "LONG" and take_profit <= stop_loss:
            structurally_valid = False
        elif direction == "SHORT" and take_profit >= stop_loss:
            structurally_valid = False

        if not structurally_valid:
            logger.warning(f"⚠️ #{signal_id} {symbol} {direction} yapısal olarak bozuk (TP vs SL ters) — iptal")
            update_signal_status(signal_id, "CANCELLED", close_price=current_price, pnl_pct=0)
            self._trade_state.pop(signal_id, None)
            result["status"] = "CANCELLED"
            return result

        # Seviye doğrulama (ters SL/TP kontrolü — BE trade'lerde SL entry tarafı atlanır)
        if direction == "LONG" and not is_be_trade and (stop_loss >= entry_price or take_profit <= entry_price):
            logger.warning(f"⚠️ #{signal_id} {symbol} LONG ters seviyeler — iptal")
            update_signal_status(signal_id, "CANCELLED", close_price=current_price, pnl_pct=0)
            self._trade_state.pop(signal_id, None)
            result["status"] = "CANCELLED"
            return result
        elif direction == "SHORT" and not is_be_trade and (stop_loss <= entry_price or take_profit >= entry_price):
            logger.warning(f"⚠️ #{signal_id} {symbol} SHORT ters seviyeler — iptal")
            update_signal_status(signal_id, "CANCELLED", close_price=current_price, pnl_pct=0)
            self._trade_state.pop(signal_id, None)
            result["status"] = "CANCELLED"
            return result

        effective_sl = stop_loss

        if direction == "LONG":
            effective_sl = self._manage_long_sl(
                signal_id, symbol, entry_price, current_price,
                stop_loss, take_profit, state, effective_sl
            )
            if current_price >= take_profit:
                pnl_pct = ((current_price - entry_price) / entry_price) * 100
                update_signal_status(signal_id, "WON", close_price=current_price, pnl_pct=pnl_pct)
                self._trade_state.pop(signal_id, None)
                result["status"] = "WON"
                result["pnl_pct"] = round(pnl_pct, 2)
                logger.info(f"🏆 KAZANDIK: #{signal_id} {symbol} LONG | PnL: +{pnl_pct:.2f}%")
            elif current_price <= effective_sl:
                raw_pnl = ((current_price - entry_price) / entry_price) * 100
                max_sl_loss = ((effective_sl - entry_price) / entry_price) * 100
                if raw_pnl < 0 and raw_pnl < max_sl_loss - 0.5:
                    pnl_pct = max_sl_loss - 0.5
                    logger.warning(f"⚠️ SLIPPAGE: #{signal_id} {symbol} | {raw_pnl:.2f}% → {pnl_pct:.2f}%")
                else:
                    pnl_pct = raw_pnl
                sl_type = self._get_sl_close_type(state)
                status = "WON" if pnl_pct > 0 else "LOST"
                update_signal_status(signal_id, status, close_price=current_price, pnl_pct=pnl_pct)
                self._trade_state.pop(signal_id, None)
                result["status"] = status
                result["pnl_pct"] = round(pnl_pct, 2)
                emoji = "🏆" if pnl_pct > 0 else "❌"
                logger.info(f"{emoji} {sl_type}: #{signal_id} {symbol} LONG | PnL: {pnl_pct:+.2f}%")
            else:
                unrealized = ((current_price - entry_price) / entry_price) * 100
                result["unrealized_pnl"] = round(unrealized, 2)
                if state.get("breakeven_moved") or state.get("trailing_sl"):
                    result["effective_sl"] = round(effective_sl, 8)

        elif direction == "SHORT":
            effective_sl = self._manage_short_sl(
                signal_id, symbol, entry_price, current_price,
                stop_loss, take_profit, state, effective_sl
            )
            if current_price <= take_profit:
                pnl_pct = ((entry_price - current_price) / entry_price) * 100
                update_signal_status(signal_id, "WON", close_price=current_price, pnl_pct=pnl_pct)
                self._trade_state.pop(signal_id, None)
                result["status"] = "WON"
                result["pnl_pct"] = round(pnl_pct, 2)
                logger.info(f"🏆 KAZANDIK: #{signal_id} {symbol} SHORT | PnL: +{pnl_pct:.2f}%")
            elif current_price >= effective_sl:
                raw_pnl = ((entry_price - current_price) / entry_price) * 100
                max_sl_loss = ((entry_price - effective_sl) / entry_price) * 100
                if raw_pnl < 0 and raw_pnl < max_sl_loss - 0.5:
                    pnl_pct = max_sl_loss - 0.5
                    logger.warning(f"⚠️ SLIPPAGE: #{signal_id} {symbol} | {raw_pnl:.2f}% → {pnl_pct:.2f}%")
                else:
                    pnl_pct = raw_pnl
                sl_type = self._get_sl_close_type(state)
                status = "WON" if pnl_pct > 0 else "LOST"
                update_signal_status(signal_id, status, close_price=current_price, pnl_pct=pnl_pct)
                self._trade_state.pop(signal_id, None)
                result["status"] = status
                result["pnl_pct"] = round(pnl_pct, 2)
                emoji = "🏆" if pnl_pct > 0 else "❌"
                logger.info(f"{emoji} {sl_type}: #{signal_id} {symbol} SHORT | PnL: {pnl_pct:+.2f}%")
            else:
                unrealized = ((entry_price - current_price) / entry_price) * 100
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
    #  BREAKEVEN / TRAILING SL
    # =================================================================

    def _manage_long_sl(self, signal_id, symbol, entry_price, current_price,
                         stop_loss, take_profit, state, effective_sl):
        """LONG: Progresif Breakeven + Trailing SL."""
        total_distance = take_profit - entry_price
        current_progress = current_price - entry_price

        if total_distance > 0 and current_progress > 0:
            progress_pct = current_progress / total_distance

            if progress_pct >= 0.60:
                trailing = entry_price + (current_progress * 0.50)
                if state.get("trailing_sl") is None or trailing > state["trailing_sl"]:
                    state["trailing_sl"] = trailing
                    effective_sl = max(effective_sl, trailing)
                    if not state.get("trailing_logged"):
                        logger.info(f"📈 #{signal_id} {symbol} TRAILING: {trailing:.6f} ({progress_pct:.0%})")
                        state["trailing_logged"] = True

            elif progress_pct >= 0.40 and not state.get("breakeven_moved"):
                state["breakeven_moved"] = True
                be_sl = entry_price * 1.001
                state["breakeven_sl"] = be_sl
                effective_sl = be_sl
                logger.info(f"🔒 #{signal_id} {symbol} BREAKEVEN: SL → {effective_sl:.6f} ({progress_pct:.0%})")

            elif progress_pct >= 0.25 and not state.get("early_protect"):
                state["early_protect"] = True
                early_sl = entry_price * 0.998
                if early_sl > stop_loss:
                    effective_sl = early_sl
                    state["early_protect_sl"] = early_sl
                    logger.info(f"🛡️ #{signal_id} {symbol} ERKEN KORUMA: SL → {effective_sl:.6f}")

        # En iyi SL seviyesini kullan
        if state.get("trailing_sl"):
            effective_sl = max(effective_sl, state["trailing_sl"])
        if state.get("breakeven_sl"):
            effective_sl = max(effective_sl, state["breakeven_sl"])
        if state.get("early_protect_sl"):
            effective_sl = max(effective_sl, state["early_protect_sl"])

        return effective_sl

    def _manage_short_sl(self, signal_id, symbol, entry_price, current_price,
                          stop_loss, take_profit, state, effective_sl):
        """SHORT: Progresif Breakeven + Trailing SL."""
        total_distance = entry_price - take_profit
        current_progress = entry_price - current_price

        if total_distance > 0 and current_progress > 0:
            progress_pct = current_progress / total_distance

            if progress_pct >= 0.60:
                trailing = entry_price - (current_progress * 0.50)
                if state.get("trailing_sl") is None or trailing < state["trailing_sl"]:
                    state["trailing_sl"] = trailing
                    effective_sl = min(effective_sl, trailing)
                    if not state.get("trailing_logged"):
                        logger.info(f"📉 #{signal_id} {symbol} TRAILING: {trailing:.6f} ({progress_pct:.0%})")
                        state["trailing_logged"] = True

            elif progress_pct >= 0.40 and not state.get("breakeven_moved"):
                state["breakeven_moved"] = True
                be_sl = entry_price * 0.999
                state["breakeven_sl"] = be_sl
                effective_sl = be_sl
                logger.info(f"🔒 #{signal_id} {symbol} BREAKEVEN: SL → {effective_sl:.6f} ({progress_pct:.0%})")

            elif progress_pct >= 0.25 and not state.get("early_protect"):
                state["early_protect"] = True
                early_sl = entry_price * 1.002
                if early_sl < stop_loss:
                    effective_sl = early_sl
                    state["early_protect_sl"] = early_sl
                    logger.info(f"🛡️ #{signal_id} {symbol} ERKEN KORUMA: SL → {effective_sl:.6f}")

        if state.get("trailing_sl"):
            effective_sl = min(effective_sl, state["trailing_sl"])
        if state.get("breakeven_sl"):
            effective_sl = min(effective_sl, state["breakeven_sl"])
        if state.get("early_protect_sl"):
            effective_sl = min(effective_sl, state["early_protect_sl"])

        return effective_sl

    def _get_sl_close_type(self, state):
        """SL kapanış tipini belirle."""
        if state.get("trailing_sl"):
            return "TRAILING_SL"
        elif state.get("breakeven_moved"):
            return "BREAKEVEN"
        return "STRUCTURAL_SL"

    # =================================================================
    #  İZLEME LİSTESİ (Basitleştirilmiş)
    # =================================================================

    def _validate_completed_setup(self, symbol, item, ltf_df, multi_tf):
        """
        Tamamlanmış setup için basit invalidation check.
        Gate'leri tekrar kontrol ETMEZ — sadece:
          1. SL tetiklendi mi?
          2. HTF bias değişti mi?
        
        Returns:
            bool: Setup hala valid mi?
        """
        direction = item["direction"]
        potential_sl = item.get("potential_sl")
        
        if not potential_sl or ltf_df is None or ltf_df.empty:
            return False
        
        # Son fiyat
        last_candle = ltf_df.iloc[-1]
        current_high = last_candle.get("high", 0)
        current_low = last_candle.get("low", 0)
        
        # SL invalidation check
        if direction == "LONG":
            if current_low <= potential_sl:
                logger.debug(f"  {symbol} LONG setup invalidated: SL {potential_sl:.5f} tetiklendi (low={current_low:.5f})")
                return False
        else:  # SHORT
            if current_high >= potential_sl:
                logger.debug(f"  {symbol} SHORT setup invalidated: SL {potential_sl:.5f} tetiklendi (high={current_high:.5f})")
                return False
        
        # HTF bias check (opsiyonel - strict değil)
        if multi_tf and "4h" in multi_tf:
            df_4h = multi_tf["4h"]
            if not df_4h.empty and len(df_4h) >= 20:
                ema_20 = df_4h["close"].iloc[-20:].mean()
                current_price = df_4h["close"].iloc[-1]
                
                bias_changed = False
                if direction == "LONG" and current_price < ema_20:
                    bias_changed = True
                elif direction == "SHORT" and current_price > ema_20:
                    bias_changed = True
                
                if bias_changed:
                    logger.debug(f"  {symbol} HTF bias değişti (setup geçersiz)")
                    return False
        
        return True

    def check_watchlist(self, strategy_engine):
        """
        15 dakikalık mum bazlı izleme listesi kontrolü — v3.5 hybrid validation.

        Akış:
          1. ICT setup bulundu → watchlist'e alındı
          2. 1 × 15m mum kapanışı beklenir (15dk)
          3. Mum kapandığında analiz:
             a) Setup tamamlanmışsa ("Tüm gate'ler geçti"):
                → Gate'leri TEKRAR CHECK ETME
                → Sadece SL/HTF invalidation check
             b) Setup henüz tamamlanmamışsa ("Gate4/5 bekleniyor"):
                → Normal gate validation (generate_signal)
          
        v3.5 Değişiklikler (HYBRID VALIDATION):
        - Setup tamamlandıysa → displacement/FVG tekrar aranmaz
        - ICT mantığı: displacement geçmişte oluştu, kaybolması normal
        - Sadece invalidation (SL/HTF) check edilir
        - v3.4'teki %100 expire sorunu çözüldü
        """
        watching_items = get_watching_items()
        promoted = []

        for item in watching_items:
            symbol = item["symbol"]
            candles_watched = int(item.get("candles_watched", 0))
            max_watch = item.get("max_watch_candles", WATCH_CONFIRM_CANDLES)
            stored_ts = item.get("last_5m_candle_ts") or ""  # Son görülen 5m mum timestamp'i

            # 5m veri çek — 3 mum = 15dk izleme (v3.6: 15m→5m)
            try:
                df_ltf = data_fetcher.get_candles(symbol, WATCH_CONFIRM_TIMEFRAME, 15)
            except Exception as e:
                logger.debug(f"Watchlist {WATCH_CONFIRM_TIMEFRAME} veri hatası ({symbol}): {e}")
                continue

            if df_ltf is None or df_ltf.empty:
                continue

            # Son kapanmış 5m mum timestamp'i (iloc kullan, index RangeIndex olabilir)
            current_ts = str(df_ltf.iloc[-1]["timestamp"])

            # Aynı mum → henüz yeni mum kapanmadı, atla
            if current_ts == stored_ts:
                continue

            # Yeni 5m mum kapandı → sayacı artır (3 mum = 15dk)
            candles_watched += 1
            logger.info(f"📊 {symbol} yeni 5m mum ({candles_watched}/{max_watch})")

            # 5m verisi ve multi-TF verisi ile yeniden analiz et
            try:
                multi_tf = data_fetcher.get_multi_timeframe_data(symbol)
                ltf_df = df_ltf  # 5m veri
            except Exception as e:
                logger.debug(f"Watchlist veri hatası ({symbol}): {e}")
                update_watchlist_item(item["id"], candles_watched, 0,
                                     last_5m_candle_ts=current_ts)
                continue

            if ltf_df is None or ltf_df.empty or multi_tf is None:
                update_watchlist_item(item["id"], candles_watched, 0,
                                     last_5m_candle_ts=current_ts)
                continue

            # v3.7 HYBRID VALIDATION:
            # TAMAMLANMIŞ setup → sadece SL/HTF invalidasyon check
            # EKSİK setup (Gate4/Gate5 bekleniyor) → SL check + gate tamamlandı mı diye bak,
            #   gate hâlâ eksikse expire ETME, beklemeye devam et; sadece SL/timeout'ta expire et
            watch_reason = item.get("watch_reason", "")
            signal_result = None

            if "Tüm gate'ler geçti" in watch_reason:
                # ── TAMAMLANMIŞ SETUP ──────────────────────────────────────
                # Sadece SL tetiklendi mi / HTF bias değişti mi kontrol et
                if not self._validate_completed_setup(symbol, item, ltf_df, multi_tf):
                    expire_watchlist_item(
                        item["id"],
                        reason=f"Setup invalidated (SL/HTF) - {candles_watched}. 5m mum"
                    )
                    logger.info(f"❌ SETUP INVALIDATED: {symbol} (SL veya HTF bias değişti)")
                    continue
            else:
                # ── EKSİK SETUP (Gate4/Gate5 bekleniyor) ──────────────────
                # 1. Önce SL kontrolü — fiyat SL'yi geçti mi?
                if not self._validate_completed_setup(symbol, item, ltf_df, multi_tf):
                    expire_watchlist_item(
                        item["id"],
                        reason=f"SL kırıldı - {candles_watched}. 5m mum"
                    )
                    logger.info(f"❌ SL KIRILDI (eksik setup): {symbol}")
                    continue

                # 2. Gate'ler tamamlandı mı kontrol et
                signal_result = strategy_engine.generate_signal(symbol, ltf_df, multi_tf)
                gates_complete = (
                    signal_result is not None
                    and signal_result.get("action") in ("SIGNAL", "WATCH")
                )

                if gates_complete:
                    # Gate'ler tamamlandı → watch_reason güncelle, entry/sl/tp güncelle
                    new_entry = signal_result.get("entry") or item.get("potential_entry")
                    new_sl    = signal_result.get("sl")    or item.get("potential_sl")
                    new_tp    = signal_result.get("tp")    or item.get("potential_tp")
                    _execute(
                        "UPDATE watchlist SET watch_reason=?, potential_entry=?, potential_sl=?, potential_tp=?, updated_at=datetime('now') WHERE id=?",
                        ("Tüm gate'ler geçti, 15dk izleme başladı", new_entry, new_sl, new_tp, item["id"])
                    )
                    logger.info(f"✅ GATE'LER TAMAMLANDI: {symbol} — şimdi 3×5m izlemeye başlıyor")
                    # candles_watched sıfırlanmıyor, sayım devam eder
                else:
                    # Gate'ler hâlâ eksik → expire ETME, beklemeye devam
                    if candles_watched >= max_watch:
                        expire_watchlist_item(
                            item["id"],
                            reason=f"Timeout - {candles_watched} mum beklendu, gate tamamlanmadı"
                        )
                        logger.info(f"⏰ TIMEOUT: {symbol} ({candles_watched} mum, gate tamamlanmadı)")
                    else:
                        update_watchlist_item(item["id"], candles_watched, 0,
                                             last_5m_candle_ts=current_ts)
                        logger.info(f"⏳ {symbol} gate bekleniyor ({candles_watched}/{max_watch})")
                    continue

            # 3 mum doldu ve setup hâlâ geçerli → PROMOTE → işlem aç
            if candles_watched >= max_watch:
                promote_watchlist_item(item["id"])
                logger.info(f"✅ 15dk İZLEME TAMAM (3×5m): {symbol} — setup hâlâ geçerli, işlem açılıyor")

                # v3.5: signal_result None olabilir (tamamlanmış setup için)
                if signal_result and signal_result.get("action") == "SIGNAL":
                    trade_signal = signal_result
                else:
                    # Watchlist verilerinden trade bilgilerini al
                    trade_signal = {
                        "symbol": symbol,
                        "direction": item["direction"],
                        "entry": item.get("potential_entry"),
                        "sl": item.get("potential_sl"),
                        "tp": item.get("potential_tp"),
                        "entry_mode": "LIMIT",  # v3.4: Her zaman LIMIT
                        "rr_ratio": signal_result.get("rr_ratio", "?") if signal_result else "?",
                        "components": signal_result.get("components", []) if signal_result else [],
                        "htf_bias": signal_result.get("htf_bias", "") if signal_result else "",
                        "session": signal_result.get("session", "") if signal_result else "",
                        "entry_type": signal_result.get("entry_type", "ICT_WATCH") if signal_result else "ICT_WATCH",
                        "sl_type": signal_result.get("sl_type", "") if signal_result else "",
                        "tp_type": signal_result.get("tp_type", "") if signal_result else "",
                    }

                trade_result = self._open_trade(trade_signal)
                if trade_result and trade_result.get("status") != "REJECTED":
                    promoted.append({
                        "symbol": symbol,
                        "action": "PROMOTED",
                        "trade_result": trade_result,
                    })
                    logger.info(f"⬆️ İZLEMEDEN AKTİF SİNYALE: {symbol} (3×5m / 15dk izleme sonrası)")
                continue

            # Henüz 1 mum dolmadı, setup geçerli → izlemeye devam
            update_watchlist_item(item["id"], candles_watched, 0,
                                 last_5m_candle_ts=current_ts)

        return promoted


# Global instance
trade_manager = TradeManager()
