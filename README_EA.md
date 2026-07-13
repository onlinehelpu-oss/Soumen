# Shooting Star Pro EA - Documentation & Quantitative Research Guide

## 1. Overview
The **Shooting Star Pro EA** is a professional-grade MetaTrader 5 Expert Advisor designed for institutional-level reversal trading. It combines strict candlestick geometry (Shooting Star & Hammer) with high-frequency breakout monitoring and robust risk management.

---

## 2. Strategy Logic
### Pattern Detection
The EA evaluates every closed candle on the user-selected timeframe for:
- **Upper Wick Ratio**: Must be ≥ 70% (default) of the total range for a Shooting Star.
- **Lower Wick Ratio**: Must be ≤ 10% (default) of the total range.
- **Body Ratio**: Must be ≤ 20% (default) of the total range.
- **Color**: Configurable to require a bearish close for Sell (SS) or bullish for Buy (Hammer).

### Entry Mechanism (Tick-Level Breakout)
Once a pattern is identified, the EA stores the High/Low levels. It immediately places a trade on the **very first tick** that breaks the candle's extreme (plus buffer), ensuring the fastest possible execution compared to waiting for a candle close.

---

## 3. Installation & Setup
1. Copy `ShootingStar_Pro_EA.mq5` to your `MQL5/Experts` directory.
2. Compile the file (`F4` to open MetaEditor, then `F7`).
3. Attach to any chart. Ensure "Algo Trading" is enabled in the terminal and "Allow Algo Trading" is checked in the EA settings.
4. Set the **Signal Timeframe** in the EA inputs to your desired interval (M1 to W1).

---

## 4. Key Input Parameters
### Risk Management
- **Risk Amount ($)**: Fixed dollar amount per trade.
- **Risk Percentage (%)**: Dynamic risk based on account balance.
- **Fixed Lot Size**: Straightforward volume entry.
- **Max Daily Loss/Trades**: Automatic shutdown for the day to protect capital.

### Advanced Management
- **Auto Breakeven**: Locks in entry price at a target RR.
- **Partial TP**: Closes a percentage (e.g., 50%) of the trade at a specific RR target.
- **Trailing Stop**: Actively trails profits using a point-based distance.

---

## 5. Quantitative Research Module
The EA is designed with a "Research First" philosophy. When run in the Strategy Tester, it provides:
1. **Custom Optimization Metric**: Returns `Profit Factor / Maximum Drawdown`. This penalizes systems that achieve high profit through excessive volatility, favoring smooth equity curves.
2. **Automated Export**: Saves a detailed statistical report (`SS_Research_Report.csv`) to the `MQL5/Files` directory after every test.

### Statistical Metrics Tracked:
- Win Rate, Profit Factor, Sharpe Ratio.
- Expectancy (Expected Payoff).
- Drawdown Stability (Max DD %).
- Recovery Factor.
- Consecutive Win/Loss counts.

---

## 6. Future Improvement Suggestions
- **Dynamic ATR Stop Loss**: Instead of point-based buffers, use ATR for volatility-adjusted protection.
- **Multi-Symbol Dashboard**: A panel to monitor 28+ pairs for Shooting Stars simultaneously.
- **News Integration**: Connection to an economic calendar to pause trading 30 mins before/after High Impact events.

---
*Disclaimer: Trading involves significant risk. This EA is a tool for quantitative research and should be thoroughly tested on a demo account before live deployment.*
