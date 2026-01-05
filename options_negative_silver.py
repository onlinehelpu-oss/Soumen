# -*- coding: utf-8 -*-
"""
Red-ShootingStar / Red-Pinbar NEXT-candle first-touch breakout (RED candle only)

This file is the SHORT-only version of your scanner. It originally depended on the
`fyers_apiv3` package which may not be available in every environment. To make the
script robust and runnable (for testing and development) this version:

- Detects if `fyers_apiv3` is importable. If not, it falls back to lightweight
  **mock** classes that emulate minimal Fyers behaviour (place_order, quotes,
  WebSocket connect/subscribe), and automatically enables `--dry-run` mode.
- Adds a simple `--run-tests` flag that executes unit tests for the candle
  detector functions (no external dependencies required for the tests).
- Adds a `--dry-run` switch (auto-enabled if fyers package missing) so you can
  test execution without placing real orders.

Notes:
- This rewrite does NOT change your strategy logic. It only provides a safe
  fallback for missing external modules and adds tests for the detector.
- When you run in a real environment with `fyers_apiv3` installed, the script
  will use the real Fyers client and WebSocket as before (unless you pass
  --dry-run explicitly).
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

ws_connection = None

import pandas as pd
import requests

# Try to import real Fyers library; if missing, provide mocks and auto-enable dry-run
HAS_FYERS = True
try:
    from fyers_apiv3 import fyersModel
    from fyers_apiv3.FyersWebsocket import data_ws
except Exception as e:  # pragma: no cover - fallback path for sandboxed envs
    HAS_FYERS = False
    print("⚠️ fyers_apiv3 not available — running in dry-run mode with mocks. Install the real package to enable live trading.")

    class MockFyersModel:
        def __init__(self, client_id=None, token=None, log_path=None):
            self.client_id = client_id
            self.token = token
            self.log_path = log_path

        def place_order(self, payload):
            # Minimal emulation of placing an order — returns a fake OK response.
            now = dt.datetime.now().strftime("%Y%m%d%H%M%S")
            order_id = f"MOCKORD-{now}"
            print(f"[MOCK] place_order -> {payload} -> order_id={order_id}")
            return {"s": "ok", "order_id": order_id}

        def quotes(self, payload):
            # Return a synthetic quote dict similar to Fyers response.
            symbols = payload.get("symbols")
            val = 100.0
            return {"s": "ok", "d": [{"v": {"lp": val, "last_price": val}}]}

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
TIMEFRAME_MIN = 15       # change to 5 / 15 / 30 / 60 etc., or override with --tf
# NOTE: R_MULTIPLIER is the direct Risk:Reward multiple (e.g., 2.0 means target = entry - 2 * risk for shorts)
R_MULTIPLIER = 1.0       # default Risk:Reward (1:2)
LOT_MULTIPLIER = 1       # Number of lots to trade
EPS = 1e-6

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
SPOT_INDICES = [
    'NSE:NIFTY50-INDEX',
    'NSE:NIFTYBANK-INDEX',
    'NSE:FINNIFTY-INDEX'
]

# ===================== OPTION SETTINGS =====================
# For CE selling, a negative distance means ITM (In-the-Money)
# -1 = 1 strike ITM, -2 = 2 strikes ITM etc.
#  0 = ATM (At-the-Money)
# +1 = 1 strike OTM etc.
STRIKE_DISTANCE = 0

# ===================== OPTION HELPERS =====================

_lot_size_cache = {}
def get_lot_size(fy, symbol: str) -> int:
    """Get lot size for a symbol, with caching."""
    if symbol in _lot_size_cache:
        return _lot_size_cache[symbol]
    try:
        resp = fy.symbol_details({"symbol": symbol})
        if resp.get("s") == "ok" and resp.get("d"):
            lot_size = resp["d"][symbol].get("lot_size")
            if lot_size:
                _lot_size_cache[symbol] = int(lot_size)
                return int(lot_size)
    except Exception as e:
        print(f"⚠️ Lot size fetch error for {symbol}: {e}")
    # Default if API fails or no lot size in response
    return 1


def get_option_contract(fy, base_symbol: str, strike_dist: int) -> str:
    """
    Selects the desired ITM/OTM call option for the nearest expiry.
    Returns the full option symbol string (e.g., 'NSE:NIFTY24OCT...CE') or None.
    """
    underlying_ltp = get_ltp(fy, base_symbol)
    if underlying_ltp is None:
        print(f"[{dt.datetime.now():%H:%M:%S}] ⚠️ Could not get LTP for {base_symbol} to select option.")
        return None

    try:
        # Request a wide range of strikes to ensure we find the target
        payload = {"symbol": base_symbol, "strikecount": 12}
        chain = fy.optionchain(payload)
        if chain.get("s") != "ok" or not chain.get("data"):
            print(f"[{dt.datetime.now():%H:%M:%S}] ⚠️ Option chain fetch failed for {base_symbol}: {chain.get('message')}")
            return None

        # 1. Find the earliest expiry date from the chains
        expiries = sorted([opt['expiry'] for opt in chain["data"]], key=lambda d: dt.datetime.strptime(d, '%d%b%y'))
        if not expiries:
            return None
        nearest_expiry_str = expiries[0]

        # 2. Filter for that specific expiry and only CE contracts
        contracts = [
            c for c in chain["data"]
            if c.get("expiry") == nearest_expiry_str and c.get("symbol", "").endswith("CE")
        ]
        if not contracts:
            return None

        # 3. Sort by strike price to easily find ATM and apply distance
        contracts.sort(key=lambda x: x.get("strike_price", 0))
        strikes = [c["strike_price"] for c in contracts]

        # 4. Find ATM strike (closest to underlying LTP)
        atm_strike = min(strikes, key=lambda s: abs(s - underlying_ltp))
        atm_idx = strikes.index(atm_strike)

        # 5. Apply distance to get target strike index
        target_idx = atm_idx + strike_dist
        if not (0 <= target_idx < len(contracts)):
            print(f"[{dt.datetime.now():%H:%M:%S}] ⚠️ Strike distance {strike_dist} is out of bounds for {base_symbol}")
            return None

        selected_contract = contracts[target_idx]
        return selected_contract.get("symbol")

    except Exception as e:
        print(f"[{dt.datetime.now():%H:%M:%S}] 🚨 Option selection error for {base_symbol}: {e}")
        return None

# ===================== TIME/ENTRY/EXIT RULES =====================
# Default (non-MCX) behaviour
ENTRY_BUFFER = 0.05                # buffer below signal low for breakout (we require strict cross below)
ENTRY_CUTOFF = dt.time(15, 0)      # no new entries after 3:00 PM (non-MCX)
EXIT_ALL_TIME = dt.time(15, 9)     # force-exit all open (non-MCX) positions at 3:09 PM

FORCE_CLOSED_ALL = False

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
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

# ===================== LOGIN & TOKEN MGMT =====================
# (kept identical to original)

def load_creds():
    creds = _read_json(CONFIG_FILE)
    if not creds:
        raise SystemExit("❌ Missing 'fyers_login_details.json'. Create it with {api_key, api_secret, redirect_url}.")
    for k in ("api_key", "api_secret", "redirect_url"):
        if k not in creds or not creds[k]:
            raise SystemExit(f"❌ '{k}' missing in {CONFIG_FILE}.")
    return creds


def appid_hash(app_id: str, secret_id: str) -> str:
    return hashlib.sha256(f"{app_id}:{secret_id}".encode()).hexdigest()


def compose_access_token_string(app_id: str, access_token: str) -> str:
    if access_token.startswith(f"{app_id}:"):
        return access_token
    return f"{app_id}:{access_token}"


def build_auth_url(app_id: str, redirect_uri: str, state: str = "sample_state") -> str:
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
    if user_input.startswith("http://") or user_input.startswith("https://"):
        q = parse_qs(urlparse(user_input).query)
        code = q.get("code", [None])[0]
        if not code:
            raise ValueError("No 'code' param found in the provided URL.")
        return code
    return user_input


def post_json(url: str, payload: dict, max_retries: int = 5, timeout: int = 20):
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
                else:
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


def validate_authcode(app_id: str, secret_id: str, auth_code: str):
    url = f"{API_HOST}/api/v3/validate-authcode"
    payload = {"grant_type": "authorization_code","appIdHash": appid_hash(app_id, secret_id),"code": auth_code}
    return post_json(url, payload)


def validate_refresh_token(app_id: str, secret_id: str, refresh_token: str):
    url = f"{API_HOST}/api/v3/validate-refresh-token"
    payload = {"grant_type": "refresh_token","appIdHash": appid_hash(app_id, secret_id),"refresh_token": refresh_token}
    return post_json(url, payload)


def save_access_token_for_today(app_id: str, access_token: str):
    token_str = compose_access_token_string(app_id, access_token)
    _write_json(TODAY_PATH, token_str)
    return token_str


def ensure_access_token():
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

# ===================== CANDLE DETECTOR (Bearish Shooting Star) =====================

def is_bearish_shooting_star_candle(o, h, l, c, prev_o, prev_c, min_range_pct=0.0015):
    """
    RED shooting-star / pin-bar defined by strict percentage-based geometry.
    - Previous candle GREEN
    - Current candle RED
    - Geometry Constraints (as % of total candle range H-L):
      - Upper Wick: 55% - 90%
      - Body:       5% - 20%
      - Lower Wick: 0% - 12%
    """
    # --- Initial Filters ---
    if c == 0 or h <= l:
        return False

    total_range = h - l
    if (total_range / max(abs(c), 1e-9)) < min_range_pct:
        return False

    if prev_c <= prev_o:  # Previous candle must be green
        return False

    if c >= o:            # Current candle must be red
        return False

    # --- Strict Geometric Calculation ---
    # Calculate components as a percentage of the total candle range
    upper_wick_pct = ((h - o) / total_range) * 100
    body_pct = ((o - c) / total_range) * 100
    lower_wick_pct = ((c - l) / total_range) * 100

    # Check if all components are within the defined percentage boundaries
    is_valid_geometry = (
        (55 <= upper_wick_pct <= 90) and
        (5 <= body_pct <= 20) and
        (0 <= lower_wick_pct <= 12)
    )

    return is_valid_geometry


def flag_bearish_shooting_star(df: pd.DataFrame, min_range_pct=0.0015):
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    prev_o, prev_c = o.shift(1), c.shift(1)

    total_range = h - l
    # Avoid division by zero for zero-range candles
    total_range_safe = total_range.where(total_range > 0, 1e-9)

    upper_wick_pct = ((h - o) / total_range_safe) * 100
    body_pct = ((o - c) / total_range_safe) * 100
    lower_wick_pct = ((c - l) / total_range_safe) * 100

    cond_min_range = (total_range / c.abs().where(c != 0, 1e-9)) >= min_range_pct
    cond_prev_green = prev_c > prev_o
    cond_red = c < o
    cond_geom = (
        (upper_wick_pct >= 55) & (upper_wick_pct <= 90) &
        (body_pct >= 5) & (body_pct <= 20) &
        (lower_wick_pct >= 0) & (lower_wick_pct <= 12)
    )

    df["BearishShoot"] = cond_min_range & cond_prev_green & cond_red & cond_geom
    return df

# ===================== ORDER HELPERS =====================

def place_order(fy, sym: str, side: int, qty: int, tag: str, dry_run=False):
    payload = {
        "symbol": sym,
        "qty": int(qty),
        "type": 2,            # market
        "side": int(side),    # 1=buy, -1=sell
        "productType": "INTRADAY",
        "validity": "DAY",
        "orderTag": tag[:15] if tag else ""
    }
    if dry_run:
        print(f"[DRY-RUN] Would place order: {payload}")
        return {"s": "ok", "order_id": "DRYRUN"}

    try:
        resp = fy.place_order(payload)
        print(f"[{dt.datetime.now():%H:%M:%S}] 📌 {tag} {resp}")
        return resp
    except Exception as e:
        print(f"[{dt.datetime.now():%H:%M:%S}] 🚨 Order error {sym} {tag}: {e}")
        return {"s": "error", "message": str(e)}


def exit_short_by_buy_market(fy, sym: str, qty: int, dry_run=False):
    # to exit a short we BUY market (side=1)
    return place_order(fy, sym, side=1, qty=qty, tag="ExitShort", dry_run=dry_run)

# ===================== TRADE LOG & TRACKING =====================
active_trades = {}  # sym -> dict(entry, sl, tgt, qty, status, side)


def has_open_positions() -> bool:
    return any(v.get("status") == "open" for v in active_trades.values())


def save_trade(sym, entry, sl, tgt, qty, side=-1):
    # side=-1 means we opened a short (sell)
    row = {
        "Datetime": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Symbol": sym,
        "Entry Price": float(entry),
        "Stop Loss": float(sl),
        "Target": float(tgt),
        "Qty": int(qty),
        "Side": "SHORT" if side == -1 else "LONG"
    }
    pd.DataFrame([row]).to_csv(
        "trade_log.csv",
        mode='a',
        header=not os.path.exists("trade_log.csv"),
        index=False
    )
    active_trades[sym] = {"entry": entry, "sl": sl, "tgt": tgt, "qty": qty, "status": "open", "side": side}

# ===================== CANDLE BUILD STATE & LTP CACHE =====================
bars = {}
processed_candles = set()
trigger = {}

ltp_cache = {}       # symbol -> (ltp, ts)
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

# ===================== WEBSOCKET HANDLER (Option-Direct Signal & Trade) =====================

def make_onmsg(fy, dry_run=False):
    def onmsg(msg):
        if msg.get("type") != "sf":
            return

        try:
            sym = msg["symbol"] # This is now an OPTION symbol
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

        # build/extend the current bar for the OPTION
        bar = bars.get(key)
        if not bar:
            bars[key] = bar = {"o": ltp, "h": ltp, "l": ltp, "c": ltp}
        else:
            bar["h"] = max(bar["h"], ltp)
            bar["l"] = min(bar["l"], ltp)
            bar["c"] = ltp

        # when OPTION candle completes, check for signal
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
                    print(f"[{tick_time:%H:%M:%S}] 🎯 OPTION-SIG {sym} TF={TIMEFRAME_MIN}m → watch NEXT LOW {bar['l']} (SL {bar['h']})")

        # check active trigger for the OPTION
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

        now_time = dt.datetime.now().time()
        if now_time >= ENTRY_CUTOFF:
            print(f"[{dt.datetime.now():%H:%M:%S}] ⏰ Skipping NEW entry {sym} — cutoff passed ({ENTRY_CUTOFF})")
            trigger.pop(sym, None)
            return

        # breakout condition on the OPTION
        threshold = round_to_tick(t["low"] - ENTRY_BUFFER)
        prev_for_cross = prev_ltp_cache.get(sym)
        if (prev_for_cross is not None) and (prev_for_cross >= threshold) and (ltp < threshold):
            print(f"[{tick_time:%H:%M:%S}] 🔥 OPTION BREAKOUT {sym} < {threshold}. Placing trade...")

            # 1. Get lot size and calculate quantity
            lot_size = get_lot_size(fy, sym)
            qty = LOT_MULTIPLIER * lot_size

            # 2. Define risk based on the OPTION candle's range
            entry_price = floor_to_tick(ltp)
            sl_price = t["high"]
            risk = sl_price - entry_price
            if risk <= 0:
                print(f"[{tick_time:%H:%M:%S}] ✋ Risk <= 0 for {sym}, skipping.")
                trigger.pop(sym, None)
                return

            tgt_price = round_to_tick(entry_price - (R_MULTIPLIER * risk))

            # 3. Place order and save trade for the OPTION
            place_order(fy, sym, side=-1, qty=qty, tag="Opt-RedShootSell", dry_run=dry_run)
            save_trade(sym, entry_price, sl_price, tgt_price, qty, side=-1)

            t["triggered"] = True
            trigger.pop(sym, None)
            print(f"[{tick_time:%H:%M:%S}] ✅ SHORT-CE {sym} @ {entry_price}, SL={sl_price}, TGT={tgt_price}, QTY={qty}")

    return onmsg

# ===================== EXIT MONITOR (for SHORT positions) with FORCE-EXIT =====================

def monitor_loop(fy, dry_run=False):
    global FORCE_CLOSED_ALL
    while True:
        try:
            now_dt = dt.datetime.now()
            now_time = now_dt.time()

            # 1) Force-exit all open trades at or after EXIT_ALL_TIME (run once)
            if (not FORCE_CLOSED_ALL) and (now_time >= EXIT_ALL_TIME):
                open_trades = [s for s, t in active_trades.items() if t.get("status") == "open"]
                if open_trades:
                    print(f"[{now_dt:%H:%M:%S}] ⏳ EXIT_ALL triggered — closing {len(open_trades)} open trades")
                    for sym in open_trades:
                        trade = active_trades.get(sym)
                        if not trade:
                            continue
                        qty = trade.get("qty")
                        if not qty:
                            print(f"[{now_dt:%H:%M:%S}] ⚠️ Qty not found for {sym}, cannot force-exit.")
                            continue
                        try:
                            print(f"[{now_dt:%H:%M:%S}] 🔔 Force exiting {sym} (COVER BUY market) qty={qty}")
                            exit_short_by_buy_market(fy, sym, qty, dry_run=dry_run)
                        except Exception as e:
                            print(f"[{now_dt:%H:%M:%S}] ⚠️ Force-exit error for {sym}: {e}")
                        active_trades[sym]["status"] = "closed"
                        active_trades.pop(sym, None)
                    trigger.clear()
                else:
                    print(f"[{now_dt:%H:%M:%S}] ⏳ EXIT_ALL triggered but no open trades.")
                FORCE_CLOSED_ALL = True

            # 2) Normal SL/TGT monitoring for open option trades
            if active_trades:
                for sym in list(active_trades.keys()):
                    trade = active_trades.get(sym)
                    if not trade or trade["status"] != "open":
                        continue

                    ltp = get_ltp(fy, sym)
                    if ltp is None:
                        continue

                    sl = trade["sl"]
                    tgt = trade["tgt"]
                    qty = trade["qty"]
                    side = trade.get("side", -1)

                    # For short trades: SL is above, TGT is below
                    if side == -1:
                        if ltp >= sl:
                            print(f"[{dt.datetime.now():%H:%M:%S}] ❌ SL HIT {sym} @ {ltp} → BUY market to cover (exit)")
                            active_trades[sym]["status"] = "exiting"
                            exit_short_by_buy_market(fy, sym, qty, dry_run=dry_run)
                            active_trades[sym]["status"] = "closed"
                            active_trades.pop(sym, None)

                        elif ltp <= tgt:
                            print(f"[{dt.datetime.now():%H:%M:%S}] 🎯 TARGET HIT {sym} @ {ltp} → BUY market to cover (exit)")
                            active_trades[sym]["status"] = "exiting"
                            exit_short_by_buy_market(fy, sym, qty, dry_run=dry_run)
                            active_trades[sym]["status"] = "closed"
                            active_trades.pop(sym, None)

        except Exception as e:
            print(f"⚠️ Monitor loop error: {e}")

        time.sleep(1.5)

# ===================== MAIN =====================

def main():
    global TIMEFRAME_MIN, R_MULTIPLIER
    parser = argparse.ArgumentParser()
    parser.add_argument("--tf", type=int, default=TIMEFRAME_MIN, help="Timeframe in minutes (e.g., 5, 15, 60)")
    parser.add_argument("--rmult", type=float, default=R_MULTIPLIER, help="Risk:Reward multiple (e.g., 2.0 means target = entry - 2 * risk)")
    parser.add_argument("--dry-run", action="store_true", help="Enable dry-run: simulate orders instead of placing live ones")
    parser.add_argument("--run-tests", action="store_true", help="Run unit tests for detector logic and exit")
    args, _ = parser.parse_known_args()
    TIMEFRAME_MIN = max(1, int(args.tf))
    R_MULTIPLIER = float(args.rmult)

    dry_run = args.dry_run or (not HAS_FYERS)

    if args.run_tests:
        run_tests()
        return

    # If using real Fyers, ensure tokens; if using mocks, create a mock client
    if HAS_FYERS:
        app_id, token_str, raw_access = ensure_access_token()
        fy = fyersModel.FyersModel(client_id=app_id, token=raw_access, log_path=".")
    else:
        # Mock environment — create a mock client
        app_id = "MOCK_APP"
        token_str = "MOCK_TOKEN"
        raw_access = "MOCK_ACCESS"
        fy = fyersModel.FyersModel(client_id=app_id, token=raw_access, log_path=".")

    # --- Dynamic Watchlist Creation ---
    print("Building dynamic watchlist of ITM options...")
    dynamic_watchlist = []
    for index_sym in SPOT_INDICES:
        option_sym = get_option_contract(fy, index_sym, STRIKE_DISTANCE)
        if option_sym:
            dynamic_watchlist.append(option_sym)
            print(f"  ✅ Added {option_sym} for {index_sym}")
        else:
            print(f"  ⚠️ Could not find suitable option for {index_sym}, it will be skipped.")
        time.sleep(0.5) # Avoid hitting API rate limits

    if not dynamic_watchlist:
        raise SystemExit("❌ No option contracts could be found for the given indices. Exiting.")

    # WebSocket uses token_str
    on_message = make_onmsg(fy, dry_run=dry_run)

    # Assign to global ws_connection so onmsg can dynamically subscribe
    global ws_connection
    ws_connection = data_ws.FyersDataSocket(
        access_token=token_str,
        log_path=".",
        litemode=False,
        write_to_file=False,
        reconnect=True,
        on_message=on_message,
        on_error=lambda m: print("🚨", m),
        on_close=lambda m: print("❌", m),
        on_connect=lambda: (
            print(f"🔌 Connected → subscribing to {len(dynamic_watchlist)} selected option contracts.") or
            ws_connection.subscribe(symbols=dynamic_watchlist)
        )
    )

    # Start exit monitor
    threading.Thread(target=monitor_loop, args=(fy, dry_run), daemon=True).start()

    print("\n========== Red-ShootingStar/PINBAR SHORT Scanner (Strict Breakout + One-Position Mode) ==========")
    print(f"🧩 Python: {sys.version.split()[0]}  |  Symbols: {len(dynamic_watchlist)} | TF={TIMEFRAME_MIN}m | Rmult={R_MULTIPLIER} | dry_run={dry_run}")
    print("🚀 Real-time SHORT scanner started …\n")

    ws_connection.connect()

# ===================== SIMPLE UNIT TESTS FOR DETECTOR =====================

def run_tests():
    print("Running tests for bearish shooting-star detector...")

    # Test 1: Valid shooting star with perfect geometry
    # Total Range = 10 (110-100)
    # Upper Wick = 8 (110-102) -> 80%
    # Body = 1.5 (102-100.5) -> 15%
    # Lower Wick = 0.5 (100.5-100) -> 5%
    assert is_bearish_shooting_star_candle(102.0, 110.0, 100.0, 100.5, 98.0, 100.0) is True, "Test 1 Failed"

    # Test 2: Fails because upper wick is too short (50%)
    assert is_bearish_shooting_star_candle(107.0, 112.0, 102.0, 105.0, 100.0, 102.0) is False, "Test 2 Failed"

    # Test 3: Fails because body is too large (30%)
    assert is_bearish_shooting_star_candle(108.0, 110.0, 98.0, 105.0, 100.0, 102.0) is False, "Test 3 Failed"

    # Test 4: Fails because lower wick is too long (20%)
    assert is_bearish_shooting_star_candle(108.0, 110.0, 98.0, 107.0, 100.0, 102.0) is False, "Test 4 Failed"

    # DataFrame flagging test with a valid case
    data = {
        'Open': [98.0, 102.0],
        'High': [100.0, 110.0],
        'Low': [97.0, 100.0],
        'Close': [100.0, 100.5]
    }
    df = pd.DataFrame(data)
    df = flag_bearish_shooting_star(df)
    assert df['BearishShoot'].iloc[1] == True, "DataFrame test failed"

    print("All tests passed ✅")

# ===================== ENTRY POINT =====================
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Exiting on user interrupt.")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        sys.exit(1)
