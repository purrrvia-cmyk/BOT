import sqlite3

conn = sqlite3.connect('ict_bot.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("="*70)
print("CANCEL EDİLEN İŞLEMLERİN NEDENLERİ")
print("="*70)

c.execute("""SELECT id, symbol, direction, entry_price, close_price, status, notes, entry_time, close_time
             FROM signals 
             WHERE status='CANCELLED' 
             ORDER BY id DESC LIMIT 20""")
rows = c.fetchall()

for r in rows:
    print(f"#{r['id']} {r['symbol']} {r['direction']} | Entry: {r['entry_price']} | Close: {r['close_price']}")
    print(f"  Notes: {r['notes']}")
    print()

print("\n" + "="*70)
print("WATCHLIST DURUMU")
print("="*70)

c.execute("""SELECT id, symbol, direction, watch_reason, status, candles_watched, max_watch_candles, expire_reason
             FROM watchlist 
             ORDER BY id DESC LIMIT 30""")
wl = c.fetchall()

for w in wl:
    print(f"#{w['id']} {w['symbol']} {w['direction']} | Status: {w['status']} | Candles: {w['candles_watched']}/{w['max_watch_candles']}")
    print(f"  Watch Reason: {w['watch_reason']}")
    if w['expire_reason']:
        print(f"  Expire Reason: {w['expire_reason']}")
    print()

conn.close()
