# Strategy: trading the natural swings with 0DTE options

A paper-trading bot that buys same-day (0DTE) options on the S&P 500 and the
Nasdaq-100. It trades in the direction of the day's trend, entering after a
pullback holds and price turns back. It trades an **internal paper book**
starting at **$7,000** (restarted from $100,000 on 2026-09-23 — the earlier
log is in `archive/trades-100k-paper.csv`), runs entirely on **free Yahoo
Finance data**, and does
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

| Market | Options traded | Why |
|---|---|---|
| S&P 500 | **SPY** | Same-day expirations, ~3% spread, and about $40–150 per contract, so any account can size properly. Its price feed is live. |
| Nasdaq-100 | **QQQ** | Same-day expirations, ~2% spread, similar price. |

**Left out on purpose:**
- **SPX** (traded 2026-09-21 only). One contract costs $500–2,500, so a
  0.5%-of-equity budget often can't buy even one, and on a smaller account it
  never can. Its price feed is also delayed on Yahoo, unlike SPY. Trading SPY
  *and* SPX would also be the same bet twice.
- **VOO.** No same-day options Monday to Thursday and an ~11% spread.

## 2. The 20-minute clock

**Why Yahoo?** It is free and needs no account. Checked 2026-09-21, nothing
free is faster for option quotes:

| Source | Options quotes | Cost |
|---|---|---|
| Yahoo (current) | ~16 min delayed | free |
| Barchart | 15 min delayed on the site; API is paid, and the site blocks automated access (HTTP 202) | paid |
| Alpaca free tier | 15 min delayed ("indicative") | free |
| Alpaca Algo Trader Plus | real time | $99/mo |
| Schwab API | real time | free with a brokerage account |
| IBKR + OPRA subscription | real time | small monthly fee |

Real-time option quotes essentially require a brokerage account. Underlying
(SPY/QQQ) prices from Yahoo are already live, which is why stops use them.


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
both a bid and an ask, measured against the underlying's price at the lagged
time. The bot never buys cheap far-out-of-the-money lottery tickets.

**Liquidity check.** If the bid-ask spread is more than **10% of the ask**,
the trade is skipped.

**No puts into a stretched fall.** If price sits at or below the lower
Bollinger Band (%B < 0), the put is skipped: buying into an already-extended
drop is chasing, and the bounce takes the premium. On 2026-09-23 both losing
trades were exactly that. Over 60 days such puts returned **−0.35R** (12
trades, 25% winners) against −0.09R for puts entered mid-band. Twelve trades
is suggestive, not proof, and the mirror case for calls pointed the *other*
way (+0.35R on 8 trades), so this applies to **puts only**.

**Size.** One flat **premium budget** for every trade, as a share of the
account so it scales: **4/35 of equity**, which is **$800** on $7,000.

| Conviction | Premium budget | On a $7,000 account |
|---|---|---|
| 2 or more of 4 | 4/35 of equity | **$800** |
| below 2 | not traded | — |

```
contracts = floor( (4/35 of equity) / (ask x 100) )     capped at 50
```

**Conviction no longer sets the size.** It did until 2026-09-25 ($1,000 on a
great setup, $500 on a decent one) and has not earned it: both 3/4 trades so
far **lost**, 2/4 trades went 4-for-5, and on 09-25 the score put $1,050 on
the loser and $430 on the winner — which is exactly why a +$298 winner and a
−$299 loser netted to nothing. The score still gates entry and is recorded on
every trade, so it can be judged on evidence later.

**A setup scoring below 2 is not traded at all.** Against the five real trades
to 2026-09-24 that keeps both winners (each exactly 2/4) and blocks two of the
three losers, worth +$739. A 3-of-4 minimum would have blocked *both* winners.

**Conviction** scores one point each, all knowable at entry: the pullback's
own stop was close enough not to need capping; the Bollinger bands were in a
squeeze; momentum was already running our way (the same test the runner uses);
and less than 25% of the room to the stop had been given up since the signal.

**What this risks.** An $800 position stopped at the 50% backstop loses about
**5.7% of the account**, and about 4% at the usual chart stop. That is still
8–11× the risk this bot ran up to 2026-09-23, and it is why the daily loss
limit is −8% — roughly one bad trade, then trading stops for the day.

| Example ($7,000 account) | Math | Contracts |
|---|---|---|
| ask 1.00 | 800 / 100 | **8** ($800) |
| ask 1.22 | 800 / 122 | **6** ($732) |
| ask 0.70 | 800 / 70 | **11** ($770) |
| ask 0.10 | budget buys 80 | **50** (the cap) |

## 5. Exits

Checked every minute, **in this order**: losses first, so a trade that is
going wrong is never held; then profit; then time decay.

1. **Swing stop, on live prices.** SPY and QQQ price data is real time, while
   option quotes are ~16 minutes behind. So the stop is checked against the
   **live** price and acts the moment it breaks, instead of 20 minutes later.
   The stop starts at the pullback's swing low, **moves up to each newer,
   higher swing low**, and never moves down.
   - **The stop must be reachable.** If the pullback low sits further away
     than **30% of the premium** would cover, the stop is pulled in to that
     distance. Otherwise the stop can never act before the 50% backstop, which
     is exactly how one trade lost 50% while price stayed above its stop.
   - **The exit price is estimated, conservatively.** The only quote available
     is ~16 minutes old, so the fill is that quote adjusted by the move since,
     using a delta measured from the chain itself. It is only ever adjusted
     **down**: a favourable move keeps the stale quote, so the book never
     books a gain the delayed feed hasn't printed. The `exit_basis` column
     says `estimated` or `quote` for every trade.
2. **The option loses 50% of its premium.** This backstop uses the option
   quote (adjusted as above), so it keeps working even when chart data fails.
3. **Take the profit at +60% — unless it's breaking out hard.** When the
   option reaches +60%, the bot looks at the last 10 one-minute candles of
   the stock:
   - **Slow grind, with ups and downs** → sell at +60%. Twice the ~30% the
     stop risks, so one winner covers two losers.
   - **Breaking out hard** → hold it as a **runner**. "Hard" means fast (at
     least **0.5R** in the last 10 minutes) *and* clean (efficiency **0.4** or
     more: at least 40% of all the up-and-down movement went one way).
     **Three windows are tested**: the live candles, the lagged candles, and
     the **bridge** between the two clocks (a move of 1R or more across that
     gap counts). The bridge exists because the lag splits a burst in half: on
     2026-09-25 a run happened between the two clocks, both tests read
     "stalled" — one missed by a single cent — and a move worth +173% was
     banked at +69%.
   - A runner is sold when **a 5-minute candle closes hard against it** (a body
     at least 2.5× the typical 5-minute move of the last hour), when it gives
     back more than **60% of its best gain** (it always keeps 40%, and never
     less than +30%), or at **15:30** on the lagged clock.

   Tuned on 2026-09-22 after a +68% winner went on to +173%. On 18 days of
   1-minute data the looser test doubles the runners (3 → 6) and they average
   **+3.3R**, with the overall figure improving from −0.16R to −0.12R per
   trade. The dip check reads 5-minute candles because the 1-minute version
   fired 7 minutes into a runner and sold it at +23%.
4. **Time decay: 60 minutes and going nowhere.** If the trade is up less than
   **0.25R** (a quarter of its entry-to-stop distance) after an hour, it is
   closed before decay eats it. A trade that is working keeps going.
5. **Economic-event exit** (Fed days only, see [section 7](#7-economic-event-days)).
6. **14:45 ET** (lagged clock): everything still open is closed — except a
   runner, which may hold to **15:30**. The last hour is where same-day options
   decay fastest, but it is also where the biggest moves of 09-21 and 09-22
   happened, and a runner is by definition trending.

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

**Commissions (modelled since 2026-09-22): IBKR Pro, Fixed schedule.**
Every buy and every sell pays, per contract: **$0.65** when the option costs
$0.10 or more, $0.50 from $0.05 to $0.10, $0.25 below that, with a **$1.00
minimum per order**, plus an estimated $0.03 per contract of regulatory fees
(a deliberately high guess: IBKR's site blocks automated reads, so the exact
pass-through wasn't confirmed). Expiry and exercise are not charged.

| Typical trade (0.5% risk, $100k) | Contracts | Round-trip fees | Share of premium |
|---|---|---|---|
| SPY ATM, late day (0.42) | 22 | $29.92 | **3.2%** |
| SPY ATM, morning (1.37) | 7 | $9.52 | 1.0% |
| QQQ ATM (1.03) | 9 | $12.24 | 1.3% |

Cheap contracts cost the most in fees, because you buy more of them. Sizing
includes both commissions, so a stopped trade still risks 0.5% *after* costs.
Exit rules (+60%, −50%) read the option's price move; `pnl` in the trade log
is net of fees, which get their own `fees` column. On Tiered pricing
(instead of Fixed), exchange and clearing fees are added separately.

**What it doesn't model:**
- **Fill size.** It assumes 1–10 contracts fill at the displayed bid or ask.
  That's realistic for these very liquid contracts at this size, but not at a
  much bigger size.
- **Price between checks.** Stops are checked once a minute, so a fast move can
  go further past the stop than a real stop order would allow.
- **Estimated exit prices.** A stop exit is priced from a ~16-minute-old quote
  adjusted by delta, not from a quote at that instant. Delta shifts as price
  moves (gamma), so the estimate is roughest on the largest moves. It is
  deliberately biased low, and `exit_basis` flags every affected trade.
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

**Live paper trades** start 2026-09-21. Day one, under the first version of
these rules (SPX, 1% risk, no live stop), produced three trades: **+$770**,
**−$10**, **−$840**.

The −$840 trade is why the rules above changed. SPY never came near the
trade's stop — it *rose* — but SPX slipped 5 points (0.07%), which halved a
$5.60 option and triggered the backstop. Six minutes later the option was
back above the entry price. Three faults, all now fixed:

1. **The stop was unreachable.** It sat ~10 SPX points away on an option with
   ~$5.60 of premium: reaching it would have cost ~90%, so the 50% backstop
   was always going to fire first. Stops are now capped at 30% of premium.
2. **Mismatched instruments.** The stop was measured on SPY while the option's
   value moved with SPX, where 1 SPY point ≈ 10 SPX points. Each market now
   trades options on the same instrument its stop is measured on.
3. **Risk was 1%.** $840 *was* the rule working: sizing targets 1% of equity
   at the backstop. Now 0.5%, and the 30% stop cap means a typical stopped
   trade loses ~0.3% rather than the full backstop.

### Day one review (2026-09-21)

All three trades **called the direction correctly** — the market rallied into
the close and every option finished far above where the bot sold:

| Trade | Sold at | Best price afterwards | Why it sold |
|---|---|---|---|
| SPX 7735C | 18.20 (+73%) | **44.00** (+319%) | 60-minute clock |
| QQQ 738C | 1.02 (flat) | **4.88** (+374%) | 60-minute clock |
| SPX 7760C | 2.80 (−50%) | **19.00** (+239%) | backstop |

**What worked, and stays:** the entries. Trend plus pullback got the direction
right three times out of three. The trailing stop never fired early — it was
never the thing that ended a trade. The spread filter and percentage sizing
behaved exactly as specified.

**What failed, and changed:** the exits sold winners while their trends were
intact. The 60-minute clock closed a +73% winner and a flat trade that later
reached +374%. Simulated on the day's real bars, letting the trailing stop
manage those two instead gives +264% and +330%, both running to the 15:45
close. Over 60 days the conditional clock lifts SPY from −0.14R to −0.05R per
trade and leaves QQQ unchanged, so the change isn't justified by one day
alone. The third trade's failure is covered above.

**Honest caveat:** −0.05R and −0.14R are within noise at ~100 trades (roughly
±0.1R). This rule change is better aligned with the strategy's own logic; it
is not a proven edge, and expectancy is still negative.

### Bollinger Bands and strength vs the Dow: recorded, not used (yet)

Tested 2026-09-22 as possible filters:

- **Relative strength vs the Dow** (SPY or QQQ divided by DIA, over the last
  30 minutes) didn't separate winners from losers across 60 days of the bot's
  own trades. For SPY it pointed the wrong way: trades where SPY was beating
  the Dow did *worse* (−0.12R vs +0.01R). SPY and the Dow move nearly together.
- **Bollinger Band breakouts** (20 bars, 2 standard deviations) on 18 days of
  1-minute data: after ~370 band breaks per market, the move continued 45–52%
  of the time — a coin flip. After a squeeze the follow-through was larger but
  based on only ~80 cases, and QQQ reversed in the first 5 minutes.

Neither is a rule. Each trade records `bb_pct` (where price sat in the bands:
0 = lower band, 1 = upper band, above 1 = broke out), `bb_squeeze`, and
`vs_dow_30m` (% beat or lagged the Dow over 30 minutes), so real trades can
show whether they matter.

## Where to see results

All state lives on the [`state` branch](https://github.com/charlescox67/odte-bot/tree/state):

| File | What it shows |
|---|---|
| [`trades.csv`](https://github.com/charlescox67/odte-bot/blob/state/trades.csv) | One row per **closed** trade: prices, contracts, `cost`, `proceeds` and `fees` in dollars, `pnl` (net of fees), exit reason, the pullback traded (`setup`), both stops, spread, event-day tag, `exit_basis` (`quote` or `estimated`), and the recorded Bollinger/Dow context |
| [`book.json`](https://github.com/charlescox67/odte-bot/blob/state/book.json) | Cash and **open** positions |
| [`logs/`](https://github.com/charlescox67/odte-bot/tree/state/logs) | Every minute's decision for each market: trend, VWAP, latest swing low and high, and why it did or didn't trade |

Open trades appear in `trades.csv` only once they close. Log files are saved
every 10 minutes; trades are saved the minute they happen.

## Parameters

| Setting | Value | File |
|---|---|---|
| Lagged clock (entries, quotes) | 20 min | `run_bot.py` `LAG_MIN` |
| Stops checked on | live price | `live_price`, `adjust_mark` |
| Entry window (lagged clock) | 10:00–14:00 ET | `ENTRY_START`, `NO_ENTRY_AFTER` |
| End-of-day exit | 14:45 ET (lagged clock) | `FLATTEN_AT` |
| Profit target | +60% of premium, unless breaking out hard | `TARGET_PCT` |
| Hard breakout | ≥0.5R in 10 min, efficiency ≥0.4, on live / lagged / bridge | `BREAKOUT_*`, `bridge_move` |
| Runner floor | keeps 40% of its best gain, min +30% | `RUNNER_KEEP`, `RUNNER_MIN_PCT` |
| Runner exit | 15:30 lagged (~15:50) | `RUNNER_FLATTEN` |
| Deep red candle | body ≥2.5× typical 5-min move | `DEEP_DIP_MULT` |
| Premium per trade | 4/35 of equity (~$800 on $7k) | `PREMIUM_PCT` |
| Minimum conviction | 2 of 4 | `MIN_CONVICTION` |
| Max cost of reaching the stop | 45% of premium | `STOP_COST_CAP` |
| Premium backstop | −50% | `BACKSTOP` |
| Max contracts | 50 | `MAX_QTY` |
| Commissions | IBKR Pro Fixed: $0.65/contract ($1 min/order) + ~$0.03 reg. | `paper_engine.py` `IBKR_RATES` |
| Max spread | 10% of ask | `MAX_SPREAD` |
| No puts below the lower band | %B < 0 | `MIN_BB_FOR_PUTS` |
| Entries per market per day | 3 | `MAX_ENTRIES_PER_DAY` |
| Daily loss stop | −8% | `DAILY_LOSS_LIMIT` |
| Time limit | 60 min, unless up ≥ 0.25R | `TIME_STOP_MIN`, `TIME_STOP_KEEP_R` |
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
| `.github/workflows/session.yml` | Starts the session. GitHub's scheduler is best-effort (on 2026-09-22 it dropped every slot), so there are three starters: slots every 10 minutes 08:00–10:50 ET plus hourly backups; jobs that hit the 6-hour limit dispatch their own successor; and `kick.sh` on the Mac starts one if none is running. |
| `kick.sh` | Mac backup starter (launchd, every 30 min while awake in market hours). |
| `test_swing.py`, `test_engine.py` | Tests that run before every session; the session doesn't start if they fail. |
| `screen_swing.py` | The 60-day historical check above. |

**Why not credit spreads or iron condors?** They're common 0DTE strategies,
but they *sell* options to profit when price stays in a range. This bot's idea
is the opposite: buy options and ride the move. Rules written for option
sellers generally don't carry over to buyers.
