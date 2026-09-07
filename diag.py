from __future__ import annotations
from ib_async import Forex, Index, Stock
from connect import connect

ib = connect()
print("accounts:", ib.managedAccounts(), "| server time:", ib.reqCurrentTime())

contracts = [
    ("SPY  stock", Stock("SPY", "SMART", "USD")),
    ("AAPL stock", Stock("AAPL", "SMART", "USD")),
    ("EURUSD fx ", Forex("EURUSD")),
    ("SPX  index", Index("SPX", "CBOE", "USD")),
]
for label, c in contracts:
    try:
        ib.qualifyContracts(c)
    except Exception as e:
        print(f"{label}: qualify FAILED {e}")
        continue
    for mtype, name in ((1, "live"), (2, "frozen"), (3, "delayed"), (4, "dly-frzn")):
        ib.reqMarketDataType(mtype)
        t = ib.reqMktData(c, "", True, False)
        ib.sleep(2.5)
        got = [v for v in (t.bid, t.ask, t.last, t.close) if v == v]  # drop nan
        print(f"{label} [{name:8}] -> {'DATA ' + str(got) if got else 'nothing'}")
        ib.cancelMktData(c)
        if got:
            break
ib.disconnect()
