"""
ALL-IN-ONE VWAP TREND PULLBACK TRADING BOT
==========================================

**Disclaimer:**
This bot is intended for educational and paper trading purposes.
Live trading involves significant financial risk. Use at your own discretion.

**Description:**
This single-file script consolidates three key functionalities:
1. Credential Setup: Securely creates your API configuration file.
2. Backtester: Downloads historical data and tests the strategy.
3. Live Bot: Runs the strategy in a live (paper trading) environment.

**Strategy (VWAP Trend Pullback):**
- **Trend Filter:** Price > VWAP (Session) AND EMA(9) > EMA(21).
- **Setup (Pullback):** Price pulls back to touch/near VWAP or EMA(21) but holds structure.
- **Entry Trigger:** A bullish candle closes back above EMA(9) confirming the resumption.
- **Entry:** ATM/ITM Call Option (Delta 0.5-0.6).
- **Exits:** Target (1:2 RR), Stop Loss (Swing Low), or Time-based exit.
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

    # EMAs
    EMA_FAST = 9
    EMA_SLOW = 21

    # Filter Settings
    ADX_PERIOD = 14
    ADX_THRESHOLD = 20
    RSI_PERIOD = 14
    RSI_MIN = 50
    RSI_MAX = 70

    # Pullback Logic
    PULLBACK_TOLERANCE = 0.0015 # 0.15% distance from VWAP/EMA considered "touch"

    # Risk Management
    R_MULTIPLIER = 2.0
    ATR_PERIOD = 14
    ATR_SL_MULT = 1.0 # SL = Low - 1*ATR
    OPTION_SL_PCT = 0.20  # 20% Stop Loss on Option Premium

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
        """Calculates VWAP and EMAs."""
        if df.empty: return df

        # EMAs
        df['ema_fast'] = df['close'].ewm(span=BotConfig.EMA_FAST, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=BotConfig.EMA_SLOW, adjust=False).mean()

        # RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=BotConfig.RSI_PERIOD).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=BotConfig.RSI_PERIOD).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        # ATR
        df['tr0'] = abs(df['high'] - df['low'])
        df['tr1'] = abs(df['high'] - df['close'].shift())
        df['tr2'] = abs(df['low'] - df['close'].shift())
        df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
        df['atr'] = df['tr'].rolling(window=BotConfig.ATR_PERIOD).mean()

        # ADX
        # Simplified ADX (Wilder's requires smoothing, using rolling mean approx for speed here or fuller impl)
        # For simplicity in this script, we'll use a basic directional movement calc
        up = df['high'].diff()
        down = -df['low'].diff()
        plus_dm = np.where((up > down) & (up > 0), up, 0.0)
        minus_dm = np.where((down > up) & (down > 0), down, 0.0)

        df['plus_di'] = 100 * pd.Series(plus_dm).ewm(alpha=1/BotConfig.ADX_PERIOD).mean() / df['atr']
        df['minus_di'] = 100 * pd.Series(minus_dm).ewm(alpha=1/BotConfig.ADX_PERIOD).mean() / df['atr']
        dx = 100 * abs(df['plus_di'] - df['minus_di']) / (df['plus_di'] + df['minus_di'])
        df['adx'] = dx.rolling(window=BotConfig.ADX_PERIOD).mean()

        # VWAP (Approximation: CumSum(Price*Vol) / CumSum(Vol) reset daily)
        if 'timestamp' in df.columns:
            df['date'] = df['timestamp'].dt.date

            def calc_vwap(group):
                cum_vol = group['volume'].cumsum()
                cum_pv = (group['close'] * group['volume']).cumsum()
                return cum_pv / cum_vol

            # Explicitly select columns to silence FutureWarning
            df['vwap'] = df.groupby('date')[['close', 'volume']].apply(lambda x: calc_vwap(x)).reset_index(level=0, drop=True)

        return df

    @staticmethod
    def detect_trend_pullback(curr_candle, prev_candle):
        """
        Strategy Logic:
        1. Trend: Close > VWAP and EMA(9) > EMA(21)
        2. Signal: Close > EMA(9) (Resumption)
        3. Pullback context: Previous candles tested VWAP or EMA(21) recently? (Implied by crossover or hold)
           - Simple trigger: Crossover EMA(9) from below?
           - Or: Just Green Candle closing above EMA(9) while Trend is Up, and Price is close to Support.

        Robust Simple Logic:
        - Trend: EMA9 > EMA21
        - Dip: Low <= EMA21 OR Low <= VWAP (Tested support)
        - Trigger: Close > EMA9 (Bounced back)
        """
        if curr_candle.empty: return False, 0

        ema9 = curr_candle['ema_fast']
        ema21 = curr_candle['ema_slow']
        vwap = curr_candle['vwap']
        adx = curr_candle.get('adx', 0)
        rsi = curr_candle.get('rsi', 50)
        atr = curr_candle.get('atr', 0)

        # 1. Trend Filter
        # Strong Trend: ADX > 20
        if adx < BotConfig.ADX_THRESHOLD: return False, 0
        # Bullish Alignment
        if not (ema9 > ema21): return False, 0
        # Price Regime
        if not (curr_candle['close'] > vwap): return False, 0

        # 2. Pullback Condition (Dip)
        # Must have touched Support recently (EMA21 or VWAP)
        # Stricter: Low must have dipped below (or very close) to EMA21/VWAP
        # but Body (Close) held above or reclaimed it.
        def touched_support(c):
            # Did Low touch Support?
            touched = (c['low'] <= ema21 * (1+BotConfig.PULLBACK_TOLERANCE)) or \
                      (c['low'] <= vwap * (1+BotConfig.PULLBACK_TOLERANCE))
            return touched

        is_pullback = touched_support(curr_candle) or touched_support(prev_candle)

        # 3. Trigger (Resumption)
        # Green Candle, Close > EMA9
        is_bullish = curr_candle['close'] > curr_candle['open']
        breakout_ema9 = curr_candle['close'] > ema9

        # RSI Momentum Check (Not Overbought)
        rsi_ok = (rsi > BotConfig.RSI_MIN) and (rsi < BotConfig.RSI_MAX)

        if is_pullback and is_bullish and breakout_ema9 and rsi_ok:
            # Valid Signal
            # Robust SL: Low - ATR
            sl_price = min(curr_candle['low'], prev_candle['low']) - (atr * BotConfig.ATR_SL_MULT)
            return True, sl_price

        return False, 0

# ============================================================================
# --- SECTION 4: BACKTESTER LOGIC ---
# ============================================================================

def run_backtester():
    """
    Downloads historical data for Indices and runs the strategy backtest.
    """
    fyers = get_fyers_instance()

    print(f"\n🚀 STARTING BACKTEST (Strategy: VWAP Trend Pullback, TF: {BotConfig.TIMEFRAME_MIN}m)")

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
            active_trade = None # {entry, sl, tgt}

            signals_count = 0

            for i in range(21, len(df)):
                curr = df.iloc[i]
                prev = df.iloc[i-1]

                # Check for Signal if no active trade
                if active_trade is None:
                    is_signal, sl = Strategy.detect_trend_pullback(curr, prev)

                    if is_signal:
                        signals_count += 1
                        entry_price = curr['close']
                        risk = entry_price - sl
                        if risk <= 0: continue # Invalid SL

                        target = entry_price + (risk * BotConfig.R_MULTIPLIER)

                        active_trade = {
                            "time": curr['timestamp'],
                            "type": "BUY",
                            "entry": entry_price,
                            "sl": sl,
                            "tgt": target,
                            "pnl": 0,
                            "outcome": "OPEN"
                        }
                else:
                    # Manage Active Trade
                    t = active_trade

                    # Check SL
                    if curr['low'] <= t['sl']:
                        t['outcome'] = "LOSS"
                        t['pnl'] = t['sl'] - t['entry']
                        trades.append(t)
                        active_trade = None
                        continue

                    # Check Target
                    if curr['high'] >= t['tgt']:
                        t['outcome'] = "WIN"
                        t['pnl'] = t['tgt'] - t['entry']
                        trades.append(t)
                        active_trade = None
                        continue

                    # EOD Exit (approx 15:15)
                    if curr['timestamp'].time() >= BotConfig.EXIT_ALL_NSE:
                         t['outcome'] = "EOD"
                         t['pnl'] = curr['close'] - t['entry']
                         trades.append(t)
                         active_trade = None

            # Close last active
            if active_trade:
                t = active_trade
                t['outcome'] = "OPEN (MTM)"
                t['pnl'] = df.iloc[-1]['close'] - t['entry']
                trades.append(t)

            # Summary
            wins = len([t for t in trades if t['outcome'] == "WIN"])
            losses = len([t for t in trades if t['outcome'] == "LOSS"])
            mtm_pnls = sum([t['pnl'] for t in trades])

            print(f"  Signals: {signals_count}")
            print(f"  Results: Wins:{wins} | Losses:{losses} | Net PnL (Spot Points): {mtm_pnls:.2f}")

            if trades:
                print(f"  Recent Trades:")
                for t in trades[-5:]:
                    ts_str = t['time'].strftime('%Y-%m-%d %H:%M')
                    print(f"    [{ts_str}] {t['type']} @ {t['entry']:.2f} -> {t['outcome']} (PnL: {t['pnl']:.2f})")

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

        self.history_df = {} # Store recent history for lookbacks

        self.paper_balance = BotConfig.PAPER_BALANCE

    def fetch_initial_state(self):
        """Pre-fetches history."""
        print("🔄 Initializing Bot State...")

        to_date = dt.date.today()
        from_date = to_date - dt.timedelta(days=5)

        for sym in BotConfig.SYMBOLS:
            time.sleep(0.2)
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

        # Check Signal
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        is_signal, sl = Strategy.detect_trend_pullback(curr, prev)

        if is_signal:
             print(f"🚀 SIGNAL (Buy Pullback) on {sym} @ {curr['close']}")
             self.execute_trade_signal(sym, curr['close'], sl)

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

            # 1. Check Spot SL
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

            # 3. Check Option Premium SL (20%)
            sl_price = pos.entry * (1 - BotConfig.OPTION_SL_PCT)
            if curr_opt_price <= sl_price:
                 print(f"❌ STOP LOSS HIT (Option Premium -20%): {sym} Spot:{curr_spot} Opt:{curr_opt_price:.2f}")
                 del self.active_positions[sym]
                 continue

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

    # Strategy Customization
    parser.add_argument("--fast_ema", type=int, default=BotConfig.EMA_FAST, help="Fast EMA Period")
    parser.add_argument("--slow_ema", type=int, default=BotConfig.EMA_SLOW, help="Slow EMA Period")

    parser.add_argument("--retrain", action="store_true", help="Ignored")

    args = parser.parse_args()

    # Update Config with CLI args
    BotConfig.EMA_FAST = args.fast_ema
    BotConfig.EMA_SLOW = args.slow_ema

    mode = args.mode
    if not mode:
        print("\n--- All-In-One VWAP Trend Bot ---")
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
