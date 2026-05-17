# HARS Signal Dashboard — Cursor Implementation Rulebook
### Hurst-Adaptive Regime Strategy (HARS) v5.0 | Upstox Analytics | Render Deployment

---

## 0. GOLDEN RULES (Read Before Writing A Single Line)

1. **The backtest script `hars_strategy_engine.py` is the only source of truth for all calculations.** Copy logic verbatim. No simplifications, no assumptions, no "equivalent" replacements.
2. **Never show data that hasn't been fetched yet.** If a value is pending, show `—`. Never show `0`, `null`, or stale data disguised as current.
3. **Candle close buffer:** After a 5-minute candle closes, wait **15 seconds** before calling the Upstox API for that candle. This ensures the candle is fully formed and indexed by the exchange.
4. **Rate limit discipline:** With 52 instruments (Nifty 50 + Index + VIX), every fetch cycle must be batch-friendly. Never fire 52 simultaneous requests. Always stagger or batch. The hard Upstox Analytics API rate limits are:

| Time Window | Request Limit |
|---|---|
| Per second | 10 requests |
| Per minute | 500 requests |
| Per 30 minutes | 2,000 requests |

The fetch scheduler must enforce these limits **proactively — never reactively**. Design all batch fetches so that the per-second limit of 10 req/s is never approached. Target a conservative **5 requests/second maximum** to leave a safety buffer. A 429 error means the rate limiter design has failed — it should never occur in normal operation.
5. **No broker integration.** This is a read-only analytics tool. No order placement, no Upstox OAuth login flow. All data access uses the static Analytics Token stored as an environment variable.

---

## 1. Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Backend | **Python + FastAPI** | Async-friendly, easy scheduler integration |
| Scheduler | **APScheduler** (BackgroundScheduler) | Runs inside FastAPI process, no extra worker needed |
| Frontend | **React + Vite** (single page app) | Fast, simple to serve as static build |
| Styling | **Plain CSS** (no Tailwind) | Match pixel-exact design from screenshots |
| Data Store | **MongoDB Atlas** via PyMongo / Motor (async) | Cloud-hosted, persists across Render redeploys, no disk dependency |
| Deployment | **Render (Web Service)** | Single service: FastAPI serves both the API and the React static build |
| Auth | **JWT tokens** (username + password stored as env vars) | Simple session management, no DB needed for auth |

---

## 2. Project Folder Structure

```
hars-dashboard/
├── backend/
│   ├── main.py                  # FastAPI app entry point
│   ├── scheduler.py             # APScheduler jobs
│   ├── data_manager.py          # Rolling window cache + Upstox fetcher
│   ├── hars_engine.py           # EXACT COPY of hars_strategy_engine.py
│   ├── signal_tracker.py        # TP/SL/EOD outcome tracker
│   ├── db.py                    # MongoDB models + connection (TradeHistory, Instruments)
│   ├── auth.py                  # JWT login logic
│   └── config.py                # Env var loader
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── pages/
│   │   │   ├── Login.jsx
│   │   │   └── Dashboard.jsx
│   │   └── components/
│   │       ├── Navbar.jsx
│   │       ├── HeroStrip.jsx
│   │       ├── LiveScanTable.jsx
│   │       └── TradeHistoryTable.jsx
│   └── vite.config.js
├── requirements.txt
├── render.yaml
└── .env.example
```

---

## 3. Environment Variables

Store ALL secrets in Render's Environment tab. Never hardcode.

```
UPSTOX_ANALYTICS_TOKEN=<your token>
DASHBOARD_USERNAME=<your chosen username>
DASHBOARD_PASSWORD=<your chosen password>
JWT_SECRET=<random 32-char string>
MONGODB_URI=<your MongoDB Atlas connection string>
MONGODB_DB_NAME=hars_dashboard
```

---

## 4. Authentication

### 4.1 Login Page (replicate screenshot exactly)
- Full black background (`#0a0d14`)
- Centered card: dark navy background (`#0f1623`), rounded corners, no border
- Title: **"Nifty Signal Login"** in white, medium weight
- Two inputs: Username, Password — dark fill, subtle border, white text placeholder
- Blue Login button full width (`#3b6ef8`)
- On submit: POST to `/api/login` → receives JWT token → stored in `localStorage` → redirect to `/dashboard`
- All `/dashboard` and `/api/*` routes (except `/api/login`) require valid JWT in `Authorization: Bearer <token>` header

### 4.2 Backend Auth Logic
- `POST /api/login` — accepts `{username, password}`, compares against env vars, returns `{token}` (JWT, 12-hour expiry)
- Middleware: verify JWT on all protected routes
- On invalid/expired token: return 401 → frontend redirects to `/login`

---

## 5. Data Architecture

### 5.1 Instruments

The Nifty 50 constituent list is **not hardcoded**. It is stored in MongoDB and fetched from Upstox at scheduled intervals.

**MongoDB Collection: `instruments`**
```json
{
  "_id": "...",
  "symbol": "RELIANCE",
  "instrument_key": "NSE_EQ|INE002A01018",
  "added_on": "2026-01-01",
  "active": true
}
```

**Quarterly Refresh API (manual trigger + scheduled):**
- `POST /api/admin/refresh-instruments` — protected endpoint (requires JWT)
- Action: Call Upstox's instrument master API → filter for current Nifty 50 constituents → diff against MongoDB → add new entries, mark removed ones as `active: false`
- Schedule: Run automatically on the **1st trading day of every quarter** (Jan, Apr, Jul, Oct) at 08:00 IST via APScheduler
- After refresh, reload the active instrument list into memory so the next data fetch cycle uses the updated list
- The Index and VIX instrument keys are constants and are never part of the quarterly refresh:

```python
INDEX_KEY = "NSE_INDEX|Nifty 50"
VIX_KEY   = "NSE_INDEX|India VIX"
```

**On startup:** Load active instruments from MongoDB into memory. If the collection is empty (first ever run), seed it with the hardcoded list below as a bootstrap, then immediately run the refresh job to verify against Upstox.

```python
# Bootstrap seed list (used only if DB is empty on first run)
BOOTSTRAP_NIFTY50 = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "BPCL", "BRITANNIA", "CIPLA", "COALINDIA", "DRREDDY",
    "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE",
    "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK",
    "INFY", "ITC", "JSWSTEEL", "KOTAKBANK", "LT",
    "M&M", "MARUTI", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TCS",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO"
]
# Total runtime instruments: 52 (Index + VIX + 50 active stocks from DB)
```

### 5.2 Rolling Window Cache (In-Memory, server process)

```
rolling_cache = {
    "NSE_INDEX|Nifty 50": DataFrame of last 500 × 5-min candles,  # Hurst input
    "NSE_INDEX|India VIX": DataFrame of last 500 × 5-min candles,  # Hurst input
    "ADANIENT":            DataFrame of last 25 × 5-min candles,   # RVOL(20) + momentum(3) + buffer
    ... (all active 50 stocks from DB)
}
```

**Why different sizes:**
- Index and VIX need 500 bars to feed `calculate_hurst()` (minimum 100 bars required, 500 for accuracy).
- Stocks only need 25 bars maximum — 20 bars for RVOL, 3 bars for momentum, with a small buffer. Gap% is handled separately via a dedicated pre-market fetch (see MARKET_OPEN_DATA_FETCH job below) and stored in a plain dict, not the rolling cache.

**Hurst is computed ONCE per day — at 08:45 IST as part of the PRE_MARKET_DATA_FETCH job (Job 0).** It is not recomputed during the trading session. The Hurst values for Index and VIX computed at 08:45 IST are used for the entire trading day's regime classification.

**Daily Pre-Market Data Fetch (scheduled at 08:45 IST, Monday–Friday):**
1. Load active instruments from MongoDB.
2. For **Index and VIX only**: check MongoDB's `candle_cache` collection — count existing 5-min bars. Fetch only the missing bars from Upstox to reach 500 total. Write to MongoDB, trim to 500, load into in-memory `rolling_cache`.
3. For **all 50 stocks**: fetch the latest 25 × 5-min bars from Upstox (no MongoDB persistence needed for stock bars — 25 bars is small enough to fetch fresh each day). Load directly into in-memory `rolling_cache`.
4. Stagger all requests: `asyncio.sleep(0.2)` between each instrument (5 req/s max).
5. Mark state as `WARMING_UP` during this process. Show `PENDING` on dashboard.
6. Once Index, VIX (500 bars each) and all 50 stocks (25 bars each) are loaded, mark state as `WARMING_UP_GAP` — waiting for the 09:18 gap fetch.
7. Once Index/VIX have 500 bars and all 50 stocks have 25 bars loaded, mark state as
   `WARMING_UP_GAP` — the system becomes fully `READY` only after Job 0b completes the
   gap fetch at 09:18.
   **READY threshold for Hurst:** Index and VIX must each have ≥ 500 bars. If either has
   fewer than 100 bars (the Hurst minimum), mark it as INSUFFICIENT, log a CRITICAL warning,
   and do not run regime classification for the day — regime stays UNKNOWN. Stock instruments
   are not subject to this threshold since Hurst is only computed for Index and VIX.

**Holiday and Weekend Handling:**
- Before any fetch, check if today is a Saturday, Sunday, or NSE market holiday.
- Maintain a `market_holidays` collection in MongoDB with known NSE holidays for the year.
- `POST /api/admin/refresh-holidays` — admin endpoint to update the holiday list for the year.
- If today is a holiday/weekend: skip the 08:45 fetch entirely. The in-memory cache from the previous trading day persists. Dashboard shows `Market Closed`.
- When fetching "missing" bars, skip any date that falls on a weekend or in the holiday list — those dates will have no candles and must not be counted as gaps.

**Stagger rule for all batch fetches:**
- Max 5 requests per second (well within the 10/s hard limit).
- Job 0: ~52 requests minimum (2 for Index/VIX catch-up + 50 for stocks). Job 0b: 100 requests (50 stocks × 2 calls). Both complete well within rate limits when staggered at 0.2s per call.
- Between each request: `asyncio.sleep(0.2)` (200ms gap = 5 req/s).

**MongoDB Collection: `candle_cache`**
```json
{
  "instrument_key": "NSE_EQ|...",
  "symbol": "RELIANCE",
  "timestamp": "2026-05-07T09:20:00+05:30",
  "open": 1420.5,
  "high": 1425.0,
  "low": 1418.0,
  "close": 1422.3,
  "volume": 125000
}
```
Index on `(instrument_key, timestamp)` — unique compound index.

**Intraday Rolling (during market hours):**
- After each 5-minute candle close, the CANDLE_SCAN job fetches the latest 1 candle per instrument and appends it to the in-memory `rolling_cache`.
- **Index and VIX:** drop oldest candle to keep at 500 bars. Also write to MongoDB `candle_cache`.
- **Stocks:** drop oldest candle to keep at 25 bars. No MongoDB write needed — stock bars are re-fetched fresh each morning.

**Candle Close Buffer Rule:**
- Every Upstox API call for a closed candle must happen at least **15 seconds** after the candle's close time.
- Example: the 09:20 candle closes at 09:25:00 IST → first valid fetch time is 09:25:15 IST.

### 5.3 Upstox API Usage

- **This token is used exclusively for fetching market data from Upstox.** It has nothing to do with internal server-to-frontend communication or dashboard authentication.
- Endpoint: `GET /v2/historical-candle/{instrument_key}/5minute/{to_date}/{from_date}`
- Auth header: `Authorization: Bearer {UPSTOX_ANALYTICS_TOKEN}` — loaded from environment variable at startup
- Response candle fields: `[timestamp, open, high, low, close, volume, oi]`
- Never call with `to_date` = future date. Always use today's date (IST) as `to_date`.
- All Upstox calls are async (use `httpx.AsyncClient`). Stagger with `asyncio.sleep(0.2)` between calls.
- Implement a global async rate-limit tracker that counts requests per second, per minute, and per 30 minutes. If any counter is approaching its limit, pause and wait before the next request. This guard runs before every Upstox API call.

---

## 6. HARS Engine — Calculation Rules

> ⚠️ **CRITICAL: Copy `hars_strategy_engine.py` verbatim into `hars_engine.py`. Do not rewrite, simplify, or optimize any calculation logic. The following is documentation only — the code file is the source of truth.**

### 6.1 Hurst Exponent (`calculate_hurst`)
- Input: array of **returns** (not prices) — `ts = np.array(ts)`
- **Returns are computed as:** `returns = close.pct_change().dropna()` on the 500-bar close price series for Index and VIX respectively. This is the input passed to `calculate_hurst()`.
- Minimum length check: if `len(ts) < 100`, return `np.nan`
- Block sizes `n_list = [20, 50, 100, 200, 400]` — filter out any `n >= len(ts)//2`
- For each block size `n`: split series into non-overlapping blocks, compute R/S for each, average them
- Fit `log(RS)` vs `log(n)` with `np.polyfit` degree 1 → slope = Hurst
- Require at least 2 valid n values, else return `np.nan`

### 6.2 Regime Classification (`classify_regime`)
- Inputs: `idx_rets` (returns of Nifty 50), `vix_rets` (returns of India VIX)
- Threshold: `h_threshold = 0.55` (hardcoded, not configurable from UI)
- Rules:
  - `h_idx ≤ 0.55` AND `h_vix ≤ 0.55` → `MEAN_REVERTING`
  - `h_idx ≤ 0.55` AND `h_vix > 0.55` → `VOLATILITY_SHOCK`
  - Any other combination → `NO_TRADE`
  - Either Hurst is `nan` → `UNKNOWN`

### 6.3 Signal Generation (`get_signals`)

**MEAN_REVERTING regime:**
- For each stock: `day_range = high.max() - low.min()` over available intraday candles
- `atr_pct = day_range / latest_close_price`
- Select stock with **highest `atr_pct`**
- Signal: `{symbol, target: 1.5%, stop: 1.0%, description: "High-ATR Mean Reversion"}`

**VOLATILITY_SHOCK regime:**
- For each stock: `rvol = latest_volume / mean(last_20_volumes)`
- `mom = (latest_close / close_3_bars_ago) - 1` (15-min momentum using last 3 × 5-min candles)
- Candidates: stocks where `rvol > 2.0` AND `mom < 0.003`
- From candidates: select stock with **highest rvol**
- Signal: `{symbol, target: 2.0%, stop: 1.0%, description: "Accumulation Volatility Shock"}`

**NO_TRADE / UNKNOWN regime:**
- Return `None`. No signal generated.

> **Direction rule:** All signals generated by this engine are **LONG-only**. Direction is always `"LONG"` in trade_history records. No short signals are ever produced.

### 6.4 When to Run the Engine
- The engine runs **once per 5-minute candle close** — i.e., at 09:20:15, 09:25:15, 09:30:15... (15-second buffer after each candle close)
- Market hours: 09:15 IST to 15:30 IST
- Do not run the engine outside market hours. Outside hours, dashboard shows last known state.
- **Hurst values are fixed for the day** — they are computed once at **08:45 IST** as part of the PRE_MARKET_DATA_FETCH job (Job 0) and reused for every engine run during the trading session. The engine does not recompute Hurst on each cycle.

---

## 7. Scheduler Jobs (APScheduler)

> **Architecture note:** The backend server runs continuously on Render as a persistent process. It is completely independent of the frontend and the user's browser session. The scheduler runs inside the FastAPI process and executes jobs on its own clock. The user logging in or out of the dashboard has zero effect on the backend engine.

> **Timezone rule:** All APScheduler cron jobs must use `timezone='Asia/Kolkata'`. Render servers run in UTC — never rely on server local time. Example: `scheduler.add_job(fn, 'cron', hour=8, minute=45, timezone='Asia/Kolkata')`.

```
Job 0: PRE_MARKET_DATA_FETCH
  - Trigger: Cron, daily at 08:45 IST Monday–Friday
  - Guard: Skip if today is a weekend or NSE market holiday
  - Action:
      1. Load active instruments from MongoDB
      2. For Index and VIX only:
         a. Check MongoDB candle_cache — count existing 5-min bars
         b. Fetch only the missing bars from Upstox to reach 500 total
         c. Stagger: asyncio.sleep(0.2) between each request (5 req/s max)
         d. Write new bars to MongoDB candle_cache
         e. Trim to latest 500 bars if over limit
      3. For all 50 active stocks:
         a. Fetch the latest 25 × 5-min bars fresh from Upstox (no MongoDB read/write)
         b. Stagger: asyncio.sleep(0.2) between each request (5 req/s max)
      4. Load Index and VIX 500-bar series from MongoDB into in-memory rolling_cache.
         Load each stock's 25 bars directly into in-memory rolling_cache.
      5. Once complete, compute Hurst for Index and VIX returns from rolling_cache
         Returns = close.pct_change().dropna() on the 500-bar close series for each.
         Pass these return arrays into calculate_hurst() — never pass raw prices.
      6. Store h_idx and h_vix in today's session state — these values are FIXED for the day
      7. Run classify_regime(h_idx, h_vix) → store today's regime in session state
      8. Mark cache state as WARMING_UP_GAP — waiting for Job 0b (gap fetch at 09:18)

Job 0b: MARKET_OPEN_DATA_FETCH
  - Trigger: Cron, daily at 09:18 IST Monday–Friday
  - Guard: Skip if today is a weekend or NSE market holiday
  - Action:
      1. For each of the 50 active stocks, make two API calls:
         asyncio.sleep(0.2) between every individual API call — not between stock pairs.
         100 calls × 0.2s = ~20 seconds total.
         a. Fetch the 09:15 candle using 1-minute interval — this gives today's opening price
            Endpoint: GET /v2/historical-candle/{instrument_key}/1minute/{today}/{today}
            Take the first candle's `open` field as `today_open`
         b. Fetch the last available candle from the previous trading day using 1-minute interval
            Endpoint: GET /v2/historical-candle/{instrument_key}/1minute/{prev_trading_day}/{prev_trading_day}
            Take the last candle's `close` field as `yesterday_close`
            (prev_trading_day = most recent trading day before today, skipping weekends and holidays)
      2. Compute: gap_pct = (today_open - yesterday_close) / yesterday_close * 100
      3. Store in in-memory dict: gap_cache[symbol] = {today_open, yesterday_close, gap_pct}
      4. This dict is used by /api/scan for the rest of the trading day — it never changes after this job runs.
      5. Mark gap state as READY. Until this job completes, Gap% shows — in the scan table.
  - Note: 50 stocks × 2 calls = 100 requests, staggered at 5 req/s = ~20 seconds total. Well within rate limits.

Job 1: CANDLE_SCAN
  - Trigger: Cron, every 5 minutes, at :15 seconds past the minute
  - Active: 09:15 to 15:30 IST Monday–Friday
  - Guard: Skip if is_market_open() returns False
  - Action:
      1. For all 52 instruments, fetch the latest 1 closed 5-min candle from Upstox
         (The 15-second buffer after candle close is built into the :15s trigger timing)
         Stagger: asyncio.sleep(0.2) between each instrument fetch
      2. For each instrument: append new candle to in-memory rolling_cache.
         - Index and VIX: keep at 500 bars, write new candle to MongoDB candle_cache.
         - Stocks: keep at 25 bars, no MongoDB write.
         NOTE: Hurst is NOT recomputed here. rolling_cache for stocks is used for
         RVOL, ATR, and momentum calculations only. Hurst uses the pre-market snapshot.
      3. Read today's fixed h_idx, h_vix, and regime from session state
         (no classify_regime call here — regime is already set for the day)
      4. Run get_signals(regime, stock_data_pool) using in-memory rolling_cache
      5. Update in-memory current_state:
         {regime, h_idx, h_vix, nifty_price, vix_price, signal, scan_table, last_updated}
      6. If signal exists and regime != NO_TRADE: create a PendingSignal entry
         (stored in memory AND persisted to MongoDB pending_signals collection)
         Before creating a PendingSignal, check if one already exists for today's date.
         If a PendingSignal is already active (status = PENDING), skip — do not overwrite
         or create a second signal. One signal per trading day maximum.

Job 2: (REMOVED — Hourly Roll no longer needed)

Job 3: EOD_SETTLE
  - Trigger: Cron, daily at 15:15 IST Monday–Friday
  - Guard: Skip if today is a weekend or NSE market holiday
  - Action:
      1. For any open PendingSignal from today: mark as "EOD" outcome
      2. Record final exit price using the 15:10 candle close price of the signal's stock
         (last complete 5-min candle before 15:15)
      3. Calculate outcome: compare exit_price to entry_price
      4. Write completed TradeHistory record to MongoDB trade_history collection
      5. Clear today's PendingSignal from memory and mark as settled in MongoDB
      6. If no PendingSignal exists for today AND today's session regime was NO_TRADE or
         UNKNOWN all day: write one NO_TRADE record to trade_history as specified in Section 8.

Job 4: INTRADAY_TP_SL_CHECK
  - Trigger: Cron, every 5 minutes, at :45 seconds past the minute (runs after CANDLE_SCAN)
  - Active: 09:15 to 15:10 IST Monday–Friday
  - Guard: Skip if is_market_open() returns False
  - Action:
      1. If a PendingSignal exists for today and is not yet settled:
         - Use the latest close price already in rolling_cache for that stock
           (do NOT make a separate Upstox API call here — use what CANDLE_SCAN already fetched)
         - If price >= entry * (1 + target/100): mark TP_HIT, record exit price, write to MongoDB
         - If price <= entry * (1 - stop/100): mark SL_HIT, record exit price, write to MongoDB
         - Entry price = close price of the candle on which the signal was generated

Job 5: QUARTERLY_INSTRUMENT_REFRESH
  - Trigger: Cron, 1st trading day of Jan / Apr / Jul / Oct at 08:00 IST
  - Action: Same as POST /api/admin/refresh-instruments (see Section 5.1)

Job 6: KEEP_ALIVE_PING
  - Trigger: Cron, every 10 minutes
  - Action: Internal GET /api/health to prevent Render free tier from spinning down
  - Recommended: Upgrade to Render paid tier for a live trading tool to eliminate this concern
```

**Memory vs Database for candle data:**
- **In-memory (`rolling_cache`):** Used during active trading for all per-cycle calculations. Zero latency. Lost on server restart.
  - Index and VIX: 500 bars each
  - Stocks: 25 bars each (re-fetched fresh every morning at 08:45)
- **MongoDB (`candle_cache`):** Persistent store for **Index and VIX only**. Used to rebuild their 500-bar rolling_cache after a restart without re-fetching from Upstox.
- **Stock bars are NOT persisted to MongoDB** — they are always fetched fresh at 08:45 (25 bars, small and fast). No persistence needed.
- **`gap_cache` (in-memory dict):** Stores `{today_open, yesterday_close, gap_pct}` per stock. Populated at 09:18 by MARKET_OPEN_DATA_FETCH job. Lost on restart — if server restarts after 09:18, re-run the gap fetch on startup to repopulate.
- **Rule:** On startup/restart, load Index and VIX bars from MongoDB. Always re-fetch stock bars from Upstox (25 bars, fast). Re-run gap fetch if today's gap_cache is empty.

---

## 8. Trade History Logic

A record is only written to the `trade_history` MongoDB collection when **one of three outcomes** is confirmed:
1. **TP HIT** — price reached target% above entry (detected by Job 4)
2. **SL HIT** — price reached stop% below entry (detected by Job 4)
3. **EOD** — settled at 15:15 IST without TP or SL being hit (by Job 3)

Until one of these happens, the signal is `PENDING` (in memory + MongoDB `pending_signals`) and does not appear in Trade History tab.

### MongoDB Collection: `trade_history`

```json
{
  "_id": "ObjectId",
  "date": "2026-05-07",
  "symbol": "RELIANCE",
  "direction": "LONG",
  "entry": 1422.30,
  "tp": 1443.63,
  "sl": 1408.08,
  "exit_price": 1443.63,
  "regime": "VOLATILITY_SHOCK",
  "outcome": "TP_HIT",
  "status": "WIN"
}
```

### Status logic:
- `exit_price > entry` → WIN
- `exit_price < entry` → LOSS
- `exit_price == entry` → BREAKEVEN

### NO_TRADE days:
- If regime was `NO_TRADE` all day: insert one record with `symbol="-"`, `direction="NO_TRADE"`, all price fields `null`, `regime="NO_TRADE"`, `outcome="NO_TRADE"`, `status="NO_TRADE"`

---

## 9. API Endpoints (FastAPI)

```
POST  /api/login                        → {token}
GET   /api/status                       → current_state (regime, hursts, prices, signal)
GET   /api/scan                         → live scan table (all active stocks with metrics)
GET   /api/history                      → all records from trade_history (newest first)
GET   /api/health                       → {"status": "ok", "cache_ready": bool}
POST  /api/admin/refresh-instruments    → trigger quarterly instrument list refresh (JWT required)
POST  /api/admin/refresh-holidays       → update NSE holiday list for the year (JWT required)
```

All endpoints except `/api/login` require `Authorization: Bearer <token>` header.

`/api/status` response shape:
```json
{
  "nifty_price": 24512.30,
  "vix_price": 14.22,
  "h_idx": 0.48,
  "h_vix": 0.61,
  "regime": "VOLATILITY_SHOCK",
  "last_updated": "2026-05-03T09:30:30+05:30",
  "cache_ready": true
}
```

`/api/scan` response shape (array, sorted by compliance score desc):
```json
[
  {
    "symbol": "RELIANCE",
    "rvol": 3.21,
    "atr_pct": 1.42,
    "gap_pct": 0.81,
    "momentum_15m": 0.18,
    "compliance_score": 4,
    "result": "SIGNAL"
  },
  ...
]
```

---

## 10. Live Scan Table — Columns & Compliance Score

| Column | Calculation | Source |
|---|---|---|
| Symbol | Stock name | Static list |
| RVOL | `latest_vol / mean(last_20_vols)` | `get_signals` logic |
| ATR% | `(high.max() - low.min()) / latest_close * 100` | `get_signals` logic |
| Gap% | `(today_open - yesterday_close) / yesterday_close * 100` | MARKET_OPEN_DATA_FETCH job at 09:18 IST — static for the day |

> **Gap% data source:** Gap% is computed once per day by the MARKET_OPEN_DATA_FETCH job (Job 0b, scheduled at 09:18 IST). For each stock, the job fetches: (a) the first 1-minute candle of today (09:15 candle, which closes at 09:16 and is safely fetchable by 09:18) to get `today_open`, and (b) the last available candle from the previous trading day to get `yesterday_close`. Both values are stored in a plain in-memory dict `gap_cache[symbol]` and reused for the entire trading day. Gap% never changes after this fetch.
| Momentum 15m | `(latest_close / close_3_bars_ago - 1) * 100` | `get_signals` logic |
| Result | SIGNAL / WATCH / — | See below |

**Compliance Score (for sorting):**
- +1 if RVOL > 2.0
- +1 if ATR% is in top 10 of all 50 stocks
- +1 if Momentum 15m < 0.3% (accumulation condition)
- +1 if Gap% > 0 (positive gap, shows demand)
- Score range: 0–4. Sort table descending by score. Ties broken by RVOL desc.

**Result column:**
- `SIGNAL` — stock is the one selected by `get_signals()` this cycle
- `WATCH` — compliance score ≥ 2 but not the top pick
- `—` — compliance score < 2

---

## 11. Dashboard Design (Replicate Screenshots Exactly)

### Design Tokens
```css
--bg-page:       #0a0d14;   /* full page background */
--bg-card:       #0f1623;   /* cards, navbar, hero strip */
--bg-table-head: #141c2e;   /* table header row */
--bg-row-alt:    #0d1420;   /* alternating table row */
--text-primary:  #ffffff;
--text-muted:    #6b7a99;
--accent-blue:   #3b6ef8;   /* login button, active tab */
--border:        #1e2a40;
--badge-pending: #2a3550;
--font:          'Inter', sans-serif;
```

### Navbar (all pages after login)
- Height: ~60px, background `--bg-card`, bottom border `--border`
- Left: **"Nifty Signal"** bold white text
- Center: Live clock showing `HH:MM:SS AM/PM IST` — updates every second via `setInterval`
- Right: **Logout** button — grey outlined, on click clears token and redirects to `/login`

### Hero Strip (below navbar)
- Single card, background `--bg-card`, border `--border`, padding 20px
- Four columns: **Nifty 50** | **India VIX** | **Regime** | **Hurst Values**
- Each column: label in `--text-muted`, value in white large text, sub-label below in muted
- Regime: show as a pill badge — color-coded:
  - `MEAN_REVERTING` → blue pill
  - `VOLATILITY_SHOCK` → orange pill
  - `NO_TRADE` → grey pill
  - `UNKNOWN` / `PENDING` → grey pill with text "PENDING"
- Hurst column: show `H(Idx): 0.48 | H(VIX): 0.61`
- When `cache_ready = false`: all values show `—`, regime shows `PENDING`

### Tabs
- Two tabs: **Live Scan** | **Trade History**
- Active tab: white text, bottom border accent blue, slightly lighter background
- Inactive tab: muted text, no border
- Tab switching is client-side only (no page reload)

### Live Scan Tab
- Info bar below tabs: `"Pre-market data fetch at 8:45 AM IST. Gap data at 9:18 AM IST. First scan at 9:20:15 AM IST."`
- Table columns: Symbol | RVOL | ATR% | Gap% | Momentum 15m | Result
- Table sorted by compliance score (highest first) — re-sort on every `/api/scan` poll
- `Result` cell: `SIGNAL` in accent blue bold, `WATCH` in muted yellow, `—` in muted grey
- Poll `/api/scan` every **30 seconds** during market hours; every 5 minutes outside hours
- Pending state (cache not ready): show all `—` values in every cell

### Trade History Tab
- Table columns: Date | Stock | Direction | Entry | TP | SL | Regime | Status
- Newest date first
- `Status` cell color: WIN → green text, LOSS → red text, NO_TRADE / BREAKEVEN → muted grey
- No pagination needed initially — show all rows (add later if > 100 rows)
- Poll `/api/history` every 60 seconds

### Login Page
- Full black background `--bg-page`
- Centered card `--bg-card`, border-radius 12px, padding 40px, width 380px
- Title: "Nifty Signal Login", 22px, white, `font-weight: 600`
- Inputs: full width, background `#1a2235`, border `--border`, border-radius 6px, padding 12px, white text, `color: --text-muted` placeholder
- Login button: full width, background `--accent-blue`, white text, border-radius 6px, height 44px

---

## 12. Render Deployment

### render.yaml
```yaml
services:
  - type: web
    name: hars-dashboard
    env: python
    buildCommand: "pip install -r requirements.txt && cd frontend && npm install && npm run build"
    startCommand: "uvicorn backend.main:app --host 0.0.0.0 --port $PORT"
    envVars:
      - key: UPSTOX_ANALYTICS_TOKEN
        sync: false
      - key: DASHBOARD_USERNAME
        sync: false
      - key: DASHBOARD_PASSWORD
        sync: false
      - key: JWT_SECRET
        sync: false
      - key: MONGODB_URI
        sync: false
      - key: MONGODB_DB_NAME
        sync: false
```

### Static File Serving
FastAPI must serve the React build as static files:
```python
from fastapi.staticfiles import StaticFiles
app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="static")
```
All `/api/*` routes must be registered **before** the static mount.

### MongoDB Atlas Setup
- Create a free M0 cluster on MongoDB Atlas.
- Whitelist all IPs (`0.0.0.0/0`) since Render's outbound IPs can change.
- Collections needed: `instruments`, `candle_cache`, `trade_history`, `pending_signals`, `market_holidays`, `daily_session`
  - `daily_session` — one document per trading day storing `h_idx`, `h_vix`, `regime`, and `cache_state`. Written by Job 0 after Hurst computation. Read on startup/restart to restore today's session without recomputing.
- Index on `candle_cache`: unique compound index on `(instrument_key, timestamp)`
- No Persistent Disk needed on Render — MongoDB Atlas handles all persistence.

### Keep-Alive (Render free tier spins down after 15 min)
- Job 6 (KEEP_ALIVE_PING) pings GET /api/health every 10 minutes.
- Strongly recommended: upgrade to Render paid tier for a live trading tool.

---

## 13. Market Hours Guard

```python
import pytz
from datetime import datetime

def is_market_open() -> bool:
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    market_open  = now.replace(hour=9,  minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close
```

- CANDLE_SCAN and INTRADAY_TP_SL_CHECK jobs must call `is_market_open()` at the start and return immediately if False.
- CANDLE_SCAN handles all intraday rolling — no separate HOURLY_ROLL job exists.
- EOD_SETTLE runs at 15:15 IST on weekdays only.

---

## 14. Error Handling & Edge Cases

| Scenario | Behaviour |
|---|---|
| Upstox API returns empty candle | Skip that instrument this cycle. Do not update cache. Do not run engine. Log warning. |
| Upstox API returns 401 (unauthorised) | Token expired or invalid. Log CRITICAL. Halt all Upstox requests immediately. Dashboard Hero Strip shows `"Data Feed Error"`. Requires manual token refresh in Render env vars + server redeploy. |
| Upstox API returns 429 (rate limit) | This must never happen — it means the proactive rate limiter in Section 0 Rule 4 has a bug. Log as CRITICAL error, halt all Upstox requests for 60 seconds, then resume. Fix the rate limiter before next deploy. |
| Hurst returns `nan` for Index or VIX | Regime = UNKNOWN. Show PENDING in dashboard. No signal generated. |
| Signal generated but entry price fetch fails | Do not create PendingSignal. Log error. |
| Server restarts mid-day | Load Index and VIX 500-bar history from MongoDB candle_cache into rolling_cache. Re-fetch 25 stock bars fresh from Upstox (fast — 50 requests staggered). Load any open PendingSignal from MongoDB back into memory. **Hurst: check session state for today's stored h_idx and h_vix. If they exist, use them — do NOT recompute. Only recompute if no stored values exist (restart before 08:45 IST).** **Gap%: if today's gap_cache is empty (restart after 09:18), immediately re-run MARKET_OPEN_DATA_FETCH to repopulate it. If restart is before 09:18, gap_cache will be populated when the job fires normally.** |
| Weekend / holiday | Jobs 1 and 4 (intraday) skip via `is_market_open()`. Jobs 0, 0b, and 3 skip via their own weekend/holiday guard (check against `market_holidays` collection). Job 5 skips if today is not the 1st trading day of a quarter. Dashboard shows last state with "Market Closed" sub-label. |
| Cache not yet ready (pre-market or loading) | All dashboard values show `—`. Regime shows `PENDING`. Hero strip shows `WARMING UP...` |
| MongoDB connection failure on startup | Log CRITICAL. Retry connection with exponential backoff (1s, 2s, 4s, 8s, max 60s). Do not start scheduler until DB is connected. |

---

## 15. What Cursor Should NOT Do

- ❌ Do not add any broker order execution code
- ❌ Do not add WebSocket streaming from Upstox (polling is sufficient and simpler)
- ❌ Do not add any charting library (TradingView, Recharts, etc.) — not in the design
- ❌ Do not use Pandas TA, TA-Lib, or any third-party indicator library — compute everything from raw OHLCV as per the backtest script
- ❌ Do not add user management, multiple users, or role-based access
- ❌ Do not add any email / Telegram / alert system
- ❌ Do not "optimize" the Hurst calculation — copy it verbatim from the backtest script
- ❌ Do not add a VIX Hurst "tiebreaker" logic that isn't in the backtest script
- ❌ Do not auto-refresh the page — use polling via `fetch()` in the background

---

## 16. Implementation Order for Cursor

Build in this exact sequence. Do not proceed to the next step until the current one works and is tested.

```
Phase 1 — Skeleton
  1.1  Set up FastAPI project structure
  1.2  Add /api/health endpoint
  1.3  Add auth (login endpoint + JWT middleware)
  1.4  Deploy skeleton to Render and verify login works

Phase 2 — Data Layer
  2.1  Copy hars_strategy_engine.py → hars_engine.py verbatim
  2.2  Build Upstox API client (fetch historical candles, rate-limit semaphore)
  2.3  Build rolling_cache: 500 bars for Index/VIX (MongoDB-backed), 25 bars for stocks (fresh daily fetch)
  2.4  Build gap_cache: MARKET_OPEN_DATA_FETCH job fetching 1-min candles at 09:18
  2.5  Verify: after 08:45 job, Index/VIX have 500 bars and all stocks have 25 bars in memory
  2.6  Verify: after 09:18 job, gap_cache has today_open, yesterday_close, gap_pct for all 50 stocks
  2.7  Verify: intraday rolling correctly appends new candles and trims to correct sizes (500 / 25)

Phase 3 — Engine Integration
  3.1  Wire CANDLE_SCAN job to read fixed regime and Hurst values from today's session state —
       do NOT call classify_regime. Wire get_signals(regime, stock_data_pool) to run after
       candle fetch. Flow: fetch candles → read session regime → run get_signals.
  3.2  Expose /api/status endpoint
  3.3  Verify: regime and signal outputs match manual backtest runs

Phase 4 — Trade Tracking
  4.1  Set up MongoDB trade_history collection and pending_signals collection (schemas defined in Section 8)
  4.2  Build PendingSignal in-memory tracker
  4.3  Build INTRADAY_TP_SL_CHECK job
  4.4  Build EOD_SETTLE job
  4.5  Expose /api/history endpoint
  4.6  Verify: a simulated signal correctly transitions to TP_HIT / SL_HIT / EOD

Phase 5 — Scan Table
  5.1  Compute per-stock metrics (RVOL, ATR%, Gap%, Momentum 15m)
  5.2  Compute compliance score and sort
  5.3  Expose /api/scan endpoint
  5.4  Verify: SIGNAL stock matches the one returned by get_signals

Phase 6 — Frontend
  6.1  Build Login page (match screenshot exactly — colors, fonts, layout)
  6.2  Build Navbar + clock
  6.3  Build Hero strip with live polling of /api/status
  6.4  Build Live Scan table with polling of /api/scan
  6.5  Build Trade History table with polling of /api/history
  6.6  Wire tab switching
  6.7  Verify: all PENDING states show — not 0 or null

Phase 7 — Final Checks
  7.1  Confirm 15-second candle buffer is in place
  7.2  Confirm rate-limit semaphore is in place (max 5 concurrent requests)
  7.3  Confirm market hours guard on all scheduler jobs
  7.4  Confirm MongoDB Atlas connection string is set in Render env vars and all collections are indexed
  7.5  Deploy full build to Render and do an end-to-end test on a market day
```

---

*End of Rulebook — HARS Signal Dashboard v1.0*
