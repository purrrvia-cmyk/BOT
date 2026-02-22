"""Clean stale watchlist items before restart"""
from database import _execute, _fetchall

# Expire all watching items
_execute("UPDATE watchlist SET status='EXPIRED', expire_reason='Comprehensive fix restart' WHERE status='WATCHING'")

# Show status
watching = _fetchall("SELECT * FROM watchlist WHERE status='WATCHING'")
print(f"Remaining WATCHING items: {len(watching)}")

active = _fetchall("SELECT id, symbol, direction, status FROM signals WHERE status IN ('ACTIVE','WAITING')")
print(f"Active/Waiting signals: {len(active)}")
for s in active:
    print(f"  #{s['id']} {s['symbol']} {s['direction']} ({s['status']})")

print("Cleanup done!")
