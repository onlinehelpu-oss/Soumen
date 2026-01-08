# -*- coding: utf-8 -*-
"""
Red-ShootingStar / Red-Pinbar NEXT-candle first-touch breakout (RED candle only)
FOR NSE STOCKS & MCX COMMODITIES
UPDATED: More realistic shooting star geometry (50-80% upper wick, 5-30% body, 0-25% lower wick)
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
            return {"s": "ok", "order_id": order_id, "id": order_id, "code": 1101} # Added code for success check

        def quotes(self, payload):
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


    fyersModel = type("fyersModel", (), {"FyersModel": MockFyersModel})
    data_ws = type("data_ws", (), {"FyersDataSocket": MockDataSocket})

# ===================== STRATEGY SETTINGS =====================
TIMEFRAME_MIN = 15  # change to 5 / 15 / 30 / 60 etc., or override with --tf
R_MULTIPLIER = 1.0  # default Risk:Reward (1:1)
LOT_MULTIPLIER = 1  # Number of lots (for futures) or shares (for stocks) to trade
EPS = 1e-6

# ===================== CANDLE GEOMETRY SETTINGS =====================
UPPER_WICK_MIN = 50
UPPER_WICK_MAX = 80
BODY_MIN = 5
BODY_MAX = 30
LOWER_WICK_MAX = 25

# One-position-at-a-time control
ONE_POSITION_AT_A_TIME = True

# Tick setup
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
TODAY = dt.date.today()
TODAY_PATH = os.path.join(TOKENS_DIR, f"{TODAY}.json")
TOKENS_STORE = "tokens_store.json"

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

# ===================== LOT SIZE MANAGEMENT =====================
lot_cache = {} # Cache for symbol lot sizes

def get_lot_size(symbol: str) -> int:
    """Fetch real-time lot size from Fyers Symbol Master CSVs."""
    if symbol in lot_cache:
        return lot_cache[symbol]

    # For Equity, lot size is always 1
    if symbol.endswith('-EQ'):
        lot_cache[symbol] = 1
        return 1

    if not HAS_FYERS:
        print(f"⚠️ Mock mode: Using fallback lot size 1 for {symbol}")
        return 1

    try:
        exchange, _ = symbol.split(':', 1)
        if exchange == 'NSE':
            url = 'https://public.fyers.in/sym_details/NSE_FO.csv'
        elif exchange == 'BSE':
            url = 'https://public.fyers.in/sym_details/BSE_FO.csv'
        elif exchange == 'MCX':
            url = 'https://public.fyers.in/sym_details/MCX_COM.csv'
        else:
            print(f"⚠️ Unknown exchange {exchange} for {symbol}, using fallback 1")
            return 1

        print(f"📡 Fetching lot size for {symbol} from {url}...")
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            print(f"⚠️ Failed to fetch master CSV: {resp.status_code}, using fallback")
            lot_size = 1
        else:
            # Read CSV without headers (positional columns)
            df = pd.read_csv(io.StringIO(resp.text), header=None)
            # Fyers symbol is in column 9 (0-indexed), Lot Size in column 3
            symbol_col = 9
            lot_col = 3
            matching_row = df[df.iloc[:, symbol_col] == symbol]
            if not matching_row.empty:
                lot_size = int(matching_row.iloc[0, lot_col])
                print(f"✅ Fetched lot size {lot_size} for {symbol}")
            else:
                print(f"⚠️ Symbol {symbol} not found in master, using fallback 1")
                lot_size = 1

        lot_cache[symbol] = lot_size
        return lot_size

    except Exception as e:
        print(f"❌ Error fetching lot size for {symbol}: {e}, using fallback 1")
        return 1


# ===================== TIME/ENTRY/EXIT RULES =====================
ENTRY_BUFFER = 0.05  # buffer below signal low for breakout
ENTRY_CUTOFF_NSE = dt.time(15, 0)
ENTRY_CUTOFF_MCX = dt.time(22, 0)
EXIT_ALL_TIME_NSE = dt.time(15, 9)
EXIT_ALL_TIME_MCX = dt.time(23, 0)
FORCE_CLOSED_ALL = False

# ===================== SMALL CANDLE GUARDS =====================
MIN_RANGE_PCT = 0.0015  # ignore if (H-L)/Close < 0.15%

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
def load_creds():
    creds = _read_json(CONFIG_FILE)
    if not creds:
        raise SystemExit("❌ Missing 'fyers_login_details.json'. Create it with {api_key, api_secret, redirect_url}.")
    for k in ("api_key", "api_secret", "redirect_url"):
        if k not in creds or not creds[k]:
            raise SystemExit(f"❌ '{k}' missing in {CONFIG_FILE}.")
    return creds


def ensure_access_token():
    creds = load_creds()
    client_id = creds["api_key"]
    secret_key = creds["api_secret"]
    redirect_uri = creds["redirect_url"]
    if os.path.exists(TODAY_PATH):
        access_token = _read_json(TODAY_PATH)
        if access_token and isinstance(access_token, str):
            print("🔑 Using today's cached access token.")
            return client_id, access_token
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
            if os.path.exists(TOKENS_STORE):
                _write_json(TOKENS_STORE, {})
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


# ===================== CANDLE DETECTOR (Bearish Shooting Star) =====================
def is_bearish_shooting_star_candle(o, h, l, c, prev_o, prev_c, min_range_pct=0.0015):
    if c >= o: return False
    if prev_c <= prev_o: return False
    if c == 0 or h <= l: return False
    total_range = h - l
    if (total_range / max(abs(c), 1e-9)) < min_range_pct: return False
    upper_wick_pct = ((h - o) / total_range) * 100
    body_pct = ((o - c) / total_range) * 100
    lower_wick_pct = ((c - l) / total_range) * 100
    return (
        (UPPER_WICK_MIN <= upper_wick_pct <= UPPER_WICK_MAX) and
        (BODY_MIN <= body_pct <= BODY_MAX) and
        (0 <= lower_wick_pct <= LOWER_WICK_MAX)
    )

# ===================== ORDER HELPERS =====================
def place_order(fy, sym: str, side: int, qty: int, tag: str, dry_run=False) -> Dict:
    clean_tag = re.sub(r'[^a-zA-Z0-9]', '', tag)
    product_type = "INTRADAY"
    if sym.startswith("MCX:"):
        product_type = "MARGIN" # or NRML depending on user preference

    payload = {
        "symbol": sym,
        "qty": int(qty),
        "type": 2,  # market
        "side": int(side),  # 1=buy, -1=sell
        "productType": product_type,
        "validity": "DAY",
        "orderTag": clean_tag[:15] if clean_tag else ""
    }

    if dry_run:
        print(f"[DRY-RUN] Would place order: {payload}")
        return {"s": "ok", "order_id": "DRYRUN", "id": "DRYRUN_ID", "code": 1101}

    try:
        resp = fy.place_order(payload)
        if resp.get('s') == 'ok' and resp.get('code') == 1101:
            print(f"[{dt.datetime.now():%H:%M:%S}] ✅ ORDER EXECUTED {tag}: {sym} {side} {qty} shares")
        elif resp.get('s') == 'error':
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


def exit_short_by_buy_market(fy, sym: str, qty_shares: int, dry_run=False):
    return place_order(fy, sym, side=1, qty=qty_shares, tag="ExitShort", dry_run=dry_run)


# ===================== TRADE LOG & TRACKING =====================
active_trades = {}  # sym -> dict(entry, sl, tgt, qty, status, side, lot_size, order_id)

def has_open_positions() -> bool:
    return any(v.get("status") == "open" for v in active_trades.values())

def save_trade(sym, entry, sl, tgt, qty_lots, lot_size, side=-1, order_id=""):
    row = {
        "Datetime": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Symbol": sym,
        "Entry Price": float(entry),
        "Stop Loss": float(sl),
        "Target": float(tgt),
        "Qty Lots/Shares": int(qty_lots),
        "Lot Size": int(lot_size),
        "Total Shares": int(qty_lots * lot_size),
        "Side": "SHORT" if side == -1 else "LONG",
        "Order ID": order_id
    }
    pd.DataFrame([row]).to_csv(
        "trade_log.csv",
        mode='a',
        header=not os.path.exists("trade_log.csv"),
        index=False
    )
    active_trades[sym] = {
        "entry": entry, "sl": sl, "tgt": tgt,
        "qty_shares": int(qty_lots * lot_size),
        "status": "open", "side": side,
        "order_id": order_id,
        "order_placed_successfully": True if order_id else False
    }

# ===================== CANDLE BUILD STATE & LTP CACHE =====================
bars = {}
processed_candles = set()
trigger = {}
ltp_cache = {}
prev_ltp_cache = {}
_last_quote_error = {}
ERROR_THROTTLE_SECS = 10

def candle_start(t: dt.datetime) -> dt.datetime:
    return t.replace(second=0, microsecond=0) - dt.timedelta(minutes=t.minute % TIMEFRAME_MIN)


# ===================== SAFE QUOTES (cache-first, REST fallback) =====================
def get_ltp(fy, sym, cache_ttl=10, max_retries=3):
    now = time.time()
    cached = ltp_cache.get(sym)
    if cached and (now - cached[1]) <= cache_ttl:
        return float(cached[0])

    for attempt in range(1, max_retries + 1):
        try:
            q = fy.quotes({"symbols": sym})
            if q.get("s") == "ok" and q.get("d"):
                v = q["d"][0].get("v", {})
                ltp = v.get("lp") or v.get("last_price")
                if ltp is not None:
                    ltp_cache[sym] = (float(ltp), time.time())
                    return float(ltp)
            # Throttled error logging
            last_err_ts = _last_quote_error.get(sym, 0)
            if now - last_err_ts > ERROR_THROTTLE_SECS:
                print(f"⚠️ Quote fetch failed {sym}: {q}")
                _last_quote_error[sym] = now
            time.sleep(1.0 * attempt)
        except Exception as e:
            last_err_ts = _last_quote_error.get(sym, 0)
            if now - last_err_ts > ERROR_THROTTLE_SECS:
                print(f"⚠️ Quote fetch exception {sym}: {e}")
                _last_quote_error[sym] = now
            time.sleep(1.0 * attempt)

    return ltp_cache.get(sym, (None, None))[0]


# ===================== WEBSOCKET HANDLER =====================
def make_onmsg(fy, dry_run=False):
    def onmsg(msg):
        if msg.get("type") != "sf": return
        try:
            sym = msg["symbol"]
            ltp = float(msg["ltp"])
            ts = int(msg.get("timestamp", time.time()))
        except Exception: return

        prev_ltp = ltp_cache.get(sym, (None, None))[0]
        if prev_ltp is not None:
            prev_ltp_cache[sym] = float(prev_ltp)
        ltp_cache[sym] = (ltp, time.time())

        tick_time = dt.datetime.fromtimestamp(ts)
        cstart = candle_start(tick_time)
        key = (sym, cstart)

        bar = bars.get(key)
        if not bar: bars[key] = bar = {"o": ltp, "h": ltp, "l": ltp, "c": ltp}
        else:
            bar["h"] = max(bar["h"], ltp)
            bar["l"] = min(bar["l"], ltp)
            bar["c"] = ltp

        if tick_time >= cstart + dt.timedelta(minutes=TIMEFRAME_MIN) - dt.timedelta(seconds=1):
            if key not in processed_candles:
                processed_candles.add(key)
                prev_cstart = cstart - dt.timedelta(minutes=TIMEFRAME_MIN)
                prev_bar = bars.get((sym, prev_cstart))

                if ONE_POSITION_AT_A_TIME and has_open_positions(): return

                if prev_bar and is_bearish_shooting_star_candle(
                        bar["o"], bar["h"], bar["l"], bar["c"],
                        prev_bar["o"], prev_bar["c"],
                        min_range_pct=MIN_RANGE_PCT
                ):
                    next_cstart = cstart + dt.timedelta(minutes=TIMEFRAME_MIN)
                    trigger[sym] = {
                        "low": bar["l"], "high": bar["h"],
                        "active_start": next_cstart, "triggered": False,
                    }
                    print(f"[{tick_time:%H:%M:%S}] 🎯 SIGNAL {sym} TF={TIMEFRAME_MIN}m → watch NEXT LOW {bar['l']:.2f} (SL {bar['h']:.2f})")
                    total_range = bar["h"] - bar["l"]
                    if total_range > 0:
                        upper_pct = ((bar["h"] - bar["o"]) / total_range) * 100
                        body_pct = ((bar["o"] - bar["c"]) / total_range) * 100
                        lower_pct = ((bar["c"] - bar["l"]) / total_range) * 100
                        print(f"[{tick_time:%H:%M:%S}] 📊 Candle Geometry: U={upper_pct:.1f}%, B={body_pct:.1f}%, L={lower_pct:.1f}%")

        t = trigger.get(sym)
        if not t: return

        if tick_time >= t["active_start"] + dt.timedelta(minutes=TIMEFRAME_MIN):
            trigger.pop(sym, None)
            return

        if tick_time < t["active_start"] or t["triggered"]: return

        if ONE_POSITION_AT_A_TIME and has_open_positions():
            print(f"[{dt.datetime.now():%H:%M:%S}] 🚫 Skipping {sym} entry — position already open.")
            trigger.pop(sym, None)
            return

        now_time = dt.datetime.now().time()
        is_mcx = sym.startswith("MCX:")
        entry_cutoff = ENTRY_CUTOFF_MCX if is_mcx else ENTRY_CUTOFF_NSE
        if now_time >= entry_cutoff:
            print(f"[{dt.datetime.now():%H:%M:%S}] ⏰ Skipping NEW entry {sym} — cutoff passed ({entry_cutoff})")
            trigger.pop(sym, None)
            return

        threshold = round_to_tick(t["low"] - ENTRY_BUFFER)
        prev_for_cross = prev_ltp_cache.get(sym)
        if (prev_for_cross is not None) and (prev_for_cross >= threshold) and (ltp < threshold):
            print(f"[{tick_time:%H:%M:%S}] 🔥 BREAKOUT {sym} < {threshold:.2f}. Placing trade...")
            t["triggered"] = True

            lot_size = get_lot_size(sym)
            if lot_size == 0:
                print(f"[{tick_time:%H:%M:%S}] ✋ Could not determine lot size for {sym}, skipping trade.")
                trigger.pop(sym, None)
                return

            qty_lots_or_shares = LOT_MULTIPLIER
            qty_shares = qty_lots_or_shares * lot_size

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
                save_trade(sym, entry_price, sl_price, tgt_price, qty_lots_or_shares, lot_size, side=-1, order_id=order_id)
                print(f"[{tick_time:%H:%M:%S}] ✅ SHORT {sym} @ {entry_price:.2f}, SL={sl_price:.2f}, TGT={tgt_price:.2f}, QTY={qty_lots_or_shares} lots/shares ({qty_shares} total), Lot Size={lot_size}")
            else:
                print(f"[{tick_time:%H:%M:%S}] ❌ Order NOT placed for {sym}, cleaning trigger...")

            trigger.pop(sym, None)

    return onmsg

# ===================== EXIT MONITOR =====================
def monitor_loop(fy, dry_run=False):
    global FORCE_CLOSED_ALL
    while True:
        try:
            now_dt = dt.datetime.now()
            now_time = now_dt.time()

            if not FORCE_CLOSED_ALL:
                open_trades_nse = [s for s, t in active_trades.items() if t.get("status") == "open" and s.startswith("NSE:")]
                open_trades_mcx = [s for s, t in active_trades.items() if t.get("status") == "open" and s.startswith("MCX:")]

                if now_time >= EXIT_ALL_TIME_NSE and open_trades_nse:
                    print(f"[{now_dt:%H:%M:%S}] ⏳ NSE EXIT_ALL triggered — closing {len(open_trades_nse)} trades")
                    for sym in open_trades_nse:
                        trade = active_trades.get(sym)
                        if trade:
                           exit_short_by_buy_market(fy, sym, trade['qty_shares'], dry_run=dry_run)
                           active_trades.pop(sym, None)

                if now_time >= EXIT_ALL_TIME_MCX and open_trades_mcx:
                    print(f"[{now_dt:%H:%M:%S}] ⏳ MCX EXIT_ALL triggered — closing {len(open_trades_mcx)} trades")
                    for sym in open_trades_mcx:
                        trade = active_trades.get(sym)
                        if trade:
                           exit_short_by_buy_market(fy, sym, trade['qty_shares'], dry_run=dry_run)
                           active_trades.pop(sym, None)

                if (now_time >= EXIT_ALL_TIME_NSE and not open_trades_nse) and (now_time >= EXIT_ALL_TIME_MCX and not open_trades_mcx):
                     FORCE_CLOSED_ALL = True


            for sym in list(active_trades.keys()):
                trade = active_trades.get(sym)
                if not trade or trade["status"] != "open" or not trade.get("order_placed_successfully", False):
                    if not trade.get("order_placed_successfully", False):
                        active_trades.pop(sym, None) # Clean up failed orders
                    continue

                ltp = get_ltp(fy, sym)
                if ltp is None: continue

                sl, tgt = trade["sl"], trade["tgt"]
                qty_shares = trade["qty_shares"]

                if trade.get("side", -1) == -1: # SHORT position
                    if ltp >= sl:
                        print(f"[{dt.datetime.now():%H:%M:%S}] ❌ SL HIT {sym} @ {ltp:.2f} → BUY market")
                        exit_short_by_buy_market(fy, sym, qty_shares, dry_run=dry_run)
                        active_trades.pop(sym, None)
                    elif ltp <= tgt:
                        print(f"[{dt.datetime.now():%H:%M:%S}] 🎯 TARGET HIT {sym} @ {ltp:.2f} → BUY market")
                        exit_short_by_buy_market(fy, sym, qty_shares, dry_run=dry_run)
                        active_trades.pop(sym, None)
        except Exception as e:
            print(f"⚠️ Monitor loop error: {e}")
        time.sleep(1.5)

# ===================== MAIN =====================
def main():
    global TIMEFRAME_MIN, R_MULTIPLIER
    parser = argparse.ArgumentParser()
    parser.add_argument("--tf", type=int, default=TIMEFRAME_MIN, help="Timeframe in minutes (e.g., 5, 15, 60)")
    parser.add_argument("--rmult", type=float, default=R_MULTIPLIER, help="Risk:Reward multiple")
    parser.add_argument("--dry-run", action="store_true", help="Enable dry-run mode")
    parser.add_argument("--run-tests", action="store_true", help="Run unit tests and exit")

    args, _ = parser.parse_known_args()
    TIMEFRAME_MIN = max(1, int(args.tf))
    R_MULTIPLIER = float(args.rmult)
    dry_run = args.dry_run or not HAS_FYERS

    if args.run_tests:
        run_tests()
        return

    if HAS_FYERS:
        client_id, access_token = ensure_access_token()
        fy = fyersModel.FyersModel(client_id=client_id, token=access_token, log_path=".")
    else:
        client_id, access_token = "MOCK_APP", "MOCK_TOKEN"
        fy = fyersModel.FyersModel(client_id=client_id, token=access_token, log_path=".")

    print("\n" + "=" * 60)
    print("🎯 BUILDING SYMBOL WATCHLIST...")
    # Pre-fetch lot sizes
    for sym in SYMBOLS:
        get_lot_size(sym) # This will fetch and cache
    print("✅ Lot sizes cached.")
    print("=" * 60)

    global ws_connection
    on_message = make_onmsg(fy, dry_run=dry_run)
    ws_connection = data_ws.FyersDataSocket(
        access_token=f"{client_id}:{access_token}",
        log_path=".", litemode=False, write_to_file=False, reconnect=True,
        on_message=on_message,
        on_error=lambda m: print("🚨", m),
        on_close=lambda m: print("❌", m),
        on_connect=lambda: (
            print(f"🔌 Connected → subscribing to {len(SYMBOLS)} symbols.") or
            ws_connection.subscribe(symbols=SYMBOLS)
        )
    )

    threading.Thread(target=monitor_loop, args=(fy, dry_run), daemon=True).start()

    print("\n" + "=" * 70)
    print("🎯 RED-SHOOTING STAR STRATEGY - STOCKS & MCX")
    print("=" * 70)
    print(f"📊 LOT/SHARE MULTIPLIER: {LOT_MULTIPLIER} per trade")
    print(f"📊 CANDLE GEOMETRY:")
    print(f"   Upper Wick: {UPPER_WICK_MIN}-{UPPER_WICK_MAX}%")
    print(f"   Body: {BODY_MIN}-{BODY_MAX}%")
    print(f"   Lower Wick: 0-{LOWER_WICK_MAX}%")
    print("=" * 70)
    print(f"🧩 Python: {sys.version.split()[0]} | Symbols: {len(SYMBOLS)}")
    print(f"📈 TF={TIMEFRAME_MIN}m | Rmult={R_MULTIPLIER} | dry_run={dry_run}")
    print("=" * 70)
    print("🚀 Real-time SHORT scanner started …\n")
    ws_connection.connect()

# ===================== SIMPLE UNIT TESTS FOR DETECTOR =====================
def run_tests():
    print("Running tests for bearish shooting-star detector...")
    assert is_bearish_shooting_star_candle(100.0, 112.0, 90.0, 95.5, 95.0, 98.0) is True, "Test 1 Failed"
    assert is_bearish_shooting_star_candle(105.0, 109.0, 95.0, 102.0, 100.0, 102.0) is False, "Test 2 Failed"
    assert is_bearish_shooting_star_candle(108.0, 112.0, 98.0, 100.0, 100.0, 102.0) is False, "Test 3 Failed"
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
