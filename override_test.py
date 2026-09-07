from __future__ import annotations
from ib_async import LimitOrder, MarketOrder, Stock
from connect import connect

ib = connect()
spy = Stock("SPY", "SMART", "USD"); ib.qualifyContracts(spy)

def attempt(label, order, wait=12):
    print(f"\n--- {label} ---")
    tr = ib.placeOrder(spy, order)
    ib.sleep(wait)
    print("status:", tr.orderStatus.status, "| filled:", tr.orderStatus.filled)
    for e in tr.log:
        print("   ", e.status, e.message or "")
    return tr

t1 = attempt("plain MKT", MarketOrder("BUY", 1))

o2 = MarketOrder("BUY", 1); o2.outsideRth = True
t2 = attempt("MKT outsideRth=True", o2)

# Marketable limit: priced far through the offer, fills like a market order
# but needs no quote from IBKR to be accepted.
o3 = LimitOrder("BUY", 1, 999.00); o3.outsideRth = True
t3 = attempt("marketable LMT 999 outsideRth", o3)

for tr in (t1, t2, t3):
    if tr.orderStatus.status not in ("Filled", "Cancelled", "ApiCancelled", "Inactive"):
        ib.cancelOrder(tr.order)
ib.sleep(3)
print("\nfinal statuses:", [t.orderStatus.status for t in (t1, t2, t3)])
print("positions:", [(p.contract.symbol, p.position) for p in ib.positions()])
ib.disconnect()
