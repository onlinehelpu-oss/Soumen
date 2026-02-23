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
import threading
from urllib.parse import urlparse, parse_qs, quote
from datetime import datetime as dt, timedelta
from typing import Optional, Tuple, Dict, Any

# Suppress specific warnings
warnings.filterwarnings("ignore", message=".*pkg_resources is deprecated.*")
warnings.filterwarnings("ignore", category=UserWarning, module="xgboost")

import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
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

    # --- Confidence Threshold ---
    CONFIDENCE_THRESHOLD = 0.55  # Optimized for Advanced Strategy (Lowered to catch more trades)

    # --- Backtester ---
    SYMBOL = "NSE:NIFTY50-INDEX"
    TIME_FRAME = "1"  # 1-minute candles
    DAYS_OF_DATA_TO_DOWNLOAD = 90
    TRAIN_TEST_SPLIT_RATIO = 0.7
    # Adjusted risk parameters for Advanced Strategy (1:3 Risk/Reward)
    BACKTEST_STOP_LOSS_PCT = 0.40  # Tight stop
    BACKTEST_TRAILING_STOP_LOSS_PCT = 0.40  # Tight trail
    BACKTEST_TAKE_PROFIT_PCT = 1.20  # High target

    # Lot Size for P&L Simulation
    LOT_SIZE = 65  # Updated to 65 as per user request
    NUM_LOTS = 1  # Increased to demonstrate higher profit potential

    # --- Ensemble Model Config ---
    ENSEMBLE_VOTING = 'soft'

    # --- Live Bot ---
    STRIKE_DISTANCE = 0  # 0 for ATM
    STOP_LOSS_PCT = 15.0  # Wide SL for Premium
    TAKE_PROFIT_PCT = 40.0  # Big Target for Premium
    TRAILING_STOP_LOSS_PCT = 5.0  # Trailing SL percentage for Live Bot
    PAPER_BALANCE = 100000
    LIVE_DATA_FILE = "live_market_data.csv"

    # --- Session Timing ---
    # Set to 00:00 to allow testing/running at any time of the day
    SESSION_START_TIME = dt.now().replace(hour=0, minute=0, second=0, microsecond=0)
    # Extended to 23:59 to allow for after-hours testing/paper trading
    SESSION_END_TIME = dt.now().replace(hour=23, minute=59, second=59, microsecond=0)


# ============================================================================
# --- SECTION 2: CREDENTIAL SETUP LOGIC ---
# ============================================================================

def setup_credentials(app_id, secret_key, redirect_url):
    """Creates the fyers_login_details.json file."""
    if not all([app_id, secret_key, redirect_url]):
        print("ERROR: App ID, Secret Key, and Redirect URL are all required.")
        return

        # Use standard key names 'api_key' and 'api_secret' for compatibility
    credentials = {
        "api_key": app_id,
        "api_secret": secret_key,
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

            # Support both old keys (client_id/secret_key) and new keys (api_key/api_secret)
        client_id = details.get("client_id") or details.get("api_key")
        secret_key = details.get("secret_key") or details.get("api_secret")
        redirect_url = details.get("redirect_url")

        if not all([client_id, secret_key, redirect_url]):
            raise KeyError("Missing client_id/api_key, secret_key/api_secret, or redirect_url")

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
                with open(token_file, 'w') as f:
                    json.dump(access_token, f)
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
    df['rsi'] = 100 - (100 / (1 + (
            df['returns'].rolling(window=14).apply(lambda x: x[x > 0].mean()) / -df['returns'].rolling(
        window=14).apply(lambda x: x[x < 0].mean()))))
    df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
    # Calculate EMA Slope (Velocity): (Current - Prev) / Prev * 100
    df['ema_slope'] = df['ema_21'].diff() / df['ema_21'].shift(1) * 100
    df['ema_short'] = df['close'].ewm(span=12, adjust=False).mean()
    df['ema_long'] = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = df['ema_short'] - df['ema_long']
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()

    # Rate of Change (ROC)
    df['roc'] = df['close'].pct_change(periods=10) * 100

    # Momentum (Close - Close n periods ago)
    df['momentum'] = df['close'] - df['close'].shift(4)

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

    # Initialize Base Models (Increased estimators for better generalization)
    xgb_model = xgb.XGBClassifier(n_estimators=200, random_state=42, n_jobs=-1, eval_metric='logloss')
    rf_model = RandomForestClassifier(n_estimators=200, max_depth=10, random_state=42, n_jobs=-1)
    gb_model = GradientBoostingClassifier(n_estimators=200, max_depth=5, random_state=42)

    # Create Voting Classifier (Ensemble)
    from sklearn.ensemble import VotingClassifier
    voting_model = VotingClassifier(
        estimators=[('xgb', xgb_model), ('rf', rf_model), ('gb', gb_model)],
        voting='soft'
    )

    voting_model.fit(X, y_mapped)

    joblib.dump(voting_model, BotConfig.MODEL_FILENAME)
    print(f"Model trained and saved to '{BotConfig.MODEL_FILENAME}'")


def run_backtest_simulation(features_df: pd.DataFrame):
    """Runs a backtest on the unseen test data with a Trailing Stop-Loss."""
    print("\n--- Backtesting with Trailing Stop-Loss ---")
    model = joblib.load(BotConfig.MODEL_FILENAME)

    train_size = int(len(features_df) * BotConfig.TRAIN_TEST_SPLIT_RATIO)
    test_data = features_df.iloc[train_size:].copy()

    if test_data.empty: return

    X_test = test_data.drop(columns=['target', 'open', 'high', 'low', 'close', 'volume'])

    # Get predictions and probabilities
    probs = model.predict_proba(X_test)
    predictions_mapped = np.argmax(probs, axis=1)
    confidences = np.max(probs, axis=1)

    # Filter by confidence threshold & RSI Trend
    prediction_remap = {0: -1, 1: 0, 2: 1}
    final_predictions = []

    # Pre-fetch technicals
    rsi_values = test_data['rsi'].values
    ema_50_values = test_data['ema_50'].values
    ema_slope_values = test_data['ema_slope'].values
    close_prices = test_data['close'].values

    for i, (pred, conf) in enumerate(zip(predictions_mapped, confidences)):
        signal = prediction_remap[pred]
        rsi = rsi_values[i]
        close = close_prices[i]
        ema_50 = ema_50_values[i]
        ema_slope = ema_slope_values[i]

        # Logic: Confidence AND Trend (EMA 50) AND Velocity (Slope) AND Momentum (RSI)
        if conf >= BotConfig.CONFIDENCE_THRESHOLD:
            # BUY CE: ML Up + Above EMA 50 + Positive Velocity + RSI > 50
            if signal == 1 and close > ema_50 and ema_slope > 0.005 and rsi > 50:
                final_predictions.append(1)
                # BUY PE: ML Down + Below EMA 50 + Negative Velocity + RSI < 50
            elif signal == -1 and close < ema_50 and ema_slope < -0.005 and rsi < 50:
                final_predictions.append(-1)
            else:
                final_predictions.append(0)
        else:
            final_predictions.append(0)

    test_data['prediction'] = final_predictions

    trades = []
    position, entry_price, stop_loss, peak_price = 0, 0.0, 0.0, 0.0

    for i in range(len(test_data)):
        current_price = test_data['close'].iloc[i]
        signal = test_data['prediction'].iloc[i]

        # --- Position Monitoring & Trailing Stop & Take Profit ---
        if position != 0:
            exit_reason = None
            if position == 1:  # Long
                peak_price = max(peak_price, current_price)
                new_stop_loss = peak_price * (1 - BotConfig.BACKTEST_TRAILING_STOP_LOSS_PCT / 100)
                stop_loss = max(stop_loss, new_stop_loss)
                take_profit_price = entry_price * (1 + BotConfig.BACKTEST_TAKE_PROFIT_PCT / 100)

                if current_price <= stop_loss:
                    exit_reason = "TRAIL_SL"
                elif current_price >= take_profit_price:
                    exit_reason = "TAKE_PROFIT"

            elif position == -1:  # Short
                peak_price = min(peak_price, current_price)
                new_stop_loss = peak_price * (1 + BotConfig.BACKTEST_TRAILING_STOP_LOSS_PCT / 100)
                stop_loss = min(stop_loss, new_stop_loss)
                take_profit_price = entry_price * (1 - BotConfig.BACKTEST_TAKE_PROFIT_PCT / 100)

                if current_price >= stop_loss:
                    exit_reason = "TRAIL_SL"
                elif current_price <= take_profit_price:
                    exit_reason = "TAKE_PROFIT"

            if exit_reason:
                # Simulate Profit/Loss
                # Assuming Delta ~ 0.5 for ATM options, so option price moves 0.5x of index
                # Note: P&L is a simulation using Delta=0.5, fixed Lot Size, and Multiplier.
                index_points = (current_price - entry_price) * position
                option_pnl = index_points * 0.5 * BotConfig.LOT_SIZE * BotConfig.NUM_LOTS
                trades.append(option_pnl)
                position = 0

                # --- Entry Logic ---
        if position == 0 and signal != 0:
            position = signal
            entry_price = current_price
            peak_price = entry_price
            if position == 1:  # Long
                stop_loss = entry_price * (1 - BotConfig.BACKTEST_STOP_LOSS_PCT / 100)
            else:  # Short
                stop_loss = entry_price * (1 + BotConfig.BACKTEST_STOP_LOSS_PCT / 100)

    total_pnl = sum(trades)
    print("Backtest complete.")
    analyze_performance(total_pnl, trades, BotConfig.PAPER_BALANCE)


def analyze_performance(total_pnl: float, trades: list, initial_balance: float):
    """Analyzes and prints the backtest performance."""
    print("\n--- Performance Analysis ---")
    print(
        f"(Note: Simulation based on Lot Size: {BotConfig.LOT_SIZE}, Lots: {BotConfig.NUM_LOTS}, and approx. Delta: 0.5)")
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

# --- HELPER FUNCTIONS FOR REAL OPTION RESOLUTION ---

def round_to_nearest_50(x: float) -> int:
    return int(round(x / 50.0) * 50)

def resolve_option_symbol(fyers: fyersModel.FyersModel, is_ce: bool, spot_ltp: float) -> Tuple[str, Optional[str]]:
    """
    Queries FYERS option chain for NIFTY and returns (symbol, 'YYYY-MM-DD' expiry)
    for nearest 50-strike of earliest expiry for the requested type (CE/PE).
    """
    chain = []
    for root in ("NSE:NIFTY50-INDEX", "NSE:NIFTY50", "NSE:NIFTY"):  # try all NIFTY roots
        try:
            resp = fyers.optionchain(data={"symbol": root}) or {}
            data = (resp.get("data") or {}).get("optionChain", []) or (resp.get("data") or {}).get("optionsChain", [])
            if data:
                chain = data
                break
        except Exception as e:
            print(f"[optionchain] root {root} failed: {e}")
    if not chain:
        # Fallback if API fails
        print("Warning: Optionchain response empty. Using fallback simulation.")
        return "", None

    target = round_to_nearest_50(spot_ltp)
    opt_type = "CE" if is_ce else "PE"
    filt = [row for row in chain if str(row.get("option_type", "")).upper() == opt_type]
    if not filt:
        print(f"Warning: Optionchain has no rows for type {opt_type}")
        return "", None

    def expiry_key(row):
        exp = row.get("expiry_date", row.get("expiry"))
        try:
            return dt.strptime(exp, "%d%b%y") if exp and len(exp) == 7 else dt.strptime(exp, "%Y-%m-%d")
        except Exception:
            return dt.max

    expiries = [r.get("expiry_date", r.get("expiry")) for r in filt if r.get("expiry_date", r.get("expiry"))]
    if expiries:
        earliest_row = min(filt, key=expiry_key)
        earliest = earliest_row.get("expiry_date", earliest_row.get("expiry"))
        filt = [r for r in filt if r.get("expiry_date", r.get("expiry")) == earliest]
        expiry_pick = earliest
    else:
        expiry_pick = None

    def strike_key(row):
        try:
            sp = row.get("strike_price", row.get("strikePrice"))
            return abs(float(sp) - target)
        except Exception:
            return 1e12

    best = min(filt, key=strike_key)
    symbol = best.get("symbol") or best.get("tradingsymbol") or best.get("tsym")
    return symbol, expiry_pick


class FyersService:
    """Handles all communication with the Fyers API for the live bot."""

    def __init__(self, config: BotConfig):
        self.config = config
        self.fyers, self.client_id, self.access_token = get_fyers_instance()
        self.underlying_ltp = 0
        self.fyers_ws = None

    def get_quote(self, symbol: str) -> float:
        """Fetches the latest LTP for a given symbol via Quote API."""
        try:
            data = {"symbols": symbol}
            response = self.fyers.quotes(data=data)
            if response.get("d") and len(response["d"]) > 0:
                return float(response["d"][0]["v"].get("lp", 0))
        except Exception as e:
            print(f"Error fetching quote for {symbol}: {e}")
        return 0.0

    def connect_to_websocket(self):
        """Initializes and connects to the Fyers Data WebSocket with detailed logging."""
        ws_access_token = f"{self.client_id}:{self.access_token}"

        # Define the callbacks with detailed logging
        def on_connect():
            print("✅ WebSocket Connected!")
            print("Subscribing to symbols:", [self.config.SYMBOL])
            self.fyers_ws.subscribe(symbols=[self.config.SYMBOL])

        def on_close():
            print("❌ WebSocket Closed.")

        def on_error(message):
            print(" WebSocket Error:", message)

        def on_message(message):
            """Callback function to handle incoming ticks."""
            # Reduced log verbosity for tick data
            if isinstance(message, dict) and 'ltp' in message:
                self.underlying_ltp = message['ltp']
            elif isinstance(message, list) and len(message) > 0 and 'ltp' in message[0]:
                self.underlying_ltp = message[0]['ltp']
            else:
                print("  [WS MSG]:", message)  # Log non-tick messages

        # Initialize the WebSocket with the new callbacks
        self.fyers_ws = data_ws.FyersDataSocket(
            access_token=ws_access_token,
            log_path="",
            on_connect=on_connect,
            on_close=on_close,
            on_error=on_error,
            on_message=on_message
        )

        print("🔌 Attempting to connect to WebSocket...")
        # Run WebSocket in a background thread to prevent blocking the main loop
        t = threading.Thread(target=self.fyers_ws.connect)
        t.daemon = True
        t.start()


def create_live_features(price_history: list) -> pd.DataFrame:
    """Creates advanced features from a list of recent prices for the live bot."""
    # Note: Live bot uses tick/close history. Proxying High/Low as Close for indicator compatibility.
    df = pd.DataFrame({'close': price_history})
    df['high'] = df['close']
    df['low'] = df['close']
    # Note: df.index is RangeIndex (0, 1, 2...), which is fine for rolling calculations.

    # Basic returns
    df['returns'] = df['close'].pct_change()

    # 1. Standard Indicators
    df['rsi'] = 100 - (100 / (1 + (
            df['returns'].rolling(window=14).apply(lambda x: x[x > 0].mean()) / -df['returns'].rolling(
        window=14).apply(lambda x: x[x < 0].mean()))))
    df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
    # Calculate EMA Slope (Velocity): (Current - Prev) / Prev * 100
    df['ema_slope'] = df['ema_21'].diff() / df['ema_21'].shift(1) * 100
    df['ema_short'] = df['close'].ewm(span=12, adjust=False).mean()
    df['ema_long'] = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = df['ema_short'] - df['ema_long']
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()

    # Rate of Change (ROC)
    df['roc'] = df['close'].pct_change(periods=10) * 100

    # Momentum
    df['momentum'] = df['close'] - df['close'].shift(4)

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

        # Drop OHLC columns to match training features
    # NOTE: We keep 'st_upper', 'st_lower', 'supertrend_signal' in the returned dataframe
    # because they ARE used as features for the ML model during training.
    # Only OHLC columns (and target) are dropped in train_and_save_model.
    return df.drop(columns=['close', 'high', 'low']).dropna()


class MLStrategy:
    """The ML-powered strategy that uses the backtested model for live trading."""

    def __init__(self, config: BotConfig):
        self.config = config
        self.model = joblib.load(self.config.MODEL_FILENAME)

    def generate_signal(self, price_history: list) -> str:
        if len(price_history) < 30: return "HOLD"
        features = create_live_features(price_history)
        if features.empty: return "HOLD"

        # Get prediction and probabilities
        try:
            # Check model confidence
            probs = self.model.predict_proba(features.tail(1))[0]
            prediction_mapped = np.argmax(probs)
            confidence = probs[prediction_mapped]

            # 1. Check Confidence
            if confidence < self.config.CONFIDENCE_THRESHOLD:
                return "HOLD"

                # Remap it back: [0, 1, 2] -> [-1, 0, 1]
            prediction_remap = {0: -1, 1: 0, 2: 1}
            prediction = prediction_remap[prediction_mapped]

            # 2. Check Advanced Filters
            current_rsi = features['rsi'].iloc[-1]
            current_ema_50 = features['ema_50'].iloc[-1]
            current_ema_slope = features['ema_slope'].iloc[-1]
            current_close = price_history[-1]
            current_time = dt.now()

            # Time Filter: No trades before 09:30 or after 15:00
            if current_time.hour < 9 or (
                    current_time.hour == 9 and current_time.minute < 30) or current_time.hour >= 15:
                return "HOLD"

                # BUY CE: ML Up + Price > EMA 50 + Positive Velocity + RSI > 50
            if prediction == 1 and current_close > current_ema_50 and current_ema_slope > 0.005 and current_rsi > 50:
                return "BUY_CE"
                # BUY PE: ML Down + Price < EMA 50 + Negative Velocity + RSI < 50
            elif prediction == -1 and current_close < current_ema_50 and current_ema_slope < -0.005 and current_rsi < 50:
                return "BUY_PE"

        except Exception as e:
            print(f"Error in signal generation: {e}")

        return "HOLD"


class PaperPosition:
    """Represents a single simulated position."""

    def __init__(self, symbol: str, entry_price: float, stop_loss: float, take_profit: float, quantity: int):
        self.symbol, self.entry_price, self.stop_loss, self.take_profit = symbol, entry_price, stop_loss, take_profit
        self.quantity = quantity
        self.current_price, self.pnl = entry_price, 0.0
        self.peak_price = entry_price  # Track peak price for Trailing SL

    def update_pnl(self, current_price: float):
        self.current_price = current_price
        self.pnl = (self.current_price - self.entry_price) * self.quantity
        if self.current_price > self.peak_price:
            self.peak_price = self.current_price


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
        print(f"🚀 STARTING LIVE BOT (PAPER TRADING - REAL PRICES) | Balance: ₹{self.paper_balance:,.2f}")
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

            self._log_data(current_price)

            self.price_history.append(current_price)
            if len(self.price_history) > 200: self.price_history.pop(0)

            print(f"[{dt.now().strftime('%H:%M:%S')}] NIFTY: {current_price} | Balance: ₹{self.paper_balance:,.2f}")

            if self.active_position:
                self._monitor_position(current_price, last_underlying_price)
            else:
                self._check_for_entry_signal(current_price)

            last_underlying_price = current_price
            time.sleep(15)

        print("\n⏳ Session time ended.")
        self._show_paper_summary()

    def _open_paper_position(self, price: float, opt_type: str):
        # 1. Resolve Real Option Symbol
        is_ce = (opt_type == "CE")
        symbol, _ = resolve_option_symbol(self.fyers_service.fyers, is_ce, price)

        if not symbol:
            print(f"  ❌ Error: Could not resolve {opt_type} option symbol. Trade skipped.")
            return

        # 2. Fetch Real LTP
        entry_premium = self.fyers_service.get_quote(symbol)

        if entry_premium == 0:
            print(f"  ❌ Error: Could not fetch quote for {symbol}. Trade skipped.")
            return

        # 3. Calculate SL/TP based on Real Premium
        sl = entry_premium * (1 - self.config.STOP_LOSS_PCT / 100)
        tp = entry_premium * (1 + self.config.TAKE_PROFIT_PCT / 100)

        total_quantity = self.config.LOT_SIZE * self.config.NUM_LOTS
        self.active_position = PaperPosition(symbol, entry_premium, sl, tp, total_quantity)

        print(f"  ✅ PAPER TRADE: Opened {symbol} (Qty: {total_quantity}) @ ₹{entry_premium:.2f} | SL: ₹{sl:.2f}, TP: ₹{tp:.2f}")

    def _monitor_position(self, price: float, last_price: float):
        pos = self.active_position
        if not pos: return

        # Fetch Real Live Price of the Option
        current_option_price = self.fyers_service.get_quote(pos.symbol)

        if current_option_price == 0:
            print(f"  ⚠️ Warning: Could not fetch live quote for {pos.symbol}. Skipping update.")
            return

        pos.update_pnl(current_option_price)

        # Update Trailing Stop Loss
        if pos.peak_price > pos.entry_price:
            new_stop_loss = pos.peak_price * (1 - self.config.TRAILING_STOP_LOSS_PCT / 100)
            if new_stop_loss > pos.stop_loss:
                pos.stop_loss = new_stop_loss
                print(f"  🔄 TRAILING SL Updated: ₹{pos.stop_loss:.2f}")

        print(
            f"  HOLDING: {pos.symbol} | Entry: {pos.entry_price:.2f} | Now: {pos.current_price:.2f} | P&L: {pos.pnl:+.2f} | SL: {pos.stop_loss:.2f}")

        if pos.current_price <= pos.stop_loss:
            self._close_paper_position("STOP LOSS")
        elif pos.current_price >= pos.take_profit:
            self._close_paper_position("TAKE PROFIT")

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
        if signal == "BUY_CE":
            self._open_paper_position(price, "CE")
        elif signal == "BUY_PE":
            self._open_paper_position(price, "PE")

    def _log_data(self, price):
        """Saves the current timestamp and price to a CSV file."""
        file_exists = os.path.isfile(self.config.LIVE_DATA_FILE)
        try:
            with open(self.config.LIVE_DATA_FILE, 'a') as f:
                if not file_exists:
                    f.write("timestamp,price\n")
                f.write(f"{dt.now().strftime('%Y-%m-%d %H:%M:%S')},{price}\n")
        except Exception as e:
            print(f"Warning: Could not save live data: {e}")

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

    # Optional Argument for Auto-Retraining in Run mode
    parser.add_argument("--retrain", action="store_true",
                        help="Automatically retrain the model before running the live bot.")

    args = parser.parse_args()

    mode = args.mode

    if not mode:
        print("\n--- Welcome to the All-In-One Trading Bot ---")
        print("Please choose an option:")
        print("  1. Setup Credentials")
        print("  2. Run Backtester")
        print("  3. Run Live Paper Trading Bot")
        print("  4. Run Live Bot (with Auto-Retrain)")
        choice = input("Enter your choice (1-4): ")

        if choice == '1':
            mode = 'setup'
        elif choice == '2':
            mode = 'backtest'
        elif choice == '3':
            mode = 'run'
        elif choice == '4':
            mode = 'run'
            args.retrain = True
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
        if args.retrain:
            print("\n🔄 Auto-Retrain Enabled: Updating model with latest data...")
            run_backtester()
            print("✅ Retraining complete. Starting Live Bot...")
        run_live_bot()


if __name__ == "__main__":
    main()
