# Shooting Star Pro EA - Documentation & Installation Guide

## 1. Introduction
The **Shooting Star Pro EA** is a professional-grade MetaTrader 5 Expert Advisor designed for high-precision trading based on candlestick reversal patterns. It specifically targets **Shooting Star** (bearish) and **Hammer** (bullish) formations, utilizing a tick-by-tick breakout entry mechanism to ensure optimal execution.

---

## 2. Installation Guide
1. **Locate MQL5 Folder**: Open your MT5 Terminal, go to `File` -> `Open Data Folder`.
2. **Copy EA File**: Navigate to `MQL5/Experts` and paste the `ShootingStar_Pro_EA.mq5` file.
3. **Compile**:
   - Open MetaEditor (`F4`).
   - Find the EA in the Navigator on the left.
   - Click `Compile` at the top. Ensure there are 0 errors and 0 warnings.
4. **Load to Chart**:
   - Go back to MT5.
   - Drag the EA from the `Navigator` window onto any chart (e.g., EURUSD M15).
5. **Enable Trading**: Ensure "Algo Trading" is enabled (Green icon at the top of MT5) and "Allow Algo Trading" is checked in the EA's "Common" tab.

---

## 3. Input Settings Explanation

### Strategy Settings
- **Signal Timeframe**: The timeframe used to detect patterns (Default: M15).
- **Min Upper Wick %**: Minimum required size of the upper wick relative to the total candle range for a Shooting Star.
- **Max Lower Wick %**: Maximum allowable size of the lower wick for a Shooting Star.
- **Max Body %**: Maximum allowable body size relative to the range (ensures a "pin" look).
- **Require Bearish Candle**: If true, Shooting Star must close lower than open (and Hammer must close higher).

### Entry & Exit
- **Entry Buffer (Points)**: Points away from the candle Low/High to place the entry breakout.
- **Stop Loss Buffer (Points)**: Points added to the candle High/Low for safety.
- **Risk Reward Ratio**: The target profit multiple (e.g., 3.0 for 1:3 RR).
- **Cancel On New Candle**: If true, if price doesn't break the entry level in the very next candle, the signal is cancelled.

### Risk Management
- **Risk Mode**: Choose between Fixed Lots, Risk in USD, or Risk in % of Balance.
- **Max Daily Loss/Trades**: Fail-safe protections to stop the EA if daily limits are hit.
- **Max Spread**: Prevents entries during volatile periods with wide spreads.

### Advanced Trade Mgmt
- **Breakeven**: Moves SL to entry price once a certain RR is reached.
- **Partial TP**: Closes a percentage of the position at a specific RR target.
- **Trailing Stop**: Actively trails the price to lock in profits.

### Filters
- **EMA Trend Filter**: Only sells below the EMA and buys above it.
- **ATR Volatility Filter**: Ensures signal candles meet a minimum size requirement relative to recent volatility.

---

## 4. Code Structure Overview
The EA is built with a modular architecture:
- **`OnInit()`**: Initializes indicator handles (EMA, ATR) and configures trade settings.
- **`OnTick()`**: The heart of the EA. It runs every price change.
    - `CheckForSignal()`: Scans for closed candles matching the pattern.
    - `MonitorEntry()`: Watches every tick for a breakout of the signal candle's extremes.
    - `ManageTrades()`: Handles BE, Trailing, and Partial TP for open positions.
- **`OnTester()`**: Provides a custom metric for optimization (Profit Factor * Recovery Factor).
- **`ExportReport()`**: Saves backtest results to `ShootingStar_Research.csv` for quantitative analysis.

---

## 5. Strategy Logic Detail
1. **Detection**: Upon candle close, the EA calculates wick/body ratios.
2. **Setup**: If valid, it draws lines on the chart and stores the "Trigger Price".
3. **Trigger**: Unlike traditional EAs that wait for the *next* close, this EA enters **immediately** when the `Bid` (for Sell) or `Ask` (for Buy) touches the trigger level.
4. **Invalidation**: If price moves and hits the Stop Loss level before the entry is triggered, the setup is discarded.

---

## 6. Future Improvement Suggestions
- **Multi-Symbol Scanner**: Modify the EA to monitor multiple pairs from a single chart.
- **News Filter Integration**: Connect to an external calendar API to pause trading during high-impact news.
- **Machine Learning Layer**: Add a module to filter signals based on historical win rates of specific wick/body ratio combinations.
- **Volatility-Based SL**: Use ATR to dynamically calculate the Stop Loss buffer.

---

## 7. Quantitative Research Mode
To use the research module:
1. Open the **Strategy Tester** in MT5.
2. Select `ShootingStar_Pro_EA.mq5`.
3. Set Optimization to "All symbols in Market Watch" or "Genetic Algorithm".
4. After the run, find the `ShootingStar_Research.csv` in the `MQL5/Files` folder. This file contains key metrics for each configuration, allowing you to rank them by robustness and consistency.

---
*Developed for professional quantitative trading.*
