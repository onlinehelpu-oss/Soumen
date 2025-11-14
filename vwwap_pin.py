# -*- coding: utf-8 -*-
"""
Green-Hammer / Green-Pinbar NEXT-candle first-touch breakout (GREEN candle only)
- Strict tick-level breakout: enters only when price CROSSES ABOVE (signal_high + buffer)
- Signal candle must have opened BELOW VWAP and CLOSED ABOVE VWAP (intrabar cross + close above)
- Never enters exactly at signal high; requires > (high + buffer)
- Previous candle must be RED + tiny-candle filter
- Single-position mode: block new signals & entries while ANY position is open
- Configurable timeframe via TIMEFRAME_MIN or --tf
- CNC / INTRADAY selectable via --mode
- Live trading (no paper-mode)
- CNC entries attempt to place broker-side GTT/OCO; fallback to internal monitor if fails
- Allocation: rupee-based per-stock (CNC/CNC mode), lot-based for MCX futures (LOT_SIZE & LOT_COUNT)
"""
import os
import sys
import json
import time
import math
import hashlib
import datetime as dt
from urllib.parse import urlparse, parse_qs, quote
import threading
import argparse
import traceback
import re

import pandas as pd
import requests

from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws

# ===================== STRATEGY SETTINGS (defaults, CLI overrides allowed) =====================
TIMEFRAME_MIN = 15       # change to 5 / 15 / 30 / 60 etc., or override with --tf
r_multiplier = 1.0       # direct Risk:Reward multiple (target = entry + r_multiplier * risk)
order_mode = "CNC"       # "CNC" or "INTRADAY" (CLI --mode)
DEFAULT_QTY = 1
EPS = 1e-6

# ===================== ALLOCATION / LOTS =====================
# Default rupee allocation per equity symbol (if not present in ALLOC_MAP)
alloc_default = 3000.0
# Per-symbol rupee allocation override (keys are exact symbols as used in SYMBOLS)
ALLOC_MAP = {
    # "NSE:SBIN-EQ": 12000.0,
}
# LOT_SIZE (units per lot) for MCX base symbols (e.g., NATGASMINI -> 40). Use base symbol key "MCX:NATGASMINI"
LOT_SIZE = {
    # "MCX:NATGASMINI": 40,
}
# LOT_COUNT: how many lots to place for a given base symbol
LOT_COUNT = {
    # "MCX:NATGASMINI": 1,
}

# One-position-at-a-time control
ONE_POSITION_AT_A_TIME = True

# Tick setup (NSE equities typically 0.05)
TICK_SIZE = 0.05

def round_to_tick(x, tick=TICK_SIZE):
    """
    Rounds a number to the nearest tick size.
    """
    return round(round(x / tick) * tick, 2)

def ceil_to_tick(x, tick=TICK_SIZE):
    """
    Ceils a number to the nearest tick size.
    """
    k = math.floor(x / tick)
    if abs(x - k * tick) < 1e-12:
        return round(x, 2)
    return round((k + 1) * tick, 2)

# ===================== CONSTANTS & PATHS =====================
CONFIG_FILE = "fyers_login_details.json"
TOKENS_DIR = "AccessToken"
TOKENS_STORE = "tokens_store.json"
TODAY = dt.date.today()
TODAY_PATH = os.path.join(TOKENS_DIR, f"{TODAY}.json")
API_HOST = "https://api-t1.fyers.in"

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
    # Example MCX futures (adjust to current month contract as needed)

]

# ===================== TIME/ENTRY/EXIT RULES =====================
ENTRY_BUFFER = 0.05                # buffer above signal high for breakout

# Default (non-MCX) behaviour
ENTRY_CUTOFF = dt.time(15, 0)      # no new entries after 3:00 PM (non-MCX / INTRADAY)
EXIT_ALL_TIME = dt.time(15, 9)     # force-exit all open non-MCX INTRADAY positions at 3:09 PM

# MCX-specific times (if using MCX)
ENTRY_CUTOFF_MCX = dt.time(22, 0)   # allow MCX signals up to 10:00 PM (if market hours)
EXIT_ALL_TIME_MCX = dt.time(22, 50) # force-exit all open MCX INTRADAY positions at 10:50 PM

FORCE_CLOSED_ALL = False
FORCE_CLOSED_ALL_MCX = False

# ===================== SMALL CANDLE GUARDS =====================
MIN_RANGE_PCT = 0.0015   # ignore if (H-L)/Close < 0.15% (tune per product)
MIN_BODY_TICKS = 0       # optional minimum body size; 0 disables

# ===================== IO HELPERS =====================
def _read_json(path, default=None):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception:
        return default

def _write_json(path, data):
    try:
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"⚠️ Could not write JSON {path}: {e}")

# ===================== LOGIN & TOKEN MGMT =====================
def load_creds():
    """
    Loads credentials from the config file.
    """
    creds = _read_json(CONFIG_FILE)
    if not creds:
        raise SystemExit("❌ Missing 'fyers_login_details.json'. Create it with {api_key, api_secret, redirect_url}.")
    for k in ("api_key", "api_secret", "redirect_url"):
        if k not in creds or not creds[k]:
            raise SystemExit(f"❌ '{k}' missing in {CONFIG_FILE}.")
    return creds

def appid_hash(app_id: str, secret_id: str) -> str:
    """
    Creates an app ID hash for Fyers API authentication.
    """
    return hashlib.sha256(f"{app_id}:{secret_id}".encode()).hexdigest()

def compose_access_token_string(app_id: str, access_token: str) -> str:
    """
    Composes the access token string for Fyers API.
    """
    if access_token.startswith(f"{app_id}:"):
        return access_token
    return f"{app_id}:{access_token}"

def build_auth_url(app_id: str, redirect_uri: str, state: str = "sample_state") -> str:
    """
    Builds the authentication URL for Fyers API.
    """
    base = f"{API_HOST}/api/v3/generate-authcode"
    params = (
        f"client_id={quote(app_id)}"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        f"&response_type=code"
        f"&state={quote(state)}"
        f"&scope=openid"
        f"&nonce={int(time.time())}"
    )
    return f"{base}?{params}"

def extract_code(user_input: str) -> str:
    """
    Extracts the authorization code from a URL.
    """
    if user_input.startswith("http://") or user_input.startswith("https://"):
        q = parse_qs(urlparse(user_input).query)
        code = q.get("code", [None])[0]
        if not code:
            raise ValueError("No 'code' param found in the provided URL.")
        return code
    return user_input

def post_json(url: str, payload: dict, max_retries: int = 5, timeout: int = 20):
    """
    Makes a POST request with JSON payload and retries on failure.
    """
    headers = {"Content-Type": "application/json"}
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
            try:
                data = r.json()
            except ValueError:
                data = None

            if r.status_code == 503:
                sleep_s = min(2 ** attempt, 30)
                print(f"[{attempt}/{max_retries}] 503 from server. Retrying in {sleep_s}s...")
                time.sleep(sleep_s)
                continue

            if r.status_code >= 400:
                if isinstance(data, dict) and data.get("s") == "error":
                    raise RuntimeError(f"Fyers error {data.get('code')}: {data.get('message')}")
                raise RuntimeError(f"Fyers returned HTTP {r.status_code}: {r.text.strip()[:200]}")

            if isinstance(data, dict) and data.get("s") == "error":
                raise RuntimeError(f"Fyers error {data.get('code')}: {data.get('message')}")

            return data

        except Exception as e:
            if attempt == max_retries:
                raise
            sleep_s = min(2 ** attempt, 30)
            print(f"[{attempt}/{max_retries}] Retrying due to: {e}. Next in {sleep_s}s...")
            time.sleep(sleep_s)
    return None

def validate_authcode(app_id: str, secret_id: str, auth_code: str):
    """
    Validates the authorization code with Fyers API.
    """
    url = f"{API_HOST}/api/v3/validate-authcode"
    payload = {"grant_type": "authorization_code","appIdHash": appid_hash(app_id, secret_id),"code": auth_code}
    return post_json(url, payload)

def validate_refresh_token(app_id: str, secret_id: str, refresh_token: str):
    """
    Validates the refresh token with Fyers API.
    """
    url = f"{API_HOST}/api/v3/validate-refresh-token"
    payload = {"grant_type": "refresh_token","appIdHash": appid_hash(app_id, secret_id),"refresh_token": refresh_token}
    return post_json(url, payload)

def save_access_token_for_today(app_id: str, access_token: str):
    """
    Saves the access token for the current day.
    """
    token_str = compose_access_token_string(app_id, access_token)
    _write_json(TODAY_PATH, token_str)
    return token_str

def ensure_access_token():
    """
    Ensures that a valid access token is available.
    """
    creds = load_creds()
    app_id = creds["api_key"]
    secret_id = creds["api_secret"]
    redirect_uri = creds["redirect_url"]

    tok_today = _read_json(TODAY_PATH)
    if isinstance(tok_today, str) and tok_today:
        token_str = compose_access_token_string(app_id, tok_today.split(":")[-1])
        if token_str != tok_today:
            _write_json(TODAY_PATH, token_str)
        print("🔑 Using today's cached access token.")
        return app_id, token_str, token_str.split(":", 1)[-1]

    store = _read_json(TOKENS_STORE, {}) or {}
    refresh_token = store.get("refresh_token")
    if refresh_token:
        try:
            print("🔄 Attempting refresh-token login …")
            r = validate_refresh_token(app_id, secret_id, refresh_token)
            access_token = r.get("access_token")
            new_refresh = r.get("refresh_token") or refresh_token
            if not access_token:
                raise RuntimeError(f"Unexpected refresh response: {r}")
            _write_json(TOKENS_STORE, {"refresh_token": new_refresh})
            token_str = save_access_token_for_today(app_id, access_token)
            print("✅ Refresh successful.")
            return app_id, token_str, access_token
        except Exception as e:
            print(f"⚠️ Refresh failed: {e}")
            try:
                if os.path.exists(TOKENS_STORE):
                    _write_json(TOKENS_STORE, {})
                print("ℹ️ Cleared stored refresh token — falling back to manual auth.")
            except Exception:
                pass

    auth_url = build_auth_url(app_id, redirect_uri)
    print("\n👉 Open this login URL in your browser, complete login, and copy the FULL redirect URL:")
    print(auth_url)
    user_val = input("\nPaste the FULL redirect URL (or just the code value): ").strip()
    auth_code = extract_code(user_val)

    r = validate_authcode(app_id, secret_id, auth_code)
    access_token = r.get("access_token")
    refresh_token = r.get("refresh_token")
    if not access_token:
        raise SystemExit(f"❌ Unexpected token response: {r}")

    token_str = save_access_token_for_today(app_id, access_token)
    if refresh_token:
        _write_json(TOKENS_STORE, {"refresh_token": refresh_token})
    print("✅ Manual auth successful — tokens saved.")
    return app_id, token_str, access_token

# ===================== HISTORICAL CANDLES & VWAP HELPERS =====================
def timeframe_to_resolution_token(minutes: int) -> str:
    """
    Converts a timeframe in minutes to a Fyers API resolution token.
    """
    if minutes == 1440:
        return "D"
    if minutes == 10080:
        return "W"
    if minutes == 43200:
        return "M"
    return str(int(minutes))

def fetch_historical_candles(fy: fyersModel.FyersModel, sym: str, timeframe_minutes: int, from_date: dt.date, to_date: dt.date):
    """
    Fetches historical candle data from Fyers API.
    """
    payload = {
        "symbol": sym,
        "resolution": timeframe_to_resolution_token(timeframe_minutes),
        "date_format": "1",
        "range_from": from_date.strftime("%Y-%m-%d"),
        "range_to": to_date.strftime("%Y-%m-%d"),
        "cont_flag": "1",
    }
    resp = fy.history(payload)
    if resp.get("s") != "ok":
        raise RuntimeError(f"Fyers history API error: {resp.get('message')}")
    candles = resp.get("candles", [])
    if not candles:
        return pd.DataFrame(columns=["timestamp","Open","High","Low","Close","Volume"]).set_index("timestamp")
    df = pd.DataFrame(candles, columns=["timestamp","Open","High","Low","Close","Volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s").dt.tz_localize("UTC").dt.tz_convert("Asia/Kolkata")
    df.set_index("timestamp", inplace=True)
    return df

def add_vwap(df: pd.DataFrame, timeframe_minutes: int) -> pd.DataFrame:
    """
    Calculates and adds the Volume Weighted Average Price (VWAP) to a DataFrame.
    """
    if df.empty:
        df["VWAP"] = pd.Series(dtype=float)
        return df
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    vol = df["Volume"].fillna(0)
    if timeframe_minutes < 1440:
        day_key = df.index.tz_convert("Asia/Kolkata").date
        pv = (tp * vol).groupby(day_key).cumsum()
        vv = vol.groupby(day_key).cumsum().replace(0, float("nan"))
        df["VWAP"] = pv / vv
    else:
        pv = (tp * vol).cumsum()
        vv = vol.cumsum().replace(0, float("nan"))
        df["VWAP"] = pv / vv
    return df

# ===================== CANDLE DETECTOR =====================
def is_bullish_hammer_candle(o, h, l, c, prev_o, prev_c, min_range_pct=0.0015):
    """
    GREEN hammer/pin-bar with:
      - Previous candle RED
      - Range filter to avoid tiny bars
      - Close > Open (green)
      - (h - c) < (c - o)  and  (o - l) > (c - o)
    """
    if c == 0:
        return False
    rng = h - l
    if rng <= 0:
        return False
    if (rng / max(c, 1e-9)) < min_range_pct:
        return False
    if prev_c >= prev_o:  # prev must be red
        return False
    if not (c > o):       # green only
        return False

    upper_shorter_than_body = (h - c) < (c - o)
    lower_longer_than_body  = (o - l) > (c - o)
    return upper_shorter_than_body and lower_longer_than_body

def flag_bullish_hammer(df: pd.DataFrame, min_range_pct=0.0015):
    """
    Flags bullish hammer candles in a DataFrame.
    """
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    prev_o, prev_c = o.shift(1), c.shift(1)
    rng = h - l
    cond_range = (rng / c) >= min_range_pct
    cond_prev_red = prev_c < prev_o
    cond_green = c > o
    cond_upper = (h - c) < (c - o)
    cond_lower = (o - l) > (c - o)
    df["BullishHammer"] = cond_range & cond_prev_red & cond_green & cond_upper & cond_lower
    return df

# ===================== ORDER HELPERS, GTT & PERSISTENCE =====================
def is_future_symbol(sym: str) -> bool:
    """
    Checks if a symbol is a futures contract.
    """
    return sym.startswith("MCX:") or "FUT" in sym.upper()

def base_symbol_for_lot(sym: str) -> str:
    """
    Extracts the base symbol for lot size calculation.
    """
    s = sym.split(":")[-1]
    m = re.match(r"([A-Z]+[A-Z0-9]*?)(?:\d.*|$)", s)
    if m:
        return "MCX:" + m.group(1)
    return sym

def calculate_qty_for_allocation(sym: str, entry_price: float) -> int:
    """
    Calculates the quantity for a given symbol and entry price based on allocation rules.
    Option A behaviour (preferred):
      - NSE equities (ending with -EQ): rupee-based qty = floor(allocation / entry_price)
      - MCX futures: pure lot-based qty = LOT_SIZE[base] * LOT_COUNT[base] (default 1 lot)
      - fallback: DEFAULT_QTY
    """
    # equities: rupee-based
    if sym.endswith("-EQ"):
        alloc = float(ALLOC_MAP.get(sym, alloc_default))
        qty = int(math.floor(alloc / max(entry_price, EPS)))
        return max(qty, 0)

    # futures (MCX): pure lot logic (IGNORE ALLOCATION)
    if is_future_symbol(sym):
        base = base_symbol_for_lot(sym)
        # units per lot (e.g., 40 for NATGASMINI). default 1 unit-per-lot if missing.
        units_per_lot = int(LOT_SIZE.get(base, 1))
        lots_to_place = int(LOT_COUNT.get(base, 1))
        if units_per_lot <= 0:
            units_per_lot = 1
        if lots_to_place <= 0:
            lots_to_place = 1
        qty = int(units_per_lot * lots_to_place)
        return max(qty, 0)

    # fallback
    return int(DEFAULT_QTY)

def extract_order_id(resp: dict):
    """
    Extracts the order ID from a Fyers API response.
    """
    if not isinstance(resp, dict):
        return None
    for k in ("id", "order_id", "orderId", "data", "orderIdStr"):
        if k in resp and not isinstance(resp[k], dict):
            return resp[k]
    if "data" in resp and isinstance(resp["data"], dict):
        for k in ("id", "order_id", "orderId"):
            if k in resp["data"]:
                return resp["data"][k]
    return None

def place_order(fy: fyersModel.FyersModel, sym: str, side: int, qty: int, tag: str, product_type: str):
    """
    Places a market order with Fyers API.
    """
    payload = {
        "symbol": sym,
        "qty": int(qty),
        "type": 2,            # market
        "side": int(side),    # 1=buy, -1=sell
        "productType": product_type,
        "validity": "DAY",
        "orderTag": tag[:15] if tag else ""
    }
    try:
        resp = fy.place_order(payload)
        order_id = extract_order_id(resp)
        print(f"[{dt.datetime.now():%H:%M:%S}] 📌 {tag} resp={resp} order_id={order_id}")
        return resp if isinstance(resp, dict) else {}, order_id
    except Exception as e:
        print(f"[{dt.datetime.now():%H:%M:%S}] 🚨 Order error {sym} {tag}: {e}")
        return {"s": "error", "message": str(e)}, None

def exit_long_by_sell_market(fy: fyersModel.FyersModel, sym: str, qty: int, product_type: str):
    """
    Exits a long position by placing a sell market order.
    """
    resp, oid = place_order(fy, sym, side=-1, qty=qty, tag="ExitLong", product_type=product_type)
    return resp, oid

def place_gtt_order(raw_access_token: str, sym: str, qty: int, sl: float, tgt: float, product_type: str):
    """
    Places a Good Till Triggered (GTT) order with Fyers API.
    Best-effort GTT/OCO creation via Fyers REST.
    - raw_access_token: OAuth access token (not app_id:token)
    - The exact payload/endpoint names may need adjusting to match Fyers' live API docs.
    - On failure, return (False, response_or_exception)
    """
    url = f"{API_HOST}/api/v3/gtt"  # best-effort endpoint; adapt to official docs if different
    headers = {"Authorization": f"Bearer {raw_access_token}", "Content-Type": "application/json"}
    # Example GTT payload (approximate). Please update to match Fyers docs if fields differ.
    payload = {
        "symbol": sym,
        "qty": int(qty),
        "productType": product_type,
        "gtt_type": "OCO",     # attempt OCO style; docs may use another key
        "orders": [
            {
                "trigger_price": float(sl),
                "order_type": "SL-M",   # stop loss market
                "limit_price": None,
                "side": -1,             # sell
                "productType": product_type,
            },
            {
                "trigger_price": float(tgt),
                "order_type": "LMT",    # target as limit sell
                "limit_price": float(tgt),
                "side": -1,
                "productType": product_type,
            }
        ],
        "validity": "DAY"
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        try:
            data = r.json()
        except Exception:
            data = {"raw_text": r.text}
        if r.status_code >= 400:
            return False, {"status_code": r.status_code, "body": data}
        return True, data
    except Exception as e:
        return False, str(e)

# ===================== TRADE LOG & TRACKING =====================
TRADE_STORE = "open_trades.json"
TRADE_LOG = "trade_log.csv"

active_trades = {}  # sym -> dict(entry, sl, tgt, qty, status, product, order_id, gtt_id)

def persist_active_trades():
    """
    Persists active trades to a JSON file.
    """
    try:
        _write_json(TRADE_STORE, active_trades)
    except Exception as e:
        print(f"⚠️ Could not persist active trades: {e}")

def load_persisted_trades():
    """
    Loads persisted trades from a JSON file.
    """
    data = _read_json(TRADE_STORE, default={}) or {}
    for sym, rec in data.items():
        try:
            active_trades[sym] = rec
        except Exception:
            pass
    if active_trades:
        print(f"[{dt.datetime.now():%H:%M:%S}] Loaded {len(active_trades)} persisted open trade(s).")

def save_trade(sym, entry, sl, tgt, qty, product_type, order_id=None, gtt_id=None):
    """
    Saves a trade to the trade log and active trades.
    """
    row = {
        "Datetime": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Symbol": sym,
        "Entry Price": float(entry),
        "Stop Loss": float(sl),
        "Target": float(tgt),
        "Qty": int(qty),
        "Product": product_type,
        "OrderID": order_id,
        "GTT_ID": gtt_id
    }
    try:
        pd.DataFrame([row]).to_csv(
            TRADE_LOG,
            mode='a',
            header=not os.path.exists(TRADE_LOG),
            index=False
        )
    except Exception as e:
        print(f"⚠️ Could not write trade_log: {e}")
    active_trades[sym] = {"entry": entry, "sl": sl, "tgt": tgt, "qty": qty, "status": "open", "product": product_type, "order_id": order_id, "gtt_id": gtt_id}
    persist_active_trades()

# ===================== CANDLE BUILD STATE & LTP CACHE =====================
bars = {}
processed_candles = set()
trigger = {}

ltp_cache = {}       # symbol -> (ltp, ts)
prev_ltp_cache = {}  # symbol -> previous ltp (for strict cross)
_last_quote_error = {}
ERROR_THROTTLE_SECS = 10

def candle_start(t: dt.datetime) -> dt.datetime:
    """
    Calculates the start time of a candle.
    """
    return t.replace(second=0, microsecond=0) - dt.timedelta(minutes=t.minute % TIMEFRAME_MIN)

# ===================== SAFE QUOTES (cache-first, REST fallback) =====================
def get_ltp(fy, sym, cache_ttl=10, max_retries=3):
    """
    Gets the last traded price (LTP) of a symbol with caching and retries.
    """
    now = time.time()
    cached = ltp_cache.get(sym)
    if cached:
        ltp_val, ts = cached
        if (now - ts) <= cache_ttl:
            return float(ltp_val)

    base_sleep = 1.0
    for attempt in range(1, max_retries + 1):
        try:
            q = fy.quotes({"symbols": sym})
            if q.get("s") != "ok" or not q.get("d"):
                last = _last_quote_error.get(sym, 0)
                if now - last > ERROR_THROTTLE_SECS:
                    print(f"⚠️ Quote fetch failed {sym}: {q}")
                    _last_quote_error[sym] = now
                if isinstance(q, dict) and q.get("code") == 429:
                    time.sleep(min(base_sleep * (2 ** attempt), 10))
                    continue
                time.sleep(base_sleep * attempt)
                continue

            v = q["d"][0].get("v", {})
            ltp = v.get("lp") or v.get("last_price")
            if ltp is None:
                last = _last_quote_error.get(sym, 0)
                if now - last > ERROR_THROTTLE_SECS:
                    print(f"⚠️ Quote fetch missing price {sym}: {q}")
                    _last_quote_error[sym] = now
                time.sleep(base_sleep * attempt)
                continue

            ltp_cache[sym] = (float(ltp), time.time())
            return float(ltp)

        except Exception as e:
            last = _last_quote_error.get(sym, 0)
            if now - last > ERROR_THROTTLE_SECS:
                print(f"⚠️ Quote fetch exception {sym}: {e}")
                _last_quote_error[sym] = now
            sleep_s = min(base_sleep * (2 ** (attempt - 1)), 10)
            time.sleep(sleep_s)
            continue

    cached = ltp_cache.get(sym)
    if cached:
        return float(cached[0])
    return None

# ===================== WEBSOCKET HANDLER (LIVE LONG logic) =====================
def make_onmsg(fy: fyersModel.FyersModel, order_mode_local: str, raw_access_token: str):
    """
    Creates the WebSocket on_message handler.
    """
    def onmsg(msg):
        if msg.get("type") != "sf":
            return

        try:
            sym = msg["symbol"]
            ltp = float(msg["ltp"])
            ts = int(msg.get("timestamp", time.time()))
        except Exception:
            return

        # track prev LTP for strict cross
        prev_ltp = ltp_cache.get(sym, (None, None))[0]
        if prev_ltp is not None:
            prev_ltp_cache[sym] = float(prev_ltp)

        # update websocket LTP cache
        ltp_cache[sym] = (ltp, time.time())

        tick_time = pd.to_datetime(ts, unit='s').tz_localize('UTC').tz_convert('Asia/Kolkata').to_pydatetime()
        cstart = candle_start(tick_time)
        key = (sym, cstart)

        # build/extend the current bar
        bar = bars.get(key)
        if not bar:
            bars[key] = bar = {"o": ltp, "h": ltp, "l": ltp, "c": ltp}
        else:
            bar["h"] = max(bar["h"], ltp)
            bar["l"] = min(bar["l"], ltp)
            bar["c"] = ltp

        # when candle completes (end of timeframe)
        if tick_time >= cstart + dt.timedelta(minutes=TIMEFRAME_MIN) - dt.timedelta(seconds=1):
            if key not in processed_candles:
                processed_candles.add(key)

                prev_cstart = cstart - dt.timedelta(minutes=TIMEFRAME_MIN)
                prev_bar = bars.get((sym, prev_cstart))

                # If single-position mode and something is open, skip forming a new signal
                if ONE_POSITION_AT_A_TIME and has_open_positions():
                    # quietly ignore new signals
                    return

                # IMPORTANT: ensure previous candle exists AND is strictly red before calling detector
                if prev_bar and prev_bar.get("c") is not None and prev_bar.get("o") is not None:
                    if prev_bar["c"] < prev_bar["o"]:
                        # apply detector (prev red + tiny-candle filter + green-only)
                        if is_bullish_hammer_candle(
                            bar["o"], bar["h"], bar["l"], bar["c"],
                            prev_bar["o"], prev_bar["c"],
                            min_range_pct=MIN_RANGE_PCT
                        ):
                            # --- VWAP check: require signal bar opened BELOW VWAP and closed ABOVE VWAP ---
                            try:
                                # fetch recent candles including this candle's timestamp day
                                from_day = (cstart - dt.timedelta(days=1)).date()
                                to_day = (cstart + dt.timedelta(days=0)).date()
                                hist = fetch_historical_candles(fy, sym, TIMEFRAME_MIN, from_date=from_day, to_date=to_day)
                                if not hist.empty:
                                    hist = add_vwap(hist, TIMEFRAME_MIN)
                                    loc_idx = hist.index.get_indexer([cstart], method='nearest', tolerance=dt.timedelta(seconds=2))
                                    valid_idx = loc_idx[loc_idx != -1]

                                    if len(valid_idx) > 0:
                                        vwap = float(hist.iloc[valid_idx[0]]["VWAP"])
                                        # REQUIRE intrabar cross: open < VWAP and close > VWAP
                                        cond_intrabar_cross_and_close = (bar["o"] < vwap) and (bar["c"] > vwap)
                                        if not cond_intrabar_cross_and_close:
                                            print(f"[{dt.datetime.now():%H:%M:%S}] ⚠️ SKIP {sym} — signal bar did NOT open below AND close above VWAP (vwap={vwap:.2f})")
                                        else:
                                            # Passed VWAP crossover test -> create trigger for next candle
                                            next_cstart = cstart + dt.timedelta(minutes=TIMEFRAME_MIN)
                                            trigger[sym] = {
                                                "low": bar["l"],      # stop loss
                                                "high": bar["h"],     # breakout level (to be crossed)
                                                "active_start": next_cstart,
                                                "triggered": False
                                            }
                                            print(f"[{dt.datetime.now():%H:%M:%S}] 🎯 GREEN-SIG {sym} (Prev RED) TF={TIMEFRAME_MIN}m → watch NEXT HIGH {bar['h']} (SL {bar['l']}) [VWAP OK]")
                                            print(f"    SIG BAR o={bar['o']:.2f} h={bar['h']:.2f} l={bar['l']:.2f} c={bar['c']:.2f} | PREV o={prev_bar['o']:.2f} c={prev_bar['c']:.2f} | VWAP={vwap:.2f}")
                                    else:
                                        # No matching hist row found — conservative choice: skip signal
                                        print(f"[{dt.datetime.now():%H:%M:%S}] ⚠️ SKIP {sym} — could not align historical candle for VWAP check.")
                                else:
                                    print(f"[{dt.datetime.now():%H:%M:%S}] ⚠️ SKIP {sym} — historical candles empty for VWAP check.")
                            except Exception as e:
                                print(f"[{dt.datetime.now():%H:%M:%S}] ⚠️ VWAP check error for {sym}: {e}\n{traceback.format_exc()}")
                    else:
                        # previous candle exists but is NOT red — explicitly skip
                        pass
                else:
                    # no previous bar available — skip signal
                    pass

        # check active trigger for symbol (NEXT candle only)
        t = trigger.get(sym)
        if not t:
            return

        # expire trigger if window passed
        if tick_time >= t["active_start"] + dt.timedelta(minutes=TIMEFRAME_MIN):
            trigger.pop(sym, None)
            return

        # only act in NEXT candle window
        if tick_time < t["active_start"] or t["triggered"]:
            return

        # Exposure guard: if any position is open, do NOT enter (and discard trigger)
        if ONE_POSITION_AT_A_TIME and has_open_positions():
            print(f"[{dt.datetime.now():%H:%M:%S}] 🚫 Skipping {sym} — position already open (single-position mode).")
            trigger.pop(sym, None)
            return

        # cutoff guard: use MCX-specific cutoff for MCX symbols, else default
        now_time = dt.datetime.now().time()
        if sym.startswith("MCX:"):
            cutoff_time = ENTRY_CUTOFF_MCX
        else:
            cutoff_time = ENTRY_CUTOFF

        if now_time >= cutoff_time and order_mode_local.upper() == "INTRADAY":
            # For INTRADAY mode, enforce cutoff
            print(f"[{dt.datetime.now():%H:%M:%S}] ⏰ Skipping NEW entry {sym} — cutoff passed ({cutoff_time})")
            trigger.pop(sym, None)
            return

        # strict cross: prev_ltp <= threshold AND current ltp > threshold
        threshold = round_to_tick(t["high"] + ENTRY_BUFFER)
        prev_for_cross = prev_ltp_cache.get(sym)
        if (prev_for_cross is not None) and (prev_for_cross <= threshold) and (ltp > threshold):
            entry = ceil_to_tick(ltp)  # ensure strictly above threshold and on valid tick
            sl = t["low"]
            risk = entry - sl
            if risk <= 0:
                print(f"[{tick_time:%H:%M:%S}] ✋ Risk <= 0 for {sym}, skipping.")
                trigger.pop(sym, None)
                return

            # determine qty using allocation / lot rules
            qty_calc = calculate_qty_for_allocation(sym, entry)
            if qty_calc <= 0:
                print(f"[{dt.datetime.now():%H:%M:%S}] ⚠️ Not enough allocation or zero lot for {sym} at entry {entry}. Skipping.")
                trigger.pop(sym, None)
                return

            tgt = round_to_tick(entry + (r_multiplier * risk))
            qty = qty_calc

            # place entry order (product_type depends on ORDER_MODE / INTRADAY vs CNC)
            product_type = order_mode_local.upper()
            resp, order_id = place_order(fy, sym, side=1, qty=qty, tag="GreenHammerBuy", product_type=product_type)

            # If this is CNC, attempt to place a broker-side GTT/OCO for SL & TGT
            gtt_id = None
            if product_type == "CNC":
                try:
                    ok, gresp = place_gtt_order(raw_access_token, sym, qty, sl, tgt, product_type=product_type)
                    if ok:
                        # try to extract id
                        if isinstance(gresp, dict):
                            gtt_id = gresp.get("id") or gresp.get("gtt_id") or gresp.get("data", {}).get("id")
                        print(f"[{dt.datetime.now():%H:%M:%S}] 🔁 GTT placed for {sym} -> {gresp}")
                    else:
                        print(f"[{dt.datetime.now():%H:%M:%S}] ⚠️ GTT placement failed for {sym} -> {gresp}")
                        gtt_id = None
                except Exception as e:
                    print(f"[{dt.datetime.now():%H:%M:%S}] ⚠️ GTT call exception for {sym}: {e}")

            # persist trade including gtt_id (if any)
            save_trade(sym, entry, sl, tgt, qty, product_type, order_id=order_id, gtt_id=gtt_id)

            t["triggered"] = True
            trigger.pop(sym, None)
            print(f"[{tick_time:%H:%M:%S}] ✅ LONG {sym} @ {entry} (cross>{threshold}), SL={sl}, TGT={tgt}, QTY={qty}, TF={TIMEFRAME_MIN}m, PROD={product_type}, GTT={gtt_id is not None}")

    return onmsg

# ===================== EXIT MONITOR (for LONG positions) with FORCE-EXIT =====================
def monitor_loop(fy: fyersModel.FyersModel, order_mode_local: str):
    """
    Monitors active trades for exit conditions (SL/TP) and force-exits at the end of the day.
    """
    global FORCE_CLOSED_ALL, FORCE_CLOSED_ALL_MCX
    while True:
        try:
            now_dt = dt.datetime.now()
            now_time = now_dt.time()

            # 1) Force-exit non-MCX INTRADAY trades at EXIT_ALL_TIME (run once)
            if (not FORCE_CLOSED_ALL) and (now_time >= EXIT_ALL_TIME):
                non_mcx_trades = [s for s in list(active_trades.keys())
                                  if (not s.startswith("MCX:")) and (active_trades.get(s, {}).get("product", "INTRADAY") == "INTRADAY")]
                if non_mcx_trades:
                    print(f"[{now_dt:%H:%M:%S}] ⏳ EXIT_ALL (non-MCX INTRADAY) — closing {len(non_mcx_trades)} trades")
                    for sym in non_mcx_trades:
                        trade = active_trades.get(sym)
                        if not trade:
                            continue
                        qty = trade.get("qty", DEFAULT_QTY)
                        try:
                            print(f"[{now_dt:%H:%M:%S}] 🔔 Force exiting {sym} (SELL market) qty={qty}")
                            exit_long_by_sell_market(fy, sym, qty, order_mode_local)
                        except Exception as e:
                            print(f"[{now_dt:%H:%M:%S}] ⚠️ Force-exit error for {sym}: {e}")
                        active_trades[sym]["status"] = "closed"
                        active_trades.pop(sym, None)
                    trigger.clear()
                else:
                    print(f"[{now_dt:%H:%M:%S}] ⏳ EXIT_ALL (non-MCX) triggered but no INTRADAY trades to close.")
                FORCE_CLOSED_ALL = True

            # 1b) Force-exit MCX INTRADAY trades at EXIT_ALL_TIME_MCX (run once)
            if (not FORCE_CLOSED_ALL_MCX) and (now_time >= EXIT_ALL_TIME_MCX):
                mcx_trades = [s for s in list(active_trades.keys())
                              if s.startswith("MCX:") and (active_trades.get(s, {}).get("product", "INTRADAY") == "INTRADAY")]
                if mcx_trades:
                    print(f"[{now_dt:%H:%M:%S}] ⏳ EXIT_ALL (MCX INTRADAY) — closing {len(mcx_trades)} trades")
                    for sym in mcx_trades:
                        trade = active_trades.get(sym)
                        if not trade:
                            continue
                        qty = trade.get("qty", DEFAULT_QTY)
                        try:
                            print(f"[{now_dt:%H:%M:%S}] 🔔 Force exiting {sym} (SELL market) qty={qty}")
                            exit_long_by_sell_market(fy, sym, qty, order_mode_local)
                        except Exception as e:
                            print(f"[{now_dt:%H:%M:%S}] ⚠️ Force-exit error for {sym}: {e}")
                        active_trades[sym]["status"] = "closed"
                        active_trades.pop(sym, None)
                    trigger.clear()
                else:
                    print(f"[{now_dt:%H:%M:%S}] ⏳ EXIT_ALL (MCX) triggered but no INTRADAY MCX trades to close.")
                FORCE_CLOSED_ALL_MCX = True

            # 2) Normal monitoring for open trades (SL/TP)
            if active_trades:
                for sym in list(active_trades.keys()):
                    trade = active_trades.get(sym)
                    if not trade or trade.get("status") != "open":
                        continue

                    ltp = get_ltp(fy, sym)
                    if ltp is None:
                        continue

                    sl = trade["sl"]
                    tgt = trade["tgt"]
                    qty = trade["qty"]
                    prod = trade.get("product", order_mode_local)

                    # SL hit
                    if ltp <= sl:
                        print(f"[{dt.datetime.now():%H:%M:%S}] ❌ SL HIT {sym} @ {ltp} → SELL market")
                        active_trades[sym]["status"] = "exiting"
                        exit_resp, oid = exit_long_by_sell_market(fy, sym, qty, prod)
                        active_trades[sym]["status"] = "closed"
                        active_trades.pop(sym, None)
                        persist_active_trades()
                        try:
                            pd.DataFrame([{
                                "Datetime": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "Symbol": sym,
                                "Exit Price": float(ltp),
                                "Exit Type": "SL",
                                "OrderID": oid
                            }]).to_csv(TRADE_LOG, mode='a', header=not os.path.exists(TRADE_LOG), index=False)
                        except Exception:
                            pass

                    # Target hit
                    elif ltp >= tgt:
                        print(f"[{dt.datetime.now():%H:%M:%S}] 🎯 TARGET HIT {sym} @ {ltp} → SELL market")
                        active_trades[sym]["status"] = "exiting"
                        exit_resp, oid = exit_long_by_sell_market(fy, sym, qty, prod)
                        active_trades[sym]["status"] = "closed"
                        active_trades.pop(sym, None)
                        persist_active_trades()
                        try:
                            pd.DataFrame([{
                                "Datetime": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "Symbol": sym,
                                "Exit Price": float(ltp),
                                "Exit Type": "TGT",
                                "OrderID": oid
                            }]).to_csv(TRADE_LOG, mode='a', header=not os.path.exists(TRADE_LOG), index=False)
                        except Exception:
                            pass

        except Exception as e:
            print(f"⚠️ Monitor loop error: {e}\n{traceback.format_exc()}")

        time.sleep(1.5)

# ===================== MAIN =====================
def has_open_positions() -> bool:
    """
    Checks if there are any open positions.
    """
    return any(v.get("status") == "open" for v in active_trades.values())

def main():
    """
    Main function to run the trading bot.
    """
    global TIMEFRAME_MIN, r_multiplier, order_mode, alloc_default
    parser = argparse.ArgumentParser()
    parser.add_argument("--tf", type=int, default=TIMEFRAME_MIN, help="Timeframe in minutes (e.g., 5, 15, 60)")
    parser.add_argument("--rmult", type=float, default=r_multiplier, help="Risk:Reward multiple (e.g., 2.0)")
    parser.add_argument("--mode", type=str, choices=["intraday","cnc"], default=order_mode.lower(), help="Order product type: intraday or cnc")
    parser.add_argument("--alloc-default", type=float, default=alloc_default, help="Default rupee allocation per symbol (stocks).")
    args, _ = parser.parse_known_args()
    TIMEFRAME_MIN = max(1, int(args.tf))
    r_multiplier = float(args.rmult)
    order_mode = "CNC" if args.mode.lower() == "cnc" else "INTRADAY"
    alloc_default = float(args.alloc_default)

    # Load persisted trades before auth
    load_persisted_trades()

    app_id, token_str, raw_access = ensure_access_token()

    # REST client uses raw_access
    fy = fyersModel.FyersModel(client_id=app_id, token=raw_access, log_path=".")

    # Start exit monitor thread
    threading.Thread(target=monitor_loop, args=(fy, order_mode), daemon=True).start()

    # WebSocket uses token_str
    on_message = make_onmsg(fy, order_mode, raw_access)
    ws = data_ws.FyersDataSocket(
        access_token=token_str,
        log_path=".",
        litemode=False,
        write_to_file=False,
        reconnect=True,
        on_message=on_message,
        on_error=lambda m: print("🚨", m),
        on_close=lambda m: print("❌", m),
        on_connect=lambda: (
            print(f"🔌 Connected → subscribing {len(SYMBOLS)} symbols | TF={TIMEFRAME_MIN}m") or
            ws.subscribe(symbols=SYMBOLS, data_type="SymbolUpdate")
        )
    )

    print("\n========== Green-Hammer/PINBAR Scanner (Strict Breakout + VWAP + Alloc/GTT) ==========")
    print(f"🧩 Python: {sys.version.split()[0]}  |  Symbols: {len(SYMBOLS)} | TF={TIMEFRAME_MIN}m | Rmult={r_multiplier} | ORDER_MODE={order_mode} | ALLOC_DEFAULT={alloc_default}")
    print(f"🗄️  Persisted open trades: {len(active_trades)} (loaded from {TRADE_STORE})")
    print("🚀 Real-time LONG scanner started …\n")

    try:
        ws.connect()
    except Exception as e:
        print(f"❌ Websocket connect failed: {e}")
        raise

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Exiting on user interrupt.")
    except Exception as e:
        print(f"❌ Fatal error: {e}\n{traceback.format_exc()}")
        sys.exit(1)
