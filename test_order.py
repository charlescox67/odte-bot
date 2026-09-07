"""One-share SPY smoke test: proves the ORDER WRITE path, then cleans up.

Read-Only API being left on is the classic silent failure — the client
connects fine and every order is rejected. Only an actual submission
proves it is off.
"""
from __future__ import annotations

from ib_async import MarketOrder, Stock

from connect import connect

ib = connect()
print("account        :", ib.managedAccounts())

spy = Stock("SPY", "SMART", "USD")
ib.qualifyContracts(spy)
print("qualified      :", spy.symbol, spy.conId, spy.primaryExchange)

trade = ib.placeOrder(spy, MarketOrder("BUY", 1))
ib.sleep(4)

print("order id       :", trade.order.orderId)
print("status         :", trade.orderStatus.status)
print("filled / rem   :", trade.orderStatus.filled, "/", trade.orderStatus.remaining)
for entry in trade.log:
    print("  log:", entry.time.strftime("%H:%M:%S"), entry.status, entry.message or "")

if trade.orderStatus.status not in ("Filled", "Cancelled", "ApiCancelled"):
    ib.cancelOrder(trade.order)
    ib.sleep(3)
    print("after cancel   :", trade.orderStatus.status)

print("positions      :", [(p.contract.symbol, p.position) for p in ib.positions()])
ib.disconnect()
