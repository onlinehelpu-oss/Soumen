# -*- coding: utf-8 -*-
"""
VWAP-RSI-EMA MCX Stocks
VWAP-EMA-RSI STRATEGY - STOCKS & MCX EXECUTION
- Tracks NSE Stocks and MCX Futures
- Executes trades in the corresponding symbol directly
- Entry Signal: VWAP body cross with EMA and RSI confirmation
- Exit Signal: Stop-loss or RSI-based exit signal
- STRICT NEXT CANDLE ENTRY
"""

from __future__ import annotations
import os
import sys
import json
import time
import math
import argparse
import threading
import atexit
import glob
import hashlib
import datetime
from urllib.parse import urlparse, parse_qs, quote
from typing import Dict, Optional, Tuple, List
from datetime import datetime as dt, timedelta, time as dtime

import requests
import pandas as pd
import numpy as np
import pytz

try:
    from fyers_apiv3 import fyersModel
    from fyers_apiv3.FyersWebsocket import data_ws
except Exception:
    fyersModel = None
    data_ws = None

# ===================== WATCHLIST =====================
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
]

# ===================== MCX LOT SIZES =====================
# Correct, hardcoded lot sizes for MCX as per user
MCX_LOTS = {
    "SILVERMIC": 1,
    "CRUDEOILM": 1,
    "NATGASMINI": 1,
}

# ============================== CONFIGURATION ==============================
TIMEFRAME_MIN = 30  # Any TF in minutes (1,2,3,5,10,15,30,60,...)

# RSI Parameters
RSI_PERIOD = 14
RSI_ENTRY_MIN = 40
RSI_ENTRY_MAX = 55
RSI_EXIT_MIN = 65
RSI_EXIT_MAX = 75

# EMA for ENTRY signal confirmation
ENTRY_FAST_EMA = 26  # e.g., EMA 9 (fast)

# Small Candle Guards
MIN_RANGE_PCT = 0.0015  # ignore if (H-L)/Close < 0.15%
MIN_BODY_TICKS = 0      # optional minimum body size; 0 disables
EMA_BUFFER = 0.0        # optional extra buffer above/below EMAs
REQUIRE_GREEN_SIGNAL = True

# ===================== TIME/ENTRY/EXIT RULES =====================
# Default (non-MCX) behaviour
ENTRY_BUFFER = 0.05  # buffer below signal low for breakout (we require strict cross below)
ENTRY_CUTOFF = dtime(15, 0)  # no new entries after 3:00 PM (non-MCX)
EXIT_ALL_TIME = dtime(15, 9)  # force-exit all open (non-MCX) positions at 3:09 PM

# MCX-specific times (user requested)
ENTRY_CUTOFF_MCX = dtime(22, 0)  # allow MCX signals up to 10:00 PM
EXIT_ALL_TIME_MCX = dtime(22, 50)  # force-exit all open MCX positions at 10:50 PM

FORCE_CLOSED_ALL = False
FORCE_CLOSED_ALL_MCX = False

# Carry Forward Flag
CARRY_FORWARD_ENABLED = False

# Product type for NSE stocks ("INTRADAY" or "CNC")
STOCK_PRODUCT_TYPE = "INTRADAY"

# Product type for MCX futures ("INTRADAY", "MARGIN", or "NRML")
MCX_PRODUCT_TYPE = "MARGIN"

# Position management
SL_MODE = "signal_low"  # "signal_low" or "swing_low"
SWING_LOOKBACK = 5  # used for swing-low

# Position sizing mode globals
POSITION_MODE = "qty"  # "alloc" or "qty"
ALLOCATION_AMOUNT = 20000
FIXED_QTY = 1  # default 1 share when using qty mode
QTY_MAP: Dict[str, int] = {}

MAX_CONCURRENT_POS = 3
DAILY_MAX_LOSS = 50000.0
TRADING_ENABLED = True
MAX_EXIT_RETRIES = 3
EXIT_RETRY_COOLDOWN_SECONDS = 10

# Market Hours (General bounds, specific checks in code)
TIMEZONE = "Asia/Kolkata"
IST = pytz.timezone(TIMEZONE)
MARKET_START = dtime(9, 15)
MARKET_END = dtime(15, 30) # Default NSE end

# Config files
LOG_FILE = "trade_log.csv"
STATE_DUMP = "symbol_states_stocks.json"
PARTIAL_CANDLES_FILE = "partial_candles_stocks.json"
STATE_PERSISTENCE_FILE = "bot_state_stocks.json"

CONFIG_FILE = "fyers_login_details.json"
TOKENS_DIR = "AccessToken"
TODAY = str(datetime.date.today())
TOKEN_PATH = os.path.join(TOKENS_DIR, f"{TODAY}.json")
SETTINGS_FILE = "settings.json"

# Re-auth guard to avoid infinite recursion
REAUTH_ATTEMPTS = 0
MAX_REAUTH_ATTEMPTS = 3

# ============================== HELPER FUNCTIONS ==============================

def is_mcx(symbol: str) -> bool:
    """Check if symbol is MCX based on prefix or name"""
    # Fyers v3 usually uses MCX:PREFIX...
    # Also fallback if not NSE/BSE and has typical MCX names
    if symbol.startswith("MCX:"):
        return True
    if symbol.startswith("NSE:") or symbol.startswith("BSE:"):
        return False
    # Fallback check for known MCX roots
    base = symbol.split(':')[1] if ':' in symbol else symbol
    for k in MCX_LOTS.keys():
        if base.startswith(k):
            return True
    return False

def get_lot_size(symbol: str) -> int:
    """Gets lot size for a given symbol."""
    if symbol.endswith("-EQ") or symbol.startswith("NSE:"):
        return 1

    # Extract base symbol for MCX futures
    # e.g., 'MCX:CRUDEOILM26JANFUT' -> 'CRUDEOILM26JANFUT'
    parts = symbol.split(':')
    base = parts[1] if len(parts) > 1 else symbol

    for mcx_base, lot in MCX_LOTS.items():
        if base.startswith(mcx_base):
            return lot

    print(f"⚠️ Could not determine lot size for {symbol}, defaulting to 1.")
    return 1

def get_product_type(symbol: str) -> str:
    return MCX_PRODUCT_TYPE if is_mcx(symbol) else STOCK_PRODUCT_TYPE

def is_market_hours(symbol: str) -> bool:
    """Check if current time is within market hours for the specific symbol"""
    now = dt.now(IST).time()
    if is_mcx(symbol):
        # MCX: Approx 09:00 to 23:30/23:55
        return dtime(9, 0) <= now <= dtime(23, 55)
    else:
        # NSE: 09:15 to 15:30
        return MARKET_START <= now <= MARKET_END

def is_entry_allowed(symbol: str) -> bool:
    now = dt.now(IST).time()
    if is_mcx(symbol):
        return dtime(9, 0) <= now <= ENTRY_CUTOFF_MCX
    else:
        return MARKET_START <= now <= ENTRY_CUTOFF

def check_exit_all_condition(symbol: str) -> bool:
    if CARRY_FORWARD_ENABLED:
        return False

    now = dt.now(IST).time()
    if is_mcx(symbol):
        return now >= EXIT_ALL_TIME_MCX
    else:
        return now >= EXIT_ALL_TIME

# ============================== FYERS LOGIN ==============================
def load_or_prompt_creds():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, dict) and "api_key" in data and "api_secret" in data and "redirect_url" in data:
                return data
        except Exception:
            pass

    print("---- Enter your Fyers Login Credentials (v3) ----")
    creds = {
        "api_key": input("Enter APP ID (e.g., ABCDE12345-100): ").strip(),
        "api_secret": input("Enter SECRET ID: ").strip(),
        "redirect_url": input("Enter Redirect URL (must match app): ").strip(),
    }
    if input("Save to 'fyers_login_details.json'? (Y/N): ").strip().upper() == "Y":
        try:
            base = {}
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r") as f:
                    base = json.load(f) or {}
            base.update(creds)
            with open(CONFIG_FILE, "w") as f:
                json.dump(base, f, indent=2)
            print(f"Saved '{CONFIG_FILE}'.")
        except Exception as e:
            print(f"[auth] Could not save creds: {e}")
    else:
        print("Skipping save.")
    return creds


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
                sleep_s = min(2 ** attempt, 30)
                print(f"[{attempt}/{max_retries}] 503 from auth server. Retrying in {sleep_s}s...")
                time.sleep(sleep_s)
                continue
            r.raise_for_status()
            data = r.json()
            if data.get("s") == "error":
                raise RuntimeError(f"Fyers error {data.get('code')}: {data.get('message')}")
            return data
        except requests.RequestException as e:
            if attempt == max_retries:
                raise
            sleep_s = min(2 ** attempt, 30)
            print(f"[{attempt}/{max_retries}] Network error: {e}. Retrying in {sleep_s}s...")
            time.sleep(sleep_s)


def get_access_token() -> dict:
    """Get or refresh access token"""
    # Try to load from file first
    if os.path.exists(TOKEN_PATH):
        try:
            with open(TOKEN_PATH, "r") as f:
                data = json.load(f)
            if isinstance(data, str):
                access_token = data.strip()
                return {"access_token": access_token}
        except Exception:
            pass

            # Interactive login
    print("[auth] No existing access token found. Starting interactive login...")
    creds = load_or_prompt_creds()
    app_id = creds["api_key"]
    secret_id = creds["api_secret"]
    redirect_uri = creds["redirect_url"]

    auth_url = build_auth_url(app_id, redirect_uri)
    print("\nLogin URL (open in browser, allow & complete login):")
    print(auth_url)

    user_val = input("\nPaste the FULL redirect URL after login, or just the 'code=' value here: ").strip()
    try:
        auth_code = extract_code(user_val)
    except Exception as e:
        print(f"Could not extract code: {e}")
        raise

    token_resp = validate_authcode(app_id, secret_id, auth_code)
    access_token = token_resp.get("access_token")
    if not access_token:
        raise RuntimeError(f"Unexpected token response: {token_resp}")

        # Save token
    os.makedirs(TOKENS_DIR, exist_ok=True)
    with open(TOKEN_PATH, "w") as f:
        json.dump(access_token, f)

    print(f"[auth] Token saved to {TOKEN_PATH}")
    return {"access_token": access_token}


# ============================== SYMBOL STATE ==============================
class SymbolState:
    def __init__(self, symbol: str):
        self.symbol = symbol  # Tradable symbol (e.g., NSE:RELIANCE-EQ)
        self.ltp = 0.0
        self.entry_price = 0.0  # Entry price
        self.data = pd.DataFrame()  # Price data for analysis
        self.status = "watch"  # watch, entry_pending, position, cooldown
        self.signal_candle = None
        self.signal_close_ts = None

        self.stop_price = 0.0
        self.high_price = 0.0
        self.qty = 0
        self.gtt_order_id = None

        # Candle tracking
        self.last_candle_ts = None
        self.just_entered = False
        self.last_update_ts = 0

    def reset_position(self):
        """Reset position state"""
        self.entry_price = 0.0
        self.stop_price = 0.0
        self.high_price = 0.0
        self.qty = 0
        self.just_entered = False
        self.gtt_order_id = None
        self.last_update_ts = 0

    def __repr__(self):
        return f"<State {self.symbol} {self.status} qty={self.qty}>"


SYMBOL_STATES: Dict[str, SymbolState] = {s: SymbolState(s) for s in SYMBOLS}

# Active subscriptions for WebSocket
ACTIVE_SUBSCRIPTIONS: List[str] = SYMBOLS.copy()


def save_state():
    """Saves the current state of all symbols to a file."""
    try:
        state_to_save = {}
        for symbol, state in SYMBOL_STATES.items():
            # Convert non-serializable objects to string representations
            state_dict = state.__dict__.copy()
            state_dict['data'] = state.data.to_json(orient='split') if not state.data.empty else None
            state_dict['signal_candle'] = state.signal_candle if state.signal_candle else None
            state_dict['last_candle_ts'] = state.last_candle_ts.isoformat() if state.last_candle_ts else None
            state_to_save[symbol] = state_dict

        with open(STATE_PERSISTENCE_FILE, 'w') as f:
            json.dump(state_to_save, f, indent=4)
        print(f"[state] Successfully saved state to {STATE_PERSISTENCE_FILE}")
    except Exception as e:
        print(f"[state] Error saving state: {e}")


def load_state():
    """Loads the state of all symbols from a file."""
    if not os.path.exists(STATE_PERSISTENCE_FILE):
        print("[state] No state file found to load.")
        return

    try:
        with open(STATE_PERSISTENCE_FILE, 'r') as f:
            loaded_states = json.load(f)

        for symbol, state_dict in loaded_states.items():
            if symbol in SYMBOL_STATES:
                state = SYMBOL_STATES[symbol]
                state.status = state_dict.get('status', 'watch')
                state.entry_price = state_dict.get('entry_price', 0.0)
                state.stop_price = state_dict.get('stop_price', 0.0)
                state.qty = state_dict.get('qty', 0)

                # Restore DataFrame
                data_json = state_dict.get('data')
                if data_json:
                    df = pd.read_json(data_json, orient='split')
                    df.index = pd.to_datetime(df.index)
                    state.data = df
                else:
                    state.data = pd.DataFrame()

                # Restore timestamps and other objects
                ts_str = state_dict.get('last_candle_ts')
                if ts_str:
                    state.last_candle_ts = dt.fromisoformat(ts_str)
                state.signal_candle = state_dict.get('signal_candle')

                print(f"[state] Loaded state for {symbol}: Status={state.status}, Qty={state.qty}")

        print(f"[state] Successfully loaded state from {STATE_PERSISTENCE_FILE}")
    except Exception as e:
        print(f"[state] Error loading state: {e}")


# ============================== CANDLE MANAGER ==============================

def sync_with_broker_positions():
    """Syncs the bot's internal state with the broker's positions."""
    if not FYERS:
        return

    try:
        response = FYERS.positions()
        if response.get('s') != 'ok':
            print(f"[sync] Failed to fetch broker positions: {response.get('message')}")
            return

        broker_positions = response.get('netPositions', [])

        # Map symbol -> position dict for easier lookup
        # Filter for non-zero qty
        active_positions = {
            pos['symbol']: pos
            for pos in broker_positions
            if pos.get('netQty', 0) != 0
        }

        # 1. Update BOT state based on BROKER state
        for symbol, state in SYMBOL_STATES.items():
            broker_pos = active_positions.get(symbol)

            # CASE A: Bot thinks it has a position
            if state.status == "position":
                if not broker_pos:
                    # Position exists in bot but NOT in broker -> Manual Close
                    print(f"\n[sync] Position for {symbol} was closed manually at broker.")
                    print(f"[sync] Resetting state for {symbol} to WATCH.")
                    state.reset_position()
                    state.status = "watch"
                else:
                    # Position exists in both -> Sync details (Qty, Entry Price)
                    net_qty = int(broker_pos.get('netQty', 0))
                    if net_qty > 0:
                        # Long position logic
                        if state.qty != net_qty:
                            print(f"[sync] {symbol} Qty mismatch (Bot: {state.qty}, Broker: {net_qty}). Updating.")
                            state.qty = net_qty

                        # Optional: Sync entry price if needed
                        avg_price = float(broker_pos.get('avgPrice', 0.0))
                        if avg_price == 0:
                            avg_price = float(broker_pos.get('buyAvg', 0.0))

                        if avg_price > 0 and (state.entry_price == 0 or abs(state.entry_price - avg_price) > 0.5):
                            state.entry_price = avg_price
                            print(f"[sync] {symbol} Entry updated to {state.entry_price:.2f}")

                    elif net_qty < 0:
                        # Bot is Long-only logic, but broker has Short?
                        # For now, treat as closed or warn?
                        # Assuming user manually reversed. Let's reset to watch to avoid messing up.
                        print(f"[sync] {symbol} found SHORT position (Qty: {net_qty}) but bot is LONG-only.")
                        print("[sync] Resetting bot state to WATCH.")
                        state.reset_position()
                        state.status = "watch"

            # CASE B: Bot is in WATCH mode (no position)
            elif state.status == "watch":
                if broker_pos:
                    # Position found at broker but bot is watching -> Manual Entry or Restart
                    net_qty = int(broker_pos.get('netQty', 0))
                    if net_qty > 0:
                        print(f"\n[sync] Found existing LONG position for {symbol} (Qty: {net_qty}). Adopting it.")
                        state.qty = net_qty

                        entry_price = float(broker_pos.get('avgPrice', 0.0))
                        if entry_price == 0:
                            entry_price = float(broker_pos.get('buyAvg', 0.0))

                        state.entry_price = entry_price
                        state.status = "position"
                        state.just_entered = False # Don't apply entry cooldown

                        # Set a default SL if we adopted it (optional, maybe Swing Low)
                        # Since we don't know the signal candle, we can't use 'signal_low' easily.
                        # We'll rely on the user or next candle evaluation?
                        # Or calculate a rough SL:
                        if SL_MODE == "swing_low" and not state.data.empty:
                             recent_lows = state.data['low'].tail(SWING_LOOKBACK)
                             state.stop_price = recent_lows.min()
                        else:
                             # Fallback SL (e.g. 1% below entry)
                             state.stop_price = state.entry_price * 0.99

                        print(f"[sync] Adopted {symbol}. Entry: {state.entry_price}, Calc SL: {state.stop_price}")


        update_subscriptions()  # Ensure subscriptions are correct after sync

    except Exception as e:
        print(f"[sync] Error during position synchronization: {e}")


class CandleManager:
    def __init__(self, timeframe_min: int = 5, on_candle=None, tz="Asia/Kolkata"):
        self.tf = int(timeframe_min)
        self.on_candle = on_candle
        self.tz = pytz.timezone(tz)
        self.lock = threading.RLock()
        self.partial: Dict[str, dict] = {}
        self.history: Dict[str, pd.DataFrame] = {}

    def _floor_ts(self, ts: dt, symbol: str) -> dt:
        if ts.tzinfo is None:
            ts = self.tz.localize(ts)
        else:
            ts = ts.astimezone(self.tz)
        ts = ts.replace(tzinfo=None)

        # Determine market start in minutes
        if is_mcx(symbol):
            start_min = 9 * 60  # 09:00
        else:
            start_min = 9 * 60 + 15  # 09:15

        current_min = ts.hour * 60 + ts.minute
        diff = current_min - start_min
        floored_diff = (diff // self.tf) * self.tf
        candle_start_min = start_min + floored_diff

        day_start = ts.replace(hour=0, minute=0, second=0, microsecond=0)
        return day_start + timedelta(minutes=candle_start_min)

    def _parse_ts(self, ts_val) -> dt:
        if ts_val is None:
            return dt.now(self.tz).replace(tzinfo=None)
        if isinstance(ts_val, (int, float)):
            return dt.fromtimestamp(float(ts_val), self.tz).replace(tzinfo=None)
        if isinstance(ts_val, str):
            try:
                dtobj = pd.to_datetime(ts_val)
                if dtobj.tzinfo is None:
                    dtobj = self.tz.localize(dtobj).replace(tzinfo=None)
                else:
                    dtobj = dtobj.astimezone(self.tz).replace(tzinfo=None)
                return dtobj
            except Exception:
                return dt.now(self.tz).replace(tzinfo=None)
        if isinstance(ts_val, dt):
            if ts_val.tzinfo is None:
                return self.tz.localize(ts_val).replace(tzinfo=None)
            return ts_val.astimezone(self.tz).replace(tzinfo=None)
        return dt.now(self.tz).replace(tzinfo=None)

    def process_tick(self, tick: dict):
        try:
            symbol = tick.get("symbol")
            if not symbol:
                return

            ltp = tick.get("ltp")
            if ltp is None:
                return
            ltp = float(ltp)
            vtt = int(tick.get("vtt", 0))

            ts = self._parse_ts(tick.get("timestamp"))
            candle_start = self._floor_ts(ts, symbol)

            with self.lock:
                p = self.partial.get(symbol)
                if p is None:
                    new_p = {"ts": candle_start, "open": ltp, "high": ltp,
                             "low": ltp, "close": ltp, "ticks": 1,
                             "start_vtt": vtt, "end_vtt": vtt}
                    self.partial[symbol] = new_p
                    return

                if candle_start == p["ts"]:
                    p["high"] = max(p["high"], ltp)
                    p["low"] = min(p["low"], ltp)
                    p["close"] = ltp
                    p["ticks"] = p.get("ticks", 0) + 1
                    p["end_vtt"] = vtt
                    return

                    # Complete the candle
                completed = dict(p)
                candle_volume = completed.get("end_vtt", 0) - completed.get("start_vtt", 0)
                candle_out = {
                    "symbol": symbol,
                    "ts": completed["ts"],
                    "open": completed["open"],
                    "high": completed["high"],
                    "low": completed["low"],
                    "close": completed["close"],
                    "volume": candle_volume,
                    "ticks": completed.get("ticks", 1),
                }

                # Append to history
                df = self.history.get(symbol)
                row = {"open": candle_out["open"], "high": candle_out["high"],
                        "low": candle_out["low"], "close": candle_out["close"],
                        "volume": candle_out["volume"]}
                ts_idx = pd.to_datetime(candle_out["ts"])

                if df is None:
                    df = pd.DataFrame([row], index=[ts_idx])
                else:
                    df = pd.concat([df, pd.DataFrame([row], index=[ts_idx])])
                    if len(df) > 2000:
                        df = df.tail(2000)
                df.index.name = "datetime"
                self.history[symbol] = df

                # Call callback
                if callable(self.on_candle):
                    try:
                        self.on_candle(symbol, candle_out)
                    except Exception as e:
                        print(f"[CandleManager] callback error: {e}")

                # Start new partial candle
                new_partial = {"ts": candle_start, "open": ltp, "high": ltp,
                               "low": ltp, "close": ltp, "ticks": 1,
                               "start_vtt": vtt, "end_vtt": vtt}
                self.partial[symbol] = new_partial

        except Exception as e:
            print(f"[CandleManager:process_tick] error: {e}")


CANDLE_MANAGER: Optional[CandleManager] = None

# ============================== ORDER HELPERS ==============================
FYERS = None
FYERS_SOCKET = None
ACCESS_TOKEN = None


def update_subscriptions():
    """Update WebSocket subscriptions"""
    # For stocks/futures, we just need to subscribe to the symbols themselves
    # The SYMBOLS list is already the trading list
    global ACTIVE_SUBSCRIPTIONS

    if set(SYMBOLS) != set(ACTIVE_SUBSCRIPTIONS):
        ACTIVE_SUBSCRIPTIONS = SYMBOLS.copy()
        if FYERS_SOCKET:
            try:
                FYERS_SOCKET.subscribe(symbols=ACTIVE_SUBSCRIPTIONS, data_type="SymbolUpdate")
                print(f"[ws] Updated subscriptions: {len(ACTIVE_SUBSCRIPTIONS)} symbols")
            except Exception as e:
                print(f"[ws] Subscription update failed: {e}")


def get_order_details(order_id: str):
    """Get order details including fill price"""
    if FYERS is None:
        return None

    try:
        # Get orderbook to find our order
        orderbook = FYERS.orderbook()
        if isinstance(orderbook, dict) and orderbook.get("s") == "ok":
            for order in orderbook.get("orderBook", []):
                if str(order.get("id")) == str(order_id):
                    return order
    except Exception as e:
        print(f"[order] Failed to get details for {order_id}: {e}")

    return None


def place_market_order(symbol: str, qty: int, side: int) -> dict:
    """Place market order for symbol"""
    if not TRADING_ENABLED:
        return {"s": "error", "message": "trading disabled"}

    if not is_market_hours(symbol):
        return {"s": "error", "message": "outside market hours"}

    side_str = "BUY" if side == 1 else "SELL"
    product = get_product_type(symbol)

    data = {
        "symbol": symbol,
        "qty": qty,
        "type": 2,  # Market order
        "side": side,
        "productType": product,
        "limitPrice": 0,
        "stopPrice": 0,
        "validity": "DAY",
        "disclosedQty": 0,
        "offlineOrder": False,
    }

    print(f"[order] Placing market {side_str} for {qty} of {symbol} ({product})")

    if FYERS is None:
        return {"s": "error", "message": "no fyers client"}

    for attempt in range(1, 4):
        try:
            resp = FYERS.place_order(data=data)
            print(f"[order] Response: {resp}")

            if isinstance(resp, dict) and resp.get("s") == "ok":
                # Try to get fill price
                order_id = resp.get("id")
                if order_id:
                    # Wait a moment for order to process
                    time.sleep(1)
                    order_details = get_order_details(order_id)
                    if order_details:
                        traded_price = order_details.get("averagePrice") or order_details.get("tradedPrice")
                        if traded_price and float(traded_price) > 0:
                            resp["fill_price"] = float(traded_price)
                            print(f"[order] Fill price: ₹{traded_price}")

            return resp
        except Exception as e:
            print(f"[order] Attempt {attempt} failed: {e}")
            time.sleep(1 * attempt)

    return {"s": "error", "message": "order failed after retries"}


# ============================== INDICATORS ==============================
def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def rsi(series: pd.Series, length: int) -> pd.Series:
    """Calculate RSI"""
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.ewm(com=length - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=length - 1, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi_val = 100 - (100 / (1 + rs))
    return rsi_val


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "volume" not in df.columns:
        return df
    df = df.copy()
    df["ema_fast_entry"] = ema(df["close"], ENTRY_FAST_EMA)
    df["rsi"] = rsi(df["close"], RSI_PERIOD)

    # Daily resetting VWAP
    df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
    df['tpv'] = df['typical_price'] * df['volume']
    df.index = pd.to_datetime(df.index)
    df['date'] = df.index.date
    df['cumulative_tpv'] = df.groupby('date')['tpv'].cumsum()
    df['cumulative_volume'] = df.groupby('date')['volume'].cumsum()
    df['vwap'] = df['cumulative_tpv'] / df['cumulative_volume']
    df.drop(columns=['typical_price', 'tpv', 'date', 'cumulative_tpv', 'cumulative_volume'], inplace=True)

    rng = (df["high"] - df["low"]) / df["close"].replace(0, pd.NA)
    df["ok_signal"] = rng >= MIN_RANGE_PCT if MIN_RANGE_PCT > 0 else True
    return df


# ============================== CANDLE CALLBACK ==============================
def on_completed_candle(symbol: str, candle: dict):
    """Process completed candle"""
    st = SYMBOL_STATES.get(symbol)
    if st is None:
        return

    try:
        row = {"open": candle["open"], "high": candle["high"],
               "low": candle["low"], "close": candle["close"], "volume": candle.get("volume", 0)}
        idx = pd.to_datetime(candle["ts"])

        df = st.data
        if df is None or df.empty:
            df = pd.DataFrame([row], index=[idx])
        else:
            df = pd.concat([df, pd.DataFrame([row], index=[idx])])
            df = df.loc[~df.index.duplicated(keep='last')]
            df = df.tail(2000)

        df.index.name = "datetime"
        st.data = compute_indicators(df)
        st.last_candle_ts = idx

        # Evaluate strategy
        evaluate_on_new_candle(st)

    except Exception as e:
        print(f"[on_completed_candle] error for {symbol}: {e}")

# ============================== TICK HANDLER ==============================

def on_tick(tick: dict):
    """Handle incoming ticks for symbols"""
    symbol = tick.get("symbol")
    ltp = float(tick.get("ltp", 0.0))
    ts_val = tick.get("timestamp")
    vtt = tick.get("vtt", 0)

    ts = None
    if ts_val:
        ts = dt.fromtimestamp(ts_val, IST).replace(tzinfo=None)
    else:
        ts = dt.now(IST).replace(tzinfo=None)

    # Process through candle manager
    if CANDLE_MANAGER:
        try:
            CANDLE_MANAGER.process_tick(
                {"symbol": symbol, "ltp": ltp, "vtt": vtt, "timestamp": ts.isoformat()}
            )
        except Exception as e:
            print(f"[on_tick:candle_manager] error: {e}")

    # Handle strategy logic
    handle_tick(symbol, ltp, ts)


def handle_tick(symbol: str, ltp: float, ts: dt):
    """Handle price ticks for strategy execution"""
    state = SYMBOL_STATES.get(symbol)
    if state is None:
        return

    state.ltp = ltp

    # ENTRY LOGIC - NEXT CANDLE ENTRY
    if state.status == "entry_pending" and state.signal_candle is not None:
        try:
            if not is_entry_allowed(symbol):
                return

            # Check if we're in the next candle
            sig_start = pd.to_datetime(state.signal_candle["ts"])
            next_allowed_start = sig_start + pd.Timedelta(minutes=TIMEFRAME_MIN)

            current_ts = pd.to_datetime(ts)
            candle_start = CANDLE_MANAGER._floor_ts(current_ts.to_pydatetime(), symbol)

            if pd.to_datetime(candle_start) == next_allowed_start:
                # Check for breakout above signal high
                signal_high = float(state.signal_candle["high"])

                # Apply buffer if configured
                breakout_price = signal_high + (signal_high * EMA_BUFFER)

                if ltp > breakout_price and is_market_hours(symbol):
                    print(f"\n[{symbol}] ENTRY TRIGGERED: LTP {ltp:.2f} > signal_high {breakout_price:.2f}")

                    # Calculate quantity
                    if is_mcx(symbol):
                        qty = get_lot_size(symbol)
                    else:
                        if POSITION_MODE == "alloc":
                            qty = int(ALLOCATION_AMOUNT / ltp)
                            if qty < 1: qty = 1
                        else:
                            qty = FIXED_QTY

                    print(f"[entry] Placing Buy for {symbol}, Qty: {qty}")

                    # Place order
                    resp = place_market_order(symbol, qty, side=1)

                    if isinstance(resp, dict) and resp.get("s") == "ok":
                        state.qty = qty
                        state.status = "position"
                        state.just_entered = True

                        fill_price = resp.get("fill_price")
                        if fill_price and fill_price > 0:
                            state.entry_price = fill_price
                        else:
                            state.entry_price = ltp

                        # Set stop loss based on SL_MODE
                        if SL_MODE == "signal_low":
                            state.stop_price = float(state.signal_candle["low"])
                        elif SL_MODE == "swing_low":
                            recent_lows = state.data['low'].tail(SWING_LOOKBACK)
                            state.stop_price = recent_lows.min()

                        print(f"\n[ENTRY CONFIRMED] {symbol}")
                        print(f"  Entry: ₹{state.entry_price:.2f}")
                        print(f"  SL: {state.stop_price:.2f}")
                        print(f"  Qty: {state.qty}")

                        # Clear signal
                        state.signal_candle = None
                        state.signal_close_ts = None
                    else:
                        print(f"[ENTRY FAILED] {symbol}: {resp}")
                        state.status = "watch"
                        state.reset_position()

        except Exception as e:
            print(f"[handle_tick:entry] error: {e}")
            import traceback
            traceback.print_exc()

    # EXIT LOGIC - Stop-Loss Only
    if state.status == "position":
        # Skip first few seconds after entry
        if state.just_entered:
            current_time = time.time()
            if current_time - state.last_update_ts < 5:
                return
            state.just_entered = False

        exit_reason = None

        # Stop Loss Check
        if state.stop_price > 0 and ltp <= state.stop_price:
            exit_reason = f"STOP LOSS HIT: LTP {ltp:.2f} <= SL {state.stop_price:.2f}"

        if exit_reason and is_market_hours(symbol):
            print(f"\n[{symbol}] {exit_reason}")
            try:
                resp = place_market_order(symbol, state.qty, side=-1)
                if isinstance(resp, dict) and resp.get("s") == "ok":
                    print(f"[EXIT CONFIRMED] {symbol}")
                    pnl = (ltp - state.entry_price) * state.qty
                    print(f"  Approx P&L: ₹{pnl:.2f}")
                    state.status = "cooldown"
                    state.reset_position()
                else:
                    print(f"[EXIT FAILED] {symbol}: {resp}")
            except Exception as e:
                print(f"[handle_tick:exit] error: {e}")
                import traceback
                traceback.print_exc()

        # P&L Print (throttled)
        current_time = time.time()
        if current_time - state.last_update_ts > 30:
            current_pnl = (ltp - state.entry_price) * state.qty
            pnl_percent = ((ltp - state.entry_price) / state.entry_price * 100) if state.entry_price else 0
            print(f"[{symbol}] LTP: {ltp:.2f} | Entry: {state.entry_price:.2f} | P&L: {current_pnl:.2f} ({pnl_percent:.2f}%)")
            state.last_update_ts = current_time


# ============================== STRATEGY EVALUATION ==============================

def evaluate_on_new_candle(st: SymbolState):
    """Evaluate strategy on new candle"""
    df = st.data
    if df is None or df.shape[0] < 2:
        return

    last_ts = st.last_candle_ts
    if last_ts is None or last_ts not in df.index:
        return

    curr = df.loc[last_ts]
    # prev = df.iloc[-2] # Unused but available

    curr_open = float(curr["open"])
    curr_close = float(curr["close"])
    curr_high = float(curr["high"])
    curr_low = float(curr["low"])

    ema_fast = float(curr.get("ema_fast_entry", float("nan")))
    vwap = float(curr.get("vwap", float("nan")))
    rsi_val = float(curr.get("rsi", float("nan")))

    # ENTRY SIGNAL (BULLISH - VWAP BODY CROSS)
    if st.status == "watch" and is_market_hours(st.symbol):
        # Check cutoff
        if not is_entry_allowed(st.symbol):
            return

        open_below_vwap = curr_open <= vwap
        closed_above_vwap = curr_close > vwap
        fast_ema_below_vwap = ema_fast < vwap
        green_ok = (not REQUIRE_GREEN_SIGNAL) or (curr_close > curr_open)
        ok_signal = bool(curr.get("ok_signal", True))
        rsi_ok = RSI_ENTRY_MIN <= rsi_val <= RSI_ENTRY_MAX

        if open_below_vwap and closed_above_vwap and fast_ema_below_vwap and green_ok and ok_signal and rsi_ok:
            st.signal_candle = {
                "ts": curr.name,
                "open": curr_open,
                "high": curr_high,
                "low": curr_low,
                "close": curr_close,
            }

            st.status = "entry_pending"
            print(f"\n[SIGNAL] {st.symbol}: VWAP CROSS ENTRY SIGNAL")
            print(f"  Close: {curr_close:.2f}")
            print(f"  VWAP: {vwap:.2f}, Fast EMA: {ema_fast:.2f}, RSI: {rsi_val:.2f}")
            print(f"  Signal High: {curr_high:.2f}, Low: {curr_low:.2f}")
            print(f"  Waiting for next candle breakout...\n")

    # EXIT SIGNAL (RSI)
    elif st.status == "position":
        rsi_profit_taking = RSI_EXIT_MIN <= rsi_val <= RSI_EXIT_MAX
        rsi_stop_loss = rsi_val < RSI_ENTRY_MIN
        exit_reason = None

        if rsi_profit_taking:
            exit_reason = f"RSI PROFIT TAKE on candle close: RSI {rsi_val:.2f} is between {RSI_EXIT_MIN}-{RSI_EXIT_MAX}"
        elif rsi_stop_loss:
            exit_reason = f"RSI STOP LOSS on candle close: RSI {rsi_val:.2f} < {RSI_ENTRY_MIN}"

        if exit_reason and is_market_hours(st.symbol):
            print(f"\n[{st.symbol}] {exit_reason}")
            try:
                resp = place_market_order(st.symbol, st.qty, side=-1)
                if isinstance(resp, dict) and resp.get("s") == "ok":
                    print(f"[EXIT CONFIRMED] {st.symbol}")
                    pnl = (st.ltp - st.entry_price) * st.qty
                    print(f"  Approx P&L: ₹{pnl:.2f}")
                    st.status = "cooldown"
                    st.reset_position()
                else:
                    print(f"[EXIT FAILED] {st.symbol}: {resp}")
            except Exception as e:
                print(f"[evaluate_on_new_candle:exit] error: {e}")
                import traceback
                traceback.print_exc()

# ============================== WEBSOCKET HANDLERS ==============================

def on_ws_message(raw):
    try:
        if not isinstance(raw, list):
            msgs = [raw]
        else:
            msgs = raw

        for m in msgs:
            # Pass to tick handler
            on_tick(m)

    except Exception as e:
        print(f"[ws] on_message error: {e}")


def on_ws_open():
    print(f"[ws:open] Subscribing to {len(ACTIVE_SUBSCRIPTIONS)} symbols")
    try:
        FYERS_SOCKET.subscribe(symbols=ACTIVE_SUBSCRIPTIONS, data_type="SymbolUpdate")
    except Exception as e:
        print("[ws:open] subscribe failed:", e)


def on_ws_error(err):
    print("[ws:error]", err)


def on_ws_close(msg):
    print("[ws:close]", msg)


# ============================== DATA WARMUP ==============================
def fetch_historical_data(fyers, symbol: str, days: int = 3) -> pd.DataFrame:
    """Fetch historical data for warmup"""
    if fyers is None:
        return pd.DataFrame()

    end = dt.now(IST)
    start = end - timedelta(days=days)

    try:
        payload = {
            "symbol": symbol,
            "resolution": str(TIMEFRAME_MIN),
            "date_format": "1",
            "range_from": start.strftime("%Y-%m-%d"),
            "range_to": end.strftime("%Y-%m-%d"),
            "cont_flag": "1",
        }

        r = fyers.history(data=payload)
        if isinstance(r, dict) and r.get("s") == "ok":
            df = pd.DataFrame(
                r["candles"],
                columns=["ts", "open", "high", "low", "close", "volume"],
            )
            df["ts"] = (
                pd.to_datetime(df["ts"], unit="s", utc=True)
                .dt.tz_convert(TIMEZONE)
                .dt.tz_localize(None)
            )
            df = df.set_index("ts")[["open", "high", "low", "close", "volume"]]
            return df
    except Exception as e:
        print(f"[warmup] Failed for {symbol}: {e}")

    return pd.DataFrame()


def warmup_data():
    """Warmup historical data for all symbols"""
    if FYERS is None:
        return

    print("[warmup] Fetching historical data...")
    for symbol in SYMBOLS:
        try:
            df = fetch_historical_data(FYERS, symbol, days=3)
            if not df.empty:
                SYMBOL_STATES[symbol].data = compute_indicators(df)
                SYMBOL_STATES[symbol].last_candle_ts = df.index[-1]
                print(f"[warmup] {symbol}: {len(df)} candles loaded")
            else:
                print(f"[warmup] {symbol}: No data")
        except Exception as e:
            print(f"[warmup] Error for {symbol}: {e}")

# ============================== MAIN ==============================

def main():
    global FYERS, FYERS_SOCKET, ACCESS_TOKEN, CANDLE_MANAGER
    global TIMEFRAME_MIN, ENTRY_FAST_EMA, ALLOCATION_AMOUNT, POSITION_MODE
    global RSI_PERIOD, RSI_ENTRY_MIN, RSI_ENTRY_MAX, RSI_EXIT_MIN, RSI_EXIT_MAX
    global TRADING_ENABLED

    # Parse arguments
    parser = argparse.ArgumentParser(description="VWAP-EMA-RSI Strategy - Stocks & MCX")
    parser.add_argument("--timeframe", "-t", type=int, default=TIMEFRAME_MIN)
    parser.add_argument("--entry-fast-ema", type=int, default=ENTRY_FAST_EMA)
    parser.add_argument("--rsi-period", type=int, default=RSI_PERIOD)
    parser.add_argument("--rsi-entry", type=str, default=f"{RSI_ENTRY_MIN}-{RSI_ENTRY_MAX}",
                        help="RSI entry range (e.g., 40-55)")
    parser.add_argument("--rsi-exit", type=str, default=f"{RSI_EXIT_MIN}-{RSI_EXIT_MAX}",
                        help="RSI exit range (e.g., 70-75)")
    parser.add_argument("--alloc", type=int, default=ALLOCATION_AMOUNT, help="Allocation amount for NSE stocks")
    parser.add_argument("--mode", type=str, default=POSITION_MODE, help="Position mode: 'alloc' or 'qty'")
    parser.add_argument("--carry", action="store_true", help="Enable carry forward (do not auto-square off at EOD)")
    parser.add_argument("--test", action="store_true", help="Test mode without live connection")
    parser.add_argument("--no-trade", action="store_true", help="Disable trading")

    args = parser.parse_args()

    # Update globals
    TIMEFRAME_MIN = args.timeframe
    ENTRY_FAST_EMA = args.entry_fast_ema
    RSI_PERIOD = args.rsi_period
    ALLOCATION_AMOUNT = args.alloc
    POSITION_MODE = args.mode
    CARRY_FORWARD_ENABLED = args.carry

    try:
        RSI_ENTRY_MIN, RSI_ENTRY_MAX = map(int, args.rsi_entry.split('-'))
        RSI_EXIT_MIN, RSI_EXIT_MAX = map(int, args.rsi_exit.split('-'))
    except ValueError:
        print("Invalid RSI range format. Use min-max (e.g., 40-55).")
        sys.exit(1)

    if args.no_trade:
        TRADING_ENABLED = False

    print("\n" + "=" * 80)
    print("VWAP-EMA-RSI STRATEGY - STOCKS & MCX")
    print("=" * 80)
    print(f"Timeframe: {TIMEFRAME_MIN} minutes")
    print(f"Entry EMA: {ENTRY_FAST_EMA}")
    print(f"RSI Period: {RSI_PERIOD}")
    print(f"RSI Entry Range: {RSI_ENTRY_MIN}-{RSI_ENTRY_MAX}")
    print(f"RSI Exit Range: {RSI_EXIT_MIN}-{RSI_EXIT_MAX}")
    print(f"Position Mode: {POSITION_MODE}")
    print(f"Allocation Amount: {ALLOCATION_AMOUNT}")
    print(f"MCX Lots: {MCX_LOTS}")
    print(f"Symbols: {len(SYMBOLS)} loaded")
    print(f"Carry Forward: {CARRY_FORWARD_ENABLED}")
    print(f"Trading Enabled: {TRADING_ENABLED}")
    print("=" * 80 + "\n")

    if not TRADING_ENABLED:
        print("[WARNING] Trading is DISABLED (--no-trade flag). Running in paper trading mode.\n")

    # Register the save_state function to be called on exit
    atexit.register(save_state)

    # Initialize Candle Manager
    CANDLE_MANAGER = CandleManager(TIMEFRAME_MIN, on_candle=on_completed_candle, tz=TIMEZONE)

    if args.test:
        print("[TEST MODE] Running without live connection")
        return

        # Get access token and initialize Fyers
    try:
        auth = get_access_token()
        ACCESS_TOKEN = auth["access_token"]
        client_id = ACCESS_TOKEN.split(":")[0] if ":" in ACCESS_TOKEN else ACCESS_TOKEN

        FYERS = fyersModel.FyersModel(
            client_id=client_id,
            is_async=False,
            token=ACCESS_TOKEN,
            log_path=""
        )

        print("[auth] Fyers model initialized successfully")

        # Load previous state
        load_state()

        # Sync positions with broker
        sync_with_broker_positions()

        # Warmup historical data
        warmup_data()

    except Exception as e:
        print(f"[auth] Failed to initialize Fyers: {e}")
        return

        # Initialize WebSocket
    FYERS_SOCKET = data_ws.FyersDataSocket(
        access_token=ACCESS_TOKEN,
        log_path="",
        litemode=True,
        write_to_file=False,
        reconnect=True,
        on_connect=on_ws_open,
        on_close=on_ws_close,
        on_error=on_ws_error,
        on_message=on_ws_message,
    )

    print("[start] Connecting WebSocket...")
    try:
        FYERS_SOCKET.connect()

        # Keep running
        print("\n[bot] Strategy is running. Press Ctrl+C to stop.\n")
        last_sync_time = time.time()

        while True:
            # Periodic subscription updates
            update_subscriptions()

            # Sync with broker positions every 5 minutes
            current_time = time.time()
            if current_time - last_sync_time >= 300:
                print("[sync] Performing periodic position synchronization...")
                sync_with_broker_positions()
                last_sync_time = current_time

            # Check for Exit All Time
            for symbol, state in SYMBOL_STATES.items():
                if state.status == "position" and check_exit_all_condition(symbol):
                    print(f"[EXIT ALL] Time reached for {symbol}. Closing position.")
                    place_market_order(symbol, state.qty, side=-1)
                    state.status = "cooldown"
                    state.reset_position()

            time.sleep(5)

    except KeyboardInterrupt:
        print("\n[exit] Interrupted by user")
    except Exception as e:
        print(f"[fatal] Error: {e}")


if __name__ == "__main__":
    main()
