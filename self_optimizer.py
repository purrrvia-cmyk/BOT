# =====================================================
# ICT Trading Bot — SMC Parameter Optimizer v3.0
# (Pure SMC — Boolean Gate Threshold Optimizer)
# =====================================================
#
# SIFIRDAN YAZILDI: Eski puanlama (scoring) ve retail
# indikatör (RSI, MACD, EMA vb.) ağırlıkları SİLİNDİ.
#
# YENİ MANTIK:
#   Bot, veritabanındaki WON/LOST işlemleri analiz ederek
#   ICT strateji motorundaki geometrik ve hacimsel eşikleri
#   (threshold) otomatik optimize eder.
#
# OPTİMİZE EDİLEN PARAMETRELER:
# ┌──────────────────────────────────┬────────────┬────────────────┐
# │ Parametre                        │ Gate       │ Güvenli Aralık │
# ├──────────────────────────────────┼────────────┼────────────────┤
# │ displacement_min_body_ratio      │ Gate 4     │ 0.40 – 0.75    │
# │ displacement_min_size_pct        │ Gate 4     │ 0.001 – 0.005  │
# │ displacement_atr_multiplier      │ Gate 4     │ 0.80 – 2.00    │
# │ fvg_min_size_pct                 │ Gate 5     │ 0.0003 – 0.004 │
# │ fvg_max_age_candles              │ Gate 5     │ 10 – 40        │
# │ liquidity_equal_tolerance        │ Gate 3     │ 0.0003 – 0.003 │
# │ ob_body_ratio_min                │ Yapısal    │ 0.25 – 0.65    │
# │ ob_max_age_candles               │ Yapısal    │ 15 – 50        │
# │ swing_lookback                   │ Gate 3     │ 3 – 8          │
# │ default_sl_pct                   │ Risk       │ 0.006 – 0.025  │
# │ default_tp_ratio                 │ Risk       │ 1.50 – 4.00    │
# └──────────────────────────────────┴────────────┴────────────────┘
#
# ÖĞRENME ALGORİTMASI:
#   1. Son N kapanmış (WON/LOST) işlemleri çek
#   2. LOST işlemlerdeki ortak zayıflıkları tespit et
#      → Zayıf displacement? Küçük FVG? Sahte sweep?
#   3. WON işlemlerin ortak kalite özelliklerini bul
#      → Büyük FVG, güçlü gövde, yüksek hacim
#   4. Eşik değerlerini veri odaklı küçük adımlarla ayarla
#   5. Her değişikliği ayrıntılı logla
#
# GÜVENLİK:
#   - Her parametrenin min/max sınırı var (boundary protection)
#   - Tek seferde max %10 değişiklik
#   - Minimum 20 kapanmış işlem gerekli
#   - Death spiral koruması (emergency mode + bounds clamp)
# =====================================================

import logging
import json
from datetime import datetime
from database import (
    get_completed_signals, get_performance_summary,
    get_component_performance, save_bot_param, get_bot_param,
    add_optimization_log, get_all_bot_params, get_loss_analysis,
    get_confluence_profitability_analysis, get_entry_mode_performance,
    get_htf_bias_accuracy, get_optimization_logs
)
from config import ICT_PARAMS, OPTIMIZER_CONFIG

logger = logging.getLogger("ICT-Bot.Optimizer")


class SelfOptimizer:
    """
    SMC Parameter Optimizer v3.0 — Boolean Gate Threshold Optimizer.

    WON/LOST işlem verilerinden öğrenerek ICT strateji motorunun
    geometrik ve hacimsel eşik değerlerini otomatik optimize eder.

    Eski scoring/confidence/retail-indicator mantığı tamamen kaldırıldı.
    Sadece SMC yapısal parametreleri optimize edilir.

    Akış:
      1. Son kapanmış işlemleri çek (batch)
      2. WON ve LOST havuzlarını ayrıştır
      3. Her parametre grubu için veri odaklı analiz yap
      4. Eşik değerlerini küçük adımlarla ayarla
      5. Her değişikliği logla ve izle
    """

    # ═══════════════════════════════════════════════════════════
    #  PARAMETRE REJİSTRİSİ
    #  Her parametrenin güvenli sınırları, grubu ve açıklaması
    # ═══════════════════════════════════════════════════════════

    PARAM_REGISTRY = {
        # ── Gate 4: Displacement Kalitesi ──
        "displacement_min_body_ratio": {
            "bounds": (0.40, 0.75),
            "group": "displacement",
            "desc": "Displacement mumunun minimum gövde/fitil oranı",
        },
        "displacement_min_size_pct": {
            "bounds": (0.001, 0.005),
            "group": "displacement",
            "desc": "Minimum displacement boyutu (fiyatın %'si)",
        },
        "displacement_atr_multiplier": {
            "bounds": (0.80, 2.00),
            "group": "displacement",
            "desc": "Displacement ATR çarpanı (şiddet ölçüsü)",
        },

        # ── Gate 5: FVG Giriş Kalitesi ──
        "fvg_min_size_pct": {
            "bounds": (0.0003, 0.004),
            "group": "fvg",
            "desc": "Minimum FVG boyutu (fiyatın %'si)",
        },
        "fvg_max_age_candles": {
            "bounds": (10, 40),
            "group": "fvg",
            "desc": "FVG geçerlilik süresi (mum sayısı)",
        },

        # ── Gate 3: Likidite Sweep Hassasiyeti ──
        "liquidity_equal_tolerance": {
            "bounds": (0.0003, 0.003),
            "group": "liquidity",
            "desc": "Equal high/low toleransı (milimetrik hassasiyet)",
        },

        # ── Yapısal: Order Block & Swing ──
        "ob_body_ratio_min": {
            "bounds": (0.25, 0.65),
            "group": "structural",
            "desc": "Order Block mumunun minimum gövde oranı",
        },
        "ob_max_age_candles": {
            "bounds": (15, 50),
            "group": "structural",
            "desc": "Order Block geçerlilik süresi (mum sayısı)",
        },
        "swing_lookback": {
            "bounds": (3, 8),
            "group": "structural",
            "desc": "Swing noktası tespiti bakış penceresi",
        },

        # ── Risk: SL / TP Fallback Değerleri ──
        "default_sl_pct": {
            "bounds": (0.006, 0.025),
            "group": "risk",
            "desc": "Fallback SL yüzdesi (yapısal SL bulunamazsa)",
        },
        "default_tp_ratio": {
            "bounds": (1.50, 4.00),
            "group": "risk",
            "desc": "TP/SL oranı (opposing liquidity bulunamazsa)",
        },
    }

    GROUP_DESCRIPTIONS = {
        "displacement": "Gate 4 — Displacement kalitesi ve momentum",
        "fvg": "Gate 5 — FVG giriş noktası kalitesi",
        "liquidity": "Gate 3 — Likidite sweep hassasiyeti",
        "structural": "Yapısal — OB ve Swing noktası tespiti",
        "risk": "Risk Yönetimi — SL/TP fallback değerleri",
    }

    def __init__(self):
        self.learning_rate = OPTIMIZER_CONFIG.get("learning_rate", 0.03)
        self.max_change_pct = OPTIMIZER_CONFIG.get("max_param_change_pct", 0.10)
        self.min_trades = OPTIMIZER_CONFIG.get("min_trades_for_optimization", 20)
        self.target_win_rate = OPTIMIZER_CONFIG.get("win_rate_target", 0.55)
        self._last_trade_count = 0
        logger.info("SMC Parameter Optimizer v3.0 başlatıldı — Boolean Gate Threshold Optimizer")

    # ═══════════════════════════════════════════════════════════
    #  BAŞLANGIÇ GÜVENLİK KONTROLÜ
    # ═══════════════════════════════════════════════════════════

    def enforce_bounds_on_startup(self):
        """
        Başlangıçta tüm DB parametrelerini sınırlar içine zorla.
        Death spiral sonrası kurtarma mekanizması.
        Sınır dışı parametreler varsayılan değerlerine sıfırlanır.
        """
        all_params = get_all_bot_params()
        reset_count = 0

        for param_name, registry in self.PARAM_REGISTRY.items():
            min_b, max_b = registry["bounds"]
            current_val = all_params.get(param_name)

            if current_val is None:
                continue

            try:
                current_val = float(current_val)
            except (TypeError, ValueError):
                continue

            if current_val < min_b or current_val > max_b:
                default = ICT_PARAMS.get(param_name, current_val)
                logger.warning(
                    f"🔄 {param_name} sınır dışı: {current_val} → {default} "
                    f"(izin: {min_b}–{max_b})"
                )
                save_bot_param(param_name, default, default)
                reset_count += 1

        if reset_count:
            logger.info(f"🔄 {reset_count} parametre sınır dışında bulundu ve sıfırlandı")
        else:
            logger.info("✅ Tüm SMC parametreleri sınırlar içinde")

        return reset_count

    # ═══════════════════════════════════════════════════════════
    #  ANA OPTİMİZASYON DÖNGÜSÜ
    # ═══════════════════════════════════════════════════════════

    def run_optimization(self):
        """
        Ana optimizasyon döngüsü — app.py tarafından her 30dk çağrılır.

        Adımlar:
          1. Yeterli veri kontrolü (min 20 kapanmış işlem)
          2. WON/LOST havuzu oluştur + istatistikler hesapla
          3. Displacement parametreleri optimize et (Gate 4)
          4. FVG parametreleri optimize et (Gate 5)
          5. Likidite parametreleri optimize et (Gate 3)
          6. Yapısal parametreler optimize et (OB, swing)
          7. Risk parametreleri optimize et (SL, TP)
          8. Seans, HTF bias, entry mode bilgi analizi
        """
        logger.info("🔄 SMC Optimizer v3.0 — Optimizasyon döngüsü başlatılıyor...")

        stats = get_performance_summary()
        total_trades = stats["total_trades"]

        if total_trades < self.min_trades:
            logger.info(
                f"Yeterli işlem yok ({total_trades}/{self.min_trades}), "
                f"optimizasyon atlanıyor."
            )
            return {
                "status": "SKIPPED",
                "reason": (
                    f"Tamamlanmış (WON/LOST) işlem sayısı: {total_trades} — "
                    f"minimum {self.min_trades} gerekli"
                ),
                "changes": [],
                "total_trades_analyzed": total_trades,
                "win_rate": stats["win_rate"],
            }

        # ═══ VERİ HAVUZU OLUŞTUR ═══
        pool = self._build_trade_pool()

        logger.info(
            f"📊 Veri havuzu: {pool['total']} işlem | "
            f"WR: {pool['win_rate']:.1f}% | "
            f"Ort kazanç: +{pool['avg_win_pnl']:.2f}% | "
            f"Ort kayıp: -{pool['avg_loss_pnl']:.2f}% | "
            f"Gerçek RR: {pool['realized_rr']:.2f}"
        )

        changes = []

        # ═══ ACİL MOD: %0 WR + 3+ kayıp ═══
        if pool["win_rate"] == 0 and len(pool["losers"]) >= 3:
            emergency = self._emergency_mode(pool, stats)
            changes.extend(emergency)

        # ═══ OPTİMİZASYON ADIMLARI ═══
        already_changed = {c["param"] for c in changes}

        # 1. Displacement parametreleri (Gate 4)
        disp_changes = self._optimize_displacement(pool, stats, already_changed)
        changes.extend(disp_changes)
        already_changed.update(c["param"] for c in disp_changes)

        # 2. FVG parametreleri (Gate 5)
        fvg_changes = self._optimize_fvg(pool, stats, already_changed)
        changes.extend(fvg_changes)
        already_changed.update(c["param"] for c in fvg_changes)

        # 3. Likidite parametreleri (Gate 3)
        liq_changes = self._optimize_liquidity(pool, stats, already_changed)
        changes.extend(liq_changes)
        already_changed.update(c["param"] for c in liq_changes)

        # 4. Yapısal parametreler (OB, swing)
        struct_changes = self._optimize_structural(pool, stats, already_changed)
        changes.extend(struct_changes)
        already_changed.update(c["param"] for c in struct_changes)

        # 5. Risk parametreleri (SL, TP)
        risk_changes = self._optimize_risk(pool, stats, already_changed)
        changes.extend(risk_changes)

        # 6. Bilgi analizleri (parametre değiştirmez, sadece loglar)
        self._log_session_analysis(pool)
        self._log_htf_bias_analysis()
        self._log_entry_mode_analysis()

        # ═══ SONUÇ ═══
        if changes:
            logger.info(
                f"✅ SMC Optimizasyon tamamlandı: {len(changes)} parametre güncellendi"
            )
            for c in changes:
                logger.info(
                    f"   → {c['param']}: {c['old']} → {c['new']} "
                    f"[{c.get('group', '?')}]"
                )
        else:
            logger.info("ℹ️ Optimizasyon: Tüm parametreler optimal aralıkta")

        self._last_trade_count = total_trades

        return {
            "status": "COMPLETED",
            "total_trades_analyzed": total_trades,
            "win_rate": stats["win_rate"],
            "changes": changes,
        }

    # ═══════════════════════════════════════════════════════════
    #  VERİ HAVUZU OLUŞTURMA
    # ═══════════════════════════════════════════════════════════

    def _build_trade_pool(self):
        """
        Son kapanmış işlemlerden analiz havuzu oluştur.

        Çekilen veriler:
          - WON/LOST ayrıştırma
          - Ort. kazanç, ort. kayıp, gerçek RR
          - Hızlı kayıp oranı (< 30dk)
          - Büyük kayıp oranı (> %2)
          - Seans dağılımı
        """
        completed = get_completed_signals(200)
        winners = [s for s in completed if s["status"] == "WON"]
        losers = [s for s in completed if s["status"] == "LOST"]

        total = len(completed)

        avg_win = (
            sum(abs(s["pnl_pct"] or 0) for s in winners) / len(winners)
            if winners else 0
        )
        avg_loss = (
            sum(abs(s["pnl_pct"] or 0) for s in losers) / len(losers)
            if losers else 0
        )
        realized_rr = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0

        # ── Hızlı kayıp analizi ──
        # Entry sonrası kısa sürede SL → fake breakout / zayıf displacement
        quick_losses = 0
        for s in losers:
            duration_min = self._calc_trade_duration_min(s)
            if duration_min is not None and duration_min < 30:
                quick_losses += 1

        quick_loss_ratio = quick_losses / len(losers) if losers else 0

        # ── Büyük kayıp analizi ──
        # SL'den çok daha büyük kayıp = slippage veya yapısal sorun
        large_losses = sum(
            1 for s in losers
            if (s.get("pnl_pct") or 0) < -2.0
        )
        large_loss_ratio = large_losses / len(losers) if losers else 0

        # ── Seans dağılımı ──
        session_stats = {}
        for s in completed:
            session = self._extract_session(s)
            if session:
                if session not in session_stats:
                    session_stats[session] = {"total": 0, "won": 0, "pnl": 0}
                session_stats[session]["total"] += 1
                if s["status"] == "WON":
                    session_stats[session]["won"] += 1
                session_stats[session]["pnl"] += (s["pnl_pct"] or 0)

        return {
            "completed": completed,
            "winners": winners,
            "losers": losers,
            "total": total,
            "win_rate": len(winners) / total * 100 if total else 0,
            "avg_win_pnl": round(avg_win, 3),
            "avg_loss_pnl": round(avg_loss, 3),
            "realized_rr": realized_rr,
            "quick_loss_ratio": round(quick_loss_ratio, 3),
            "large_loss_ratio": round(large_loss_ratio, 3),
            "session_stats": session_stats,
        }

    # ═══════════════════════════════════════════════════════════
    #  1. DISPLACEMENT PARAMETRELERİ (Gate 4)
    # ═══════════════════════════════════════════════════════════

    def _optimize_displacement(self, pool, stats, already_changed):
        """
        Displacement kalitesini WON/LOST analizinden öğren.

        Kararlar:
        ┌────────────────────────┬──────────────────────────────────┐
        │ Durum                  │ Aksiyon                          │
        ├────────────────────────┼──────────────────────────────────┤
        │ Ort kayıp yüksek +    │ body_ratio ↑  atr_mult ↑        │
        │ WR düşük               │ → Zayıf momentum filtrelemesi    │
        ├────────────────────────┼──────────────────────────────────┤
        │ Hızlı kayıp oranı     │ body_ratio ↑  atr_mult ↑        │
        │ > %40                  │ → Fake breakout koruması         │
        ├────────────────────────┼──────────────────────────────────┤
        │ WR > %70 + yeterli    │ body_ratio ↓  (hafif)            │
        │ veri                   │ → Daha fazla setup yakala        │
        └────────────────────────┴──────────────────────────────────┘
        """
        changes = []

        if pool["total"] < self.min_trades:
            return changes

        avg_loss = pool["avg_loss_pnl"]
        quick_loss_ratio = pool["quick_loss_ratio"]
        win_rate = pool["win_rate"]

        # ────────────────────────────────────────
        # displacement_min_body_ratio
        # ────────────────────────────────────────
        param = "displacement_min_body_ratio"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            if avg_loss > 1.8 and win_rate < 45:
                # Displacement gövdesi zayıfmış → sıkılaştır
                step = current * self.learning_rate * 1.5
                new_val = current + step
                reason = (
                    f"Fake breakout'larda artış tespit edildi, "
                    f"displacement_min_body_ratio {current:.2f}'den "
                    f"{min(new_val, self.PARAM_REGISTRY[param]['bounds'][1]):.2f}'e güncellendi"
                )
                change = self._apply_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif quick_loss_ratio > 0.40 and win_rate < 50:
                # Hızlı kayıplar = fake displacement
                step = current * self.learning_rate * 2.0
                new_val = current + step
                reason = (
                    f"Hızlı kayıp oranı yüksek ({quick_loss_ratio:.0%}), "
                    f"displacement_min_body_ratio {current:.2f}'den "
                    f"{min(new_val, self.PARAM_REGISTRY[param]['bounds'][1]):.2f}'e güncellendi"
                )
                change = self._apply_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif win_rate > 70 and pool["total"] >= 30:
                # WR çok iyi → hafif gevşet (daha fazla setup yakalansın)
                step = current * self.learning_rate * 0.5
                new_val = current - step
                reason = (
                    f"Win rate yüksek ({win_rate:.1f}%), "
                    f"displacement_min_body_ratio {current:.2f}'den "
                    f"{max(new_val, self.PARAM_REGISTRY[param]['bounds'][0]):.2f}'e gevşetildi"
                )
                change = self._apply_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        # ────────────────────────────────────────
        # displacement_atr_multiplier
        # ────────────────────────────────────────
        param = "displacement_atr_multiplier"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            if quick_loss_ratio > 0.35 and win_rate < 50:
                # Hızlı kayıplar → displacement momentum yetersiz
                step = current * self.learning_rate
                new_val = current + step
                reason = (
                    f"Hızlı kayıp oranı {quick_loss_ratio:.0%}, "
                    f"displacement_atr_multiplier {current:.2f}'den "
                    f"{min(new_val, self.PARAM_REGISTRY[param]['bounds'][1]):.2f}'e güncellendi "
                    f"(daha güçlü momentum gerekli)"
                )
                change = self._apply_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif win_rate > 65 and avg_loss < 1.0:
                # İyi performans, hafif gevşet
                step = current * self.learning_rate * 0.5
                new_val = current - step
                reason = (
                    f"İyi performans (WR: {win_rate:.1f}%, ort kayıp: {avg_loss:.2f}%), "
                    f"displacement_atr_multiplier {current:.2f}'den "
                    f"{max(new_val, self.PARAM_REGISTRY[param]['bounds'][0]):.2f}'e gevşetildi"
                )
                change = self._apply_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        # ────────────────────────────────────────
        # displacement_min_size_pct
        # ────────────────────────────────────────
        param = "displacement_min_size_pct"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            if avg_loss > 2.0 and win_rate < 40:
                # Yüksek kayıp + düşük WR → displacement boyutu yetersiz
                step = current * self.learning_rate * 1.5
                new_val = current + step
                reason = (
                    f"Yüksek ort. kayıp ({avg_loss:.2f}%) ve düşük WR ({win_rate:.1f}%), "
                    f"displacement_min_size_pct {current:.4f}'den "
                    f"{min(new_val, self.PARAM_REGISTRY[param]['bounds'][1]):.4f}'e güncellendi"
                )
                change = self._apply_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif win_rate > 65 and pool["total"] >= 25:
                # Performans iyi → hafif gevşet
                step = current * self.learning_rate * 0.5
                new_val = current - step
                reason = (
                    f"WR iyi ({win_rate:.1f}%), "
                    f"displacement_min_size_pct {current:.4f}'den "
                    f"{max(new_val, self.PARAM_REGISTRY[param]['bounds'][0]):.4f}'e gevşetildi"
                )
                change = self._apply_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        return changes

    # ═══════════════════════════════════════════════════════════
    #  2. FVG PARAMETRELERİ (Gate 5)
    # ═══════════════════════════════════════════════════════════

    def _optimize_fvg(self, pool, stats, already_changed):
        """
        FVG kalitesini WON/LOST analizinden öğren.

        Kararlar:
        ┌────────────────────────┬──────────────────────────────────┐
        │ Durum                  │ Aksiyon                          │
        ├────────────────────────┼──────────────────────────────────┤
        │ Gerçek RR düşük +     │ fvg_min_size_pct ↑               │
        │ WR düşük               │ → Küçük FVG'leri eleyerek        │
        │                        │   kaliteyi artır                 │
        ├────────────────────────┼──────────────────────────────────┤
        │ RR iyi + WR iyi       │ fvg_min_size_pct ↓ (hafif)       │
        │                        │ → Daha fazla FVG yakala          │
        ├────────────────────────┼──────────────────────────────────┤
        │ LIMIT WR < MARKET WR  │ fvg_max_age_candles ↓            │
        │                        │ → Eski FVG'ler güvenilmez        │
        └────────────────────────┴──────────────────────────────────┘
        """
        changes = []

        if pool["total"] < self.min_trades:
            return changes

        realized_rr = pool["realized_rr"]
        win_rate = pool["win_rate"]

        # ────────────────────────────────────────
        # fvg_min_size_pct
        # ────────────────────────────────────────
        param = "fvg_min_size_pct"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            if realized_rr < 1.5 and win_rate < 50:
                # Düşük RR + düşük WR → küçük FVG'lerden giriyoruz
                step = current * self.learning_rate * 1.5
                new_val = current + step
                reason = (
                    f"Gerçek RR düşük ({realized_rr:.2f}) ve WR düşük ({win_rate:.1f}%), "
                    f"fvg_min_size_pct {current:.5f}'den "
                    f"{min(new_val, self.PARAM_REGISTRY[param]['bounds'][1]):.5f}'e güncellendi "
                    f"(daha büyük FVG hedefleme)"
                )
                change = self._apply_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif realized_rr > 2.5 and win_rate > 60:
                # İyi RR + iyi WR → hafif gevşet
                step = current * self.learning_rate * 0.5
                new_val = current - step
                reason = (
                    f"İyi RR ({realized_rr:.2f}) ve WR ({win_rate:.1f}%), "
                    f"fvg_min_size_pct {current:.5f}'den "
                    f"{max(new_val, self.PARAM_REGISTRY[param]['bounds'][0]):.5f}'e gevşetildi"
                )
                change = self._apply_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        # ────────────────────────────────────────
        # fvg_max_age_candles
        # ────────────────────────────────────────
        param = "fvg_max_age_candles"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            # LIMIT vs MARKET entry karşılaştırması
            entry_perf = get_entry_mode_performance()
            limit_data = entry_perf.get("LIMIT", {})
            market_data = entry_perf.get("MARKET", {})

            limit_wr = limit_data.get("win_rate", 0)
            market_wr = market_data.get("win_rate", 0)
            limit_total = limit_data.get("total", 0)
            market_total = market_data.get("total", 0)

            if limit_total >= 5 and market_total >= 5 and limit_wr < market_wr - 10:
                # LIMIT (FVG entry) MARKET'ten çok daha kötü → eski FVG'ler bozulmuş
                step = max(1, current * self.learning_rate)
                new_val = current - step
                reason = (
                    f"LIMIT WR ({limit_wr:.0f}%) < MARKET WR ({market_wr:.0f}%), "
                    f"fvg_max_age_candles {int(current)}'den "
                    f"{max(int(new_val), self.PARAM_REGISTRY[param]['bounds'][0])}'e azaltıldı "
                    f"(eski FVG'ler güvenilmez)"
                )
                change = self._apply_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif win_rate < 40 and pool["total"] >= 20:
                # Genel WR düşük → eski FVG'leri kısıtla
                step = max(1, current * self.learning_rate * 0.8)
                new_val = current - step
                reason = (
                    f"WR düşük ({win_rate:.1f}%), "
                    f"fvg_max_age_candles {int(current)}'den "
                    f"{max(int(new_val), self.PARAM_REGISTRY[param]['bounds'][0])}'e azaltıldı"
                )
                change = self._apply_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        return changes

    # ═══════════════════════════════════════════════════════════
    #  3. LİKİDİTE PARAMETRELERİ (Gate 3)
    # ═══════════════════════════════════════════════════════════

    def _optimize_liquidity(self, pool, stats, already_changed):
        """
        Likidite sweep kalitesini analiz et.

        Kararlar:
        ┌────────────────────────┬──────────────────────────────────┐
        │ Durum                  │ Aksiyon                          │
        ├────────────────────────┼──────────────────────────────────┤
        │ Hızlı kayıp > %50 +   │ tolerance ↓                      │
        │ WR düşük               │ → Sahte sweep'leri ele           │
        ├────────────────────────┼──────────────────────────────────┤
        │ WR > %65 + hızlı      │ tolerance ↑ (hafif)              │
        │ kayıp düşük            │ → Daha fazla seviye yakala       │
        └────────────────────────┴──────────────────────────────────┘
        """
        changes = []

        if pool["total"] < self.min_trades:
            return changes

        quick_loss_ratio = pool["quick_loss_ratio"]
        win_rate = pool["win_rate"]

        # ────────────────────────────────────────
        # liquidity_equal_tolerance
        # ────────────────────────────────────────
        param = "liquidity_equal_tolerance"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            if quick_loss_ratio > 0.50 and win_rate < 45:
                # Çok fazla hızlı kayıp → sahte sweep'ler → tolerans sıkılaştır
                step = current * self.learning_rate
                new_val = current - step  # Tolerans küçült = daha hassas seviye
                reason = (
                    f"Hızlı kayıp oranı {quick_loss_ratio:.0%} ve WR {win_rate:.1f}%, "
                    f"liquidity_equal_tolerance {current:.5f}'den "
                    f"{max(new_val, self.PARAM_REGISTRY[param]['bounds'][0]):.5f}'e "
                    f"sıkılaştırıldı (sahte sweep filtresi)"
                )
                change = self._apply_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif win_rate > 65 and quick_loss_ratio < 0.15:
                # Sweep tespiti çok iyi → hafif gevşet
                step = current * self.learning_rate * 0.5
                new_val = current + step
                reason = (
                    f"Sweep kalitesi iyi (WR: {win_rate:.1f}%, hızlı kayıp: {quick_loss_ratio:.0%}), "
                    f"liquidity_equal_tolerance {current:.5f}'den "
                    f"{min(new_val, self.PARAM_REGISTRY[param]['bounds'][1]):.5f}'e "
                    f"gevşetildi (daha fazla seviye)"
                )
                change = self._apply_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        return changes

    # ═══════════════════════════════════════════════════════════
    #  4. YAPISAL PARAMETRELER (OB, Swing)
    # ═══════════════════════════════════════════════════════════

    def _optimize_structural(self, pool, stats, already_changed):
        """
        Order Block ve swing noktası parametrelerini optimize et.

        Bu parametreler Gate'lere dolaylı bağlıdır — gate öncesi
        veri hazırlığının kalitesini belirler.

        Kararlar:
        ┌────────────────────────┬──────────────────────────────────┐
        │ Durum                  │ Aksiyon                          │
        ├────────────────────────┼──────────────────────────────────┤
        │ WR < %40               │ ob_body ↑, ob_age ↓, swing ↑    │
        │                        │ → Daha kaliteli yapısal veri     │
        ├────────────────────────┼──────────────────────────────────┤
        │ WR > %65               │ ob_body ↓ (hafif)                │
        │                        │ → Daha fazla yapı belirlensin    │
        └────────────────────────┴──────────────────────────────────┘
        """
        changes = []

        if pool["total"] < self.min_trades:
            return changes

        win_rate = pool["win_rate"]
        avg_loss = pool["avg_loss_pnl"]

        # ────────────────────────────────────────
        # ob_body_ratio_min
        # ────────────────────────────────────────
        param = "ob_body_ratio_min"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            if win_rate < 40 and pool["total"] >= 20:
                step = current * self.learning_rate
                new_val = current + step
                reason = (
                    f"WR düşük ({win_rate:.1f}%), "
                    f"ob_body_ratio_min {current:.2f}'den "
                    f"{min(new_val, self.PARAM_REGISTRY[param]['bounds'][1]):.2f}'e güncellendi "
                    f"(OB kalite filtresi sıkılaştırıldı)"
                )
                change = self._apply_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif win_rate > 65 and pool["total"] >= 20:
                step = current * self.learning_rate * 0.5
                new_val = current - step
                reason = (
                    f"WR yüksek ({win_rate:.1f}%), "
                    f"ob_body_ratio_min {current:.2f}'den "
                    f"{max(new_val, self.PARAM_REGISTRY[param]['bounds'][0]):.2f}'e gevşetildi"
                )
                change = self._apply_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        # ────────────────────────────────────────
        # ob_max_age_candles
        # ────────────────────────────────────────
        param = "ob_max_age_candles"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            if win_rate < 42 and avg_loss > 1.5:
                # Düşük WR + yüksek kayıp → eski OB'ler bozulmuş
                step = max(1, current * self.learning_rate)
                new_val = current - step
                reason = (
                    f"WR düşük ({win_rate:.1f}%) ve ort kayıp yüksek ({avg_loss:.2f}%), "
                    f"ob_max_age_candles {int(current)}'den "
                    f"{max(int(new_val), self.PARAM_REGISTRY[param]['bounds'][0])}'e "
                    f"azaltıldı (daha taze OB hedefleme)"
                )
                change = self._apply_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        # ────────────────────────────────────────
        # swing_lookback
        # ────────────────────────────────────────
        param = "swing_lookback"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            # Swing lookback: çok küçük = noise, çok büyük = eski seviyeler
            if win_rate < 38 and pool["quick_loss_ratio"] > 0.40:
                # Hızlı kayıplar + düşük WR → swing seviyeleri hassas değil
                new_val = current + 1
                reason = (
                    f"Hızlı kayıp oranı yüksek ({pool['quick_loss_ratio']:.0%}), "
                    f"swing_lookback {int(current)}'den {int(new_val)}'e artırıldı "
                    f"(daha güvenilir swing seviyeleri)"
                )
                change = self._apply_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        return changes

    # ═══════════════════════════════════════════════════════════
    #  5. RİSK PARAMETRELERİ (SL, TP)
    # ═══════════════════════════════════════════════════════════

    def _optimize_risk(self, pool, stats, already_changed):
        """
        SL ve TP parametrelerini gerçekleşen trade sonuçlarından öğren.

        NOT: v3.0'da SL = sweep wick extreme, TP = opposing liquidity.
        Bu parametreler sadece FALLBACK olarak kullanılır.
        Ama gerçekleşen RR ve kayıp büyüklüğünü izleyerek trend gösterir.

        Kararlar:
        ┌─────────────────────────┬─────────────────────────────────┐
        │ Durum                   │ Aksiyon                         │
        ├─────────────────────────┼─────────────────────────────────┤
        │ Kayıp oranı > %60 +    │ default_sl_pct ↑                │
        │ ort kayıp makul         │ → Noise filtresi genişlet       │
        ├─────────────────────────┼─────────────────────────────────┤
        │ Ort kayıp > %2.5       │ default_sl_pct ↓                │
        │                         │ → SL çok geniş, daralt          │
        ├─────────────────────────┼─────────────────────────────────┤
        │ Gerçek RR < 1.2 +      │ default_tp_ratio ↑              │
        │ WR < %50                │ → TP hedefini yükselt           │
        ├─────────────────────────┼─────────────────────────────────┤
        │ RR > 3.0 + WR < %45    │ default_tp_ratio ↓              │
        │                         │ → TP çok uzak, yakınlaştır      │
        └─────────────────────────┴─────────────────────────────────┘
        """
        changes = []

        if pool["total"] < self.min_trades:
            return changes

        avg_win = pool["avg_win_pnl"]
        avg_loss = pool["avg_loss_pnl"]
        win_rate = pool["win_rate"]
        realized_rr = pool["realized_rr"]

        if avg_win <= 0 or avg_loss <= 0:
            return changes

        loss_rate = len(pool["losers"]) / pool["total"] if pool["total"] else 0

        # ────────────────────────────────────────
        # default_sl_pct
        # ────────────────────────────────────────
        param = "default_sl_pct"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])
            sl_as_pct = current * 100  # 0.012 → 1.2%

            if loss_rate > 0.60 and avg_loss < sl_as_pct * 0.9:
                # Çok sık kayıp AMA kayıplar SL'den küçük → noise tetikliyor
                step = current * self.learning_rate
                new_val = current + step
                reason = (
                    f"Kayıp oranı yüksek ({loss_rate:.0%}) ama ort kayıp makul ({avg_loss:.2f}%), "
                    f"default_sl_pct {current:.4f}'den "
                    f"{min(new_val, self.PARAM_REGISTRY[param]['bounds'][1]):.4f}'e "
                    f"genişletildi (noise filtresi)"
                )
                change = self._apply_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif avg_loss > 2.5 and win_rate < 45:
                # Ort kayıp çok büyük → SL çok geniş
                step = current * self.learning_rate
                new_val = current - step
                reason = (
                    f"Ort kayıp çok yüksek ({avg_loss:.2f}%), "
                    f"default_sl_pct {current:.4f}'den "
                    f"{max(new_val, self.PARAM_REGISTRY[param]['bounds'][0]):.4f}'e daraltıldı"
                )
                change = self._apply_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        # ────────────────────────────────────────
        # default_tp_ratio
        # ────────────────────────────────────────
        param = "default_tp_ratio"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            if realized_rr < 1.2 and win_rate < 50:
                # RR çok düşük → TP hedefini yükselt
                new_val = current + 0.1
                reason = (
                    f"Gerçek RR düşük ({realized_rr:.2f}) ve WR düşük ({win_rate:.1f}%), "
                    f"default_tp_ratio {current:.1f}'den "
                    f"{min(new_val, self.PARAM_REGISTRY[param]['bounds'][1]):.1f}'e artırıldı"
                )
                change = self._apply_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif realized_rr > 3.0 and win_rate < 45:
                # RR yüksek ama WR düşük → TP çok uzak, ulaşılamıyor
                new_val = current - 0.1
                reason = (
                    f"RR yüksek ({realized_rr:.2f}) ama WR düşük ({win_rate:.1f}%), "
                    f"default_tp_ratio {current:.1f}'den "
                    f"{max(new_val, self.PARAM_REGISTRY[param]['bounds'][0]):.1f}'e "
                    f"yakınlaştırıldı (ulaşılabilir TP)"
                )
                change = self._apply_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        return changes

    # ═══════════════════════════════════════════════════════════
    #  ACİL MOD
    # ═══════════════════════════════════════════════════════════

    def _emergency_mode(self, pool, stats):
        """
        🚨 ACİL MOD — %0 win rate ile ardışık kayıplarda tetiklenir.

        Agresif sıkılaştırma: displacement ve FVG eşiklerini yükselt
        → sadece en kaliteli setup'lara gir.

        Tetikleme: WR == 0% ve >= 3 kayıp (max 10 kayıp sonrası pasif)
        """
        changes = []
        n_losses = len(pool["losers"])

        if n_losses > 10:
            logger.info("🚨 Acil mod atlandı — yeterli veri toplandı")
            return changes

        logger.warning(
            f"🚨 ACİL MOD AKTİF: %0 win rate, {n_losses} ardışık kayıp → "
            f"Displacement ve FVG filtreleri agresif sıkılaştırılıyor!"
        )

        # 1. Displacement body ratio sıkılaştır
        param = "displacement_min_body_ratio"
        current = get_bot_param(param, ICT_PARAMS[param])
        new_val = current * 1.08  # %8 artış
        reason = (
            f"🚨 ACİL: {n_losses} ardışık kayıp tespit edildi, "
            f"displacement_min_body_ratio {current:.2f}'den {new_val:.2f}'e sıkılaştırıldı"
        )
        change = self._apply_change(param, current, new_val, reason, stats)
        if change:
            changes.append(change)

        # 2. FVG minimum boyut sıkılaştır
        param = "fvg_min_size_pct"
        current = get_bot_param(param, ICT_PARAMS[param])
        new_val = current * 1.10  # %10 artış
        reason = (
            f"🚨 ACİL: Küçük FVG'lerden girilen kayıplar → "
            f"fvg_min_size_pct {current:.5f}'den {new_val:.5f}'e yükseltildi"
        )
        change = self._apply_change(param, current, new_val, reason, stats)
        if change:
            changes.append(change)

        # 3. SL hafif genişlet (premature stop-out koruması)
        param = "default_sl_pct"
        current = get_bot_param(param, ICT_PARAMS[param])
        if current < 0.020:
            new_val = current * 1.06  # %6 artış
            reason = (
                f"🚨 ACİL: SL mesafesi {current:.4f}'den {new_val:.4f}'e "
                f"genişletildi (erken stop-out koruması)"
            )
            change = self._apply_change(param, current, new_val, reason, stats)
            if change:
                changes.append(change)

        return changes

    # ═══════════════════════════════════════════════════════════
    #  BİLGİ ANALİZLERİ (parametre değiştirmez, sadece loglar)
    # ═══════════════════════════════════════════════════════════

    def _log_session_analysis(self, pool):
        """
        Seans bazlı (London Open / NY Open) performans analizi.

        Trade notlarındaki Session bilgisini parse ederek hangi killzone'un
        daha başarılı olduğunu raporlar. Parametre değiştirmez.
        """
        session_stats = pool.get("session_stats", {})
        if not session_stats:
            return

        logger.info("📊 ─── Seans Performans Raporu ───")
        for session, data in session_stats.items():
            wr = data["won"] / data["total"] * 100 if data["total"] else 0
            avg_pnl = data["pnl"] / data["total"] if data["total"] else 0
            logger.info(
                f"   {session}: {data['total']} işlem, "
                f"WR={wr:.0f}%, ort PnL={avg_pnl:+.2f}%"
            )
            if data["total"] >= 5 and wr < 35:
                logger.warning(
                    f"   ⚠️ {session} düşük performans gösteriyor — "
                    f"bu killzone'da dikkatli ol"
                )

    def _log_htf_bias_analysis(self):
        """
        HTF Bias (4H yön tayini) doğruluk analizi.

        BULLISH vs BEARISH bias'ın hangi yönde daha isabetli olduğunu raporlar.
        Parametre değiştirmez.
        """
        accuracy = get_htf_bias_accuracy()
        if not accuracy:
            return

        logger.info("📊 ─── HTF Bias Doğruluk Raporu ───")
        for bias, data in accuracy.items():
            logger.info(
                f"   HTF '{bias}': {data['total']} işlem, WR={data['win_rate']}%"
            )
            if data["total"] >= 5 and data["win_rate"] < 40:
                logger.warning(
                    f"   ⚠️ HTF '{bias}' düşük doğruluk ({data['win_rate']}%) — "
                    f"bu bias ile dikkatli ol"
                )

    def _log_entry_mode_analysis(self):
        """
        LIMIT vs MARKET giriş mode performans karşılaştırması.

        FVG limit entry mi yoksa market entry mi daha karlı?
        Parametre değiştirmez.
        """
        perf = get_entry_mode_performance()
        if not perf:
            return

        logger.info("📊 ─── Entry Mode Performans Raporu ───")
        for mode, data in perf.items():
            logger.info(
                f"   {mode}: {data['total']} işlem, "
                f"WR={data['win_rate']}%, avgPnL={data['avg_pnl']}%"
            )

    # ═══════════════════════════════════════════════════════════
    #  YARDIMCI METODLAR
    # ═══════════════════════════════════════════════════════════

    def _apply_change(self, param_name, current_val, new_val, reason, stats):
        """
        Parametre değişikliğini güvenli şekilde uygula.

        Kontroller:
          1. Max değişim limiti (%10)
          2. Sınır kontrolü (bounds clamp)
          3. Integer/float uyumu
          4. Minimum anlamlı değişiklik (%1)

        Returns:
            dict: Değişiklik bilgisi veya None (uygulanmadıysa)
        """
        registry = self.PARAM_REGISTRY.get(param_name)
        if not registry:
            logger.warning(f"⚠️ {param_name} parametre rejistrisinde bulunamadı")
            return None

        min_b, max_b = registry["bounds"]

        # ── Max değişim limiti (%10) ──
        max_change = abs(current_val * self.max_change_pct)
        if max_change > 0 and abs(new_val - current_val) > max_change:
            new_val = current_val + (
                max_change if new_val > current_val else -max_change
            )

        # ── Sınır kontrolü ──
        new_val = max(min_b, min(max_b, new_val))

        # ── Integer parametre kontrolü ──
        if isinstance(ICT_PARAMS.get(param_name), int):
            new_val = int(round(new_val))
        else:
            # Küçük değerler için daha fazla ondalık
            if abs(new_val) < 0.01:
                new_val = round(new_val, 6)
            elif abs(new_val) < 1:
                new_val = round(new_val, 5)
            else:
                new_val = round(new_val, 4)

        # ── Anlamlı değişiklik kontrolü (%1'den az → atla) ──
        if current_val != 0:
            change_pct = abs(new_val - current_val) / abs(current_val)
            if change_pct < 0.01:
                return None
        elif new_val == current_val:
            return None

        # ── Kaydet ──
        default_val = ICT_PARAMS.get(param_name, current_val)
        save_bot_param(param_name, new_val, default_val)

        add_optimization_log(
            param_name, current_val, new_val, reason,
            stats["win_rate"], stats["win_rate"], stats["total_trades"]
        )

        logger.info(f"📊 {param_name}: {current_val} → {new_val} | {reason}")

        return {
            "param": param_name,
            "old": current_val,
            "new": new_val,
            "reason": reason,
            "bounds": [min_b, max_b],
            "group": registry["group"],
        }

    def _get_last_change_direction(self, param_name):
        """
        Son optimizasyon loglarından parametrenin son değişim yönünü tespit et.

        Returns: "up" (artırıldı), "down" (azaltıldı), "none" (değişmedi)
        """
        try:
            logs = get_optimization_logs(30)
            for log in logs:
                if log.get("param_name") == param_name:
                    old_val = float(log.get("old_value", 0))
                    new_val = float(log.get("new_value", 0))
                    if new_val > old_val:
                        return "up"
                    elif new_val < old_val:
                        return "down"
                    return "none"
        except Exception:
            pass
        return "none"

    def _calc_trade_duration_min(self, signal):
        """
        Bir işlemin süresini dakika cinsinden hesapla.

        entry_time ile close_time arasındaki farkı döndürür.
        Veri yoksa veya parse hatalıysa None döner.
        """
        entry_time = signal.get("entry_time") or signal.get("created_at", "")
        close_time = signal.get("close_time", "")

        if not entry_time or not close_time:
            return None

        try:
            entry_dt = datetime.fromisoformat(entry_time)
            close_dt = datetime.fromisoformat(close_time)
            duration_min = (close_dt - entry_dt).total_seconds() / 60
            return round(duration_min, 1)
        except Exception:
            return None

    def _extract_session(self, signal):
        """
        Sinyal notlarından seans bilgisini çıkar.

        Notes formatı: "... | Session: NY_OPEN | ..."
        Returns: "LONDON_OPEN", "NY_OPEN", vb. veya None
        """
        notes = signal.get("notes", "") or ""
        if "Session:" not in notes:
            return None

        try:
            session_part = notes.split("Session:")[1].split("|")[0].strip()
            return session_part if session_part else None
        except Exception:
            return None

    # ═══════════════════════════════════════════════════════════
    #  OPTİMİZASYON ÖZETİ (API Endpoint)
    # ═══════════════════════════════════════════════════════════

    def get_optimization_summary(self):
        """
        Optimizasyon özetini döndür — app.py API endpoint'i için.

        Endpoint: GET /api/optimization/summary

        Geriye uyumlu alanlar korundu + yeni v3.0 alanları eklendi:
        - optimizer_version, param_groups, realized_rr
        - changed_params artık bounds ve group bilgisi içerir
        """
        stats = get_performance_summary()
        all_params = get_all_bot_params()
        loss_info = get_loss_analysis(30)
        entry_mode_perf = get_entry_mode_performance()
        htf_accuracy = get_htf_bias_accuracy()

        # ── Varsayılandan değişen parametreleri bul ──
        changed_params = {}
        for param_name, registry in self.PARAM_REGISTRY.items():
            default_val = ICT_PARAMS.get(param_name)
            if default_val is None:
                continue

            current_val = all_params.get(param_name, default_val)
            try:
                current_val = float(current_val)
                default_val = float(default_val)
            except (TypeError, ValueError):
                continue

            if abs(current_val - default_val) > 0.0001:
                change_pct = (
                    ((current_val - default_val) / default_val) * 100
                    if default_val != 0 else 0
                )
                changed_params[param_name] = {
                    "default": default_val,
                    "current": current_val,
                    "change_pct": round(change_pct, 1),
                    "bounds": list(registry["bounds"]),
                    "group": registry["group"],
                    "description": registry["desc"],
                }

        # ── WON/LOST analiz özeti ──
        completed = get_completed_signals(100)
        winners = [s for s in completed if s["status"] == "WON"]
        losers = [s for s in completed if s["status"] == "LOST"]

        avg_win = (
            sum(abs(s["pnl_pct"] or 0) for s in winners) / len(winners)
            if winners else 0
        )
        avg_loss = (
            sum(abs(s["pnl_pct"] or 0) for s in losers) / len(losers)
            if losers else 0
        )
        realized_rr = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0

        # ── Hızlı kayıp analizi ──
        quick_losses = 0
        for s in losers:
            dur = self._calc_trade_duration_min(s)
            if dur is not None and dur < 30:
                quick_losses += 1
        quick_loss_ratio = (
            round(quick_losses / len(losers) * 100, 1) if losers else 0
        )

        return {
            "optimizer_version": "3.0 — SMC Threshold Optimizer",
            "total_optimizations": len(changed_params),
            "current_win_rate": stats["win_rate"],
            "target_win_rate": self.target_win_rate * 100,
            "realized_rr": realized_rr,
            "avg_win_pnl": round(avg_win, 2),
            "avg_loss_pnl": round(avg_loss, 2),
            "quick_loss_ratio_pct": quick_loss_ratio,
            "changed_params": changed_params,
            "performance": stats,
            "loss_lessons": loss_info.get("lesson_summary", []),
            "entry_mode_performance": entry_mode_perf,
            "htf_bias_accuracy": htf_accuracy,
            "param_groups": self.GROUP_DESCRIPTIONS,
            "optimizable_params": {
                name: {
                    "bounds": list(reg["bounds"]),
                    "group": reg["group"],
                    "description": reg["desc"],
                    "current": get_bot_param(name, ICT_PARAMS.get(name)),
                    "default": ICT_PARAMS.get(name),
                }
                for name, reg in self.PARAM_REGISTRY.items()
            },
            "last_check": datetime.now().isoformat(),
        }


# ═══════════════════════════════════════════════════════════
#  GLOBAL INSTANCE (app.py backward compat)
# ═══════════════════════════════════════════════════════════

self_optimizer = SelfOptimizer()
