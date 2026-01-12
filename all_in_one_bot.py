"""
ALL-IN-ONE ML OPTIONS TRADING BOT
=================================

**Disclaimer:**
This bot is intended for educational and paper trading purposes.
Live trading involves significant financial risk. Use at your own discretion.

**Description:**
This single-file script consolidates three key functionalities:
1. Credential Setup: Securely creates your API configuration file.
2. Backtester: Downloads real market data, trains an ML model, and tests its performance.
3. Live Bot: Runs the ML model in a live (paper trading) environment.

**Usage:**
1. Setup Credentials (Run Once):
   python all_in_one_bot.py setup --client_id YOUR_ID --secret_key YOUR_KEY --redirect_url YOUR_URL

2. Train & Backtest the Model:
   python all_in_one_bot.py backtest

3. Run the Live Paper Trading Bot:
   python all_in_one_bot.py run
"""

import os
import sys
import json
import time
import argparse
import webbrowser
import hashlib
import requests
import warnings
from urllib.parse import urlparse, parse_qs, quote
from datetime import datetime as dt, timedelta
from typing import Optional

# Suppress specific warnings
warnings.filterwarnings("ignore", message=".*pkg_resources is deprecated.*")
warnings.filterwarnings("ignore", category=UserWarning, module="xgboost")

import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
import joblib
import xgboost as xgb

# Fyers API library
from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws


# ============================================================================
# --- SECTION 1: CONFIGURATION ---
# ============================================================================

class BotConfig:
    """Consolidated configuration for all bot functionalities."""
    # --- File Names ---
    LOGIN_DETAILS_FILE = "fyers_login_details.json"
    MODEL_FILENAME = "real_options_model.joblib"

    # --- Backtester ---
    SYMBOL = "NSE:NIFTY50-INDEX"
    TIME_FRAME = "1"  # 1-minute candles
    DAYS_OF_DATA_TO_DOWNLOAD = 60
    TRAIN_TEST_SPLIT_RATIO = 0.7
    BACKTEST_STOP_LOSS_PCT = 0.2  # Initial stop loss
    BACKTEST_TRAILING_STOP_LOSS_PCT = 0.2 # Trail the stop 0.2% behind the peak price


    # --- Live Bot ---
    STRIKE_DISTANCE = 0  # 0 for ATM
    STOP_LOSS_PCT = 15.0  # % on option premium
    TAKE_PROFIT_PCT = 30.0 # % on option premium
    PAPER_BALANCE = 100000

    # --- Session Timing ---
    SESSION_START_TIME = dt.now().replace(hour=9, minute=15, second=0, microsecond=0)
    SESSION_END_TIME = dt.now().replace(hour=15, minute=30, second=0, microsecond=0)


# ============================================================================
# --- SECTION 2: CREDENTIAL SETUP LOGIC ---
# ============================================================================

def setup_credentials(app_id, secret_key, redirect_url):
    """Creates the fyers_login_details.json file."""
    if not all([app_id, secret_key, redirect_url]):
        print("ERROR: App ID, Secret Key, and Redirect URL are all required.")
        return

    credentials = {
        "client_id": app_id,  # The library expects this key, but it's the App ID
        "secret_key": secret_key,
        "redirect_url": redirect_url
    }

    file_name = BotConfig.LOGIN_DETAILS_FILE
    try:
        with open(file_name, 'w') as f:
            json.dump(credentials, f, indent=2)

        print(f"✅ Successfully created '{file_name}' with your credentials.")
        print("You are now ready to run the backtester or the live bot.")
    except Exception as e:
        print(f"❌ An error occurred while creating the file: {e}")


# ============================================================================
# --- SECTION 3: SHARED AUTHENTICATION & BACKTESTER LOGIC ---
# ============================================================================

# --- New Authentication Logic Merged from Login Script ---

def build_auth_url(app_id, redirect_uri, state="sample_state"):
    # v3 auth is served from api-t1
    base = "https://api-t1.fyers.in/api/v3/generate-authcode"
    # scope/nonce are optional but harmless; URL-encode redirect
    params = (
        f"client_id={quote(app_id)}"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        f"&response_type=code"
        f"&state={quote(state)}"
        f"&scope=openid"
        f"&nonce={int(time.time())}"
    )
    return f"{base}?{params}"

def extract_code(user_input):
    """Accept either a raw code or a full redirect URL with ?code=..."""
    if user_input.startswith("http://") or user_input.startswith("https://"):
        q = parse_qs(urlparse(user_input).query)
        code = q.get("code", [None])[0]
        if not code:
            raise ValueError("No 'code' param found in the provided URL.")
        return code
    return user_input  # assume raw code

def sha256_appIdHash(app_id, secret_id):
    return hashlib.sha256(f"{app_id}:{secret_id}".encode("utf-8")).hexdigest()

def validate_authcode(app_id, secret_id, auth_code, max_retries=5):
    url = "https://api-t1.fyers.in/api/v3/validate-authcode"
    payload = {
        "grant_type": "authorization_code",
        "appIdHash": sha256_appIdHash(app_id, secret_id),
        "code": auth_code,
    }
    headers = {"Content-Type": "application/json"}

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=20)
            if r.status_code == 503:
                # Service temporarily unavailable -> backoff and retry
                sleep_s = min(2 ** attempt, 30)
                print(f"[{attempt}/{max_retries}] 503 from auth server. Retrying in {sleep_s}s...")
                time.sleep(sleep_s)
                continue
            r.raise_for_status()
            data = r.json()
            if data.get("s") == "error":
                # Bubble up API error messages for clarity
                raise RuntimeError(f"Fyers error {data.get('code')}: {data.get('message')}")
            return data  # expected to include access_token, refresh_token, etc.
        except requests.RequestException as e:
            if attempt == max_retries:
                raise
            sleep_s = min(2 ** attempt, 30)
            print(f"[{attempt}/{max_retries}] Network error: {e}. Retrying in {sleep_s}s...")
            time.sleep(sleep_s)

# --- End New Authentication Logic ---

def get_fyers_instance():
    """
    Creates an authenticated FyersModel instance. This function is shared
    by both the backtester and the live bot.
    """
    try:
        with open(BotConfig.LOGIN_DETAILS_FILE, 'r') as f:
            details = json.load(f)
        client_id, secret_key, redirect_url = details["client_id"], details["secret_key"], details["redirect_url"]
    except (FileNotFoundError, KeyError) as e:
        print(f"ERROR: Credential file is missing or invalid: {e}.")
        print("Please run 'setup' mode to create the file correctly.")
        sys.exit(1)

    # Use standard AccessToken directory structure for compatibility with other bots
    tokens_dir = "AccessToken"
    today_str = str(dt.now().date())
    token_file = os.path.join(tokens_dir, f"{today_str}.json")

    # Try to load existing token
    access_token = None
    if os.path.exists(token_file):
        with open(token_file, 'r') as f:
            access_token = json.load(f)

    # If no token, or token is invalid, generate a new one
    if not access_token:
        print("No valid token found. Starting new login process...")

        # --- REPLACED: Old SessionModel logic with new manual auth flow ---
        auth_url = build_auth_url(client_id, redirect_url)
        webbrowser.open(auth_url, new=1)
        print("\nLogin URL (opened in browser):")
        print(auth_url)

        user_val = input("\nPaste the FULL redirect URL after login, or just the 'code=' value here: ").strip()
        try:
            auth_code = extract_code(user_val)
            token_resp = validate_authcode(client_id, secret_key, auth_code)

            if token_resp.get("access_token"):
                access_token = token_resp["access_token"]
                os.makedirs(tokens_dir, exist_ok=True)
                with open(token_file, 'w') as f: json.dump(access_token, f)
            else:
                print(f"ERROR: Failed to generate token: {token_resp}")
                sys.exit(1)
        except Exception as e:
            print(f"ERROR during login: {e}")
            sys.exit(1)
        # --- END REPLACEMENT ---

    fyers = fyersModel.FyersModel(client_id=client_id, is_async=False, token=access_token, log_path="")

    # Verify by fetching profile
    try:
        profile = fyers.get_profile()
        if profile.get('data'):
            print(f"Authentication successful! Welcome, {profile['data']['name']}.")
        else:
            if os.path.exists(token_file): os.remove(token_file)
            print("Authentication failed. Removed invalid token file. Please restart.")
            sys.exit(1)
    except Exception as e:
        print(f"Error during profile fetch: {e}")
        sys.exit(1)

    return fyers, client_id, access_token

def download_historical_data(fyers: fyersModel.FyersModel):
    """Downloads historical data for the specified symbol."""
    print(f"Downloading {BotConfig.DAYS_OF_DATA_TO_DOWNLOAD} days of data for {BotConfig.SYMBOL}...")
    to_date = dt.now().date()
    from_date = to_date - timedelta(days=BotConfig.DAYS_OF_DATA_TO_DOWNLOAD)

    data = {
        "symbol": BotConfig.SYMBOL,
        "resolution": BotConfig.TIME_FRAME,
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

def create_backtest_features(df: pd.DataFrame) -> pd.DataFrame:
    """Creates advanced features from the OHLCV data for backtesting."""
    print("Engineering advanced features for backtest...")

    # Basic returns
    df['returns'] = df['close'].pct_change()

    # 1. Standard Indicators
    df['rsi'] = 100 - (100 / (1 + (df['returns'].rolling(window=14).apply(lambda x: x[x>0].mean()) / -df['returns'].rolling(window=14).apply(lambda x: x[x<0].mean()))))
    df['ema_short'] = df['close'].ewm(span=12, adjust=False).mean()
    df['ema_long'] = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = df['ema_short'] - df['ema_long']
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()

    # 2. Volatility Features (Bollinger Bands)
    window = 20
    df['bollinger_mid'] = df['close'].rolling(window=window).mean()
    df['bollinger_std'] = df['close'].rolling(window=window).std()
    df['bollinger_upper'] = df['bollinger_mid'] + (df['bollinger_std'] * 2)
    df['bollinger_lower'] = df['bollinger_mid'] - (df['bollinger_std'] * 2)
    df['bollinger_width'] = df['bollinger_upper'] - df['bollinger_lower']

    # 3. Time-Based Features
    df['hour'] = df.index.hour
    df['minute'] = df.index.minute

    # 4. Lag Features (Momentum)
    for lag in [1, 2, 3, 5]:
        df[f'rsi_lag_{lag}'] = df['rsi'].shift(lag)
        df[f'macd_lag_{lag}'] = df['macd'].shift(lag)

    # --- Target ---
    lookahead_period = 5
    price_change = df['close'].shift(-lookahead_period) - df['close']
    df['target'] = np.sign(price_change)

    return df.dropna()

def train_and_save_model(features_df: pd.DataFrame):
    """Trains the model on the training portion of the data."""
    print("\n--- Model Training ---")
    train_size = int(len(features_df) * BotConfig.TRAIN_TEST_SPLIT_RATIO)
    train_data = features_df.iloc[:train_size]

    if train_data.empty:
        print("ERROR: Not enough data for the training period.")
        return

    X = train_data.drop(columns=['target', 'open', 'high', 'low', 'close', 'volume'])
    y = train_data['target']

    # Remap labels for XGBoost: [-1, 0, 1] -> [0, 1, 2]
    y_mapped = y.map({-1: 0, 0: 1, 1: 2})

    print(f"Training on {len(X)} data points...")
    # Removed deprecated use_label_encoder parameter
    model = xgb.XGBClassifier(n_estimators=100, random_state=42, n_jobs=-1, eval_metric='logloss')
    model.fit(X, y_mapped)

    joblib.dump(model, BotConfig.MODEL_FILENAME)
    print(f"Model trained and saved to '{BotConfig.MODEL_FILENAME}'")

def run_backtest_simulation(features_df: pd.DataFrame):
    """Runs a backtest on the unseen test data with a Trailing Stop-Loss."""
    print("\n--- Backtesting with Trailing Stop-Loss ---")
    model = joblib.load(BotConfig.MODEL_FILENAME)

    train_size = int(len(features_df) * BotConfig.TRAIN_TEST_SPLIT_RATIO)
    test_data = features_df.iloc[train_size:].copy()

    if test_data.empty: return

    X_test = test_data.drop(columns=['target', 'open', 'high', 'low', 'close', 'volume'])

    # Get predictions and remap them back: [0, 1, 2] -> [-1, 0, 1]
    predictions_mapped = model.predict(X_test)
    prediction_remap = {0: -1, 1: 0, 2: 1}
    test_data['prediction'] = [prediction_remap[p] for p in predictions_mapped]

    trades = []
    position, entry_price, stop_loss, peak_price = 0, 0.0, 0.0, 0.0

    for i in range(len(test_data)):
        current_price = test_data['close'].iloc[i]
        signal = test_data['prediction'].iloc[i]

        # --- Position Monitoring & Trailing Stop ---
        if position != 0:
            exit_reason = None
            if position == 1: # Long
                peak_price = max(peak_price, current_price)
                new_stop_loss = peak_price * (1 - BotConfig.BACKTEST_TRAILING_STOP_LOSS_PCT / 100)
                stop_loss = max(stop_loss, new_stop_loss)
                if current_price <= stop_loss: exit_reason = "TRAIL_SL"

            elif position == -1: # Short
                peak_price = min(peak_price, current_price)
                new_stop_loss = peak_price * (1 + BotConfig.BACKTEST_TRAILING_STOP_LOSS_PCT / 100)
                stop_loss = min(stop_loss, new_stop_loss)
                if current_price >= stop_loss: exit_reason = "TRAIL_SL"

            if not exit_reason and signal != position and signal != 0:
                exit_reason = "SIGNAL_EXIT"

            if exit_reason:
                trades.append((current_price - entry_price) * position)
                position = 0

        # --- Entry Logic ---
        if position == 0 and signal != 0:
            position = signal
            entry_price = current_price
            peak_price = entry_price
            if position == 1: # Long
                stop_loss = entry_price * (1 - BotConfig.BACKTEST_STOP_LOSS_PCT / 100)
            else: # Short
                stop_loss = entry_price * (1 + BotConfig.BACKTEST_STOP_LOSS_PCT / 100)

    total_pnl = sum(trades)
    print("Backtest complete.")
    analyze_performance(total_pnl, trades, BotConfig.PAPER_BALANCE)

def analyze_performance(total_pnl: float, trades: list, initial_balance: float):
    """Analyzes and prints the backtest performance."""
    print("\n--- Performance Analysis ---")
    if not trades:
        print("No trades were made during the backtest.")
        return

    wins = sum(1 for t in trades if t > 0)
    losses = len(trades) - wins
    win_rate = (wins / len(trades) * 100) if trades else 0

    avg_win = np.mean([t for t in trades if t > 0]) if wins > 0 else 0
    avg_loss = np.mean([t for t in trades if t <= 0]) if losses > 0 else 0

    print(f"Total Trades: {len(trades)}")
    print(f"Win Rate: {win_rate:.2f}%")
    print(f"Average Win: ₹{avg_win:,.2f}")
    print(f"Average Loss: ₹{avg_loss:,.2f}")
    print(f"Total P&L: ₹{total_pnl:,.2f}")
    print(f"Return on Initial Capital: {(total_pnl / initial_balance * 100):.2f}%")

def run_backtester():
    """Main function to run the full backtesting process."""
    fyers, _, _ = get_fyers_instance()
    historical_df = download_historical_data(fyers)

    if not historical_df.empty:
        features_df = create_backtest_features(historical_df)
        if len(features_df) < 50:
            print("ERROR: Not enough data remaining after feature engineering.")
            return
        train_and_save_model(features_df)
        run_backtest_simulation(features_df)


# ============================================================================
# --- SECTION 4: LIVE BOT LOGIC ---
# ============================================================================

class FyersService:
    """Handles all communication with the Fyers API for the live bot."""
    def __init__(self, config: BotConfig):
        self.config = config
        self.fyers, self.client_id, self.access_token = get_fyers_instance()
        self.underlying_ltp = 0

    def connect_to_websocket(self):
        """Initializes and connects to the Fyers Data WebSocket with detailed logging."""
        ws_access_token = f"{self.client_id}:{self.access_token}"

        # Define the callbacks with detailed logging
        def on_connect():
            print("✅ WebSocket Connected!")
            print("Subscribing to symbols:", [self.config.SYMBOL])
            fyers_ws.subscribe(symbols=[self.config.SYMBOL])

        def on_close():
            print("❌ WebSocket Closed.")

        def on_error(message):
            print(" WebSocket Error:", message)

        def on_message(message):
            """Callback function to handle incoming ticks."""
            print("  [WS MSG]:", message) # Log every message
            if 'ltp' in message:
                self.underlying_ltp = message['ltp']
            elif isinstance(message, list) and 'ltp' in message[0]:
                self.underlying_ltp = message[0]['ltp']

        # Initialize the WebSocket with the new callbacks
        fyers_ws = data_ws.FyersDataSocket(
            access_token=ws_access_token,
            log_path="",
            on_connect=on_connect,
            on_close=on_close,
            on_error=on_error,
            on_message=on_message
        )

        print("🔌 Attempting to connect to WebSocket...")
        fyers_ws.connect()

def create_live_features(price_history: list) -> pd.DataFrame:
    """Creates advanced features from a list of recent prices for the live bot."""
    df = pd.DataFrame({'close': price_history})
    df.index = pd.to_datetime(df.index) # Ensure index is datetime

    # Basic returns
    df['returns'] = df['close'].pct_change()

    # 1. Standard Indicators
    df['rsi'] = 100 - (100 / (1 + (df['returns'].rolling(window=14).apply(lambda x: x[x>0].mean()) / -df['returns'].rolling(window=14).apply(lambda x: x[x<0].mean()))))
    df['ema_short'] = df['close'].ewm(span=12, adjust=False).mean()
    df['ema_long'] = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = df['ema_short'] - df['ema_long']
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()

    # 2. Volatility Features (Bollinger Bands)
    window = 20
    df['bollinger_mid'] = df['close'].rolling(window=window).mean()
    df['bollinger_std'] = df['close'].rolling(window=window).std()
    df['bollinger_upper'] = df['bollinger_mid'] + (df['bollinger_std'] * 2)
    df['bollinger_lower'] = df['bollinger_mid'] - (df['bollinger_std'] * 2)
    df['bollinger_width'] = df['bollinger_upper'] - df['bollinger_lower']

    # 3. Time-Based Features
    now = dt.now()
    df['hour'] = now.hour
    df['minute'] = now.minute

    # 4. Lag Features (Momentum)
    for lag in [1, 2, 3, 5]:
        df[f'rsi_lag_{lag}'] = df['rsi'].shift(lag)
        df[f'macd_lag_{lag}'] = df['macd'].shift(lag)

    return df.drop(columns=['close']).dropna()

class MLStrategy:
    """The ML-powered strategy that uses the backtested model for live trading."""
    def __init__(self, config: BotConfig):
        self.config = config
        self.model = joblib.load(self.config.MODEL_FILENAME)

    def generate_signal(self, price_history: list) -> str:
        if len(price_history) < 30: return "HOLD"
        features = create_live_features(price_history)
        if features.empty: return "HOLD"

        # Get prediction and remap it back: [0, 1, 2] -> [-1, 0, 1]
        prediction_mapped = self.model.predict(features.tail(1))[0]
        prediction_remap = {0: -1, 1: 0, 2: 1}
        prediction = prediction_remap[prediction_mapped]

        if prediction == 1: return "BUY_CE"
        elif prediction == -1: return "BUY_PE"
        return "HOLD"

class PaperPosition:
    """Represents a single simulated position."""
    def __init__(self, symbol: str, entry_price: float, stop_loss: float, take_profit: float):
        self.symbol, self.entry_price, self.stop_loss, self.take_profit = symbol, entry_price, stop_loss, take_profit
        self.current_price, self.pnl = entry_price, 0.0

    def update_pnl(self, current_price: float):
        self.current_price = current_price
        self.pnl = (self.current_price - self.entry_price)

class LiveOptionsBot:
    """The main class for the live options trading bot."""
    def __init__(self, config: BotConfig):
        self.config = config
        self.fyers_service = FyersService(config)
        self.strategy = MLStrategy(config)
        self.paper_balance = self.config.PAPER_BALANCE
        self.paper_trades = []
        self.active_position: Optional[PaperPosition] = None
        self.price_history = []

    def run(self):
        print(f"🚀 STARTING LIVE BOT (PAPER TRADING) | Balance: ₹{self.paper_balance:,.2f}")
        self.fyers_service.connect_to_websocket()
        time.sleep(5)
        last_underlying_price = 0
        while dt.now() < self.config.SESSION_END_TIME:
            if dt.now() < self.config.SESSION_START_TIME:
                print(f"\rWaiting for market open...", end="")
                time.sleep(30)
                continue

            current_price = self.fyers_service.underlying_ltp
            if current_price == 0:
                print("\rWaiting for LTP...", end="")
                time.sleep(5)
                continue

            self.price_history.append(current_price)
            if len(self.price_history) > 200: self.price_history.pop(0)

            print(f"[{dt.now().strftime('%H:%M:%S')}] NIFTY: {current_price} | Balance: ₹{self.paper_balance:,.2f}")

            if self.active_position: self._monitor_position(current_price, last_underlying_price)
            else: self._check_for_entry_signal(current_price)

            last_underlying_price = current_price
            time.sleep(15)

        self._show_paper_summary()

    def _open_paper_position(self, price: float, opt_type: str):
        entry_premium = 100.0
        sl = entry_premium * (1 - self.config.STOP_LOSS_PCT / 100)
        tp = entry_premium * (1 + self.config.TAKE_PROFIT_PCT / 100)
        strike = int(round(price / 50) * 50)
        symbol = f"NIFTY_DEMO_{strike}_{opt_type}"
        self.active_position = PaperPosition(symbol, entry_premium, sl, tp)
        print(f"  ✅ PAPER TRADE: Opened {symbol} @ ₹{entry_premium:.2f} | SL: ₹{sl:.2f}, TP: ₹{tp:.2f}")

    def _monitor_position(self, price: float, last_price: float):
        pos = self.active_position
        if not pos or last_price == 0: return

        price_change = (price - last_price)
        opt_type = pos.symbol.split('_')[-1]
        premium_change = (price_change * 0.5) if opt_type == 'CE' else (-price_change * 0.5)
        pos.update_pnl(pos.current_price + premium_change)

        print(f"  HOLDING: {pos.symbol} | Entry: {pos.entry_price:.2f} | Now: {pos.current_price:.2f} | P&L: {pos.pnl:+.2f}")
        if pos.current_price <= pos.stop_loss: self._close_paper_position("STOP LOSS")
        elif pos.current_price >= pos.take_profit: self._close_paper_position("TAKE PROFIT")

    def _close_paper_position(self, reason: str):
        pos = self.active_position
        if not pos: return
        self.paper_balance += pos.pnl
        self.paper_trades.append({"symbol": pos.symbol, "pnl": pos.pnl, "reason": reason})
        print(f"  ❌ PAPER TRADE: Closed {pos.symbol} for {reason} | P&L: ₹{pos.pnl:+.2f}")
        self.active_position = None

    def _check_for_entry_signal(self, price: float):
        signal = self.strategy.generate_signal(self.price_history)
        print(f"  ML Signal: {signal}")
        if signal == "BUY_CE": self._open_paper_position(price, "CE")
        elif signal == "BUY_PE": self._open_paper_position(price, "PE")

    def _show_paper_summary(self):
        print("\n--- Paper Trading Summary ---")
        if not self.paper_trades:
            print("No trades were executed.")
            return
        total_pnl = sum(t['pnl'] for t in self.paper_trades)
        wins = sum(1 for t in self.paper_trades if t['pnl'] > 0)
        print(f"Total Trades: {len(self.paper_trades)}")
        print(f"Win Rate: {(wins / len(self.paper_trades) * 100):.2f}%")
        print(f"Total P&L: ₹{total_pnl:,.2f}")
        print(f"Final Balance: ₹{self.paper_balance:,.2f}")

def run_live_bot():
    """Initializes and runs the live options bot."""
    bot = LiveOptionsBot(BotConfig())
    bot.run()


# ============================================================================
# --- SECTION 5: MAIN EXECUTION ---
# ============================================================================

def main():
    """Main function to drive the bot's functionality."""
    parser = argparse.ArgumentParser(
        description="All-In-One ML Options Trading Bot",
        formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument(
        "mode",
        nargs='?',
        choices=["setup", "backtest", "run"],
        help="Optional: The mode to run the script in (setup, backtest, run)."
    )

    # Arguments specifically for 'setup' mode, using user-friendly names
    parser.add_argument("--app_id", help="Your Fyers Application ID.")
    parser.add_argument("--secret_key", help="Your Fyers API Secret Key.")
    parser.add_argument("--redirect_url", help="Your Fyers API Redirect URL.")

    args = parser.parse_args()

    mode = args.mode

    if not mode:
        print("\n--- Welcome to the All-In-One Trading Bot ---")
        print("Please choose an option:")
        print("  1. Setup Credentials")
        print("  2. Run Backtester")
        print("  3. Run Live Paper Trading Bot")
        choice = input("Enter your choice (1-3): ")

        if choice == '1': mode = 'setup'
        elif choice == '2': mode = 'backtest'
        elif choice == '3': mode = 'run'
        else:
            print("Invalid choice. Exiting.")
            return

    print(f"\n--- Running in {mode.upper()} mode ---")

    if mode == "setup":
        app_id = args.app_id or input("Enter your Fyers App ID: ")
        secret_key = args.secret_key or input("Enter Secret Key: ")
        redirect_url = args.redirect_url or input("Enter Redirect URL: ")
        setup_credentials(app_id, secret_key, redirect_url)

    elif mode == "backtest":
        run_backtester()

    elif mode == "run":
        run_live_bot()

if __name__ == "__main__":
    main()
