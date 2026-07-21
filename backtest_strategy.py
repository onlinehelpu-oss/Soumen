# Backtester for Flexible Upper Wick Rejection strategy in Python

import pandas as pd
import numpy as np
import datetime
from typing import Dict, Any, List

def run_backtest(df: pd.DataFrame,
                 ema_period: int = 21,
                 min_upper_wick_pct: float = 50.0,
                 rr_ratio: float = 2.0,
                 min_candle_pct: float = 0.01) -> Dict[str, Any]:
    """
    Runs a detailed backtest of the Flexible Upper Wick Rejection strategy.

    Rules:
    - Signal Candle (C_sig):
        - Red candle (Close < Open)
        - Previous candle (C_prev) was Green (Close > Open)
        - C_sig High is above EMA, C_sig Close is below EMA
        - Upper Wick >= min_upper_wick_pct% of total candle range
        - Candle size/close >= min_candle_pct% (ignore tiny candles)
    - Entry:
        - During the immediate next candle (C_next), if price drops below C_sig Low, we enter SHORT.
        - Stop loss = C_sig High
        - Take profit = Entry - RR * (SL - Entry)
    """
    if len(df) < max(ema_period + 2, 50):
        raise ValueError("DataFrame has too few rows for backtesting.")

    df = df.copy()
    # Calculate EMA
    df['EMA'] = df['close'].ewm(span=ema_period, adjust=False).mean()

    # Calculate Candle fields
    df['range'] = df['high'] - df['low']
    df['body_top'] = np.maximum(df['open'], df['close'])
    df['upper_wick'] = df['high'] - df['body_top']
    df['upper_wick_pct'] = (df['upper_wick'] / df['range']) * 100.0

    trades = []
    active_trade = None

    for i in range(2, len(df)):
        # Check active trade
        current_bar = df.iloc[i]

        if active_trade is not None:
            # Check if hit SL or TP
            high_price = current_bar['high']
            low_price = current_bar['low']

            # Did it hit SL first or TP first or both?
            # We use conservative approach: if both hit in the same candle, we count as SL (loss).
            hit_sl = high_price >= active_trade['SL']
            hit_tp = low_price <= active_trade['TP']

            if hit_sl and hit_tp:
                # Conservative: loss
                active_trade['exit_price'] = active_trade['SL']
                active_trade['exit_time'] = current_bar.name
                active_trade['pnl'] = active_trade['entry_price'] - active_trade['SL']
                active_trade['status'] = 'LOSS'
                trades.append(active_trade)
                active_trade = None
            elif hit_sl:
                active_trade['exit_price'] = active_trade['SL']
                active_trade['exit_time'] = current_bar.name
                active_trade['pnl'] = active_trade['entry_price'] - active_trade['SL']
                active_trade['status'] = 'LOSS'
                trades.append(active_trade)
                active_trade = None
            elif hit_tp:
                active_trade['exit_price'] = active_trade['TP']
                active_trade['exit_time'] = current_bar.name
                active_trade['pnl'] = active_trade['entry_price'] - active_trade['TP']
                active_trade['status'] = 'WIN'
                trades.append(active_trade)
                active_trade = None
            continue

        # Check for Signal Candle on previous bar (index i-1)
        prev_bar = df.iloc[i-2]
        sig_bar = df.iloc[i-1]

        # 1. Previous candle was Green
        cond1 = prev_bar['close'] > prev_bar['open']
        # 2. Signal candle is Red
        cond2 = sig_bar['close'] < sig_bar['open']
        # 3. EMA condition: Signal candle High above EMA, Close below EMA
        cond3 = (sig_bar['high'] > sig_bar['EMA']) and (sig_bar['close'] < sig_bar['EMA'])
        # 4. Upper Wick condition
        cond4 = sig_bar['upper_wick_pct'] >= min_upper_wick_pct
        # 5. Tiny candle check
        cond5 = sig_bar['range'] > 0 and (sig_bar['range'] / sig_bar['close']) * 100.0 >= min_candle_pct

        if cond1 and cond2 and cond3 and cond4 and cond5:
            # Signal candle detected!
            # Entry condition: if next immediate candle (current bar `i`) breaks low of signal candle
            if current_bar['low'] < sig_bar['low']:
                # Entry triggered!
                entry_price = sig_bar['low']
                sl = sig_bar['high']
                tp = entry_price - rr_ratio * (sl - entry_price)

                active_trade = {
                    'entry_time': current_bar.name,
                    'entry_price': entry_price,
                    'SL': sl,
                    'TP': tp,
                    'signal_time': sig_bar.name,
                    'signal_high': sig_bar['high'],
                    'signal_low': sig_bar['low']
                }

                # Check if this entry candle itself hits SL or TP
                high_price = current_bar['high']
                low_price = current_bar['low']
                hit_sl = high_price >= sl
                hit_tp = low_price <= tp

                if hit_sl and hit_tp:
                    active_trade['exit_price'] = sl
                    active_trade['exit_time'] = current_bar.name
                    active_trade['pnl'] = entry_price - sl
                    active_trade['status'] = 'LOSS'
                    trades.append(active_trade)
                    active_trade = None
                elif hit_sl:
                    active_trade['exit_price'] = sl
                    active_trade['exit_time'] = current_bar.name
                    active_trade['pnl'] = entry_price - sl
                    active_trade['status'] = 'LOSS'
                    trades.append(active_trade)
                    active_trade = None
                elif hit_tp:
                    active_trade['exit_price'] = tp
                    active_trade['exit_time'] = current_bar.name
                    active_trade['pnl'] = entry_price - tp
                    active_trade['status'] = 'WIN'
                    trades.append(active_trade)
                    active_trade = None

    # Calculate statistics
    trade_df = pd.DataFrame(trades)
    if trade_df.empty:
        return {
            'total_trades': 0,
            'wins': 0,
            'losses': 0,
            'win_rate': 0.0,
            'total_pnl': 0.0,
            'profit_factor': 0.0,
            'trades': []
        }

    total_trades = len(trade_df)
    wins = len(trade_df[trade_df['status'] == 'WIN'])
    losses = len(trade_df[trade_df['status'] == 'LOSS'])
    win_rate = (wins / total_trades) * 100.0 if total_trades > 0 else 0.0

    total_pnl = trade_df['pnl'].sum()
    gross_profits = trade_df[trade_df['pnl'] > 0]['pnl'].sum()
    gross_losses = abs(trade_df[trade_df['pnl'] < 0]['pnl'].sum())

    profit_factor = gross_profits / gross_losses if gross_losses > 0 else float('inf')

    return {
        'total_trades': total_trades,
        'wins': wins,
        'losses': losses,
        'win_rate': win_rate,
        'total_pnl': total_pnl,
        'profit_factor': profit_factor,
        'trades': trades
    }

if __name__ == "__main__":
    # Create sample synthetic historical data for verification
    np.random.seed(42)
    dates = pd.date_range(start="2023-01-01", periods=500, freq="h")
    close = 100.0 + np.cumsum(np.random.normal(0, 0.5, 500))
    open_p = close - np.random.normal(0, 0.2, 500)
    high = np.maximum(open_p, close) + np.abs(np.random.normal(0.5, 0.2, 500))
    low = np.minimum(open_p, close) - np.abs(np.random.normal(0.1, 0.1, 500))

    df = pd.DataFrame({
        'open': open_p,
        'high': high,
        'low': low,
        'close': close
    }, index=dates)

    results = run_backtest(df)
    print("=== Backtest Sample Verification ===")
    print(f"Total Trades: {results['total_trades']}")
    print(f"Win Rate: {results['win_rate']:.2f}%")
    print(f"Profit Factor: {results['profit_factor']:.2f}")
    print(f"Total PnL Points: {results['total_pnl']:.2f}")
