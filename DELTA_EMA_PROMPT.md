# Prompt: Multi-Symbol Delta Exchange Trading Bot (EMA "Strict Body Cross" Strategy)

**Objective**: Build a production-grade trading bot for **Delta Exchange (India)** that scans for high-volatility perpetual futures and executes a specific EMA-based momentum strategy.

#### **1. Market Scanning & Setup**
*   **Dynamic Selection**: The bot must scan all live perpetual contracts.
*   **Filter Criteria**: Only trade symbols where the 24-hour price change is greater than `MIN_CHANGE_PCT` (default 5%).
*   **Periodic Refresh**: Re-scan every 5 minutes to add new "top movers" to the watchlist.
*   **Warmup**: Upon starting or adding a symbol, fetch 1000 historical candles (30m timeframe) to calculate initial indicators.

#### **2. Trading Strategy (Long Only)**
*   **Indicators**:
    *   **Entry**: 9 EMA (Fast) and 15 EMA (Slow).
    *   **Exit**: 50 EMA.
    *   **Risk**: ATR (14) and Pivot-based Swing Highs/Lows.
*   **The "Strict Body Cross" Signal**:
    1.  **Trend**: Fast EMA must be above Slow EMA AND Slow EMA must be rising.
    2.  **The Dip**: The current candle’s `Low` must have touched or gone below the Slow EMA.
    3.  **The Breakout**: The candle `Open` must be below both EMAs, and the `Close` must be above both EMAs (plus a buffer).
    4.  **Confirmation**: The candle must be **Green**, its `High` must be higher than the previous candle's `High`, and its size must fall within a specific `MIN/MAX_RANGE_PCT`.
*   **Execution**:
    *   **Trigger**: If a signal is confirmed, wait for the **very next candle**. If price breaks above the signal candle's `High`, execute a **Market Buy**.
    *   **Target**: Use the most recent fractal **Swing High** (from the last 50 candles) as the take-profit price.
    *   **Stop Loss**: Use the signal candle's `Low` (or a recent Swing Low) as the initial stop.

#### **3. Exit & Risk Management**
*   **Bracket Orders**: Immediately after a market entry, place exchange-side **Stop Market** and **Take Profit Market** orders. Use `reduce_only=True`.
*   **Trailing Stop**: If price moves in profit by `1.0 * ATR`, move the exchange Stop Loss to the **Entry Price** (Breakeven).
*   **EMA Trend Exit**:
    *   If a **Red** candle opens below the 50 EMA, touches it (High > EMA), but closes below it, flag an "Exit Pending."
    *   If the next candle breaks below that signal candle's `Low`, exit at Market.

#### **4. Reliability & Fail-Safes**
*   **Broker Sync**: Every 15 seconds, poll the exchange for active positions. If a position is closed manually (outside the bot), the bot must detect it, cancel any "orphan" SL/TP orders, and reset to "watch" mode.
*   **Connection**: Use a WebSocket for real-time price updates. If the connection drops, it must auto-reconnect and re-subscribe to all symbols.
*   **Persistence**: Save all active trade data to `bot_state.json`. If the bot restarts, it must resume tracking existing positions and their associated Order IDs.
*   **Logging**: Maintain a `trade_log.csv` of every entry/exit and print a "Heartbeat" dashboard showing current prices and trend status (🟢/🔴).

#### **5. Technical Specifications**
*   **API**: Delta Exchange v2 (REST + WebSocket).
*   **Auth**: HMAC-SHA256 signing using API Key/Secret.
*   **Environment**: Toggleable between India Production and Testnet servers.
