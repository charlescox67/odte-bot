from __future__ import annotations
from ib_async import LimitOrder, Stock
from connect import connect

ib = connect()
spy = Stock("SPY", "SMART", "USD"); ib.qualifyContracts(spy)

for mtype, label in ((1, "LIVE"), (3, "DELAYED")):
    ib.reqMarketDataType(mtype)
    t = ib.reqMktData(spy, "", False, False)
    ib.sleep(4)
    print(f"{label:8} bid={t.bid} ask={t.ask} last={t.last} close={t.close}")
    ib.cancelMktData(spy)

# Limit orders do not need a quote to be accepted -> isolates the write path.
trade = ib.placeOrder(spy, LimitOrder("BUY", 1, 100.00))
ib.sleep(4)
print("LMT status     :", trade.orderStatus.status)
for e in trade.log:
    print("  log:", e.status, e.message or "")
ib.cancelOrder(trade.order); ib.sleep(2)
print("after cancel   :", trade.orderStatus.status)
ib.disconnect()
