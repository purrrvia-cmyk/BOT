# =====================================================
# ICT Trading Bot — SMC Parameter Optimizer v4.0
# (Narrative → POI → Trigger Threshold Optimizer)
# =====================================================
#
# v4.0 UYARLAMA: Gate sistemi kaldırıldı.
# Yeni mimari: Narrative (4H yapı) → POI (OB+FVG+Likidite) → Trigger
#
# MANTIK:
#   Bot, veritabanındaki WON/LOST işlemleri analiz ederek
#   ICT strateji motorundaki geometrik ve hacimsel eşikleri
#   (threshold) otomatik optimize eder.
#
# OPTİMİZE EDİLEN PARAMETRELER:
# ┌──────────────────────────────────┬────────────┬────────────────┐
# │ Parametre                        │ Katman     │ Güvenli Aralık │
# ├──────────────────────────────────┼────────────┼────────────────┤
# │ displacement_min_body_ratio      │ Trigger    │ 0.40 – 0.75    │
# │ displacement_min_size_pct        │ Trigger    │ 0.002 – 0.010  │
# │ displacement_atr_multiplier      │ Trigger    │ 1.00 – 2.50    │
# │ bos_min_displacement             │ Narrative  │ 0.001 – 0.006  │
# │ fvg_min_size_pct                 │ POI        │ 0.0003 – 0.004 │
# │ fvg_max_age_candles              │ POI        │ 10 – 40        │
# │ liquidity_equal_tolerance        │ POI        │ 0.0003 – 0.003 │
# │ ob_body_ratio_min                │ POI        │ 0.25 – 0.65    │
# │ ob_max_age_candles               │ POI        │ 15 – 50        │
# │ swing_lookback                   │ Yapısal    │ 3 – 8          │
# │ default_sl_pct                   │ Risk       │ 0.008 – 0.025  │
# │ poi_max_distance_pct             │ POI        │ 0.005 – 0.020  │
# │ min_rr_ratio                     │ Risk       │ 1.20 – 3.00    │
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
    SMC Parameter Optimizer v4.1 — Target-Based Adaptive Optimizer.

    WON/LOST işlem verilerinden öğrenerek ICT strateji motorunun
    geometrik ve hacimsel eşik değerlerini otomatik optimize eder.

    v4.1 FARKLAR (v4.0'dan):
      1. TARGET-BASED: Tüm koşullar hedef WR (%55) bazlı
      2. COMPONENT-AWARE: Hangi trigger tipi kötü ise o katman öncelikli
      3. MAX 4 CHANGE: Döngü başına max 4 parametre değişir
      4. ROLLBACK: Son değişiklik WR'yi düşürdüyse geri alınır
      5. PRIORITY: Parametreler etki büyüklüğüne göre sıralanır

    Mimari:
      1. Bileşen performansını analiz et (SWEEP %47, MSS %33, DISP %100)
      2. Kötü bileşenlere ait parametreleri önceliklendir
      3. Rollback kontrolü (son değişiklik kötüleştirdi mi?)
      4. Hedef-adaptif adım hesapla (hedefe uzaksa büyük, yakınsa küçük)
      5. Max 4 en öncelikli parametreyi değiştir

    Bileşen → Parametre İlişkisi:
      SWEEP/REJECTION → liquidity_equal_tolerance, swing_lookback
      MSS            → bos_min_displacement, ob_body_ratio_min
      DISPLACEMENT   → displacement_*, fvg_*
      HTF_BIAS       → bos_min_displacement, swing_lookback
      POI_ZONE       → poi_max_distance_pct, ob_max_age_candles, fvg_max_age_candles
      (Risk)         → default_sl_pct, min_rr_ratio
    """

    # ═══════════════════════════════════════════════════════════
    #  BİLEŞEN → PARAMETRE HARİTASI
    #  Hangi bileşen kötüyse hangi parametreler optimize edilecek
    # ═══════════════════════════════════════════════════════════

    COMPONENT_PARAM_MAP = {
        "SWEEP": ["liquidity_equal_tolerance", "swing_lookback"],
        "REJECTION": ["liquidity_equal_tolerance", "displacement_min_body_ratio"],
        "MSS": ["bos_min_displacement", "ob_body_ratio_min", "swing_lookback"],
        "DISPLACEMENT": ["displacement_min_body_ratio", "displacement_atr_multiplier", "displacement_min_size_pct"],
        "HTF_BIAS": ["bos_min_displacement", "swing_lookback"],
        "POI_ZONE": ["poi_max_distance_pct", "ob_max_age_candles", "fvg_max_age_candles", "fvg_min_size_pct"],
    }

    # Her döngüde max kaç parametre değişebilir
    MAX_CHANGES_PER_CYCLE = 4

    # ═══════════════════════════════════════════════════════════
    #  PARAMETRE REJİSTRİSİ
    #  Her parametrenin güvenli sınırları, grubu ve açıklaması
    # ═══════════════════════════════════════════════════════════

    PARAM_REGISTRY = {
        # ── Trigger Katmanı: Displacement Kalitesi ──
        "displacement_min_body_ratio": {
            "bounds": (0.40, 0.75),
            "group": "trigger",
            "desc": "Displacement mumunun minimum gövde/fitil oranı",
        },
        "displacement_min_size_pct": {
            "bounds": (0.002, 0.010),
            "group": "trigger",
            "desc": "Minimum displacement boyutu (fiyatın %'si)",
        },
        "displacement_atr_multiplier": {
            "bounds": (1.00, 2.50),
            "group": "trigger",
            "desc": "Displacement ATR çarpanı (şiddet ölçüsü)",
        },

        # ── Narrative Katmanı: Yapı Kırılımı ──
        "bos_min_displacement": {
            "bounds": (0.001, 0.006),
            "group": "narrative",
            "desc": "BOS için minimum kırılım oranı",
        },

        # ── POI Katmanı: FVG Kalitesi ──
        "fvg_min_size_pct": {
            "bounds": (0.0003, 0.004),
            "group": "poi",
            "desc": "Minimum FVG boyutu (fiyatın %'si)",
        },
        "fvg_max_age_candles": {
            "bounds": (10, 40),
            "group": "poi",
            "desc": "FVG geçerlilik süresi (mum sayısı)",
        },

        # ── POI Katmanı: Likidite Sweep Hassasiyeti ──
        "liquidity_equal_tolerance": {
            "bounds": (0.0003, 0.003),
            "group": "poi",
            "desc": "Equal high/low toleransı (milimetrik hassasiyet)",
        },

        # ── POI Katmanı: Order Block & Swing ──
        "ob_body_ratio_min": {
            "bounds": (0.25, 0.65),
            "group": "poi",
            "desc": "Order Block mumunun minimum gövde oranı",
        },
        "ob_max_age_candles": {
            "bounds": (15, 50),
            "group": "poi",
            "desc": "Order Block geçerlilik süresi (mum sayısı)",
        },
        "swing_lookback": {
            "bounds": (3, 8),
            "group": "structural",
            "desc": "Swing noktası tespiti bakış penceresi",
        },

        # ── POI Katmanı: Confluence Mesafe ──
        "poi_max_distance_pct": {
            "bounds": (0.005, 0.020),
            "group": "poi",
            "desc": "POI bölgesine max uzaklık eşiği (%)",
        },

        # ── Risk: SL / RR ──
        "default_sl_pct": {
            "bounds": (0.008, 0.025),
            "group": "risk",
            "desc": "Fallback SL yüzdesi (yapısal SL bulunamazsa)",
        },
        "min_rr_ratio": {
            "bounds": (1.20, 3.00),
            "group": "risk",
            "desc": "Minimum Risk:Reward oranı eşiği",
        },
    }

    GROUP_DESCRIPTIONS = {
        "trigger": "Trigger Katmanı — Displacement kalitesi ve momentum",
        "narrative": "Narrative Katmanı — 4H yapı analizi (BOS/CHoCH)",
        "poi": "POI Katmanı — OB, FVG, Likidite confluence kalitesi",
        "structural": "Yapısal — Swing noktası tespiti",
        "risk": "Risk Yönetimi — SL ve RR eşikleri",
    }

    def __init__(self):
        self.learning_rate = OPTIMIZER_CONFIG.get("learning_rate", 0.03)
        self.max_change_pct = OPTIMIZER_CONFIG.get("max_param_change_pct", 0.10)
        self.min_trades = OPTIMIZER_CONFIG.get("min_trades_for_optimization", 20)
        self.target_win_rate = OPTIMIZER_CONFIG.get("win_rate_target", 0.55)
        self._last_trade_count = 0
        # Rollback tracking: son optimizasyon anındaki WR
        self._last_optimization_wr = None
        self._last_optimization_changes = []
        logger.info("SMC Parameter Optimizer v4.1 başlatıldı — Target-Based Adaptive Optimization")

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

        v4.1 Akış:
          1. Yeterli veri kontrolü (min 20 kapanmış işlem)
          2. ROLLBACK: Son değişiklikler WR'yi düşürdüyse geri al
          3. BİLEŞEN ANALİZİ: Hangi trigger tipi kaybettiriyor?
          4. ÖNCELİKLEME: Kötü bileşenlere ait parametreler önce
          5. TÜM parametreleri hesapla ama MAX 4 UYGULANIR
          6. Seans/HTF bilgi analizi
        """
        logger.info("🔄 SMC Optimizer v4.1 — Optimizasyon döngüsü başlatılıyor...")

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

        # ═══ ADIM 1: ROLLBACK KONTROLÜ ═══
        rollback_changes = self._check_rollback(pool, stats)
        changes.extend(rollback_changes)

        # ═══ ADIM 2: ACİL MOD (%0 WR + 3+ kayıp) ═══
        if pool["win_rate"] == 0 and len(pool["losers"]) >= 3:
            emergency = self._emergency_mode(pool, stats)
            changes.extend(emergency)

        # Rollback veya acil mod aktifse normal optimizasyonu atla
        if changes:
            self._post_optimization(changes, pool, stats, total_trades)
            return {
                "status": "COMPLETED",
                "total_trades_analyzed": total_trades,
                "win_rate": stats["win_rate"],
                "changes": changes,
            }

        # ═══ ADIM 3: BİLEŞEN PERFORMANS ANALİZİ ═══
        comp_perf = get_component_performance()
        priority_params = self._get_priority_params(comp_perf, pool)

        logger.info(f"📊 Bileşen bazlı öncelik sırası: {[p['param'] for p in priority_params[:6]]}")

        # ═══ ADIM 4: TÜM DEĞİŞİKLİKLERİ HESAPLA ═══
        already_changed = set()
        all_candidates = []

        # Her katmandan değişiklik adaylarını topla
        all_candidates.extend(self._optimize_displacement(pool, stats, already_changed))
        all_candidates.extend(self._optimize_fvg(pool, stats, already_changed))
        all_candidates.extend(self._optimize_liquidity(pool, stats, already_changed))
        all_candidates.extend(self._optimize_structural(pool, stats, already_changed))
        all_candidates.extend(self._optimize_risk(pool, stats, already_changed))
        all_candidates.extend(self._optimize_poi_confluence(pool, stats, already_changed))
        all_candidates.extend(self._optimize_narrative(pool, stats, already_changed))

        # ═══ ADIM 5: ÖNCELİKLEME + MAX 4 LİMİT ═══
        changes = self._select_top_changes(all_candidates, priority_params)

        # ═══ ADIM 6: BİLGİ ANALİZLERİ ═══
        self._log_session_analysis(pool)
        self._log_htf_bias_analysis()
        self._log_component_analysis(comp_perf)

        # ═══ SONUÇ ═══
        self._post_optimization(changes, pool, stats, total_trades)

        return {
            "status": "COMPLETED",
            "total_trades_analyzed": total_trades,
            "win_rate": stats["win_rate"],
            "changes": changes,
        }

    def _post_optimization(self, changes, pool, stats, total_trades):
        """Optimizasyon sonrası: logla ve state'i kaydet."""
        if changes:
            logger.info(
                f"✅ SMC Optimizasyon tamamlandı: {len(changes)} parametre güncellendi "
                f"(max {self.MAX_CHANGES_PER_CYCLE})"
            )
            for c in changes:
                logger.info(
                    f"   → {c['param']}: {c['old']} → {c['new']} "
                    f"[{c.get('group', '?')}] priority={c.get('priority', '?')}"
                )
        else:
            logger.info("ℹ️ Optimizasyon: Tüm parametreler optimal aralıkta veya hedefte")

        # Rollback tracking için state kaydet
        self._last_optimization_wr = pool["win_rate"]
        self._last_optimization_changes = [
            {"param": c["param"], "old": c["old"], "new": c["new"]}
            for c in changes
        ]
        self._last_trade_count = total_trades

    # ═══════════════════════════════════════════════════════════
    #  ROLLBACK KONTROLÜ
    # ═══════════════════════════════════════════════════════════

    def _check_rollback(self, pool, stats):
        """
        Son optimizasyondan sonra WR düştüyse → değişiklikleri geri al.

        Mantık:
          - Son opt. WR'si biliniyorsa ve şu anki WR 3+ puan düştüyse
          - Son değiştirilen parametreleri eski değerlerine döndür
          - En son 1 döngü geri alınır (zincirleme rollback yok)
        """
        changes = []

        if self._last_optimization_wr is None or not self._last_optimization_changes:
            return changes

        current_wr = pool["win_rate"]
        last_wr = self._last_optimization_wr
        wr_drop = last_wr - current_wr

        # WR 3+ puan düştüyse rollback
        if wr_drop >= 3.0 and len(pool["completed"]) >= self.min_trades + 2:
            logger.warning(
                f"🔙 ROLLBACK: WR {last_wr:.1f}% → {current_wr:.1f}% "
                f"({wr_drop:.1f} puan düşüş) — son {len(self._last_optimization_changes)} "
                f"değişiklik geri alınıyor"
            )

            for prev_change in self._last_optimization_changes:
                param = prev_change["param"]
                old_val = prev_change["old"]  # Geri dönülecek değer
                current_val = get_bot_param(param, ICT_PARAMS.get(param))

                reason = (
                    f"🔙 ROLLBACK: WR {wr_drop:.1f} puan düştü "
                    f"({last_wr:.1f}%→{current_wr:.1f}%), "
                    f"{param} {current_val} → {old_val} geri alındı"
                )

                default_val = ICT_PARAMS.get(param, old_val)
                save_bot_param(param, old_val, default_val)
                add_optimization_log(param, current_val, old_val, reason,
                                     current_wr, current_wr, stats["total_trades"])

                registry = self.PARAM_REGISTRY.get(param, {})
                changes.append({
                    "param": param,
                    "old": current_val,
                    "new": old_val,
                    "reason": reason,
                    "bounds": list(registry.get("bounds", (0, 0))),
                    "group": registry.get("group", "?"),
                    "priority": "ROLLBACK",
                })

                logger.info(f"🔙 {param}: {current_val} → {old_val} (rollback)")

            # Rollback sonrası state temizle (zincirleme rollback engeli)
            self._last_optimization_wr = None
            self._last_optimization_changes = []

        return changes

    # ═══════════════════════════════════════════════════════════
    #  BİLEŞEN BAZLI ÖNCELİKLEME
    # ═══════════════════════════════════════════════════════════

    def _get_priority_params(self, comp_perf, pool):
        """
        Bileşen performansına göre parametreleri önceliklendir.

        Mantık:
          1. Her bileşenin WR'sini al (SWEEP %47, MSS %33, vb.)
          2. WR'si en düşük bileşenin parametrelerine en yüksek öncelik ver
          3. Risk parametreleri her zaman orta öncelikli (her zaman relevant)

        Returns:
            Sıralı liste: [{"param": "...", "priority_score": float, "reason": "..."}, ...]
        """
        target_wr = self.target_win_rate * 100
        param_priorities = {}

        # Bileşen bazlı öncelikleme
        for comp_name, comp_data in comp_perf.items():
            comp_wr = comp_data.get("win_rate", 50)
            comp_total = comp_data.get("total", 0)

            if comp_total < 3:
                continue  # Yetersiz veri

            # Hedeften uzaklık = öncelik puanı (yüksek = öncelikli)
            gap = target_wr - comp_wr  # Pozitif = kötü performans

            # Bu bileşene bağlı parametreleri bul
            mapped_params = self.COMPONENT_PARAM_MAP.get(comp_name, [])
            for param in mapped_params:
                if param not in param_priorities:
                    param_priorities[param] = {
                        "param": param,
                        "priority_score": 0,
                        "reasons": [],
                    }
                # En kötü bileşenin gap'ini kullan (birden fazla bileşen aynı parametreyi etkileyebilir)
                param_priorities[param]["priority_score"] = max(
                    param_priorities[param]["priority_score"], gap
                )
                param_priorities[param]["reasons"].append(
                    f"{comp_name}:{comp_wr:.0f}%"
                )

        # Risk parametreleri her zaman orta öncelik
        for risk_param in ["default_sl_pct", "min_rr_ratio"]:
            if risk_param not in param_priorities:
                param_priorities[risk_param] = {
                    "param": risk_param,
                    "priority_score": (target_wr - pool["win_rate"]) * 0.5,
                    "reasons": ["risk-always-relevant"],
                }

        # Sırala: en yüksek priority_score en önce
        sorted_params = sorted(
            param_priorities.values(),
            key=lambda x: -x["priority_score"]
        )

        return sorted_params

    def _select_top_changes(self, all_candidates, priority_params):
        """
        Tüm aday değişikliklerden max MAX_CHANGES_PER_CYCLE kadarını seç.

        Seçim kriterleri:
          1. Bileşen bazlı öncelik sırasına göre (kötü bileşen = yüksek öncelik)
          2. Aynı bileşenden birden fazla parametre seçme (çeşitlilik)
          3. Acil mod değişiklikleri her zaman dahil
        """
        if not all_candidates:
            return []

        # Priority map oluştur
        priority_map = {p["param"]: p["priority_score"] for p in priority_params}

        # Her adaya öncelik puanı ata
        for candidate in all_candidates:
            candidate["priority"] = priority_map.get(candidate["param"], 0)

        # Önceliğe göre sırala
        all_candidates.sort(key=lambda c: -c["priority"])

        # Max limit uygula + grup çeşitliliği sağla
        selected = []
        selected_groups = {}

        for candidate in all_candidates:
            if len(selected) >= self.MAX_CHANGES_PER_CYCLE:
                break

            group = candidate.get("group", "?")
            # Aynı gruptan max 2 parametre
            if selected_groups.get(group, 0) >= 2:
                continue

            selected.append(candidate)
            selected_groups[group] = selected_groups.get(group, 0) + 1

        # Seçilen adayları DB'ye kaydet
        if selected:
            self._commit_changes(selected)
            logger.info(
                f"🎯 {len(all_candidates)} aday değişiklikten {len(selected)} seçildi "
                f"ve uygulandı (max {self.MAX_CHANGES_PER_CYCLE})"
            )

        return selected

    def _log_component_analysis(self, comp_perf):
        """Bileşen performansını logla — optimizer karar gerekçesi."""
        if not comp_perf:
            return

        target_wr = self.target_win_rate * 100
        logger.info("📊 ─── Bileşen Performans Raporu ───")
        for comp, data in sorted(comp_perf.items(), key=lambda x: x[1].get("win_rate", 0)):
            wr = data.get("win_rate", 0)
            total = data.get("total", 0)
            status = "🔴" if wr < target_wr - 10 else "🟡" if wr < target_wr else "🟢"
            logger.info(f"   {status} {comp}: WR={wr:.0f}%, {total} işlem")
            if wr < target_wr - 10 and total >= 3:
                mapped = self.COMPONENT_PARAM_MAP.get(comp, [])
                if mapped:
                    logger.info(f"      → Hedef parametreler: {', '.join(mapped)}")

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
    #  HEDEF BAZLI ADIM HESAPLAMA
    # ═══════════════════════════════════════════════════════════

    def _calc_adaptive_step(self, current_val, win_rate, direction="up"):
        """
        Hedef WR'ye uzaklığa göre adaptif adım hesapla.

        WR hedefe ne kadar uzaksa adım o kadar büyük.
        WR hedefe yakınsa adım küçük (ince ayar).

        direction: "up" = parametreyi artır, "down" = azalt
        """
        target = self.target_win_rate * 100  # 55%
        gap = target - win_rate  # Pozitif = hedefin altında

        if gap <= 0:
            # Hedefin üzerinde → küçük adım (gevşetme)
            intensity = 0.5
        elif gap <= 5:
            # Hedefe yakın (50-55%) → normal adım
            intensity = 1.0
        elif gap <= 10:
            # Orta mesafe (45-50%) → büyük adım
            intensity = 1.5
        else:
            # Uzak (< 45%) → agresif adım
            intensity = 2.0

        step = abs(current_val) * self.learning_rate * intensity
        return step if direction == "up" else -step

    # ═══════════════════════════════════════════════════════════
    #  1. DISPLACEMENT PARAMETRELERİ (Trigger Katmanı)
    # ═══════════════════════════════════════════════════════════

    def _optimize_displacement(self, pool, stats, already_changed):
        """
        Displacement kalitesini WON/LOST analizinden öğren.

        v4.1 FARK: Koşullar artık target_win_rate bazlı.
        WR < hedef (%55) ise optimize et, uzaklığa göre adım büyüklüğü ayarla.

        Kararlar:
        ┌──────────────────────────┬──────────────────────────────────┐
        │ Durum                    │ Aksiyon                          │
        ├──────────────────────────┼──────────────────────────────────┤
        │ WR < hedef + hızlı kayıp│ body_ratio ↑  atr_mult ↑        │
        │ yüksek                   │ → Zayıf momentum filtrelemesi    │
        ├──────────────────────────┼──────────────────────────────────┤
        │ WR < hedef + ort kayıp  │ body_ratio ↑ size_pct ↑          │
        │ yüksek                   │ → Displacement boyutu yetersiz   │
        ├──────────────────────────┼──────────────────────────────────┤
        │ WR > hedef+10 + yeterli │ body_ratio ↓  (hafif)            │
        │ veri                     │ → Daha fazla setup yakala        │
        └──────────────────────────┴──────────────────────────────────┘
        """
        changes = []

        if pool["total"] < self.min_trades:
            return changes

        avg_loss = pool["avg_loss_pnl"]
        quick_loss_ratio = pool["quick_loss_ratio"]
        win_rate = pool["win_rate"]
        target_wr = self.target_win_rate * 100  # 55

        # ────────────────────────────────────────
        # displacement_min_body_ratio
        # ────────────────────────────────────────
        param = "displacement_min_body_ratio"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            if win_rate < target_wr and quick_loss_ratio > 0.25:
                # Hedefin altında + hızlı kayıplar var → displacement gövdesi zayıf
                step = self._calc_adaptive_step(current, win_rate, "up")
                new_val = current + step
                reason = (
                    f"WR ({win_rate:.1f}%) hedefin ({target_wr:.0f}%) altında, "
                    f"hızlı kayıp oranı {quick_loss_ratio:.0%}, "
                    f"displacement_min_body_ratio {current:.2f}'den "
                    f"{min(new_val, self.PARAM_REGISTRY[param]['bounds'][1]):.2f}'e güncellendi "
                    f"(daha güçlü gövde gerekli)"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif win_rate >= target_wr + 10 and pool["total"] >= 30:
                # Hedefin çok üzerinde → hafif gevşet
                step = self._calc_adaptive_step(current, win_rate, "up") * 0.3
                new_val = current - abs(step)
                reason = (
                    f"WR yüksek ({win_rate:.1f}%), "
                    f"displacement_min_body_ratio {current:.2f}'den "
                    f"{max(new_val, self.PARAM_REGISTRY[param]['bounds'][0]):.2f}'e gevşetildi"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        # ────────────────────────────────────────
        # displacement_atr_multiplier
        # ────────────────────────────────────────
        param = "displacement_atr_multiplier"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            if win_rate < target_wr and quick_loss_ratio > 0.20:
                # Hedefin altında + hızlı kayıplar → momentum yetersiz
                step = self._calc_adaptive_step(current, win_rate, "up")
                new_val = current + step
                reason = (
                    f"WR ({win_rate:.1f}%) hedefin altında, "
                    f"hızlı kayıp oranı {quick_loss_ratio:.0%}, "
                    f"displacement_atr_multiplier {current:.2f}'den "
                    f"{min(new_val, self.PARAM_REGISTRY[param]['bounds'][1]):.2f}'e güncellendi "
                    f"(daha güçlü momentum gerekli)"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif win_rate >= target_wr + 10 and avg_loss < 1.0:
                # Hedefin çok üzerinde → gevşet
                step = self._calc_adaptive_step(current, win_rate, "up") * 0.3
                new_val = current - abs(step)
                reason = (
                    f"WR yüksek ({win_rate:.1f}%), ort kayıp düşük ({avg_loss:.2f}%), "
                    f"displacement_atr_multiplier {current:.2f}'den "
                    f"{max(new_val, self.PARAM_REGISTRY[param]['bounds'][0]):.2f}'e gevşetildi"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        # ────────────────────────────────────────
        # displacement_min_size_pct
        # ────────────────────────────────────────
        param = "displacement_min_size_pct"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            if win_rate < target_wr and avg_loss > 1.0:
                # Hedefin altında + kayıplar büyük → displacement boyutu yetersiz
                step = self._calc_adaptive_step(current, win_rate, "up")
                new_val = current + step
                reason = (
                    f"WR ({win_rate:.1f}%) hedefin altında, ort kayıp {avg_loss:.2f}%, "
                    f"displacement_min_size_pct {current:.4f}'den "
                    f"{min(new_val, self.PARAM_REGISTRY[param]['bounds'][1]):.4f}'e güncellendi"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif win_rate >= target_wr + 10 and pool["total"] >= 25:
                step = self._calc_adaptive_step(current, win_rate, "up") * 0.3
                new_val = current - abs(step)
                reason = (
                    f"WR iyi ({win_rate:.1f}%), "
                    f"displacement_min_size_pct {current:.4f}'den "
                    f"{max(new_val, self.PARAM_REGISTRY[param]['bounds'][0]):.4f}'e gevşetildi"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        return changes

    # ═══════════════════════════════════════════════════════════
    #  2. FVG PARAMETRELERİ (POI Katmanı)
    # ═══════════════════════════════════════════════════════════

    def _optimize_fvg(self, pool, stats, already_changed):
        """
        FVG kalitesini WON/LOST analizinden öğren.

        v4.1 FARK: target_win_rate bazlı koşullar.

        Kararlar:
        ┌──────────────────────────┬──────────────────────────────────┐
        │ Durum                    │ Aksiyon                          │
        ├──────────────────────────┼──────────────────────────────────┤
        │ WR < hedef               │ fvg_min_size_pct ↑               │
        │                          │ → Küçük FVG'leri ele             │
        ├──────────────────────────┼──────────────────────────────────┤
        │ WR < hedef               │ fvg_max_age_candles ↓            │
        │                          │ → Eski FVG'ler güvenilmez        │
        ├──────────────────────────┼──────────────────────────────────┤
        │ WR > hedef+10            │ fvg_min_size_pct ↓ (hafif)       │
        │                          │ → Daha fazla FVG yakala          │
        └──────────────────────────┴──────────────────────────────────┘
        """
        changes = []

        if pool["total"] < self.min_trades:
            return changes

        realized_rr = pool["realized_rr"]
        win_rate = pool["win_rate"]
        target_wr = self.target_win_rate * 100

        # ────────────────────────────────────────
        # fvg_min_size_pct
        # ────────────────────────────────────────
        param = "fvg_min_size_pct"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            if win_rate < target_wr:
                # Hedefin altında → küçük FVG'leri filtrele
                step = self._calc_adaptive_step(current, win_rate, "up")
                new_val = current + step
                reason = (
                    f"WR ({win_rate:.1f}%) hedefin ({target_wr:.0f}%) altında, "
                    f"fvg_min_size_pct {current:.5f}'den "
                    f"{min(new_val, self.PARAM_REGISTRY[param]['bounds'][1]):.5f}'e güncellendi "
                    f"(daha büyük FVG hedefleme)"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif win_rate >= target_wr + 10 and realized_rr > 2.0:
                # Hedefin çok üzerinde + RR iyi → gevşet
                step = self._calc_adaptive_step(current, win_rate, "up") * 0.3
                new_val = current - abs(step)
                reason = (
                    f"WR iyi ({win_rate:.1f}%) ve RR iyi ({realized_rr:.2f}), "
                    f"fvg_min_size_pct {current:.5f}'den "
                    f"{max(new_val, self.PARAM_REGISTRY[param]['bounds'][0]):.5f}'e gevşetildi"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        # ────────────────────────────────────────
        # fvg_max_age_candles
        # ────────────────────────────────────────
        param = "fvg_max_age_candles"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            if win_rate < target_wr and pool["total"] >= 20:
                # Hedefin altında → eski FVG'leri kısıtla
                step = max(1, self._calc_adaptive_step(current, win_rate, "up") * 0.5)
                new_val = current - abs(step)
                reason = (
                    f"WR ({win_rate:.1f}%) hedefin altında, "
                    f"fvg_max_age_candles {int(current)}'den "
                    f"{max(int(new_val), self.PARAM_REGISTRY[param]['bounds'][0])}'e azaltıldı "
                    f"(daha taze FVG hedefleme)"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif win_rate >= target_wr + 10:
                # Hedefin üzerinde → eski FVG'leri de dahil et
                step = max(1, abs(current * self.learning_rate * 0.3))
                new_val = current + step
                reason = (
                    f"WR yüksek ({win_rate:.1f}%), "
                    f"fvg_max_age_candles {int(current)}'den "
                    f"{min(int(new_val), self.PARAM_REGISTRY[param]['bounds'][1])}'e genişletildi"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        return changes

    # ═══════════════════════════════════════════════════════════
    #  3. LİKİDİTE PARAMETRELERİ (POI Katmanı)
    # ═══════════════════════════════════════════════════════════

    def _optimize_liquidity(self, pool, stats, already_changed):
        """
        Likidite sweep kalitesini analiz et.

        v4.1 FARK: target_win_rate bazlı koşullar.

        Kararlar:
        ┌──────────────────────────┬──────────────────────────────────┐
        │ Durum                    │ Aksiyon                          │
        ├──────────────────────────┼──────────────────────────────────┤
        │ WR < hedef + hızlı kayıp │ tolerance ↓                      │
        │                          │ → Sahte sweep'leri ele           │
        ├──────────────────────────┼──────────────────────────────────┤
        │ WR > hedef+10            │ tolerance ↑ (hafif)              │
        │                          │ → Daha fazla seviye yakala       │
        └──────────────────────────┴──────────────────────────────────┘
        """
        changes = []

        if pool["total"] < self.min_trades:
            return changes

        quick_loss_ratio = pool["quick_loss_ratio"]
        win_rate = pool["win_rate"]
        target_wr = self.target_win_rate * 100

        # ────────────────────────────────────────
        # liquidity_equal_tolerance
        # ────────────────────────────────────────
        param = "liquidity_equal_tolerance"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            if win_rate < target_wr and quick_loss_ratio > 0.20:
                # Hedefin altında + hızlı kayıplar → sahte sweep'ler
                step = self._calc_adaptive_step(current, win_rate, "up")
                new_val = current - abs(step)  # Tolerans küçült = daha hassas
                reason = (
                    f"WR ({win_rate:.1f}%) hedefin altında, "
                    f"hızlı kayıp oranı {quick_loss_ratio:.0%}, "
                    f"liquidity_equal_tolerance {current:.5f}'den "
                    f"{max(new_val, self.PARAM_REGISTRY[param]['bounds'][0]):.5f}'e "
                    f"sıkılaştırıldı (sahte sweep filtresi)"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif win_rate >= target_wr + 10 and quick_loss_ratio < 0.15:
                # Hedefin çok üzerinde → hafif gevşet
                step = self._calc_adaptive_step(current, win_rate, "up") * 0.3
                new_val = current + abs(step)
                reason = (
                    f"WR yüksek ({win_rate:.1f}%), hızlı kayıp düşük ({quick_loss_ratio:.0%}), "
                    f"liquidity_equal_tolerance {current:.5f}'den "
                    f"{min(new_val, self.PARAM_REGISTRY[param]['bounds'][1]):.5f}'e "
                    f"gevşetildi (daha fazla seviye)"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        return changes

    # ═══════════════════════════════════════════════════════════
    #  4. YAPISAL PARAMETRELER (OB, Swing)
    # ═══════════════════════════════════════════════════════════

    def _optimize_structural(self, pool, stats, already_changed):
        """
        Order Block ve swing noktası parametrelerini optimize et.

        v4.1 FARK: target_win_rate bazlı koşullar.

        Kararlar:
        ┌──────────────────────────┬──────────────────────────────────┐
        │ Durum                    │ Aksiyon                          │
        ├──────────────────────────┼──────────────────────────────────┤
        │ WR < hedef               │ ob_body ↑, ob_age ↓, swing ↑    │
        │                          │ → Daha kaliteli yapısal veri     │
        ├──────────────────────────┼──────────────────────────────────┤
        │ WR > hedef+10            │ ob_body ↓ (hafif)                │
        │                          │ → Daha fazla yapı belirlensin    │
        └──────────────────────────┴──────────────────────────────────┘
        """
        changes = []

        if pool["total"] < self.min_trades:
            return changes

        win_rate = pool["win_rate"]
        avg_loss = pool["avg_loss_pnl"]
        target_wr = self.target_win_rate * 100

        # ────────────────────────────────────────
        # ob_body_ratio_min
        # ────────────────────────────────────────
        param = "ob_body_ratio_min"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            if win_rate < target_wr and pool["total"] >= 20:
                step = self._calc_adaptive_step(current, win_rate, "up")
                new_val = current + step
                reason = (
                    f"WR ({win_rate:.1f}%) hedefin ({target_wr:.0f}%) altında, "
                    f"ob_body_ratio_min {current:.2f}'den "
                    f"{min(new_val, self.PARAM_REGISTRY[param]['bounds'][1]):.2f}'e güncellendi "
                    f"(OB kalite filtresi sıkılaştırıldı)"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif win_rate >= target_wr + 10 and pool["total"] >= 20:
                step = self._calc_adaptive_step(current, win_rate, "up") * 0.3
                new_val = current - abs(step)
                reason = (
                    f"WR yüksek ({win_rate:.1f}%), "
                    f"ob_body_ratio_min {current:.2f}'den "
                    f"{max(new_val, self.PARAM_REGISTRY[param]['bounds'][0]):.2f}'e gevşetildi"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        # ────────────────────────────────────────
        # ob_max_age_candles
        # ────────────────────────────────────────
        param = "ob_max_age_candles"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            if win_rate < target_wr and avg_loss > 0.8:
                # Hedefin altında → eski OB'leri kısıtla
                step = max(1, self._calc_adaptive_step(current, win_rate, "up") * 0.3)
                new_val = current - abs(step)
                reason = (
                    f"WR ({win_rate:.1f}%) hedefin altında, ort kayıp {avg_loss:.2f}%, "
                    f"ob_max_age_candles {int(current)}'den "
                    f"{max(int(new_val), self.PARAM_REGISTRY[param]['bounds'][0])}'e "
                    f"azaltıldı (daha taze OB hedefleme)"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif win_rate >= target_wr + 10:
                # Hedefin çok üzerinde → gevşet
                step = max(1, abs(current * self.learning_rate * 0.3))
                new_val = current + step
                reason = (
                    f"WR yüksek ({win_rate:.1f}%), "
                    f"ob_max_age_candles {int(current)}'den "
                    f"{min(int(new_val), self.PARAM_REGISTRY[param]['bounds'][1])}'e genişletildi"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        # ────────────────────────────────────────
        # swing_lookback
        # ────────────────────────────────────────
        param = "swing_lookback"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            if win_rate < target_wr and pool["quick_loss_ratio"] > 0.25:
                # Hedefin altında + hızlı kayıplar → swing seviyeleri hassas
                new_val = current + 1
                reason = (
                    f"WR ({win_rate:.1f}%) hedefin altında, "
                    f"hızlı kayıp oranı {pool['quick_loss_ratio']:.0%}, "
                    f"swing_lookback {int(current)}'den {int(new_val)}'e artırıldı "
                    f"(daha güvenilir swing seviyeleri)"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif win_rate >= target_wr + 10 and current > 4:
                # Hedefin çok üzerinde + lookback büyük → gevşet
                new_val = current - 1
                reason = (
                    f"WR yüksek ({win_rate:.1f}%), "
                    f"swing_lookback {int(current)}'den {int(new_val)}'e azaltıldı "
                    f"(daha fazla swing noktası)"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        return changes

    # ═══════════════════════════════════════════════════════════
    #  5. RİSK PARAMETRELERİ (SL, TP)
    # ═══════════════════════════════════════════════════════════

    def _optimize_risk(self, pool, stats, already_changed):
        """
        SL ve min RR parametrelerini gerçekleşen trade sonuçlarından öğren.

        v4.1 FARK: target_win_rate bazlı koşullar.

        Kararlar:
        ┌──────────────────────────┬─────────────────────────────────┐
        │ Durum                    │ Aksiyon                         │
        ├──────────────────────────┼─────────────────────────────────┤
        │ WR < hedef + kayıp       │ default_sl_pct ↑                │
        │ ort SL'den küçük         │ → Noise SL tetikliyor           │
        ├──────────────────────────┼─────────────────────────────────┤
        │ WR < hedef + kayıp büyük │ default_sl_pct ↓                │
        │                          │ → SL çok geniş                  │
        ├──────────────────────────┼─────────────────────────────────┤
        │ WR < hedef + RR düşük    │ min_rr_ratio ↑                  │
        │                          │ → Kalite filtresi sıkılaştır    │
        ├──────────────────────────┼─────────────────────────────────┤
        │ WR > hedef + RR düşük    │ min_rr_ratio ↓                  │
        │                          │ → Daha fazla setup yakala       │
        └──────────────────────────┴─────────────────────────────────┘
        """
        changes = []

        if pool["total"] < self.min_trades:
            return changes

        avg_win = pool["avg_win_pnl"]
        avg_loss = pool["avg_loss_pnl"]
        win_rate = pool["win_rate"]
        realized_rr = pool["realized_rr"]
        target_wr = self.target_win_rate * 100

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

            if win_rate < target_wr and avg_loss < sl_as_pct * 0.8:
                # Hedefin altında + kayıplar SL'den küçük → noise tetikliyor
                step = self._calc_adaptive_step(current, win_rate, "up")
                new_val = current + step
                reason = (
                    f"WR ({win_rate:.1f}%) hedefin altında, "
                    f"ort kayıp ({avg_loss:.2f}%) SL'den küçük → noise koruması, "
                    f"default_sl_pct {current:.4f}'den "
                    f"{min(new_val, self.PARAM_REGISTRY[param]['bounds'][1]):.4f}'e genişletildi"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif win_rate < target_wr and avg_loss > sl_as_pct * 1.2:
                # Hedefin altında + kayıplar SL'den büyük → SL çok geniş
                step = self._calc_adaptive_step(current, win_rate, "up")
                new_val = current - abs(step)
                reason = (
                    f"WR ({win_rate:.1f}%) hedefin altında, "
                    f"ort kayıp ({avg_loss:.2f}%) SL'den büyük → SL daraltılıyor, "
                    f"default_sl_pct {current:.4f}'den "
                    f"{max(new_val, self.PARAM_REGISTRY[param]['bounds'][0]):.4f}'e daraltıldı"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        # ────────────────────────────────────────
        # min_rr_ratio
        # ────────────────────────────────────────
        param = "min_rr_ratio"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            if win_rate >= target_wr and realized_rr < 1.3:
                # Hedefin üzerinde ama RR düşük → daha fazla setup yakala
                new_val = current - 0.1
                reason = (
                    f"WR iyi ({win_rate:.1f}%) ama RR düşük ({realized_rr:.2f}), "
                    f"min_rr_ratio {current:.2f}'den "
                    f"{max(new_val, self.PARAM_REGISTRY[param]['bounds'][0]):.2f}'e "
                    f"gevşetildi (daha fazla setup)"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif win_rate < target_wr:
                # Hedefin altında → RR eşiğini artır (sadece yüksek RR setuplara gir)
                step = 0.05 + (target_wr - win_rate) / 100  # WR uzaksa daha büyük adım
                new_val = current + step
                reason = (
                    f"WR ({win_rate:.1f}%) hedefin ({target_wr:.0f}%) altında, "
                    f"min_rr_ratio {current:.2f}'den "
                    f"{min(new_val, self.PARAM_REGISTRY[param]['bounds'][1]):.2f}'e "
                    f"artırıldı (sadece yüksek RR setuplara gir)"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        return changes

    # ═══════════════════════════════════════════════════════════
    #  6. POI CONFLUENCE PARAMETRELERİ
    # ═══════════════════════════════════════════════════════════

    def _optimize_poi_confluence(self, pool, stats, already_changed):
        """
        POI bölgesi ile fiyat arasındaki mesafe eşiğini optimize et.

        v4.1 FARK: target_win_rate bazlı koşullar.

        Kararlar:
        ┌──────────────────────────┬─────────────────────────────────┐
        │ Durum                    │ Aksiyon                         │
        ├──────────────────────────┼─────────────────────────────────┤
        │ WR < hedef + hızlı kayıp │ poi_max_distance_pct ↓          │
        │                          │ → POI'ye daha yakın giriş       │
        ├──────────────────────────┼─────────────────────────────────┤
        │ WR > hedef+10            │ poi_max_distance_pct ↑ (hafif)  │
        │                          │ → Daha fazla setup yakala       │
        └──────────────────────────┴─────────────────────────────────┘
        """
        changes = []

        if pool["total"] < self.min_trades:
            return changes

        win_rate = pool["win_rate"]
        quick_loss_ratio = pool["quick_loss_ratio"]
        realized_rr = pool["realized_rr"]
        target_wr = self.target_win_rate * 100

        param = "poi_max_distance_pct"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            if win_rate < target_wr and quick_loss_ratio > 0.20:
                # Hedefin altında + hızlı kayıplar → POI'ye daha yakın gir
                step = self._calc_adaptive_step(current, win_rate, "up")
                new_val = current - abs(step)
                reason = (
                    f"WR ({win_rate:.1f}%) hedefin altında, "
                    f"hızlı kayıp oranı {quick_loss_ratio:.0%}, "
                    f"poi_max_distance_pct {current:.4f}'den "
                    f"{max(new_val, self.PARAM_REGISTRY[param]['bounds'][0]):.4f}'e "
                    f"daraltıldı (POI'ye daha yakın giriş)"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif win_rate >= target_wr + 10 and realized_rr > 1.5:
                # Hedefin çok üzerinde → hafif genişlet
                step = self._calc_adaptive_step(current, win_rate, "up") * 0.3
                new_val = current + abs(step)
                reason = (
                    f"WR yüksek ({win_rate:.1f}%), RR iyi ({realized_rr:.2f}), "
                    f"poi_max_distance_pct {current:.4f}'den "
                    f"{min(new_val, self.PARAM_REGISTRY[param]['bounds'][1]):.4f}'e "
                    f"gevşetildi (daha fazla setup)"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

        return changes

    # ═══════════════════════════════════════════════════════════
    #  7. NARRATIVE PARAMETRELERİ (BOS Hassasiyeti)
    # ═══════════════════════════════════════════════════════════

    def _optimize_narrative(self, pool, stats, already_changed):
        """
        BOS (Break of Structure) kırılım hassasiyetini optimize et.

        v4.1 FARK: target_win_rate bazlı koşullar.

        Kararlar:
        ┌──────────────────────────┬─────────────────────────────────┐
        │ Durum                    │ Aksiyon                         │
        ├──────────────────────────┼─────────────────────────────────┤
        │ WR < hedef               │ bos_min_displacement ↑          │
        │                          │ → Sahte BOS'ları filtrele       │
        ├──────────────────────────┼─────────────────────────────────┤
        │ WR > hedef+10            │ bos_min_displacement ↓ (hafif)  │
        │                          │ → Daha fazla narrative yakala   │
        └──────────────────────────┴─────────────────────────────────┘
        """
        changes = []

        if pool["total"] < self.min_trades:
            return changes

        win_rate = pool["win_rate"]
        quick_loss_ratio = pool["quick_loss_ratio"]
        avg_loss = pool["avg_loss_pnl"]
        target_wr = self.target_win_rate * 100

        param = "bos_min_displacement"
        if param not in already_changed:
            current = get_bot_param(param, ICT_PARAMS[param])

            if win_rate < target_wr:
                # Hedefin altında → BOS hassasiyetini artır
                step = self._calc_adaptive_step(current, win_rate, "up")
                new_val = current + step
                reason = (
                    f"WR ({win_rate:.1f}%) hedefin ({target_wr:.0f}%) altında, "
                    f"bos_min_displacement {current:.4f}'den "
                    f"{min(new_val, self.PARAM_REGISTRY[param]['bounds'][1]):.4f}'e "
                    f"artırıldı (daha güçlü BOS gerekli)"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
                if change:
                    changes.append(change)

            elif win_rate >= target_wr + 10 and pool["total"] < 30:
                # Hedefin çok üzerinde ama az işlem → gevşet
                step = self._calc_adaptive_step(current, win_rate, "up") * 0.3
                new_val = current - abs(step)
                reason = (
                    f"WR yüksek ({win_rate:.1f}%) ama az işlem ({pool['total']}), "
                    f"bos_min_displacement {current:.4f}'den "
                    f"{max(new_val, self.PARAM_REGISTRY[param]['bounds'][0]):.4f}'e "
                    f"gevşetildi (daha fazla narrative)"
                )
                change = self._prepare_change(param, current, new_val, reason, stats)
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
        v4.0: MARKET-only — entry mode karşılaştırması artık geçersiz.
        Geriye uyumluluk için boş bırakıldı, çağrılmaz.
        """
        pass

    # ═══════════════════════════════════════════════════════════
    #  YARDIMCI METODLAR
    # ═══════════════════════════════════════════════════════════

    def _prepare_change(self, param_name, current_val, new_val, reason, stats):
        """
        Parametre değişikliğini HESAPLA ama KAYDETME (aday oluştur).

        Kontroller:
          1. Max değişim limiti (%10)
          2. Sınır kontrolü (bounds clamp)
          3. Integer/float uyumu
          4. Minimum anlamlı değişiklik (%1)

        Returns:
            dict: Aday değişiklik bilgisi veya None (geçersizse)
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

        return {
            "param": param_name,
            "old": current_val,
            "new": new_val,
            "reason": reason,
            "bounds": [min_b, max_b],
            "group": registry["group"],
            "_stats": stats,  # commit sırasında lazım olacak
        }

    def _commit_changes(self, candidates, stats=None):
        """
        Seçilmiş aday değişiklikleri DB'ye kaydet.

        Args:
            candidates: _prepare_change'den dönen aday listesi
            stats: Performans istatistikleri (yoksa adaydan alınır)
        """
        for c in candidates:
            s = stats or c.get("_stats", {})
            default_val = ICT_PARAMS.get(c["param"], c["old"])
            save_bot_param(c["param"], c["new"], default_val)
            add_optimization_log(
                c["param"], c["old"], c["new"], c["reason"],
                s.get("win_rate", 0), s.get("win_rate", 0),
                s.get("total_trades", 0),
            )
            logger.info(f"📊 {c['param']}: {c['old']} → {c['new']} | {c['reason']}")
            # Temizlik: iç alanı kaldır
            c.pop("_stats", None)

    def _apply_change(self, param_name, current_val, new_val, reason, stats):
        """
        Parametre değişikliğini HEMEN uygula (acil mod / rollback için).

        prepare + commit'i tek çağrıda yapar.
        Returns:
            dict: Değişiklik bilgisi veya None
        """
        candidate = self._prepare_change(param_name, current_val, new_val, reason, stats)
        if candidate:
            self._commit_changes([candidate], stats)
        return candidate

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

        Geriye uyumlu alanlar korundu + v4.0 alanları eklendi:
        - optimizer_version, param_groups, realized_rr
        - changed_params artık bounds ve group bilgisi içerir
        """
        stats = get_performance_summary()
        all_params = get_all_bot_params()
        loss_info = get_loss_analysis(30)
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
            "optimizer_version": "4.1 — Target-Based Adaptive Optimizer (Narrative → POI → Trigger)",
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
