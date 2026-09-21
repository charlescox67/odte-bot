# Strategy: trading the natural swings with 0DTE options

A paper-trading bot that buys same-day (0DTE) options on the S&P 500 and the
Nasdaq-100. It trades in the direction of the day's trend, entering after a
pullback holds and price turns back. It trades an **internal paper book**
starting at $100,000, runs entirely on **free Yahoo Finance data**, and does
not connect to any broker. It runs on GitHub Actions every trading day.

> **Status: unproven.** A 60-day historical check of these rules was negative
> (see [Evidence so far](#evidence-so-far)). The paper book is the real test.
> Nothing here is financial advice.

**Contents**
1. [Markets](#1-markets)
2. [The 20-minute clock](#2-the-20-minute-clock)
3. [Entry](#3-entry)
4. [Contract and size](#4-contract-and-size)
5. [Exits](#5-exits)
6. [Daily limits](#6-daily-limits)
7. [Economic-event days](#7-economic-event-days)
8. [How accurate the paper results are](#8-how-accurate-the-paper-results-are)
9. [Evidence so far](#evidence-so-far)
10. [Where to see results](#where-to-see-results)
11. [Parameters](#parameters)
12. [Code map](#code-map)

---

## 1. Markets

| Market | Options traded | Chart the signal reads | Why |
|---|---|---|---|
| S&P 500 | **SPX** (SPXW daily expirations) | **SPY** | Tightest spread (~1%), expires every day, settles in cash. Yahoo reports no volume for the SPX index, so the signal reads SPY, which tracks the same index. |
| Nasdaq-100 | **QQQ** | **QQQ** | Daily expirations, ~2% spread. |

**Left out on purpose:**
- **VOO.** No same-day options Monday to Thursday, a spread of about 11%, and
  the same S&P 500 bet as SPX anyway.
- **SPY options.** Trading SPY *and* SPX on one signal would be the same bet
  twice.

## 2. The 20-minute clock

Yahoo's stock charts are close to real time, but its **option quotes are
delayed**. If the bot read a live chart signal and then "bought" at a delayed
option price, it would be buying at prices from *before* the move it just saw.
That's free money that doesn't exist in real trading.

So the bot runs everything on a **lagged clock**: at 12:00 ET it looks at the
market **as of 11:40**, and only at bars that had fully finished by 11:40. The
option quotes it fills at come from roughly that moment too. In effect the bot
lives 20 minutes in the past, which keeps its P&L honest. How well that
20-minute assumption matches the real delay is measured in
[section 8](#8-how-accurate-the-paper-results-are).

## 3. Entry

All of this is read from **5-minute bars** built from 1-minute bars, only as
of the lagged clock.

**1. The trend must agree.**
- Uptrend: the last 5-minute close is above the session **VWAP**
  (volume-weighted average price since 09:30), *and* the 9-bar average is above
  the 21-bar average (exponential moving averages).
- Downtrend: both reversed.
- Neither: no trade.

**2. A pullback must hold.** A **swing low** is a 5-minute bar whose low is
lower than the 2 bars on each side of it. It's only known 2 bars (10 minutes)
later, once those bars exist. In an uptrend the bot needs:
- the latest swing low to be **higher** than the swing low before it (a
  *higher low*),
- that swing low to have been confirmed within the **last 30 minutes**, and
- no 5-minute bar to have closed **below** it since.

**3. Price must turn back.** The last completed 5-minute bar **closes above
the previous bar's high**. That's the entry signal.

**4. One trade per pullback.** Each swing low can be traded once. Its time is
the trade's `setup` id; `L1130` means the swing low of the 11:30 bar.

Downtrends are the exact mirror: a *lower high*, no close above it, a close
below the previous bar's low, and the bot buys **puts**.

**Entry window:** 10:00 to 14:00 ET on the lagged clock. 10:00 gives real swings
time to form after the open; 14:00 avoids late-day swings where small moves hit
option prices very hard.

```
  price                         uptrend -> buy calls
    |            /\   <- stop trails up to each new higher low
    |      /\   /  \
    |     /  \ /
    |    /    HL  <- initial stop
    |   /      ^ entry: a bar closes above the previous bar's high
    |__/__________________ VWAP
```

## 4. Contract and size

**Contract.** The **nearest-to-the-money** same-day option, as long as it has
both a bid and an ask. For SPX, "nearest" is measured against SPX's own price
at the lagged time, not SPY's. The bot never buys cheap far-out-of-the-money
lottery tickets.

**Liquidity check.** If the bid-ask spread is more than **10% of the ask**,
the trade is skipped.

**Size.** The number of contracts is set so that hitting the 50% loss exit
(below) would lose **1% of the account**:

```
contracts = floor( 1% of equity / (ask x 100 x 50%) )     capped at 10
```

If that comes out below 1, the contract is too expensive for the risk budget
and the trade is skipped.

| Example | Math | Contracts |
|---|---|---|
| SPX, ask 10.50, $100k | 1,000 / 525 = 1.9 | **1** ($1,050 spent) |
| SPX, ask 25.00, $100k | 1,000 / 1,250 = 0.8 | **skipped** |
| QQQ, ask 1.03, $100k | 1,000 / 51.5 = 19.4 | **10** (the cap; $1,030 spent) |

Because contract counts round down, the real risk per trade is usually
**0.5–1%** of the account, not exactly 1%.

## 5. Exits

Checked every minute. Whichever happens **first** closes the trade:

1. **The option loses 50% of its premium.** This backstop uses only the option
   quote, so it keeps working even when the chart data fails to load.
2. **Swing stop.** The underlying closes a 1-minute bar below the stop (above
   it, for puts). The stop starts at the pullback's swing low. It **moves up
   to each newer, higher swing low** while the trend continues, and never
   moves down. For SPX trades the stop is measured on **SPY's chart**.
3. **60 minutes** after entry.
4. **Economic-event exit** (Fed days only, see [section 7](#7-economic-event-days)).
5. **15:45 ET**: everything still open is closed.

There's **no fixed profit target**. The trailing stop decides when a winner
ends.

Exits sell at the **bid**, and entries buy at the **ask**, so every trade
pays the full spread.

If a position is somehow still open at the 16:00 close, it settles at its
intrinsic value (what the option is worth at the closing price).

## 6. Daily limits

- **One open position per market** at a time.
- **At most 3 entries per market per day.**
- **No new entries after a −2% day.** The day's loss is closed plus open P&L
  on trades entered that day, measured against the account value at the start
  of the day. Open positions are still managed normally.

## 7. Economic-event days

Scheduled releases can move the market sharply and make fills worse. Dates
come from the official calendars:
[BLS](https://www.bls.gov/schedule/2026/home.htm) and
[Federal Reserve](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm).

| Day | Released | Rule |
|---|---|---|
| CPI, PPI, jobs report | 08:30 ET, before the open | **Half the usual size** |
| Fed decision (FOMC) | 14:00 ET | **No entries after 13:00**; everything **closed at 13:55** (lagged clock, so exits are priced before the announcement) |

Remaining 2026 dates:
- **CPI:** Oct 14, Nov 10, Dec 10
- **PPI:** Oct 15, Nov 13, Dec 15
- **Jobs report:** Oct 2, Nov 6, Dec 4
- **FOMC:** Oct 28, Dec 9

The list ends on **2026-12-31**. After that, the log prints a warning every
tick until 2027 dates are added to `event_calendar.py`.

## 8. How accurate the paper results are

**Measured delay (2026-09-21, 13:30–13:40 ET).** For 10 minutes the option
chains were sampled once a minute. Near the current price, *call − put ≈
underlying − strike* (put-call parity), so the quotes reveal which moment's
price they reflect. That was compared with the actual price minute by minute:

| | Best-fit delay | Confidence |
|---|---|---|
| **SPX options** | **~16 min** | High. SPX moved 9 points, and a 20-minute delay fit twice as badly as 16. |
| QQQ options | unclear | Low. QQQ barely moved (0.59), so 0–15 minutes all fit about equally well. |
| Last-trade timestamps (all chains) | 15 min | Direct. |

**What that means.** The bot assumes 20 minutes, but the quotes are about
15–16 minutes old. So every fill is priced **4–5 minutes after** the moment the
bot made its decision, never before it.
- **No peeking.** The bot never "buys" at a price from before the move it
  reacted to.
- **A small built-in delay.** It behaves like placing each order about 4–5
  minutes late. For a momentum entry that usually means paying a little more,
  and a stop exit usually sells a little lower. The paper results should be
  slightly **worse**, not better, than the same rules with real-time data.
- **20 is kept on purpose.** Setting it lower than the true delay would flip
  that error into peeking. That's worse than being a few minutes slow, and
  QQQ's delay couldn't be pinned down.

**What the paper book gets right:**
- **Real, live quotes.** Every entry and exit uses an option quote that
  actually existed. Nothing is estimated from a pricing model.
- **Real costs of the option itself.** Time decay (theta) and the bid-ask
  spread are already in those prices.
- **No peeking.** Decisions use only bars that had finished by the lagged
  time. The tests check this directly.

**What it doesn't model:**
- **Commissions and exchange fees.** Typically about $0.65 per contract per
  side at retail brokers, plus exchange fees on SPX. A 10-contract QQQ round
  trip costs roughly $13–15 that the book doesn't deduct.
- **Fill size.** It assumes 1–10 contracts fill at the displayed bid or ask.
  That's realistic for these very liquid contracts at this size, but not at a
  much bigger size.
- **Price between checks.** Stops are checked once a minute, so a fast move can
  go further past the stop than a real stop order would allow.
- **Missed minutes.** If Yahoo fails to respond, that minute is skipped. The
  50% backstop still runs as long as the options quote loads.

## Evidence so far

**60-day historical check** (`screen_swing.py`). It replays these exact rules,
using the same functions the bot runs, over 60 days of 5-minute prices.
Yahoo has no historical option prices, so it measures the **underlying only**,
in *R* (1R is the distance from entry to the initial stop). It doesn't include
time decay or spreads, which can only make these numbers worse.

| | Trades | Winners | Average per trade | First 30 days → last 30 days |
|---|---|---|---|---|
| SPY | 118 | 44% | **−0.17R** | +0.17R → −0.51R |
| QQQ | 119 | 46% | **−0.02R** | +0.18R → −0.21R |

**Verdict: no evidence of an edge.** The rules weren't adjusted to improve
these numbers: with 60 days of data, that would just be fitting noise.

The previous strategy, an opening-range breakout, was also checked over 60
days and found no edge either. It was replaced on 2026-09-21.

**Live paper trades** start 2026-09-21. See the trade log below.

## Where to see results

All state lives on the [`state` branch](https://github.com/charlescox67/odte-bot/tree/state):

| File | What it shows |
|---|---|
| [`trades.csv`](https://github.com/charlescox67/odte-bot/blob/state/trades.csv) | One row per **closed** trade: prices, contracts, `cost` and `proceeds` in dollars, `pnl`, exit reason, the pullback traded (`setup`), both stops, spread, event-day tag |
| [`book.json`](https://github.com/charlescox67/odte-bot/blob/state/book.json) | Cash and **open** positions |
| [`logs/`](https://github.com/charlescox67/odte-bot/tree/state/logs) | Every minute's decision for each market: trend, VWAP, latest swing low and high, and why it did or didn't trade |

Open trades appear in `trades.csv` only once they close. Log files are saved
every 10 minutes; trades are saved the minute they happen.

## Parameters

| Setting | Value | File |
|---|---|---|
| Lagged clock | 20 min | `run_bot.py` `LAG_MIN` |
| Entry window (lagged clock) | 10:00–14:00 ET | `ENTRY_START`, `NO_ENTRY_AFTER` |
| End-of-day exit | 15:45 ET | `FLATTEN_AT` |
| Risk per trade | 1% of equity at the backstop | `RISK_PCT` |
| Premium backstop | −50% | `BACKSTOP` |
| Max contracts | 10 | `MAX_QTY` |
| Max spread | 10% of ask | `MAX_SPREAD` |
| Entries per market per day | 3 | `MAX_ENTRIES_PER_DAY` |
| Daily loss stop | −2% | `DAILY_LOSS_LIMIT` |
| Time limit | 60 min | `TIME_STOP_MIN` |
| Swing definition | beats 2 bars each side | `swing_signal.py` `PIVOT_K` |
| Swing must be recent | confirmed within 30 min | `FRESH_BARS` (6 bars) |
| Trend averages | 9 and 21 bars (5-min) | `EMA_FAST`, `EMA_SLOW` |
| Data-day size | ×0.5 | `event_calendar.py` `DATA_RISK_MULT` |
| Fed day cutoff / close | 13:00 / 13:55 | `FOMC_ENTRY_CUTOFF`, `FOMC_FLATTEN` |

## Code map

| File | Role |
|---|---|
| `swing_signal.py` | The entry and stop rules. Pure functions with no network or clock access, so they're fully testable. |
| `run_bot.py` | One tick: fetch data, manage open trades first, then look for entries. Sizing and limits live here. |
| `event_calendar.py` | Economic-release and Fed dates and the rules for those days. |
| `paper_engine.py` | The paper book: cash, positions, settlement, trade log. |
| `cloud_loop.py` | Runs one tick per minute for a session on GitHub Actions and saves state to the `state` branch. |
| `.github/workflows/session.yml` | Starts a job every hour on the hour plus 7 minutes, 09:07–16:07 ET, weekdays. One job waits behind the running one, so a late or dropped start is covered. |
| `test_swing.py`, `test_engine.py` | Tests that run before every session; the session doesn't start if they fail. |
| `screen_swing.py` | The 60-day historical check above. |

**Why not credit spreads or iron condors?** They're common 0DTE strategies,
but they *sell* options to profit when price stays in a range. This bot's idea
is the opposite: buy options and ride the move. Rules written for option
sellers generally don't carry over to buyers.
