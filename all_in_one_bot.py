"""
ALL-IN-ONE GREEN-HAMMER TRADING BOT
===================================

**Disclaimer:**
This bot is intended for educational and paper trading purposes.
Live trading involves significant financial risk. Use at your own discretion.

**Description:**
This single-file script consolidates three key functionalities:
1. Credential Setup: Securely creates your API configuration file.
2. Backtester: Downloads historical data and tests the Green Hammer strategy.
3. Live Bot: Runs the strategy in a live (paper trading) environment.

**Strategy (Green-Hammer / Green-Pinbar):**
- **Pattern:** Bullish Hammer (Green Body, Long Lower Wick).
- **Trend Filter:** Price > Regime EMA (26 period) OR Price close to Day Low.
- **Entry:** Breakout above the signal candle's High.
- **Exits:** Target (R:R), Stop Loss (Candle Low), or Time-based exit.
- **Assets:** NSE Stocks & MCX Futures.

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
    TIMEFRAME_MIN = 5
    REGIME_EMA_PERIOD = 26
    R_MULTIPLIER = 2.0  # Risk:Reward 1:2
    ENTRY_BUFFER = 0.05  # Buffer above high for breakout

    # --- Position Sizing ---
    PAPER_BALANCE = 100000
    ALLOCATION_AMOUNT = 20000  # Capital per trade for NSE
    MCX_LOT_MULTIPLIER = 1

    # --- Session Timing ---
    # NSE: 09:15 - 15:30 (Entries until 15:00, Exit all 15:09)
    # MCX: 09:00 - 23:30 (Entries until 22:00, Exit all 22:50)
    ENTRY_CUTOFF_NSE = dt.time(15, 0)
    EXIT_ALL_NSE = dt.time(15, 9)

    ENTRY_CUTOFF_MCX = dt.time(22, 0)
    EXIT_ALL_MCX = dt.time(22, 50)

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
        # MCX Futures (Make sure to update expiry if needed, or use generic logic if available)
        # Note: Users should update these monthly
        'MCX:SILVERMIC26FEBFUT',
        'MCX:CRUDEOILM26FEBFUT',
        'MCX:NATGASMINI26JANFUT'
    ]
    # Filter out invalid MCX placeholders for safety in default config, keep valid structure
    # Using generic placeholders that user must update, or just the Stocks for now
    # We will keep the list but add a check.

    # --- MCX Lot Sizes ---
    MCX_LOTS = {
        "SILVERMIC": 1,
        "CRUDEOILM": 1,
        "NATGASMINI": 1,
    }

    # --- Candle Geometry ---
    LOWER_WICK_MIN = 50.0  # %
    LOWER_WICK_MAX = 80.0
    BODY_MIN = 5.0
    BODY_MAX = 30.0
    UPPER_WICK_MAX = 25.0


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
    Ensures a valid Fyers access token is available using the robust flow from Code-2.
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
        # Verify functionality (optional, but good practice)
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
                new_refresh_token = response.get("refresh_token")  # might be same

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

def get_lot_size(symbol: str) -> int:
    if symbol.endswith("-EQ"):
        return 1
    # MCX Logic
    base = symbol.split(':')[1] if ':' in symbol else symbol
    # Simple matching
    for mcx_base, lot in BotConfig.MCX_LOTS.items():
        if base.startswith(mcx_base):
            return lot
    return 1


def is_bullish_hammer_candle(o, h, l, c, prev_o, prev_c, min_range_pct=0.0015, ignore_prev_candle=False):
    """
    Detects Green Hammer / Green Pinbar:
    - Current Candle Green (Close > Open)
    - Previous Candle Red (Close < Open) [unless ignored]
    - Long Lower Wick (50-80% of range)
    - Small Body (5-30% of range)
    - Small/No Upper Wick (0-25% of range)
    """
    if c <= o: return False  # Must be Green

    if not ignore_prev_candle:
        if prev_c >= prev_o: return False  # Prev must be Red

    total_range = h - l
    if total_range == 0 or c == 0: return False

    # Check minimum volatility
    if (total_range / c) < min_range_pct: return False

    upper_wick = h - c
    body = c - o
    lower_wick = o - l

    upper_pct = (upper_wick / total_range) * 100
    body_pct = (body / total_range) * 100
    lower_pct = (lower_wick / total_range) * 100

    return (
            (BotConfig.LOWER_WICK_MIN <= lower_pct <= BotConfig.LOWER_WICK_MAX) and
            (BotConfig.BODY_MIN <= body_pct <= BotConfig.BODY_MAX) and
            (upper_pct <= BotConfig.UPPER_WICK_MAX)
    )


# ============================================================================
# --- SECTION 4: BACKTESTER LOGIC ---
# ============================================================================

def run_backtester():
    """
    Downloads historical data for symbols and runs the Green Hammer strategy backtest.
    """
    fyers = get_fyers_instance()

    print(f"\n🚀 STARTING BACKTEST (Strategy: Green Hammer, TF: {BotConfig.TIMEFRAME_MIN}m)")
    print(f"Symbols: {len(BotConfig.SYMBOLS)}")

    total_trades = 0
    total_wins = 0
    total_pnl = 0.0

    to_date = dt.date.today()
    from_date = to_date - dt.timedelta(days=90)  # Fyers limit roughly 100 days for 15m usually fine

    for symbol in BotConfig.SYMBOLS:
        print(f"\nProcessing {symbol}...")
        time.sleep(0.2)  # Rate limit
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
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s').dt.tz_localize('UTC').dt.tz_convert(
                'Asia/Kolkata')

            # --- Feature Engineering ---
            # EMA
            df['ema'] = df['close'].ewm(span=BotConfig.REGIME_EMA_PERIOD, adjust=False).mean()

            # Day Low (Approximation: Low of the day so far)
            # We need to group by day to find day low properly, but for vector backtest,
            # let's assume 'Day Low' is the minimum of the current day up to that candle.
            df['date'] = df['timestamp'].dt.date
            df['day_low'] = df.groupby('date')['low'].cummin()

            # Identify Signals
            trades = []
            for i in range(1, len(df) - 1):  # Need prev and next
                curr = df.iloc[i]
                prev = df.iloc[i - 1]

                # 1. Pattern Check
                is_hammer = is_bullish_hammer_candle(
                    curr.open, curr.high, curr.low, curr.close,
                    prev.open, prev.close,
                    ignore_prev_candle=False  # Strict check
                )

                if not is_hammer: continue

                # 2. Context Check
                # Price > EMA  OR  Price ~ Day Low
                # Using Close > EMA as per Code-2 logic
                above_ema = curr.close > curr.ema
                at_day_low = curr.low <= (curr.day_low * 1.001)  # 0.1% tolerance

                if not (above_ema or at_day_low): continue

                # 3. Entry Setup
                signal_high = curr.high
                signal_low = curr.low
                entry_trigger = signal_high * (
                            1 + BotConfig.ENTRY_BUFFER / 100)  # Percentage buffer or fixed? Code-2 uses fixed +0.05
                # Code-2: round_to_tick(t["high"] + ENTRY_BUFFER) -> ENTRY_BUFFER=0.05
                entry_trigger = signal_high + BotConfig.ENTRY_BUFFER

                sl = signal_low
                risk = entry_trigger - sl
                if risk <= 0: continue
                target = entry_trigger + (risk * BotConfig.R_MULTIPLIER)

                # 4. Check Next Candles for Entry
                # We look at the immediate next candle for entry (Code-2 logic: "active_start": next_cstart)
                next_candle = df.iloc[i + 1]

                if next_candle.high > entry_trigger:
                    # Trade Activated
                    entry_price = max(next_candle.open, entry_trigger)  # Realistic entry

                    # Check outcome in next few candles?
                    # For simple backtest, let's assume intraday exit or TP/SL hit within next N candles
                    # Code-2 is Intraday.

                    outcome_pnl = 0
                    result = "OPEN"

                    # Scan forward for SL or TP
                    for j in range(i + 1, min(i + 25, len(df))):  # Look ahead ~1 day
                        future = df.iloc[j]

                        # Check SL first (conservative)
                        if future.low <= sl:
                            outcome_pnl = -risk
                            result = "LOSS"
                            break

                        # Check TP
                        if future.high >= target:
                            outcome_pnl = risk * BotConfig.R_MULTIPLIER
                            result = "WIN"
                            break

                        # End of Day Exit
                        if future.timestamp.time() >= BotConfig.EXIT_ALL_NSE:  # Approx for all
                            outcome_pnl = future.close - entry_price
                            result = "EOD"
                            break

                    # Calculate PnL Amount
                    if symbol.startswith("MCX"):
                        # Lot based
                        qty = BotConfig.MCX_LOT_MULTIPLIER
                        # MCX values are often just points, need to know point value.
                        # Assuming 1 pt = 1 INR for simplicity in this generic backtest,
                        # or user interprets points.
                        # Actually SilverMic 1 pt = 1 INR per unit? Lot size 1?
                        # Let's just sum points captured.
                        trade_val = outcome_pnl * qty
                    else:
                        # Stock based (Allocation)
                        qty = int(BotConfig.ALLOCATION_AMOUNT / entry_price) if entry_price > 0 else 0
                        if qty == 0: qty = 1
                        trade_val = outcome_pnl * qty

                    trades.append(trade_val)
                    # Skip candles involved in this trade to avoid overlapping signals?
                    # Simple backtest: we just log it.

            # Summary for Symbol
            sym_trades = len(trades)
            sym_wins = len([t for t in trades if t > 0])
            sym_pnl = sum(trades)

            total_trades += sym_trades
            total_wins += sym_wins
            total_pnl += sym_pnl

            print(f"  Trades: {sym_trades}, Wins: {sym_wins}, PnL: {sym_pnl:.2f}")

        except Exception as e:
            print(f"  ❌ Error: {e}")

    print("\n" + "=" * 40)
    print("BACKTEST SUMMARY")
    print("=" * 40)
    print(f"Total Trades: {total_trades}")
    win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0
    print(f"Win Rate: {win_rate:.2f}%")
    print(f"Total P&L: {total_pnl:.2f}")
    print("=" * 40)


# ============================================================================
# --- SECTION 5: LIVE PAPER BOT LOGIC ---
# ============================================================================

class PaperPosition:
    def __init__(self, symbol, entry, sl, tgt, qty, product_type):
        self.symbol = symbol
        self.entry = entry
        self.sl = sl
        self.tgt = tgt
        self.qty = qty
        self.product_type = product_type
        self.status = "OPEN"
        self.pnl = 0.0
        self.entry_time = dt.datetime.now()


class LivePaperBot:
    def __init__(self):
        self.fyers = get_fyers_instance()
        self.config = BotConfig()

        # State
        self.active_positions: Dict[str, PaperPosition] = {}
        self.candles_build = {}  # (symbol, timeframe_start) -> OHLC
        self.processed_candles = set()
        self.triggers = {}  # symbol -> {level, sl, active_until}

        self.ema_cache = {}
        self.day_low_cache = {}
        self.ltp_cache = {}

        self.paper_balance = BotConfig.PAPER_BALANCE

    def fetch_initial_state(self):
        """Pre-fetches history for EMA and Day Lows."""
        print("🔄 Initializing Bot State...")

        # 1. EMAs
        now_date = dt.date.today()
        start_date = now_date - dt.timedelta(days=10)

        for sym in BotConfig.SYMBOLS:
            time.sleep(0.2)  # Rate limit
            try:
                # History
                resp = self.fyers.history({
                    "symbol": sym, "resolution": str(BotConfig.TIMEFRAME_MIN),
                    "date_format": "1", "range_from": start_date.strftime("%Y-%m-%d"),
                    "range_to": now_date.strftime("%Y-%m-%d"), "cont_flag": "1"
                })
                if resp.get("candles"):
                    df = pd.DataFrame(resp["candles"], columns=["t", "o", "h", "l", "c", "v"])
                    df['ema'] = df['c'].ewm(span=BotConfig.REGIME_EMA_PERIOD, adjust=False).mean()
                    self.ema_cache[sym] = df['ema'].iloc[-1]
            except Exception:
                pass

            # Day Lows (via Quotes)
            try:
                q = self.fyers.quotes({"symbols": sym})
                if q.get("d"):
                    low = q["d"][0]["v"].get("low_price")
                    if low: self.day_low_cache[sym] = float(low)
            except Exception:
                pass

        print(f"✅ State Initialized. Tracking {len(BotConfig.SYMBOLS)} symbols.")

    def run(self):
        self.fetch_initial_state()

        # WebSocket Setup
        access_token = f"{self.fyers.client_id}:{self.fyers.token}"

        def on_message(msg):
            # Handle List of ticks (common) or single Dict
            if isinstance(msg, list):
                for m in msg:
                    self.process_tick(m)
            elif isinstance(msg, dict) and msg.get("type") == "sf":
                self.process_tick(msg)
            else:
                # Debug unhandled messages (e.g., connection acknowledgements)
                # print(f"[DEBUG] Unhandled Msg: {msg}")
                pass

        def on_error(msg):
            print(f"WS Error: {msg}")

        def on_open():
            print("🔌 WebSocket Connected.")
            self.ws.subscribe(symbols=BotConfig.SYMBOLS)

        self.ws = data_ws.FyersDataSocket(
            access_token=access_token,
            log_path=".",
            litemode=False,
            write_to_file=False,
            reconnect=True,
            on_connect=on_open,
            on_message=on_message,
            on_error=on_error
        )

        # Start WS in thread
        ws_thread = threading.Thread(target=self.ws.connect)
        ws_thread.daemon = True
        ws_thread.start()

        # Main Monitor Loop
        print(f"🚀 Live Paper Bot Running | Balance: {self.paper_balance}")
        print("Waiting for data... (Heartbeat every 300s)")

        last_print = dt.datetime.now()
        try:
            while True:
                time.sleep(1)
                self.monitor_positions()
                self.check_session_times()

                if (dt.datetime.now() - last_print).seconds >= 300:
                    print(
                        f"[{dt.datetime.now().strftime('%H:%M:%S')}] Monitoring {len(BotConfig.SYMBOLS)} symbols... Active Positions: {len(self.active_positions)} | Balance: {self.paper_balance:.2f}")
                    last_print = dt.datetime.now()

        except KeyboardInterrupt:
            print("\n🛑 Bot Stopped by User.")

    def get_candle_start(self, ts):
        """Floors timestamp to nearest timeframe start."""
        dt_obj = dt.datetime.fromtimestamp(ts)
        return dt_obj.replace(second=0, microsecond=0) - dt.timedelta(minutes=dt_obj.minute % BotConfig.TIMEFRAME_MIN)

    def process_tick(self, msg):
        sym = msg.get("symbol")
        ltp = float(msg.get("ltp"))
        ts = msg.get("timestamp", time.time())

        # Debug: Print first tick for a symbol to confirm data flow
        if sym not in self.ltp_cache:
            print(f"[DEBUG] First tick received for {sym}: {ltp}")

        self.ltp_cache[sym] = ltp

        # Update Day Low
        if ltp < self.day_low_cache.get(sym, float('inf')):
            self.day_low_cache[sym] = ltp

        # Candle Building
        c_start = self.get_candle_start(ts)
        key = (sym, c_start)

        if key not in self.candles_build:
            self.candles_build[key] = {"o": ltp, "h": ltp, "l": ltp, "c": ltp}
        else:
            c = self.candles_build[key]
            c["h"] = max(c["h"], ltp)
            c["l"] = min(c["l"], ltp)
            c["c"] = ltp

        # Check Candle Completion
        # Logic: If we are in the last second of the candle window (or past it), mark as complete.
        # Note: This relies on receiving a tick near the end.
        tick_dt = dt.datetime.fromtimestamp(ts)
        candle_end_time = c_start + dt.timedelta(minutes=BotConfig.TIMEFRAME_MIN)

        # Completion Condition: Tick time is >= (End - 1s)
        # This matches Code-2 logic which checks every tick if it completes the bar.
        if tick_dt >= (candle_end_time - dt.timedelta(seconds=1)):
            if key not in self.processed_candles:
                self.processed_candles.add(key)
                self.analyze_completed_candle(sym, self.candles_build[key], c_start)

                # Update EMA for next calculation
                prev_ema = self.ema_cache.get(sym)
                close = self.candles_build[key]["c"]
                if prev_ema:
                    k = 2 / (BotConfig.REGIME_EMA_PERIOD + 1)
                    self.ema_cache[sym] = (close * k) + (prev_ema * (1 - k))
                else:
                    self.ema_cache[sym] = close

        # Check Triggers (Breakout)
        if sym in self.triggers:
            trig = self.triggers[sym]
            now = dt.datetime.now()
            if now < trig["active_until"]:
                if ltp >= trig["level"]:
                    self.execute_paper_trade(sym, ltp, trig["sl"])
                    del self.triggers[sym]
            else:
                del self.triggers[sym]  # Expired

    def analyze_completed_candle(self, sym, candle, start_time):
        # 1. Retrieve Prev Candle (approximation from build history if available, else skip)
        prev_start = start_time - dt.timedelta(minutes=BotConfig.TIMEFRAME_MIN)
        prev_candle = self.candles_build.get((sym, prev_start))

        # Check Session Start (NSE 9:15, MCX 9:00)
        is_session_start = False
        t_time = start_time.time()
        if sym.startswith("MCX"):
            if t_time == dt.time(9, 0): is_session_start = True
        else:
            if t_time == dt.time(9, 15): is_session_start = True

        p_o, p_c = (0, 0)
        if prev_candle:
            p_o, p_c = prev_candle["o"], prev_candle["c"]
        elif not is_session_start:
            return  # Need prev candle unless start of day

        # 2. Check Pattern
        is_hammer = is_bullish_hammer_candle(
            candle["o"], candle["h"], candle["l"], candle["c"],
            p_o, p_c, ignore_prev_candle=is_session_start
        )

        if is_hammer:
            # 3. Context
            ema = self.ema_cache.get(sym, 0)
            day_low = self.day_low_cache.get(sym, 0)

            above_ema = candle["c"] > ema
            at_day_low = candle["l"] <= (day_low * 1.001)

            if above_ema or at_day_low:
                print(f"[{dt.datetime.now().time()}] 🕯️ Signal Detected: {sym} (Hammer)")

                # Set Trigger
                trigger_level = candle["h"] + BotConfig.ENTRY_BUFFER
                self.triggers[sym] = {
                    "level": trigger_level,
                    "sl": candle["l"],
                    "active_until": dt.datetime.now() + dt.timedelta(minutes=BotConfig.TIMEFRAME_MIN)
                }

    def execute_paper_trade(self, sym, entry, sl):
        if sym in self.active_positions: return

        # Risk Calc
        risk = entry - sl
        if risk <= 0: return
        tgt = entry + (risk * BotConfig.R_MULTIPLIER)

        # Qty Calc
        if sym.startswith("MCX"):
            qty = BotConfig.MCX_LOT_MULTIPLIER
        else:
            qty = int(BotConfig.ALLOCATION_AMOUNT / entry)
            if qty < 1: qty = 1

        pos = PaperPosition(sym, entry, sl, tgt, qty, "INTRADAY")
        self.active_positions[sym] = pos
        print(f"✅ PAPER TRADE OPEN: {sym} Buy @ {entry:.2f} | SL {sl:.2f} | TGT {tgt:.2f} | Qty {qty}")

    def monitor_positions(self):
        for sym in list(self.active_positions.keys()):
            pos = self.active_positions[sym]
            ltp = self.ltp_cache.get(sym)
            if not ltp: continue

            # Check Exit
            pnl = (ltp - pos.entry) * pos.qty
            pos.pnl = pnl

            # SL Hit
            if ltp <= pos.sl:
                print(f"❌ SL HIT: {sym} @ {ltp:.2f} | PnL: {pnl:.2f}")
                self.paper_balance += pnl
                del self.active_positions[sym]

            # TP Hit
            elif ltp >= pos.tgt:
                print(f"🎯 TGT HIT: {sym} @ {ltp:.2f} | PnL: {pnl:.2f}")
                self.paper_balance += pnl
                del self.active_positions[sym]

            # Trailing? (Code-1 had trailing, Code-2 didn't explicitly ask for it in main logic,
            # but Code-1 backtester had it. We stick to Code-2 R:R logic here for simplicity.)

    def check_session_times(self):
        now = dt.datetime.now().time()

        # NSE Exit
        if now >= BotConfig.EXIT_ALL_NSE and now < dt.time(15, 11):
            self.close_all_filtered(lambda s: not s.startswith("MCX"), "NSE Session End")

        # MCX Exit
        if now >= BotConfig.EXIT_ALL_MCX:
            self.close_all_filtered(lambda s: s.startswith("MCX"), "MCX Session End")

    def close_all_filtered(self, filter_func, reason):
        for sym in list(self.active_positions.keys()):
            if filter_func(sym):
                pos = self.active_positions[sym]
                ltp = self.ltp_cache.get(sym, pos.entry)
                pnl = (ltp - pos.entry) * pos.qty
                print(f"⏰ {reason}: Closing {sym} @ {ltp} | PnL: {pnl:.2f}")
                self.paper_balance += pnl
                del self.active_positions[sym]


# ============================================================================
# --- SECTION 6: MAIN EXECUTION ---
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="All-In-One Green Hammer Bot")
    parser.add_argument("mode", nargs='?', choices=["setup", "backtest", "run"], help="Mode")
    parser.add_argument("--app_id", help="Fyers App ID")
    parser.add_argument("--secret_key", help="Fyers Secret Key")
    parser.add_argument("--redirect_url", help="Fyers Redirect URL")
    parser.add_argument("--retrain", action="store_true", help="Ignored (Rule-based strategy)")

    args = parser.parse_args()

    mode = args.mode
    if not mode:
        print("\n--- All-In-One Green Hammer Bot ---")
        print("1. Setup Credentials")
        print("2. Run Backtester")
        print("3. Run Live Paper Trading Bot")
        print("4. Run Live Bot (Auto-Retrain -> Just Runs Live)")
        choice = input("Enter choice (1-4): ")
        if choice == '1':
            mode = 'setup'
        elif choice == '2':
            mode = 'backtest'
        elif choice == '3':
            mode = 'run'
        elif choice == '4':
            mode = 'run'

    if mode == "setup":
        app_id = args.app_id or input("App ID: ")
        secret = args.secret_key or input("Secret Key: ")
        url = args.redirect_url or input("Redirect URL: ")
        setup_credentials(app_id, secret, url)

    elif mode == "backtest":
        run_backtester()

    elif mode == "run":
        if args.retrain:
            print("ℹ️ Note: This is a rule-based strategy. 'Retraining' is not applicable.")
            print("Running backtest first for verification...")
            run_backtester()
            print("\nStarting Live Bot...")

        bot = LivePaperBot()
        bot.run()


if __name__ == "__main__":
    main()
