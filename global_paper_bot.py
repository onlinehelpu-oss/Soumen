"""
GLOBAL REAL-TIME PAPER TRADING BOT FRAMEWORK
============================================

**Description:**
This script provides a robust framework for real-time paper trading using the Fyers API.
It handles:
1. Authentication (Auto-login / Token management)
2. Real-time Market Data (WebSocket)
3. Paper Trading Execution (Virtual Balance, Positions, P&L, Stop Loss, Take Profit)
4. Custom Strategy Implementation (Green Hammer / Green Pinbar Strategy)

**Usage:**
1. Setup Credentials (Run Once):
   python global_paper_bot.py setup --client_id YOUR_ID --secret_key YOUR_KEY --redirect_url YOUR_URL

2. Run the Bot:
   python global_paper_bot.py run
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
import math
import re
from urllib.parse import urlparse, parse_qs, quote
from datetime import datetime as dt, timedelta, time as dttime
from typing import Optional, Tuple, Dict, Any, List, Set
import pandas as pd
import pytz

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
    TRADE_LOG_FILE = "trade_log.csv"

    # --- Strategy Parameters (Green Hammer) ---
    TIMEFRAME_MIN = 15
    R_MULTIPLIER = 1.0
    REGIME_EMA_PERIOD = 26

    # Candle Geometry
    LOWER_WICK_MIN = 50
    LOWER_WICK_MAX = 80
    BODY_MIN = 5
    BODY_MAX = 30
    UPPER_WICK_MAX = 25
    MIN_RANGE_PCT = 0.0015

    # Entry Rules
    ENTRY_BUFFER = 0.05

    # Timing
    ENTRY_CUTOFF = dttime(15, 0)      # NSE
    EXIT_ALL_TIME = dttime(15, 9)     # NSE
    ENTRY_CUTOFF_MCX = dttime(22, 0)  # MCX
    EXIT_ALL_TIME_MCX = dttime(22, 50)# MCX

    # Capital
    PAPER_BALANCE = 100000.0
    ALLOCATION_AMOUNT = 16000     # Per trade allocation for NSE
    MCX_LOT_MULTIPLIER = 1        # Fixed lots for MCX

    # --- Watchlist ---
    SYMBOLS = [
        'NSE:ADANIENT-EQ', 'NSE:ADANIPORTS-EQ', 'NSE:APOLLOHOSP-EQ', 'NSE:ASIANPAINT-EQ', 'NSE:AXISBANK-EQ',
        'NSE:BAJAJ-AUTO-EQ', 'NSE:BAJFINANCE-EQ', 'NSE:BAJAJFINSV-EQ', 'NSE:BPCL-EQ', 'NSE:BHARTIARTL-EQ',
        'NSE:BRITANNIA-EQ', 'NSE:CIPLA-EQ', 'NSE:COALINDIA-EQ', 'NSE:DIVISLAB-EQ', 'NSE:DRREDDY-EQ',
        'NSE:EICHERMOT-EQ', 'NSE:GRASIM-EQ', 'NSE:HCLTECH-EQ', 'NSE:HDFCBANK-EQ', 'NSE:HDFCLIFE-EQ',
        'NSE:HEROMOTOCO-EQ', 'NSE:HINDALCO-EQ', 'NSE:HINDUNILVR-EQ', 'NSE:ICICIBANK-EQ', 'NSE:ITC-EQ',
        'NSE:INFY-EQ', 'NSE:JSWSTEEL-EQ', 'NSE:KOTAKBANK-EQ', 'NSE:LTIM-EQ', 'NSE:LT-EQ',
        'NSE:M&M-EQ', 'NSE:MARUTI-EQ', 'NSE:NTPC-EQ', 'NSE:NESTLEIND-EQ', 'NSE:ONGC-EQ',
        'NSE:POWERGRID-EQ', 'NSE:RELIANCE-EQ', 'NSE:SBILIFE-EQ', 'NSE:SBIN-EQ', 'NSE:SIEMENS-EQ',
        'NSE:SUNPHARMA-EQ', 'NSE:TCS-EQ', 'NSE:TATACONSUM-EQ', 'NSE:TATASTEEL-EQ',
        'NSE:TECHM-EQ', 'NSE:TITAN-EQ', 'NSE:UPL-EQ',
        # MCX Futures (Make sure these are current expiries or generic if supported)
        # 'MCX:CRUDEOILM24MARFUT', 'MCX:SILVERMIC24APR24FUT'
    ]

    MCX_LOTS = {
        "SILVERMIC": 1,
        "CRUDEOILM": 1,
        "NATGASMINI": 1,
    }


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
    """Manages WebSocket connection and real-time price updates for multiple symbols."""
    def __init__(self, client_id, access_token, symbols: List[str]):
        self.client_id = client_id
        self.access_token = access_token
        self.symbols = symbols
        self.ltp_store = {} # symbol -> ltp
        self.fyers_ws = None
        self.lock = threading.Lock()

        # Initialize LTPs with 0
        for s in symbols:
            self.ltp_store[s] = 0.0

    def connect(self):
        ws_token = f"{self.client_id}:{self.access_token}"

        def on_connect():
            print(f"✅ WebSocket Connected. Subscribing to {len(self.symbols)} symbols.")
            self.fyers_ws.subscribe(symbols=self.symbols)

        def on_message(msg):
            # Handle tick data
            if isinstance(msg, list) and len(msg) > 0:
                for tick in msg:
                    if 'ltp' in tick and 'symbol' in tick:
                        with self.lock:
                            self.ltp_store[tick['symbol']] = tick['ltp']

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

    def get_ltp(self, symbol: str) -> float:
        with self.lock:
            return self.ltp_store.get(symbol, 0.0)

    def get_all_ltp(self) -> Dict[str, float]:
        with self.lock:
            return self.ltp_store.copy()


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
            "Datetime": dt.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Symbol": position.symbol,
            "Side": position.side,
            "Entry": position.entry_price,
            "Exit": price,
            "PnL": pnl,
            "Reason": reason
        }
        self.trade_log.append(record)

        # Save to file
        pd.DataFrame([record]).to_csv(BotConfig.TRADE_LOG_FILE, mode='a', header=not os.path.exists(BotConfig.TRADE_LOG_FILE), index=False)

        color = "🟢" if pnl > 0 else "🔴"
        print(f"{color} CLOSE {position.side}: {position.symbol} @ {price:.2f} | P&L: {pnl:.2f} | Reason: {reason}")
        print(f"💰 Current Balance: {self.balance:.2f}")

    def update(self, current_prices: Dict[str, float]):
        """Checks SL/TP for all active positions."""
        for pos in list(self.active_positions):
            current_price = current_prices.get(pos.symbol, 0)
            if current_price == 0: continue

            # Update running P&L
            if pos.side == "BUY":
                pos.pnl = (current_price - pos.entry_price) * pos.quantity
                if current_price > pos.peak_price:
                    pos.peak_price = current_price

                if current_price <= pos.stop_loss:
                    self.close_position(pos, current_price, "STOP_LOSS")
                elif current_price >= pos.take_profit:
                    self.close_position(pos, current_price, "TAKE_PROFIT")

            # (Short logic if needed)


# ============================================================================
# --- SECTION 5: CUSTOM STRATEGY SECTION (GREEN HAMMER) ---
# ============================================================================

class CustomStrategy:
    """
    Green-Hammer / Green-Pinbar Strategy logic.
    """
    def __init__(self, config: BotConfig, fyers_model):
        self.config = config
        self.fyers = fyers_model

        # State Management
        self.bars = {}          # (symbol, candle_start_time) -> {o, h, l, c}
        self.regime_emas = {}   # symbol -> current EMA value
        self.day_lows = {}      # symbol -> day low value
        self.triggers = {}      # symbol -> {low, high, active_start, triggered}
        self.processed_candles = set()

        # Helpers
        self.TICK_SIZE = 0.05

    def round_to_tick(self, x):
        return round(round(x / self.TICK_SIZE) * self.TICK_SIZE, 2)

    def candle_start(self, t: dt) -> dt:
        return t.replace(second=0, microsecond=0) - timedelta(minutes=t.minute % self.config.TIMEFRAME_MIN)

    # --- Initialization ---
    def fetch_initial_data(self):
        print("\n🔄 Strategy: Fetching Initial Data (EMAs & Day Lows)...")
        # 1. Fetch EMAs
        lookback_days = math.ceil((self.config.REGIME_EMA_PERIOD * self.config.TIMEFRAME_MIN * 3) / (60 * 6.0))
        lookback_days = max(lookback_days, 5)
        start_date = dt.now().date() - timedelta(days=lookback_days)
        end_date = dt.now().date()

        for sym in self.config.SYMBOLS:
            try:
                time.sleep(0.1) # Throttle
                data = {
                    "symbol": sym, "resolution": str(self.config.TIMEFRAME_MIN),
                    "date_format": "1", "range_from": start_date.strftime("%Y-%m-%d"),
                    "range_to": end_date.strftime("%Y-%m-%d"), "cont_flag": "1"
                }
                resp = self.fyers.history(data)
                candles = resp.get("candles", [])
                if candles:
                     df = pd.DataFrame(candles, columns=["ts", "o", "h", "l", "c", "v"])
                     df["ema"] = df["c"].ewm(span=self.config.REGIME_EMA_PERIOD, adjust=False).mean()
                     self.regime_emas[sym] = df["ema"].iloc[-1]
            except Exception as e:
                print(f"  ❌ Error fetching history for {sym}: {e}")

        # 2. Fetch Day Lows
        try:
            # Batch fetch
            chunk_size = 50
            for i in range(0, len(self.config.SYMBOLS), chunk_size):
                chunk = self.config.SYMBOLS[i:i+chunk_size]
                resp = self.fyers.quotes({"symbols": ",".join(chunk)})
                if resp.get("d"):
                    for item in resp["d"]:
                        sym = item.get("n")
                        low = item.get("v", {}).get("low_price")
                        if low: self.day_lows[sym] = float(low)
        except Exception as e:
            print(f"  ❌ Error fetching quotes: {e}")

        print(f"✅ Strategy Initialized. Tracking {len(self.regime_emas)} EMAs and {len(self.day_lows)} Day Lows.")

    def get_lot_size(self, symbol: str) -> int:
        if symbol.endswith("-EQ"): return 1
        base = symbol.split(':')[1]
        for mcx_base, lot in self.config.MCX_LOTS.items():
            if base.startswith(mcx_base): return lot
        return 1

    def is_bullish_hammer_candle(self, o, h, l, c, prev_o, prev_c, ignore_prev=False):
        # 1. Must be Green
        if c <= o: return False

        # 2. Prev must be Red (unless ignored)
        if not ignore_prev:
            if prev_c >= prev_o: return False

        # 3. Geometry
        if c == 0 or h <= l: return False
        total_range = h - l
        if (total_range / max(abs(c), 1e-9)) < self.config.MIN_RANGE_PCT: return False

        upper_wick_pct = ((h - c) / total_range) * 100
        body_pct = ((c - o) / total_range) * 100
        lower_wick_pct = ((o - l) / total_range) * 100

        return (
            (self.config.LOWER_WICK_MIN <= lower_wick_pct <= self.config.LOWER_WICK_MAX) and
            (self.config.BODY_MIN <= body_pct <= self.config.BODY_MAX) and
            (0 <= upper_wick_pct <= self.config.UPPER_WICK_MAX)
        )

    def on_tick(self, symbol: str, ltp: float, active_positions: List[PaperPosition]) -> Optional[Dict]:
        """
        Main logic called per symbol per tick.
        """
        if ltp == 0: return None
        now = dt.now()

        # 1. Update Day Low
        curr_low = self.day_lows.get(symbol, float('inf'))
        if ltp < curr_low: self.day_lows[symbol] = ltp

        # 2. Manage Candle Building
        cstart = self.candle_start(now)
        key = (symbol, cstart)
        bar = self.bars.get(key)

        if not bar:
            self.bars[key] = bar = {"o": ltp, "h": ltp, "l": ltp, "c": ltp}
        else:
            bar["h"] = max(bar["h"], ltp)
            bar["l"] = min(bar["l"], ltp)
            bar["c"] = ltp

        # 3. Check Signal (At Candle Close)
        # Note: We check if current time is just past the candle end
        next_cstart = cstart + timedelta(minutes=self.config.TIMEFRAME_MIN)

        # Logic to process "completed" candle
        # We can't easily detect "exact" close in loop, so we assume if we see a tick
        # for a NEW candle (time >= next_cstart), the previous one (key) is done.
        # However, to be robust, we'll check if the CURRENT time is close to the end of the candle interval
        # But provided logic used: `if tick_time >= cstart + minutes - 1 sec`.

        is_candle_complete_time = now >= (next_cstart - timedelta(seconds=1))

        if is_candle_complete_time and key not in self.processed_candles:
            self.processed_candles.add(key)

            # Update EMA
            curr_ema = self.regime_emas.get(symbol)
            if curr_ema:
                k = 2 / (self.config.REGIME_EMA_PERIOD + 1)
                new_ema = (bar["c"] * k) + (curr_ema * (1 - k))
                self.regime_emas[symbol] = new_ema
            else:
                new_ema = bar["c"]
                self.regime_emas[symbol] = new_ema

            # Check Position existence
            has_pos = any(p.symbol == symbol for p in active_positions)
            if not has_pos:
                # Get Prev Bar
                prev_cstart = cstart - timedelta(minutes=self.config.TIMEFRAME_MIN)
                prev_bar = self.bars.get((symbol, prev_cstart))

                # Check Context
                is_above_ema = bar["c"] > new_ema
                cached_day_low = self.day_lows.get(symbol, bar["l"])
                is_at_day_low = bar["l"] <= (cached_day_low + 0.01)

                if is_above_ema or is_at_day_low:
                    # Session Start logic
                    is_session_start = False
                    if symbol.startswith("MCX") and cstart.hour == 9 and cstart.minute == 0: is_session_start = True
                    elif not symbol.startswith("MCX") and cstart.hour == 9 and cstart.minute == 15: is_session_start = True

                    p_o, p_c = (prev_bar["o"], prev_bar["c"]) if prev_bar else (0, 0)

                    if self.is_bullish_hammer_candle(bar["o"], bar["h"], bar["l"], bar["c"], p_o, p_c, ignore_prev=is_session_start):
                         print(f"[{now.strftime('%H:%M:%S')}] 🎯 SIGNAL {symbol}: Green Hammer detected. Watching for Breakout > {bar['h']}")
                         self.triggers[symbol] = {
                             "low": bar["l"],
                             "high": bar["h"],
                             "active_start": next_cstart,
                             "triggered": False
                         }

        # 4. Check Trigger (Breakout)
        trigger = self.triggers.get(symbol)
        if trigger:
            # Expiry
            if now >= trigger["active_start"] + timedelta(minutes=self.config.TIMEFRAME_MIN):
                self.triggers.pop(symbol, None)
            elif now >= trigger["active_start"] and not trigger["triggered"]:
                # Cutoff Time
                cutoff = self.config.ENTRY_CUTOFF_MCX if symbol.startswith("MCX") else self.config.ENTRY_CUTOFF
                if now.time() < cutoff:
                    breakout_level = self.round_to_tick(trigger["high"] + self.config.ENTRY_BUFFER)

                    if ltp > breakout_level:
                        # ENTRY!
                        lot_size = self.get_lot_size(symbol)

                        # Qty Logic
                        if symbol.startswith("MCX"):
                            qty = self.config.MCX_LOT_MULTIPLIER * lot_size # Total units
                        else:
                            # Alloc logic
                            shares = int(self.config.ALLOCATION_AMOUNT / ltp) if ltp > 0 else 0
                            qty = max(lot_size, (shares // lot_size) * lot_size)

                        sl = trigger["low"]
                        risk = ltp - sl
                        tgt = ltp + (self.config.R_MULTIPLIER * risk)

                        self.triggers.pop(symbol, None) # Remove trigger

                        return {
                            "action": "BUY",
                            "quantity": qty,
                            "sl_price": sl,
                            "tp_price": tgt
                        }

        # 5. Check Time-Based Exits (Exit All)
        # Note: This is checked every tick, but framework only closes open positions if requested
        # We can implement a check in PaperExchange or here.
        # Here we can return a SELL signal for ALL positions if time matches?
        # Actually, framework calls update() which manages SL/TP. Time exit needs explicit handling.
        exit_time = self.config.EXIT_ALL_TIME_MCX if symbol.startswith("MCX") else self.config.EXIT_ALL_TIME
        if now.time() >= exit_time:
             # Find if we have position
             pos = next((p for p in active_positions if p.symbol == symbol), None)
             if pos:
                 return {"action": "EXIT_TIME", "quantity": 0, "sl_price":0, "tp_price":0} # Signal to close

        return None


# ============================================================================
# --- SECTION 6: MAIN RUNNER ---
# ============================================================================

def run_bot():
    print("🚀 STARTING GLOBAL PAPER TRADING BOT (Green Hammer Strategy)...")

    # 1. Auth
    fyers, client_id, token = get_fyers_instance()

    # 2. Init Components
    market_data = MarketDataService(client_id, token, BotConfig.SYMBOLS)
    paper_exchange = PaperExchange(BotConfig.PAPER_BALANCE)
    strategy = CustomStrategy(BotConfig, fyers)

    # 3. Strategy Init
    strategy.fetch_initial_data()

    # 4. Connect Data
    market_data.connect()

    # 5. Main Loop
    print(f"👀 Monitoring {len(BotConfig.SYMBOLS)} symbols...")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1) # Loop interval

            # Get Snapshot
            current_prices = market_data.get_all_ltp()

            # Update Exchange (SL/TP)
            paper_exchange.update(current_prices)

            # Strategy Logic for each symbol
            active_positions = paper_exchange.active_positions

            for symbol in BotConfig.SYMBOLS:
                ltp = current_prices.get(symbol, 0)
                if ltp == 0: continue

                decision = strategy.on_tick(symbol, ltp, active_positions)

                if decision:
                    action = decision['action']

                    if action == "BUY":
                         # Check if already open to prevent dups (Strategy logic might already check, but safety)
                         if not any(p.symbol == symbol for p in active_positions):
                             paper_exchange.open_position(
                                 symbol, ltp, decision['quantity'], "BUY",
                                 decision['sl_price'], decision['tp_price']
                             )

                    elif action == "EXIT_TIME":
                         pos = next((p for p in active_positions if p.symbol == symbol), None)
                         if pos:
                             paper_exchange.close_position(pos, ltp, "TIME_EXIT")

    except KeyboardInterrupt:
        print("\n🛑 Bot Stopped by User.")
        print(f"Final Balance: {paper_exchange.balance:.2f}")


def main():
    parser = argparse.ArgumentParser(description="Global Paper Trading Bot")
    parser.add_argument("mode", nargs='?', choices=["setup", "run"], help="Mode: setup or run")
    parser.add_argument("--client_id", help="Fyers App ID")
    parser.add_argument("--secret_key", help="Fyers Secret Key")
    parser.add_argument("--redirect_url", help="Redirect URL")

    args = parser.parse_args()

    mode = args.mode

    if not mode:
        print("\n--- Global Paper Trading Bot ---")
        print("1. Setup Credentials")
        print("2. Run Bot")
        choice = input("Enter choice (1 or 2): ").strip()

        if choice == '1':
            mode = 'setup'
        elif choice == '2':
            mode = 'run'
        else:
            print("Invalid choice. Exiting.")
            sys.exit(1)

    if mode == "setup":
        app_id = args.client_id or input("Enter Fyers App ID: ").strip()
        secret_key = args.secret_key or input("Enter Secret Key: ").strip()
        redirect_url = args.redirect_url or input("Enter Redirect URL: ").strip()
        setup_credentials(app_id, secret_key, redirect_url)
    elif mode == "run":
        run_bot()

if __name__ == "__main__":
    main()
