# -*- coding: utf-8 -*-
"""
Green-Hammer / Green-Pinbar Strategy for NSE Stocks & MCX Futures (BUY)

This script identifies the "Green Hammer" candlestick pattern on a given
watchlist of NSE equities and MCX futures. It then enters a LONG position on
the breakout of the signal candle's high on the next candle.

Features:
- Trades a mixed watchlist of NSE stocks and MCX futures.
- Implements separate trading hours for NSE and MCX.
- Uses hardcoded lot sizes for MCX futures for reliability.
- Supports carry-forward (CNC/MARGIN) positions via --carry flag.
- Syncs with broker positions to handle manual closures.
- Includes a dry-run mode for testing without placing live orders.
"""
import os
import sys
import json
import time
import math
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
            return {"s": "ok", "d": [{"v": {"lp": val, "last_price": val, "low_price": 90.0}}]}

        def symbol_details(self, payload):
            return {"s": "ok", "d": {payload.get("symbol"): {"lot_size": 65}}}

        def positions(self):
            print("[MOCK] positions() -> returning empty list")
            return {"s": "ok", "netPositions": []}

        def history(self, data=None):
            return {
                "s": "ok",
                "candles": [[1600000000 + i * 60, 100, 105, 95, 100, 1000] for i in range(200)]
            }


    class MockDataSocket:
        def __init__(self, access_token=None, log_path=None, litemode=False, write_to_file=False, reconnect=True,
                     on_message=None, on_error=None, on_close=None, on_connect=None):
            self.access_token = access_token
            self._on_message = on_message
            self._on_error = on_error
            self._on_close = on_close
            self._on_connect = on_connect

        def subscribe(self, symbols=None, data_type="SymbolUpdate"):
            print(f"[MOCK] Subscribed to {len(symbols or [])} symbols (data_type={data_type})")

        def connect(self):
            print("[MOCK] WebSocket connect() called")
            if callable(self._on_connect):
                try: self._on_connect()
                except Exception as e:
                    if callable(self._on_error): self._on_error(e)

        def close(self):
            print("[MOCK] WebSocket close() called")
            if callable(self._on_close): self._on_close(None)

    fyersModel = type("fyersModel", (), {"FyersModel": MockFyersModel})
    data_ws = type("data_ws", (), {"FyersDataSocket": MockDataSocket})

# ===================== STRATEGY SETTINGS =====================
TIMEFRAME_MIN = 5
R_MULTIPLIER = 1.0
LOT_MULTIPLIER = 1
MCX_LOT_MULTIPLIER = 1
REGIME_EMA_PERIOD = 26
EPS = 1e-6

# ===================== CANDLE GEOMETRY SETTINGS (HAMMER) =====================
# Bullish Hammer / Pinbar Logic:
# - Color: GREEN (Close > Open)
# - Lower Wick: 50-80% of Range (Long rejection from bottom)
# - Body: 5-30% of Range (Small body)
# - Upper Wick: 0-25% of Range (Little to no upper shadow)
LOWER_WICK_MIN = 50
LOWER_WICK_MAX = 80
BODY_MIN = 5
BODY_MAX = 30
UPPER_WICK_MAX = 25

ONE_POSITION_AT_A_TIME = True
TICK_SIZE = 0.05

def round_to_tick(x, tick=TICK_SIZE): return round(round(x / tick) * tick, 2)
def ceil_to_tick(x, tick=TICK_SIZE):
    k = math.floor(x / tick)
    if abs(x - k * tick) < 1e-12: return round(x, 2)
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

MCX_LOTS = {"SILVERMIC": 1, "CRUDEOILM": 1, "NATGASMINI": 1}

def get_lot_size(symbol: str) -> int:
    if symbol.endswith("-EQ"): return 1
    base = symbol.split(':')[1]
    for mcx_base, lot in MCX_LOTS.items():
        if base.startswith(mcx_base): return lot
    return 1

# ===================== TIME/ENTRY/EXIT RULES =====================
ENTRY_BUFFER = 0.05
ENTRY_CUTOFF = dt.time(15, 0)
EXIT_ALL_TIME = dt.time(15, 9)
ENTRY_CUTOFF_MCX = dt.time(22, 0)
EXIT_ALL_TIME_MCX = dt.time(22, 50)

FORCE_CLOSED_ALL = False
FORCE_CLOSED_ALL_MCX = False

# Product Defaults
STOCK_PRODUCT_TYPE = "INTRADAY"
MCX_PRODUCT_TYPE = "MARGIN"
CARRY_FORWARD = False # If True, disables auto-exit and uses CNC/MARGIN

POSITION_MODE = "qty"
ALLOCATION_AMOUNT = 100000
FIXED_QTY = 1
QTY_MAP: Dict[str, int] = {}

MIN_RANGE_PCT = 0.0015

# ===================== IO HELPERS =====================
def _read_json(path, default=None):
    try:
        with open(path, 'r') as f: return json.load(f)
    except: return default
def _write_json(path, data):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w') as f: json.dump(data, f, indent=2)

# ===================== STATE MANAGEMENT =====================
active_trades = {}

def save_state():
    try:
        _write_json("active_trades_buy.json", active_trades)
        print(f"[{dt.datetime.now():%H:%M:%S}] ✅ State saved.")
    except Exception as e: print(f"❌ Error saving state: {e}")

def load_state():
    global active_trades
    data = _read_json("active_trades_buy.json")
    if data:
        active_trades = data
        print(f"[{dt.datetime.now():%H:%M:%S}] ✅ State loaded. {len(active_trades)} trades restored.")

# ===================== LOGIN =====================
def load_creds():
    creds = _read_json(CONFIG_FILE)
    if not creds: raise SystemExit("❌ Missing credentials.")
    return creds

def ensure_access_token():
    creds = load_creds()
    client_id = creds["api_key"]
    if os.path.exists(TODAY_PATH):
        print("🔑 Using today's cached access token.")
        return client_id, _read_json(TODAY_PATH)
    # Simplified login - assuming token generation handled by main script usually
    # but providing full interactive fallback just in case
    session = fyersModel.SessionModel(
        client_id=client_id, secret_key=creds["api_secret"],
        redirect_uri=creds["redirect_url"], response_type="code", grant_type="authorization_code"
    )
    url = session.generate_authcode()
    print(f"\n👉 Login URL: {url}")
    webbrowser.open(url, new=1)
    code = input("Paste auth_code: ").strip()
    session.set_token(code)
    resp = session.generate_token()
    if resp.get("s") == "ok":
        _write_json(TODAY_PATH, resp["access_token"])
        return client_id, resp["access_token"]
    raise SystemExit(f"❌ Login failed: {resp}")

# ===================== CANDLE DETECTOR (Bullish Hammer) =====================
def is_bullish_hammer_candle(o, h, l, c, prev_o, prev_c, min_range_pct=0.0015):
    """
    Logic for GREEN Hammer:
    - Previous candle RED (prev_c < prev_o)
    - Current candle GREEN (c > o)
    - Long Lower Wick (50-80% of range)
    - Small Body (5-30% of range) at Top
    - Tiny Upper Wick (0-25% of range)
    """
    if c <= o: return False # Must be Green
    if prev_c >= prev_o: return False # Previous must be Red

    total_range = h - l
    if total_range == 0: return False
    if (total_range / max(abs(c), 1e-9)) < min_range_pct: return False

    lower_wick_pct = ((o - l) / total_range) * 100
    body_pct = ((c - o) / total_range) * 100
    upper_wick_pct = ((h - c) / total_range) * 100

    return (
        (LOWER_WICK_MIN <= lower_wick_pct <= LOWER_WICK_MAX) and
        (BODY_MIN <= body_pct <= BODY_MAX) and
        (0 <= upper_wick_pct <= UPPER_WICK_MAX)
    )

# ===================== ORDER HELPERS =====================
def place_order(fy, sym, side, qty, tag, dry_run=False):
    clean_tag = re.sub(r'[^a-zA-Z0-9]', '', tag)
    # Determine product type
    if sym.startswith("MCX:"):
        prod = MCX_PRODUCT_TYPE
    else:
        prod = STOCK_PRODUCT_TYPE

    payload = {
        "symbol": sym, "qty": int(qty), "type": 2, "side": int(side),
        "productType": prod, "validity": "DAY", "orderTag": clean_tag[:15]
    }
    if dry_run:
        print(f"[DRY] Would place: {payload}")
        return {"s": "ok", "order_id": "DRY"}
    try:
        resp = fy.place_order(payload)
        if resp.get("s")=="ok":
            print(f"✅ ORDER PLACED {sym} {side} {qty}")
        else:
            print(f"❌ ORDER FAILED {sym}: {resp}")
        return resp
    except Exception as e:
        print(f"🚨 ORDER ERROR {sym}: {e}")
        return {"s": "error"}

def exit_long_by_sell_market(fy, sym, qty_lots, lot_size, dry_run=False):
    # Exit Long = Sell (side = -1)
    qty_shares = qty_lots * lot_size
    return place_order(fy, sym, side=-1, qty=qty_shares, tag="ExitLong", dry_run=dry_run)

def save_trade(sym, entry, sl, tgt, qty_lots, lot_size=1, order_id=""):
    if sym.startswith("MCX:"): prod = MCX_PRODUCT_TYPE
    else: prod = STOCK_PRODUCT_TYPE

    active_trades[sym] = {
        "entry": entry, "sl": sl, "tgt": tgt, "qty": qty_lots,
        "status": "open", "side": 1, "lot_size": lot_size,
        "order_id": order_id, "order_placed_successfully": True, "productType": prod
    }
    save_state()

# ===================== DATA & CACHES =====================
bars = {}
processed_candles = set()
trigger = {}
ltp_cache = {}
prev_ltp_cache = {}
regime_ema_values = {}
day_low_cache = {}

def candle_start(t):
    return t.replace(second=0, microsecond=0) - dt.timedelta(minutes=t.minute % TIMEFRAME_MIN)

def fetch_initial_emas(fy, symbols, tf, period):
    print(f"\n🔄 Init EMA-{period}...")
    lookback = max(5, math.ceil((period * tf * 3)/(60*6)))
    start = (dt.date.today() - dt.timedelta(days=lookback)).strftime("%Y-%m-%d")
    end = dt.date.today().strftime("%Y-%m-%d")
    for sym in symbols:
        try:
            time.sleep(0.1)
            res = fy.history({"symbol": sym, "resolution": str(tf), "date_format": "1", "range_from": start, "range_to": end, "cont_flag": "1"})
            if res.get("s")=="ok" and res.get("candles"):
                df = pd.DataFrame(res["candles"], columns=["ts","o","h","l","c","v"])
                df["ema"] = df["c"].ewm(span=period, adjust=False).mean()
                regime_ema_values[sym] = df["ema"].iloc[-1]
        except Exception as e: print(f"⚠️ EMA Error {sym}: {e}")
    print(f"✅ EMA Initialized for {len(regime_ema_values)} symbols.")

def fetch_day_lows(fy, symbols):
    print(f"\n🔄 Init Day Lows...")
    chunk_size = 50
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i:i+chunk_size]
        try:
            res = fy.quotes({"symbols": ",".join(chunk)})
            if res.get("s")=="ok":
                for item in res.get("d", []):
                    # Note: 'low_price' is key for day low in Fyers quotes
                    low = item.get("v", {}).get("low_price")
                    if low: day_low_cache[item.get("n")] = float(low)
        except Exception as e: print(f"⚠️ DayLow Error: {e}")
    print(f"✅ Day Lows Initialized.")

def get_ltp(fy, sym):
    cached = ltp_cache.get(sym)
    if cached and (time.time() - cached[1] < 10): return cached[0]
    # Fallback REST
    try:
        res = fy.quotes({"symbols": sym})
        if res.get("s")=="ok":
            val = res["d"][0]["v"].get("lp") or res["d"][0]["v"].get("last_price")
            if val:
                ltp_cache[sym] = (float(val), time.time())
                return float(val)
    except: pass
    return None

# ===================== LOGIC =====================
import traceback

def make_onmsg(fy, dry_run=False):
    def onmsg(msg):
        try:
            if msg.get("type") != "sf": return

            # Validate essential fields
            raw_ltp = msg.get("ltp")
            raw_ts = msg.get("timestamp")

            if raw_ltp is None or raw_ts is None:
                return

            sym = msg.get("symbol")
            ltp = float(raw_ltp)
            ts = raw_ts

            prev_ltp_cache[sym] = ltp_cache.get(sym, (None,))[0]
            ltp_cache[sym] = (ltp, time.time())

            # Update Day Low
            cur_low = day_low_cache.get(sym, 9999999.0)
            if ltp < cur_low: day_low_cache[sym] = ltp

            tick_time = dt.datetime.fromtimestamp(ts)
            cstart = candle_start(tick_time)
            key = (sym, cstart)
            bar = bars.get(key)
            if not bar: bars[key] = bar = {"o": ltp, "h": ltp, "l": ltp, "c": ltp}
            else:
                bar["h"] = max(bar["h"], ltp)
                bar["l"] = min(bar["l"], ltp)
                bar["c"] = ltp

            # Check Candle Completion
            if tick_time >= cstart + dt.timedelta(minutes=TIMEFRAME_MIN) - dt.timedelta(seconds=1):
                if key not in processed_candles:
                    processed_candles.add(key)

                    # Update EMA
                    curr_ema = regime_ema_values.get(sym, bar['c'])
                    k = 2/(REGIME_EMA_PERIOD+1)
                    new_ema = (bar['c']*k) + (curr_ema*(1-k))
                    regime_ema_values[sym] = new_ema

                    prev_bar = bars.get((sym, cstart - dt.timedelta(minutes=TIMEFRAME_MIN)))

                    if ONE_POSITION_AT_A_TIME and any(t["status"]=="open" for t in active_trades.values()): return

                    # SIGNAL LOGIC (BUY)
                    # 1. Close > EMA
                    is_above_ema = bar['c'] > new_ema
                    # 2. At Day Low
                    cached_day_low = day_low_cache.get(sym, bar['l'])
                    # Allow small tolerance above low
                    is_at_day_low = bar['l'] <= (cached_day_low + 0.05)

                    is_valid_context = is_above_ema or is_at_day_low

                    if is_valid_context and prev_bar and is_bullish_hammer_candle(
                        bar['o'], bar['h'], bar['l'], bar['c'], prev_bar['o'], prev_bar['c']
                    ):
                        reasons = []
                        if is_above_ema: reasons.append(f"Above EMA {new_ema:.2f}")
                        if is_at_day_low: reasons.append(f"At Day Low {bar['l']:.2f}")
                        print(f"[{tick_time:%H:%M}] 🎯 BUY SIGNAL {sym}: {' & '.join(reasons)}")

                        trigger[sym] = {
                            "high": bar['h'], "low": bar['l'],
                            "active_start": cstart + dt.timedelta(minutes=TIMEFRAME_MIN),
                            "triggered": False
                        }

            # CHECK TRIGGER
            t = trigger.get(sym)
            if not t: return
            if tick_time >= t["active_start"] + dt.timedelta(minutes=TIMEFRAME_MIN):
                trigger.pop(sym, None); return
            if tick_time < t["active_start"] or t["triggered"]: return

            # GUARD: CUTOFF
            now_t = dt.datetime.now().time()
            cutoff = ENTRY_CUTOFF_MCX if sym.startswith("MCX:") else ENTRY_CUTOFF
            if now_t >= cutoff: return

            # BUY ENTRY: Breakout above High
            threshold = round_to_tick(t["high"] + ENTRY_BUFFER)
            prev_ltp = prev_ltp_cache.get(sym)

            if (prev_ltp is not None) and (prev_ltp <= threshold) and (ltp > threshold):
                print(f"🚀 BREAKOUT BUY {sym} > {threshold}")

                # Sizing
                lot_size = get_lot_size(sym)
                if sym.startswith("MCX:"):
                    qty_lots = MCX_LOT_MULTIPLIER
                    qty_shares = qty_lots * lot_size
                else:
                    if POSITION_MODE == "alloc":
                        calc = int(ALLOCATION_AMOUNT / ltp)
                        qty_shares = max(lot_size, (calc // lot_size) * lot_size)
                        qty_lots = qty_shares // lot_size
                    else:
                        qty_lots = LOT_MULTIPLIER
                        qty_shares = qty_lots * lot_size

                sl = t["low"]
                entry_price = floor_to_tick(ltp)
                risk = entry_price - sl
                if risk <= 0: return
                tgt = round_to_tick(entry_price + (risk * R_MULTIPLIER))

                resp = place_order(fy, sym, 1, qty_shares, "GreenHamBuy", dry_run)
                if resp.get("s")=="ok":
                    save_trade(sym, entry_price, sl, tgt, qty_lots, lot_size, resp.get("id"))
                    t["triggered"] = True
                    trigger.pop(sym, None)

        except Exception as e:
            print(f"🚨 OnMsg Error: {e}")
            traceback.print_exc()
            print(f"📄 Message dump: {msg}")

    return onmsg

def sync_positions(fy):
    global active_trades
    try:
        res = fy.positions()
        if res.get("s") != "ok": return
        # Get list of open symbols at broker
        broker_syms = {p["symbol"] for p in res.get("netPositions", []) if p.get("netQty", 0) != 0}

        # Identify trades we think are open but broker says are closed
        closed = [s for s, t in active_trades.items() if t["status"]=="open" and s not in broker_syms]

        for s in closed:
            print(f"⚠️ Position {s} closed at broker manually. Removing from bot monitoring.")
            active_trades.pop(s)
        if closed: save_state()
    except Exception as e: print(f"Sync Error: {e}")

def monitor_loop(fy, dry_run=False):
    global FORCE_CLOSED_ALL, FORCE_CLOSED_ALL_MCX
    last_sync = time.time()
    while True:
        try:
            now = time.time()
            if now - last_sync > 60:
                sync_positions(fy)
                last_sync = now

            now_dt = dt.datetime.now()
            now_time = now_dt.time()

            # AUTO EXIT Logic (Only if NOT carrying forward)
            if not CARRY_FORWARD:
                # NSE Exit
                if (not FORCE_CLOSED_ALL) and (now_time >= EXIT_ALL_TIME):
                    trades = [s for s,t in active_trades.items() if not s.startswith("MCX:")]
                    if trades:
                        print(f"[{now_dt:%H:%M:%S}] ⏳ EXIT ALL NSE (Intraday) - Closing {len(trades)} trades")
                        for s in trades:
                            t = active_trades[s]
                            exit_long_by_sell_market(fy, s, t["qty"], t["lot_size"], dry_run)
                            active_trades.pop(s)
                        save_state()
                    else:
                        print(f"[{now_dt:%H:%M:%S}] ⏳ EXIT_ALL (NSE) triggered but no open NSE trades.")
                    FORCE_CLOSED_ALL = True

                # MCX Exit
                if (not FORCE_CLOSED_ALL_MCX) and (now_time >= EXIT_ALL_TIME_MCX):
                    trades = [s for s,t in active_trades.items() if s.startswith("MCX:")]
                    if trades:
                        print(f"[{now_dt:%H:%M:%S}] ⏳ EXIT ALL MCX (Intraday) - Closing {len(trades)} trades")
                        for s in trades:
                            t = active_trades[s]
                            exit_long_by_sell_market(fy, s, t["qty"], t["lot_size"], dry_run)
                            active_trades.pop(s)
                        save_state()
                    else:
                        print(f"[{now_dt:%H:%M:%S}] ⏳ EXIT_ALL (MCX) triggered but no open MCX trades.")
                    FORCE_CLOSED_ALL_MCX = True

            # SL/TGT Monitor
            for sym in list(active_trades.keys()):
                t = active_trades.get(sym)
                if not t or t["status"] != "open": continue

                ltp = get_ltp(fy, sym)
                if not ltp: continue

                sl = t["sl"]
                tgt = t["tgt"]

                if ltp <= sl:
                    print(f"❌ SL HIT {sym} @ {ltp} (SL {sl}) -> Exit")
                    exit_long_by_sell_market(fy, sym, t["qty"], t["lot_size"], dry_run)
                    active_trades.pop(sym)
                    save_state()
                elif ltp >= tgt:
                    print(f"🎯 TGT HIT {sym} @ {ltp} (TGT {tgt}) -> Exit")
                    exit_long_by_sell_market(fy, sym, t["qty"], t["lot_size"], dry_run)
                    active_trades.pop(sym)
                    save_state()

        except Exception as e: print(f"Monitor Error: {e}")
        time.sleep(1)

def main():
    load_state()
    parser = argparse.ArgumentParser()

    # We cannot access global variables here to set defaults if we intend to declare them global later in this function scope.
    # To fix the SyntaxError, we use a simple workaround: access globals via dictionary for read/write or separate the scope.
    # Or cleaner: Just use hardcoded defaults in argparse or None, and only update if set.

    # However, to respect the existing structure:
    parser.add_argument("--tf", type=int, default=TIMEFRAME_MIN, help="Timeframe (min)")
    parser.add_argument("--rmult", type=float, default=R_MULTIPLIER, help="Risk Reward Multiplier")
    parser.add_argument("--regime-ema", type=int, default=REGIME_EMA_PERIOD, help="EMA Period")
    parser.add_argument("--mode", type=str, default=POSITION_MODE, choices=["qty","alloc"])
    parser.add_argument("--alloc", type=float, default=ALLOCATION_AMOUNT)
    parser.add_argument("--mcx-lots", type=int, default=MCX_LOT_MULTIPLIER)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--carry", action="store_true", help="Carry forward positions (CNC/MARGIN)")
    parser.add_argument("--run-tests", action="store_true", help="Run unit tests for detector logic and exit")

    args = parser.parse_args()

    # Update globals safely using globals() dict
    globals()["TIMEFRAME_MIN"] = args.tf
    globals()["R_MULTIPLIER"] = args.rmult
    globals()["REGIME_EMA_PERIOD"] = args.regime_ema
    globals()["POSITION_MODE"] = args.mode
    globals()["ALLOCATION_AMOUNT"] = args.alloc
    globals()["MCX_LOT_MULTIPLIER"] = args.mcx_lots
    globals()["CARRY_FORWARD"] = args.carry

    # Update Product Type Globals based on Carry flag
    if globals()["CARRY_FORWARD"]:
        globals()["STOCK_PRODUCT_TYPE"] = "CNC"
        globals()["MCX_PRODUCT_TYPE"] = "MARGIN"
    else:
        globals()["STOCK_PRODUCT_TYPE"] = "INTRADAY"
        # MCX Intraday is technically "INTRADAY" product type in Fyers, sometimes "MARGIN" is used for everything but we stick to defaults
        globals()["MCX_PRODUCT_TYPE"] = "INTRADAY"

    if globals()["CARRY_FORWARD"]:
        print("📦 CARRY FORWARD MODE ENABLED (CNC / MARGIN)")
    else:
        print("🕒 INTRADAY MODE")

    dry_run = args.dry_run or (not HAS_FYERS)

    if args.run_tests:
        print("Running tests for Bullish Hammer detector...")
        # Green Hammer: Green body, Long lower wick, Small body, Small/No upper wick
        # o=100, c=105, h=106, l=90. Range=16. Body=5 (31%). Lower=10 (62%). Upper=1 (6%).
        # Body > 30% might fail default. Let's adjust example.
        # Range=20. L=80, H=100.
        # LowerWick 50-80% -> 10-16 pts.
        # Body 5-30% -> 1-6 pts.
        # Upper 0-25% -> 0-5 pts.
        # Example: L=80, H=100. C=98, O=95.
        # Body = 3 (15%). Lower = 95-80=15 (75%). Upper = 100-98=2 (10%). -> Valid
        assert is_bullish_hammer_candle(95, 100, 80, 98, 96, 94) == True
        # Red candle -> Fail
        assert is_bullish_hammer_candle(98, 100, 80, 95, 96, 94) == False
        print("All tests passed ✅")
        return

    if HAS_FYERS:
        cid, token = ensure_access_token()
        fy = fyersModel.FyersModel(client_id=cid, token=token, log_path=".")
    else:
        fy = fyersModel.FyersModel(client_id="MOCK", token="MOCK", log_path=".")

    fetch_initial_emas(fy, SYMBOLS, TIMEFRAME_MIN, REGIME_EMA_PERIOD)
    fetch_day_lows(fy, SYMBOLS)

    print("\n✅ WATCHLIST:")
    for s in SYMBOLS: print(f"  - {s}")
    print("="*60)
    print(f"Strategy: GREEN HAMMER BUY")
    print(f"Condition: Close > EMA({REGIME_EMA_PERIOD}) OR At Day Low")
    print(f"Product: Stocks={STOCK_PRODUCT_TYPE}, MCX={MCX_PRODUCT_TYPE}")
    print("="*60)

    on_message = make_onmsg(fy, dry_run=dry_run)
    global ws_connection
    ws_connection = data_ws.FyersDataSocket(
        access_token=f"{fy.client_id}:{fy.token}", log_path=".", litemode=False,
        write_to_file=False, reconnect=True, on_message=on_message,
        on_error=lambda m: print("🚨", m), on_close=lambda m: print("❌", m),
        on_connect=lambda: (print("🔌 Connected") or ws_connection.subscribe(symbols=SYMBOLS))
    )

    threading.Thread(target=monitor_loop, args=(fy, dry_run), daemon=True).start()
    ws_connection.connect()

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: sys.exit(0)
    except Exception as e: print(e); sys.exit(1)
