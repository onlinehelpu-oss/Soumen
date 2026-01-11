"""
LIVE FYERS OPTIONS TRADING BOT
===============================

**Disclaimer:**
This bot is intended for educational and paper trading purposes.
Live trading involves significant financial risk. Use at your own discretion.
"""

import time
import json
import os
from datetime import datetime as dt, timedelta
import webbrowser

# Fyers API library
from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws
import pandas as pd
import joblib
import numpy as np

# ============================================================================
# CONFIGURATION
# ============================================================================

class LiveConfig:
    """Configuration for the live/paper trading bot."""
    # Trading Parameters
    SYMBOL_UNDERLYING = "NSE:NIFTY50-INDEX"
    STRIKE_DISTANCE = 0  # 0 for ATM, positive for OTM, negative for ITM
    TIME_FRAME = "1"  # 1-minute candles for the underlying

    # Risk Management
    STOP_LOSS_PCT = 15.0  # % stop loss on the option premium
    TAKE_PROFIT_PCT = 30.0 # % take profit on the option premium

    # Session Timing
    SESSION_START_TIME = dt.now().replace(hour=9, minute=15, second=0, microsecond=0)
    SESSION_END_TIME = dt.now().replace(hour=15, minute=30, second=0, microsecond=0)

    # API Details
    LOGIN_DETAILS_FILE = "fyers_login_details.json"
    MODEL_FILENAME = "real_options_model.joblib"

# ============================================================================
# FYERS API INTEGRATION (Placeholder)
# ============================================================================

class FyersService:
    """Handles all communication with the Fyers API."""

    def __init__(self, config: LiveConfig):
        self.config = config
        self.fyers = None
        self.access_token = None
        self.client_id = ""
        self.underlying_ltp = 0
        print("Initializing Fyers Service...")
        self._load_credentials()
        self._login()

    def _load_credentials(self):
        """Loads API credentials from the JSON file."""
        try:
            with open(self.config.LOGIN_DETAILS_FILE, 'r') as f:
                details = json.load(f)
                self.client_id = details["client_id"]
                self.secret_key = details["secret_key"]
                self.redirect_url = details["redirect_url"]
        except FileNotFoundError:
            print(f"ERROR: Login file not found at '{self.config.LOGIN_DETAILS_FILE}'")
            print("Please create the file with your Fyers API credentials.")
            exit()
        except KeyError as e:
            print(f"ERROR: Missing key '{e.args[0]}' in '{self.config.LOGIN_DETAILS_FILE}'.")
            print("Please ensure the file is formatted correctly with 'client_id', 'secret_key', and 'redirect_url'.")
            exit()

    def _login(self):
        """Handles the Fyers login process."""
        token_file = f"fyers_token_{self.client_id}.json"

        # Try to load existing token
        if os.path.exists(token_file):
            with open(token_file, 'r') as f:
                self.access_token = json.load(f)
            print("Loaded access token from file.")
        else:
            self._generate_new_token(token_file)

        # Initialize FyersModel
        self.fyers = fyersModel.FyersModel(client_id=self.client_id, is_async=False, token=self.access_token, log_path=os.getcwd())
        print("FyersModel initialized.")
        self._check_profile()

    def _generate_new_token(self, token_file: str):
        """Generates a new access token via browser authentication."""
        print("No existing token found. Starting new login process...")
        session = fyersModel.SessionModel(
            client_id=self.client_id,
            secret_key=self.secret_key,
            redirect_uri=self.redirect_url,
            response_type='code',
            grant_type='authorization_code'
        )

        auth_url = session.generate_authcode()
        print(f"Authentication URL generated. Opening in browser...")
        webbrowser.open(auth_url, new=1)

        auth_code = input("Please enter the auth code from the redirected URL: ").strip()

        session.set_token(auth_code)
        response = session.generate_token()

        if response.get("access_token"):
            self.access_token = response["access_token"]
            print("Access token generated successfully.")
            with open(token_file, 'w') as f:
                json.dump(self.access_token, f)
            print(f"Token saved to {token_file}")
        else:
            print(f"ERROR: Failed to generate access token. Response: {response}")
            exit()

    def _check_profile(self):
        """Checks if the login was successful by fetching the profile."""
        try:
            profile = self.fyers.get_profile()
            if profile.get('data'):
                print(f"Login successful! Welcome, {profile['data']['name']}.")
            else:
                print(f"WARNING: Could not verify login. Profile response: {profile}")
                # Potentially invalid token, try to regenerate
                os.remove(f"fyers_token_{self.client_id}.json")
                print("Removed potentially invalid token file. Please restart the bot.")
                exit()
        except Exception as e:
            print(f"An error occurred while checking profile: {e}")
            exit()

    def connect_to_websocket(self):
        """Initializes and connects to the Fyers Data WebSocket."""
        print("Connecting to Fyers WebSocket...")

        data_type = "symbolData"
        symbols = [self.config.SYMBOL_UNDERLYING]

        def on_ticks(message):
            """Callback function to handle incoming ticks."""
            if message and isinstance(message, list) and 'ltp' in message[0]:
                self.underlying_ltp = message[0]['ltp']
                # print(f"LTP for {message[0]['symbol']}: {self.underlying_ltp}") # Uncomment for debugging

        def on_connect():
            """Callback function for successful WebSocket connection."""
            print("WebSocket connected. Subscribing to symbols...")
            fyers_ws.subscribe(symbol=symbols, data_type=data_type)

        def on_close():
            print("WebSocket connection closed.")

        def on_error(message):
            print(f"WebSocket Error: {message}")

        # Construct the access token for the WebSocket
        ws_access_token = f"{self.client_id}:{self.access_token}"

        # Initialize the WebSocket
        fyers_ws = data_ws.FyersDataSocket(
            access_token=ws_access_token,
            log_path="",
            on_connect=on_connect,
            on_close=on_close,
            on_error=on_error,
            on_message=on_ticks
        )

        # Establish the connection in a separate thread
        fyers_ws.connect()
        print("WebSocket connection process initiated.")

    def place_paper_order(self, symbol: str, side: str):
        """Simulates placing an order."""
        print(f"--- PAPER TRADING ---")
        print(f"Action: {side.upper()} | Symbol: {symbol}")
        print(f"--------------------")
        # In a real implementation, this would track virtual positions
        return True

# ============================================================================
# ML STRATEGY COMPONENTS
# ============================================================================

def create_features(price_history: list) -> pd.DataFrame:
    """Creates features from a list of recent prices."""
    df = pd.DataFrame({'close': price_history})
    df['returns'] = df['close'].pct_change()

    # Standard Indicators
    df['rsi'] = 100 - (100 / (1 + (df['returns'].rolling(window=14).apply(lambda x: x[x>0].mean()) / -df['returns'].rolling(window=14).apply(lambda x: x[x<0].mean()))))
    df['ema_short'] = df['close'].ewm(span=12, adjust=False).mean()
    df['ema_long'] = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = df['ema_short'] - df['ema_long']
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()

    return df.drop(columns=['close']).dropna()

class MLStrategy:
    """The ML-powered strategy that uses the backtested model."""
    def __init__(self, config: LiveConfig):
        self.config = config
        self.model = None
        self._load_model()

    def _load_model(self):
        """Loads the trained model from file."""
        try:
            self.model = joblib.load(self.config.MODEL_FILENAME)
            print(f"Successfully loaded ML model from '{self.config.MODEL_FILENAME}'")
        except FileNotFoundError:
            print(f"ERROR: Model file not found at '{self.config.MODEL_FILENAME}'")
            print("Please run the backtester.py script to train and save the model.")
            exit()

    def generate_signal(self, price_history: list) -> str:
        """Generates a signal using the loaded ML model."""
        if len(price_history) < 30: # Need enough data for feature creation
            return "HOLD"

        features = create_features(price_history)
        if features.empty:
            return "HOLD"

        # Use the latest set of features for prediction
        latest_features = features.tail(1)

        prediction = self.model.predict(latest_features)[0]

        if prediction == 1:
            return "BUY_CE"
        elif prediction == -1:
            return "BUY_PE"
        else:
            return "HOLD"

# ============================================================================
# PAPER TRADING COMPONENTS
# ============================================================================

class PaperPosition:
    """Represents a single simulated position."""
    def __init__(self, symbol: str, entry_price: float, stop_loss: float, take_profit: float):
        self.symbol = symbol
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.entry_time = dt.now()
        self.current_price = entry_price
        self.pnl = 0.0

    def update_pnl(self, current_price: float):
        """Updates the current price and P&L of the position."""
        self.current_price = current_price
        self.pnl = (self.current_price - self.entry_price)

# ============================================================================
# LIVE OPTIONS BOT
# ============================================================================

class LiveOptionsBot:
    """The main class for the live options trading bot."""

    def __init__(self, config: LiveConfig):
        self.config = config
        self.fyers_service = FyersService(config)
        self.strategy = MLStrategy(config)

        # Paper Trading State
        self.paper_balance = 100000  # Starting virtual balance
        self.paper_trades = []
        self.active_position: Optional[PaperPosition] = None

        self.price_history = []
        print("Live Options Bot initialized for Paper Trading.")

    def run(self):
        """Starts the main trading loop."""
        print("\n" + "=" * 60)
        print("🚀 STARTING LIVE OPTIONS BOT (PAPER TRADING MODE)")
        print(f"Underlying: {self.config.SYMBOL_UNDERLYING} | Initial Balance: ₹{self.paper_balance:,.2f}")
        print(f"Session Time: {self.config.SESSION_START_TIME.strftime('%H:%M')} - {self.config.SESSION_END_TIME.strftime('%H:%M')}")
        print("=" * 60)

        self.fyers_service.connect_to_websocket()
        print("Waiting for first price tick from WebSocket...")
        time.sleep(5)

        last_underlying_price = 0

        while dt.now() < self.config.SESSION_END_TIME:
            if dt.now() < self.config.SESSION_START_TIME:
                print(f"\rWaiting for market to open... {self.config.SESSION_START_TIME.strftime('%H:%M')}", end="")
                time.sleep(30)
                continue

            current_underlying_price = self.fyers_service.underlying_ltp
            if current_underlying_price == 0:
                print("\rWaiting for valid LTP...", end="")
                time.sleep(5)
                continue

            self.price_history.append(current_underlying_price)
            if len(self.price_history) > 200: self.price_history.pop(0)

            print(f"[{dt.now().strftime('%H:%M:%S')}] NIFTY: {current_underlying_price} | Paper Balance: ₹{self.paper_balance:,.2f}")

            if self.active_position:
                self._monitor_position(current_underlying_price, last_underlying_price)
            else:
                self._check_for_entry_signal(current_underlying_price)

            last_underlying_price = current_underlying_price
            time.sleep(15)

        print("\n" + "=" * 60)
        print("🛑 SESSION ENDED. Bot shutting down.")
        self._show_paper_summary()

    def _open_paper_position(self, underlying_price: float, option_type: str):
        """Opens a new simulated position."""
        # Assume ATM option premium is ~100 for simulation
        entry_premium = 100.0

        sl_price = entry_premium * (1 - self.config.STOP_LOSS_PCT / 100)
        tp_price = entry_premium * (1 + self.config.TAKE_PROFIT_PCT / 100)

        strike = int(round(underlying_price / 50) * 50)
        symbol = f"NIFTY_DEMO_{strike}_{option_type}"

        self.active_position = PaperPosition(symbol, entry_premium, sl_price, tp_price)

        print(f"  ✅ PAPER TRADE: Opened {symbol} at ₹{entry_premium:.2f}")
        print(f"     SL: ₹{sl_price:.2f} | TP: ₹{tp_price:.2f}")

    def _monitor_position(self, underlying_price: float, last_underlying_price: float):
        """Monitors and updates the active paper position."""
        if not self.active_position or last_underlying_price == 0:
            return

        # Simulate option price change (assuming delta of 0.5)
        price_change = (underlying_price - last_underlying_price)
        option_type = self.active_position.symbol.split('_')[-1]

        if option_type == 'CE':
            premium_change = price_change * 0.5
        else: # PE
            premium_change = -price_change * 0.5

        new_price = self.active_position.current_price + premium_change
        self.active_position.update_pnl(new_price)

        pos = self.active_position
        print(f"  HOLDING: {pos.symbol} | Entry: {pos.entry_price:.2f} | Now: {pos.current_price:.2f} | P&L: {pos.pnl:+.2f}")

        # Check for SL/TP
        if pos.current_price <= pos.stop_loss:
            self._close_paper_position("STOP LOSS")
        elif pos.current_price >= pos.take_profit:
            self._close_paper_position("TAKE PROFIT")

    def _close_paper_position(self, reason: str):
        """Closes the active position and records the trade."""
        if not self.active_position:
            return

        pos = self.active_position
        self.paper_balance += pos.pnl

        trade_summary = {
            "symbol": pos.symbol, "reason": reason, "pnl": pos.pnl,
            "entry": pos.entry_price, "exit": pos.current_price
        }
        self.paper_trades.append(trade_summary)

        pnl_color = "\033[92m" if pos.pnl > 0 else "\033[91m"
        reset_color = "\033[0m"
        print(f"  ❌ PAPER TRADE: Closed {pos.symbol} for {reason}.")
        print(f"     P&L: {pnl_color}₹{pos.pnl:+.2f}{reset_color}")

        self.active_position = None

    def _check_for_entry_signal(self, underlying_price: float):
        """Generates a signal and opens a position if needed."""
        signal = self.strategy.generate_signal(self.price_history)
        print(f"  ML Signal: {signal}")

        if signal == "BUY_CE":
            self._open_paper_position(underlying_price, "CE")
        elif signal == "BUY_PE":
            self._open_paper_position(underlying_price, "PE")

    def _show_paper_summary(self):
        """Displays a summary of all paper trades."""
        print("\n" + "="*30)
        print("📜 PAPER TRADING SUMMARY")
        print("="*30)
        if not self.paper_trades:
            print("No trades were executed.")
            return

        total_pnl = sum(t['pnl'] for t in self.paper_trades)
        wins = sum(1 for t in self.paper_trades if t['pnl'] > 0)
        losses = len(self.paper_trades) - wins
        win_rate = (wins / len(self.paper_trades) * 100) if self.paper_trades else 0

        print(f"Total Trades: {len(self.paper_trades)}")
        print(f"Wins: {wins} | Losses: {losses} | Win Rate: {win_rate:.2f}%")
        print(f"Total P&L: ₹{total_pnl:,.2f}")
        print(f"Final Balance: ₹{self.paper_balance:,.2f}")
        print("🛑 SESSION ENDED. Bot shutting down.")
        print("=" * 60)


# ============================================================================
# SCRIPT ENTRY
# ============================================================================

if __name__ == "__main__":
    config = LiveConfig()
    bot = LiveOptionsBot(config)
    bot.run()
