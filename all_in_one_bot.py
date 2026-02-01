"""
ALL-IN-ONE VOLATILITY BREAKOUT (VBO) TRADING BOT
================================================

**Disclaimer:**
This bot is intended for educational and paper trading purposes.
Live trading involves significant financial risk. Use at your own discretion.

**Strategy (Volatility Breakout - VBO):**
- **Concept:** Capture explosive moves when price breaks Bollinger Bands with high volume and momentum.
- **Timeframe:** 5 Minutes.
- **Long Signal (Buy CE):**
  - Price closes ABOVE Upper Bollinger Band.
  - RSI > 60 (Strong Bullish Momentum).
  - Volume > 1.2x Average Volume (20).
  - ADX > 20 (Trend Presence).
- **Short Signal (Buy PE):**
  - Price closes BELOW Lower Bollinger Band.
  - RSI < 40 (Strong Bearish Momentum).
  - Volume > 1.2x Average Volume (20).
  - ADX > 20.
- **Exits:**
  - Stop Loss: 1.5x ATR from Entry.
  - Target: 2.0x Risk.
  - Trailing: Standard ATR Trail.

**Assets:** NSE Indices (NIFTY, BANKNIFTY) -> Options.
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
    TIMEFRAME_MIN = 5

    # Bollinger Bands
    BB_PERIOD = 20
    BB_STD_DEV = 2

    # Filters
    RSI_PERIOD = 14
    RSI_LONG_MIN = 60
    RSI_SHORT_MAX = 40

    ADX_PERIOD = 14
    ADX_THRESHOLD = 20

    VOL_MA_PERIOD = 20
    VOL_MULT = 1.2

    # Risk Management
    R_MULTIPLIER = 2.0
    ATR_PERIOD = 14
    ATR_SL_MULT = 1.5
    OPTION_SL_PCT = 0.20

    # --- Position Sizing ---
    PAPER_BALANCE = 100000
    ALLOCATION_AMOUNT = 20000

    # --- Session Timing ---
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
        'BANKNIFTY': 15,
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
        """Calculates Bollinger Bands, RSI, ATR, ADX, Volume MA."""
        if df.empty: return df

        # --- Bollinger Bands ---
        df['bb_mid'] = df['close'].rolling(window=BotConfig.BB_PERIOD).mean()
        df['bb_std'] = df['close'].rolling(window=BotConfig.BB_PERIOD).std()
        df['bb_upper'] = df['bb_mid'] + (BotConfig.BB_STD_DEV * df['bb_std'])
        df['bb_lower'] = df['bb_mid'] - (BotConfig.BB_STD_DEV * df['bb_std'])

        # --- ATR ---
        df['tr0'] = abs(df['high'] - df['low'])
        df['tr1'] = abs(df['high'] - df['close'].shift())
        df['tr2'] = abs(df['low'] - df['close'].shift())
        df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
        df['atr'] = df['tr'].rolling(window=BotConfig.ATR_PERIOD).mean()

        # --- RSI ---
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=BotConfig.RSI_PERIOD).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=BotConfig.RSI_PERIOD).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        # --- ADX ---
        up = df['high'].diff()
        down = -df['low'].diff()
        plus_dm = np.where((up > down) & (up > 0), up, 0.0)
        minus_dm = np.where((down > up) & (down > 0), down, 0.0)

        # Using rolling mean for simplicity/speed (Standard ADX uses smoothed MA)
        df['plus_di'] = 100 * pd.Series(plus_dm).rolling(window=BotConfig.ADX_PERIOD).mean() / df['atr']
        df['minus_di'] = 100 * pd.Series(minus_dm).rolling(window=BotConfig.ADX_PERIOD).mean() / df['atr']
        dx = 100 * abs(df['plus_di'] - df['minus_di']) / (df['plus_di'] + df['minus_di'])
        df['adx'] = dx.rolling(window=BotConfig.ADX_PERIOD).mean()

        # --- Volume MA ---
        df['vol_ma'] = df['volume'].rolling(window=BotConfig.VOL_MA_PERIOD).mean()

        return df

    @staticmethod
    def detect_signal(curr_candle, prev_candle):
        """
        Returns: (SignalType, StopLoss)
        """
        if curr_candle.empty or prev_candle.empty: return None, 0

        # Indicators
        close = curr_candle['close']
        bb_upper = curr_candle['bb_upper']
        bb_lower = curr_candle['bb_lower']
        rsi = curr_candle['rsi']
        adx = curr_candle.get('adx', 0)
        vol = curr_candle['volume']
        vol_ma = curr_candle.get('vol_ma', 0)
        atr = curr_candle.get('atr', 0)

        # Common Checks
        is_trend = adx > BotConfig.ADX_THRESHOLD
        is_high_vol = vol > (vol_ma * BotConfig.VOL_MULT)

        if not is_trend: return None, 0 # Filter Chop

        # --- LONG SIGNAL ---
        # Close > Upper Band
        # RSI > 60
        if (close > bb_upper) and (rsi > BotConfig.RSI_LONG_MIN) and is_high_vol:
            # Check if this is a fresh breakout?
            # (Prev candle was below or at least this candle pushed out)
            # Simple check: Just Breakout + Volume.
            sl = close - (atr * BotConfig.ATR_SL_MULT)
            return "BUY", sl

        # --- SHORT SIGNAL ---
        # Close < Lower Band
        # RSI < 40
        if (close < bb_lower) and (rsi < BotConfig.RSI_SHORT_MAX) and is_high_vol:
            sl = close + (atr * BotConfig.ATR_SL_MULT)
            return "SELL", sl

        return None, 0

# ============================================================================
# --- SECTION 4: BACKTESTER LOGIC ---
# ============================================================================

def run_backtester():
    fyers = get_fyers_instance()
    print(f"\n🚀 STARTING BACKTEST (Strategy: Volatility Breakout, TF: {BotConfig.TIMEFRAME_MIN}m)")

    to_date = dt.date.today()
    from_date = to_date - dt.timedelta(days=60)

    for symbol in BotConfig.SYMBOLS:
        print(f"\nProcessing {symbol}...")
        time.sleep(0.5)
        try:
            data = {
                "symbol": symbol, "resolution": str(BotConfig.TIMEFRAME_MIN),
                "date_format": "1", "range_from": from_date.strftime("%Y-%m-%d"),
                "range_to": to_date.strftime("%Y-%m-%d"), "cont_flag": "1"
            }
            resp = fyers.history(data)
            if resp.get("s") != "ok" or not resp.get("candles"):
                print(f"  ⚠️ No data/Error: {resp.get('message')}")
                continue

            cols = ["timestamp", "open", "high", "low", "close", "volume"]
            df = pd.DataFrame(resp["candles"], columns=cols)
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s').dt.tz_localize('UTC').dt.tz_convert('Asia/Kolkata')

            df = Strategy.calculate_indicators(df)

            trades = []
            active_trade = None

            signals_count = 0

            for i in range(21, len(df)):
                curr = df.iloc[i]
                prev = df.iloc[i-1]

                # Check for Signal
                if active_trade is None:
                    sig_type, sl = Strategy.detect_signal(curr, prev)

                    if sig_type:
                        signals_count += 1
                        entry_price = curr['close']

                        risk = abs(entry_price - sl)
                        if risk == 0: continue

                        if sig_type == "BUY":
                            tgt = entry_price + (risk * BotConfig.R_MULTIPLIER)
                        else:
                            tgt = entry_price - (risk * BotConfig.R_MULTIPLIER)

                        active_trade = {
                            "time": curr['timestamp'],
                            "type": sig_type,
                            "entry": entry_price,
                            "sl": sl,
                            "tgt": tgt,
                            "pnl": 0,
                            "outcome": "OPEN"
                        }
                else:
                    # Manage Active Trade
                    t = active_trade

                    if t['type'] == "BUY":
                        # SL Hit
                        if curr['low'] <= t['sl']:
                            t['outcome'] = "LOSS"
                            t['pnl'] = t['sl'] - t['entry']
                            trades.append(t)
                            active_trade = None
                        # TGT Hit
                        elif curr['high'] >= t['tgt']:
                            t['outcome'] = "WIN"
                            t['pnl'] = t['tgt'] - t['entry']
                            trades.append(t)
                            active_trade = None
                        # Exit on RSI weakness? (Optional)

                    elif t['type'] == "SELL":
                        # SL Hit
                        if curr['high'] >= t['sl']:
                            t['outcome'] = "LOSS"
                            t['pnl'] = t['entry'] - t['sl']
                            trades.append(t)
                            active_trade = None
                        # TGT Hit
                        elif curr['low'] <= t['tgt']:
                            t['outcome'] = "WIN"
                            t['pnl'] = t['entry'] - t['tgt']
                            trades.append(t)
                            active_trade = None

                    # EOD Exit
                    if active_trade and curr['timestamp'].time() >= BotConfig.EXIT_ALL_NSE:
                         t = active_trade
                         t['outcome'] = "EOD"
                         if t['type'] == "BUY": t['pnl'] = curr['close'] - t['entry']
                         else: t['pnl'] = t['entry'] - curr['close']
                         trades.append(t)
                         active_trade = None

            # Close last
            if active_trade:
                t = active_trade
                t['outcome'] = "OPEN (MTM)"
                if t['type'] == "BUY": t['pnl'] = df.iloc[-1]['close'] - t['entry']
                else: t['pnl'] = t['entry'] - df.iloc[-1]['close']
                trades.append(t)

            # Summary
            wins = len([t for t in trades if t['pnl'] > 0])
            losses = len([t for t in trades if t['pnl'] <= 0])
            mtm_pnls = sum([t['pnl'] for t in trades])

            print(f"  Signals: {signals_count}")
            print(f"  Results: Wins:{wins} | Losses:{losses} | Net PnL (Spot): {mtm_pnls:.2f}")

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
    def __init__(self, symbol, option_symbol, entry, sl_spot, qty, entry_spot, side):
        self.symbol = symbol
        self.option_symbol = option_symbol
        self.entry = entry # Option Entry Price
        self.sl_spot = sl_spot # Spot Level SL
        self.qty = qty
        self.entry_spot = entry_spot
        self.side = side
        self.status = "OPEN"
        self.pnl = 0.0
        self.entry_time = dt.datetime.now()

class LivePaperBot:
    def __init__(self):
        self.fyers = get_fyers_instance()
        self.config = BotConfig()
        self.active_positions: Dict[str, PaperPosition] = {}
        self.candles_build = {}
        self.processed_candles = set()
        self.ltp_cache = {}
        self.history_df = {}
        self.paper_balance = BotConfig.PAPER_BALANCE

    def fetch_initial_state(self):
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
                    df = Strategy.calculate_indicators(df)
                    self.history_df[sym] = df
                    print(f"  Loaded {len(df)} candles for {sym}")
            except Exception as e: print(f"  Failed to load history for {sym}: {e}")
        print(f"✅ State Initialized.")

    def run(self):
        self.fetch_initial_state()
        access_token = f"{self.fyers.client_id}:{self.fyers.token}"

        def on_message(msg):
            if isinstance(msg, list):
                for m in msg: self.process_tick(m)
            elif isinstance(msg, dict) and msg.get("type") == "sf": self.process_tick(msg)

        def on_error(msg): print(f"WS Error: {msg}")
        def on_open():
            print("🔌 WebSocket Connected.")
            self.ws.subscribe(symbols=BotConfig.SYMBOLS)

        self.ws = data_ws.FyersDataSocket(
            access_token=access_token, log_path=".", litemode=False, write_to_file=False,
            reconnect=True, on_connect=on_open, on_message=on_message, on_error=on_error
        )
        threading.Thread(target=self.ws.connect, daemon=True).start()
        print(f"🚀 Live Bot Running | Balance: {self.paper_balance}")

        try:
            while True:
                time.sleep(1)
                self.monitor_positions()
        except KeyboardInterrupt: print("\n🛑 Bot Stopped.")

    def get_candle_start(self, ts):
        dt_obj = dt.datetime.fromtimestamp(ts)
        return dt_obj.replace(second=0, microsecond=0) - dt.timedelta(minutes=dt_obj.minute % BotConfig.TIMEFRAME_MIN)

    def process_tick(self, msg):
        sym = msg.get("symbol")
        ltp = float(msg.get("ltp"))
        ts = msg.get("timestamp", time.time())
        self.ltp_cache[sym] = ltp

        c_start = self.get_candle_start(ts)
        key = (sym, c_start)
        if key not in self.candles_build:
            self.candles_build[key] = {"o": ltp, "h": ltp, "l": ltp, "c": ltp, "v": 0}
        else:
            c = self.candles_build[key]
            c["h"] = max(c["h"], ltp)
            c["l"] = min(c["l"], ltp)
            c["c"] = ltp
            if "v" in msg: c["v"] += msg.get("v") # Approx

        tick_dt = dt.datetime.fromtimestamp(ts)
        candle_end_time = c_start + dt.timedelta(minutes=BotConfig.TIMEFRAME_MIN)
        if tick_dt >= (candle_end_time - dt.timedelta(seconds=1)):
            if key not in self.processed_candles:
                self.processed_candles.add(key)
                self.analyze_completed_candle(sym, self.candles_build[key], c_start)

    def analyze_completed_candle(self, sym, candle_dict, start_time):
        new_row = {
            "timestamp": pd.Timestamp(start_time).tz_localize(None).tz_localize('UTC').tz_convert('Asia/Kolkata'),
            "open": candle_dict["o"], "high": candle_dict["h"], "low": candle_dict["l"], "close": candle_dict["c"],
            "volume": candle_dict.get("v", 1000)
        }
        df = self.history_df.get(sym)
        if df is None: return
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        if len(df) > 200: df = df.iloc[-200:]
        df = Strategy.calculate_indicators(df)
        self.history_df[sym] = df

        curr, prev = df.iloc[-1], df.iloc[-2]
        sig_type, sl = Strategy.detect_signal(curr, prev)

        if sig_type:
            print(f"🚀 SIGNAL ({sig_type}) on {sym} @ {curr['close']}")
            self.execute_trade_signal(sym, sig_type, curr['close'], sl)

    def execute_trade_signal(self, sym, sig_type, spot_price, stop_loss_level):
        if sym in self.active_positions: return

        strike_step = 50 if "NIFTY50" in sym else 100
        if "BANK" in sym: strike_step = 100

        opt_type = "CE" if sig_type == "BUY" else "PE"
        atm_strike = round(spot_price / strike_step) * strike_step

        qty = BotConfig.LOT_SIZES.get(BotConfig.INDEX_MAP.get(sym, 'NIFTY'), 50)
        pos = PaperPosition(sym, f"{opt_type}_{atm_strike}", 100.0, stop_loss_level, qty, spot_price, sig_type)
        self.active_positions[sym] = pos
        print(f"✅ PAPER {sig_type} OPEN: {sym} | Opt: {opt_type} {atm_strike} | SL: {stop_loss_level}")

    def monitor_positions(self):
        for sym in list(self.active_positions.keys()):
            pos = self.active_positions[sym]
            curr_spot = self.ltp_cache.get(sym)
            if not curr_spot: continue

            exit_triggered = False
            pnl = 0
            spot_change = curr_spot - pos.entry_spot
            if pos.side == "SELL": spot_change = -spot_change

            pos.pnl = (spot_change * 0.5) * pos.qty

            if pos.side == "BUY" and curr_spot <= pos.sl_spot: exit_triggered = True
            elif pos.side == "SELL" and curr_spot >= pos.sl_spot: exit_triggered = True

            if pos.pnl <= -(pos.entry * BotConfig.OPTION_SL_PCT * pos.qty): exit_triggered = True

            if exit_triggered:
                print(f"❌ CLOSING {sym} | PnL: {pos.pnl:.2f}")
                del self.active_positions[sym]


# ============================================================================
# --- SECTION 6: MAIN EXECUTION ---
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="All-In-One VBO Bot")
    parser.add_argument("mode", nargs='?', choices=["setup", "backtest", "run"], help="Mode")
    parser.add_argument("--app_id", help="Fyers App ID")
    parser.add_argument("--secret_key", help="Fyers Secret Key")
    parser.add_argument("--redirect_url", help="Fyers Redirect URL")
    parser.add_argument("--retrain", action="store_true", help="Ignored")

    args = parser.parse_args()
    mode = args.mode

    if not mode:
        print("\n--- All-In-One VBO Bot ---")
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
    elif mode == "backtest": run_backtester()
    elif mode == "run": LivePaperBot().run()

if __name__ == "__main__": main()
