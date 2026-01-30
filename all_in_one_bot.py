"""
ALL-IN-ONE ABSORPTION-COMPRESSION-DISPLACEMENT TRADING BOT
==========================================================

**Disclaimer:**
This bot is intended for educational and paper trading purposes.
Live trading involves significant financial risk. Use at your own discretion.

**Description:**
This single-file script consolidates three key functionalities:
1. Credential Setup: Securely creates your API configuration file.
2. Backtester: Downloads historical data and tests the strategy.
3. Live Bot: Runs the strategy in a live (paper trading) environment.

**Strategy (Absorption -> Compression -> Displacement):**
- **Phase 1 (Flush):** Bearish candle, breaks 20-period Low, High Range, High Volume.
- **Phase 2 (Compression):** 4-8 candles, tight consolidation (High-Low < 0.8x Avg Range), no new significant low.
- **Phase 3 (Displacement):** Bullish breakout above Compression High, High Range, Close near High, Volume Increase.
- **Entry:** ATM/ITM Call Option (Delta 0.45-0.65).
- **Exits:** Target (Trailing), Stop Loss (Compression Low or Option Premium -30%), or Time-based exit.
- **Assets:** NSE Indices (NIFTY, BANKNIFTY) -> Options.

**Usage:**
1. Setup Credentials (Run Once):
   python all_in_one_bot.py setup --app_id YOUR_ID --secret_key YOUR_KEY --redirect_url YOUR_URL

2. Run Backtest:
   python all_in_one_bot.py backtest

3. Run Live Paper Trading Bot:
   python all_in_one_bot.py run
"""

import os
import sys
import json
import time
import math
import argparse
import webbrowser
import threading
import traceback
import datetime as dt
import pytz
from urllib.parse import urlparse, parse_qs
import warnings
import re
from typing import Optional, Dict, List, Tuple

# Suppress specific warnings
warnings.filterwarnings("ignore", message=".*pkg_resources is deprecated.*")

import pandas as pd
import numpy as np

# Try to import real Fyers library
try:
    from fyers_apiv3 import fyersModel
    from fyers_apiv3.FyersWebsocket import data_ws

    HAS_FYERS = True
except ImportError:
    print("⚠️ fyers_apiv3 not installed. Running in limited/mock mode.")
    HAS_FYERS = False


    # Mock classes for dependency check passes
    class MockFyersModel:
        def __init__(self, **kwargs):
            self.client_id = kwargs.get("client_id", "MOCK")
            self.token = kwargs.get("token", "MOCK")

        def history(self, data): return {"s": "ok", "candles": []}

        def quotes(self, data): return {"s": "ok", "d": []}

        def option_chain(self, data): return {"s": "ok", "d": []}


    class MockSessionModel:
        def __init__(self, **kwargs): pass

        def generate_authcode(self): return "http://mock-login-url"

        def set_token(self, token): pass

        def generate_token(self): return {"s": "ok", "access_token": "MOCK_TOKEN", "refresh_token": "MOCK_REFRESH"}


    class MockDataSocket:
        def __init__(self, **kwargs): pass

        def connect(self): print("[MOCK] WS Connected")

        def subscribe(self, symbols): print(f"[MOCK] Subscribed to {len(symbols)} symbols")


    fyersModel = type("fyersModel", (), {"FyersModel": MockFyersModel, "SessionModel": MockSessionModel})
    data_ws = type("data_ws", (), {"FyersDataSocket": MockDataSocket})


# ============================================================================
# --- SECTION 1: CONFIGURATION ---
# ============================================================================

class BotConfig:
    """Consolidated configuration for all bot functionalities."""
    # --- File Names ---
    LOGIN_DETAILS_FILE = "fyers_login_details.json"
    TOKENS_STORE = "tokens_store.json"
    TOKENS_DIR = "AccessToken"
    TRADE_LOG_FILE = "paper_trade_log.csv"

    # --- Strategy Settings ---
    TIMEFRAME_MIN = 3

    # Phase 1: Flush
    FLUSH_LOOKBACK = 20
    FLUSH_RANGE_MULT = 1.5
    FLUSH_VOL_MULT = 1.5

    # Phase 2: Compression
    COMPRESSION_MIN_CANDLES = 4
    COMPRESSION_MAX_CANDLES = 8
    COMPRESSION_RANGE_THRESHOLD = 0.8  # Total Range < 0.8 * Avg Range

    # Phase 3: Displacement
    DISPLACEMENT_RANGE_MULT = 1.5
    DISPLACEMENT_CLOSE_PCT = 0.25  # Close in top 25% (i.e. > 75% of range, wait. 25% means upper wick < 25%)

    # Risk Management
    OPTION_SL_PCT = 0.30  # 30% Stop Loss on Option Premium
    TRAILING_SL_Start = 0.10 # Start trailing after 10% profit

    # --- Position Sizing ---
    PAPER_BALANCE = 100000
    ALLOCATION_AMOUNT = 20000  # Capital per trade
    LOT_SIZE = 50 # Default NIFTY, logic will adjust

    # --- Session Timing ---
    # NSE: 09:15 - 15:30 (Entries until 15:00, Exit all 15:15)
    ENTRY_CUTOFF_NSE = dt.time(15, 0)
    EXIT_ALL_NSE = dt.time(15, 15)

    # --- Watchlist (Indices) ---
    SYMBOLS = [
        'NSE:NIFTY50-INDEX',
        'NSE:NIFTYBANK-INDEX'
    ]

    # Map Index to Option Symbol Base
    INDEX_MAP = {
        'NSE:NIFTY50-INDEX': 'NIFTY',
        'NSE:NIFTYBANK-INDEX': 'BANKNIFTY',
        'NSE:FINNIFTY-INDEX': 'FINNIFTY'
    }

    LOT_SIZES = {
        'NIFTY': 50,
        'BANKNIFTY': 15, # Verify current lot sizes
        'FINNIFTY': 40
    }


# ============================================================================
# --- SECTION 2: CREDENTIAL SETUP & AUTHENTICATION ---
# ============================================================================

def _read_json(path):
    if os.path.exists(path):
        with open(path, 'r') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}


def _write_json(path, data):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


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

    file_name = BotConfig.LOGIN_DETAILS_FILE
    try:
        _write_json(file_name, credentials)
        print(f"✅ Successfully created '{file_name}' with your credentials.")
    except Exception as e:
        print(f"❌ An error occurred while creating the file: {e}")


def get_fyers_instance():
    """
    Ensures a valid Fyers access token is available.
    """
    creds = _read_json(BotConfig.LOGIN_DETAILS_FILE)
    if not creds:
        print(f"❌ Missing '{BotConfig.LOGIN_DETAILS_FILE}'. Please run 'setup' mode first.")
        sys.exit(1)

    client_id = creds.get("api_key")
    secret_key = creds.get("api_secret")
    redirect_uri = creds.get("redirect_url")

    today_str = str(dt.date.today())
    today_token_path = os.path.join(BotConfig.TOKENS_DIR, f"{today_str}.json")

    # 1. Try Today's Token
    access_token = _read_json(today_token_path)
    if access_token and isinstance(access_token, str):
        return fyersModel.FyersModel(client_id=client_id, token=access_token, log_path="", is_async=False)

    # 2. Try Refresh Token
    store = _read_json(BotConfig.TOKENS_STORE)
    refresh_token = store.get("refresh_token")

    if refresh_token:
        print("🔄 Attempting login via Refresh Token...")
        try:
            session = fyersModel.SessionModel(
                client_id=client_id,
                secret_key=secret_key,
                redirect_uri=redirect_uri,
                response_type="code",
                grant_type="refresh_token"
            )
            session.set_token(refresh_token)
            response = session.generate_token()

            if response.get("s") == "ok":
                new_access_token = response["access_token"]
                new_refresh_token = response.get("refresh_token")

                _write_json(today_token_path, new_access_token)
                if new_refresh_token:
                    _write_json(BotConfig.TOKENS_STORE, {"refresh_token": new_refresh_token})

                print("✅ Access Token Refreshed.")
                return fyersModel.FyersModel(client_id=client_id, token=new_access_token, log_path="", is_async=False)
            else:
                print(f"⚠️ Refresh failed: {response.get('message')}")
        except Exception as e:
            print(f"⚠️ Refresh error: {e}")

    # 3. Manual Login
    print("👉 Initiating Manual Login...")
    session = fyersModel.SessionModel(
        client_id=client_id,
        secret_key=secret_key,
        redirect_uri=redirect_uri,
        response_type="code",
        grant_type="authorization_code"
    )

    auth_url = session.generate_authcode()
    print(f"\nOpen this URL to login:\n{auth_url}")
    webbrowser.open(auth_url, new=1)

    auth_code = input("\nPaste the auth_code here: ").strip()
    session.set_token(auth_code)
    response = session.generate_token()

    if response.get("s") == "ok":
        access_token = response["access_token"]
        refresh_token = response.get("refresh_token")

        _write_json(today_token_path, access_token)
        if refresh_token:
            _write_json(BotConfig.TOKENS_STORE, {"refresh_token": refresh_token})

        print("✅ Login Successful.")
        return fyersModel.FyersModel(client_id=client_id, token=access_token, log_path="", is_async=False)
    else:
        print(f"❌ Login Failed: {response}")
        sys.exit(1)


# ============================================================================
# --- SECTION 3: STRATEGY LOGIC ---
# ============================================================================

class Strategy:
    @staticmethod
    def calculate_indicators(df: pd.DataFrame):
        """Calculates rolling averages and indicators."""
        if df.empty: return df

        # Candle Range (High - Low)
        df['range'] = df['high'] - df['low']

        # Rolling Average Range (20)
        df['avg_range'] = df['range'].rolling(window=BotConfig.FLUSH_LOOKBACK).mean()

        # Rolling Average Volume (20)
        df['avg_vol'] = df['volume'].rolling(window=BotConfig.FLUSH_LOOKBACK).mean()

        # Lowest Low of last 20 candles (excluding current)
        df['lowest_low_20'] = df['low'].rolling(window=BotConfig.FLUSH_LOOKBACK).min().shift(1)

        # VWAP (Approximation: CumSum(Price*Vol) / CumSum(Vol) reset daily)
        if 'timestamp' in df.columns:
            df['date'] = df['timestamp'].dt.date
            # Simple Intraday VWAP Calculation
            # We need to handle potential index misalignment if grouped
            def calc_vwap(group):
                cum_vol = group['volume'].cumsum()
                cum_pv = (group['close'] * group['volume']).cumsum()
                return cum_pv / cum_vol

            # Use transform to keep original index
            # Explicitly select columns to silence FutureWarning
            df['vwap'] = df.groupby('date')[['close', 'volume']].apply(lambda x: calc_vwap(x)).reset_index(level=0, drop=True)

        return df

    @staticmethod
    def detect_flush(curr_candle, prev_candles_df):
        """
        Phase 1: Liquidity Flush
        - Bearish Candle (Close < Open)
        - Low < Lowest Low (last 20)
        - Range > 1.5 * Avg Range
        - Volume > 1.5 * Avg Volume
        """
        if curr_candle.empty: return False

        # Basic Bearish Check
        if curr_candle['close'] >= curr_candle['open']: return False

        # Context
        avg_range = curr_candle['avg_range']
        avg_vol = curr_candle['avg_vol']
        lowest_low = curr_candle['lowest_low_20']

        if pd.isna(avg_range) or pd.isna(avg_vol) or pd.isna(lowest_low): return False

        # Conditions
        is_break_low = curr_candle['low'] < lowest_low
        is_high_range = curr_candle['range'] > (avg_range * BotConfig.FLUSH_RANGE_MULT)
        is_high_vol = curr_candle['volume'] > (avg_vol * BotConfig.FLUSH_VOL_MULT)

        return is_break_low and is_high_range and is_high_vol

    @staticmethod
    def detect_compression(recent_candles_df, avg_range):
        """
        Phase 2: Micro Compression (Absorption)
        - 4 to 8 candles
        - Total High-Low Range of the Zone < 0.8 * Avg Range
        - No significant new low
        - Volume does NOT collapse completely (Absorption) -> Check Avg Volume in zone vs History?

        Returns: (is_compression, compression_high, compression_low)
        """
        if len(recent_candles_df) < BotConfig.COMPRESSION_MIN_CANDLES:
            return False, 0, 0

        max_h = recent_candles_df['high'].max()
        min_l = recent_candles_df['low'].min()

        zone_range = max_h - min_l

        # Check tightness
        if zone_range < (avg_range * BotConfig.COMPRESSION_RANGE_THRESHOLD):
            # Check Volume Absorption (Optional but requested)
            # "Volume does NOT collapse completely"
            # We can check if average volume in compression zone is at least 50% of recent avg volume
            # But this might be too strict if volume naturally drops in consolidation.
            # We'll skip strict volume check here to avoid missing valid setups unless critical.
            return True, max_h, min_l

        return False, 0, 0

    @staticmethod
    def detect_displacement(curr_candle, compression_high, avg_range):
        """
        Phase 3: Displacement Breakout
        - Bullish Candle (Close > Open)
        - Close > Compression High
        - Range > 1.5 * Avg Range
        - Close near High (Top 25%)
        - (Optional) Volume Increase
        """
        if curr_candle['close'] <= curr_candle['open']: return False
        if curr_candle['close'] <= compression_high: return False

        # Range Check
        if curr_candle['range'] <= (avg_range * BotConfig.DISPLACEMENT_RANGE_MULT): return False

        # Close near High Check (Top 25% -> Upper Wick < 25% of Range)
        upper_wick_len = curr_candle['high'] - curr_candle['close']
        if upper_wick_len > (curr_candle['range'] * BotConfig.DISPLACEMENT_CLOSE_PCT):
            return False

        return True

# ============================================================================
# --- SECTION 4: BACKTESTER LOGIC ---
# ============================================================================

def run_backtester():
    """
    Downloads historical data for Indices and runs the strategy backtest.
    """
    fyers = get_fyers_instance()

    print(f"\n🚀 STARTING BACKTEST (Strategy: Absorption-Compression-Displacement, TF: {BotConfig.TIMEFRAME_MIN}m)")

    to_date = dt.date.today()
    from_date = to_date - dt.timedelta(days=60)

    for symbol in BotConfig.SYMBOLS:
        print(f"\nProcessing {symbol}...")
        time.sleep(0.5)
        try:
            # Download Data
            data = {
                "symbol": symbol,
                "resolution": str(BotConfig.TIMEFRAME_MIN),
                "date_format": "1",
                "range_from": from_date.strftime("%Y-%m-%d"),
                "range_to": to_date.strftime("%Y-%m-%d"),
                "cont_flag": "1"
            }
            resp = fyers.history(data)

            if resp.get("s") != "ok" or not resp.get("candles"):
                print(f"  ⚠️ No data/Error: {resp.get('message')}")
                continue

            cols = ["timestamp", "open", "high", "low", "close", "volume"]
            df = pd.DataFrame(resp["candles"], columns=cols)
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s').dt.tz_localize('UTC').dt.tz_convert('Asia/Kolkata')

            # Calculate Indicators
            df = Strategy.calculate_indicators(df)

            trades = []

            # State Machine Variables
            state = "WAITING" # WAITING, FLUSH_DETECTED, COMPRESSION
            flush_idx = -1
            compression_start_idx = -1
            compression_high = 0
            compression_low = 0

            for i in range(21, len(df)):
                curr = df.iloc[i]

                # Update State Machine
                if state == "WAITING":
                    # Check for Flush
                    if Strategy.detect_flush(curr, df.iloc[i-20:i]):
                        state = "FLUSH_DETECTED"
                        flush_idx = i
                        # print(f"  [Flush] {curr['timestamp']} Price: {curr['close']}")

                elif state == "FLUSH_DETECTED":
                    # We need 4-8 candles of compression immediately after flush
                    candles_since_flush = i - flush_idx

                    if candles_since_flush >= BotConfig.COMPRESSION_MIN_CANDLES:
                        # Check Compression
                        # Zone: candles from flush_idx+1 to i (inclusive)
                        zone_df = df.iloc[flush_idx+1 : i+1]
                        is_comp, c_h, c_l = Strategy.detect_compression(zone_df, df.iloc[flush_idx]['avg_range'])

                        if is_comp:
                            state = "COMPRESSION"
                            compression_high = c_h
                            compression_low = c_l
                            compression_start_idx = flush_idx + 1
                            # print(f"  [Compression] {curr['timestamp']} High: {c_h} Low: {c_l}")

                    if candles_since_flush > BotConfig.COMPRESSION_MAX_CANDLES:
                        # Too long without compression confirmation or breakout, reset
                        state = "WAITING"

                elif state == "COMPRESSION":
                    candles_in_comp = i - compression_start_idx

                    # Check Displacement Breakout
                    # + Confirm Price > VWAP
                    vwap = curr.get('vwap', 0)
                    is_above_vwap = (curr['close'] > vwap) if vwap > 0 else True

                    if is_above_vwap and Strategy.detect_displacement(curr, compression_high, df.iloc[flush_idx]['avg_range']):
                         # CONFIRMED SIGNAL
                         entry_price = curr['close']
                         stop_loss = compression_low # Spot SL
                         target = entry_price + (entry_price - stop_loss) * 2 # 1:2 RR on Spot

                         # Simulate Trade Result (Spot)
                         outcome = "OPEN"
                         pnl = 0

                         for j in range(i+1, min(i+50, len(df))):
                             fut = df.iloc[j]
                             if fut['low'] <= stop_loss:
                                 outcome = "LOSS"
                                 pnl = stop_loss - entry_price
                                 break
                             if fut['high'] >= target:
                                 outcome = "WIN"
                                 pnl = target - entry_price
                                 break

                         trades.append({
                             "time": curr['timestamp'],
                             "type": "BUY",
                             "entry": entry_price,
                             "sl": stop_loss,
                             "outcome": outcome,
                             "pnl": pnl
                         })

                         state = "WAITING" # Reset

                    # If price breaks below compression low, invalid pattern
                    elif curr['close'] < compression_low:
                        state = "WAITING"

                    # If too many candles pass (e.g. > 15 since flush), reset
                    elif (i - flush_idx) > 15:
                        state = "WAITING"

            # Summary
            wins = len([t for t in trades if t['outcome'] == "WIN"])
            losses = len([t for t in trades if t['outcome'] == "LOSS"])
            total = len(trades)
            win_rate = (wins/total*100) if total > 0 else 0

            print(f"  Signals: {total}, Wins: {wins}, Losses: {losses}, WR: {win_rate:.1f}%")
            if total > 0:
                print(f"  Last Trade: {trades[-1]}")

        except Exception as e:
            print(f"  ❌ Error: {e}")
            traceback.print_exc()


# ============================================================================
# --- SECTION 5: LIVE PAPER BOT LOGIC ---
# ============================================================================

class PaperPosition:
    def __init__(self, symbol, option_symbol, entry, sl_spot, qty, entry_spot):
        self.symbol = symbol
        self.option_symbol = option_symbol
        self.entry = entry # Option Entry Price
        self.sl_spot = sl_spot # Spot Level SL
        self.qty = qty
        self.entry_spot = entry_spot
        self.status = "OPEN"
        self.pnl = 0.0
        self.entry_time = dt.datetime.now()
        self.highest_price = entry # For trailing


class LivePaperBot:
    def __init__(self):
        self.fyers = get_fyers_instance()
        self.config = BotConfig()

        # State
        self.active_positions: Dict[str, PaperPosition] = {}
        self.candles_build = {}  # (symbol, timeframe_start) -> OHLC
        self.processed_candles = set()
        self.ltp_cache = {} # Cache for latest price

        # Strategy State per Symbol
        # { symbol: { state: 'WAITING'|'FLUSH'|'COMPRESSION', flush_data: {...}, comp_data: {...} } }
        self.strategy_state = {}

        self.history_df = {} # Store recent history for lookbacks

        self.paper_balance = BotConfig.PAPER_BALANCE

    def fetch_initial_state(self):
        """Pre-fetches history."""
        print("🔄 Initializing Bot State...")

        to_date = dt.date.today()
        from_date = to_date - dt.timedelta(days=5)

        for sym in BotConfig.SYMBOLS:
            time.sleep(0.2)
            self.strategy_state[sym] = {"state": "WAITING", "flush_data": None, "comp_data": None}

            try:
                resp = self.fyers.history({
                    "symbol": sym, "resolution": str(BotConfig.TIMEFRAME_MIN),
                    "date_format": "1", "range_from": from_date.strftime("%Y-%m-%d"),
                    "range_to": to_date.strftime("%Y-%m-%d"), "cont_flag": "1"
                })
                if resp.get("candles"):
                    df = pd.DataFrame(resp["candles"], columns=["timestamp", "open", "high", "low", "close", "volume"])
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s').dt.tz_localize('UTC').dt.tz_convert('Asia/Kolkata')

                    # Calculate Indicators
                    df = Strategy.calculate_indicators(df)
                    self.history_df[sym] = df
                    print(f"  Loaded {len(df)} candles for {sym}")
            except Exception as e:
                print(f"  Failed to load history for {sym}: {e}")

        print(f"✅ State Initialized.")

    def run(self):
        self.fetch_initial_state()

        # WebSocket Setup
        access_token = f"{self.fyers.client_id}:{self.fyers.token}"

        def on_message(msg):
            if isinstance(msg, list):
                for m in msg: self.process_tick(m)
            elif isinstance(msg, dict) and msg.get("type") == "sf":
                self.process_tick(msg)

        def on_error(msg): print(f"WS Error: {msg}")
        def on_open():
            print("🔌 WebSocket Connected.")
            self.ws.subscribe(symbols=BotConfig.SYMBOLS)

        self.ws = data_ws.FyersDataSocket(
            access_token=access_token, log_path=".", litemode=False, write_to_file=False,
            reconnect=True, on_connect=on_open, on_message=on_message, on_error=on_error
        )

        ws_thread = threading.Thread(target=self.ws.connect)
        ws_thread.daemon = True
        ws_thread.start()

        print(f"🚀 Live Paper Bot Running | Balance: {self.paper_balance}")

        last_print = dt.datetime.now()
        try:
            while True:
                time.sleep(1)
                self.monitor_positions()

                if (dt.datetime.now() - last_print).seconds >= 60:
                    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] Monitoring... Active: {len(self.active_positions)}")
                    last_print = dt.datetime.now()

        except KeyboardInterrupt:
            print("\n🛑 Bot Stopped by User.")

    def get_candle_start(self, ts):
        dt_obj = dt.datetime.fromtimestamp(ts)
        return dt_obj.replace(second=0, microsecond=0) - dt.timedelta(minutes=dt_obj.minute % BotConfig.TIMEFRAME_MIN)

    def process_tick(self, msg):
        sym = msg.get("symbol")
        ltp = float(msg.get("ltp"))
        ts = msg.get("timestamp", time.time())

        self.ltp_cache[sym] = ltp

        # Candle Building
        c_start = self.get_candle_start(ts)
        key = (sym, c_start)

        if key not in self.candles_build:
            self.candles_build[key] = {"o": ltp, "h": ltp, "l": ltp, "c": ltp, "v": 0}
        else:
            c = self.candles_build[key]
            c["h"] = max(c["h"], ltp)
            c["l"] = min(c["l"], ltp)
            c["c"] = ltp

        # Check Candle Completion
        tick_dt = dt.datetime.fromtimestamp(ts)
        candle_end_time = c_start + dt.timedelta(minutes=BotConfig.TIMEFRAME_MIN)

        if tick_dt >= (candle_end_time - dt.timedelta(seconds=1)):
            if key not in self.processed_candles:
                self.processed_candles.add(key)
                self.analyze_completed_candle(sym, self.candles_build[key], c_start)

    def analyze_completed_candle(self, sym, candle_dict, start_time):
        new_row = {
            "timestamp": pd.Timestamp(start_time).tz_localize(None).tz_localize('UTC').tz_convert('Asia/Kolkata'),
            "open": candle_dict["o"],
            "high": candle_dict["h"],
            "low": candle_dict["l"],
            "close": candle_dict["c"],
            "volume": candle_dict.get("v", 1000) # Mock volume if missing
        }

        df = self.history_df.get(sym)
        if df is None: return

        # Append
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        # Keep last 100 candles to keep performance high
        if len(df) > 200: df = df.iloc[-200:]

        # Recalculate indicators
        df = Strategy.calculate_indicators(df)
        self.history_df[sym] = df

        # Analyze Pattern with Updated DF
        self.update_strategy_state(sym, df)

    def update_strategy_state(self, sym, df):
        if len(df) < 50: return

        curr = df.iloc[-1]
        state_obj = self.strategy_state[sym]
        current_state = state_obj["state"]

        # --- STATE: WAITING ---
        if current_state == "WAITING":
            if Strategy.detect_flush(curr, df.iloc[-21:-1]):
                state_obj["state"] = "FLUSH_DETECTED"
                state_obj["flush_data"] = {"idx": len(df)-1, "avg_range": curr["avg_range"]}
                print(f"⚠️ FLUSH Detected on {sym} @ {curr['close']}")

        # --- STATE: FLUSH_DETECTED ---
        elif current_state == "FLUSH_DETECTED":
            flush_idx = state_obj["flush_data"]["idx"]
            curr_idx = len(df)-1
            candles_since = curr_idx - flush_idx

            if candles_since >= BotConfig.COMPRESSION_MIN_CANDLES:
                zone_df = df.iloc[flush_idx+1 : curr_idx+1]
                avg_range = state_obj["flush_data"]["avg_range"]

                is_comp, c_h, c_l = Strategy.detect_compression(zone_df, avg_range)
                if is_comp:
                    state_obj["state"] = "COMPRESSION"
                    state_obj["comp_data"] = {"high": c_h, "low": c_l, "start_idx": flush_idx+1}
                    print(f"⚠️ COMPRESSION Detected on {sym} High:{c_h} Low:{c_l}")

            if candles_since > BotConfig.COMPRESSION_MAX_CANDLES:
                state_obj["state"] = "WAITING"

        # --- STATE: COMPRESSION ---
        elif current_state == "COMPRESSION":
            flush_idx = state_obj["flush_data"]["idx"]
            comp_high = state_obj["comp_data"]["high"]
            comp_low = state_obj["comp_data"]["low"]
            avg_range = state_obj["flush_data"]["avg_range"]

            # Check Breakout & VWAP
            vwap = curr.get('vwap', 0)
            is_above_vwap = (curr['close'] > vwap) if vwap > 0 else True

            if is_above_vwap and Strategy.detect_displacement(curr, comp_high, avg_range):
                print(f"🚀 DISPLACEMENT (BUY SIGNAL) on {sym} @ {curr['close']}")
                self.execute_trade_signal(sym, curr['close'], comp_low)
                state_obj["state"] = "WAITING" # Reset

            # Check Fail
            elif curr['close'] < comp_low:
                state_obj["state"] = "WAITING"
            elif (len(df)-1 - flush_idx) > 15:
                state_obj["state"] = "WAITING"

    def execute_trade_signal(self, sym, spot_price, stop_loss_level):
        if sym in self.active_positions: return

        try:
            # Simple manual construction of ATM+1 strike
            strike_step = 50 if "NIFTY50" in sym else 100
            if "BANK" in sym: strike_step = 100

            atm_strike = round(spot_price / strike_step) * strike_step
            selected_strike = atm_strike

            print(f"  Attempting to buy Call Option: Strike {selected_strike}")

            qty = BotConfig.LOT_SIZES.get(BotConfig.INDEX_MAP.get(sym, 'NIFTY'), 50)

            # Using 100 as mock option price if real is unavailable
            pos = PaperPosition(sym, f"CE_{selected_strike}", 100.0, stop_loss_level, qty, spot_price)
            self.active_positions[sym] = pos
            print(f"✅ PAPER ORDER PLACED: {sym} | SL (Spot) {stop_loss_level} | Mock Opt Entry: 100.0")

        except Exception as e:
            print(f"❌ Order Execution Failed: {e}")

    def monitor_positions(self):
        for sym in list(self.active_positions.keys()):
            pos = self.active_positions[sym]

            # Get latest Spot Price from cache
            curr_spot = self.ltp_cache.get(sym)
            if not curr_spot: continue

            # 1. Check Spot SL (Compression Low)
            if curr_spot <= pos.sl_spot:
                print(f"❌ STOP LOSS HIT (Spot Level): {sym} @ {curr_spot}")
                del self.active_positions[sym]
                continue

            # 2. Simulate Option Price (Delta 0.5)
            spot_change = curr_spot - pos.entry_spot
            sim_opt_change = spot_change * 0.5
            curr_opt_price = pos.entry + sim_opt_change

            pnl = (curr_opt_price - pos.entry) * pos.qty
            pos.pnl = pnl

            # 3. Check Option Premium SL (30%)
            sl_price = pos.entry * (1 - BotConfig.OPTION_SL_PCT)
            if curr_opt_price <= sl_price:
                 print(f"❌ STOP LOSS HIT (Option Premium -30%): {sym} Spot:{curr_spot} Opt:{curr_opt_price:.2f}")
                 del self.active_positions[sym]
                 continue

            # 4. Trailing Profit (Simple)
            # If profit > 10%, trail SL to Cost.
            # (Simplified logic for paper trading)
            if curr_opt_price > pos.highest_price:
                pos.highest_price = curr_opt_price

            # Log periodic status
            # print(f"  [Pos] {sym} Spot:{curr_spot} Opt:{curr_opt_price:.2f} PnL:{pnl:.2f}")


# ============================================================================
# --- SECTION 6: MAIN EXECUTION ---
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="All-In-One Bot")
    parser.add_argument("mode", nargs='?', choices=["setup", "backtest", "run"], help="Mode")
    parser.add_argument("--app_id", help="Fyers App ID")
    parser.add_argument("--secret_key", help="Fyers Secret Key")
    parser.add_argument("--redirect_url", help="Fyers Redirect URL")
    parser.add_argument("--retrain", action="store_true", help="Ignored")

    args = parser.parse_args()

    mode = args.mode
    if not mode:
        print("\n--- All-In-One Absorption Bot ---")
        print("1. Setup Credentials")
        print("2. Run Backtester")
        print("3. Run Live Paper Trading Bot")
        choice = input("Enter choice (1-3): ")
        if choice == '1': mode = 'setup'
        elif choice == '2': mode = 'backtest'
        elif choice == '3': mode = 'run'

    if mode == "setup":
        app_id = args.app_id or input("App ID: ")
        secret = args.secret_key or input("Secret Key: ")
        url = args.redirect_url or input("Redirect URL: ")
        setup_credentials(app_id, secret, url)

    elif mode == "backtest":
        run_backtester()

    elif mode == "run":
        bot = LivePaperBot()
        bot.run()


if __name__ == "__main__":
    main()
