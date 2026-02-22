# =====================================================
# QPA (Quantitative Price Action) - Otomatik Optimizer v1.0
# =====================================================
# ICT Optimizer'dan TAMAMEN BAĞIMSIZ çalışır.
# QPA sinyallerinin kazanma/kaybetme havuzunu analiz eder
# ve QPA parametrelerini otomatik günceller.
#
# Optimize edilen parametreler:
#   - min_qpa_score (sinyal eşiği)
#   - min_qpa_confidence (güven eşiği)
#   - Bileşen ağırlıkları (w_volatility, w_price_action, vb.)
#   - SL/TP oranları
#   - Pattern sensitivity parametreleri
# =====================================================

import logging
import json
from datetime import datetime
from database import (
    get_qpa_completed_signals, get_qpa_performance_summary,
    get_qpa_component_performance, save_bot_param, get_bot_param,
    add_qpa_optimization_log, get_all_bot_params,
    get_qpa_loss_analysis, get_qpa_confluence_analysis
)

logger = logging.getLogger("ICT-Bot.QPA-Optimizer")

# QPA Optimizer varsayılan ayarları
QPA_OPTIMIZER_CONFIG = {
    "min_trades": 5,
    "learning_rate": 0.05,
    "max_change_pct": 0.15,
    "win_rate_target": 0.60,
}


class QPAOptimizer:
    """
    QPA stratejisi için bağımsız self-learning optimizer.
    ICT optimizer ile aynı mimari ama farklı parametreler üzerinde çalışır.
    """

    def __init__(self):
        self.learning_rate = QPA_OPTIMIZER_CONFIG["learning_rate"]
        self.max_change = QPA_OPTIMIZER_CONFIG["max_change_pct"]
        self.min_trades = QPA_OPTIMIZER_CONFIG["min_trades"]
        self.target_win_rate = QPA_OPTIMIZER_CONFIG["win_rate_target"]

    def run_optimization(self):
        """
        QPA optimizasyon döngüsü.
        
        1. QPA win rate → sinyal eşiği kalibrasyonu
        2. QPA bileşen performansı → ağırlık ayarı
        3. QPA confluence → optimal skor tespiti
        4. QPA risk parametreleri (SL/TP)
        5. Kayıp analizi → derin öğrenme
        """
        logger.info("🔄 QPA Optimizasyon döngüsü başlatılıyor...")

        stats = get_qpa_performance_summary()
        total_trades = stats["total_trades"]

        if total_trades < self.min_trades:
            logger.info(f"QPA: Yeterli işlem yok ({total_trades}/{self.min_trades})")
            return {
                "status": "SKIPPED",
                "reason": f"QPA: Minimum {self.min_trades} işlem gerekli, şu an: {total_trades}",
                "changes": []
            }

        changes = []

        # 1. Win Rate bazlı güven eşiği
        wr_change = self._optimize_confidence(stats)
        if wr_change:
            changes.append(wr_change)

        # 2. Bileşen ağırlık ayarı
        weight_changes = self._optimize_weights(stats)
        changes.extend(weight_changes)

        # 3. Confluence skor eşiği
        conf_changes = self._optimize_score_threshold(stats)
        changes.extend(conf_changes)

        # 4. Risk parametreleri
        risk_changes = self._optimize_risk(stats)
        changes.extend(risk_changes)

        # 5. Kayıp analizi
        loss_changes = self._learn_from_losses()
        changes.extend(loss_changes)

        if changes:
            logger.info(f"✅ QPA Optimizasyon: {len(changes)} parametre güncellendi")
        else:
            logger.info("ℹ️ QPA Optimizasyon: Değişiklik gerekmedi")

        return {
            "status": "COMPLETED",
            "total_trades_analyzed": total_trades,
            "win_rate": stats["win_rate"],
            "changes": changes
        }

    def _optimize_confidence(self, stats):
        """QPA min_qpa_confidence ayarı"""
        from qpa_strategy import QPA_PARAMS

        current_wr = stats["win_rate"] / 100
        current_th = get_bot_param("qpa_min_qpa_confidence", QPA_PARAMS["min_qpa_confidence"])

        if current_wr < self.target_win_rate * 0.85:
            adj = self.learning_rate * (self.target_win_rate - current_wr) * 100
            new_th = min(90, current_th + adj)
            reason = f"QPA WR düşük ({stats['win_rate']}%), güven eşiği yükseltiliyor"
        elif current_wr > self.target_win_rate * 1.15:
            adj = self.learning_rate * 5
            new_th = max(50, current_th - adj)
            reason = f"QPA WR yüksek ({stats['win_rate']}%), güven eşiği düşürülüyor"
        else:
            return None

        new_th = round(new_th, 1)
        if abs(new_th - current_th) < 0.5:
            return None

        save_bot_param("qpa_min_qpa_confidence", new_th, QPA_PARAMS["min_qpa_confidence"])
        add_qpa_optimization_log(
            "min_qpa_confidence", current_th, new_th, reason,
            stats["win_rate"], stats["win_rate"], stats["total_trades"]
        )
        logger.info(f"📊 QPA Güven eşiği: {current_th} → {new_th}")
        return {"param": "min_qpa_confidence", "old": current_th, "new": new_th, "reason": reason}

    def _optimize_weights(self, stats):
        """QPA bileşen ağırlıklarını performansa göre ayarla"""
        from qpa_strategy import QPA_PARAMS
        changes = []

        comp_perf = get_qpa_component_performance()
        if not comp_perf:
            return changes

        # QPA bileşen → ağırlık parametresi eşleştirmesi
        weight_mapping = {
            "QPA_VOLATILITY": {"param": "w_volatility", "min": 5, "max": 35},
            "QPA_PRICE_ACTION": {"param": "w_price_action", "min": 10, "max": 40},
            "QPA_VOLUME_PROFILE": {"param": "w_volume", "min": 5, "max": 35},
            "QPA_MOMENTUM": {"param": "w_momentum", "min": 5, "max": 30},
            "QPA_SR_LEVELS": {"param": "w_sr_level", "min": 5, "max": 30},
            "QPA_CANDLE_STRUCTURE": {"param": "w_candle_struct", "min": 2, "max": 20},
        }

        for comp_name, cfg in weight_mapping.items():
            if comp_name not in comp_perf:
                continue

            comp = comp_perf[comp_name]
            if comp["total"] < 3:
                continue

            param = cfg["param"]
            qpa_param = f"qpa_{param}"
            win_rate = comp["win_rate"] / 100
            current = get_bot_param(qpa_param, QPA_PARAMS[param])

            if win_rate < 0.35:
                # Kötü performans → ağırlığı azalt
                new_val = max(cfg["min"], current - current * self.learning_rate)
                reason = f"{comp_name} düşük WR ({comp['win_rate']}%), ağırlık azaltılıyor"
            elif win_rate > 0.75:
                # Çok iyi → ağırlığı artır
                new_val = min(cfg["max"], current + current * self.learning_rate * 0.7)
                reason = f"{comp_name} yüksek WR ({comp['win_rate']}%), ağırlık artırılıyor"
            else:
                continue

            new_val = round(new_val, 1)
            if abs(new_val - current) < 0.5:
                continue

            save_bot_param(qpa_param, new_val, QPA_PARAMS[param])
            add_qpa_optimization_log(
                param, current, new_val, reason,
                stats["win_rate"], stats["win_rate"], stats["total_trades"]
            )
            changes.append({"param": param, "old": current, "new": new_val, "reason": reason})
            logger.info(f"📊 QPA {param}: {current} → {new_val}")

        return changes

    def _optimize_score_threshold(self, stats):
        """QPA min_qpa_score kalibrasyonu — confluence analizi"""
        from qpa_strategy import QPA_PARAMS
        changes = []

        analysis = get_qpa_confluence_analysis()
        if not analysis["buckets"]:
            return changes

        optimal = analysis["optimal_min_score"]
        if optimal is None:
            return changes

        current = get_bot_param("qpa_min_qpa_score", QPA_PARAMS["min_qpa_score"])
        target = max(45, min(85, optimal))

        if target > current + 2:
            new_val = min(target, current + self.learning_rate * 20)
            reason = f"QPA: Skor {target}+ bölgesi daha kârlı — eşik yükseltiliyor"
        elif target < current - 2:
            new_val = max(target, current - self.learning_rate * 15)
            reason = f"QPA: Düşük eşik yeterli — eşik düşürülüyor"
        else:
            return changes

        new_val = round(new_val, 1)
        if abs(new_val - current) < 0.5:
            return changes

        save_bot_param("qpa_min_qpa_score", new_val, QPA_PARAMS["min_qpa_score"])
        add_qpa_optimization_log(
            "min_qpa_score", current, new_val, reason,
            stats["win_rate"], stats["win_rate"], stats["total_trades"]
        )
        changes.append({"param": "min_qpa_score", "old": current, "new": new_val, "reason": reason})
        return changes

    def _optimize_risk(self, stats):
        """QPA SL/TP parametreleri"""
        from qpa_strategy import QPA_PARAMS
        changes = []

        completed = get_qpa_completed_signals(50)
        if len(completed) < self.min_trades:
            return changes

        winners = [s for s in completed if s["status"] == "WON"]
        losers = [s for s in completed if s["status"] == "LOST"]

        if not losers:
            return changes

        avg_loss = sum(abs(s["pnl_pct"]) for s in losers) / len(losers) if losers else 0
        avg_win = sum(abs(s["pnl_pct"]) for s in winners) / len(winners) if winners else 0

        loss_rate = len(losers) / len(completed)
        current_sl = get_bot_param("qpa_qpa_sl_pct", QPA_PARAMS["qpa_sl_pct"])

        # SL çok sık tetikleniyorsa genişlet
        if loss_rate > 0.55 and avg_loss > current_sl * 100 * 0.9:
            new_sl = min(current_sl * 1.1, 0.03)
            new_sl = round(new_sl, 4)
            if abs(new_sl - current_sl) > 0.0005:
                save_bot_param("qpa_qpa_sl_pct", new_sl, QPA_PARAMS["qpa_sl_pct"])
                reason = f"QPA kayıp oranı yüksek ({loss_rate:.0%}), SL genişletiliyor"
                add_qpa_optimization_log("qpa_sl_pct", current_sl, new_sl, reason,
                                         stats["win_rate"], stats["win_rate"], stats["total_trades"])
                changes.append({"param": "qpa_sl_pct", "old": current_sl, "new": new_sl, "reason": reason})

        # RR oranı ayarla
        if avg_win > 0 and avg_loss > 0:
            actual_rr = avg_win / avg_loss
            current_tp = get_bot_param("qpa_qpa_tp_ratio", QPA_PARAMS["qpa_tp_ratio"])
            if actual_rr < 1.5 and current_tp < 4.0:
                new_tp = min(current_tp + 0.2, 4.0)
                new_tp = round(new_tp, 1)
                save_bot_param("qpa_qpa_tp_ratio", new_tp, QPA_PARAMS["qpa_tp_ratio"])
                reason = f"QPA gerçek RR düşük ({actual_rr:.1f}), TP artırılıyor"
                add_qpa_optimization_log("qpa_tp_ratio", current_tp, new_tp, reason,
                                         stats["win_rate"], stats["win_rate"], stats["total_trades"])
                changes.append({"param": "qpa_tp_ratio", "old": current_tp, "new": new_tp, "reason": reason})

        return changes

    def _learn_from_losses(self):
        """QPA kayıp analizi — neden kaybettik?"""
        from qpa_strategy import QPA_PARAMS
        changes = []

        loss_info = get_qpa_loss_analysis(30)
        if loss_info["total_losses"] < 5:
            return changes

        stats = get_qpa_performance_summary()

        # Düşük güvenle girilen kayıplar çoksa
        if loss_info["total_losses"] > 0:
            low_conf_ratio = loss_info["low_confidence_losses"] / loss_info["total_losses"]
            if low_conf_ratio > 0.4:
                current = get_bot_param("qpa_min_qpa_confidence", QPA_PARAMS["min_qpa_confidence"])
                new_val = min(85, current + self.learning_rate * 15)
                new_val = round(new_val, 1)
                if new_val - current >= 1.0:
                    save_bot_param("qpa_min_qpa_confidence", new_val, QPA_PARAMS["min_qpa_confidence"])
                    reason = f"QPA kayıpların %{low_conf_ratio*100:.0f}'i düşük güvenli — eşik artırılıyor"
                    add_qpa_optimization_log("min_qpa_confidence", current, new_val, reason,
                                             stats["win_rate"], stats["win_rate"], stats["total_trades"])
                    changes.append({"param": "min_qpa_confidence", "old": current,
                                    "new": new_val, "reason": reason})
                    logger.info(f"🧠 QPA DERS: {reason}")

        # Ortalama kayıp yüksekse SL daralt
        if loss_info["avg_loss_pct"] > 2.0:
            current_sl = get_bot_param("qpa_qpa_sl_pct", QPA_PARAMS["qpa_sl_pct"])
            new_sl = max(0.008, current_sl * 0.92)
            new_sl = round(new_sl, 4)
            if abs(new_sl - current_sl) > 0.001:
                save_bot_param("qpa_qpa_sl_pct", new_sl, QPA_PARAMS["qpa_sl_pct"])
                reason = f"QPA ort. kayıp %{loss_info['avg_loss_pct']:.1f} yüksek — SL daraltılıyor"
                add_qpa_optimization_log("qpa_sl_pct", current_sl, new_sl, reason,
                                         stats["win_rate"], stats["win_rate"], stats["total_trades"])
                changes.append({"param": "qpa_sl_pct", "old": current_sl,
                                "new": new_sl, "reason": reason})
                logger.info(f"🧠 QPA DERS: {reason}")

        # Ders özetleri
        for lesson in loss_info.get("lesson_summary", []):
            logger.info(f"📝 QPA Ders: {lesson}")

        return changes

    def get_optimization_summary(self):
        """QPA optimizasyon özeti"""
        from qpa_strategy import QPA_PARAMS
        stats = get_qpa_performance_summary()
        all_params = get_all_bot_params()

        changed_params = {}
        for key, default_val in QPA_PARAMS.items():
            qpa_key = f"qpa_{key}"
            current_val = all_params.get(qpa_key, default_val)
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
            "last_check": datetime.now().isoformat()
        }


# Global instance
qpa_optimizer = QPAOptimizer()
