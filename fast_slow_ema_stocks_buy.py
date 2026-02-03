# Fast Slow EMA Strategy - Strict Next Candle Entry, Fyers v3 login merged
# -*- coding: utf-8 -*-
"""
- ENTRY (Bullish, using ENTRY_FAST_EMA & ENTRY_SLOW_EMA):
    * Signal candle (NOW STRICT BODY CROSS):
        - EMA_fast > EMA_slow (uptrend sequence)
        - Candle OPEN < min(EMA_fast, EMA_slow)  (body starts below both EMAs)
        - Candle CLOSE > max(EMA_fast, EMA_slow) + EMA_BUFFER  (body closes above both EMAs)
        - (optional) candle is green if REQUIRE_GREEN_SIGNAL is True
        - tiny-candle filter via MIN_RANGE_PCT if enabled
    * ENTRY:
        - Only allowed on the VERY NEXT candle.
        - During that next candle, if any tick LTP > signal_high -> market BUY.

- STOPLOSS:
    * Either signal low or swing low (SL_MODE = "signal_low" or "swing_low").

- EXIT:
    * EXIT EMA: red candle crosses & closes below EXIT_EMA → exit_pending.
      Only next candle can trigger actual exit on break below exit_low.
    * TARGET: previous swing high (last SWING_HIGH_LOOKBACK highs before signal candle).
      If LTP >= target_price, exit immediately.
    * Whatever comes first (TARGET or EXIT EMA / SL) closes the trade.

- EMAs:
    * ENTRY_FAST_EMA, ENTRY_SLOW_EMA – only for entry.
    * EXIT_EMA – only for exit.

- Position sizing:
    * POSITION_MODE = "qty": fixed quantity per trade (FIXED_QTY, default 1).
    * POSITION_MODE = "alloc": alloc-based: qty = ALLOC_DEFAULT // entry_price.

- Fyers v3 login:
    * Uses api-t1 endpoints with appIdHash.
    * Saves access_token under AccessToken/<today>.json and fyers_login_details.json.
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
from typing import Dict, Optional
from datetime import datetime as dt, timedelta

import requests
import pandas as pd
import numpy as np
import pytz

# Try to import Fyers packages - run in test mode if missing
try:
    from fyers_apiv3 import fyersModel
    from fyers_apiv3.FyersWebsocket import data_ws
except Exception:
    fyersModel = None
    data_ws = None

# ---------------------------- USER-CONFIGURED CONSTANTS ----------------------------
TIMEFRAME_MIN = 5  # Any TF in minutes (1,2,3,5,10,15,30,60,...)

EXIT_EMA = 50  # Exit EMA (red candle cross & close below this)

# EMAs dedicated for ENTRY signal (default 5 and 20 but configurable)Fast EMA
ENTRY_FAST_EMA = 9  # e.g., EMA 5  (fast)
ENTRY_SLOW_EMA = 15  # e.g., EMA 20 (slow)

MIN_RANGE_PCT = 0.0  # tiny-candle filter (0.001 = 0.1%), 0.0 = off
EMA_BUFFER = 0.0  # optional extra buffer above/below EMAs
REQUIRE_GREEN_SIGNAL = True

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

LOG_FILE = "trade_log.csv"
STATE_DUMP = "symbol_states.json"
PARTIAL_CANDLES_FILE = "partial_candles.json"

# Default product type: "CNC" (delivery) or "Intraday"
PRODUCT_TYPE = "CNC"

ALLOC_DEFAULT = 1000.0
ALLOC_MAP = {}

SL_MODE = "signal_low"  # "signal_low" or "swing_low"
SWING_LOOKBACK = 5  # used for swing-low
SWING_HIGH_LOOKBACK = 50  # used for target swing-high

MAX_CONCURRENT_POS = 3
DAILY_MAX_LOSS = 50000.0
TRADING_ENABLED = True
MAX_EXIT_RETRIES = 3
EXIT_RETRY_COOLDOWN_SECONDS = 10

TRAIL_ATR_MULT = 1.0  # None = disabled, float = multiplier (e.g., 1.0)

# Timezone (IST)
TIMEZONE = "Asia/Kolkata"
IST = pytz.timezone(TIMEZONE)

# Config files
CONFIG_FILE = "fyers_login_details.json"
TOKENS_DIR = "AccessToken"
TODAY = str(datetime.date.today())
TOKEN_PATH = os.path.join(TOKENS_DIR, f"{TODAY}.json")

# Settings file for user-customisable values (optional)
SETTINGS_FILE = "settings.json"

# Position sizing mode globals
POSITION_MODE = "qty"  # "alloc" or "qty"
FIXED_QTY = 1  # default 1 share when using qty mode
QTY_MAP: Dict[str, int] = {}

# Re-auth guard to avoid infinite recursion
REAUTH_ATTEMPTS = 0
MAX_REAUTH_ATTEMPTS = 3

# ---------- small print filter to avoid noisy console spam ----------
_real_print = print
ALLOWED_SUBSTRINGS = (
    "ENTRY SIGNAL", "[signal:", "EXIT SIGNAL", "[exit:", "[CANDLE]", "[order]", "[auth]", "[ws]",
    "[blocked-entry]", "[entry-debug]", "[exit-debug]", "TARGET EXIT", "STOP-LOSS", "[ENTRY CONFIRMED]",
    "[sync]", "[warmup]", "[main]"
)


def print(*args, **kwargs):
    try:
        s = " ".join(str(x) for x in args)
        # Prepend timestamp
        ts = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
        s = f"{ts} {s}"
    except Exception:
        return
    for sub in ALLOWED_SUBSTRINGS:
        if sub in s:
            return _real_print(s, **kwargs)
    return None


# ---------------------------- SETTINGS LOADER ----------------------------
def load_settings_file(path: str = SETTINGS_FILE) -> dict:
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception as e:
        _real_print(f"[warn] Could not load settings file {path}: {e}")
        return {}

        # ---------------------------- FYERS LOGIN HELPERS (MERGED) ----------------------------


def load_or_prompt_creds():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, dict) and "api_key" in data and "api_secret" in data and "redirect_url" in data:
                return data
        except Exception:
            pass

    _real_print("---- Enter your Fyers Login Credentials (v3) ----")
    creds = {
        "api_key": input("Enter APP ID (e.g., ABCDE12345-100): ").strip(),
        "api_secret": input("Enter SECRET ID: ").strip(),
        "redirect_url": input("Enter Redirect URL (must match app): ").strip(),
    }
    if input("Save to 'fyers_login_details.json'? (Y/N): ").strip().upper() == "Y":
        try:
            base = {}
            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, "r") as f:
                        base = json.load(f) or {}
                        if not isinstance(base, dict):
                            base = {}
                except Exception:
                    base = {}
            base.update(creds)
            with open(CONFIG_FILE, "w") as f:
                json.dump(base, f, indent=2)
            _real_print(f"Saved '{CONFIG_FILE}'.")
        except Exception as e:
            _real_print(f"[auth] Could not save creds: {e}")
    else:
        _real_print("Skipping save.")
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
                _real_print(f"[{attempt}/{max_retries}] 503 from auth server. Retrying in {sleep_s}s...")
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
            _real_print(f"[{attempt}/{max_retries}] Network error: {e}. Retrying in {sleep_s}s...")
            time.sleep(sleep_s)


def run_interactive_login() -> str:
    creds = load_or_prompt_creds()
    app_id = creds["api_key"]
    secret_id = creds["api_secret"]
    redirect_uri = creds["redirect_url"]

    if os.path.exists(TOKEN_PATH):
        try:
            with open(TOKEN_PATH, "r") as f:
                data = json.load(f)
            if isinstance(data, str):
                access_token = data
            elif isinstance(data, dict):
                access_token = data.get("access_token") or data.get("token")
            else:
                access_token = None
            if access_token:
                _real_print(f"API Key : {app_id}")
                _real_print(f"Access Token (loaded from file) : {access_token}")
                return access_token
        except Exception:
            pass

    auth_url = build_auth_url(app_id, redirect_uri)
    _real_print("\nLogin URL (open in browser, allow & complete login):")
    _real_print(auth_url)

    user_val = input("\nPaste the FULL redirect URL after login, or just the 'code=' value here: ").strip()
    try:
        auth_code = extract_code(user_val)
    except Exception as e:
        _real_print(f"Could not extract code: {e}")
        raise

    token_resp = validate_authcode(app_id, secret_id, auth_code)
    access_token = token_resp.get("access_token")
    if not access_token:
        raise RuntimeError(f"Unexpected token response: {token_resp}")

    os.makedirs(TOKENS_DIR, exist_ok=True)
    try:
        with open(TOKEN_PATH, "w") as f:
            json.dump(access_token, f)
    except Exception as e:
        _real_print(f"[auth] Failed to save token to {TOKEN_PATH}: {e}")

    try:
        base = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    base = json.load(f) or {}
                    if not isinstance(base, dict):
                        base = {}
            except Exception:
                base = {}
        base["access_token"] = access_token
        with open(CONFIG_FILE, "w") as f:
            json.dump(base, f, indent=2)
    except Exception as e:
        _real_print(f"[auth] Could not store access_token into {CONFIG_FILE}: {e}")

    _real_print("\nLogin successful.")
    _real_print(f"API Key : {app_id}")
    _real_print(f"Access Token : {access_token}")
    _real_print(f"Saved to: {TOKEN_PATH}")
    return access_token


# ---------------------------- STATE OBJECTS ----------------------------
class SymbolState:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.data = pd.DataFrame()
        self.status = "watch"
        self.signal_candle = None
        self.signal_close_ts = None
        self.signal_expiry = None
        self.signal_notified = False
        self.entry_price = 0.0
        self.qty = 0
        self.stop_price = 0.0
        self.gtt_order_id = None
        # exit
        self.exit_signal_candle = None
        self.exit_signal_expiry = None
        self.exit_pending = False
        self.exit_try_count = 0
        self.last_failed_exit_ts = None
        # candle tracking
        self.last_candle_ts = None
        self.last_eval_candle = None
        # guards
        self.just_entered = False
        self.entry_time = 0.0
        # target
        self.target_price = None
        self.potential_target_price = None
        # trailing
        self.atr_at_entry = 0.0
        self.sl_trailed = False

    def __repr__(self):
        return f"<State {self.symbol} {self.status} qty={self.qty} sl={self.stop_price} tp={self.target_price}>"


SYMBOL_STATES: Dict[str, SymbolState] = {s: SymbolState(s) for s in SYMBOLS}

# ---------------------------- LOGGING UTIL ----------------------------
if not os.path.exists(LOG_FILE):
    import csv

    with open(LOG_FILE, "w", newline="") as f:
        csv.writer(f).writerow(["ts", "symbol", "action", "qty", "price", "response"])


def log_trade_event(symbol, action, qty, price, response):
    resp_text = json.dumps(response, default=str)
    import csv
    with open(LOG_FILE, "a", newline="") as f:
        csv.writer(f).writerow([dt.now().isoformat(), symbol, action, qty, price, resp_text])

        # ---------------------------- INDICATORS ----------------------------


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    df["ema_exit"] = ema(df["close"], EXIT_EMA)
    df["ema_fast_entry"] = ema(df["close"], ENTRY_FAST_EMA)
    df["ema_slow_entry"] = ema(df["close"], ENTRY_SLOW_EMA)
    df["atr"] = atr(df, 14)
    rng = (df["high"] - df["low"]) / df["close"].replace(0, pd.NA)
    df["ok_signal"] = rng >= MIN_RANGE_PCT if MIN_RANGE_PCT > 0 else True
    return df


# ---------------------------- CANDLE MANAGER ----------------------------
class CandleManager:
    def __init__(self, timeframe_min: int = 5, on_candle=None, tz="Asia/Kolkata",
                 persist_file=PARTIAL_CANDLES_FILE, max_history=2000):
        self.tf = int(timeframe_min)
        self.on_candle = on_candle
        self.tz = pytz.timezone(tz)
        self.persist_file = persist_file
        self.lock = threading.RLock()
        self.partial: Dict[str, dict] = {}
        self.history: Dict[str, pd.DataFrame] = {}
        self.max_history = max_history

        if os.path.exists(self.persist_file):
            try:
                with open(self.persist_file, "r") as f:
                    saved = json.load(f)
                for sym, p in (saved or {}).items():
                    p["ts"] = pd.to_datetime(p["ts"]).tz_localize(None)
                    self.partial[sym] = p
            except Exception:
                self.partial = {}

    def _floor_ts(self, ts: dt) -> dt:
        if ts.tzinfo is None:
            ts = self.tz.localize(ts)
        else:
            ts = ts.astimezone(self.tz)
        ts = ts.replace(tzinfo=None)
        minute = (ts.minute // self.tf) * self.tf
        return ts.replace(second=0, microsecond=0, minute=minute)

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

    def _persist_partial(self):
        try:
            to_save = {}
            for sym, p in self.partial.items():
                out = dict(p)
                out["ts"] = p["ts"].isoformat()
                to_save[sym] = out
            with open(self.persist_file, "w") as f:
                json.dump(to_save, f, indent=2)
        except Exception:
            pass

    def _append_history(self, symbol: str, candle: dict):
        df = self.history.get(symbol)
        row = {"open": candle["open"], "high": candle["high"],
               "low": candle["low"], "close": candle["close"]}
        ts = pd.to_datetime(candle["ts"])
        if df is None:
            df = pd.DataFrame([row], index=[ts])
        else:
            df = pd.concat([df, pd.DataFrame([row], index=[ts])])
            if len(df) > self.max_history:
                df = df.tail(self.max_history)
        df.index.name = "datetime"
        self.history[symbol] = df
        if callable(self.on_candle):
            try:
                self.on_candle(symbol, {"ts": ts, **row})
            except Exception as e:
                _real_print(f"[CandleManager:_append_history:on_candle_call] error: {e}")

    def process_tick(self, tick: dict):
        try:
            symbol = tick.get("symbol")
            if not symbol:
                return
            ltp = tick.get("ltp") or tick.get("last_price") or tick.get("last_traded_price")
            if ltp is None:
                return
            ltp = float(ltp)
            ts = self._parse_ts(tick.get("timestamp"))
            candle_start = self._floor_ts(ts)
            with self.lock:
                p = self.partial.get(symbol)
                if p is None:
                    new_p = {"ts": candle_start, "open": ltp, "high": ltp,
                             "low": ltp, "close": ltp, "ticks": 1}
                    self.partial[symbol] = new_p
                    self._persist_partial()
                    return
                if candle_start == p["ts"]:
                    p["high"] = max(p["high"], ltp)
                    p["low"] = min(p["low"], ltp)
                    p["close"] = ltp
                    p["ticks"] = p.get("ticks", 0) + 1
                    self._persist_partial()
                    return
                completed = dict(p)
                candle_out = {
                    "symbol": symbol,
                    "ts": completed["ts"],
                    "open": completed["open"],
                    "high": completed["high"],
                    "low": completed["low"],
                    "close": completed["close"],
                    "ticks": completed.get("ticks", 1),
                }
                self._append_history(symbol, candle_out)
                new_partial = {"ts": candle_start, "open": ltp, "high": ltp,
                               "low": ltp, "close": ltp, "ticks": 1}
                self.partial[symbol] = new_partial
                self._persist_partial()
        except Exception as e:
            _real_print(f"[CandleManager:process_tick] error: {e}")

    def force_close_all_up_to(self, upto_ts: dt = None):
        if upto_ts is None:
            upto_ts = dt.now(self.tz).replace(tzinfo=None)
        with self.lock:
            for symbol, p in list(self.partial.items()):
                if p["ts"] < upto_ts:
                    candle_out = {
                        "symbol": symbol,
                        "ts": p["ts"],
                        "open": p["open"],
                        "high": p["high"],
                        "low": p["low"],
                        "close": p["close"],
                        "ticks": p.get("ticks", 1),
                    }
                    self._append_history(symbol, candle_out)
                    del self.partial[symbol]
            self._persist_partial()

    def get_latest_candle(self, symbol: str):
        df = self.history.get(symbol)
        if df is None or df.empty:
            return None
        last_idx = df.index[-1]
        row = df.loc[last_idx]
        return {"ts": last_idx, **row.to_dict()}


CANDLE_MANAGER: Optional[CandleManager] = None

# ---------------------------- ORDER HELPERS ----------------------------
FYERS = None
FYERS_SOCKET = None
ACCESS_TOKEN = None
OPEN_POSITIONS = set()
DAILY_PNL = 0.0


def decide_qty(symbol: str, entry_price: float) -> int:
    sym_upper = symbol.upper()

    if QTY_MAP.get(symbol) is not None:
        try:
            q = int(QTY_MAP.get(symbol))
            return max(0, q)
        except Exception:
            pass

    if sym_upper.startswith("MCX:"):
        if POSITION_MODE == "qty" and FIXED_QTY and FIXED_QTY > 0:
            return max(0, int(FIXED_QTY))
        return 1

    if POSITION_MODE == "qty":
        if FIXED_QTY and FIXED_QTY > 0:
            return max(0, int(FIXED_QTY))

    alloc = ALLOC_MAP.get(symbol, ALLOC_DEFAULT)
    if entry_price <= 0:
        return 0
    try:
        q = int(alloc // entry_price)
        return max(0, q)
    except Exception:
        return 0


def place_market_order(symbol: str, qty: int, side: int) -> dict:
    if qty <= 0:
        return {"s": "error", "message": "qty is 0"}

    side_str = "BUY" if side == 1 else "SELL"

    # Dynamically set productType based on exchange
    order_product_type = "INTRADAY" if symbol.startswith("MCX:") else PRODUCT_TYPE

    data = {
        "symbol": symbol,
        "qty": qty,
        "type": 2,
        "side": side,
        "productType": order_product_type,
        "limitPrice": 0,
        "stopPrice": 0,
        "validity": "DAY",
        "disclosedQty": 0,
        "offlineOrder": False,
    }
    _real_print(f"[order] Placing market {side_str} for {qty} of {symbol} with productType={order_product_type}")
    if side == 1 and len([s for s in SYMBOL_STATES.values() if s.status == "position"]) >= MAX_CONCURRENT_POS:
        _real_print(f"[order] MAX_CONCURRENT_POS reached ({MAX_CONCURRENT_POS}). Rejecting new buy for {symbol}.")
        return {"s": "error", "message": "max concurrent positions reached"}
    if FYERS is None:
        err = {"s": "error", "message": "no fyers client"}
        log_trade_event(symbol, side_str, qty, None, err)
        return err
    for attempt in range(1, 4):
        try:
            resp = FYERS.place_order(data=data)
            log_trade_event(symbol, side_str, qty, None, resp)
            if isinstance(resp, dict) and resp.get("s") == "ok":
                if side == 1:
                    OPEN_POSITIONS.add(symbol)
                else:
                    OPEN_POSITIONS.discard(symbol)
            return resp
        except Exception as e:
            _real_print(f"[order] Attempt {attempt} failed: {e}")
            time.sleep(1 * attempt)
    err = {"s": "error", "message": "order failed after retries"}
    log_trade_event(symbol, side_str, qty, None, err)
    return err


def verify_order_success(order_id: str, max_retries: int = 4) -> bool:
    """
    Polls the orderbook to verify if the order was Accepted/Filled.
    Returns True if Filled (2), Pending (6), Transit (4), or Open (11).
    Returns False if Rejected (5) or Cancelled (1).
    """
    if not order_id or FYERS is None:
        return False

    _real_print(f"[order] Verifying status for Order ID: {order_id}...")

    for i in range(max_retries):
        try:
            time.sleep(1) # Wait for broker to process
            resp = FYERS.orderbook()
            if isinstance(resp, dict) and resp.get("s") == "ok":
                orders = resp.get("orderBook", [])
                for o in orders:
                    if str(o.get("id")) == str(order_id):
                        # Status Codes:
                        # 1: Cancelled, 2: Traded/Filled, 3: (unused?), 4: Transit,
                        # 5: Rejected, 6: Pending, 11: Open? (Fyers docs vary, but 2/6 are key)
                        status = o.get("status")
                        msg = o.get("message", "")

                        if status in (2, 6, 4, 11):
                            _real_print(f"[order] Order verified! Status={status} ({msg})")
                            return True
                        elif status in (5, 1):
                            _real_print(f"[order] Order REJECTED/CANCELLED! Status={status} Msg={msg}")
                            return False
                        else:
                            _real_print(f"[order] Order status {status} unknown. Assuming pending.")
                            return True # Assume OK if uncertain
        except Exception as e:
            _real_print(f"[order] Verification error: {e}")
            time.sleep(0.5)

    _real_print("[order] Could not verify order status (timeout). Assuming success to be safe.")
    return True # Fail-open: Let Sync fix it if it's actually missing


def place_gtt_stoploss(symbol: str, qty: int, trigger_price: float) -> dict:
    sl_price = round(trigger_price * 0.99, 1)

    # Dynamically set productType based on exchange
    order_product_type = "INTRADAY" if symbol.startswith("MCX:") else PRODUCT_TYPE

    data = {
        "symbol": symbol,
        "type": 1,  # Single order
        "side": -1, # Sell
        "productType": order_product_type,
        "orderInfo": {
            "leg1": {
                "price": sl_price,
                "qty": qty,
                "triggerPrice": trigger_price
            }
        }
    }
    if FYERS is None:
        err = {"s": "error", "message": "no fyers client for gtt"}
        log_trade_event(symbol, "GTT_FAIL", qty, trigger_price, err)
        return err
    for attempt in range(1, 3):
        try:
            resp = FYERS.place_gtt_order(data=data)
            log_trade_event(symbol, "GTT_PLACE", qty, trigger_price, resp)
            if isinstance(resp, dict) and resp.get("s") != "ok":
                _real_print(f"[order] GTT placement failed: {resp}")
            return resp
        except Exception as e:
            _real_print(f"[order] GTT Exception: {e}")
            time.sleep(1)
    err = {"s": "error", "message": "gtt failed after retries"}
    log_trade_event(symbol, "GTT_FAIL", qty, trigger_price, err)
    return err


def cancel_gtt_order(gtt_id: str) -> dict:
    if FYERS is None:
        return {"s": "error", "message": "no fyers client"}
    try:
        return FYERS.cancel_gtt_order(id=gtt_id)
    except Exception as e:
        return {"s": "error", "message": str(e)}

        # ---------------------------- TOKEN UTILITIES ----------------------------


def load_token() -> str:
    try:
        if os.path.exists(TOKEN_PATH):
            with open(TOKEN_PATH, "r") as f:
                data = json.load(f)
            if isinstance(data, str):
                return data.strip()
            if isinstance(data, dict):
                for k in ("access_token", "accessToken", "token"):
                    if data.get(k):
                        return str(data[k]).strip()
    except Exception:
        pass

    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in ("access_token", "accessToken", "token"):
                    if data.get(k):
                        return str(data[k]).strip()
    except Exception:
        pass

    try:
        if os.path.isdir(TOKENS_DIR):
            files = sorted(glob.glob(os.path.join(TOKENS_DIR, "*")))
            for fn in files:
                try:
                    with open(fn, "r") as f:
                        raw = f.read().strip()
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                        if isinstance(data, str):
                            return data.strip()
                        if isinstance(data, dict):
                            for k in ("access_token", "accessToken", "token"):
                                if data.get(k):
                                    return str(data[k]).strip()
                    except Exception:
                        return raw
                except Exception:
                    continue
    except Exception:
        pass

    raise Exception(
        "No access token found. Please run the integrated Fyers login flow once "
        "or create fyers_login_details.json with key 'access_token'."
    )


def get_access_token() -> dict:
    tok = None
    try:
        tok = load_token()
    except Exception:
        tok = None

    if tok:
        return {"access_token": tok}

    _real_print("[auth] No existing access token found. Starting interactive login...")
    access_token = run_interactive_login()
    if not access_token:
        raise Exception(
            "Could not obtain access token even after interactive login."
        )
    return {"access_token": access_token}


def warmup_all(fyers_client):
    if fyers_client is None:
        _real_print("[warmup] No fyers client available, skipping warmup.")
        return
    _real_print("[warmup] Attempting warmup fetch for symbols (best-effort)")
    for sym in SYMBOLS:
        try:
            if hasattr(fyers_client, "history"):
                try:
                    _ = fyers_client.history(
                        symbol=sym,
                        resolution=f"{TIMEFRAME_MIN}",
                        range_from=(dt.now() - timedelta(days=2)).strftime("%Y-%m-%d"),
                        range_to=dt.now().strftime("%Y-%m-%d"),
                        cont_flag=0,
                    )
                except Exception:
                    pass
            elif hasattr(fyers_client, "get_historical_data"):
                try:
                    _ = fyers_client.get_historical_data(sym, TIMEFRAME_MIN)
                except Exception:
                    pass
            time.sleep(0.05)
        except Exception:
            continue
    _real_print("[warmup] Warmup complete (best-effort).")


# ---------------------------- STRATEGY HELPERS ----------------------------
def compute_swing_low_for_signal(state: SymbolState, lookback: int) -> float:
    try:
        if state.signal_candle is not None:
            end_ts = state.signal_candle["ts"]
            df = state.data.loc[:end_ts]
        else:
            df = state.data
        if df.empty:
            return float("nan")
        tail = df.tail(lookback)
        return float(tail["low"].min())
    except Exception:
        return float("nan")


def compute_prev_swing_high_for_entry(state: SymbolState, lookback: int, reference_price: float = None) -> float:
    """
    Calculates the target based on the nearest SWING HIGH (Fractal Peak) that is greater than the reference price.
    Uses a 5-bar fractal definition (Pivot Width = 2) to filter out noise.
    If no valid structural swing high is found above entry, falls back to the global maximum of the lookback period.
    """
    try:
        df = state.data
        if df is None or df.empty:
            return float("nan")
        if state.signal_candle is not None:
            sig_ts = pd.to_datetime(state.signal_candle["ts"])
            df_up_to = df.loc[:sig_ts]
        else:
            df_up_to = df

        # We exclude the signal candle itself to look at past structure
        if df_up_to.shape[0] <= 1:
            return float("nan")

        # Take the window of interest. We need enough data to compute pivots.
        # If lookback is 50, we take 50 candles BEFORE signal.
        prior = df_up_to.iloc[:-1].tail(lookback).copy()
        if prior.empty:
            return float("nan")

        pivot_width = 2  # 5-bar fractal (2 left, 2 right)

        # If prior is too short for pivot calc, fallback to max
        if len(prior) < (pivot_width * 2 + 1):
            return float(prior["high"].max())

        highs = prior["high"].values
        peaks = []

        # Fractal Swing High Detection
        # Check for peaks where High[i] > Neighbors
        for i in range(pivot_width, len(highs) - pivot_width):
            current = highs[i]
            is_peak = True

            # Check left neighbors
            for j in range(1, pivot_width + 1):
                if highs[i-j] >= current:
                    is_peak = False
                    break
            if not is_peak: continue

            # Check right neighbors
            for j in range(1, pivot_width + 1):
                if highs[i+j] >= current:
                    is_peak = False
                    break

            if is_peak:
                peaks.append(current)

        # If reference price is provided, find LATEST Fractal Peak > reference
        if reference_price is not None and not math.isnan(reference_price):
            valid_peaks = [p for p in peaks if p > reference_price]
            if valid_peaks:
                return float(valid_peaks[-1])  # LATEST (Most Recent) Swing High above entry

        # Fallback to absolute max if no higher swing found or no ref price
        return float(prior["high"].max())
    except Exception as e:
        _real_print(f"[warn] compute_prev_swing_high error: {e}")
        return float("nan")


def on_completed_candle(symbol: str, candle: dict):
    st = SYMBOL_STATES.get(symbol)
    if st is None:
        return
    try:
        row = {"open": candle["open"], "high": candle["high"],
               "low": candle["low"], "close": candle["close"]}
        idx = pd.to_datetime(candle["ts"])
        df = st.data
        if df is None or df.empty:
            df = pd.DataFrame([row], index=[idx])
        else:
            df = pd.concat([df, pd.DataFrame([row], index=[idx])])
            # De-duplicate the index, keeping the last entry
            df = df.loc[~df.index.duplicated(keep='last')]
            df = df.tail(2000)
        df.index.name = "datetime"
        st.data = compute_indicators(df)
        st.last_candle_ts = idx
    except Exception as e:
        _real_print(f"[on_completed_candle] error processing for {symbol}: {e}")
        return
    evaluate_on_new_candle(st)


# ---------------------------- LTP / Tick handler ----------------------------
def on_tick(symbol: str, ltp: float, ts: Optional[dt] = None):
    if ts is None:
        ts = dt.now(IST).replace(tzinfo=None)
    if isinstance(ts, str):
        try:
            ts = pd.to_datetime(ts)
            if ts.tzinfo is not None:
                ts = ts.tz_convert(TIMEZONE).tz_localize(None)
            else:
                ts = IST.localize(ts).replace(tzinfo=None)
        except Exception:
            ts = dt.now(IST).replace(tzinfo=None)
    elif isinstance(ts, dt):
        if ts.tzinfo is not None:
            ts = ts.astimezone(IST).replace(tzinfo=None)
        else:
            ts = IST.localize(ts).replace(tzinfo=None)

    if CANDLE_MANAGER:
        try:
            CANDLE_MANAGER.process_tick(
                {"symbol": symbol, "ltp": ltp, "timestamp": ts.isoformat()}
            )
        except Exception as e:
            _real_print(f"[on_tick:candle_manager_call] error: {e}")

    state = SYMBOL_STATES.get(symbol)
    if state is None:
        return

    skip_exit_checks = bool(state.just_entered)

    # TARGET EXIT
    if (
            state.status == "position"
            and state.target_price is not None
            and state.target_price > 0
            and not skip_exit_checks
    ):
        try:
            if ltp >= state.target_price:
                _real_print(
                    f"[{symbol}] TARGET EXIT at LTP {ltp:.2f} (>= {state.target_price:.2f})"
                )
                resp = place_market_order(symbol, state.qty, side=-1)
                log_trade_event(symbol, "TARGET_SELL", state.qty, ltp, resp)
                if isinstance(resp, dict) and resp.get("s") == "ok":
                    pnl = (ltp - state.entry_price) * state.qty
                    _real_print(f"[{symbol}] TARGET EXIT OK. PnL={pnl:.2f}")
                    if state.gtt_order_id:
                        cancel_gtt_order(state.gtt_order_id)
                    state.status = "cooldown"
                    state.exit_pending = False
                    state.exit_signal_candle = None
                    state.target_price = None
                else:
                    _real_print(f"[{symbol}] TARGET EXIT ORDER FAILED: {resp}")
        except Exception as e:
            _real_print(f"[on_tick:target_exit] error: {e}")

            # ENTRY: strict next candle
    if state.status == "entry_pending" and state.signal_candle is not None:
        try:
            tick_ts = (
                ts
                if isinstance(ts, dt)
                else pd.to_datetime(ts).to_pydatetime().replace(tzinfo=None)
            )

            sig_start = state.signal_candle.get("ts")
            if sig_start is None:
                state.status = "watch"
                state.signal_candle = None
                state.signal_close_ts = None
            else:
                try:
                    sig_floor = pd.to_datetime(sig_start)
                    if sig_floor.tzinfo is not None:
                        sig_floor = sig_floor.tz_convert(TIMEZONE).tz_localize(None)
                    next_allowed_bucket = (
                            sig_floor.to_pydatetime().replace(tzinfo=None)
                            + timedelta(minutes=TIMEFRAME_MIN)
                    )
                except Exception:
                    if isinstance(sig_start, dt):
                        next_allowed_bucket = (
                                sig_start + timedelta(minutes=TIMEFRAME_MIN)
                        ).replace(tzinfo=None)
                    else:
                        next_allowed_bucket = None

                try:
                    if CANDLE_MANAGER is not None:
                        current_bucket = CANDLE_MANAGER._floor_ts(tick_ts)
                    else:
                        minute = (tick_ts.minute // TIMEFRAME_MIN) * TIMEFRAME_MIN
                        current_bucket = tick_ts.replace(
                            second=0, microsecond=0, minute=minute
                        )
                except Exception:
                    minute = (tick_ts.minute // TIMEFRAME_MIN) * TIMEFRAME_MIN
                    current_bucket = tick_ts.replace(
                        second=0, microsecond=0, minute=minute
                    )

                if next_allowed_bucket is None:
                    _real_print(
                        f"[blocked-entry] {symbol} unable to compute next_allowed_bucket; cancelling signal."
                    )
                    state.status = "watch"
                    state.signal_candle = None
                    state.signal_close_ts = None
                else:
                    if isinstance(current_bucket, pd.Timestamp):
                        current_bucket = current_bucket.to_pydatetime().replace(
                            tzinfo=None
                        )

                    if current_bucket < next_allowed_bucket:
                        pass
                    elif current_bucket == next_allowed_bucket:
                        trigger = float(state.signal_candle["high"])
                        if ltp > trigger:
                            # Time-based entry cutoff
                            now_ist = tick_ts
                            is_mcx = symbol.startswith("MCX:")

                            if is_mcx and now_ist.hour >= 22:
                                _real_print(
                                    f"[blocked-entry] {symbol} MCX entry blocked after 10 PM IST. Cancelling signal.")
                                state.status = "watch"
                                state.signal_candle = None
                                state.signal_close_ts = None
                                return

                            if not is_mcx and now_ist.hour >= 15:
                                _real_print(
                                    f"[blocked-entry] {symbol} NSE entry blocked after 3 PM IST. Cancelling signal.")
                                state.status = "watch"
                                state.signal_candle = None
                                state.signal_close_ts = None
                                return

                            qty = decide_qty(symbol, ltp)
                            if qty <= 0:
                                state.status = "watch"
                                state.signal_candle = None
                                state.signal_close_ts = None
                                return
                            _real_print(
                                f"[entry-debug] {symbol} next-candle bucket {current_bucket} "
                                f"LTP {ltp} > signal_high {trigger} -> ENTRY"
                            )
                            resp = place_market_order(symbol, qty, side=1)
                            if isinstance(resp, dict) and resp.get("s") == "ok":
                                order_id = resp.get("id")
                                # VERIFY ORDER WAS NOT REJECTED
                                if verify_order_success(order_id):
                                    state.entry_price = ltp
                                    state.qty = qty
                                    state.just_entered = True
                                    state.entry_time = time.time()
                                    state.exit_pending = False
                                    state.exit_signal_candle = None
                                    state.exit_signal_expiry = None
                                    state.exit_try_count = 0
                                    state.last_failed_exit_ts = None

                                    # Capture ATR for trailing
                                    try:
                                        if not state.data.empty and "atr" in state.data.columns:
                                            last_atr = float(state.data["atr"].iloc[-1])
                                            state.atr_at_entry = last_atr
                                            _real_print(f"[entry-debug] ATR at entry: {last_atr:.2f}")
                                        else:
                                            state.atr_at_entry = 0.0
                                    except Exception:
                                        state.atr_at_entry = 0.0

                                    if SL_MODE == "signal_low":
                                        state.stop_price = float(state.signal_candle["low"])
                                    else:
                                        swing = compute_swing_low_for_signal(
                                            state, SWING_LOOKBACK
                                        )
                                        state.stop_price = (
                                            float(state.signal_candle["low"])
                                            if math.isnan(swing) or swing <= 0
                                            else float(swing)
                                        )

                                        # Validate potential target against actual entry price
                                    if (
                                            state.potential_target_price is not None
                                            and state.potential_target_price > state.entry_price
                                    ):
                                        state.target_price = state.potential_target_price
                                    else:
                                        state.target_price = None

                                        # Enhanced logging for target
                                    if state.target_price is not None:
                                        target_str = f"{state.target_price:.2f}"
                                    elif state.potential_target_price is not None:
                                        target_str = f"N/A (target {state.potential_target_price:.2f} <= entry {state.entry_price:.2f})"
                                    else:
                                        target_str = "N/A"

                                    state.potential_target_price = None  # Clear after use

                                    _real_print(
                                        f"[ENTRY CONFIRMED] {state.symbol}: "
                                        f"Entered at {state.entry_price:.2f} | "
                                        f"Target={target_str} | "
                                        f"Stoploss={state.stop_price:.2f}"
                                    )

                                    gtt_resp = place_gtt_stoploss(
                                        symbol, qty, trigger_price=state.stop_price
                                    )
                                    if isinstance(gtt_resp, dict) and gtt_resp.get("s") == "ok":
                                        state.gtt_order_id = (
                                                gtt_resp.get("id") or gtt_resp.get("gtt_id")
                                        )
                                        _real_print(
                                            f"[order] GTT placed id={state.gtt_order_id}"
                                        )
                                    state.status = "position"
                                    state.signal_candle = None
                                    state.signal_close_ts = None
                                else:
                                    _real_print(f"[order] BUY ORDER REJECTED/CANCELLED for {symbol}. Resetting to WATCH.")
                                    state.status = "watch" # Go back to watch, do not cooldown? or cooldown?
                                    # If rejected (e.g. margin), maybe cooldown is better?
                                    # Let's do cooldown to avoid infinite retries on same signal
                                    state.status = "cooldown"
                                    state.signal_candle = None
                                    state.signal_close_ts = None
                            else:
                                _real_print(
                                    f"[order] BUY ORDER FAILED for {symbol}: {resp}"
                                )
                                state.status = "cooldown"
                                state.signal_candle = None
                                state.signal_close_ts = None
                    else:
                        _real_print(
                            f"[entry-debug] {symbol} next candle {next_allowed_bucket} "
                            f"closed without breaking signal_high; cancelling signal."
                        )
                        state.status = "watch"
                        state.signal_candle = None
                        state.signal_close_ts = None
        except Exception as e:
            _real_print(f"[on_tick:entry] error: {e}")

            # EXIT via EXIT EMA (next-candle gating)
    if (
            state.status == "position"
            and state.exit_pending
            and state.exit_signal_candle is not None
            and not skip_exit_checks
    ):
        try:
            tick_ts = (
                ts
                if isinstance(ts, dt)
                else pd.to_datetime(ts).to_pydatetime().replace(tzinfo=None)
            )

            exit_low = float(state.exit_signal_candle["low"])

            try:
                if CANDLE_MANAGER is not None:
                    current_bucket = CANDLE_MANAGER._floor_ts(tick_ts)
                else:
                    minute = (tick_ts.minute // TIMEFRAME_MIN) * TIMEFRAME_MIN
                    current_bucket = tick_ts.replace(
                        second=0, microsecond=0, minute=minute
                    )
            except Exception:
                minute = (tick_ts.minute // TIMEFRAME_MIN) * TIMEFRAME_MIN
                current_bucket = tick_ts.replace(
                    second=0, microsecond=0, minute=minute
                )

            if isinstance(current_bucket, pd.Timestamp):
                current_bucket = current_bucket.to_pydatetime().replace(tzinfo=None)

            exit_sig_start = state.exit_signal_candle.get("ts")
            exit_next_bucket = None
            try:
                exit_floor = pd.to_datetime(exit_sig_start)
                if exit_floor.tzinfo is not None:
                    exit_floor = exit_floor.tz_convert(TIMEZONE).tz_localize(None)
                exit_next_bucket = (
                        exit_floor.to_pydatetime().replace(tzinfo=None)
                        + timedelta(minutes=TIMEFRAME_MIN)
                )
            except Exception:
                if isinstance(exit_sig_start, dt):
                    exit_next_bucket = (
                            exit_sig_start + timedelta(minutes=TIMEFRAME_MIN)
                    ).replace(tzinfo=None)
                else:
                    exit_next_bucket = None

            if exit_next_bucket is not None and current_bucket > exit_next_bucket:
                _real_print(
                    f"[exit-debug] {symbol} next candle {exit_next_bucket} "
                    f"closed without breaking exit_low; cancelling exit_pending."
                )
                state.exit_pending = False
                state.exit_signal_candle = None
                state.exit_signal_expiry = None
                state.exit_try_count = 0
            elif exit_next_bucket is not None and current_bucket == exit_next_bucket:
                if ltp < exit_low:
                    _real_print(
                        f"[{symbol}] EXIT TRIGGERED at LTP {ltp:.2f} (< {exit_low:.2f})"
                    )
                    resp = place_market_order(symbol, state.qty, side=-1)
                    state.exit_try_count += 1
                    if isinstance(resp, dict) and resp.get("s") == "ok":
                        pnl = (ltp - state.entry_price) * state.qty
                        _real_print(f"[{symbol}] EXIT OK. PnL={pnl:.2f}")
                        if state.gtt_order_id:
                            cancel_gtt_order(state.gtt_order_id)
                        state.status = "cooldown"
                        state.exit_pending = False
                        state.exit_signal_candle = None
                        state.target_price = None
                    else:
                        _real_print(f"[{symbol}] EXIT ORDER FAILED: {resp}")
                        state.last_failed_exit_ts = dt.now(IST).replace(tzinfo=None)
                        if state.exit_try_count >= MAX_EXIT_RETRIES:
                            _real_print(f"[{symbol}] EXIT failed {state.exit_try_count} times. Moving to cooldown.")
                            state.status = "cooldown"
                            state.exit_pending = False
                            state.exit_signal_candle = None
                            state.target_price = None
            else:
                if ltp < exit_low:
                    _real_print(
                        f"[{symbol}] EXIT TRIGGERED at LTP {ltp:.2f} (< {exit_low:.2f})"
                    )
                    resp = place_market_order(symbol, state.qty, side=-1)
                    state.exit_try_count += 1
                    if isinstance(resp, dict) and resp.get("s") == "ok":
                        pnl = (ltp - state.entry_price) * state.qty
                        _real_print(f"[{symbol}] EXIT OK. PnL={pnl:.2f}")
                        if state.gtt_order_id:
                            cancel_gtt_order(state.gtt_order_id)
                        state.status = "cooldown"
                        state.exit_pending = False
                        state.exit_signal_candle = None
                        state.target_price = None
                    else:
                        _real_print(f"[{symbol}] EXIT ORDER FAILED: {resp}")
                        state.last_failed_exit_ts = dt.now(IST).replace(tzinfo=None)
                        if state.exit_try_count >= MAX_EXIT_RETRIES:
                            _real_print(f"[{symbol}] EXIT failed {state.exit_try_count} times. Moving to cooldown.")
                            state.status = "cooldown"
                            state.exit_pending = False
                            state.exit_signal_candle = None
                            state.target_price = None
        except Exception as e:
            _real_print(f"[on_tick:ema_exit] error: {e}")

            # STOP LOSS
    if (
            state.status == "position"
            and state.stop_price
            and state.stop_price > 0
            and not skip_exit_checks
    ):
        # TRAILING STOP LOGIC (ATR-based cost-to-cost)
        if (
                TRAIL_ATR_MULT is not None
                and TRAIL_ATR_MULT > 0
                and state.atr_at_entry > 0
                and not state.sl_trailed
        ):
            try:
                dist = state.atr_at_entry * TRAIL_ATR_MULT
                trigger_val = state.entry_price + dist
                if ltp >= trigger_val:
                    _real_print(
                        f"[{symbol}] Trailing SL Triggered: LTP {ltp:.2f} >= Entry {state.entry_price:.2f} + {TRAIL_ATR_MULT}xATR ({dist:.2f})"
                    )
                    # Move SL to Entry Price (Cost-to-Cost)
                    new_sl = state.entry_price
                    state.stop_price = new_sl
                    state.sl_trailed = True
                    _real_print(f"[{symbol}] Moving Stop Loss to {new_sl:.2f}")

                    # Update GTT if it exists
                    if state.gtt_order_id:
                        _real_print(f"[{symbol}] Cancelling old GTT {state.gtt_order_id} to place new trailed GTT.")
                        cancel_gtt_order(state.gtt_order_id)
                        state.gtt_order_id = None

                        # Place new GTT at new SL
                    gtt_resp = place_gtt_stoploss(symbol, state.qty, trigger_price=new_sl)
                    if isinstance(gtt_resp, dict) and gtt_resp.get("s") == "ok":
                        state.gtt_order_id = gtt_resp.get("id") or gtt_resp.get("gtt_id")
                        _real_print(f"[{symbol}] New Trailed GTT placed id={state.gtt_order_id}")
                    else:
                        _real_print(f"[{symbol}] Failed to place new trailed GTT: {gtt_resp}")

            except Exception as e:
                _real_print(f"[{symbol}] Trailing SL logic error: {e}")

        try:
            if ltp <= state.stop_price:
                # Check cooldown to prevent rapid looping on failure
                if state.last_failed_exit_ts:
                    diff = (dt.now(IST).replace(tzinfo=None) - state.last_failed_exit_ts).total_seconds()
                    if diff < 10:
                        return

                _real_print(
                    f"[{symbol}] STOP-LOSS HIT at LTP {ltp:.2f} (<= {state.stop_price:.2f})"
                )
                sell_resp = place_market_order(symbol, state.qty, side=-1)
                log_trade_event(
                    symbol, "STOP_SELL", state.qty, ltp, sell_resp
                )
                if isinstance(sell_resp, dict) and sell_resp.get("s") == "ok":
                    pnl = (ltp - state.entry_price) * state.qty
                    _real_print(f"[{symbol}] STOP-LOSS SELL OK. PnL={pnl:.2f}")
                    if state.gtt_order_id:
                        cancel_gtt_order(state.gtt_order_id)
                    state.status = "cooldown"
                    state.gtt_order_id = None
                    state.target_price = None
                else:
                    _real_print(f"[{symbol}] STOP-LOSS SELL FAILED: {sell_resp}")
                    state.last_failed_exit_ts = dt.now(IST).replace(tzinfo=None)
                    # If sell fails, check if we are still in the grace period
                    if (dt.now(IST).replace(tzinfo=None).timestamp() - getattr(state, "entry_time", 0)) < 30:
                        _real_print(f"[{symbol}] Sell failed inside grace period. Waiting 5s for API sync...")
                        time.sleep(5)
                    else:
                        # If outside grace period, force sync to detect manual close
                        _real_print(f"[{symbol}] Forcing immediate position sync due to order failure...")
                        sync_with_broker_positions(force_sync=True)
        except Exception as e:
            _real_print(f"[on_tick:stop_loss] error: {e}")

    if state.just_entered:
        state.just_entered = False

        # ---------------------------- STRATEGY EVALUATOR ----------------------------


def evaluate_on_new_candle(st: SymbolState):
    df = st.data
    if df is None or df.shape[0] < 2:  # Need at least 2 rows for previous EMA
        return

    last_ts = st.last_candle_ts
    if last_ts is None or last_ts not in df.index:
        return

    curr = df.loc[last_ts]
    prev = df.iloc[-2]  # Get previous candle's data

    curr_open = float(curr["open"])
    curr_low = float(curr["low"])
    curr_high = float(curr["high"])
    curr_close = float(curr["close"])

    prev_high = float(prev["high"])

    ema_fast = float(curr.get("ema_fast_entry", float("nan")))
    ema_slow = float(curr.get("ema_slow_entry", float("nan")))
    ema_slow_prev = float(prev.get("ema_slow_entry", float("nan")))
    ema_exit = float(curr.get("ema_exit", float("nan")))

    # ENTRY SIGNAL  (STRICT BODY CROSS)
    if st.status == "watch":
        ema_sequence_ok = ema_fast > ema_slow
        rising_slow_ema = ema_slow > ema_slow_prev  # New condition

        lowest_ema = min(ema_fast, ema_slow)
        highest_ema = max(ema_fast, ema_slow)

        # MODIFIED ENTRY RULE:
        # 1. EMA_fast > EMA_slow (Uptrend)
        # 2. Candle Low <= EMA_slow (Touched or crossed below Slow EMA)
        # 3. Candle Close > Both EMAs (Strong close)
        # 4. Higher High: Current High > Previous High
        touched_slow_ema = curr_low <= ema_slow
        closed_above_both = curr_close > (highest_ema + EMA_BUFFER)
        green_ok = (not REQUIRE_GREEN_SIGNAL) or (curr_close > curr_open)
        ok_signal = bool(curr.get("ok_signal", True))
        higher_high = curr_high > prev_high

        # Debug Log for Higher High (only if other conditions met, to reduce spam)
        if ema_sequence_ok and rising_slow_ema and touched_slow_ema and closed_above_both:
            pass  # Placeholder if detailed logging needed
            # _real_print(f"[signal-check] {st.symbol} HigherHigh={higher_high} (CurrHigh:{curr_high} > PrevHigh:{prev_high})")

        if (
                ema_sequence_ok
                and rising_slow_ema
                and touched_slow_ema
                and closed_above_both
                and green_ok
                and ok_signal
                and higher_high
        ):
            _real_print(f"[signal-confirm] {st.symbol} Higher High Condition MET: {curr_high:.2f} > {prev_high:.2f}")
            st.signal_candle = {
                "ts": curr.name,
                "open": curr_open,
                "high": curr_high,
                "low": curr_low,
                "close": curr_close,
            }

            # Calculate potential target and store it for validation after entry
            # Use current signal high as reference for "nearest swing high > entry"
            target = compute_prev_swing_high_for_entry(st, SWING_HIGH_LOOKBACK, reference_price=curr_high)
            st.potential_target_price = float(target) if target is not None and not math.isnan(target) else None

            try:
                sig_start = pd.to_datetime(curr.name)
                if sig_start.tzinfo is not None:
                    sig_start = sig_start.tz_convert(TIMEZONE).tz_localize(None)
                sig_close_ts = (
                        sig_start + pd.Timedelta(minutes=TIMEFRAME_MIN)
                ).to_pydatetime().replace(tzinfo=None)
            except Exception:
                if isinstance(curr.name, dt):
                    sig_close_ts = (
                            curr.name + timedelta(minutes=TIMEFRAME_MIN)
                    ).replace(tzinfo=None)
                else:
                    sig_close_ts = None
            st.signal_close_ts = sig_close_ts
            st.signal_expiry = curr.name + pd.Timedelta(minutes=TIMEFRAME_MIN)
            st.status = "entry_pending"
            st.signal_notified = False
            st.qty = decide_qty(st.symbol, curr_high)
            _real_print(
                f"****** [{st.symbol}] ENTRY SIGNAL (BODY crossed from below both EMAs to above both EMAs, EMA_fast>EMA_slow) ******")

            target_info = (
                f"potential_target={st.potential_target_price:.2f}"
                if st.potential_target_price is not None
                else "no-target"
            )
            _real_print(
                f"[signal:{st.symbol}] signal_high={curr_high:.2f} signal_low={curr_low:.2f} | "
                f"{target_info} | waiting for NEXT CANDLE to attempt breakout entry"
            )

            # EXIT SIGNAL (EXIT EMA)
    if st.status == "position":
        intrabar_up = (curr_open < ema_exit) and (curr_high > ema_exit)
        closed_below = curr_close < ema_exit - EMA_BUFFER
        is_red = curr_close < curr_open
        if is_red and intrabar_up and closed_below:
            st.exit_signal_candle = {
                "ts": curr.name,
                "open": curr_open,
                "high": curr_high,
                "low": curr_low,
                "close": curr_close,
            }
            st.exit_signal_expiry = curr.name + pd.Timedelta(minutes=TIMEFRAME_MIN)
            st.exit_pending = True
            st.exit_try_count = 0
            _real_print(
                f"****** [{st.symbol}] EXIT SIGNAL (RED candle crossed & closed below EXIT EMA) ******"
            )
            _real_print(
                f"[exit:{st.symbol}] exit_low={curr_low:.2f} exit_high={curr_high:.2f} | "
                f"waiting for NEXT CANDLE to attempt exit on break below exit_low"
            )

            # ---------------------------- WEBSOCKET HANDLERS ----------------------------


def on_ws_message(raw):
    try:
        if not isinstance(raw, list):
            msgs = [raw]
        else:
            msgs = raw
        for m in msgs:
            symbol = m.get("symbol") or m.get("scrip") or m.get("instrument")
            ltp = m.get("ltp") or m.get("last_price")
            timestamp = m.get("timestamp") or m.get("time")
            if symbol and ltp is not None:
                on_tick(symbol, float(ltp), timestamp)
    except Exception as e:
        _real_print(f"[ws] on_message error: {e}")


def detect_and_print_signals_on_open():
    """
    Kept for future use, but NO LONGER CALLED at on_ws_open().
    Live signals now only come from new completed candles via CandleManager.
    """
    try:
        for sym, st in SYMBOL_STATES.items():
            try:
                df = None
                if getattr(st, "data", None) is not None and not st.data.empty:
                    df = st.data.copy()
                elif CANDLE_MANAGER is not None and CANDLE_MANAGER.history.get(sym) is not None:
                    df = compute_indicators(CANDLE_MANAGER.history.get(sym).copy())
                else:
                    if FYERS is not None and hasattr(FYERS, "history"):
                        try:
                            r = FYERS.history(
                                symbol=sym,
                                resolution=f"{TIMEFRAME_MIN}",
                                range_from=(dt.now() - timedelta(days=2)).strftime("%Y-%m-%d"),
                                range_to=dt.now().strftime("%Y-%m-%d"),
                                cont_flag=0,
                            )
                            if isinstance(r, dict) and r.get("s") == "ok":
                                df_tmp = pd.DataFrame(r["candles"],
                                                      columns=["ts", "open", "high", "low", "close", "volume"])
                                df_tmp["ts"] = (pd.to_datetime(df_tmp["ts"], unit="s", utc=True).dt.tz_convert(
                                    TIMEZONE).dt.tz_localize(None))
                                df_tmp = df_tmp.set_index("ts")[["open", "high", "low", "close"]]
                                df = compute_indicators(df_tmp)
                        except Exception:
                            df = None
                if df is None or df.empty:
                    continue
                last_ts = df.index[-1]
                st.data = df
                st.last_candle_ts = last_ts
            except Exception:
                continue
    except Exception:
        pass


def on_ws_open():
    _real_print(f"[ws:open] subscribing to {len(SYMBOLS)} symbols...")
    try:
        FYERS_SOCKET.subscribe(symbols=SYMBOLS, data_type="SymbolUpdate")
    except Exception as e:
        _real_print("[ws:open] subscribe failed:", e)

    try:
        for sym, st in SYMBOL_STATES.items():
            try:
                if st is None:
                    continue
                if getattr(st, "status", None) == "entry_pending" and st.signal_candle:
                    sc = st.signal_candle
                    _real_print(
                        f"****** [{sym}] ENTRY SIGNAL (BODY crossed from below both EMAs to above both EMAs, EMA_fast>EMA_slow) ******")
                    _real_print(
                        f"[signal:{sym}] signal_high={float(sc.get('high')):.2f} signal_low={float(sc.get('low')):.2f} | waiting for NEXT CANDLE to attempt breakout entry"
                    )
                if getattr(st, "status", None) == "position" and getattr(st, "exit_pending",
                                                                         False) and st.exit_signal_candle:
                    ec = st.exit_signal_candle
                    _real_print(f"****** [{sym}] EXIT SIGNAL (RED candle crossed & closed below EXIT EMA) ******")
                    _real_print(
                        f"[exit:{sym}] exit_low={float(ec.get('low')):.2f} exit_high={float(ec.get('high')):.2f} | waiting for NEXT CANDLE to attempt exit on break below exit_low")
            except Exception:
                continue
    except Exception:
        pass
        # NO detect_and_print_signals_on_open() call here


def _clear_config_access_token():
    try:
        if not os.path.exists(CONFIG_FILE):
            return
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f) or {}
        if not isinstance(data, dict):
            return
        changed = False
        for k in ("access_token", "accessToken", "token"):
            if k in data:
                data.pop(k, None)
                changed = True
        if changed:
            with open(CONFIG_FILE, "w") as f:
                json.dump(data, f, indent=2)
    except Exception:
        pass


def on_ws_error(err):
    global REAUTH_ATTEMPTS
    _real_print("[ws:error]", err)
    try:
        code = None
        msg = ""
        if isinstance(err, dict):
            code = err.get("code") or err.get("status") or None
            msg = str(err.get("message") or err.get("msg") or "")
        else:
            msg = str(err)
        expired = False
        if code in (-99, 401, "expired"):
            expired = True
        if (
                "expired" in msg.lower()
                or "token is expired" in msg.lower()
                or "invalid token" in msg.lower()
        ):
            expired = True
        if expired:
            if REAUTH_ATTEMPTS >= MAX_REAUTH_ATTEMPTS:
                _real_print(
                    "[ws:error] Token expired, but max re-auth attempts reached. Please restart script and login again.")
                return
            REAUTH_ATTEMPTS += 1
            _real_print(
                "[ws:error] Token expired detected. Attempting automatic refresh..."
            )
            _remove_local_tokens(TOKENS_DIR)
            _clear_config_access_token()

            # Force interactive login flow to get a fresh token
            try:
                _real_print("[auth] Prompting for new login to refresh token...")
                new_token = run_interactive_login()
                if new_token:
                    global ACCESS_TOKEN
                    ACCESS_TOKEN = new_token
            except Exception as e:
                _real_print(f"[auth] Interactive login failed: {e}")

            ok = _recreate_fyers_and_ws()
            if ok:
                _real_print("[ws:error] Re-auth and reconnect succeeded.")
            else:
                _real_print(
                    "[ws:error] Re-auth failed. Please run interactive auth or place a new token in AccessToken/"
                )
    except Exception as e:
        _real_print("[ws:error] on_ws_error handler exception:", e)


def on_ws_close(msg):
    _real_print("[ws:close]", msg)


# ---------------------------- History warmup ----------------------------
def fetch_history(fyers_client, symbol: str, days: int = 2) -> pd.DataFrame:
    if fyers_client is None:
        return pd.DataFrame()
    end = dt.now(IST).date()
    start = end - timedelta(days=days)
    payload = {
        "symbol": symbol,
        "resolution": str(TIMEFRAME_MIN),
        "date_format": "1",
        "range_from": start.strftime("%Y-%m-%d"),
        "range_to": end.strftime("%Y-%m-%d"),
        "cont_flag": "1",
    }
    try:
        r = fyers_client.history(data=payload)
        if not isinstance(r, dict) or r.get("s") != "ok":
            return pd.DataFrame()
        df = pd.DataFrame(
            r["candles"],
            columns=["ts", "open", "high", "low", "close", "volume"],
        )
        df["ts"] = (
            pd.to_datetime(df["ts"], unit="s", utc=True)
            .dt.tz_convert(TIMEZONE)
            .dt.tz_localize(None)
        )
        df = df.set_index("ts")[["open", "high", "low", "close"]]
        return df
    except Exception:
        return pd.DataFrame()


def warmup_all_full(fyers_client):
    if fyers_client is None:
        return
    for sym in SYMBOLS:
        try:
            df = fetch_history(fyers_client, sym, days=3)
            if df is None or df.empty:
                continue
            df = compute_indicators(df)
            SYMBOL_STATES[sym].data = df
            SYMBOL_STATES[sym].last_candle_ts = df.index[-1]
        except Exception:
            continue

            # ---------------------------- Token/WS recreation helpers ----------------------------


def _remove_local_tokens(dir_path=TOKENS_DIR):
    try:
        if not os.path.exists(dir_path):
            return
        for fn in glob.glob(os.path.join(dir_path, "*.json")):
            try:
                os.remove(fn)
            except Exception:
                pass
    except Exception:
        pass


def _recreate_fyers_and_ws():
    global FYERS, FYERS_SOCKET, ACCESS_TOKEN
    try:
        auth = get_access_token()
        ACCESS_TOKEN = auth.get("access_token") or auth.get("token") or ACCESS_TOKEN
        client_id = (
            ACCESS_TOKEN.split(":")[0]
            if ":" in (ACCESS_TOKEN or "")
            else ACCESS_TOKEN
        )
        FYERS = fyersModel.FyersModel(
            client_id=client_id, is_async=False, token=ACCESS_TOKEN, log_path=""
        )
        _real_print("[auth] Re-created fyers client after refresh.")
    except Exception as e:
        _real_print("[auth] Re-auth failed:", e)
        return False

    try:
        new_socket = data_ws.FyersDataSocket(
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
        _real_print("[ws] Attempting to reconnect websocket with refreshed token...")
        globals()["FYERS_SOCKET"] = new_socket
        new_socket.connect()
        return True
    except Exception as e:
        _real_print("[ws] Reconnect failed:", e)
        return False

    # ---------------------------- SYNC WITH BROKER ----------------------------


def sync_with_broker_positions(force_sync=False):
    """
    Periodically syncs bot state with Fyers broker positions.
    - If bot has a position but broker doesn't -> Mark as closed (manual exit).
    - If bot has a position and broker has different qty/price -> Update bot state.
    - If broker has a position but bot doesn't -> Ignore (don't manage other trades).
    :param force_sync: If True, bypass the 30-second grace period (used when order fails).
    """
    if FYERS is None:
        return

    try:
        # 1. Fetch POSITIONS (Net/Intraday)
        pos_resp = FYERS.positions()
        net_positions = []
        if isinstance(pos_resp, dict) and pos_resp.get("s") == "ok":
            net_positions = pos_resp.get("netPositions", [])
        else:
            _real_print(f"[sync] Error fetching positions: {pos_resp}")
            return

        # 2. Fetch HOLDINGS (CNC delivery from previous days)
        hold_resp = FYERS.holdings()
        holdings_list = []
        if isinstance(hold_resp, dict) and hold_resp.get("s") == "ok":
            holdings_list = hold_resp.get("holdings", [])
        else:
            # If holdings fail, we shouldn't necessarily abort, but log it.
            _real_print(f"[sync] Warning fetching holdings: {hold_resp}")

        # Create map of symbol -> total open quantity
        # Key: symbol, Value: quantity
        # NORMALIZE KEYS to handle NSE:X vs X-EQ issues
        broker_qty_map = {}

        def normalize(s):
            # Remove NSE: or MCX: prefix and -EQ suffix for looser matching
            # But be careful not to merge different symbols.
            # Ideally Fyers v3 uses full name.
            if not s: return ""
            return s.upper().replace("NSE:", "").replace("MCX:", "").replace("BSE:", "")

        for p in net_positions:
            sym = p.get("symbol")
            qty = int(p.get("netQty", 0))
            if qty != 0:
                # Store exact match
                broker_qty_map[sym] = broker_qty_map.get(sym, 0) + qty
                # Store normalized match
                norm = normalize(sym)
                if norm != sym:
                    broker_qty_map[norm] = broker_qty_map.get(norm, 0) + qty

        for h in holdings_list:
            sym = h.get("symbol")
            qty = int(h.get("quantity", 0))  # Holdings usually strictly long/positive
            if qty != 0:
                broker_qty_map[sym] = broker_qty_map.get(sym, 0) + qty
                norm = normalize(sym)
                if norm != sym:
                    broker_qty_map[norm] = broker_qty_map.get(norm, 0) + qty

        # Check if we need to fetch orders (only if we have missing positions that bot thinks are open)
        pending_orders_map = {}
        need_orderbook = False
        for sym, state in SYMBOL_STATES.items():
            if state.status == "position":
                # Check exact OR normalized
                if broker_qty_map.get(sym, 0) == 0 and broker_qty_map.get(normalize(sym), 0) == 0:
                    # Potential missing position - we might need to check if it's pending in orderbook
                    need_orderbook = True
                    break

        if need_orderbook:
            try:
                ord_resp = FYERS.orderbook()
                if isinstance(ord_resp, dict) and ord_resp.get("s") == "ok":
                    orders = ord_resp.get("orderBook", [])
                    for o in orders:
                        # Check for pending Buy/Sell orders
                        status = o.get("status")
                        s = o.get("symbol")
                        if status in (6, 4, 11): # 6=Pending, 4=Transit, 11=Open
                            pending_orders_map[s] = True
                            pending_orders_map[normalize(s)] = True
            except Exception as e:
                _real_print(f"[sync] Error fetching orderbook: {e}")

        # Iterate over monitored symbols that are in 'position' state
        for sym, state in SYMBOL_STATES.items():
            if state.status == "position":
                # Check Grace Period
                if not force_sync:
                    # If we entered less than 30 seconds ago, skip sync to allow API update
                    if state.entry_time > 0 and (time.time() - state.entry_time) < 30:
                        continue

                actual_qty = broker_qty_map.get(sym, 0)
                if actual_qty == 0:
                    # Try normalized lookup
                    actual_qty = broker_qty_map.get(normalize(sym), 0)

                # Case 1: Bot has position, Broker has 0
                if actual_qty == 0:
                    # Check if we have a pending order (Exact or Normalized)
                    if pending_orders_map.get(sym) or pending_orders_map.get(normalize(sym)):
                        # Pending order exists, so don't close the position yet
                        # _real_print(f"[sync] {sym} has 0 qty but PENDING order found. Waiting.")
                        pass
                    else:
                        _real_print(f"[sync] Position for {sym} missing in broker (qty=0). Resetting to WATCH.")
                        state.status = "watch"
                        state.qty = 0
                        state.entry_price = 0.0
                        state.stop_price = 0.0
                        state.target_price = None
                        state.exit_pending = False
                        state.exit_signal_candle = None
                        if state.gtt_order_id:
                            cancel_gtt_order(state.gtt_order_id)
                            state.gtt_order_id = None
                    continue

                # Case 2: Broker has position. Compare Quantities.
                # Logic:
                # - If broker < bot: Partial close happened externally. Update bot.
                # - If broker > bot: Extra shares bought externally. Ignore extra (Isolate).
                # - If broker == bot: All good.

                if actual_qty < state.qty:
                    _real_print(f"[sync] Qty mismatch for {sym}: Bot={state.qty}, Broker={actual_qty}. Updating bot to {actual_qty}.")
                    state.qty = actual_qty
                elif actual_qty > state.qty:
                    # _real_print(f"[sync] Ignoring excess qty for {sym}: Bot={state.qty}, Broker={actual_qty}.")
                    pass

    except Exception as e:
        _real_print(f"[sync] Exception: {e}")

        # ---------------------------- STATE SAVE/LOAD ----------------------------


def _serialize_state():
    out = {}
    for sym, st in SYMBOL_STATES.items():
        out[sym] = {
            "status": getattr(st, "status", None),
            "qty": getattr(st, "qty", 0),
            "entry_price": getattr(st, "entry_price", None),
            "stop_price": getattr(st, "stop_price", None),
            "gtt_order_id": getattr(st, "gtt_order_id", None),
            "last_candle_ts": (
                getattr(st, "last_candle_ts", None).isoformat()
                if getattr(st, "last_candle_ts", None) is not None
                else None
            ),
            "signal_notified": getattr(st, "signal_notified", False),
            "target_price": getattr(st, "target_price", None),
            "atr_at_entry": getattr(st, "atr_at_entry", 0.0),
            "sl_trailed": getattr(st, "sl_trailed", False),
            "entry_time": getattr(st, "entry_time", 0.0),
        }
    return out


def save_state_to_disk():
    try:
        with open(STATE_DUMP, "w") as f:
            json.dump(_serialize_state(), f, indent=2)
    except Exception as e:
        _real_print("[state] Failed to save state:", e)


def load_state_from_disk():
    if not os.path.exists(STATE_DUMP):
        return
    try:
        with open(STATE_DUMP, "r") as f:
            raw = json.load(f)
        for sym, info in raw.items():
            if sym not in SYMBOL_STATES:
                SYMBOL_STATES[sym] = SymbolState(sym)
            st = SYMBOL_STATES[sym]
            st.status = info.get("status", st.status)
            st.qty = info.get("qty", st.qty)
            st.entry_price = info.get("entry_price", st.entry_price)
            st.stop_price = info.get("stop_price", st.stop_price)
            st.gtt_order_id = info.get("gtt_order_id", st.gtt_order_id)
            st.signal_notified = info.get("signal_notified", False)
            st.target_price = info.get("target_price", None)
            st.atr_at_entry = info.get("atr_at_entry", 0.0)
            st.sl_trailed = info.get("sl_trailed", False)
            st.entry_time = info.get("entry_time", 0.0)
    except Exception as e:
        _real_print("[state] Failed to load state:", e)


atexit.register(lambda: save_state_to_disk())


# ---------------------------- CLI & Main ----------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Fast-Slow EMA Strategy - Strict next-candle entry (Fyers v3)")
    p.add_argument("--timeframe", "-t", type=int, default=TIMEFRAME_MIN)
    p.add_argument("--exit-ema", type=int, default=EXIT_EMA)
    p.add_argument("--entry-fast-ema", type=int, default=ENTRY_FAST_EMA)
    p.add_argument("--entry-slow-ema", type=int, default=ENTRY_SLOW_EMA)
    p.add_argument("--min-range-pct", type=float, default=MIN_RANGE_PCT,
                   help="Minimum candle range fraction (e.g., 0.001 for 0.1%%)")
    p.add_argument("--ema-buffer", type=float, default=EMA_BUFFER,
                   help="Buffer above/below EMA to confirm breaks (float)")
    p.add_argument("--test-table", action="store_true", help="Run test mode without Fyers")
    p.add_argument("--no-require-green", dest="require_green", action="store_false")
    p.add_argument("--use-ltp-entry", action="store_true", help="Use LTP immediate entry/exit (default)")
    p.add_argument("--position-mode", choices=["alloc", "qty"], default=POSITION_MODE, help="Position sizing mode")
    p.add_argument("--fixed-qty", type=int, default=None,
                   help="When --position-mode qty is set, fixed quantity per trade (default 1).")
    p.add_argument("--qty-map", type=str, default="",
                   help='Optional per-symbol qty map as JSON string, e.g. \'{"NSE:RELIANCE-EQ":2}\'.')
    p.add_argument("--product-type", type=str, default=PRODUCT_TYPE, help="Order product type: 'CNC' or 'Intraday'.")
    p.add_argument("--sl-mode", type=str, default=SL_MODE, help="Stop-loss mode: 'signal_low' or 'swing_low'.")
    p.add_argument("--qty-map-file", type=str, default="", help="Optional file path to JSON with per-symbol qty map.")
    p.add_argument("--trail-atr-mult", type=float, default=TRAIL_ATR_MULT,
                   help="Multiplier for ATR Trailing Stop (move to cost). Set negative to disable.")
    p.set_defaults(require_green=True)
    return p.parse_args()


def print_startup(args):
    _real_print("[update] Timeframe =", TIMEFRAME_MIN, "m")
    _real_print("[update] Exit EMA =", EXIT_EMA)
    _real_print("[update] Entry fast EMA =", ENTRY_FAST_EMA)
    _real_print("[update] Entry slow EMA =", ENTRY_SLOW_EMA)
    _real_print("[update] EMA buffer =", EMA_BUFFER)
    _real_print("[update] Require green signal =", REQUIRE_GREEN_SIGNAL)
    _real_print("[mode] Order mode set to", PRODUCT_TYPE)
    _real_print("[mode] SL_MODE =", SL_MODE, " POSITION_MODE =", POSITION_MODE)
    _real_print("[mode] FIXED_QTY =", FIXED_QTY)
    _real_print("[mode] TRAIL_ATR_MULT =", TRAIL_ATR_MULT)


def main():
    global TIMEFRAME_MIN, EXIT_EMA, ENTRY_FAST_EMA, ENTRY_SLOW_EMA, EMA_BUFFER, MIN_RANGE_PCT, REQUIRE_GREEN_SIGNAL
    global CANDLE_MANAGER, FYERS, FYERS_SOCKET, ACCESS_TOKEN
    global POSITION_MODE, FIXED_QTY, QTY_MAP, ALLOC_DEFAULT, PRODUCT_TYPE, SL_MODE
    global TRAIL_ATR_MULT

    args = parse_args()
    file_settings = load_settings_file()

    def pick(name, cli_val, default_val):
        try:
            if cli_val is not None and cli_val != default_val:
                return cli_val
            if name in file_settings:
                return file_settings[name]
            return default_val
        except Exception:
            return default_val

    TIMEFRAME_MIN = int(pick("timeframe", args.timeframe, TIMEFRAME_MIN))
    EXIT_EMA = int(pick("exit_ema", args.exit_ema, EXIT_EMA))
    ENTRY_FAST_EMA = int(pick("entry_fast_ema", getattr(args, "entry_fast_ema", None), ENTRY_FAST_EMA))
    ENTRY_SLOW_EMA = int(pick("entry_slow_ema", getattr(args, "entry_slow_ema", None), ENTRY_SLOW_EMA))
    EMA_BUFFER = float(pick("ema_buffer", args.ema_buffer, EMA_BUFFER))
    MIN_RANGE_PCT = float(pick("min_range_pct", getattr(args, "min_range_pct", MIN_RANGE_PCT), MIN_RANGE_PCT))
    REQUIRE_GREEN_SIGNAL = bool(pick("require_green", getattr(args, "require_green", None), REQUIRE_GREEN_SIGNAL))

    POSITION_MODE = str(pick("position_mode", getattr(args, "position_mode", None), POSITION_MODE)).lower()

    cli_fixed = getattr(args, "fixed_qty", None)
    FIXED_QTY = int(pick("fixed_qty", cli_fixed, FIXED_QTY if FIXED_QTY is not None else 1))

    ALLOC_DEFAULT = float(pick("alloc_default", None, ALLOC_DEFAULT))
    PRODUCT_TYPE = str(pick("product_type", getattr(args, "product_type", None), PRODUCT_TYPE))
    SL_MODE = str(pick("sl_mode", getattr(args, "sl_mode", None), SL_MODE)).lower()

    if args.qty_map_file:
        try:
            with open(args.qty_map_file, "r") as f:
                qm = json.load(f)
            if isinstance(qm, dict):
                QTY_MAP.update({k: int(v) for k, v in qm.items()})
        except Exception as e:
            _real_print("[warn] Failed to load qty-map file:", e)
    if args.qty_map:
        try:
            parsed = json.loads(args.qty_map)
            if isinstance(parsed, dict):
                QTY_MAP.update({k: int(v) for k, v in parsed.items()})
        except Exception:
            _real_print("[warn] Failed to parse --qty-map JSON; ignoring.")

    ALLOWED_PRODUCT = {"CNC", "Intraday"}
    ALLOWED_SL = {"signal_low", "swing_low"}
    ALLOWED_POS = {"alloc", "qty"}

    if PRODUCT_TYPE not in ALLOWED_PRODUCT:
        _real_print(f"[warn] Invalid PRODUCT_TYPE '{PRODUCT_TYPE}'. Falling back to 'CNC'.")
        PRODUCT_TYPE = "CNC"
    if SL_MODE not in ALLOWED_SL:
        _real_print(f"[warn] Invalid SL_MODE '{SL_MODE}'. Falling back to 'signal_low'.")
        SL_MODE = "signal_low"
    if POSITION_MODE not in ALLOWED_POS:
        _real_print(f"[warn] Invalid POSITION_MODE '{POSITION_MODE}'. Falling back to 'alloc'.")
        POSITION_MODE = "alloc"

    cli_trail = getattr(args, "trail_atr_mult", None)
    TRAIL_ATR_MULT = float(pick("trail_atr_mult", cli_trail, TRAIL_ATR_MULT))
    if TRAIL_ATR_MULT < 0:
        TRAIL_ATR_MULT = None  # Disabled

    TIMEFRAME_MIN = max(1, int(TIMEFRAME_MIN))
    EXIT_EMA = max(1, int(EXIT_EMA))
    ENTRY_FAST_EMA = max(1, int(ENTRY_FAST_EMA))
    ENTRY_SLOW_EMA = max(1, int(ENTRY_SLOW_EMA))

    print_startup(args)

    try:
        CANDLE_MANAGER = CandleManager(TIMEFRAME_MIN, on_candle=on_completed_candle, tz=TIMEZONE)
    except Exception:
        CANDLE_MANAGER = CandleManager(TIMEFRAME_MIN, on_candle=on_completed_candle)

    if args.test_table or fyersModel is None:
        FYERS = None
        _real_print("[mode] TEST TABLE mode (no fyers). Warmup synthetic data.")
        for sym in SYMBOLS:
            st = SYMBOL_STATES[sym]
            idx = pd.date_range(end=dt.now(), periods=200, freq=f"{TIMEFRAME_MIN}min")
            df = pd.DataFrame(
                {
                    "open": np.linspace(100, 110, len(idx)),
                    "high": np.linspace(101, 111, len(idx)),
                    "low": np.linspace(99, 109, len(idx)),
                    "close": np.linspace(100, 110, len(idx)),
                },
                index=idx,
            )
            df.index.name = "datetime"
            st.data = compute_indicators(df)
            st.last_candle_ts = st.data.index[-1]
    else:
        try:
            auth = get_access_token()
            ACCESS_TOKEN = auth["access_token"]
        except Exception as e:
            _real_print("[auth] Failed to obtain access token:", e)
            return

        client_id = ACCESS_TOKEN.split(":")[0] if ":" in ACCESS_TOKEN else ACCESS_TOKEN
        FYERS = fyersModel.FyersModel(
            client_id=client_id,
            is_async=False,
            token=ACCESS_TOKEN,
            log_path=""
        )

        _real_print("[auth] Fyers model initialized. Running warmup history fetch (best-effort).")
        warmup_all(FYERS)
        warmup_all_full(FYERS)

        global FYERS_SOCKET
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

        _real_print("\n================= FAST-SLOW EMA BOT — STARTUP SUMMARY =================")
        _real_print(f"TIMEFRAME_MIN   = {TIMEFRAME_MIN}")
        _real_print(f"EXIT_EMA        = {EXIT_EMA}")
        _real_print(f"ENTRY_FAST_EMA  = {ENTRY_FAST_EMA}")
        _real_print(f"ENTRY_SLOW_EMA  = {ENTRY_SLOW_EMA}")
        _real_print("")
        _real_print(f"PRODUCT_TYPE    = \"{PRODUCT_TYPE}\"")
        _real_print(f"POSITION_MODE   = \"{POSITION_MODE}\" (FIXED_QTY={FIXED_QTY})")
        _real_print("=====================================================================\n")

        try:
            _real_print("[start] Connecting WebSocket...")
            FYERS_SOCKET.connect()
        except Exception as e:
            _real_print("[ws] connect failed:", e)
            return

    load_state_from_disk()

    if args.test_table:
        CANDLE_MANAGER.force_close_all_up_to()
        _real_print("[test] Emitted synthetic candles to evaluator. Exiting (test mode).")
        return

    try:
        _real_print("[main] Starting main loop. Press Ctrl+C to exit.")

        # Initial sync
        if FYERS is not None:
            _real_print("[main] Performing initial position sync...")
            sync_with_broker_positions()

        last_sync_time = time.time()
        SYNC_INTERVAL = 60  # seconds

        while True:
            # Periodic Sync
            if FYERS is not None and (time.time() - last_sync_time > SYNC_INTERVAL):
                sync_with_broker_positions()
                last_sync_time = time.time()

            time.sleep(1)
    except KeyboardInterrupt:
        _real_print("\n[exit] Interrupted by user. Shutting down.")
    except Exception as e:
        _real_print(f"[fatal] Unexpected error: {e}")


if __name__ == "__main__":
    main()
