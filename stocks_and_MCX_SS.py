# -*- coding: utf-8 -*-
"""
Red-ShootingStar / Red-Pinbar Strategy for NSE Stocks & MCX Futures

This script identifies the "Red Shooting Star" candlestick pattern on a given
watchlist of NSE equities and MCX futures. It then enters a short position on
the breakout of the signal candle's low on the next candle.

Features:
- Trades a mixed watchlist of NSE stocks and MCX futures.
- Implements separate trading hours for NSE and MCX.
- Uses hardcoded lot sizes for MCX futures for reliability.
- Supports carry-forward (CNC) positions for NSE stocks by saving and
  loading the trade state.
- Includes a dry-run mode for testing without placing live orders.
"""
import os
import sys
import json
import time
import math
import hashlib
import datetime as dt
import pytz
from urllib.parse import urlparse, parse_qs, quote
import threading
import argparse
import webbrowser
import re
from typing import Optional, Dict, List, Tuple

ws_connection = None
import pandas as pd
import requests
import io

# Try to import real Fyers library
HAS_FYERS = True
try:
    from fyers_apiv3 import fyersModel
    from fyers_apiv3.FyersWebsocket import data_ws
except Exception as e:
    HAS_FYERS = False
    print(
        f"⚠️ fyers_apiv3 not available — running in dry-run mode with mocks. Install the real package to enable live trading.")


    class MockFyersModel:
        def __init__(self, client_id=None, token=None, log_path=None):
            self.client_id = client_id
            self.token = token
            self.log_path = log_path

        def place_order(self, payload):
            now = dt.datetime.now().strftime("%Y%m%d%H%M%S")
            order_id = f"MOCKORD-{now}"
            print(f"[MOCK] place_order -> {payload} -> order_id={order_id}")
            return {"s": "ok", "order_id": order_id}

        def quotes(self, payload):
            symbols = payload.get("symbols")
            val = 100.0
            return {"s": "ok", "d": [{"v": {"lp": val, "last_price": val}}]}

        def symbol_details(self, payload):
            return {"s": "ok", "d": {payload.get("symbol"): {"lot_size": 65}}}

        def positions(self):
            print("[MOCK] positions() -> returning empty list")
            return {"s": "ok", "netPositions": []}


    class MockDataSocket:
        def __init__(self, access_token=None, log_path=None, litemode=False, write_to_file=False, reconnect=True,
                     on_message=None, on_error=None, on_close=None, on_connect=None):
            self.access_token = access_token
            self.log_path = log_path
            self.litemode = litemode
            self.write_to_file = write_to_file
            self.reconnect = reconnect
            self._on_message = on_message
            self._on_error = on_error
            self._on_close = on_close
            self._on_connect = on_connect
            self._subscribed = []

        def subscribe(self, symbols=None, data_type="SymbolUpdate"):
            self._subscribed = symbols or []
            print(f"[MOCK] Subscribed to {len(self._subscribed)} symbols (data_type={data_type})")

        def connect(self):
            print("[MOCK] WebSocket connect() called — calling on_connect callback (if any)")
            if callable(self._on_connect):
                try:
                    self._on_connect()
                except Exception as e:
                    if callable(self._on_error):
                        self._on_error(e)

        def close(self):
            print("[MOCK] WebSocket close() called")
            if callable(self._on_close):
                self._on_close(None)

                # point names used later


    fyersModel = type("fyersModel", (), {"FyersModel": MockFyersModel})
    data_ws = type("data_ws", (), {"FyersDataSocket": MockDataSocket})

# ===================== STRATEGY SETTINGS =====================
TIMEFRAME_MIN = 15  # change to 5 / 15 / 30 / 60 etc., or override with --tf
R_MULTIPLIER = 1.0  # default Risk:Reward (1:2)
LOT_MULTIPLIER = 1  # Number of lots to trade
EPS = 1e-6

# ===================== CANDLE GEOMETRY SETTINGS =====================
# UPDATED: More realistic shooting star geometry
UPPER_WICK_MIN = 50  # was 55 (50-80% → Clear rejection but not extreme)
UPPER_WICK_MAX = 80  # was 90
BODY_MIN = 5  # was 5 (5-30% → Small to medium body)
BODY_MAX = 30  # was 20
LOWER_WICK_MAX = 25  # was 12 (0-25% → Permits small lower shadows)

# One-position-at-a-time control
ONE_POSITION_AT_A_TIME = True

# Tick setup (NSE equities typically 0.05)
TICK_SIZE = 0.05


def round_to_tick(x, tick=TICK_SIZE):
    return round(round(x / tick) * tick, 2)


def ceil_to_tick(x, tick=TICK_SIZE):
    k = math.floor(x / tick)
    if abs(x - k * tick) < 1e-12:
        return round(x, 2)
    return round((k + 1) * tick, 2)


def floor_to_tick(x, tick=TICK_SIZE):
    k = math.floor(x / tick)
    return round(k * tick, 2)


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
    'MCX:SILVERMIC26FEBFUT', 'MCX:CRUDEOILM26JANFUT', 'MCX:NATGASMINI26JANFUT'
]
# ===================== LOT SIZES =====================
# Correct, hardcoded lot sizes for MCX as per user
MCX_LOTS = {
    "SILVERMIC": 1,
    "CRUDEOILM": 10,
    "NATGASMINI": 10,
}

def get_lot_size(symbol: str) -> int:
    """Gets lot size for a given symbol."""
    if symbol.endswith("-EQ"):
        return 1

    # Extract base symbol for MCX futures
    base = symbol.split(':')[1]  # e.g., 'MCX:CRUDEOILM26JANFUT' -> 'CRUDEOILM26JANFUT'
    for mcx_base, lot in MCX_LOTS.items():
        if base.startswith(mcx_base):
            return lot

    print(f"⚠️ Could not determine lot size for {symbol}, defaulting to 1.")
    return 1

# ===================== TIME/ENTRY/EXIT RULES =====================
# Default (non-MCX) behaviour
ENTRY_BUFFER = 0.05                # buffer below signal low for breakout (we require strict cross below)
ENTRY_CUTOFF = dt.time(15, 0)      # no new entries after 3:00 PM (non-MCX)
EXIT_ALL_TIME = dt.time(15, 9)     # force-exit all open (non-MCX) positions at 3:09 PM

# MCX-specific times (user requested)
ENTRY_CUTOFF_MCX = dt.time(22, 0)   # allow MCX signals up to 10:00 PM
EXIT_ALL_TIME_MCX = dt.time(22, 50) # force-exit all open MCX positions at 10:50 PM

FORCE_CLOSED_ALL = False
FORCE_CLOSED_ALL_MCX = False

LOG_FILE = "trade_log.csv"

# Product type for NSE stocks ("INTRADAY" or "CNC")
STOCK_PRODUCT_TYPE = "INTRADAY"

# Product type for MCX futures ("INTRADAY", "MARGIN", or "NRML")
MCX_PRODUCT_TYPE = "INTRADAY"

# Timezone (IST)
TIMEZONE = "Asia/Kolkata"
IST = pytz.timezone(TIMEZONE)

# Position sizing mode globals
POSITION_MODE = "qty"  # "alloc" or "qty"
FIXED_QTY = 1  # default 1 share when using qty mode
QTY_MAP: Dict[str, int] = {}

# Re-auth guard to avoid infinite recursion
REAUTH_ATTEMPTS = 0
MAX_REAUTH_ATTEMPTS = 3

# ===================== SMALL CANDLE GUARDS =====================
MIN_RANGE_PCT = 0.0015  # ignore if (H-L)/Close < 0.15%
MIN_BODY_TICKS = 0  # optional minimum body size; 0 disables
# ===================== IO HELPERS =====================
def _read_json(path, default=None):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

# ===================== STATE MANAGEMENT =====================
def save_state():
    """Saves the active_trades dictionary to a file."""
    try:
        _write_json("active_trades.json", active_trades)
        print(f"[{dt.datetime.now():%H:%M:%S}] ✅ State saved successfully.")
    except Exception as e:
        print(f"[{dt.datetime.now():%H:%M:%S}] ❌ Error saving state: {e}")

def load_state():
    """Loads the active_trades dictionary from a file."""
    global active_trades
    loaded_trades = _read_json("active_trades.json")
    if loaded_trades:
        active_trades = loaded_trades
        print(f"[{dt.datetime.now():%H:%M:%S}] ✅ State loaded successfully. {len(active_trades)} active trades restored.")

# ===================== LOGIN & TOKEN MGMT =====================


def load_creds():
    creds = _read_json(CONFIG_FILE)
    if not creds:
        raise SystemExit("❌ Missing 'fyers_login_details.json'. Create it with {api_key, api_secret, redirect_url}.")
    for k in ("api_key", "api_secret", "redirect_url"):
        if k not in creds or not creds[k]:
            raise SystemExit(f"❌ '{k}' missing in {CONFIG_FILE}.")
    return creds


def ensure_access_token():
    """
    Ensures a valid Fyers access token is available.
    """
    creds = load_creds()
    client_id = creds["api_key"]
    secret_key = creds["api_secret"]
    redirect_uri = creds["redirect_url"]
    # Check if a token file for today already exists
    if os.path.exists(TODAY_PATH):
        access_token = _read_json(TODAY_PATH)
        if access_token and isinstance(access_token, str):
            print("🔑 Using today's cached access token.")
            return client_id, access_token
            # If today's token doesn't exist, try to use a refresh token
    store = _read_json(TOKENS_STORE, {}) or {}
    refresh_token = store.get("refresh_token")
    if refresh_token:
        try:
            print("🔄 Attempting refresh-token login …")
            session = fyersModel.SessionModel(
                client_id=client_id,
                secret_key=secret_key,
                redirect_uri=redirect_uri,
                response_type="code",
                grant_type="refresh_token"
            )
            session.set_token(refresh_token)
            response = session.generate_token()
            if response.get("s") != "ok":
                raise RuntimeError(f"Refresh token failed: {response.get('message')}")

            new_access_token = response["access_token"]
            new_refresh_token = response.get("refresh_token")
            _write_json(TOKENS_STORE, {"refresh_token": new_refresh_token or refresh_token})
            _write_json(TODAY_PATH, new_access_token)
            print("✅ Refresh successful.")
            return client_id, new_access_token
        except Exception as e:
            print(f"⚠️ Refresh failed: {e}. Falling back to manual login.")
            # Clear stored tokens on failure
            if os.path.exists(TOKENS_STORE):
                _write_json(TOKENS_STORE, {})
                # Fallback to interactive login
    session = fyersModel.SessionModel(
        client_id=client_id,
        secret_key=secret_key,
        redirect_uri=redirect_uri,
        response_type="code",
        grant_type="authorization_code"
    )

    auth_url = session.generate_authcode()
    print(f"\n👉 Open this login URL in your browser, complete login, and copy the auth_code from the redirect URL:")
    print(auth_url)
    webbrowser.open(auth_url, new=1)
    auth_code = input("\nPaste the auth_code here: ").strip()
    session.set_token(auth_code)
    response = session.generate_token()
    if response.get("s") != "ok":
        raise SystemExit(f"❌ Token generation failed: {response.get('message')}")
    access_token = response["access_token"]
    refresh_token = response.get("refresh_token")
    print("✅ New access token generated successfully.")
    _write_json(TODAY_PATH, access_token)
    if refresh_token:
        _write_json(TOKENS_STORE, {"refresh_token": refresh_token})
    return client_id, access_token


# ===================== CANDLE DETECTOR (Bearish Shooting Star) - UPDATED =====================
def is_bearish_shooting_star_candle(o, h, l, c, prev_o, prev_c, min_range_pct=0.0015):
    """
    RED shooting-star / pin-bar with UPDATED geometry (more realistic):
    - Previous candle GREEN
    - Current candle RED
    - Geometry Constraints (as % of total candle range H-L):
      - Upper Wick: 50% - 80% (UPDATED: was 55-90%)
      - Body: 5% - 30% (UPDATED: was 5-20%)
      - Lower Wick: 0% - 25% (UPDATED: was 0-12%)
    """
    # --- Initial Filters ---
    if c >= o:  # Current candle must be red
        return False
    if prev_c <= prev_o:  # Previous candle must be green
        return False
    if c == 0 or h <= l:
        return False
    total_range = h - l
    if (total_range / max(abs(c), 1e-9)) < min_range_pct:
        return False
        # --- UPDATED Geometric Calculation ---
    upper_wick_pct = ((h - o) / total_range) * 100
    body_pct = ((o - c) / total_range) * 100
    lower_wick_pct = ((c - l) / total_range) * 100
    is_valid_geometry = (
            (UPPER_WICK_MIN <= upper_wick_pct <= UPPER_WICK_MAX) and
            (BODY_MIN <= body_pct <= BODY_MAX) and
            (0 <= lower_wick_pct <= LOWER_WICK_MAX)
    )
    return is_valid_geometry


def flag_bearish_shooting_star(df: pd.DataFrame, min_range_pct=0.0015):
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    prev_o, prev_c = o.shift(1), c.shift(1)
    total_range = h - l
    total_range_safe = total_range.where(total_range > 0, 1e-9)
    upper_wick_pct = ((h - o) / total_range_safe) * 100
    body_pct = ((o - c) / total_range_safe) * 100
    lower_wick_pct = ((c - l) / total_range_safe) * 100
    # Define conditions for clarity, starting with the most important ones
    cond_red = c < o
    cond_prev_green = prev_c > prev_o
    cond_min_range = (total_range / c.abs().where(c != 0, 1e-9)) >= min_range_pct
    cond_geom = (
            (upper_wick_pct >= UPPER_WICK_MIN) & (upper_wick_pct <= UPPER_WICK_MAX) &
            (body_pct >= BODY_MIN) & (body_pct <= BODY_MAX) &
            (lower_wick_pct >= 0) & (lower_wick_pct <= LOWER_WICK_MAX)
    )
    df["BearishShoot"] = cond_red & cond_prev_green & cond_min_range & cond_geom
    return df


# ===================== ORDER HELPERS =====================
def place_order(fy, sym: str, side: int, qty: int, tag: str, dry_run=False) -> Dict:
    # Fix order tag - remove special characters
    clean_tag = re.sub(r'[^a-zA-Z0-9]', '', tag)  # Keep only alphanumeric

    # Determine product type based on exchange
    if sym.startswith("MCX:"):
        product_type = MCX_PRODUCT_TYPE
    else:
        product_type = STOCK_PRODUCT_TYPE

    payload = {
        "symbol": sym,
        "qty": int(qty),  # CRITICAL: This must be TOTAL SHARES
        "type": 2,  # market
        "side": int(side),  # 1=buy, -1=sell
        "productType": product_type,
        "validity": "DAY",
        "orderTag": clean_tag[:15] if clean_tag else ""
    }

    if dry_run:
        print(f"[DRY-RUN] Would place order: {payload}")
        return {"s": "ok", "order_id": "DRYRUN"}

    try:
        resp = fy.place_order(payload)

        # Only print success messages for executed orders
        if resp.get('s') == 'ok' and resp.get('code') == 1101:  # 1101 = Successfully placed order
            print(f"[{dt.datetime.now():%H:%M:%S}] ✅ ORDER EXECUTED {tag}: {sym} {side} {qty} shares")
        elif resp.get('s') == 'error':
            # Check for margin shortfall error
            error_msg = resp.get('message', '')
            if 'Margin Shortfall' in error_msg or 'RED:' in error_msg:
                print(f"[{dt.datetime.now():%H:%M:%S}] ❌ MARGIN SHORTFALL - Order NOT placed: {error_msg}")
            else:
                print(f"[{dt.datetime.now():%H:%M:%S}] ❌ Order error {sym} {tag}: {error_msg}")
        else:
            print(f"[{dt.datetime.now():%H:%M:%S}] 📌 {tag} Response: {resp}")

        return resp

    except Exception as e:
        print(f"[{dt.datetime.now():%H:%M:%S}] 🚨 Order error {sym} {tag}: {e}")
        return {"s": "error", "message": str(e)}


def exit_short_by_buy_market(fy, sym: str, qty_lots: int, lot_size: int, dry_run=False):
    # to exit a short we BUY market (side=1)
    # Convert lots to shares for Fyers API
    qty_shares = qty_lots * lot_size
    return place_order(fy, sym, side=1, qty=qty_shares, tag="ExitShort", dry_run=dry_run)


# ===================== TRADE LOG & TRACKING =====================
active_trades = {}  # sym -> dict(entry, sl, tgt, qty, status, side, lot_size, order_id)


def has_open_positions() -> bool:
    return any(v.get("status") == "open" for v in active_trades.values())


def save_trade(sym, entry, sl, tgt, qty_lots, side=-1, lot_size=1, order_id=""):
    # Determine product type based on exchange
    if sym.startswith("MCX:"):
        product_type = MCX_PRODUCT_TYPE
    else:
        product_type = STOCK_PRODUCT_TYPE

    row = {
        "Datetime": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Symbol": sym,
        "Entry Price": float(entry),
        "Stop Loss": float(sl),
        "Target": float(tgt),
        "Qty": int(qty_lots),  # Number of lots
        "Lot Size": int(lot_size),  # Shares per lot
        "Total Shares": int(qty_lots * lot_size),
        "Side": "SHORT" if side == -1 else "LONG",
        "Order ID": order_id,
        "Product Type": product_type
    }
    pd.DataFrame([row]).to_csv(
        "trade_log.csv",
        mode='a',
        header=not os.path.exists("trade_log.csv"),
        index=False
    )
    active_trades[sym] = {
        "entry": entry,
        "sl": sl,
        "tgt": tgt,
        "qty": qty_lots,
        "status": "open",
        "side": side,
        "lot_size": lot_size,
        "order_id": order_id,
        "order_placed_successfully": True if order_id else False,
        "productType": product_type
    }
    save_state()


# ===================== CANDLE BUILD STATE & LTP CACHE =====================
bars = {}
processed_candles = set()
trigger = {}
ltp_cache = {}  # symbol -> (ltp, ts)
prev_ltp_cache = {}  # symbol -> previous ltp (for strict cross)
_last_quote_error = {}
ERROR_THROTTLE_SECS = 10


def candle_start(t: dt.datetime) -> dt.datetime:
    return t.replace(second=0, microsecond=0) - dt.timedelta(minutes=t.minute % TIMEFRAME_MIN)


# ===================== SAFE QUOTES (cache-first, REST fallback) =====================
def get_ltp(fy, sym, cache_ttl=10, max_retries=3):
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


# ===================== WEBSOCKET HANDLER (LIVE SHORT logic) =====================
def make_onmsg(fy, dry_run=False):
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
        tick_time = dt.datetime.fromtimestamp(ts)
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
            # when candle completes, check for signal
        if tick_time >= cstart + dt.timedelta(minutes=TIMEFRAME_MIN) - dt.timedelta(seconds=1):
            if key not in processed_candles:
                processed_candles.add(key)
                prev_cstart = cstart - dt.timedelta(minutes=TIMEFRAME_MIN)
                prev_bar = bars.get((sym, prev_cstart))
                if ONE_POSITION_AT_A_TIME and has_open_positions():
                    return
                if prev_bar and is_bearish_shooting_star_candle(
                        bar["o"], bar["h"], bar["l"], bar["c"],
                        prev_bar["o"], prev_bar["c"],
                        min_range_pct=MIN_RANGE_PCT
                ):
                    next_cstart = cstart + dt.timedelta(minutes=TIMEFRAME_MIN)
                    trigger[sym] = {
                        "low": bar["l"],
                        "high": bar["h"],
                        "active_start": next_cstart,
                        "triggered": False
                    }
                    print(
                        f"[{tick_time:%H:%M:%S}] 🎯 SIGNAL {sym} TF={TIMEFRAME_MIN}m → watch NEXT LOW {bar['l']:.2f} (SL {bar['h']:.2f})")
                    # Log candle geometry for debugging
                    total_range = bar["h"] - bar["l"]
                    if total_range > 0:
                        upper_pct = ((bar["h"] - bar["o"]) / total_range) * 100
                        body_pct = ((bar["o"] - bar["c"]) / total_range) * 100
                        lower_pct = ((bar["c"] - bar["l"]) / total_range) * 100
                        print(
                            f"[{tick_time:%H:%M:%S}] 📊 Candle Geometry: U={upper_pct:.1f}%, B={body_pct:.1f}%, L={lower_pct:.1f}%")
        t = trigger.get(sym)
        if not t:
            return
            # expire trigger if window passed
        if tick_time >= t["active_start"] + dt.timedelta(minutes=TIMEFRAME_MIN):
            trigger.pop(sym, None)
            return
            # only act in NEXT candle window and if not already triggered
        if tick_time < t["active_start"] or t["triggered"]:
            return
        if ONE_POSITION_AT_A_TIME and has_open_positions():
            print(f"[{dt.datetime.now():%H:%M:%S}] 🚫 Skipping {sym} entry — position already open.")
            trigger.pop(sym, None)
            return

        # cutoff guard: use MCX-specific cutoff for MCX symbols, else default
        now_time = dt.datetime.now().time()
        if sym.startswith("MCX:"):
            cutoff_time = ENTRY_CUTOFF_MCX
        else:
            cutoff_time = ENTRY_CUTOFF

        if now_time >= cutoff_time:
            print(f"[{dt.datetime.now():%H:%M:%S}] ⏰ Skipping NEW entry {sym} — cutoff passed ({cutoff_time})")
            trigger.pop(sym, None)
            return

        # breakout condition
        threshold = round_to_tick(t["low"] - ENTRY_BUFFER)
        prev_for_cross = prev_ltp_cache.get(sym)
        if (prev_for_cross is not None) and (prev_for_cross >= threshold) and (ltp < threshold):
            print(f"[{tick_time:%H:%M:%S}] 🔥 BREAKOUT {sym} < {threshold:.2f}. Placing trade...")

            lot_size = get_lot_size(sym)
            if lot_size == 0:
                print(f"[{tick_time:%H:%M:%S}] ✋ Could not determine lot size for {sym}, skipping trade.")
                trigger.pop(sym, None)
                return

            qty_shares = LOT_MULTIPLIER * lot_size
            qty_lots = LOT_MULTIPLIER

            entry_price = floor_to_tick(ltp)
            sl_price = t["high"]
            risk = sl_price - entry_price
            if risk <= 0:
                print(f"[{tick_time:%H:%M:%S}] ✋ Risk <= 0 for {sym}, skipping.")
                trigger.pop(sym, None)
                return
            tgt_price = round_to_tick(entry_price - (R_MULTIPLIER * risk))

            order_resp = place_order(fy, sym, side=-1, qty=qty_shares, tag="RedShootSell", dry_run=dry_run)

            if order_resp.get('s') == 'ok' and order_resp.get('code') == 1101:
                order_id = order_resp.get('id', '')
                save_trade(sym, entry_price, sl_price, tgt_price, qty_lots, side=-1, lot_size=lot_size,
                           order_id=order_id)
                t["triggered"] = True
                trigger.pop(sym, None)
                print(
                    f"[{tick_time:%H:%M:%S}] ✅ SHORT {sym} @ {entry_price:.2f}, SL={sl_price:.2f}, TGT={tgt_price:.2f}, QTY={qty_lots} lots ({qty_shares} shares), Lot Size={lot_size}")
            else:
                print(f"[{tick_time:%H:%M:%S}] ❌ Order NOT placed for {sym}, cleaning trigger...")
                trigger.pop(sym, None)

    return onmsg


def sync_positions_with_broker(fy):
    """
    Checks Fyers positions and removes any trades from active_trades
    that are no longer open. This handles manual closing.
    """
    global active_trades
    try:
        response = fy.positions()
        if response.get('s') != 'ok':
            print(f"[{dt.datetime.now():%H:%M:%S}] ⚠️ Could not fetch broker positions: {response.get('message')}")
            return False

        # Create a set of symbols for open positions at the broker
        broker_open_symbols = {
            pos['symbol'] for pos in response.get('netPositions', [])
            if pos.get('netQty', 0) != 0
        }

        # Find trades in memory that are no longer open at the broker
        manually_closed_trades = [
            sym for sym, trade in active_trades.items()
            if trade['status'] == 'open' and sym not in broker_open_symbols
        ]

        if manually_closed_trades:
            print(f"[{dt.datetime.now():%H:%M:%S}]  Detected {len(manually_closed_trades)} manually closed trade(s). Syncing state...")
            for sym in manually_closed_trades:
                print(f"    - Removing {sym} from active trades.")
                active_trades.pop(sym, None)

            # Return True to indicate that the state has changed
            return True

    except Exception as e:
        print(f"[{dt.datetime.now():%H:%M:%S}] ❌ Error syncing positions: {e}")

    return False

# ===================== EXIT MONITOR (for SHORT positions) with FORCE-EXIT =====================
def monitor_loop(fy, dry_run=False):
    global FORCE_CLOSED_ALL, FORCE_CLOSED_ALL_MCX
    last_sync = time.time()
    sync_interval = 60 # Sync every 60 seconds

    while True:
        try:
            now = time.time()
            now_dt = dt.datetime.now()
            now_time = now_dt.time()

            # Periodically sync with broker positions
            if now - last_sync > sync_interval:
                if sync_positions_with_broker(fy):
                    save_state() # Save state if changes were made
                last_sync = now

            # 1) Force-exit non-MCX open trades at or after EXIT_ALL_TIME (run once)
            if (not FORCE_CLOSED_ALL) and (now_time >= EXIT_ALL_TIME):
                # collect non-MCX trades
                non_mcx_trades = [s for s, t in active_trades.items() if not s.startswith("MCX:") and t.get("productType") != "CNC"]
                if non_mcx_trades:
                    print(f"[{now_dt:%H:%M:%S}] ⏳ EXIT_ALL (non-MCX) triggered — closing {len(non_mcx_trades)} open non-MCX trades")
                    for sym in non_mcx_trades:
                        trade = active_trades.get(sym)
                        if not trade:
                            continue
                        qty = trade.get("qty")
                        lot_size = trade.get("lot_size", 1)
                        try:
                            print(f"[{now_dt:%H:%M:%S}] 🔔 Force exiting {sym} (COVER BUY market) qty={qty}")
                            exit_short_by_buy_market(fy, sym, qty, lot_size, dry_run=dry_run)
                        except Exception as e:
                            print(f"[{now_dt:%H:%M:%S}] ⚠️ Force-exit error for {sym}: {e}")
                        active_trades[sym]["status"] = "closed"
                        active_trades.pop(sym, None)
                    trigger.clear()
                    save_state()
                else:
                    print(f"[{now_dt:%H:%M:%S}] ⏳ EXIT_ALL (non-MCX) triggered but no open non-MCX trades.")
                FORCE_CLOSED_ALL = True

            # 1b) Force-exit MCX open trades at or after EXIT_ALL_TIME_MCX (run once)
            if (not FORCE_CLOSED_ALL_MCX) and (now_time >= EXIT_ALL_TIME_MCX):
                mcx_trades = [s for s in list(active_trades.keys()) if s.startswith("MCX:")]
                if mcx_trades:
                    print(f"[{now_dt:%H:%M:%S}] ⏳ EXIT_ALL (MCX) triggered — closing {len(mcx_trades)} open MCX trades")
                    for sym in mcx_trades:
                        trade = active_trades.get(sym)
                        if not trade:
                            continue
                        qty = trade.get("qty")
                        lot_size = trade.get("lot_size", 1)
                        try:
                            print(f"[{now_dt:%H:%M:%S}] 🔔 Force exiting {sym} (COVER BUY market) qty={qty}")
                            exit_short_by_buy_market(fy, sym, qty, lot_size, dry_run=dry_run)
                        except Exception as e:
                            print(f"[{now_dt:%H:%M:%S}] ⚠️ Force-exit error for {sym}: {e}")
                        active_trades[sym]["status"] = "closed"
                        active_trades.pop(sym, None)
                    trigger.clear()
                    save_state()
                else:
                    print(f"[{now_dt:%H:%M:%S}] ⏳ EXIT_ALL (MCX) triggered but no open MCX trades.")
                FORCE_CLOSED_ALL_MCX = True

            if active_trades:
                for sym in list(active_trades.keys()):
                    trade = active_trades.get(sym)
                    if not trade or trade["status"] != "open":
                        continue

                    if not trade.get("order_placed_successfully", False):
                        print(f"[{now_dt:%H:%M:%S}] ⚠️ Skipping monitoring for {sym} - order not placed successfully")
                        active_trades.pop(sym, None)
                        continue

                    ltp = get_ltp(fy, sym)
                    if ltp is None:
                        continue
                    sl = trade["sl"]
                    tgt = trade["tgt"]
                    qty_lots = trade["qty"]
                    lot_size = trade.get("lot_size", 1)
                    side = trade.get("side", -1)

                    if side == -1:
                        if ltp >= sl:
                            print(
                                f"[{dt.datetime.now():%H:%M:%S}] ❌ SL HIT {sym} @ {ltp:.2f} → BUY market to cover (exit)")
                            active_trades[sym]["status"] = "exiting"
                            exit_short_by_buy_market(fy, sym, qty_lots, lot_size, dry_run=dry_run)
                            active_trades[sym]["status"] = "closed"
                            active_trades.pop(sym, None)
                            save_state()
                        elif ltp <= tgt:
                            print(
                                f"[{dt.datetime.now():%H:%M:%S}] 🎯 TARGET HIT {sym} @ {ltp:.2f} → BUY market to cover (exit)")
                            active_trades[sym]["status"] = "exiting"
                            exit_short_by_buy_market(fy, sym, qty_lots, lot_size, dry_run=dry_run)
                            active_trades[sym]["status"] = "closed"
                            active_trades.pop(sym, None)
                            save_state()
        except Exception as e:
            print(f"⚠️ Monitor loop error: {e}")
        time.sleep(1.5)

# ===================== MAIN =====================

def main():
    global TIMEFRAME_MIN, R_MULTIPLIER

    load_state()

    parser = argparse.ArgumentParser()
    parser.add_argument("--tf", type=int, default=TIMEFRAME_MIN, help="Timeframe in minutes (e.g., 5, 15, 60)")
    parser.add_argument("--rmult", type=float, default=R_MULTIPLIER,
                        help="Risk:Reward multiple (e.g., 2.0 means target = entry - 2 * risk)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Enable dry-run: simulate orders instead of placing live ones")
    parser.add_argument("--run-tests", action="store_true", help="Run unit tests for detector logic and exit")

    args, _ = parser.parse_known_args()
    TIMEFRAME_MIN = max(1, int(args.tf))
    R_MULTIPLIER = float(args.rmult)
    dry_run = args.dry_run or (not HAS_FYERS)

    if args.run_tests:
        run_tests()
        return

    if HAS_FYERS:
        client_id, access_token = ensure_access_token()
        fy = fyersModel.FyersModel(client_id=client_id, token=access_token, log_path=".")
    else:
        client_id = "MOCK_APP"
        access_token = "MOCK_ACCESS"
        fy = fyersModel.FyersModel(client_id=client_id, token=access_token, log_path=".")

    print("\n✅ WATCHLIST:")
    print("=" * 60)
    for symbol in SYMBOLS:
        print(f"  - {symbol}")
    print("=" * 60)

    on_message = make_onmsg(fy, dry_run=dry_run)
    global ws_connection
    ws_connection = data_ws.FyersDataSocket(
        access_token=f"{client_id}:{access_token}",
        log_path=".",
        litemode=False,
        write_to_file=False,
        reconnect=True,
        on_message=on_message,
        on_error=lambda m: print("🚨", m),
        on_close=lambda m: print("❌", m),
        on_connect=lambda: (
                print(f"🔌 Connected → subscribing to {len(SYMBOLS)} symbols.") or
                ws_connection.subscribe(symbols=SYMBOLS)
        )
    )

    threading.Thread(target=monitor_loop, args=(fy, dry_run),
                     daemon=True).start()

    print("\n" + "=" * 70)
    print("🎯 RED-SHOOTING STAR STRATEGY - REAL-TIME")
    print("=" * 70)
    print(f"📊 LOT SIZES: NSE Equities = 1, MCX Futures = Hardcoded")
    print(f"📊 PRODUCT TYPE: {STOCK_PRODUCT_TYPE} for NSE, {MCX_PRODUCT_TYPE} for MCX")
    print(f"📊 CANDLE GEOMETRY:")
    print(f"   Upper Wick: {UPPER_WICK_MIN}-{UPPER_WICK_MAX}% (Clear rejection)")
    print(f"   Body: {BODY_MIN}-{BODY_MAX}% (Small-medium body)")
    print(f"   Lower Wick: 0-{LOWER_WICK_MAX}% (Small/no lower shadow)")
    print(f"📊 LOT MULTIPLIER: {LOT_MULTIPLIER} lot(s) per trade")
    print("=" * 70)
    print(f"🧩 Python: {sys.version.split()[0]} | Symbols: {len(SYMBOLS)}")
    print(f"📈 TF={TIMEFRAME_MIN}m | Rmult={R_MULTIPLIER} | dry_run={dry_run}")
    print("=" * 70)
    print("🚀 Real-time SHORT scanner started …\n")
    ws_connection.connect()


# ===================== SIMPLE UNIT TESTS FOR DETECTOR =====================
def run_tests():
    print("Running tests for UPDATED bearish shooting-star detector...")
    # Test 1: Valid shooting star with updated geometry
    # Upper: ~54.5%, Body: ~20.5%, Lower: 25.0%
    assert is_bearish_shooting_star_candle(100.0, 112.0, 90.0, 95.5, 95.0, 98.0) is True, "Test 1 Failed"
    # Test 2: Upper wick too short (40%)
    assert is_bearish_shooting_star_candle(105.0, 109.0, 95.0, 102.0, 100.0, 102.0) is False, "Test 2 Failed"
    # Test 3: Body too large (40%)
    assert is_bearish_shooting_star_candle(108.0, 112.0, 98.0, 100.0, 100.0, 102.0) is False, "Test 3 Failed"
    # Test 4: Lower wick too long (30%)
    assert is_bearish_shooting_star_candle(108.0, 112.0, 98.0, 101.0, 100.0, 102.0) is False, "Test 4 Failed"
    print("All tests passed ✅")


# ===================== ENTRY POINT =====================
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Exiting on user interrupt.")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)