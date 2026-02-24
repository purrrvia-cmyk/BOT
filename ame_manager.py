# =====================================================
# AME Trade Manager v2.0 — 3-Level Partial TP + Session-Aware
# =====================================================
#
# Kademeli çıkış stratejisi:
#   TP1 (1R)   → %40 kapat, SL → breakeven
#   TP2 (2R)   → %30 kapat, SL → TP1 seviyesi
#   TP3 (3.5R) → Trailing ile %30 kapat
#
# v1.0'dan farklar:
#   - 3 kademeli TP (tek TP yerine)
#   - Breakeven SL after TP1
#   - Trailing stop after TP2
#   - Birikimli PnL hesabı
#   - Session-aware risk
# =====================================================

import logging
from datetime import datetime, timezone, timedelta
from database import (
    add_ame_signal,
    update_ame_signal_status,
    get_ame_active_signals,
    get_ame_active_trade_count,
    get_ame_performance_summary,
)
from ame_strategy import ame_strategy
from data_fetcher import data_fetcher

logger = logging.getLogger("AME.TradeManager")

AME_PARAMS = {
    "max_concurrent": 5,
    "max_same_direction": 3,
    "cooldown_minutes": 10,
    "max_duration_hours": 8,
    # Partial TP sizing
    "tp1_rr": 1.0,
    "tp2_rr": 2.0,
    "tp3_rr": 3.5,
    "tp1_size": 0.40,
    "tp2_size": 0.30,
    "tp3_size": 0.30,
    # Trailing
    "trailing_lock_pct": 0.50,
    # SL management
    "sl_breakeven_after_tp1": True,
    "sl_move_to_tp1_after_tp2": True,
    # Kelly position sizing
    "kelly_enabled": True,
    "kelly_fraction": 0.25,       # Use 25% of Kelly (quarter-Kelly for safety)
    "min_position_pct": 1.0,      # Min position size %
    "max_position_pct": 5.0,      # Max position size %
    "default_position_pct": 2.0,  # Default when insufficient data
    # Performance-adaptive
    "adaptive_enabled": True,
    "adaptive_lookback": 20,       # Last N trades for adaptation
    "adaptive_min_trades": 5,      # Min trades before adapting
}


class AMETradeManager:
    """
    AME v2.0 Trade Manager — 3-Level Partial TP + Trailing

    Trade lifecycle:
      1. Signal → open (full size)
      2. TP1 hit → close 40%, SL → breakeven
      3. TP2 hit → close 30%, SL → TP1, trailing active
      4. TP3 hit OR trailing SL → close remaining 30%
    """

    def __init__(self):
        self._trade_state = {}  # {signal_id: {tp1_hit, tp2_hit, accumulated_pnl, ...}}

    # ───────────────────────────────────────────
    #  OPEN TRADE
    # ───────────────────────────────────────────

    def process_signal(self, signal):
        """Yeni sinyal → işlem açma kontrolü."""
        if not signal:
            return None

        symbol = signal.get("symbol", "")
        direction = signal.get("direction", "")

        # ── Limit kontrolleri ──
        active = get_ame_active_signals()

        if len(active) >= AME_PARAMS["max_concurrent"]:
            logger.warning(f"⛔ AME max eşzamanlı işlem limiti ({AME_PARAMS['max_concurrent']})")
            return None

        same_dir = sum(1 for a in active if a.get("direction") == direction)
        if same_dir >= AME_PARAMS["max_same_direction"]:
            logger.warning(f"⛔ AME max aynı yön limiti ({direction}: {same_dir})")
            return None

        # Duplicate check
        if any(a.get("symbol") == symbol for a in active):
            return None

        # Cooldown
        for a in active:
            if a.get("symbol") == symbol:
                created = a.get("created_at", "")
                if created:
                    try:
                        ct = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                        if datetime.now(timezone.utc) - ct < timedelta(minutes=AME_PARAMS["cooldown_minutes"]):
                            return None
                    except Exception:
                        pass

        # ── Open trade in DB ──
        entry = signal.get("entry", signal.get("entry_price", 0))
        sl = signal.get("sl", signal.get("stop_loss", 0))
        tp3 = signal.get("tp3", signal.get("tp", signal.get("take_profit", 0)))

        signal_id = add_ame_signal(
            symbol=symbol,
            direction=direction,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp3,
            score=signal.get("score", 0),
            rr_ratio=signal.get("rr_ratio", 0),
            regime=signal.get("regime", ""),
            mode=signal.get("mode", "balanced"),
            impulse_score=signal.get("impulse_score", 0),
            velocity=signal.get("velocity", 0),
        )

        if not signal_id:
            return None

        # ── Initialize partial TP state ──
        tp1 = signal.get("tp1")
        tp2 = signal.get("tp2")
        if tp1 is None or tp2 is None:
            risk = abs(entry - sl)
            if direction == "LONG":
                tp1 = tp1 or entry + risk * AME_PARAMS["tp1_rr"]
                tp2 = tp2 or entry + risk * AME_PARAMS["tp2_rr"]
            else:
                tp1 = tp1 or entry - risk * AME_PARAMS["tp1_rr"]
                tp2 = tp2 or entry - risk * AME_PARAMS["tp2_rr"]

        self._trade_state[signal_id] = {
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "tp1_hit": False,
            "tp2_hit": False,
            "accumulated_pnl": 0.0,
            "remaining_size": 1.0,
            "effective_sl": sl,
            "original_sl": sl,
            "trailing_active": False,
        }

        # ── Kelly position sizing ──
        kelly = self.calc_kelly_size() if AME_PARAMS["kelly_enabled"] else None
        position_pct = kelly["position_pct"] if kelly and kelly["sufficient_data"] else AME_PARAMS["default_position_pct"]

        result = {
            "id": signal_id,
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry,
            "stop_loss": sl,
            "take_profit": tp3,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "score": signal.get("score", 0),
            "status": "OPENED",
            "position_pct": round(position_pct, 2),
            "kelly_full": kelly["kelly_full"] if kelly else 0,
        }

        logger.info(
            f"✅ AME v2.1 İŞLEM: #{signal_id} {symbol} {direction} | "
            f"Entry: {entry:.4f} | SL: {sl:.4f} | "
            f"TP1: {tp1:.4f} TP2: {tp2:.4f} TP3: {tp3:.4f} | "
            f"Score: {signal.get('score', 0):.0f} | Pos: {position_pct:.1f}%"
        )

        return result

    # ───────────────────────────────────────────
    #  CHECK OPEN TRADES
    # ───────────────────────────────────────────

    def check_open_trades(self):
        """Tüm açık AME işlemlerini kontrol et."""
        active = get_ame_active_signals()
        results = []
        for signal in active:
            try:
                r = self._check_trade(signal)
                if r:
                    results.append(r)
            except Exception as e:
                logger.error(f"AME trade check hatası #{signal.get('id')}: {e}")
        return results

    def _init_state(self, signal):
        """Trade state'i initialize et (restart recovery dahil)."""
        sid = signal["id"]
        if sid not in self._trade_state:
            entry = signal["entry_price"]
            sl = signal["stop_loss"]
            tp = signal["take_profit"]
            d = signal["direction"]

            risk = abs(entry - sl)
            if risk <= 0:
                risk = entry * 0.01  # fallback

            if d == "LONG":
                tp1 = entry + risk * AME_PARAMS["tp1_rr"]
                tp2 = entry + risk * AME_PARAMS["tp2_rr"]
            else:
                tp1 = entry - risk * AME_PARAMS["tp1_rr"]
                tp2 = entry - risk * AME_PARAMS["tp2_rr"]

            self._trade_state[sid] = {
                "tp1": tp1,
                "tp2": tp2,
                "tp3": tp,
                "tp1_hit": False,
                "tp2_hit": False,
                "accumulated_pnl": 0.0,
                "remaining_size": 1.0,
                "effective_sl": sl,
                "original_sl": sl,
                "trailing_active": False,
            }
        return self._trade_state[sid]

    def _check_trade(self, signal):
        """Tek bir AME işlemini kontrol et — partial TP + trailing."""
        sid = signal["id"]
        symbol = signal["symbol"]
        direction = signal["direction"]
        entry = signal["entry_price"]

        # Current price
        ticker = data_fetcher.get_ticker(symbol)
        if not ticker or "last" not in ticker:
            return None
        price = float(ticker["last"])

        # Get/init state
        state = self._init_state(signal)

        # ── Max duration ──
        try:
            created = signal.get("created_at", "")
            if created:
                ct = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                age_hours = (datetime.now(timezone.utc) - ct).total_seconds() / 3600
                if age_hours > AME_PARAMS["max_duration_hours"]:
                    pnl = self._calc_closing_pnl(direction, entry, price, state)
                    status = "WON" if pnl > 0 else "LOST"
                    update_ame_signal_status(sid, status, close_price=price, pnl_pct=pnl)
                    self._trade_state.pop(sid, None)
                    emoji = "⏰🏆" if pnl > 0 else "⏰❌"
                    logger.info(f"{emoji} AME SÜRE DOLDU: #{sid} {symbol} | PnL: {pnl:+.2f}%")
                    return {"id": sid, "symbol": symbol, "status": status, "pnl_pct": round(pnl, 2)}
        except Exception:
            pass

        if direction == "LONG":
            return self._check_long(sid, symbol, entry, price, state)
        else:
            return self._check_short(sid, symbol, entry, price, state)

    # ───────────────────────────────────────────
    #  LONG TRADE MANAGEMENT
    # ───────────────────────────────────────────

    def _check_long(self, sid, symbol, entry, price, state):
        """LONG trade: SL → TP1(40%) → TP2(30%) → TP3 trailing(30%)."""
        eff_sl = state["effective_sl"]
        tp1 = state["tp1"]
        tp2 = state["tp2"]
        tp3 = state["tp3"]

        # ═══ SL Check ═══
        if price <= eff_sl:
            pnl = self._calc_closing_pnl("LONG", entry, price, state)
            status = "WON" if pnl > 0 else "LOST"
            update_ame_signal_status(sid, status, close_price=price, pnl_pct=pnl)
            self._trade_state.pop(sid, None)
            emoji = "🏆" if pnl > 0 else "❌"
            tp_info = ""
            if state["tp2_hit"]:
                tp_info = " (TP2✓ sonrası)"
            elif state["tp1_hit"]:
                tp_info = " (TP1✓ sonrası)"
            logger.info(f"{emoji} AME SL: #{sid} {symbol} LONG{tp_info} | PnL: {pnl:+.2f}%")
            return {"id": sid, "symbol": symbol, "status": status, "pnl_pct": round(pnl, 2)}

        # ═══ TP1 (close 40%) ═══
        if not state["tp1_hit"] and price >= tp1:
            partial_pnl = (tp1 - entry) / entry * 100 * AME_PARAMS["tp1_size"]
            state["tp1_hit"] = True
            state["accumulated_pnl"] += partial_pnl
            state["remaining_size"] -= AME_PARAMS["tp1_size"]

            if AME_PARAMS["sl_breakeven_after_tp1"]:
                # SL slightly above entry (+5% of risk)
                state["effective_sl"] = entry + (tp1 - entry) * 0.05
            logger.info(f"🔒 AME TP1+BE: #{sid} {symbol} | +{partial_pnl:.2f}% locked, SL→{state['effective_sl']:.4f}")
            self._trade_state[sid] = state

        # ═══ TP2 (close 30%) ═══
        if state["tp1_hit"] and not state["tp2_hit"] and price >= tp2:
            partial_pnl = (tp2 - entry) / entry * 100 * AME_PARAMS["tp2_size"]
            state["tp2_hit"] = True
            state["accumulated_pnl"] += partial_pnl
            state["remaining_size"] -= AME_PARAMS["tp2_size"]

            if AME_PARAMS["sl_move_to_tp1_after_tp2"]:
                state["effective_sl"] = tp1
            state["trailing_active"] = True
            logger.info(f"📈 AME TP2: #{sid} {symbol} | +{partial_pnl:.2f}% locked, SL→{tp1:.4f}, trailing ON")
            self._trade_state[sid] = state

        # ═══ TP3 / Trailing (remaining 30%) ═══
        if state["tp2_hit"]:
            # Trailing: lock 50% of current profit as SL
            if state["trailing_active"]:
                profit = price - entry
                if profit > 0:
                    trail_sl = entry + profit * AME_PARAMS["trailing_lock_pct"]
                    if trail_sl > state["effective_sl"]:
                        state["effective_sl"] = trail_sl
                        self._trade_state[sid] = state

            # TP3 hit → close everything
            if price >= tp3:
                partial_pnl = (tp3 - entry) / entry * 100 * state["remaining_size"]
                total_pnl = state["accumulated_pnl"] + partial_pnl
                update_ame_signal_status(sid, "WON", close_price=price, pnl_pct=total_pnl)
                self._trade_state.pop(sid, None)
                logger.info(f"🏆🎯 AME TP3: #{sid} {symbol} LONG | Total PnL: {total_pnl:+.2f}%")
                return {"id": sid, "symbol": symbol, "status": "WON", "pnl_pct": round(total_pnl, 2)}

        return None

    # ───────────────────────────────────────────
    #  SHORT TRADE MANAGEMENT
    # ───────────────────────────────────────────

    def _check_short(self, sid, symbol, entry, price, state):
        """SHORT trade: SL → TP1(40%) → TP2(30%) → TP3 trailing(30%)."""
        eff_sl = state["effective_sl"]
        tp1 = state["tp1"]
        tp2 = state["tp2"]
        tp3 = state["tp3"]

        # ═══ SL Check ═══
        if price >= eff_sl:
            pnl = self._calc_closing_pnl("SHORT", entry, price, state)
            status = "WON" if pnl > 0 else "LOST"
            update_ame_signal_status(sid, status, close_price=price, pnl_pct=pnl)
            self._trade_state.pop(sid, None)
            emoji = "🏆" if pnl > 0 else "❌"
            tp_info = ""
            if state["tp2_hit"]:
                tp_info = " (TP2✓ sonrası)"
            elif state["tp1_hit"]:
                tp_info = " (TP1✓ sonrası)"
            logger.info(f"{emoji} AME SL: #{sid} {symbol} SHORT{tp_info} | PnL: {pnl:+.2f}%")
            return {"id": sid, "symbol": symbol, "status": status, "pnl_pct": round(pnl, 2)}

        # ═══ TP1 (close 40%) ═══
        if not state["tp1_hit"] and price <= tp1:
            partial_pnl = (entry - tp1) / entry * 100 * AME_PARAMS["tp1_size"]
            state["tp1_hit"] = True
            state["accumulated_pnl"] += partial_pnl
            state["remaining_size"] -= AME_PARAMS["tp1_size"]

            if AME_PARAMS["sl_breakeven_after_tp1"]:
                state["effective_sl"] = entry - (entry - tp1) * 0.05
            logger.info(f"🔒 AME TP1+BE: #{sid} {symbol} | +{partial_pnl:.2f}% locked, SL→{state['effective_sl']:.4f}")
            self._trade_state[sid] = state

        # ═══ TP2 (close 30%) ═══
        if state["tp1_hit"] and not state["tp2_hit"] and price <= tp2:
            partial_pnl = (entry - tp2) / entry * 100 * AME_PARAMS["tp2_size"]
            state["tp2_hit"] = True
            state["accumulated_pnl"] += partial_pnl
            state["remaining_size"] -= AME_PARAMS["tp2_size"]

            if AME_PARAMS["sl_move_to_tp1_after_tp2"]:
                state["effective_sl"] = tp1
            state["trailing_active"] = True
            logger.info(f"📈 AME TP2: #{sid} {symbol} | +{partial_pnl:.2f}% locked, SL→{tp1:.4f}, trailing ON")
            self._trade_state[sid] = state

        # ═══ TP3 / Trailing (remaining 30%) ═══
        if state["tp2_hit"]:
            if state["trailing_active"]:
                profit = entry - price
                if profit > 0:
                    trail_sl = entry - profit * AME_PARAMS["trailing_lock_pct"]
                    if trail_sl < state["effective_sl"]:
                        state["effective_sl"] = trail_sl
                        self._trade_state[sid] = state

            if price <= tp3:
                partial_pnl = (entry - tp3) / entry * 100 * state["remaining_size"]
                total_pnl = state["accumulated_pnl"] + partial_pnl
                update_ame_signal_status(sid, "WON", close_price=price, pnl_pct=total_pnl)
                self._trade_state.pop(sid, None)
                logger.info(f"🏆🎯 AME TP3: #{sid} {symbol} SHORT | Total PnL: {total_pnl:+.2f}%")
                return {"id": sid, "symbol": symbol, "status": "WON", "pnl_pct": round(total_pnl, 2)}

        return None

    # ───────────────────────────────────────────
    #  PnL CALCULATION
    # ───────────────────────────────────────────

    def _calc_closing_pnl(self, direction, entry, close_price, state):
        """Kalan pozisyonun PnL'ini hesapla + birikmiş kısmi PnL topla."""
        remaining = state["remaining_size"]
        if direction == "LONG":
            remaining_pnl = (close_price - entry) / entry * 100 * remaining
        else:
            remaining_pnl = (entry - close_price) / entry * 100 * remaining
        return round(state["accumulated_pnl"] + remaining_pnl, 4)

    # ───────────────────────────────────────────
    #  STATUS
    # ───────────────────────────────────────────

    def get_status(self):
        """AME trade manager durum özeti."""
        active = get_ame_active_signals()
        perf = get_ame_performance_summary()

        # Enrich with partial TP info
        enriched = []
        for a in active:
            sid = a.get("id")
            state = self._trade_state.get(sid, {})
            a["tp1_hit"] = state.get("tp1_hit", False)
            a["tp2_hit"] = state.get("tp2_hit", False)
            a["accumulated_pnl"] = round(state.get("accumulated_pnl", 0), 2)
            a["remaining_size"] = round(state.get("remaining_size", 1.0), 2)
            a["effective_sl"] = state.get("effective_sl", a.get("stop_loss"))
            a["tp1"] = state.get("tp1", 0)
            a["tp2"] = state.get("tp2", 0)
            a["tp3"] = state.get("tp3", a.get("take_profit", 0))
            enriched.append(a)

        # Kelly + Adaptive info
        kelly = self.calc_kelly_size()
        adaptive = self.get_adaptive_state()

        return {
            "active_trades": len(active),
            "active_signals": enriched,
            "performance": perf,
            "mode": ame_strategy.mode,
            "params": AME_PARAMS,
            "kelly": kelly,
            "adaptive": adaptive,
        }

    # ───────────────────────────────────────────
    #  KELLY CRITERION POSITION SIZING
    # ───────────────────────────────────────────

    def calc_kelly_size(self):
        """
        Kelly Criterion: f* = (W * B - L) / B
          W = win probability
          B = avg win / avg loss ratio
          L = loss probability (1 - W)

        Quarter-Kelly kullanılır (güvenlik).
        Returns: {kelly_full, kelly_fraction, position_pct, win_rate, avg_rr, sufficient_data}
        """
        perf = get_ame_performance_summary()
        total = perf.get("total_trades", 0)
        wins = perf.get("winning_trades", 0)

        result = {
            "kelly_full": 0,
            "kelly_fraction": 0,
            "position_pct": AME_PARAMS["default_position_pct"],
            "win_rate": 0,
            "avg_rr": 0,
            "sufficient_data": False,
        }

        if total < AME_PARAMS["adaptive_min_trades"]:
            return result

        win_rate = wins / total if total > 0 else 0
        avg_rr = perf.get("avg_rr", 1.0)

        if avg_rr <= 0:
            avg_rr = 1.0

        # Kelly: f* = W - (1-W)/B
        loss_rate = 1.0 - win_rate
        kelly_full = win_rate - (loss_rate / avg_rr) if avg_rr > 0 else 0
        kelly_full = max(0, kelly_full)  # Never negative

        # Quarter Kelly (safety)
        kelly_frac = kelly_full * AME_PARAMS["kelly_fraction"]

        # Convert to position size (%)
        position_pct = kelly_frac * 100
        position_pct = max(AME_PARAMS["min_position_pct"],
                          min(AME_PARAMS["max_position_pct"], position_pct))

        result["kelly_full"] = round(kelly_full, 4)
        result["kelly_fraction"] = round(kelly_frac, 4)
        result["position_pct"] = round(position_pct, 2)
        result["win_rate"] = round(win_rate * 100, 1)
        result["avg_rr"] = round(avg_rr, 2)
        result["sufficient_data"] = True

        return result

    # ───────────────────────────────────────────
    #  PERFORMANCE-ADAPTIVE PARAMETERS
    # ───────────────────────────────────────────

    def adapt_parameters(self):
        """
        Son N trade'in performansına göre parametreleri otomatik ayarla:
          - Win rate yüksek → min_score düşür (daha fazla sinyal)
          - Win rate düşük → min_score artır (daha az ama kaliteli)
          - Avg RR yüksek → TP'leri genişlet
          - Avg RR düşük → TP'leri daralt
          - Consecutive losses → cooldown artır, max_concurrent azalt
        """
        if not AME_PARAMS["adaptive_enabled"]:
            return

        perf = get_ame_performance_summary()
        total = perf.get("total_trades", 0)

        if total < AME_PARAMS["adaptive_min_trades"]:
            return

        win_rate = perf.get("win_rate", 50) / 100.0  # Convert from % to ratio
        avg_rr = perf.get("avg_rr", 1.0)

        # ── Adapt min_signal_score ──
        if win_rate >= 0.65:
            # Performing well → can be slightly more aggressive
            ame_strategy.p["min_signal_score"] = max(45, ame_strategy.p["min_signal_score"] - 2)
        elif win_rate <= 0.35:
            # Underperforming → be more selective
            ame_strategy.p["min_signal_score"] = min(75, ame_strategy.p["min_signal_score"] + 3)
        elif win_rate <= 0.45:
            ame_strategy.p["min_signal_score"] = min(70, ame_strategy.p["min_signal_score"] + 1)

        # ── Adapt TP multipliers ──
        if avg_rr >= 1.5:
            # Good R:R → can extend targets
            AME_PARAMS["tp3_rr"] = min(5.0, 3.5 + (avg_rr - 1.5) * 0.3)
        elif avg_rr < 0.8:
            # Poor R:R → tighten targets
            AME_PARAMS["tp3_rr"] = max(2.5, 3.5 - (0.8 - avg_rr) * 0.5)

        # ── Adapt concurrency ──
        if win_rate >= 0.60 and total >= 10:
            AME_PARAMS["max_concurrent"] = min(7, 5 + 1)
        elif win_rate < 0.35:
            AME_PARAMS["max_concurrent"] = max(3, 5 - 1)
        else:
            AME_PARAMS["max_concurrent"] = 5

        # ── Adapt cooldown ──
        total_pnl = perf.get("total_pnl", 0)
        if total_pnl < -5:
            AME_PARAMS["cooldown_minutes"] = min(20, AME_PARAMS["cooldown_minutes"] + 2)
        elif total_pnl > 5:
            AME_PARAMS["cooldown_minutes"] = max(5, AME_PARAMS["cooldown_minutes"] - 1)

        logger.info(
            f"🧠 AME Adaptif: WR={win_rate:.0%} RR={avg_rr:.2f} → "
            f"MinScore={ame_strategy.p['min_signal_score']} "
            f"MaxConcurrent={AME_PARAMS['max_concurrent']} "
            f"TP3_RR={AME_PARAMS['tp3_rr']:.1f} "
            f"Cooldown={AME_PARAMS['cooldown_minutes']}min"
        )

    def get_adaptive_state(self):
        """Return current adaptive parameter state."""
        return {
            "enabled": AME_PARAMS["adaptive_enabled"],
            "min_signal_score": ame_strategy.p.get("min_signal_score", 55),
            "max_concurrent": AME_PARAMS["max_concurrent"],
            "tp3_rr": AME_PARAMS["tp3_rr"],
            "cooldown_minutes": AME_PARAMS["cooldown_minutes"],
        }


# ═══ Global Instance ═══
ame_trade_manager = AMETradeManager()
