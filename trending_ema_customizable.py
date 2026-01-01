# Fast Slow EMA Strategy - Strict Next Candle Entry, Fyers v3 login merged
# -*- coding: utf-8 -*-
"""
- ENTRY (Bullish, using VWAP & ENTRY_FAST_EMA):
    * Signal candle (strict body cross):
        - Candle OPEN is AT or BELOW VWAP.
        - Candle CLOSE is ABOVE VWAP.
        - EMA_fast > VWAP (trend confirmation).
        - (optional) candle is green if REQUIRE_GREEN_SIGNAL is True.
        - tiny-candle filter via MIN_RANGE_PCT if enabled.
    * ENTRY:
        - Only allowed on the VERY NEXT candle.
        - During that next candle, if any tick LTP > signal_high -> market BUY.

- STOPLOSS:
    * Either signal low or swing low (SL_MODE = "signal_low" or "swing_low").

- EXIT:
    * EXIT EMA: red candle crosses & closes below EXIT_EMA → exit_pending.
      Only next candle can trigger actual exit on break below exit_low.
    * No target-based exit; position is closed by stop-loss or EXIT_EMA signal.

- Indicators:
    * VWAP & ENTRY_FAST_EMA – only for entry.
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

# EMAs dedicated for ENTRY signal (default 5 and 20 but configurable)
ENTRY_FAST_EMA = 9  # e.g., EMA 5  (fast)

MIN_RANGE_PCT = 0.0  # tiny-candle filter (0.001 = 0.1%), 0.0 = off
EMA_BUFFER = 0.0  # optional extra buffer above/below EMAs
REQUIRE_GREEN_SIGNAL = True

# Spot indices to track for signals
SYMBOLS = [
    "NSE:NIFTY50-INDEX",
    "NSE:NIFTYBANK-INDEX",
    "NSE:FINNIFTY-INDEX"
]

# Strike selection: 0=ATM, 1=1st OTM, -1=1st ITM
STRIKE_DISTANCE = 0

# Position sizing for options (in lots)
FIXED_LOTS = 1

LOG_FILE = "trade_log.csv"
STATE_DUMP = "symbol_states.json"
PARTIAL_CANDLES_FILE = "partial_candles.json"

# Default product type: "INTRADAY", "CNC", "MARGIN", "CO", "BO"
PRODUCT_TYPE = "INTRADAY"

SL_MODE = "signal_low"  # "signal_low" or "swing_low"
SWING_LOOKBACK = 5  # used for swing-low

MAX_CONCURRENT_POS = 3
DAILY_MAX_LOSS = 50000.0
TRADING_ENABLED = True
MAX_EXIT_RETRIES = 3
EXIT_RETRY_COOLDOWN_SECONDS = 10

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

# Position sizing mode globals are now handled by FIXED_LOTS at the top
# Re-auth guard to avoid infinite recursion
REAUTH_ATTEMPTS = 0
MAX_REAUTH_ATTEMPTS = 3

# ---------- small print filter to avoid noisy console spam ----------
_real_print = print
ALLOWED_SUBSTRINGS = (
    "ENTRY SIGNAL", "[signal:", "EXIT SIGNAL", "[exit:", "[CANDLE]", "[order]", "[auth]", "[ws]",
    "[blocked-entry]", "[entry-debug]", "[exit-debug]", "TARGET EXIT", "STOP-LOSS", "[ENTRY CONFIRMED]"
)


def print(*args, **kwargs):
    try:
        s = " ".join(str(x) for x in args)
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


# ---------------------------- OPTION HELPERS ----------------------------
def get_strike_increment(symbol: str) -> int:
    s = symbol.upper()
    if "BANKNIFTY" in s:
        return 100
    if "FINNIFTY" in s:
        return 50
    if "NIFTY" in s:
        return 50
    return 50


def get_atm_strike(ltp: float, symbol: str) -> int:
    inc = get_strike_increment(symbol)
    return int(round(ltp / inc)) * inc


def get_option_details(symbol: str, ltp: float) -> Optional[dict]:
    if FYERS is None:
        return None
    try:
        chain_resp = FYERS.optionchain({"symbol": symbol})
        data = chain_resp.get("data", {})
        chain = data.get("optionChain") or data.get("optionsChain", [])
        if not chain:
            _real_print(f"[{symbol}] Option chain is empty.")
            return None

        # Handle both 'expiry_date' and 'expiry' keys
        get_expiry = lambda c: c.get("expiry_date") or c.get("expiry")

        expiries = sorted(list(set(get_expiry(c) for c in chain if get_expiry(c))))
        if not expiries:
            _real_print(f"[{symbol}] No expiry dates found in option chain.")
            return None
        nearest_expiry = expiries[0]

        atm = get_atm_strike(ltp, symbol)
        strike_inc = get_strike_increment(symbol)
        target_strike = atm + (STRIKE_DISTANCE * strike_inc)

        ce_chain = [c for c in chain if
                    get_expiry(c) == nearest_expiry and c.get("option_type") == "CE"]
        if not ce_chain:
            _real_print(f"[{symbol}] No CE options for expiry {nearest_expiry}")
            return None

        closest_ce = min(ce_chain, key=lambda c: abs(c.get("strike_price", float('inf')) - target_strike))
        lot_size = int(closest_ce.get("lot_size", 1))

        return {
            "symbol": closest_ce.get("symbol"),
            "lot_size": lot_size,
            "strike": closest_ce.get("strike_price"),
            "expiry": nearest_expiry
        }
    except Exception as e:
        _real_print(f"[{symbol}] Failed to get option details: {e}")
        return None


# ---------------------------- STATE OBJECTS ----------------------------
class SymbolState:
    def __init__(self, symbol: str):
        self.symbol = symbol  # underlying index symbol
        self.data = pd.DataFrame()
        self.status = "watch"
        self.signal_candle = None
        self.signal_close_ts = None
        # -- position details --
        self.entry_price = 0.0  # underlying entry price
        self.stop_price = 0.0  # underlying stop price
        self.option_symbol: Optional[str] = None
        self.option_entry_price: float = 0.0
        self.qty: int = 0  # in contracts, not lots
        self.lot_size: int = 1
        # -- exit --
        self.exit_signal_candle = None
        self.exit_pending = False
        self.exit_try_count = 0
        self.last_failed_exit_ts = None
        # -- internal --
        self.last_candle_ts = None
        self.just_entered = False

    def __repr__(self):
        return f"<State {self.symbol} {self.status} opt={self.option_symbol} qty={self.qty} sl={self.stop_price}>"


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


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "volume" not in df.columns:
        return df
    df = df.copy()
    df["ema_exit"] = ema(df["close"], EXIT_EMA)
    df["ema_fast_entry"] = ema(df["close"], ENTRY_FAST_EMA)

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
               "low": candle["low"], "close": candle["close"], "volume": candle["volume"]}
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
            except Exception:
                pass

    def process_tick(self, tick: dict):
        try:
            symbol = tick.get("symbol")
            if not symbol:
                return
            ltp = tick.get("ltp") or tick.get("last_price") or tick.get("last_traded_price")
            if ltp is None:
                return
            ltp = float(ltp)
            vtt = int(tick.get("vtt", 0))
            ts = self._parse_ts(tick.get("timestamp"))
            candle_start = self._floor_ts(ts)
            with self.lock:
                p = self.partial.get(symbol)
                if p is None:
                    new_p = {"ts": candle_start, "open": ltp, "high": ltp,
                             "low": ltp, "close": ltp, "ticks": 1,
                             "start_vtt": vtt, "end_vtt": vtt}
                    self.partial[symbol] = new_p
                    self._persist_partial()
                    return
                if candle_start == p["ts"]:
                    p["high"] = max(p["high"], ltp)
                    p["low"] = min(p["low"], ltp)
                    p["close"] = ltp
                    p["ticks"] = p.get("ticks", 0) + 1
                    p["end_vtt"] = vtt
                    self._persist_partial()
                    return
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
                self._append_history(symbol, candle_out)
                new_partial = {"ts": candle_start, "open": ltp, "high": ltp,
                               "low": ltp, "close": ltp, "ticks": 1,
                               "start_vtt": vtt, "end_vtt": vtt}
                self.partial[symbol] = new_partial
                self._persist_partial()
        except Exception:
            return

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


def place_market_order(symbol: str, qty: int, side: int) -> dict:
    side_str = "BUY" if side == 1 else "SELL"
    data = {
        "symbol": symbol,
        "qty": qty,
        "type": 2,
        "side": side,
        "productType": PRODUCT_TYPE,
        "limitPrice": 0,
        "stopPrice": 0,
        "validity": "DAY",
        "disclosedQty": 0,
        "offlineOrder": False,
    }
    _real_print(f"[order] Placing market {side_str} for {qty} of {symbol}")
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


def on_completed_candle(symbol: str, candle: dict):
    st = SYMBOL_STATES.get(symbol)
    if st is None:
        return
    try:
        row = {"open": candle["open"], "high": candle["high"],
               "low": candle["low"], "close": candle["close"], "volume": candle["volume"]}
        idx = pd.to_datetime(candle["ts"])
        df = st.data
        if df is None or df.empty:
            df = pd.DataFrame([row], index=[idx])
        else:
            df = pd.concat([df, pd.DataFrame([row], index=[idx])])
            df = df.tail(2000)
        df.index.name = "datetime"
        st.data = compute_indicators(df)
        st.last_candle_ts = idx
    except Exception:
        return
    evaluate_on_new_candle(st)


# ---------------------------- LTP / Tick handler ----------------------------
def on_tick(tick: dict):
    symbol = tick.get("symbol")
    ltp = float(tick.get("ltp", 0.0))
    vtt = int(tick.get("vtt", 0))
    ts = tick.get("timestamp") or tick.get("time")

    # Ensure timestamp is a timezone-naive datetime object
    if ts is None:
        ts = dt.now(IST).replace(tzinfo=None)
    elif isinstance(ts, (int, float)):
        ts = dt.fromtimestamp(ts, IST).replace(tzinfo=None)
    elif isinstance(ts, str):
        ts = pd.to_datetime(ts).to_pydatetime().replace(tzinfo=None)
    if getattr(ts, 'tzinfo', None) is not None:
        ts = ts.astimezone(IST).replace(tzinfo=None)

    # Update candles
    if CANDLE_MANAGER:
        CANDLE_MANAGER.process_tick({"symbol": symbol, "ltp": ltp, "vtt": vtt, "timestamp": ts})

    state = SYMBOL_STATES.get(symbol)
    if state is None:
        return

    # --- ENTRY LOGIC (Strict next-candle) ---
    if state.status == "entry_pending" and state.signal_candle is not None:
        sig_ts = pd.to_datetime(state.signal_candle["ts"])
        next_candle_ts = sig_ts + timedelta(minutes=TIMEFRAME_MIN)
        current_candle_ts = ts.replace(second=0, microsecond=0, minute=(ts.minute // TIMEFRAME_MIN) * TIMEFRAME_MIN)

        if current_candle_ts > next_candle_ts:
            _real_print(f"[{symbol}] ENTRY SIGNAL EXPIRED (next candle passed).")
            state.status = "watch"
            state.signal_candle = None
        elif current_candle_ts == next_candle_ts and ltp > state.signal_candle["high"]:
            opt = get_option_details(symbol, ltp)
            if not opt:
                _real_print(f"[{symbol}] Could not get option details. Aborting entry.")
                state.status = "watch"
                state.signal_candle = None
                return

            qty = FIXED_LOTS * opt["lot_size"]
            resp = place_market_order(opt["symbol"], qty, side=1)  # side=1 for BUY
            if resp.get("s") == "ok":
                state.status = "position"
                state.entry_price = ltp
                state.option_symbol = opt["symbol"]
                state.qty = qty
                state.lot_size = opt["lot_size"]
                state.just_entered = True

                if SL_MODE == "signal_low":
                    state.stop_price = float(state.signal_candle["low"])
                else:
                    swing = compute_swing_low_for_signal(state, SWING_LOOKBACK)
                    state.stop_price = float(state.signal_candle["low"]) if pd.isna(swing) else swing

                _real_print(f"[ENTRY CONFIRMED] {symbol} @ {ltp:.2f} | Bought {qty} of {opt['symbol']} | SL={state.stop_price:.2f}")
            else:
                _real_print(f"[{symbol}] ENTRY FAILED: {resp.get('message')}")
                state.status = "cooldown"
            state.signal_candle = None

    # --- EXIT LOGIC (Stop-loss or EMA signal) ---
    if state.status == "position" and not state.just_entered:
        # 1. Stop-loss check
        if state.stop_price > 0 and ltp <= state.stop_price:
            _real_print(f"[{symbol}] STOP-LOSS HIT at {ltp:.2f}")
            resp = place_market_order(state.option_symbol, state.qty, side=-1)
            if resp.get("s") == "ok":
                state.status = "cooldown"
            else:
                _real_print(f"[{symbol}] STOP-LOSS SELL FAILED: {resp.get('message')}")
            return

        # 2. EMA-based exit check (strict next-candle)
        if state.exit_pending and state.exit_signal_candle:
            exit_sig_ts = pd.to_datetime(state.exit_signal_candle["ts"])
            next_candle_ts = exit_sig_ts + timedelta(minutes=TIMEFRAME_MIN)
            current_candle_ts = ts.replace(second=0, microsecond=0, minute=(ts.minute // TIMEFRAME_MIN) * TIMEFRAME_MIN)

            if current_candle_ts > next_candle_ts:
                _real_print(f"[{symbol}] EXIT SIGNAL EXPIRED.")
                state.exit_pending = False
                state.exit_signal_candle = None
            elif current_candle_ts == next_candle_ts and ltp < state.exit_signal_candle["low"]:
                _real_print(f"[{symbol}] EXIT TRIGGERED at {ltp:.2f}")
                resp = place_market_order(state.option_symbol, state.qty, side=-1)
                if resp.get("s") == "ok":
                    state.status = "cooldown"
                else:
                    _real_print(f"[{symbol}] EXIT FAILED: {resp.get('message')}")
                state.exit_pending = False
                state.exit_signal_candle = None

    if state.just_entered:
        state.just_entered = False

        # ---------------------------- STRATEGY EVALUATOR ----------------------------


def evaluate_on_new_candle(st: SymbolState):
    df = st.data
    if df is None or df.empty:
        return

    last_ts = st.last_candle_ts
    if last_ts is None:
        return

    curr = df.loc[last_ts]
    curr_open = float(curr["open"])
    curr_low = float(curr["low"])
    curr_high = float(curr["high"])
    curr_close = float(curr["close"])

    ema_fast = float(curr.get("ema_fast_entry", float("nan")))
    vwap = float(curr.get("vwap", float("nan")))
    ema_exit = float(curr.get("ema_exit", float("nan")))

    # ENTRY SIGNAL (VWAP BODY CROSS)
    if st.status == "watch":
        open_below_vwap = curr_open <= vwap
        closed_above_vwap = curr_close > vwap
        fast_ema_above_vwap = ema_fast > vwap
        green_ok = (not REQUIRE_GREEN_SIGNAL) or (curr_close > curr_open)
        ok_signal = bool(curr.get("ok_signal", True))

        if open_below_vwap and closed_above_vwap and fast_ema_above_vwap and green_ok and ok_signal:
            st.signal_candle = {
                "ts": curr.name,
                "open": curr_open,
                "high": curr_high,
                "low": curr_low,
                "close": curr_close,
            }
            st.status = "entry_pending"
            _real_print(f"****** [{st.symbol}] ENTRY SIGNAL (VWAP Body Cross & EMA > VWAP) ******")
            _real_print(
                f"[signal:{st.symbol}] signal_high={curr_high:.2f} signal_low={curr_low:.2f} | "
                f"DEBUG: open={curr_open:.2f} close={curr_close:.2f} vwap={vwap:.2f} ema_fast={ema_fast:.2f} | "
                f"waiting for NEXT CANDLE to attempt breakout entry"
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
            st.exit_pending = True
            st.exit_try_count = 0
            _real_print(
                f"****** [{st.symbol}] EXIT SIGNAL (RED candle crossed & closed below EXIT EMA) ******"
            )
            _real_print(
                f"[exit:{st.symbol}] exit_low={curr_low:.2f} | "
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
            if symbol and ltp is not None:
                on_tick(m)
    except Exception:
        pass


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
        df = df.set_index("ts")[["open", "high", "low", "close", "volume"]]
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

        # ---------------------------- STATE SAVE/LOAD ----------------------------


def _serialize_state():
    out = {}
    for sym, st in SYMBOL_STATES.items():
        out[sym] = {
            "status": st.status,
            "entry_price": st.entry_price,
            "stop_price": st.stop_price,
            "option_symbol": st.option_symbol,
            "option_entry_price": st.option_entry_price,
            "qty": st.qty,
            "lot_size": st.lot_size,
            "last_candle_ts": st.last_candle_ts.isoformat() if st.last_candle_ts else None,
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
                continue
            st = SYMBOL_STATES[sym]
            st.status = info.get("status", "watch")
            st.entry_price = info.get("entry_price", 0.0)
            st.stop_price = info.get("stop_price", 0.0)
            st.option_symbol = info.get("option_symbol")
            st.option_entry_price = info.get("option_entry_price", 0.0)
            st.qty = info.get("qty", 0)
            st.lot_size = info.get("lot_size", 1)
            # Don't load last_candle_ts, let it warm up
    except Exception as e:
        _real_print("[state] Failed to load state:", e)


atexit.register(lambda: save_state_to_disk())


# ---------------------------- CLI & Main ----------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Fast-Slow EMA Strategy for Index Options (Fyers v3)")
    p.add_argument("--timeframe", "-t", type=int, default=TIMEFRAME_MIN)
    p.add_argument("--exit-ema", type=int, default=EXIT_EMA)
    p.add_argument("--entry-fast-ema", type=int, default=ENTRY_FAST_EMA)
    p.add_argument("--strike-dist", type=int, default=STRIKE_DISTANCE, help="Strike distance from ATM (0=ATM, 1=OTM, -1=ITM)")
    p.add_argument("--fixed-lots", type=int, default=FIXED_LOTS, help="Number of lots per trade")
    p.add_argument("--product-type", type=str, default=PRODUCT_TYPE, help="Order product type, e.g., 'INTRADAY', 'CNC'")
    p.add_argument("--sl-mode", type=str, default=SL_MODE, help="Stop-loss mode: 'signal_low' or 'swing_low'")
    p.add_argument("--test-table", action="store_true", help="Run in test mode without Fyers")
    p.set_defaults(require_green=True)
    return p.parse_args()


def print_startup(args):
    _real_print("[update] Timeframe =", TIMEFRAME_MIN, "m")
    _real_print("[update] Exit EMA =", EXIT_EMA)
    _real_print("[update] Entry fast EMA =", ENTRY_FAST_EMA)
    _real_print("[update] Strike Distance =", STRIKE_DISTANCE)
    _real_print("[update] Fixed Lots =", FIXED_LOTS)
    _real_print("[mode] Order mode set to", PRODUCT_TYPE)
    _real_print("[mode] SL_MODE =", SL_MODE)


def main():
    global TIMEFRAME_MIN, EXIT_EMA, ENTRY_FAST_EMA, STRIKE_DISTANCE, FIXED_LOTS, PRODUCT_TYPE, SL_MODE
    global CANDLE_MANAGER, FYERS, FYERS_SOCKET, ACCESS_TOKEN

    args = parse_args()
    file_settings = load_settings_file()

    def pick(name, cli_val, default_val):
        if cli_val is not None and cli_val != default_val:
            return cli_val
        return file_settings.get(name, default_val)

    TIMEFRAME_MIN = int(pick("timeframe", args.timeframe, TIMEFRAME_MIN))
    EXIT_EMA = int(pick("exit_ema", args.exit_ema, EXIT_EMA))
    ENTRY_FAST_EMA = int(pick("entry_fast_ema", args.entry_fast_ema, ENTRY_FAST_EMA))
    STRIKE_DISTANCE = int(pick("strike_dist", args.strike_dist, STRIKE_DISTANCE))
    FIXED_LOTS = int(pick("fixed_lots", args.fixed_lots, FIXED_LOTS))
    PRODUCT_TYPE = str(pick("product_type", args.product_type, PRODUCT_TYPE))
    SL_MODE = str(pick("sl_mode", args.sl_mode, SL_MODE)).lower()

    ALLOWED_PRODUCT = {"INTRADAY", "CNC", "MARGIN", "CO", "BO"}
    if PRODUCT_TYPE not in ALLOWED_PRODUCT:
        _real_print(f"[warn] Invalid PRODUCT_TYPE '{PRODUCT_TYPE}'. Falling back to 'INTRADAY'.")
        PRODUCT_TYPE = "INTRADAY"

    ALLOWED_SL = {"signal_low", "swing_low"}
    if SL_MODE not in ALLOWED_SL:
        _real_print(f"[warn] Invalid SL_MODE '{SL_MODE}'. Falling back to 'signal_low'.")
        SL_MODE = "signal_low"

    print_startup(args)

    CANDLE_MANAGER = CandleManager(TIMEFRAME_MIN, on_candle=on_completed_candle, tz=TIMEZONE)

    if args.test_table or fyersModel is None:
        FYERS = None
        _real_print("[mode] TEST TABLE mode (no fyers). Warmup synthetic data.")
        for sym in SYMBOLS:
            st = SYMBOL_STATES[sym]
            idx = pd.date_range(end=dt.now(), periods=200, freq=f"{TIMEFRAME_MIN}min")
            df = pd.DataFrame({
                "open": np.linspace(100, 110, len(idx)),
                "high": np.linspace(101, 111, len(idx)),
                "low": np.linspace(99, 109, len(idx)),
                "close": np.linspace(100, 110, len(idx)),
                "volume": np.random.randint(1000, 5000, len(idx)),
            }, index=idx)
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

        client_id = ACCESS_TOKEN.split(":")[0]
        FYERS = fyersModel.FyersModel(client_id=client_id, is_async=False, token=ACCESS_TOKEN, log_path="")

        _real_print("[auth] Fyers model initialized. Running warmup history fetch.")
        warmup_all_full(FYERS)

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
        _real_print(f"STRIKE_DISTANCE = {STRIKE_DISTANCE}")
        _real_print(f"FIXED_LOTS      = {FIXED_LOTS}")
        _real_print(f"PRODUCT_TYPE    = \"{PRODUCT_TYPE}\"")
        _real_print(f"SL_MODE         = \"{SL_MODE}\"")
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
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _real_print("\n[exit] Interrupted by user. Shutting down.")
    except Exception as e:
        _real_print(f"[fatal] Unexpected error: {e}")


if __name__ == "__main__":
    main()
