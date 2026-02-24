import sqlite3
from datetime import datetime

conn = sqlite3.connect('ict_bot.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

# Aktif işlemler
print('=' * 60)
print('AKTIF ISLEMLER')
print('=' * 60)
c.execute("""SELECT id, symbol, direction, entry_price, stop_loss, take_profit, status, entry_time 
             FROM signals WHERE status IN ('ACTIVE', 'WAITING') ORDER BY id DESC""")
active = c.fetchall()
if active:
    for s in active:
        print(f"#{s['id']} {s['symbol']} {s['direction']} | Entry: {s['entry_price']} | SL: {s['stop_loss']} | TP: {s['take_profit']} | Status: {s['status']}")
else:
    print('Yok')

# Son 20 kapanan işlem
print('\n' + '=' * 60)
print('SON 20 KAPANAN ISLEM')
print('=' * 60)
c.execute("""SELECT id, symbol, direction, entry_price, stop_loss, take_profit, status, 
             entry_time, close_time, close_price, pnl_pct
             FROM signals 
             WHERE status IN ('WON', 'LOST', 'CANCELLED')
             ORDER BY id DESC LIMIT 20""")
closed = c.fetchall()
for s in closed:
    pnl = s['pnl_pct'] or 0
    emoji = 'WIN' if s['status'] == 'WON' else 'LOSS' if s['status'] == 'LOST' else 'CANCEL'
    print(f"[{emoji}] #{s['id']} {s['symbol']} {s['direction']} | PnL: {pnl:.2f}% | Close: {s['close_price']}")

# Genel performans
print('\n' + '=' * 60)
print('GENEL PERFORMANS (SON 30 ISLEM)')
print('=' * 60)
c.execute("""SELECT 
    COUNT(*) as total,
    SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) as wins,
    SUM(CASE WHEN status='LOST' THEN 1 ELSE 0 END) as losses,
    SUM(CASE WHEN status='CANCELLED' THEN 1 ELSE 0 END) as cancelled,
    AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END) as avg_pnl
    FROM signals 
    WHERE status IN ('WON', 'LOST', 'CANCELLED')
    ORDER BY id DESC LIMIT 30""")
perf = c.fetchone()
if perf and perf['total'] > 0:
    wr = (perf['wins'] / perf['total'] * 100) if perf['total'] > 0 else 0
    print(f"Total: {perf['total']} | Win: {perf['wins']} | Loss: {perf['losses']} | Cancelled: {perf['cancelled']}")
    print(f"Win Rate: {wr:.1f}% | Avg PnL: {perf['avg_pnl']:.2f}%")
else:
    print("Henuz kapanan islem yok")

# Kayıp eden işlemlerin detayı
print('\n' + '=' * 60)
print('KAYIP EDEN ISLEMLERIN DETAYI (SON 10)')
print('=' * 60)
c.execute("""SELECT id, symbol, direction, entry_price, stop_loss, take_profit, close_price, pnl_pct, entry_time, close_time
             FROM signals 
             WHERE status='LOST'
             ORDER BY id DESC LIMIT 10""")
losses = c.fetchall()
if losses:
    for s in losses:
        print(f"#{s['id']} {s['symbol']} {s['direction']} | Entry: {s['entry_price']} | SL: {s['stop_loss']} | Close: {s['close_price']} | PnL: {s['pnl_pct']:.2f}%")
else:
    print("Henuz kayip islem yok")

# Kazanan işlemlerin detayı
print('\n' + '=' * 60)
print('KAZANAN ISLEMLERIN DETAYI (SON 10)')
print('=' * 60)
c.execute("""SELECT id, symbol, direction, entry_price, stop_loss, take_profit, close_price, pnl_pct, entry_time, close_time
             FROM signals 
             WHERE status='WON'
             ORDER BY id DESC LIMIT 10""")
wins = c.fetchall()
if wins:
    for s in wins:
        print(f"#{s['id']} {s['symbol']} {s['direction']} | Entry: {s['entry_price']} | TP: {s['take_profit']} | Close: {s['close_price']} | PnL: {s['pnl_pct']:.2f}%")
else:
    print("Henuz kazanan islem yok")

# Stratejilere göre breakdown
print('\n' + '=' * 60)
print('STRATEJILERE GORE BREAKDOWN')
print('=' * 60)
c.execute("""SELECT 
    strategy,
    COUNT(*) as total,
    SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) as wins,
    SUM(CASE WHEN status='LOST' THEN 1 ELSE 0 END) as losses,
    AVG(CASE WHEN status IN ('WON', 'LOST') THEN pnl_pct ELSE NULL END) as avg_pnl
    FROM signals 
    WHERE status IN ('WON', 'LOST', 'CANCELLED')
    GROUP BY strategy""")
strats = c.fetchall()
for st in strats:
    wr = (st['wins'] / st['total'] * 100) if st['total'] > 0 else 0
    avg = st['avg_pnl'] or 0
    print(f"{st['strategy']}: Total={st['total']}, Win={st['wins']}, Loss={st['losses']}, WR={wr:.1f}%, Avg PnL={avg:.2f}%")

conn.close()
