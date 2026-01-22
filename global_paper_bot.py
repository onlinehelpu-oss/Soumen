"""
GLOBAL REAL-TIME PAPER TRADING BOT FRAMEWORK
============================================

**Description:**
This script provides a robust framework for real-time paper trading using the Fyers API.
It handles:
1. Authentication (Auto-login / Token management)
2. Real-time Market Data (WebSocket)
3. Paper Trading Execution (Virtual Balance, Positions, P&L, Stop Loss, Take Profit)
4. Custom Strategy Implementation (A dedicated section for YOUR logic)

**Usage:**
1. Setup Credentials (Run Once):
   python global_paper_bot.py setup --client_id YOUR_ID --secret_key YOUR_KEY --redirect_url YOUR_URL

2. Run the Bot:
   python global_paper_bot.py run

**How to Add Your Strategy:**
Scroll down to the 'CUSTOM STRATEGY SECTION'.
Implement your logic in the `generate_signal` method.
"""

import os
import sys
import json
import time
import argparse
import webbrowser
import hashlib
import requests
import threading
import warnings
from urllib.parse import urlparse, parse_qs, quote
from datetime import datetime as dt, timedelta
from typing import Optional, Tuple, Dict, Any, List

# Suppress warnings
warnings.filterwarnings("ignore")

# Fyers API library
try:
    from fyers_apiv3 import fyersModel
    from fyers_apiv3.FyersWebsocket import data_ws
except ImportError:
    print("Error: 'fyers_apiv3' not found. Please install it using: pip install fyers-apiv3")
    sys.exit(1)


# ============================================================================
# --- SECTION 1: CONFIGURATION ---
# ============================================================================

class BotConfig:
    """Global configuration for the bot."""
    # --- File Names ---
    LOGIN_DETAILS_FILE = "fyers_login_details.json"
    LIVE_DATA_FILE = "paper_trade_data.csv"

    # --- Trading Parameters ---
    SYMBOL = "NSE:NIFTY50-INDEX"  # The symbol to monitor and trade
    PAPER_BALANCE = 100000.0      # Starting virtual capital

    # Default Risk Management (Can be overridden by Strategy)
    DEFAULT_QUANTITY = 50         # Quantity per trade
    STOP_LOSS_PCT = 10.0          # Default Stop Loss %
    TAKE_PROFIT_PCT = 20.0        # Default Take Profit %
    TRAILING_SL_PCT = 5.0         # Trailing Stop Loss %

    # --- Session Timing ---
    # Run all day for testing (00:00 to 23:59)
    SESSION_START_TIME = dt.now().replace(hour=0, minute=0, second=0, microsecond=0)
    SESSION_END_TIME = dt.now().replace(hour=23, minute=59, second=59, microsecond=0)


# ============================================================================
# --- SECTION 2: AUTHENTICATION (Reusable Logic) ---
# ============================================================================

def setup_credentials(app_id, secret_key, redirect_url):
    """Creates the fyers_login_details.json file."""
    if not all([app_id, secret_key, redirect_url]):
        print("ERROR: App ID, Secret Key, and Redirect URL are all required.")
        return

    credentials = {
        "api_key": app_id,
        "api_secret": secret_key,
        "redirect_url": redirect_url
    }

    try:
        with open(BotConfig.LOGIN_DETAILS_FILE, 'w') as f:
            json.dump(credentials, f, indent=2)
        print(f"✅ Successfully created '{BotConfig.LOGIN_DETAILS_FILE}'. Ready to run.")
    except Exception as e:
        print(f"❌ Error creating file: {e}")

def build_auth_url(app_id, redirect_uri, state="sample_state"):
    base = "https://api-t1.fyers.in/api/v3/generate-authcode"
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
    if user_input.startswith("http://") or user_input.startswith("https://"):
        q = parse_qs(urlparse(user_input).query)
        code = q.get("code", [None])[0]
        if not code:
            raise ValueError("No 'code' param found in the provided URL.")
        return code
    return user_input

def sha256_appIdHash(app_id, secret_id):
    return hashlib.sha256(f"{app_id}:{secret_id}".encode("utf-8")).hexdigest()

def validate_authcode(app_id, secret_id, auth_code):
    url = "https://api-t1.fyers.in/api/v3/validate-authcode"
    payload = {
        "grant_type": "authorization_code",
        "appIdHash": sha256_appIdHash(app_id, secret_id),
        "code": auth_code,
    }
    r = requests.post(url, headers={"Content-Type": "application/json"}, json=payload)
    return r.json()

def get_fyers_instance():
    """Authenticates and returns the FyersModel instance."""
    if not os.path.exists(BotConfig.LOGIN_DETAILS_FILE):
        print("❌ Config file not found. Run 'setup' mode first.")
        sys.exit(1)

    with open(BotConfig.LOGIN_DETAILS_FILE, 'r') as f:
        details = json.load(f)

    client_id = details.get("client_id") or details.get("api_key")
    secret_key = details.get("secret_key") or details.get("api_secret")
    redirect_url = details.get("redirect_url")

    tokens_dir = "AccessToken"
    today_str = str(dt.now().date())
    token_file = os.path.join(tokens_dir, f"{today_str}.json")

    access_token = None
    if os.path.exists(token_file):
        with open(token_file, 'r') as f:
            access_token = json.load(f)

    if not access_token:
        print("🔑 Starting Login Process...")
        auth_url = build_auth_url(client_id, redirect_url)
        webbrowser.open(auth_url, new=1)
        print(f"\nLogin URL: {auth_url}")
        user_val = input("\nPaste the Redirect URL or Code here: ").strip()

        try:
            auth_code = extract_code(user_val)
            token_resp = validate_authcode(client_id, secret_key, auth_code)
            if token_resp.get("access_token"):
                access_token = token_resp["access_token"]
                os.makedirs(tokens_dir, exist_ok=True)
                with open(token_file, 'w') as f:
                    json.dump(access_token, f)
            else:
                print(f"❌ Login Failed: {token_resp}")
                sys.exit(1)
        except Exception as e:
            print(f"❌ Error: {e}")
            sys.exit(1)

    fyers = fyersModel.FyersModel(client_id=client_id, is_async=False, token=access_token, log_path="")
    return fyers, client_id, access_token


# ============================================================================
# --- SECTION 3: MARKET DATA ENGINE ---
# ============================================================================

class MarketDataService:
    """Manages WebSocket connection and real-time price updates."""
    def __init__(self, client_id, access_token, symbol):
        self.client_id = client_id
        self.access_token = access_token
        self.symbol = symbol
        self.ltp = 0.0
        self.fyers_ws = None
        self.lock = threading.Lock()
        # You can store history here if needed for indicators
        self.price_history = []

    def connect(self):
        ws_token = f"{self.client_id}:{self.access_token}"

        def on_connect():
            print(f"✅ WebSocket Connected. Subscribing to {self.symbol}")
            self.fyers_ws.subscribe(symbols=[self.symbol])

        def on_message(msg):
            # Handle tick data
            if isinstance(msg, list) and len(msg) > 0 and 'ltp' in msg[0]:
                price = msg[0]['ltp']
                with self.lock:
                    self.ltp = price
                    self.price_history.append(price)
                    if len(self.price_history) > 1000: # Keep last 1000 ticks
                        self.price_history.pop(0)

        def on_error(msg):
            print(f"⚠️ WS Error: {msg}")

        self.fyers_ws = data_ws.FyersDataSocket(
            access_token=ws_token,
            log_path="",
            on_connect=on_connect,
            on_message=on_message,
            on_error=on_error
        )

        t = threading.Thread(target=self.fyers_ws.connect)
        t.daemon = True
        t.start()

    def get_ltp(self):
        with self.lock:
            return self.ltp

    def get_history(self):
        with self.lock:
            return list(self.price_history)


# ============================================================================
# --- SECTION 4: PAPER TRADING ENGINE ---
# ============================================================================

class PaperPosition:
    def __init__(self, symbol, entry_price, quantity, side, stop_loss, take_profit):
        self.symbol = symbol
        self.entry_price = entry_price
        self.quantity = quantity
        self.side = side # "BUY" or "SELL"
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.peak_price = entry_price
        self.pnl = 0.0

class PaperExchange:
    """Simulates the exchange for paper trading."""
    def __init__(self, balance):
        self.balance = balance
        self.active_positions = [] # List of PaperPosition
        self.trade_log = []

    def open_position(self, symbol, price, quantity, side, sl, tp):
        pos = PaperPosition(symbol, price, quantity, side, sl, tp)
        self.active_positions.append(pos)
        print(f"🔵 OPEN {side}: {symbol} @ {price:.2f} | Qty: {quantity} | SL: {sl:.2f} | TP: {tp:.2f}")

    def close_position(self, position, price, reason):
        # Calculate P&L
        if position.side == "BUY":
            pnl = (price - position.entry_price) * position.quantity
        else: # SELL
            pnl = (position.entry_price - price) * position.quantity

        self.balance += pnl
        self.active_positions.remove(position)

        record = {
            "symbol": position.symbol,
            "side": position.side,
            "entry": position.entry_price,
            "exit": price,
            "pnl": pnl,
            "reason": reason,
            "time": dt.now().strftime("%H:%M:%S")
        }
        self.trade_log.append(record)

        color = "🟢" if pnl > 0 else "🔴"
        print(f"{color} CLOSE {position.side}: {position.symbol} @ {price:.2f} | P&L: {pnl:.2f} | Reason: {reason}")
        print(f"💰 Current Balance: {self.balance:.2f}")

    def update(self, current_price):
        """Checks SL/TP for all active positions."""
        for pos in list(self.active_positions):
            # Update running P&L for display if needed
            if pos.side == "BUY":
                pos.pnl = (current_price - pos.entry_price) * pos.quantity
                # Trailing logic (optional, simple implementation)
                if current_price > pos.peak_price:
                    pos.peak_price = current_price
                    # E.g. Trail SL if configured (not implemented here to keep it clean)

                # Check Exits
                if current_price <= pos.stop_loss:
                    self.close_position(pos, current_price, "STOP_LOSS")
                elif current_price >= pos.take_profit:
                    self.close_position(pos, current_price, "TAKE_PROFIT")

            elif pos.side == "SELL":
                pos.pnl = (pos.entry_price - current_price) * pos.quantity
                if current_price < pos.peak_price:
                    pos.peak_price = current_price

                if current_price >= pos.stop_loss:
                    self.close_position(pos, current_price, "STOP_LOSS")
                elif current_price <= pos.take_profit:
                    self.close_position(pos, current_price, "TAKE_PROFIT")


# ============================================================================
# --- SECTION 5: CUSTOM STRATEGY SECTION ---
# ============================================================================

class CustomStrategy:
    """
    PUT YOUR LOGIC HERE.
    This class receives market data and decides whether to Buy or Sell.
    """
    def __init__(self, config: BotConfig):
        self.config = config
        # Initialize any variables/indicators here
        self.last_signal = None

    def on_tick(self, current_price: float, price_history: List[float], active_positions: List[PaperPosition]) -> Optional[Dict]:
        """
        Called every time the price updates.

        Args:
            current_price: The latest LTP.
            price_history: List of recent prices (ticks).
            active_positions: List of currently open positions.

        Returns:
            None (if no action) OR
            Dict with keys: {'action': 'BUY'/'SELL', 'quantity': int, 'sl_price': float, 'tp_price': float}
        """

        # --- EXAMPLE LOGIC (Simple Trend Following) ---
        # If we have no position, and price moves up significantly...

        # 1. Don't trade if we already have a position (Simple mode)
        if len(active_positions) > 0:
            return None

        # 2. Need some history
        if len(price_history) < 20:
            return None

        # 3. Simple Indicator: Price > Average of last 20 ticks
        avg_price = sum(price_history[-20:]) / 20

        # LOGIC: Buy if Current Price > Average + 5 points
        if current_price > avg_price + 5:
            sl = current_price * (1 - self.config.STOP_LOSS_PCT/100)
            tp = current_price * (1 + self.config.TAKE_PROFIT_PCT/100)

            return {
                "action": "BUY",
                "quantity": self.config.DEFAULT_QUANTITY,
                "sl_price": sl,
                "tp_price": tp
            }

        # LOGIC: Sell if Current Price < Average - 5 points
        elif current_price < avg_price - 5:
             sl = current_price * (1 + self.config.STOP_LOSS_PCT/100)
             tp = current_price * (1 - self.config.TAKE_PROFIT_PCT/100)

             return {
                 "action": "SELL",
                 "quantity": self.config.DEFAULT_QUANTITY,
                 "sl_price": sl,
                 "tp_price": tp
             }

        return None


# ============================================================================
# --- SECTION 6: MAIN RUNNER ---
# ============================================================================

def run_bot():
    print("🚀 STARTING GLOBAL PAPER TRADING BOT...")

    # 1. Auth
    fyers, client_id, token = get_fyers_instance()

    # 2. Init Components
    market_data = MarketDataService(client_id, token, BotConfig.SYMBOL)
    paper_exchange = PaperExchange(BotConfig.PAPER_BALANCE)
    strategy = CustomStrategy(BotConfig)

    # 3. Connect Data
    market_data.connect()

    # 4. Main Loop
    print(f"👀 Monitoring {BotConfig.SYMBOL} for signals...")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1) # Check every 1 second

            current_price = market_data.get_ltp()
            if current_price == 0:
                print("Waiting for data...", end="\r")
                continue

            # Update active positions (Check SL/TP)
            paper_exchange.update(current_price)

            # Consult Strategy
            history = market_data.get_history()
            positions = paper_exchange.active_positions

            decision = strategy.on_tick(current_price, history, positions)

            if decision:
                action = decision['action']
                qty = decision['quantity']
                sl = decision['sl_price']
                tp = decision['tp_price']

                # Execute Trade
                paper_exchange.open_position(BotConfig.SYMBOL, current_price, qty, action, sl, tp)

            # Periodic Status Log (Every ~10s or based on conditions)
            # print(f"LTP: {current_price} | Positions: {len(positions)}", end="\r")

    except KeyboardInterrupt:
        print("\n🛑 Bot Stopped by User.")
        print(f"Final Balance: {paper_exchange.balance:.2f}")


def main():
    parser = argparse.ArgumentParser(description="Global Paper Trading Bot")
    parser.add_argument("mode", choices=["setup", "run"], help="Mode: setup or run")
    parser.add_argument("--client_id", help="Fyers App ID")
    parser.add_argument("--secret_key", help="Fyers Secret Key")
    parser.add_argument("--redirect_url", help="Redirect URL")

    args = parser.parse_args()

    if args.mode == "setup":
        setup_credentials(args.client_id, args.secret_key, args.redirect_url)
    elif args.mode == "run":
        run_bot()

if __name__ == "__main__":
    main()
