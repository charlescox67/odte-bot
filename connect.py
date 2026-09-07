"""Connect to IBKR and refuse to run against anything but a paper account."""
from __future__ import annotations

from ib_async import IB

HOST = "127.0.0.1"
PORT = 7497          # 7497 = TWS paper. 7496 = TWS LIVE. Do not "fix" this.
CLIENT_ID = 11


def connect(host: str = HOST, port: int = PORT, client_id: int = CLIENT_ID) -> IB:
    """Return a connected IB handle, or raise if it is not a paper account.

    The port is the only thing separating paper from live, so the account
    prefix is checked independently before any order can be placed.
    """
    ib = IB()
    ib.connect(host, port, clientId=client_id)

    accounts = ib.managedAccounts()
    if not accounts:
        ib.disconnect()
        raise RuntimeError("no managed accounts returned")

    non_paper = [a for a in accounts if not a.startswith("D")]
    if non_paper:
        ib.disconnect()
        raise RuntimeError(f"refusing to run: live account(s) reachable: {non_paper}")

    return ib


if __name__ == "__main__":
    ib = connect()
    print("server version :", ib.client.serverVersion())
    print("accounts       :", ib.managedAccounts())
    summary = {v.tag: v.value for v in ib.accountSummary() if v.tag == "NetLiquidation"}
    print("net liquidation:", summary.get("NetLiquidation"))
    ib.disconnect()
    print("PAPER ONLY     : verified")
