# =====================================================
# ICT Trading Bot - Otomatik Optimizasyon Modülü v2.0
# (Smart Money Concepts - Öğrenen Motor)
# =====================================================
# Kazanma/kaybetme havuzunu + confluence-kârlılık
# korelasyonunu analiz ederek ICT parametrelerini
# otomatik günceller, HTF bias/entry mode performans
# karşılaştırması yapar ve sürekli öğrenir.
#
# v2.0 EKLENENLER:
#   - Confluence Score ↔ Kârlılık korelasyon analizi
#   - Entry Mode (LIMIT vs MARKET) performans karşılaştırması
#   - HTF Bias doğruluk takibi
#   - min_confluence_score otomatik kalibrasyonu
#   - fvg_min_size_pct, liquidity_min_touches,
#     liquidity_sweep_lookback ICT parametreleri optimizasyonu
#   - Sweep olmadan girilen kayıp analizi
# =====================================================

import logging
import json
from datetime import datetime
from database import (
    get_completed_signals, get_performance_summary,
    get_component_performance, save_bot_param, get_bot_param,
    add_optimization_log, get_all_bot_params, get_loss_analysis,
    get_confluence_profitability_analysis, get_entry_mode_performance,
    get_htf_bias_accuracy
)
from config import ICT_PARAMS, OPTIMIZER_CONFIG, OPTIMIZER_PARAM_BOUNDS

logger = logging.getLogger("ICT-Bot.Optimizer")


class SelfOptimizer:
    """
    Otomatik öğrenen optimizer.
    Her optimizasyon döngüsünde:
    1. Tamamlanan işlemleri analiz et
    2. Bileşen bazlı performansı değerlendir
    3. Zayıf parametreleri tespit et
    4. Kontrollü şekilde güncelle
    5. Log tut
    """

    def __init__(self):
        self.learning_rate = OPTIMIZER_CONFIG["learning_rate"]
        self.max_change = OPTIMIZER_CONFIG["max_param_change_pct"]
        self.min_trades = OPTIMIZER_CONFIG["min_trades_for_optimization"]  # Config'den al (varsayılan: 5)
        self.target_win_rate = OPTIMIZER_CONFIG["win_rate_target"]
        self.optimization_history = []

    def _save_with_bounds(self, param_name, new_val, default_val):
        """
        Parametre kaydet (OPTIMIZER_PARAM_BOUNDS ile sınırlandır).
        Death spiral koruması: Parametre asla tanımlı sınırların dışına çıkamaz.
        """
        bounds = OPTIMIZER_PARAM_BOUNDS.get(param_name)
        if bounds:
            min_b, max_b = bounds
            old_val = new_val
            new_val = max(min_b, min(max_b, new_val))
            if new_val != old_val:
                logger.info(f"🛡️ {param_name} bounds clamp: {old_val} → {new_val} (sınır: {min_b}-{max_b})")
        save_bot_param(param_name, new_val, default_val)
        return new_val

    def enforce_bounds_on_startup(self):
        """
        Başlangıçta tüm DB parametrelerini kontrol et.
        Sınır dışındaki değerleri varsayılanlara sıfırla.
        Death spiral sonrası kurtarma mekanizması.
        """
        all_params = get_all_bot_params()
        reset_count = 0
        for param_name, current_val in all_params.items():
            bounds = OPTIMIZER_PARAM_BOUNDS.get(param_name)
            if bounds is None:
                continue
            min_b, max_b = bounds
            if current_val < min_b or current_val > max_b:
                default = ICT_PARAMS.get(param_name, current_val)
                logger.warning(
                    f"🔄 {param_name} sınır dışı: {current_val} → {default} "
                    f"(izin: {min_b}-{max_b})"
                )
                save_bot_param(param_name, default, default)
                reset_count += 1
        if reset_count:
            logger.info(f"🔄 {reset_count} parametre sınır dışında bulundu ve sıfırlandı")
        else:
            logger.info("✅ Tüm optimizer parametreleri sınırlar içinde")
        return reset_count

    def run_optimization(self):
        """
        Ana optimizasyon döngüsü.
        
        Adımlar:
          1. Win Rate → güven eşiği kalibrasyonu
          2. İCT bileşen performansı → parametre ince ayar
          3. Confluence Score ↔ Kârlılık korelasyonu → optimal skor tespiti
          4. Risk parametreleri (SL/TP) → gerçek RR'a göre ayar
          5. Sabırlı mod (bekleme süresi) → düşük güven WR'a göre
          6. Kayıp analizi → derin öğrenme (neden kaybettik?)
          7. HTF Bias doğruluk takibi → bilgilendirme
          8. Entry Mode performansı → bilgilendirme
        """
        logger.info("🔄 Optimizasyon döngüsü başlatılıyor...")

        stats = get_performance_summary()
        total_trades = stats["total_trades"]

        if total_trades < self.min_trades:
            logger.info(f"Yeterli işlem yok ({total_trades}/{self.min_trades}), optimizasyon atlanıyor.")
            return {
                "status": "SKIPPED",
                "reason": f"Tamamlanmış (WON/LOST) işlem sayısı: {total_trades} — minimum {self.min_trades} gerekli. Aktif işlemler sayılmaz.",
                "changes": [],
                "total_trades_analyzed": total_trades,
                "win_rate": stats["win_rate"]
            }

        changes = []

        # ★ ACİL MOD: Win rate %0 ve 3+ kayıp → agresif düzeltme
        current_wr = stats["win_rate"] / 100
        if current_wr == 0.0 and stats["losing_trades"] >= 3:
            emergency_changes = self._emergency_mode(stats)
            changes.extend(emergency_changes)
            if emergency_changes:
                logger.warning(f"🚨 ACİL MOD: %0 win rate, {stats['losing_trades']} kayıp → {len(emergency_changes)} acil düzeltme")

        # 1. Win Rate bazlı güven eşiği ayarlama
        wr_change = self._optimize_confidence_threshold(stats)
        if wr_change:
            changes.append(wr_change)

        # 2. Bileşen bazlı ağırlık ayarlama (ICT parametreleri)
        comp_changes = self._optimize_component_weights(stats)
        changes.extend(comp_changes)

        # 3. ★ Confluence Score ↔ Kârlılık korelasyonu
        conf_changes = self._optimize_confluence_threshold(stats)
        changes.extend(conf_changes)

        # 4. Risk yönetimi parametreleri
        risk_changes = self._optimize_risk_params(stats)
        changes.extend(risk_changes)

        # 5. Sabırlı mod ayarları
        patience_change = self._optimize_patience(stats)
        if patience_change:
            changes.append(patience_change)

        # 6. Kayıp analizi → derin öğrenme (neden kaybettik?)
        # Adım 1-5'te zaten değişen parametreleri topla → çift ayarlamayı önle
        already_changed = {c["param"] for c in changes}
        loss_changes = self._learn_from_losses(already_changed)
        changes.extend(loss_changes)

        # 7-8. Bilgilendirme analizleri (parametre değiştirmez, log tutar)
        self._log_htf_bias_accuracy()
        self._log_entry_mode_performance()

        if changes:
            logger.info(f"✅ Optimizasyon tamamlandı: {len(changes)} parametre güncellendi")
        else:
            logger.info("ℹ️ Optimizasyon: Değişiklik gerekli değil")

        return {
            "status": "COMPLETED",
            "total_trades_analyzed": total_trades,
            "win_rate": stats["win_rate"],
            "changes": changes
        }

    def _optimize_confidence_threshold(self, stats):
        """
        Win rate'e göre minimum güven eşiğini ayarla:
        - Win rate düşükse -> eşiği yükselt (daha seçici ol)
        - Win rate hedefin üstündeyse -> eşiği biraz düşür (daha fazla fırsat)
        """
        current_wr = stats["win_rate"] / 100
        current_threshold = get_bot_param("min_confidence", ICT_PARAMS["min_confidence"])

        if current_wr < self.target_win_rate * 0.85:
            # Win rate çok düşük, eşiği yükselt
            adjustment = self.learning_rate * (self.target_win_rate - current_wr) * 100
            new_threshold = min(90, current_threshold + adjustment)
            reason = f"Win rate düşük ({stats['win_rate']}%), güven eşiği yükseltiliyor"

        elif current_wr > self.target_win_rate * 1.15:
            # Win rate çok yüksek, biraz daha fazla sinyal üretilebilir
            adjustment = self.learning_rate * 5
            new_threshold = max(55, current_threshold - adjustment)
            reason = f"Win rate yüksek ({stats['win_rate']}%), güven eşiği düşürülüyor"

        else:
            return None

        new_threshold = round(new_threshold, 1)
        if abs(new_threshold - current_threshold) < 0.5:
            return None

        # Uygula ve logla
        self._save_with_bounds("min_confidence", new_threshold, ICT_PARAMS["min_confidence"])
        add_optimization_log(
            "min_confidence", current_threshold, new_threshold, reason,
            stats["win_rate"], stats["win_rate"], stats["total_trades"]
        )

        logger.info(f"📊 Güven eşiği: {current_threshold} -> {new_threshold} ({reason})")

        return {
            "param": "min_confidence",
            "old": current_threshold,
            "new": new_threshold,
            "reason": reason
        }

    def _optimize_component_weights(self, stats):
        """
        ICT bileşen bazlı performans analizi:
        - Başarılı bileşenlerin parametrelerini hafif gevşet (daha fazla yakalansın)
        - Başarısız bileşenlerin parametrelerini sıkılaştır (daha seçici ol)

        Optimize edilen ICT parametreleri:
          ORDER_BLOCK   → ob_body_ratio_min   (aralık: 0.3 - 0.7)
          FVG           → fvg_min_size_pct    (aralık: 0.001 - 0.005)
          LIQUIDITY_SWEEP → liquidity_equal_tolerance (aralık: 0.0005 - 0.003)
          DISPLACEMENT  → displacement_min_body_ratio (aralık: 0.5 - 0.85)
        """
        changes = []
        comp_perf = stats.get("component_performance", {})

        if not comp_perf:
            return changes

        # ICT bileşen → parametre eşleştirmesi + güvenli aralıklar
        # Her bileşenin performansına göre ilgili parametresi ayarlanır
        param_mapping = {
            "ORDER_BLOCK": {
                "param": "ob_body_ratio_min",
                "min_val": 0.3, "max_val": 0.7
            },
            "FVG": {
                "param": "fvg_min_size_pct",
                "min_val": 0.0005, "max_val": 0.005
            },
            "LIQUIDITY_SWEEP": {
                "param": "liquidity_equal_tolerance",
                "min_val": 0.0005, "max_val": 0.003
            },
            "DISPLACEMENT": {
                "param": "displacement_min_body_ratio",
                "min_val": 0.5, "max_val": 0.85
            },
            # Ek bileşen eşleştirmeleri (BUG 4 düzeltmesi)
            "MARKET_STRUCTURE": {
                "param": "swing_lookback",
                "min_val": 3, "max_val": 10
            },
            "BREAKER_BLOCK": {
                "param": "ob_max_age_candles",
                "min_val": 15, "max_val": 50
            },
            "HIGH_VOLUME_DISPLACEMENT": {
                "param": "displacement_min_size_pct",
                "min_val": 0.002, "max_val": 0.008
            },
        }

        for comp_name, cfg in param_mapping.items():
            if comp_name not in comp_perf:
                continue

            comp = comp_perf[comp_name]
            if comp["total"] < 5:  # Yeterli veri yok
                continue

            param_name = cfg["param"]
            win_rate = comp["win_rate"] / 100
            current_val = get_bot_param(param_name, ICT_PARAMS[param_name])
            new_val = current_val

            if win_rate < 0.4:
                # Kötü performans → daha seçici ol (parametreyi artır)
                adjustment = current_val * self.learning_rate
                new_val = min(cfg["max_val"], current_val + adjustment)
                reason = f"{comp_name} düşük WR ({comp['win_rate']}%), daha seçici"

            elif win_rate > 0.75:
                # Çok iyi performans → biraz gevşet (daha fazla fırsat)
                adjustment = current_val * self.learning_rate * 0.5
                new_val = max(cfg["min_val"], current_val - adjustment)
                reason = f"{comp_name} yüksek WR ({comp['win_rate']}%), biraz gevşetiliyor"

            else:
                continue

            # Max değişim sınırı (%15)
            max_change_abs = current_val * self.max_change
            if abs(new_val - current_val) > max_change_abs:
                new_val = current_val + (max_change_abs if new_val > current_val else -max_change_abs)

            new_val = round(new_val, 6)
            if abs(new_val - current_val) < current_val * 0.01:
                continue

            # Bounds kontrolü (death spiral koruması)
            new_val = self._save_with_bounds(param_name, new_val, ICT_PARAMS[param_name])
            add_optimization_log(
                param_name, current_val, new_val, reason,
                stats["win_rate"], stats["win_rate"], stats["total_trades"]
            )

            changes.append({
                "param": param_name,
                "old": current_val,
                "new": new_val,
                "reason": reason
            })

            logger.info(f"📊 {param_name}: {current_val} -> {new_val} ({reason})")

        return changes

    def _optimize_confluence_threshold(self, stats):
        """
        ★ Confluence Score ↔ Kârlılık Korelasyon Analizi

        Veritabanındaki tamamlanmış işlemlere bakarak:
        - "Score 80+ olan işlemler Score 60-70'e göre ne kadar daha kârlı?"
        - Optimal minimum confluence score'u otomatik tespit et
        - min_confluence_score parametresini buna göre kalibre et

        Bu, botun "Hangi skor düzeyinde işlem açmalıyım?" sorusuna
        veri odaklı cevap vermesini sağlar.
        """
        changes = []
        analysis = get_confluence_profitability_analysis()

        if not analysis["buckets"]:
            return changes

        optimal = analysis["optimal_min_score"]
        if optimal is None:
            return changes

        current_min = get_bot_param("min_confluence_score", ICT_PARAMS["min_confluence_score"])

        # Optimal → current'tan farklıysa ve makul aralıkta ise ayarla
        # Aralık sınırı: 50-85
        target = max(50, min(85, optimal))

        # Küçük adımlarla yaklaş (agresif değişiklik yok)
        if target > current_min + 2:
            new_val = min(target, current_min + self.learning_rate * 20)
            new_val = round(new_val, 1)
            reason = (f"Confluence analizi: skor {target}+ bölgesi daha kârlı — "
                      f"eşik {current_min} → {new_val}")
        elif target < current_min - 2:
            new_val = max(target, current_min - self.learning_rate * 15)
            new_val = round(new_val, 1)
            reason = (f"Confluence analizi: düşük eşik yeterli — "
                      f"eşik {current_min} → {new_val}")
        else:
            return changes

        if abs(new_val - current_min) < 0.5:
            return changes

        save_bot_param("min_confluence_score", new_val, ICT_PARAMS["min_confluence_score"])
        # Bounds kontrolü
        bounds = OPTIMIZER_PARAM_BOUNDS.get("min_confluence_score")
        if bounds:
            new_val = max(bounds[0], min(bounds[1], new_val))
            save_bot_param("min_confluence_score", new_val, ICT_PARAMS["min_confluence_score"])
        add_optimization_log(
            "min_confluence_score", current_min, new_val, reason,
            stats["win_rate"], stats["win_rate"], stats["total_trades"]
        )
        changes.append({
            "param": "min_confluence_score",
            "old": current_min,
            "new": new_val,
            "reason": reason
        })
        logger.info(f"🎯 Confluence kalibrasyonu: {current_min} → {new_val}")

        # Bucket detaylarını logla
        for label, b in analysis["buckets"].items():
            logger.info(f"  📈 Score {label}: {b['total']} işlem, "
                        f"WR={b['win_rate']}%, avgPnL={b['avg_pnl']}%")

        return changes

    def _optimize_risk_params(self, stats):
        """
        Risk yönetimi parametrelerini optimize et:
        - Ortalama RR oranına göre TP/SL ayarla
        - Kaybeden işlemlerdeki SL mesafesini analiz et
        """
        changes = []

        completed = get_completed_signals(50)
        if len(completed) < self.min_trades:
            return changes

        # Kazanan/kaybeden analizi
        winners = [s for s in completed if s["status"] == "WON"]
        losers = [s for s in completed if s["status"] == "LOST"]

        if not losers:
            return changes

        # Ortalama kayıp yüzdesi
        avg_loss = sum(abs(s["pnl_pct"]) for s in losers) / len(losers) if losers else 0
        avg_win = sum(abs(s["pnl_pct"]) for s in winners) / len(winners) if winners else 0

        current_sl = get_bot_param("default_sl_pct", ICT_PARAMS["default_sl_pct"])
        current_tp_ratio = get_bot_param("default_tp_ratio", ICT_PARAMS["default_tp_ratio"])

        # SL çok sık tetikleniyorsa (kayıp oranı yüksek) ve ortalama kayıp artıyorsa
        loss_rate = len(losers) / len(completed) if completed else 0

        if loss_rate > 0.55 and avg_loss > current_sl * 100 * 0.9:
            # SL'yi biraz genişlet (market noise'ı azalt)
            new_sl = min(current_sl * 1.1, 0.03)  # Max %3
            new_sl = round(new_sl, 4)

            if abs(new_sl - current_sl) > 0.0005:
                new_sl = self._save_with_bounds("default_sl_pct", new_sl, ICT_PARAMS["default_sl_pct"])
                reason = f"Kayıp oranı yüksek ({loss_rate:.0%}), SL genişletiliyor"
                add_optimization_log(
                    "default_sl_pct", current_sl, new_sl, reason,
                    stats["win_rate"], stats["win_rate"], stats["total_trades"]
                )
                changes.append({
                    "param": "default_sl_pct",
                    "old": current_sl,
                    "new": new_sl,
                    "reason": reason
                })

        # RR oranı ayarlama
        if avg_win > 0 and avg_loss > 0:
            actual_rr = avg_win / avg_loss
            if actual_rr < 1.5 and current_tp_ratio < 3.0:
                new_tp_ratio = min(current_tp_ratio + 0.2, 3.0)
                new_tp_ratio = round(new_tp_ratio, 1)

                new_tp_ratio = self._save_with_bounds("default_tp_ratio", new_tp_ratio, ICT_PARAMS["default_tp_ratio"])
                reason = f"Gerçek RR düşük ({actual_rr:.1f}), TP oranı artırılıyor"
                add_optimization_log(
                    "default_tp_ratio", current_tp_ratio, new_tp_ratio, reason,
                    stats["win_rate"], stats["win_rate"], stats["total_trades"]
                )
                changes.append({
                    "param": "default_tp_ratio",
                    "old": current_tp_ratio,
                    "new": new_tp_ratio,
                    "reason": reason
                })

        return changes

    def _optimize_patience(self, stats):
        """
        Sabırlı mod optimizasyonu:
        - Watch'tan promote edilen sinyallerin başarısını analiz et
        - Bekleme süresini ayarla
        """
        current_watch = get_bot_param("patience_watch_candles", ICT_PARAMS["patience_watch_candles"])
        completed = get_completed_signals(50)

        if len(completed) < self.min_trades:
            return None

        # Düşük güvenle açılan işlemlerin sonuçlarını analiz et
        low_conf_trades = [s for s in completed if s["confidence"] and s["confidence"] < 70]
        high_conf_trades = [s for s in completed if s["confidence"] and s["confidence"] >= 70]

        if not low_conf_trades or not high_conf_trades:
            return None

        low_wr = sum(1 for s in low_conf_trades if s["status"] == "WON") / len(low_conf_trades)
        high_wr = sum(1 for s in high_conf_trades if s["status"] == "WON") / len(high_conf_trades)

        # Düşük güvenli işlemler çok başarısızsa, daha sabırlı ol
        if low_wr < 0.35 and current_watch < 5:
            new_watch = min(current_watch + 1, 5)
            reason = f"Düşük güvenli WR: {low_wr:.0%}, bekleme artırılıyor"

            save_bot_param("patience_watch_candles", new_watch, ICT_PARAMS["patience_watch_candles"])
            add_optimization_log(
                "patience_watch_candles", current_watch, new_watch, reason,
                stats["win_rate"], stats["win_rate"], stats["total_trades"]
            )

            return {
                "param": "patience_watch_candles",
                "old": current_watch,
                "new": new_watch,
                "reason": reason
            }

        return None

    def get_optimization_summary(self):
        """Optimizasyon özetini döndür — tüm yeni analizler dahil."""
        stats = get_performance_summary()
        all_params = get_all_bot_params()
        loss_info = get_loss_analysis(30)
        confluence_analysis = get_confluence_profitability_analysis()
        entry_mode_perf = get_entry_mode_performance()
        htf_accuracy = get_htf_bias_accuracy()

        # Varsayılandan değişen parametreleri bul
        changed_params = {}
        for key, default_val in ICT_PARAMS.items():
            current_val = all_params.get(key, default_val)
            if isinstance(current_val, (int, float)) and isinstance(default_val, (int, float)):
                if abs(current_val - default_val) > 0.0001:
                    changed_params[key] = {
                        "default": default_val,
                        "current": current_val,
                        "change_pct": round(((current_val - default_val) / default_val) * 100, 1)
                                      if default_val != 0 else 0
                    }

        return {
            "total_optimizations": len(changed_params),
            "current_win_rate": stats["win_rate"],
            "target_win_rate": self.target_win_rate * 100,
            "changed_params": changed_params,
            "performance": stats,
            "loss_lessons": loss_info.get("lesson_summary", []),
            "confluence_analysis": confluence_analysis,
            "entry_mode_performance": entry_mode_perf,
            "htf_bias_accuracy": htf_accuracy,
            "last_check": datetime.now().isoformat()
        }

    def _log_htf_bias_accuracy(self):
        """
        HTF Bias doğruluk takibi — 4H yön tayini ne kadar isabetli?
        Sadece loglar, parametre değiştirmez (bilgilendirme amaçlı).
        """
        accuracy = get_htf_bias_accuracy()
        if not accuracy:
            return

        for bias, data in accuracy.items():
            logger.info(
                f"📊 HTF Bias '{bias}': {data['total']} işlem, "
                f"WR={data['win_rate']}%"
            )
            if data["total"] >= 5 and data["win_rate"] < 40:
                logger.warning(
                    f"⚠️ HTF Bias '{bias}' düşük doğruluk ({data['win_rate']}%) — "
                    f"bu bias ile dikkatli ol"
                )

    def _log_entry_mode_performance(self):
        """
        LIMIT vs MARKET giriş performansı — hangisi daha kârlı?
        Sadece loglar, parametre değiştirmez (bilgilendirme amaçlı).
        """
        perf = get_entry_mode_performance()
        if not perf:
            return

        for mode, data in perf.items():
            logger.info(
                f"📊 Entry Mode '{mode}': {data['total']} işlem, "
                f"WR={data['win_rate']}%, avgPnL={data['avg_pnl']}%"
            )


    def _learn_from_losses(self, already_changed=None):
        """
        Kayıp analizi yaparak otomatik ders çıkar.
        Neden kaybettik? Hangi bileşen eksikti? Hangi bileşen yanılttı?
        
        Args:
            already_changed: Aynı döngüde daha önce değiştirilen param isimleri seti.
                           Çift ayarlamayı önlemek için bu parametreler atlanır.
        """
        if already_changed is None:
            already_changed = set()

        changes = []
        loss_info = get_loss_analysis(30)

        if loss_info["total_losses"] < 5:
            return changes

        stats = get_performance_summary()

        # 1. Düşük güvenle girilen kayıplar çoğunluksa → min_confidence artır
        #    (Adım 1'de zaten ayarlandıysa atla — çift ayarlama koruması)
        if "min_confidence" not in already_changed and loss_info["total_losses"] > 0:
            low_conf_ratio = loss_info["low_confidence_losses"] / loss_info["total_losses"]
            if low_conf_ratio > 0.4:
                current = get_bot_param("min_confidence", ICT_PARAMS["min_confidence"])
                # Küçük adımlarla artır (agresif değil, ideal)
                new_val = min(80, current + self.learning_rate * 15)
                new_val = round(new_val, 1)
                if new_val - current >= 1.0:
                    new_val = self._save_with_bounds("min_confidence", new_val, ICT_PARAMS["min_confidence"])
                    reason = (f"Kayıpların %{low_conf_ratio*100:.0f}'i düşük güvenli — "
                             f"eşik {current} → {new_val}")
                    add_optimization_log("min_confidence", current, new_val, reason,
                                        stats["win_rate"], stats["win_rate"], stats["total_trades"])
                    changes.append({"param": "min_confidence", "old": current,
                                   "new": new_val, "reason": reason})
                    logger.info(f"🧠 DERS: {reason}")
        elif "min_confidence" in already_changed:
            logger.debug("ℹ️ min_confidence bu döngüde zaten ayarlandı, kayıp analizi atlıyor")

        # 2. En çok eksik olan bileşeni kontrol et → confluence eşiğini ayarla
        #    (Adım 3'te zaten ayarlandıysa atla — çift ayarlama koruması)
        missing = loss_info.get("missing_components", {})
        total_losses = loss_info["total_losses"]

        # Displacement kayıplarda çok eksikse → confluence eşiğini hafif artır
        if "min_confluence_score" not in already_changed:
            disp_missing = missing.get("DISPLACEMENT", 0)
            if total_losses > 0 and disp_missing / total_losses > 0.6:
                current = get_bot_param("displacement_min_body_ratio",
                                       ICT_PARAMS["displacement_min_body_ratio"])
                # Displacement parametresini sıkılaştırmak yerine, confluence eşiğini hafif artır
                current_conf = get_bot_param("min_confluence_score", ICT_PARAMS["min_confluence_score"])
                new_conf = min(75, current_conf + 1.0)
                if new_conf > current_conf:
                    new_conf = self._save_with_bounds("min_confluence_score", new_conf, ICT_PARAMS["min_confluence_score"])
                    reason = (f"Kayıpların %{disp_missing/total_losses*100:.0f}'inde DISPLACEMENT eksik — "
                             f"confluence {current_conf} → {new_conf}")
                    add_optimization_log("min_confluence_score", current_conf, new_conf, reason,
                                        stats["win_rate"], stats["win_rate"], stats["total_trades"])
                    changes.append({"param": "min_confluence_score", "old": current_conf,
                                   "new": new_conf, "reason": reason})
                    logger.info(f"🧠 DERS: {reason}")
        else:
            logger.debug("ℹ️ min_confluence_score bu döngüde zaten ayarlandı, kayıp analizi atlıyor")

        # 3. HTF onaysız kayıplar çoksa → uyar
        htf_missing = missing.get("HTF_CONFIRMATION", 0)
        if total_losses > 0 and htf_missing / total_losses > 0.65:
            reason = (f"Kayıpların %{htf_missing/total_losses*100:.0f}'inde HTF onayı yoktu — "
                     f"HTF uyumu kritik")
            logger.info(f"🧠 NOT: {reason}")

        # 4. Sweep olmadan girilen kayıplar çoksa → uyar
        sweep_missing = missing.get("LIQUIDITY_SWEEP", 0)
        if total_losses > 0 and sweep_missing / total_losses > 0.5:
            reason = (f"Kayıpların %{sweep_missing/total_losses*100:.0f}'inde Sweep yoktu — "
                     f"Sweep gate'i kritik önemde")
            logger.info(f"🧠 NOT: {reason}")

        # 5. Ortalama kayıp büyükse → SL mesafesini kontrol et
        #    (Adım 4'te zaten ayarlandıysa atla — çelişkili yön koruması)
        if "default_sl_pct" not in already_changed and loss_info["avg_loss_pct"] > 2.0:
            current_sl = get_bot_param("default_sl_pct", ICT_PARAMS["default_sl_pct"])
            # SL çok geniş olabilir, daralt
            new_sl = max(0.008, current_sl * 0.92)
            new_sl = round(new_sl, 4)
            if abs(new_sl - current_sl) > 0.001:
                new_sl = self._save_with_bounds("default_sl_pct", new_sl, ICT_PARAMS["default_sl_pct"])
                reason = (f"Ortalama kayıp %{loss_info['avg_loss_pct']:.1f} çok yüksek — "
                         f"SL {current_sl} → {new_sl}")
                add_optimization_log("default_sl_pct", current_sl, new_sl, reason,
                                    stats["win_rate"], stats["win_rate"], stats["total_trades"])
                changes.append({"param": "default_sl_pct", "old": current_sl,
                               "new": new_sl, "reason": reason})
                logger.info(f"🧠 DERS: {reason}")
        elif "default_sl_pct" in already_changed:
            logger.debug("ℹ️ default_sl_pct bu döngüde zaten ayarlandı, kayıp analizi atlıyor")

        # Ders özetini logla
        for lesson in loss_info.get("lesson_summary", []):
            logger.info(f"📝 Optimizer Ders: {lesson}")

        return changes


    def _emergency_mode(self, stats):
        """
        🚨 ACİL MOD — %0 win rate ile ardışık kayıplarda tetiklenir.
        TEK SEFER çalışır (death spiral koruması):
        - Sadece SL mesafesini hafif artır (kayıp büyüklüğünü azalt)
        - confidence/confluence'a DOKUNMAZ (çok artırırsa hiç işlem açılmaz)
        """
        changes = []

        # Acil mod sadece ilk 5 kayıpta tetiklenir, sonra normal öğrenmeye bırakır
        if stats["losing_trades"] > 8:
            logger.info("🚨 Acil mod atlandı — yeterli veri toplandı, normal öğrenme devrede")
            return changes

        # SL mesafesini hafif artır (kayma koruması)
        current_sl = get_bot_param("default_sl_pct", ICT_PARAMS["default_sl_pct"])
        if current_sl < 0.02:
            new_sl = min(0.02, current_sl * 1.10)
            new_sl = round(new_sl, 4)
            new_sl = self._save_with_bounds("default_sl_pct", new_sl, ICT_PARAMS["default_sl_pct"])
            if new_sl > current_sl:
                reason = f"🚨 ACİL: SL mesafesi artırılıyor {current_sl} → {new_sl}"
                add_optimization_log("default_sl_pct", current_sl, new_sl, reason,
                                    stats["win_rate"], stats["win_rate"], stats["total_trades"])
                changes.append({"param": "default_sl_pct", "old": current_sl,
                               "new": new_sl, "reason": reason})

        return changes


# Global instance
self_optimizer = SelfOptimizer()
