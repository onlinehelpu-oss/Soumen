"""
ML MODEL BACKTESTER FOR FYERS OPTIONS BOT
=========================================

**Purpose:**
To download real historical data, train a machine learning model,
and backtest its performance on unseen data to validate its effectiveness
before deploying it in the live bot.
"""

import os
import json
from datetime import datetime as dt, timedelta
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
import joblib
import numpy as np

# Fyers API library - Assuming fyers_login_details.json and token exist
from fyers_apiv3 import fyersModel

# --- Configuration ---
SYMBOL = "NSE:NIFTY50-INDEX"
TIME_FRAME = "1" # 1-minute candles
DAYS_OF_DATA_TO_DOWNLOAD = 60
DAYS_FOR_TRAINING = 45 # The rest will be used for testing

MODEL_FILENAME = "real_options_model.joblib"
LOGIN_DETAILS_FILE = "fyers_login_details.json"

# ============================================================================
# 1. FYERS DATA DOWNLOADER
# ============================================================================

def get_fyers_instance():
    """Creates an authenticated FyersModel instance."""
    try:
        with open(LOGIN_DETAILS_FILE, 'r') as f:
            details = json.load(f)
        client_id = details["client_id"]
        token_file = f"fyers_token_{client_id}.json"

        with open(token_file, 'r') as f:
            access_token = json.load(f)

        return fyersModel.FyersModel(client_id=client_id, is_async=False, token=access_token, log_path="")
    except FileNotFoundError as e:
        print(f"ERROR: Could not find login/token file: {e}")
        print("Please run the live_options_bot.py once to generate the token.")
        exit()

def download_historical_data(fyers: fyersModel.FyersModel):
    """Downloads historical data for the specified symbol."""
    print(f"Downloading {DAYS_OF_DATA_TO_DOWNLOAD} days of data for {SYMBOL}...")

    to_date = dt.now().date()
    from_date = to_date - timedelta(days=DAYS_OF_DATA_TO_DOWNLOAD)

    data = {
        "symbol": SYMBOL,
        "resolution": TIME_FRAME,
        "date_format": "1",
        "range_from": from_date.strftime("%Y-%m-%d"),
        "range_to": to_date.strftime("%Y-%m-%d"),
        "cont_flag": "1"
    }

    try:
        response = fyers.history(data)
        if response.get("candles"):
            df = pd.DataFrame(response["candles"])
            df.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
            df.set_index('timestamp', inplace=True)
            print(f"Successfully downloaded {len(df)} candles.")
            return df
        else:
            print(f"ERROR: No candle data in response: {response}")
            return pd.DataFrame()
    except Exception as e:
        print(f"ERROR during data download: {e}")
        return pd.DataFrame()

# ============================================================================
# 2. FEATURE ENGINEERING
# ============================================================================

def create_features(df: pd.DataFrame) -> pd.DataFrame:
    """Creates features from the OHLCV data."""
    print("Engineering features...")
    df['returns'] = df['close'].pct_change()

    # Standard Indicators
    df['rsi'] = 100 - (100 / (1 + (df['returns'].rolling(window=14).apply(lambda x: x[x>0].mean()) / -df['returns'].rolling(window=14).apply(lambda x: x[x<0].mean()))))
    df['ema_short'] = df['close'].ewm(span=12, adjust=False).mean()
    df['ema_long'] = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = df['ema_short'] - df['ema_long']
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()

    # Target: 1 for up, -1 for down, 0 for hold
    lookahead_period = 5 # Predict 5 minutes ahead
    price_change = df['close'].shift(-lookahead_period) - df['close']
    df['target'] = np.sign(price_change) # Simple direction

    return df.dropna()

# ============================================================================
# 3. MODEL TRAINING & SAVING
# ============================================================================

def train_and_save_model(features_df: pd.DataFrame):
    """Trains the model on the training portion of the data."""
    print("\n--- Model Training ---")

    # Split data into training and testing sets by percentage
    train_size = int(len(features_df) * 0.7)
    train_data = features_df.iloc[:train_size]

    if train_data.empty:
        print("ERROR: Not enough data for the training period.")
        return

    X = train_data.drop(columns=['target', 'open', 'high', 'low', 'close', 'volume'])
    y = train_data['target']

    print(f"Training on {len(X)} data points...")

    model = RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=-1, class_weight='balanced')
    model.fit(X, y)

    joblib.dump(model, MODEL_FILENAME)
    print(f"Model trained and saved to '{MODEL_FILENAME}'")

# ============================================================================
# 4. BACKTESTING ENGINE
# ============================================================================

def run_backtest(features_df: pd.DataFrame):
    """Runs a backtest on the unseen test data."""
    print("\n--- Backtesting ---")

    model = joblib.load(MODEL_FILENAME)

    train_size = int(len(features_df) * 0.7)
    test_data = features_df.iloc[train_size:]

    if test_data.empty:
        print("ERROR: Not enough data for the testing period.")
        return

    X_test = test_data.drop(columns=['target', 'open', 'high', 'low', 'close', 'volume'])

    predictions = model.predict(X_test)

    # --- Simple Backtest Logic ---
    balance = 100000
    pnl = 0
    trades = []
    position = 0 # 1 for long (CE), -1 for short (PE), 0 for none

    for i in range(len(test_data) - 1):
        signal = predictions[i]

        # Entry
        if position == 0 and signal != 0:
            position = signal
            entry_price = test_data['close'].iloc[i]

        # Exit
        elif position != 0 and signal != position:
            exit_price = test_data['close'].iloc[i]
            trade_pnl = (exit_price - entry_price) * position # Profit if price moves with position
            pnl += trade_pnl
            trades.append(trade_pnl)
            position = 0

    print("Backtest complete.")
    analyze_performance(pnl, trades, balance)

# ============================================================================
# 5. PERFORMANCE ANALYSIS
# ============================================================================

def analyze_performance(total_pnl: float, trades: list, initial_balance: float):
    """Analyzes and prints the backtest performance."""
    print("\n--- Performance Analysis ---")

    if not trades:
        print("No trades were made during the backtest.")
        return

    wins = sum(1 for t in trades if t > 0)
    losses = len(trades) - wins
    win_rate = (wins / len(trades) * 100) if trades else 0

    print(f"Total Trades: {len(trades)}")
    print(f"Win Rate: {win_rate:.2f}%")
    print(f"Total P&L: ₹{total_pnl:,.2f}")
    print(f"Return on Initial Capital: {(total_pnl / initial_balance * 100):.2f}%")

# ============================================================================
# MAIN
# ============================================================================

def main():
    """Main function to run the backtester."""
    import argparse
    parser = argparse.ArgumentParser(description="ML Model Backtester")
    parser.add_argument(
        "--mode",
        type=str,
        default="live",
        choices=["live", "test"],
        help="Set the mode to 'live' for real data or 'test' for sample CSV data."
    )
    args = parser.parse_args()

    print("="*50)
    print(f"ML Model Backtester Initializing (Mode: {args.mode.upper()})")
    print("="*50)

    historical_df = pd.DataFrame()

    if args.mode == "live":
        fyers = get_fyers_instance()
        historical_df = download_historical_data(fyers)
    else: # test mode
        try:
            print("Loading data from 'sample_nifty_data.csv'...")
            historical_df = pd.read_csv('sample_nifty_data.csv', index_col='timestamp', parse_dates=True)
            print(f"Successfully loaded {len(historical_df)} records.")
        except FileNotFoundError:
            print("ERROR: sample_nifty_data.csv not found. Please ensure the file exists.")
            return

    if not historical_df.empty:
        features_df = create_features(historical_df)

        if len(features_df) < 50: # Arbitrary threshold for minimum data
            print("ERROR: Not enough data remaining after feature engineering to run a meaningful backtest.")
            print(f"Required at least ~50 data points, but only have {len(features_df)}.")
            return

        train_and_save_model(features_df)
        run_backtest(features_df)

    print("\n" + "="*50)
    print("Backtester Finished")
    print("="*50)

if __name__ == "__main__":
    main()
