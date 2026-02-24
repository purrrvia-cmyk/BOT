import sqlite3
from datetime import datetime, timedelta

conn = sqlite3.connect('ict_bot.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

print('='*70)
print('WATCHLIST EXPIRE ANALİZİ (SON 1 SAAT)')
print('='*70)

# Son 1 saat içinde expire olan itemlar
one_hour_ago = (datetime.now() - timedelta(hours=1)).isoformat()
c.execute('''SELECT symbol, direction, watch_reason, expire_reason, candles_watched, 
             max_watch_candles, created_at, updated_at
             FROM watchlist 
             WHERE status='EXPIRED' AND updated_at > ?
             ORDER BY updated_at DESC LIMIT 50''', (one_hour_ago,))
expired = c.fetchall()

if expired:
    print(f'Toplam Expire: {len(expired)}')
    print()
    
    # Expire sebeplerini grupla
    expire_reasons = {}
    for item in expired:
        reason = item['expire_reason'] or 'Bilinmiyor'
        expire_reasons[reason] = expire_reasons.get(reason, 0) + 1
        candles = f"{item['candles_watched']}/{item['max_watch_candles']}"
        print(f"{item['symbol']} {item['direction']} | Candles: {candles}")
        print(f"  Watch: {item['watch_reason']}")
        print(f"  Expire: {item['expire_reason']}")
        print()
    
    print('='*70)
    print('EXPIRE SEBEPLERİ (GRUPLU)')
    print('='*70)
    for reason, count in sorted(expire_reasons.items(), key=lambda x: x[1], reverse=True):
        print(f"{count:3d}x - {reason}")
else:
    print('Son 1 saatte expire olan watchlist item yok')

print()
print('='*70)
print('AKTİF WATCHLIST (ŞU ANDA)')
print('='*70)
c.execute('''SELECT symbol, direction, watch_reason, candles_watched, max_watch_candles, created_at
             FROM watchlist 
             WHERE status='WATCHING'
             ORDER BY created_at DESC LIMIT 20''')
watching = c.fetchall()

if watching:
    print(f'Toplam İzlemede: {len(watching)}')
    for item in watching:
        candles = f"{item['candles_watched']}/{item['max_watch_candles']}"
        print(f"{item['symbol']} {item['direction']} | Candles: {candles}")
        print(f"  Reason: {item['watch_reason']}")
else:
    print('Hiç aktif watchlist item yok')

print()
print('='*70)
print('PROMOTE EDİLEN İTEMLAR (SON 1 SAAT)')
print('='*70)
c.execute('''SELECT symbol, direction, candles_watched, created_at, updated_at
             FROM watchlist 
             WHERE status='PROMOTED' AND updated_at > ?
             ORDER BY updated_at DESC''', (one_hour_ago,))
promoted = c.fetchall()

if promoted:
    print(f'Toplam Promoted: {len(promoted)}')
    for item in promoted:
        print(f"{item['symbol']} {item['direction']} | Candles: {item['candles_watched']}")
else:
    print('Hiç promote edilen item yok')

print()
print('='*70)
print('ANALİZ ÖZETİ')
print('='*70)
print(f"Expire: {len(expired)}")
print(f"Promoted: {len(promoted)}")
print(f"Aktif İzlemede: {len(watching)}")
if len(expired) > 0:
    promote_rate = len(promoted) / (len(expired) + len(promoted)) * 100 if (len(expired) + len(promoted)) > 0 else 0
    print(f"Promote Rate: {promote_rate:.1f}%")

conn.close()
