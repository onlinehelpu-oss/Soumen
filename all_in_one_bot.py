"""
ALL-IN-ONE GREEN-HAMMER OPTION BOT
==================================

**Disclaimer:**
This bot is intended for educational and paper trading purposes.
Live trading involves significant financial risk. Use at your own discretion.

**Description:**
This single-file script consolidates three key functionalities:
1. Credential Setup: Securely creates your API configuration file.
2. Backtester: Downloads historical data (Spot Index) and tests the Green Hammer strategy.
3. Live Bot: Runs the strategy in a live (paper trading) environment on OPTION CHARTS.

**Strategy (Green-Hammer / Green-Pinbar):**
- **Pattern:** Bullish Hammer (Green Body, Long Lower Wick) on the **OPTION CHART**.
- **Trend Filter:** Price > Regime EMA (26 period).
- **Entry:** Breakout above the signal candle's High.
- **Exits:** Target (R:R), Stop Loss (Candle Low), or Time-based exit.
- **Assets:** NIFTY, BANKNIFTY, FINNIFTY, SENSEX Options (CE).

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
import requests
import io

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
    class MockFyersModel:
        def __init__(self, **kwargs):
            self.client_id = kwargs.get("client_id", "MOCK")
            self.token = kwargs.get("token", "MOCK")
        def history(self, data): return {"s": "ok", "candles": []}
        def quotes(self, data): return {"s": "ok", "d": []}
        def optionchain(self, data): return {"s": "ok", "data": {"optionsChain": []}}
        def place_order(self, data): return {"s": "ok", "id": "MOCK_ORD"}

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
    """Consolidated configuration."""
    LOGIN_DETAILS_FILE = "fyers_login_details.json"
    TOKENS_STORE = "tokens_store.json"
    TOKENS_DIR = "AccessToken"
    TRADE_LOG_FILE = "paper_trade_log.csv"

    # Strategy
    TIMEFRAME_MIN = 1
    REGIME_EMA_PERIOD = 26
    R_MULTIPLIER = 1.0
    ENTRY_BUFFER = 0.05
    STRIKE_DISTANCE = 0 # 0=ATM, -1=ITM, 1=OTM

    # Candle Geometry (Green Hammer)
    UPPER_WICK_MAX = 25.0
    BODY_MIN = 5.0
    BODY_MAX = 30.0
    LOWER_WICK_MIN = 50.0
    LOWER_WICK_MAX = 80.0

    # Risk
    PAPER_BALANCE = 100000
    LOT_MULTIPLIER = 1

    # Indices to Trade
    SPOT_INDICES = [
        'NSE:NIFTY50-INDEX',
        'NSE:NIFTYBANK-INDEX',
        'BSE:SENSEX-INDEX'
    ]

    # Times
    ENTRY_CUTOFF = dt.time(15, 0)
    EXIT_ALL_TIME = dt.time(15, 9)


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
    credentials = {"api_key": app_id, "api_secret": secret_key, "redirect_url": redirect_url}
    try:
        _write_json(BotConfig.LOGIN_DETAILS_FILE, credentials)
        print(f"✅ Successfully created '{BotConfig.LOGIN_DETAILS_FILE}'")
    except Exception as e:
        print(f"❌ Error creating file: {e}")

def get_fyers_instance():
    creds = _read_json(BotConfig.LOGIN_DETAILS_FILE)
    if not creds:
        print(f"❌ Missing '{BotConfig.LOGIN_DETAILS_FILE}'. Run 'setup' first.")
        sys.exit(1)

    client_id = creds.get("api_key")
    secret_key = creds.get("api_secret")
    redirect_uri = creds.get("redirect_url")
    today_token_path = os.path.join(BotConfig.TOKENS_DIR, f"{str(dt.date.today())}.json")

    # 1. Cached Token
    access_token = _read_json(today_token_path)
    if isinstance(access_token, str):
        return fyersModel.FyersModel(client_id=client_id, token=access_token, log_path="", is_async=False)

    # 2. Refresh Token
    store = _read_json(BotConfig.TOKENS_STORE)
    refresh_token = store.get("refresh_token")
    if refresh_token:
        print("🔄 Trying Refresh Token...")
        try:
            session = fyersModel.SessionModel(client_id=client_id, secret_key=secret_key, redirect_uri=redirect_uri, response_type="code", grant_type="refresh_token")
            session.set_token(refresh_token)
            resp = session.generate_token()
            if resp.get("s") == "ok":
                _write_json(today_token_path, resp["access_token"])
                if resp.get("refresh_token"): _write_json(BotConfig.TOKENS_STORE, {"refresh_token": resp.get("refresh_token")})
                print("✅ Token Refreshed.")
                return fyersModel.FyersModel(client_id=client_id, token=resp["access_token"], log_path="", is_async=False)
        except Exception: pass

    # 3. Manual Login
    print("👉 Manual Login Required.")
    session = fyersModel.SessionModel(client_id=client_id, secret_key=secret_key, redirect_uri=redirect_uri, response_type="code", grant_type="authorization_code")
    print(f"Login URL: {session.generate_authcode()}")
    webbrowser.open(session.generate_authcode(), new=1)
    auth_code = input("Paste Auth Code: ").strip()
    session.set_token(auth_code)
    resp = session.generate_token()
    if resp.get("s") == "ok":
        _write_json(today_token_path, resp["access_token"])
        if resp.get("refresh_token"): _write_json(BotConfig.TOKENS_STORE, {"refresh_token": resp.get("refresh_token")})
        print("✅ Login Successful.")
        return fyersModel.FyersModel(client_id=client_id, token=resp["access_token"], log_path="", is_async=False)
    else:
        print(f"❌ Login Failed: {resp}")
        sys.exit(1)


# ============================================================================
# --- SECTION 3: STRATEGY LOGIC ---
# ============================================================================

def is_bullish_hammer_candle(o, h, l, c, prev_o, prev_c, min_range_pct=0.0015):
    """
    Green Hammer Detection:
    - Current: Green
    - Prev: Red
    - Lower Wick: 50-80%
    - Body: 5-30%
    - Upper Wick: 0-25%
    """
    if c <= o: return False # Green
    if prev_c >= prev_o: return False # Prev Red

    total_range = h - l
    if total_range == 0 or c == 0: return False
    if (total_range / c) < min_range_pct: return False

    upper_pct = ((h - c) / total_range) * 100
    body_pct = ((c - o) / total_range) * 100
    lower_pct = ((o - l) / total_range) * 100

    return (
        (BotConfig.LOWER_WICK_MIN <= lower_pct <= BotConfig.LOWER_WICK_MAX) and
        (BotConfig.BODY_MIN <= body_pct <= BotConfig.BODY_MAX) and
        (upper_pct <= BotConfig.UPPER_WICK_MAX)
    )

class RealTimeOptionManager:
    def __init__(self, fy):
        self.fy = fy
        self.lot_cache = {}

    def get_lot_size(self, symbol, exchange):
        if not HAS_FYERS: return 65
        key = (symbol, exchange)
        if key in self.lot_cache: return self.lot_cache[key]

        try:
            url = f'https://public.fyers.in/sym_details/{exchange}_FO.csv'
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                df = pd.read_csv(io.StringIO(resp.text), header=None)
                row = df[df[9] == symbol] # Col 9 is symbol, Col 3 is lot size
                if not row.empty:
                    ls = int(row.iloc[0, 3])
                    self.lot_cache[key] = ls
                    return ls
        except: pass
        return 65 # Fallback

    def refresh_options(self, indices, strike_distance=0):
        print("🔄 Refreshing Option Chain...")
        selected = {}
        for idx in indices:
            try:
                # 1. Get Spot LTP
                q = self.fy.quotes({"symbols": idx})
                if not q.get('d'): continue
                spot_ltp = float(q['d'][0]['v']['lp'])

                # 2. Strike Interval & ATM
                interval = 100 if 'BANK' in idx or 'SENSEX' in idx else 50
                atm_strike = round(spot_ltp / interval) * interval
                target_strike = atm_strike + (strike_distance * interval) # + for OTM (CE), - for ITM (CE)

                # 3. Fetch Chain
                # Handle BSE symbol format for chain
                chain_sym = idx
                resp = self.fy.optionchain({"symbol": chain_sym})
                if resp.get('s') != 'ok': continue

                options = resp['data']['optionsChain']

                # 4. Filter CE & Expiry
                ce_opts = [o for o in options if o.get('option_type') == 'CE' and abs(float(o.get('strike_price', 0)) - target_strike) < 1.0]

                # Find nearest expiry
                if ce_opts:
                    # Sort by expiry ts (handle missing keys safely)
                    ce_opts.sort(key=lambda x: x.get('expiry_date', x.get('expiry', 9999999999))) # Timestamp usually
                    best_opt = ce_opts[0]

                    sym = best_opt.get('symbol')
                    if sym:
                        print(f"✅ Found {sym} (Strike: {best_opt.get('strike_price')}) for {idx}")
                        selected[sym] = best_opt
                    else:
                        print(f"⚠️ Found option but symbol is missing: {best_opt}")
            except Exception as e:
                print(f"❌ Error refreshing {idx}: {e}")

        return selected


# ============================================================================
# --- SECTION 4: BACKTESTER LOGIC (SPOT PROXY) ---
# ============================================================================

def run_backtester():
    print(f"\n🚀 BACKTESTING (Strategy: Green Hammer on Spot Index Proxy)")
    print("⚠️ Note: Simulating option trades using Spot Index moves (Delta 0.5)")

    fyers = get_fyers_instance()

    for symbol in BotConfig.SPOT_INDICES:
        print(f"\nTesting {symbol}...")
        try:
            # Get 100 days data
            end = dt.date.today()
            start = end - dt.timedelta(days=100)
            data = {
                "symbol": symbol, "resolution": str(BotConfig.TIMEFRAME_MIN),
                "date_format": "1", "range_from": start.strftime("%Y-%m-%d"),
                "range_to": end.strftime("%Y-%m-%d"), "cont_flag": "1"
            }
            resp = fyers.history(data)
            if not resp.get("candles"): continue

            df = pd.DataFrame(resp["candles"], columns=["t","o","h","l","c","v"])
            df['t'] = pd.to_datetime(df['t'], unit='s').dt.tz_localize('UTC').dt.tz_convert('Asia/Kolkata')

            # EMA
            df['ema'] = df['c'].ewm(span=BotConfig.REGIME_EMA_PERIOD, adjust=False).mean()

            trades = 0
            wins = 0
            pnl = 0.0

            for i in range(1, len(df)-1):
                curr = df.iloc[i]
                prev = df.iloc[i-1]

                # Signal
                if not is_bullish_hammer_candle(curr.o, curr.h, curr.l, curr.c, prev.o, prev.c):
                    continue

                # Filter: Close > EMA
                if curr.c <= curr.ema: continue

                # Entry: Breakout of High
                trigger = curr.h + (curr.h * 0.0005) # Small buffer
                sl = curr.l
                risk = trigger - sl
                if risk <= 0: continue
                target = trigger + (risk * BotConfig.R_MULTIPLIER)

                # Next Candle check
                next_c = df.iloc[i+1]
                if next_c.h > trigger:
                    trades += 1
                    # Outcome check (Simplified)
                    if next_c.l <= sl:
                        pnl -= risk * 0.5 # Delta 0.5
                    elif next_c.h >= target:
                        pnl += (risk * BotConfig.R_MULTIPLIER) * 0.5
                        wins += 1
                    else:
                        pnl += (next_c.c - trigger) * 0.5 # EOD

            print(f"  Trades: {trades}, Wins: {wins}, Est PnL Points: {pnl:.2f}")

        except Exception as e:
            print(f"  Error: {e}")


# ============================================================================
# --- SECTION 5: LIVE PAPER BOT LOGIC ---
# ============================================================================

class PaperPosition:
    def __init__(self, symbol, entry, sl, tgt, qty):
        self.symbol = symbol
        self.entry = entry
        self.sl = sl
        self.tgt = tgt
        self.qty = qty
        self.pnl = 0.0

class LivePaperBot:
    def __init__(self):
        self.fyers = get_fyers_instance()
        self.opt_mgr = RealTimeOptionManager(self.fyers)
        self.active_opts = {} # Symbol -> Details

        self.candles_build = {}
        self.processed_candles = set()
        self.triggers = {} # Sym -> {level, sl, active_until, lot_size}
        self.ema_cache = {}
        self.positions = {}

        self.balance = BotConfig.PAPER_BALANCE

    def run(self):
        # 1. Refresh Options
        self.active_opts = self.opt_mgr.refresh_options(BotConfig.SPOT_INDICES, BotConfig.STRIKE_DISTANCE)
        if not self.active_opts:
            print("❌ No options found. Exiting.")
            return

        # 2. Init History for EMA (on Options)
        print("🔄 Warming up EMA on Option Charts...")
        start_dt = dt.date.today() - dt.timedelta(days=5)
        for sym in self.active_opts:
            time.sleep(0.2)
            try:
                resp = self.fyers.history({
                    "symbol": sym, "resolution": str(BotConfig.TIMEFRAME_MIN),
                    "date_format": "1", "range_from": start_dt.strftime("%Y-%m-%d"),
                    "range_to": dt.date.today().strftime("%Y-%m-%d"), "cont_flag": "1"
                })
                if resp.get("candles"):
                    closes = [c[4] for c in resp["candles"]]
                    if closes:
                        # Simple EMA calc
                        ema = closes[0]
                        k = 2/(BotConfig.REGIME_EMA_PERIOD+1)
                        for c in closes[1:]:
                            ema = (c*k) + (ema*(1-k))
                        self.ema_cache[sym] = ema
            except: pass

        # 3. Connect WS
        print(f"🔌 Connecting WS... Subscribing to {len(self.active_opts)} options.")
        token = f"{self.fyers.client_id}:{self.fyers.token}"
        self.ws = data_ws.FyersDataSocket(
            access_token=token, log_path=".", litemode=False, write_to_file=False,
            reconnect=True, on_connect=self.on_open, on_message=self.on_message, on_error=self.on_error
        )
        self.ws.connect()

    def on_open(self):
        print("✅ WebSocket Connected.")
        self.ws.subscribe(symbols=list(self.active_opts.keys()))

    def on_error(self, msg): print(f"WS Error: {msg}")

    def on_message(self, msg):
        if isinstance(msg, list):
            for m in msg: self.process_tick(m)
        elif isinstance(msg, dict) and msg.get("type") == "sf":
            self.process_tick(msg)

    def process_tick(self, msg):
        sym = msg.get("symbol")
        ltp = float(msg.get("ltp"))
        ts = msg.get("timestamp", time.time())

        # Debug First Tick
        if sym not in self.candles_build:
            print(f"[DEBUG] First tick: {sym} @ {ltp}")

        # Monitor Positions
        if sym in self.positions:
            self.monitor_trade(sym, ltp)

        # Candle Logic
        tick_dt = dt.datetime.fromtimestamp(ts)
        cstart = tick_dt.replace(second=0, microsecond=0) - dt.timedelta(minutes=tick_dt.minute % BotConfig.TIMEFRAME_MIN)
        key = (sym, cstart)

        if key not in self.candles_build:
            self.candles_build[key] = {"o": ltp, "h": ltp, "l": ltp, "c": ltp}
        else:
            c = self.candles_build[key]
            c["h"] = max(c["h"], ltp)
            c["l"] = min(c["l"], ltp)
            c["c"] = ltp

        # Completion (End of minute)
        end_time = cstart + dt.timedelta(minutes=BotConfig.TIMEFRAME_MIN)
        if tick_dt >= (end_time - dt.timedelta(seconds=1)):
            if key not in self.processed_candles:
                self.processed_candles.add(key)
                self.analyze_candle(sym, self.candles_build[key], cstart)

                # Update EMA
                close = self.candles_build[key]["c"]
                prev = self.ema_cache.get(sym, close)
                k = 2/(BotConfig.REGIME_EMA_PERIOD+1)
                self.ema_cache[sym] = (close*k) + (prev*(1-k))

        # Check Trigger
        if sym in self.triggers:
            trig = self.triggers[sym]
            now = dt.datetime.now()
            if now < trig["active_until"]:
                if ltp >= trig["level"]:
                    self.enter_trade(sym, ltp, trig["sl"], trig["lot_size"])
                    del self.triggers[sym]
            else:
                del self.triggers[sym]

    def analyze_candle(self, sym, curr, start_time):
        prev_start = start_time - dt.timedelta(minutes=BotConfig.TIMEFRAME_MIN)
        prev = self.candles_build.get((sym, prev_start))
        if not prev: return # Need prev for Red Candle check

        if is_bullish_hammer_candle(curr["o"], curr["h"], curr["l"], curr["c"], prev["o"], prev["c"]):
            # Filter: Price > EMA
            ema = self.ema_cache.get(sym, 0)
            if curr["c"] > ema:
                print(f"🕯️ Signal: {sym} (Hammer > EMA {ema:.2f})")

                # Exchange info for lot size
                exch = 'NSE' if 'NSE' in sym else 'BSE'
                ls = self.opt_mgr.get_lot_size(sym, exch)

                self.triggers[sym] = {
                    "level": curr["h"] + BotConfig.ENTRY_BUFFER,
                    "sl": curr["l"],
                    "active_until": dt.datetime.now() + dt.timedelta(minutes=BotConfig.TIMEFRAME_MIN),
                    "lot_size": ls
                }

    def enter_trade(self, sym, price, sl, lot_size):
        if sym in self.positions: return

        risk = price - sl
        if risk <= 0: return
        tgt = price + (risk * BotConfig.R_MULTIPLIER)
        qty = lot_size * BotConfig.LOT_MULTIPLIER

        self.positions[sym] = PaperPosition(sym, price, sl, tgt, qty)
        print(f"✅ ENTER BUY: {sym} @ {price} | Qty: {qty} | SL: {sl} | TGT: {tgt}")

    def monitor_trade(self, sym, ltp):
        pos = self.positions[sym]
        pnl = (ltp - pos.entry) * pos.qty

        if ltp <= pos.sl:
            print(f"❌ SL HIT: {sym} @ {ltp} | PnL: {pnl:.2f}")
            self.balance += pnl
            del self.positions[sym]
        elif ltp >= pos.tgt:
            print(f"🎯 TGT HIT: {sym} @ {ltp} | PnL: {pnl:.2f}")
            self.balance += pnl
            del self.positions[sym]


# ============================================================================
# --- SECTION 6: MAIN ---
# ============================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", nargs='?', choices=["setup", "backtest", "run"], help="Mode")
    parser.add_argument("--app_id", help="App ID")
    parser.add_argument("--secret_key", help="Secret Key")
    parser.add_argument("--redirect_url", help="Redirect URL")
    parser.add_argument("--retrain", action="store_true", help="Ignored")
    args = parser.parse_args()

    if not args.mode:
        print("\n--- All-In-One Green Hammer Option Bot ---")
        print("1. Setup Credentials")
        print("2. Run Backtester")
        print("3. Run Live Paper Trading Bot")
        print("4. Run Live Bot (Auto-Retrain -> Just Runs Live)")
        c = input("Enter choice: ")
        if c=='1': args.mode='setup'
        elif c=='2': args.mode='backtest'
        elif c in ['3','4']: args.mode='run'

    if args.mode == 'setup':
        setup_credentials(args.app_id or input("App ID: "), args.secret_key or input("Secret: "), args.redirect_url or input("URL: "))
    elif args.mode == 'backtest':
        run_backtester()
    elif args.mode == 'run':
        LivePaperBot().run()

if __name__ == "__main__":
    main()
