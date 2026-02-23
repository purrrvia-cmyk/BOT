#!/usr/bin/env python3
# =============================================================================
# ICT Sinyal Backtest & Doğrulama Sistemi
# =============================================================================
# Bu script:
#   1. forex_ict.py'nin ürettiği ICT sinyallerini tarihsel veri üzerinde test eder
#   2. Walk-forward simülasyonuyla gerçekçi sonuçlar üretir
#   3. Güncel verilerle canlı test yapar
#   4. Detaylı performans raporu çıkarır
# =============================================================================

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import logging
import json
import time

# Bot modüllerini import et
from forex_ict import ForexICTEngine, FOREX_INSTRUMENTS

logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
logger = logging.getLogger("Backtest")

# =============================================================================
# BÖLÜM 1: Tarihsel Veri Çekme
# =============================================================================

def fetch_historical_data(yf_symbol, period="60d", interval="1h"):
    """yfinance'tan tarihsel veri çek"""
    try:
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period=period, interval=interval, auto_adjust=True)
        if df.empty:
            return pd.DataFrame()
        df.columns = [c.lower() for c in df.columns]
        df = df[["open", "high", "low", "close", "volume"]].copy()
        df = df.dropna()
        return df
    except Exception as e:
        logger.error(f"Veri çekme hatası ({yf_symbol}): {e}")
        return pd.DataFrame()

# =============================================================================
# BÖLÜM 2: Walk-Forward Backtest Motoru
# =============================================================================

class ICTBacktester:
    """ICT sinyallerini tarihsel veri üzerinde walk-forward test eder"""

    def __init__(self, engine=None):
        self.engine = engine or ForexICTEngine()
        self.trades = []
        self.signals_log = []

    def _simulate_signal_on_window(self, df_window, instrument_key, signal_result):
        """
        Bir sinyal sonucu verildikten sonra, ilerleyen mumlarda
        SL mi yoksa TP mi vurulduğunu simüle et.
        """
        if not signal_result or "error" in signal_result:
            return None
        
        signal = signal_result.get("signal", "WAIT")
        if signal == "WAIT":
            return None

        sl_tp = signal_result.get("sl_tp")
        if not sl_tp:
            return None

        entry_price = signal_result["price"]
        sl = sl_tp["sl"]
        tp1 = sl_tp["tp1"]
        tp2 = sl_tp["tp2"]
        direction = sl_tp["direction"]

        # Sinyal zamanından sonraki mumları simüle et
        # df_window'un son mumundan itibaren "gelecek" veriye bakmamız gerek
        # Ama walk-forward'da biz sinyal anındaki fiyatı biliyoruz
        
        return {
            "instrument": instrument_key,
            "signal": signal,
            "direction": direction,
            "entry": entry_price,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "net_score": signal_result.get("net_score", 0),
            "bull_score": signal_result.get("bull_score", 0),
            "bear_score": signal_result.get("bear_score", 0),
            "confluence_bull": signal_result.get("confluence_bull", 0),
            "confluence_bear": signal_result.get("confluence_bear", 0),
            "reasons": signal_result.get("reasons_bull" if direction == "LONG" else "reasons_bear", []),
        }

    def walk_forward_backtest(self, instrument_key, timeframe="1h", 
                               window_size=60, step_size=5, max_hold_bars=20):
        """
        Walk-forward backtest:
        - window_size mum ile analiz yap
        - step_size mum ileri git
        - Sinyal varsa ilerleyen max_hold_bars mum içinde SL/TP kontrol et
        """
        inst = FOREX_INSTRUMENTS.get(instrument_key)
        if not inst:
            print(f"  ❌ Bilinmeyen enstrüman: {instrument_key}")
            return []

        yf_symbol = inst["yf_symbol"]
        print(f"  📊 {inst['name']} ({yf_symbol}) verisi çekiliyor...")

        # Daha fazla veri çek (walk-forward için)
        df_full = fetch_historical_data(yf_symbol, period="60d", interval="1h")
        if df_full.empty or len(df_full) < window_size + max_hold_bars + 10:
            print(f"  ⚠️ Yetersiz veri: {len(df_full)} mum")
            return []

        print(f"  ✅ {len(df_full)} mum verisi alındı ({df_full.index[0].strftime('%Y-%m-%d')} → {df_full.index[-1].strftime('%Y-%m-%d')})")

        trades = []
        total_signals = 0
        last_signal_bar = -step_size  # Cooldown

        for i in range(window_size, len(df_full) - max_hold_bars, step_size):
            # Analiz penceresi
            df_window = df_full.iloc[i - window_size:i].copy()
            df_window = df_window.reset_index(drop=True)

            # Gelecek mumlar (SL/TP simülasyonu için)
            df_future = df_full.iloc[i:i + max_hold_bars].copy()

            if len(df_window) < 30 or len(df_future) < 3:
                continue

            # ICT sinyali üret (engine'in get_candles'ını bypass et, direkt analiz yap)
            try:
                signal_result = self._analyze_window(df_window, instrument_key)
            except Exception as e:
                continue

            if not signal_result:
                continue

            signal = signal_result.get("signal", "WAIT")
            if signal == "WAIT":
                continue

            total_signals += 1
            sl_tp = signal_result.get("sl_tp")
            if not sl_tp:
                continue

            entry_price = signal_result["price"]
            sl = sl_tp["sl"]
            tp1 = sl_tp["tp1"]
            tp2 = sl_tp["tp2"]
            direction = sl_tp["direction"]

            # İleri simülasyon: SL veya TP1'e hangisi önce vurulur?
            trade_result = self._simulate_trade(df_future, entry_price, sl, tp1, tp2, direction, max_hold_bars)

            trade_record = {
                "instrument": instrument_key,
                "entry_time": df_full.index[i].strftime("%Y-%m-%d %H:%M") if hasattr(df_full.index[i], 'strftime') else str(df_full.index[i]),
                "signal": signal,
                "direction": direction,
                "entry": round(entry_price, 5),
                "sl": round(sl, 5),
                "tp1": round(tp1, 5),
                "tp2": round(tp2, 5),
                "net_score": signal_result.get("net_score", 0),
                "confluence": max(signal_result.get("confluence_bull", 0), signal_result.get("confluence_bear", 0)),
                **trade_result
            }
            trades.append(trade_record)

        self.trades.extend(trades)
        return trades

    def _analyze_window(self, df, instrument_key):
        """
        Bir veri penceresini ICT motoru ile analiz et.
        Engine'in iç metodlarını kullanarak sinyal üret.
        """
        inst = FOREX_INSTRUMENTS[instrument_key]
        cur_price = float(df["close"].iloc[-1])

        if len(df) < 30:
            return None

        # Tüm ICT analiz
        ms = self.engine.detect_market_structure(df)
        obs = self.engine.detect_order_blocks(df, cur_price)
        breakers = self.engine.detect_breaker_blocks(df)
        fvgs = self.engine.detect_fvg(df)
        displacements = self.engine.detect_displacement(df)
        sweeps = self.engine.detect_liquidity_sweeps(df)
        inducements = self.engine.detect_inducement(df, ms)
        ote = self.engine.calc_ote(df, ms)
        pd_zone = self.engine.calc_premium_discount(df)
        indicators = self.engine.calc_indicators(df)

        # Skor hesapla (generate_signal mantığını tekrarla)
        bull_score = 0
        bear_score = 0
        confluence_count = {"bull": 0, "bear": 0}
        reasons_bull = []
        reasons_bear = []

        # 1. Market Structure (30 puan)
        if ms["trend"] == "BULLISH":
            bull_score += 30
            confluence_count["bull"] += 1
            reasons_bull.append("Piyasa yapısı yükseliş trendinde")
        elif ms["trend"] == "BEARISH":
            bear_score += 30
            confluence_count["bear"] += 1
            reasons_bear.append("Piyasa yapısı düşüş trendinde")

        for bos in ms["bos"]:
            if bos["type"] == "BULLISH_BOS":
                bull_score += 10
                confluence_count["bull"] += 1
            elif bos["type"] == "BEARISH_BOS":
                bear_score += 10
                confluence_count["bear"] += 1

        if ms["choch"]:
            if ms["choch"]["type"] == "BULLISH_CHOCH":
                bull_score += 15
                confluence_count["bull"] += 1
            elif ms["choch"]["type"] == "BEARISH_CHOCH":
                bear_score += 15
                confluence_count["bear"] += 1

        # 2. Order Blocks (20 puan)
        active_obs = [ob for ob in obs if not ob["mitigated"]]
        for ob in active_obs:
            if ob["type"] == "BULLISH_OB" and ob["low"] <= cur_price <= ob["high"]:
                bull_score += 20
                confluence_count["bull"] += 1
                break
        for ob in active_obs:
            if ob["type"] == "BEARISH_OB" and ob["low"] <= cur_price <= ob["high"]:
                bear_score += 20
                confluence_count["bear"] += 1
                break

        # 3. Breaker Blocks (10 puan)
        if any(bb["type"] == "BULLISH_BREAKER" for bb in breakers):
            bull_score += 10
            confluence_count["bull"] += 1
        if any(bb["type"] == "BEARISH_BREAKER" for bb in breakers):
            bear_score += 10
            confluence_count["bear"] += 1

        # 4. FVG (15 puan)
        active_fvgs = [f for f in fvgs if not f["filled"] and f["idx"] >= len(df) - 15]
        bull_fvg = [f for f in active_fvgs if f["type"] == "BULLISH_FVG"]
        bear_fvg = [f for f in active_fvgs if f["type"] == "BEARISH_FVG"]
        ce_bull = [f for f in bull_fvg if f["ce_tested"]]
        ce_bear = [f for f in bear_fvg if f["ce_tested"]]

        if bull_fvg:
            bull_score += 15 if ce_bull else 10
            confluence_count["bull"] += 1
        if bear_fvg:
            bear_score += 15 if ce_bear else 10
            confluence_count["bear"] += 1

        # 5. Displacement (10 puan)
        recent_disp = [d for d in displacements if d["idx"] >= len(df) - 5]
        for d in recent_disp:
            if "BULLISH" in d["type"]:
                bull_score += 10
                confluence_count["bull"] += 1
            else:
                bear_score += 10
                confluence_count["bear"] += 1

        # 6. Liquidity Sweeps (15 puan)
        recent_sweeps = [s for s in sweeps if s["idx"] >= len(df) - 5]
        for sw in recent_sweeps:
            if sw["type"] == "BUY_SIDE_SWEEP":
                bull_score += 15
                confluence_count["bull"] += 1
            elif sw["type"] == "SELL_SIDE_SWEEP":
                bear_score += 15
                confluence_count["bear"] += 1

        # 7. OTE (10 puan)
        if ote:
            if ote["direction"] == "LONG" and ote["ote_bottom"] <= cur_price <= ote["ote_top"]:
                bull_score += 10
                confluence_count["bull"] += 1
            elif ote["direction"] == "SHORT" and ote["ote_bottom"] <= cur_price <= ote["ote_top"]:
                bear_score += 10
                confluence_count["bear"] += 1

        # 8. Premium/Discount (10 puan)
        if pd_zone["zone"] == "DISCOUNT" and pd_zone["zone_pct"] > 60:
            bull_score += 10
            confluence_count["bull"] += 1
        elif pd_zone["zone"] == "PREMIUM" and pd_zone["zone_pct"] > 60:
            bear_score += 10
            confluence_count["bear"] += 1

        # 9. RSI (5 puan)
        if indicators["rsi"] < 35:
            bull_score += 5
        elif indicators["rsi"] > 65:
            bear_score += 5

        # ── FILTRELER & CEZALAR (v2) ──

        # F1. Premium/Discount celiskisi
        if pd_zone["zone"] == "PREMIUM" and pd_zone["zone_pct"] > 75:
            bull_score -= 15
        elif pd_zone["zone"] == "DISCOUNT" and pd_zone["zone_pct"] > 75:
            bear_score -= 15

        # F2. RSI asiri bolge celiskisi
        if indicators["rsi"] > 75 and bull_score > bear_score:
            bull_score -= 10
        elif indicators["rsi"] < 25 and bear_score > bull_score:
            bear_score -= 10

        # Sinyal kararı (v2 esikleri)
        net_score = bull_score - bear_score

        if net_score >= 55 and confluence_count["bull"] >= 5:
            signal = "STRONG_LONG"
        elif net_score >= 30 and confluence_count["bull"] >= 3:
            signal = "LONG"
        elif net_score <= -55 and confluence_count["bear"] >= 5:
            signal = "STRONG_SHORT"
        elif net_score <= -30 and confluence_count["bear"] >= 3:
            signal = "SHORT"
        else:
            return None

        direction = "LONG" if signal in ("STRONG_LONG", "LONG") else "SHORT"

        # SL/TP hesapla
        atr = indicators["atr"]
        if atr <= 0:
            return None

        swing_lookback = min(20, len(df) - 1)
        recent_swing_high = float(df["high"].iloc[-swing_lookback:].max())
        recent_swing_low = float(df["low"].iloc[-swing_lookback:].min())

        if direction == "LONG":
            atr_sl = cur_price - atr * 1.2
            swing_sl = recent_swing_low - atr * 0.15
            sl = max(atr_sl, swing_sl)
            if abs(cur_price - sl) < atr * 0.4:
                sl = atr_sl
            risk = abs(cur_price - sl)
            if risk <= 0:
                return None
            tp1 = cur_price + risk * 1.8
            tp2 = cur_price + risk * 3.0
        else:
            atr_sl = cur_price + atr * 1.2
            swing_sl = recent_swing_high + atr * 0.15
            sl = min(atr_sl, swing_sl)
            if abs(sl - cur_price) < atr * 0.4:
                sl = atr_sl
            risk = abs(sl - cur_price)
            if risk <= 0:
                return None
            tp1 = cur_price - risk * 1.8
            tp2 = cur_price - risk * 3.0

        rr1 = abs(tp1 - cur_price) / risk if risk > 0 else 0
        rr2 = abs(tp2 - cur_price) / risk if risk > 0 else 0

        return {
            "price": cur_price,
            "signal": signal,
            "net_score": net_score,
            "bull_score": bull_score,
            "bear_score": bear_score,
            "confluence_bull": confluence_count["bull"],
            "confluence_bear": confluence_count["bear"],
            "reasons_bull": reasons_bull,
            "reasons_bear": reasons_bear,
            "sl_tp": {
                "sl": round(sl, 5),
                "tp1": round(tp1, 5),
                "tp2": round(tp2, 5),
                "direction": direction,
                "rr1": round(rr1, 2),
                "rr2": round(rr2, 2),
            }
        }

    def _simulate_trade(self, df_future, entry, sl, tp1, tp2, direction, max_bars):
        """
        Gelecek mumları tarayarak SL veya TP'ye hangisinin önce vurulduğunu belirle.
        Partial TP1 + TP2 sistemi: TP1'e vurulursa %50 kar, SL BE'ye taşınır.
        """
        result = {
            "outcome": "TIMEOUT",
            "exit_price": entry,
            "pnl_pct": 0.0,
            "bars_held": 0,
            "hit_tp1": False,
            "hit_tp2": False,
            "max_favorable": 0.0,
            "max_adverse": 0.0,
        }

        if df_future.empty:
            return result

        tp1_hit = False
        be_sl = entry  # Breakeven SL (TP1 sonrası)

        for idx in range(min(len(df_future), max_bars)):
            bar = df_future.iloc[idx]
            high = bar["high"]
            low = bar["low"]

            if direction == "LONG":
                # Max favorable/adverse excursion
                mfe = (high - entry) / entry * 100
                mae = (entry - low) / entry * 100
                result["max_favorable"] = max(result["max_favorable"], mfe)
                result["max_adverse"] = max(result["max_adverse"], mae)

                if not tp1_hit:
                    # SL kontrolü (orijinal SL)
                    if low <= sl:
                        result["outcome"] = "SL_HIT"
                        result["exit_price"] = sl
                        result["pnl_pct"] = round((sl - entry) / entry * 100, 4)
                        result["bars_held"] = idx + 1
                        return result
                    # TP1 kontrolü
                    if high >= tp1:
                        tp1_hit = True
                        result["hit_tp1"] = True
                        be_sl = entry  # SL'yi breakeven'a taşı
                else:
                    # TP1 vuruldu, şimdi TP2 veya BE-SL kontrolü
                    if low <= be_sl:
                        result["outcome"] = "TP1_THEN_BE"
                        result["exit_price"] = tp1
                        result["pnl_pct"] = round((tp1 - entry) / entry * 100 * 0.5, 4)
                        result["bars_held"] = idx + 1
                        return result
                    if high >= tp2:
                        result["outcome"] = "TP2_HIT"
                        result["hit_tp2"] = True
                        # %50 TP1 + %50 TP2
                        pnl = ((tp1 - entry) * 0.5 + (tp2 - entry) * 0.5) / entry * 100
                        result["exit_price"] = tp2
                        result["pnl_pct"] = round(pnl, 4)
                        result["bars_held"] = idx + 1
                        return result

            else:  # SHORT
                mfe = (entry - low) / entry * 100
                mae = (high - entry) / entry * 100
                result["max_favorable"] = max(result["max_favorable"], mfe)
                result["max_adverse"] = max(result["max_adverse"], mae)

                if not tp1_hit:
                    if high >= sl:
                        result["outcome"] = "SL_HIT"
                        result["exit_price"] = sl
                        result["pnl_pct"] = round((entry - sl) / entry * 100, 4)
                        result["bars_held"] = idx + 1
                        return result
                    if low <= tp1:
                        tp1_hit = True
                        result["hit_tp1"] = True
                        be_sl = entry
                else:
                    if high >= be_sl:
                        result["outcome"] = "TP1_THEN_BE"
                        result["exit_price"] = tp1
                        result["pnl_pct"] = round((entry - tp1) / entry * 100 * 0.5, 4)
                        result["bars_held"] = idx + 1
                        return result
                    if low <= tp2:
                        result["outcome"] = "TP2_HIT"
                        result["hit_tp2"] = True
                        pnl = ((entry - tp1) * 0.5 + (entry - tp2) * 0.5) / entry * 100
                        result["exit_price"] = tp2
                        result["pnl_pct"] = round(pnl, 4)
                        result["bars_held"] = idx + 1
                        return result

        # Timeout - son fiyattan çık
        last_price = df_future["close"].iloc[-1] if len(df_future) > 0 else entry
        if direction == "LONG":
            result["pnl_pct"] = round((last_price - entry) / entry * 100, 4)
        else:
            result["pnl_pct"] = round((entry - last_price) / entry * 100, 4)
        result["exit_price"] = last_price
        result["bars_held"] = len(df_future)

        if tp1_hit:
            result["outcome"] = "TP1_TIMEOUT"
            # TP1 karı + kalan pozisyon
            if direction == "LONG":
                result["pnl_pct"] = round(((tp1 - entry) * 0.5 + (last_price - entry) * 0.5) / entry * 100, 4)
            else:
                result["pnl_pct"] = round(((entry - tp1) * 0.5 + (entry - last_price) * 0.5) / entry * 100, 4)

        return result

# =============================================================================
# BÖLÜM 3: Performans Raporu
# =============================================================================

def generate_report(trades, title="ICT Backtest Raporu"):
    """Detaylı performans raporu üret"""
    if not trades:
        print(f"\n{'='*60}")
        print(f"  {title}")
        print(f"{'='*60}")
        print("  ⚠️ Hiç trade bulunamadı!")
        return {}

    df = pd.DataFrame(trades)
    
    total = len(df)
    wins = len(df[df["pnl_pct"] > 0])
    losses = len(df[df["pnl_pct"] < 0])
    breakeven = len(df[df["pnl_pct"] == 0])
    
    win_rate = wins / total * 100 if total > 0 else 0
    
    avg_win = df[df["pnl_pct"] > 0]["pnl_pct"].mean() if wins > 0 else 0
    avg_loss = df[df["pnl_pct"] < 0]["pnl_pct"].mean() if losses > 0 else 0
    
    total_pnl = df["pnl_pct"].sum()
    avg_pnl = df["pnl_pct"].mean()
    
    # Profit Factor
    gross_profit = df[df["pnl_pct"] > 0]["pnl_pct"].sum() if wins > 0 else 0
    gross_loss = abs(df[df["pnl_pct"] < 0]["pnl_pct"].sum()) if losses > 0 else 0.001
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    
    # Max Drawdown
    cumulative = df["pnl_pct"].cumsum()
    peak = cumulative.cummax()
    drawdown = cumulative - peak
    max_dd = drawdown.min()
    
    # Outcome dağılımı
    outcomes = df["outcome"].value_counts().to_dict()
    
    # TP1 ve TP2 hit oranları
    tp1_rate = df["hit_tp1"].sum() / total * 100 if total > 0 else 0
    tp2_rate = df["hit_tp2"].sum() / total * 100 if total > 0 else 0
    
    # Sinyal türü dağılımı
    signal_dist = df["signal"].value_counts().to_dict()
    
    # Direction dağılımı
    dir_dist = df["direction"].value_counts().to_dict()
    
    # Ortalama bar tutma süresi
    avg_bars = df["bars_held"].mean()
    
    # Strong vs Normal sinyal karşılaştırma
    strong_signals = df[df["signal"].str.contains("STRONG")]
    normal_signals = df[~df["signal"].str.contains("STRONG")]

    print(f"\n{'='*70}")
    print(f"  📊 {title}")
    print(f"{'='*70}")
    print(f"  Tarih Aralığı  : {df['entry_time'].iloc[0]} → {df['entry_time'].iloc[-1]}")
    print(f"{'─'*70}")
    print(f"  📈 GENEL PERFORMANS")
    print(f"{'─'*70}")
    print(f"  Toplam İşlem   : {total}")
    print(f"  Kazanan        : {wins} ({win_rate:.1f}%)")
    print(f"  Kaybeden       : {losses} ({100-win_rate:.1f}%)")
    print(f"  Başabaş        : {breakeven}")
    print(f"  Toplam PnL     : {total_pnl:+.2f}%")
    print(f"  Ortalama PnL   : {avg_pnl:+.3f}%")
    print(f"  Ort. Kazanç    : {avg_win:+.3f}%")
    print(f"  Ort. Kayıp     : {avg_loss:+.3f}%")
    print(f"  Profit Factor  : {profit_factor:.2f}")
    print(f"  Max Drawdown   : {max_dd:.2f}%")
    print(f"  Ort. Bar Tutma : {avg_bars:.1f} mum")
    
    print(f"\n{'─'*70}")
    print(f"  🎯 TP PERFORMANSI")
    print(f"{'─'*70}")
    print(f"  TP1 Vurma Oranı: {tp1_rate:.1f}%")
    print(f"  TP2 Vurma Oranı: {tp2_rate:.1f}%")
    
    print(f"\n  📋 Sonuç Dağılımı:")
    for outcome, count in sorted(outcomes.items()):
        pct = count / total * 100
        icon = "✅" if "TP" in outcome else ("❌" if "SL" in outcome else "⏱️")
        print(f"     {icon} {outcome:20s}: {count:3d} ({pct:.1f}%)")
    
    print(f"\n{'─'*70}")
    print(f"  📊 SİNYAL DAĞILIMI")
    print(f"{'─'*70}")
    for sig, count in signal_dist.items():
        sig_trades = df[df["signal"] == sig]
        sig_wr = len(sig_trades[sig_trades["pnl_pct"] > 0]) / len(sig_trades) * 100
        sig_pnl = sig_trades["pnl_pct"].sum()
        print(f"     {sig:15s}: {count:3d} işlem | Win: {sig_wr:.0f}% | PnL: {sig_pnl:+.2f}%")
    
    print(f"\n  📊 Yön Dağılımı:")
    for dir_name, count in dir_dist.items():
        dir_trades = df[df["direction"] == dir_name]
        dir_wr = len(dir_trades[dir_trades["pnl_pct"] > 0]) / len(dir_trades) * 100
        dir_pnl = dir_trades["pnl_pct"].sum()
        print(f"     {dir_name:15s}: {count:3d} işlem | Win: {dir_wr:.0f}% | PnL: {dir_pnl:+.2f}%")

    if len(strong_signals) >= 2 and len(normal_signals) >= 2:
        print(f"\n{'─'*70}")
        print(f"  ⭐ STRONG vs NORMAL SİNYAL KARŞILAŞTIRMA")
        print(f"{'─'*70}")
        s_wr = len(strong_signals[strong_signals["pnl_pct"] > 0]) / len(strong_signals) * 100
        n_wr = len(normal_signals[normal_signals["pnl_pct"] > 0]) / len(normal_signals) * 100
        print(f"     STRONG Sinyaller: {len(strong_signals)} işlem | Win: {s_wr:.0f}% | PnL: {strong_signals['pnl_pct'].sum():+.2f}%")
        print(f"     Normal Sinyaller: {len(normal_signals)} işlem | Win: {n_wr:.0f}% | PnL: {normal_signals['pnl_pct'].sum():+.2f}%")

    # Enstrüman bazlı performans
    if "instrument" in df.columns:
        instruments = df["instrument"].unique()
        if len(instruments) > 1:
            print(f"\n{'─'*70}")
            print(f"  🌍 ENSTRÜMAN BAZLI PERFORMANS")
            print(f"{'─'*70}")
            for inst in instruments:
                inst_trades = df[df["instrument"] == inst]
                inst_wr = len(inst_trades[inst_trades["pnl_pct"] > 0]) / len(inst_trades) * 100 if len(inst_trades) > 0 else 0
                inst_pnl = inst_trades["pnl_pct"].sum()
                inst_name = FOREX_INSTRUMENTS.get(inst, {}).get("name", inst)
                print(f"     {inst_name:15s}: {len(inst_trades):3d} işlem | Win: {inst_wr:.0f}% | PnL: {inst_pnl:+.2f}%")

    # Son 5 işlem detayı
    print(f"\n{'─'*70}")
    print(f"  📝 SON 5 İŞLEM")
    print(f"{'─'*70}")
    for _, trade in df.tail(5).iterrows():
        icon = "✅" if trade["pnl_pct"] > 0 else ("❌" if trade["pnl_pct"] < 0 else "➖")
        print(f"     {icon} {trade['entry_time']} | {trade['instrument']:8s} | {trade['direction']:5s} | "
              f"Score: {trade['net_score']:+3.0f} | {trade['outcome']:12s} | PnL: {trade['pnl_pct']:+.3f}%")

    # Değerlendirme
    print(f"\n{'='*70}")
    print(f"  🏆 DEĞERLENDİRME")
    print(f"{'='*70}")
    
    rating_score = 0
    
    if win_rate >= 55:
        print(f"  ✅ Kazanma oranı iyi ({win_rate:.1f}% >= 55%)")
        rating_score += 2
    elif win_rate >= 45:
        print(f"  ⚠️ Kazanma oranı orta ({win_rate:.1f}%)")
        rating_score += 1
    else:
        print(f"  ❌ Kazanma oranı düşük ({win_rate:.1f}% < 45%)")
    
    if profit_factor >= 1.5:
        print(f"  ✅ Profit Factor güçlü ({profit_factor:.2f} >= 1.5)")
        rating_score += 2
    elif profit_factor >= 1.0:
        print(f"  ⚠️ Profit Factor pozitif ({profit_factor:.2f})")
        rating_score += 1
    else:
        print(f"  ❌ Profit Factor negatif ({profit_factor:.2f} < 1.0)")
    
    if max_dd > -5:
        print(f"  ✅ Drawdown kontrollü ({max_dd:.2f}% > -5%)")
        rating_score += 2
    elif max_dd > -10:
        print(f"  ⚠️ Drawdown orta ({max_dd:.2f}%)")
        rating_score += 1
    else:
        print(f"  ❌ Drawdown yüksek ({max_dd:.2f}%)")
    
    if avg_win > abs(avg_loss):
        print(f"  ✅ Kazanç/Kayıp oranı pozitif (Ort Kazanç: {avg_win:.3f}% > Ort Kayıp: {abs(avg_loss):.3f}%)")
        rating_score += 2
    else:
        print(f"  ⚠️ Ortalama kayıp ortalama kazançtan büyük")
    
    if tp1_rate >= 40:
        print(f"  ✅ TP1 vurma oranı iyi ({tp1_rate:.1f}%)")
        rating_score += 1
    else:
        print(f"  ⚠️ TP1 vurma oranı düşük ({tp1_rate:.1f}%)")
    
    ratings = ["❌ BAŞARISIZ", "⚠️ ZAYIF", "⚠️ GELİŞTİRİLMELİ",
               "📊 ORTA", "📊 KABUL EDİLEBİLİR", "✅ İYİ",
               "✅ ÇOK İYİ", "🏆 MÜKEMMEL", "🏆 ELİT", "🏆 ELİT+"]
    
    rating = ratings[min(rating_score, len(ratings)-1)]
    print(f"\n  >>> GENEL DERECE: {rating} (Skor: {rating_score}/9)")
    print(f"{'='*70}\n")

    return {
        "total_trades": total,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "total_pnl": total_pnl,
        "max_drawdown": max_dd,
        "avg_pnl": avg_pnl,
        "tp1_rate": tp1_rate,
        "tp2_rate": tp2_rate,
        "rating_score": rating_score,
    }


# =============================================================================
# BÖLÜM 4: Güncel/Canlı Sinyal Testi
# =============================================================================

def live_signal_test(engine=None):
    """Tüm enstrümanlarda güncel ICT sinyali üret ve raporla"""
    if engine is None:
        engine = ForexICTEngine()

    print(f"\n{'='*70}")
    print(f"  🔴 CANLI SİNYAL TESTİ - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"{'='*70}")

    # Kill Zone bilgisi
    kill = engine.detect_kill_zones()
    if kill["is_kill_zone"]:
        print(f"  🕐 AKTİF KILL ZONE: {kill['active_zone']} ({kill['minutes_remaining']} dk kaldı)")
    else:
        next_kz = kill.get("next_zone", {})
        if next_kz:
            print(f"  🕐 Kill Zone KAPALI | Sonraki: {next_kz.get('name', '?')}")
        else:
            print(f"  🕐 Kill Zone KAPALI")

    silver = engine.detect_silver_bullet()
    if silver["is_active"]:
        print(f"  ⚡ SILVER BULLET AKTİF: {silver['active']['name']}")

    signals = []
    print(f"\n{'─'*70}")
    print(f"  {'Enstrüman':12s} | {'Sinyal':14s} | {'Skor':>6s} | {'Confluence':>4s} | {'SL/TP1/TP2':>30s}")
    print(f"{'─'*70}")

    for key in FOREX_INSTRUMENTS:
        try:
            result = engine.generate_signal(key, timeframe="1h")
            if "error" in result:
                print(f"  {key:12s} | ⚠️  Veri hatası")
                continue

            signal = result["signal"]
            net_score = result["net_score"]
            conf = max(result["confluence_bull"], result["confluence_bear"])
            
            if signal == "WAIT":
                icon = "⏸️"
                color_label = "BEKLE"
            elif "LONG" in signal:
                icon = "🟢"
                color_label = result["label"]
            else:
                icon = "🔴"
                color_label = result["label"]

            sl_tp_str = ""
            if result.get("sl_tp"):
                st = result["sl_tp"]
                sl_tp_str = f"SL:{st['sl']:.4f} TP1:{st['tp1']:.4f} TP2:{st['tp2']:.4f}"
            
            print(f"  {key:12s} | {icon} {color_label:11s} | {net_score:+5.0f} | {conf:4d} | {sl_tp_str}")
            
            signals.append(result)
            time.sleep(0.5)  # Rate limit

        except Exception as e:
            print(f"  {key:12s} | ❌ Hata: {str(e)[:40]}")

    # Aktif sinyaller özeti
    active = [s for s in signals if s["signal"] != "WAIT"]
    print(f"\n{'─'*70}")
    print(f"  📊 ÖZET: {len(signals)} enstrüman tarandı, {len(active)} aktif sinyal")

    for s in active:
        print(f"\n  {'='*60}")
        print(f"  🎯 {s['name']} ({s['instrument']}) - {s['label']}")
        print(f"  {'─'*60}")
        print(f"  Fiyat: {s['price']}")
        print(f"  Skor: Bull {s['bull_score']} vs Bear {s['bear_score']} = Net {s['net_score']:+.0f}")
        print(f"  Confluence: Bull {s['confluence_bull']} / Bear {s['confluence_bear']}")
        
        if s.get("sl_tp"):
            st = s["sl_tp"]
            print(f"  Giriş: {s['price']:.5f}")
            print(f"  SL   : {st['sl']:.5f} (R:R = —)")
            print(f"  TP1  : {st['tp1']:.5f} (R:R = {st['rr1']:.1f})")
            print(f"  TP2  : {st['tp2']:.5f} (R:R = {st['rr2']:.1f})")
        
        reasons_key = "reasons_bull" if "LONG" in s["signal"] else "reasons_bear"
        reasons = s.get(reasons_key, [])
        if reasons:
            print(f"  📋 ICT Nedenleri:")
            for r in reasons[:5]:
                print(f"     • {r}")

        # Karşı taraf
        counter_key = "reasons_bear" if "LONG" in s["signal"] else "reasons_bull"
        counter = s.get(counter_key, [])
        if counter:
            print(f"  ⚠️ Karşı Sinyaller:")
            for r in counter[:3]:
                print(f"     • {r}")

    if not active:
        print(f"\n  ⏸️ Şu anda net bir ICT sinyali yok. Daha iyi setup için bekleyin.")

    print(f"\n{'='*70}")
    return signals


# =============================================================================
# BÖLÜM 5: Ana Program
# =============================================================================

def main():
    print("""
╔══════════════════════════════════════════════════════════════════╗
║            ICT SİNYAL BACKTEST & DOĞRULAMA SİSTEMİ             ║
║                                                                  ║
║   Inner Circle Trader (ICT) metodolojisi ile                    ║
║   Forex/Emtia sinyallerinin geriye dönük testi                  ║
╚══════════════════════════════════════════════════════════════════╝
    """)

    engine = ForexICTEngine()
    backtester = ICTBacktester(engine)

    # ======================
    # AŞAMA 1: BACKTEST
    # ======================
    print("═" * 70)
    print("  AŞAMA 1: TARİHSEL BACKTEST (Walk-Forward)")
    print("═" * 70)
    print("  60 günlük 1H veri üzerinde walk-forward simülasyon")
    print("  Pencere: 60 mum analiz → 5 mum adım → 20 mum max tutma\n")

    all_trades = []
    instruments_to_test = list(FOREX_INSTRUMENTS.keys())

    for inst_key in instruments_to_test:
        try:
            trades = backtester.walk_forward_backtest(
                inst_key, 
                timeframe="1h",
                window_size=60,
                step_size=5,
                max_hold_bars=20
            )
            all_trades.extend(trades)
            if trades:
                print(f"     → {len(trades)} işlem simüle edildi")
            else:
                print(f"     → Sinyal üretilemedi")
            time.sleep(1)  # Rate limit
        except Exception as e:
            print(f"  ❌ {inst_key} hatası: {e}")

    # Genel backtest raporu
    report = generate_report(all_trades, "📊 TÜM ENSTRÜMANLAR - TARİHSEL BACKTEST")

    # ======================
    # AŞAMA 2: CANLI TEST
    # ======================
    print("\n" + "═" * 70)
    print("  AŞAMA 2: GÜNCEL VERİ İLE CANLI TEST")
    print("═" * 70)

    live_signals = live_signal_test(engine)

    # ======================
    # AŞAMA 3: SONUÇ
    # ======================
    print("\n" + "═" * 70)
    print("  📊 GENEL DEĞERLENDİRME")
    print("═" * 70)

    if report:
        wr = report.get("win_rate", 0)
        pf = report.get("profit_factor", 0)
        pnl = report.get("total_pnl", 0)
        
        print(f"\n  ICT Sinyal Motoru Değerlendirmesi:")
        print(f"  ─────────────────────────────────")
        
        if wr >= 50 and pf >= 1.2:
            print(f"  ✅ ICT sinyal motoru DOĞRU sinyal üretiyor.")
            print(f"     Kazanma oranı: {wr:.1f}% | Profit Factor: {pf:.2f} | Toplam PnL: {pnl:+.2f}%")
            print(f"     Walk-forward backtest sonuçları pozitif beklenen değer gösteriyor.")
        elif wr >= 45 and pf >= 1.0:
            print(f"  ⚠️ ICT sinyal motoru KISMİ BAŞARILI.")
            print(f"     Kazanma oranı: {wr:.1f}% | Profit Factor: {pf:.2f} | Toplam PnL: {pnl:+.2f}%")
            print(f"     Sinyaller pozitif eğilimli ama iyileştirme gerekli.")
        else:
            print(f"  ❌ ICT sinyal motoru bu dönemde BAŞARISIZ.")
            print(f"     Kazanma oranı: {wr:.1f}% | Profit Factor: {pf:.2f} | Toplam PnL: {pnl:+.2f}%")
            print(f"     Parametre optimizasyonu veya filtre iyileştirmesi gerekli.")

    active_signals = [s for s in (live_signals or []) if s.get("signal", "WAIT") != "WAIT"]
    if active_signals:
        print(f"\n  🔴 Şu anda {len(active_signals)} aktif sinyal var:")
        for s in active_signals:
            print(f"     {s['name']:12s}: {s['label']} (Skor: {s['net_score']:+.0f})")
    else:
        print(f"\n  ⏸️ Şu anda aktif sinyal yok - Kill Zone veya confluence bekleniyor.")

    print(f"\n{'═'*70}")
    print(f"  Test tamamlandı: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═'*70}\n")

    # Trade detaylarını JSON'a kaydet
    if all_trades:
        output_path = os.path.join(os.path.dirname(__file__), "backtest_results.json")
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump({
                "test_date": datetime.now().isoformat(),
                "total_trades": len(all_trades),
                "report": report,
                "trades": all_trades,
                "live_signals": [
                    {
                        "instrument": s.get("instrument"),
                        "signal": s.get("signal"),
                        "net_score": s.get("net_score"),
                        "price": s.get("price"),
                    } for s in (live_signals or [])
                ]
            }, f, indent=2, ensure_ascii=False, default=str)
        print(f"  📁 Detaylı sonuçlar: backtest_results.json")


if __name__ == "__main__":
    main()
