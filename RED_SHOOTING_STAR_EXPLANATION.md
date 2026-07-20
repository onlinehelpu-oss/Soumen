# Red Shooting Star / Red Pinbar Reversal Strategy for BTCUSD (MQL5)

This document provides a comprehensive technical explanation of the converted **RedShootingStar_EA.mq5** Expert Advisor, designed to run on the MetaTrader 5 (MT5) platform specifically optimized for **BTCUSD**.

---

## 1. Strategy Overview

The **Red Shooting Star** strategy is a high-probability price action reversal strategy. It scans for a specific bearish pinbar pattern (or Shooting Star) at key areas of market resistance or trend boundaries, and enters a short position if the signal candle's low is broken on the immediately following candle.

### Key Rules implemented:
1. **Candle Pattern Rejection**: The signal candle must exhibit a long upper wick (clear upward price rejection), a small body, and a minimal or non-existent lower wick.
2. **Context Filters**:
   - **Regime EMA (26 period)**: Price (Close) must be below the Regime EMA, **OR** the signal candle's High must be at or extremely close to the Day High.
   - **Filter EMA (15 period)**: Optional strict configuration where the signal candle High is above the 15-EMA, Close is below the 15-EMA, previous candle is Green, and the signal candle is Red.
3. **Execution (Breakout Entry)**: A pending/virtual trigger watches the next candle. Entry occurs **only** when the current Bid price strictly crosses below the signal candle's low (minus a configurable buffer).
4. **Risk Management**: Placing a market short position with automatic Bracket Orders (Stop Loss above the signal candle high plus buffer, and Take Profit at a custom Risk-Reward ratio multiplier).

---

## 2. Parameter Configurations & Architecture

The input parameters are cleanly separated using MT5 `input group` directives for clear organization on the platform's user interface:

### 1. Strategy Parameters
- `InpTimeframe`: Allows selectable timeframes (e.g., 15-Minute chart).
- `InpRiskRewardRatio`: Standard multiplier (e.g., `1.0` for 1:1 risk/reward target, or `2.0` for 1:2).
- `InpFixedLotSize`: Fixed lot size suited for trading BTCUSD (e.g., `0.1` lots).
- `InpOnePositionAtTime`: Ensures only one position is active at any time to avoid over-exposure.

### 2. EMA Filters
- `InpRegimeEMAPeriod` (Default: `26`): Period for trend/regime detection.
- `InpUseFilterEMA` & `InpFilterEMAPeriod`: Standard 15-period filter requiring body crossover.

### 3. Candle Geometry
The geometry supports both **Classic Shooting Star** and **Flexible Upper Wick Rejection**:
- Classic parameters limit wicks and body sizes relative to the total range (`High - Low`):
  - **Upper Wick**: 50% - 80% (Clear rejection).
  - **Body**: 5% - 30% (Small to medium body).
  - **Lower Wick**: 0% - 25% (Allows small lower shadow).
- Flexible rejection allows scanning candle bodies of any color as long as they meet the rejection guidelines.

### 4. Small Candle Guard & Buffers
- `InpMinRangePct` (Default: `0.0015` or 0.15%): Filters out small, noisy candles from triggering false signals on BTCUSD.
- `InpEntryBufferPoints` & `InpSLBufferPoints`: Buffers converted to price points for accurate triggering and protection.

---

## 3. Step-by-Step Code Walkthrough

### Step 1: Initialization (`OnInit`)
- Assigns the Magic Number to the standard trade helper (`m_trade.SetExpertMagicNumber(...)`).
- Initializes indicator handles for both EMAs (`iMA`).
- Resets all internal tracking states for signal breakout triggers.

### Step 2: Candle Geometry & Pattern Detection (`CheckCandleGeometry`)
- Copies rate data for shift 1 (completed candle) and shift 2 (previous candle).
- Calculates percentages for Upper Wick, Body, and Lower Wick relative to the `total_range`.
- Assesses pattern match for **Classic** or **Flexible (Fluent)**.
- Validates context rules: verifies if the candle's close is below the 26-EMA (regime trend) OR if the high is at the Day's High (reversal at resistance).

### Step 3: Next-Candle Breakout Monitoring (`OnTick`)
- Detects the closing of a candle by checking when `iTime(...)` changes.
- Once a candle closes, `EvaluateSignals()` executes. If a pattern is confirmed, it marks the trigger window as active:
  - `m_trigger_start_time` starts at the exact open of the new candle.
  - `m_trigger_expiration` is set exactly 1 timeframe period later (ensuring strict *next-candle-only* breakout execution).
- Every subsequent price tick within that active window is evaluated.
- To prevent false entries, **Strict Cross** is applied:
  $$\text{Last Bid} \geq \text{Threshold} \quad \text{and} \quad \text{Current Bid} < \text{Threshold}$$
  Where $\text{Threshold} = \text{Signal Low} - \text{Entry Buffer}$.

### Step 4: Trade Execution (`ExecuteShortEntry`)
- Calculates Stop Loss price (`Signal High + Buffer`) and Take Profit price (`Entry Price - (RR * Risk)`).
- Normalizes order volumes according to symbol-specific parameters (`SYMBOL_VOLUME_STEP`, `SYMBOL_VOLUME_MIN`).
- Places the market short order utilizing standard `CTrade.Sell()`.

### Step 5: Session Management & Cleanup
- Features optional hour/minute cutoffs (`InpUseSessionControl`) to prevent executing trades late in the session and auto-close open positions at day-end (e.g., MCF or custom platform times).
