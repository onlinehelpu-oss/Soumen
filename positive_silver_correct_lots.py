# -*- coding: utf-8 -*-
"""
Green-Hammer / Green-Pinbar NEXT-candle first-touch breakout (GREEN candle only)
- Strict tick-level breakout: enters only when price CROSSES ABOVE (signal_high + buffer)
- Never enters exactly at signal high; requires > (high + buffer)
- Previous candle must be RED + tiny-candle filter
- Single-position mode: block new signals & entries while ANY position is open
- Configurable timeframe via TIMEFRAME_MIN or --tf
- Live trading (no paper-mode)
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
import re

import pandas as pd
import requests

from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws

# ===================== STRATEGY SETTINGS =====================
TIMEFRAME_MIN = 15       # change to 5 / 15 / 30 / 60 etc., or override with --tf
R_MULTIPLIER = 1.0       # default Risk:Reward (1:1)
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

# ===================== CONSTANTS & PATHS =====================
CONFIG_FILE = "fyers_login_details.json"
TOKENS_DIR = "AccessToken"
TOKENS_STORE = "tokens_store.json"
TODAY = dt.date.today()
TODAY_PATH = os.path.join(TOKENS_DIR, f"{TODAY}.json")
API_HOST = "https://api-t1.fyers.in"

# ===================== WATCHLIST =====================
SYMBOLS = [
    'MCX:NATGASMINI25OCTFUT',
    'MCX:SILVERMIC25NOVFUT',
    'MCX:CRUDEOILM25NOVFUT'
]

# ===================== LOT SIZE MANAGEMENT =====================
MCX_LOT_SIZES = {
    "SILVERMIC": 1,
    "CRUDEOILM": 10,
    "NATGASMINI": 250,
}
lot_cache = {}

def get_lot_size(symbol: str) -> int:
    """Determine the lot size for a given symbol using hardcoded values for MCX."""
    if symbol in lot_cache:
        return lot_cache[symbol]

    if symbol.startswith('MCX:'):
        clean_symbol = symbol.split(':')[1]
        match = re.match(r'([A-Z]+)', clean_symbol)
        if match:
            base_symbol = match.group(1)
            if base_symbol in MCX_LOT_SIZES:
                lot_size = MCX_LOT_SIZES[base_symbol]
                print(f"✅ Using hardcoded lot size {lot_size} for {symbol}")
                lot_cache[symbol] = lot_size
                return lot_size

    print(f"⚠️ Could not determine lot size for '{symbol}'. Defaulting to 1.")
    lot_cache[symbol] = 1
    return 1

# ===================== TIME/ENTRY/EXIT RULES =====================
ENTRY_BUFFER = 0.05                # buffer above signal high for breakout
ENTRY_CUTOFF_MCX = dt.time(22, 0)   # allow MCX signals up to 10:00 PM
EXIT_ALL_TIME_MCX = dt.time(22, 50) # force-exit all open MCX positions at 10:50 PM
FORCE_CLOSED_ALL_MCX = False

# ===================== SMALL CANDLE GUARDS =====================
MIN_RANGE_PCT = 0.0015   # ignore if (H-L)/Close < 0.15% (tune per product)

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
            return client_id, access_token, access_token.split(':')[-1]

    store = _read_json(TOKENS_STORE, {}) or {}
    refresh_token = store.get("refresh_token")

    session = fyersModel.SessionModel(
        client_id=client_id,
        secret_key=secret_key,
        redirect_uri=redirect_uri,
        response_type="code"
    )

    if refresh_token:
        try:
            print("🔄 Attempting refresh-token login …")
            session.set_token(refresh_token)
            session.grant_type = "refresh_token"
            response = session.generate_token()

            if response.get("s") != "ok":
                raise RuntimeError(f"Refresh token failed: {response.get('message')}")

            access_token = response["access_token"]
            new_refresh_token = response.get("refresh_token") or refresh_token
            _write_json(TOKENS_STORE, {"refresh_token": new_refresh_token})
            _write_json(TODAY_PATH, access_token)
            print("✅ Refresh successful.")
            return client_id, f"{client_id}:{access_token}", access_token
        except Exception as e:
            print(f"⚠️ Refresh failed: {e}. Falling back to manual login.")
            if os.path.exists(TOKENS_STORE):
                _write_json(TOKENS_STORE, {})

    auth_url = session.generate_authcode()
    print("\n👉 Open this login URL in your browser, complete login, and copy the auth_code from the redirect URL:")
    print(auth_url)
    auth_code = input("\nPaste the auth_code here: ").strip()

    session.set_token(auth_code)
    session.grant_type = "authorization_code"
    response = session.generate_token()

    if response.get("s") != "ok":
        raise SystemExit(f"❌ Token generation failed: {response.get('message')}")

    access_token = response["access_token"]
    refresh_token = response.get("refresh_token")
    _write_json(TODAY_PATH, access_token)
    if refresh_token:
        _write_json(TOKENS_STORE, {"refresh_token": refresh_token})

    print("✅ New access token generated successfully.")
    return client_id, f"{client_id}:{access_token}", access_token

# ===================== CANDLE DETECTOR =====================
def is_bullish_hammer_candle(o, h, l, c, prev_o, prev_c, min_range_pct=0.0015):
    if c == 0: return False
    rng = h - l
    if rng <= 0: return False
    if (rng / max(c, 1e-9)) < min_range_pct: return False
    if prev_c >= prev_o: return False
    if not (c > o): return False
    upper_shorter_than_body = (h - c) < (c - o)
    lower_longer_than_body  = (o - l) > (c - o)
    return upper_shorter_than_body and lower_longer_than_body

# ===================== ORDER HELPERS =====================
def place_order(fy: fyersModel.FyersModel, sym: str, side: int, qty: int, tag: str):
    payload = {
        "symbol": sym, "qty": int(qty), "type": 2, "side": int(side),
        "productType": "INTRADAY", "validity": "DAY", "orderTag": tag[:15] if tag else ""
    }
    try:
        resp = fy.place_order(payload)
        print(f"[{dt.datetime.now():%H:%M:%S}] 📌 {tag} {resp}")
        return resp
    except Exception as e:
        print(f"[{dt.datetime.now():%H:%M:%S}] 🚨 Order error {sym} {tag}: {e}")
        return {"s": "error", "message": str(e)}

def exit_long_by_sell_market(fy: fyersModel.FyersModel, sym: str, qty: int):
    return place_order(fy, sym, side=-1, qty=qty, tag="ExitLong")

# ===================== TRADE LOG & TRACKING =====================
active_trades = {}

def has_open_positions() -> bool:
    return any(v.get("status") == "open" for v in active_trades.values())

def save_trade(sym, entry, sl, tgt, qty):
    row = {
        "Datetime": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Symbol": sym,
        "Entry Price": float(entry), "Stop Loss": float(sl), "Target": float(tgt), "Qty": int(qty)
    }
    pd.DataFrame([row]).to_csv(
        "trade_log.csv", mode='a', header=not os.path.exists("trade_log.csv"), index=False
    )
    active_trades[sym] = {"entry": entry, "sl": sl, "tgt": tgt, "qty": qty, "status": "open"}

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
    if cached and (now - cached[1]) <= cache_ttl: return float(cached[0])

    for attempt in range(1, max_retries + 1):
        try:
            q = fy.quotes({"symbols": sym})
            if q.get("s") == "ok" and q.get("d"):
                ltp = q["d"][0].get("v", {}).get("lp")
                if ltp is not None:
                    ltp_cache[sym] = (float(ltp), time.time())
                    return float(ltp)

            last_err = _last_quote_error.get(sym, 0)
            if now - last_err > ERROR_THROTTLE_SECS:
                print(f"⚠️ Quote fetch failed {sym}: {q}")
                _last_quote_error[sym] = now
            time.sleep(1.0 * attempt)
        except Exception as e:
            last_err = _last_quote_error.get(sym, 0)
            if now - last_err > ERROR_THROTTLE_SECS:
                print(f"⚠️ Quote fetch exception {sym}: {e}")
                _last_quote_error[sym] = now
            time.sleep(1.0 * attempt)

    return ltp_cache.get(sym, (None, None))[0]

# ===================== WEBSOCKET HANDLER (LIVE LONG logic) =====================
def make_onmsg(fy: fyersModel.FyersModel):
    def onmsg(msg):
        if msg.get("type") != "sf": return
        try:
            sym, ltp, ts = msg["symbol"], float(msg["ltp"]), int(msg.get("timestamp", time.time()))
        except Exception: return

        prev_ltp = ltp_cache.get(sym, (None, None))[0]
        if prev_ltp is not None: prev_ltp_cache[sym] = float(prev_ltp)
        ltp_cache[sym] = (ltp, time.time())

        tick_time = dt.datetime.fromtimestamp(ts)
        cstart = candle_start(tick_time)
        key = (sym, cstart)

        bar = bars.get(key, {"o": ltp, "h": ltp, "l": ltp, "c": ltp})
        bar["h"], bar["l"], bar["c"] = max(bar["h"], ltp), min(bar["l"], ltp), ltp
        bars[key] = bar

        if tick_time >= cstart + dt.timedelta(minutes=TIMEFRAME_MIN) - dt.timedelta(seconds=1):
            if key not in processed_candles:
                processed_candles.add(key)
                prev_bar = bars.get((sym, cstart - dt.timedelta(minutes=TIMEFRAME_MIN)))

                if ONE_POSITION_AT_A_TIME and has_open_positions(): return

                if prev_bar and is_bullish_hammer_candle(
                    bar["o"], bar["h"], bar["l"], bar["c"], prev_bar["o"], prev_bar["c"], MIN_RANGE_PCT
                ):
                    trigger[sym] = {
                        "low": bar["l"], "high": bar["h"], "active_start": cstart + dt.timedelta(minutes=TIMEFRAME_MIN),
                        "triggered": False
                    }
                    print(f"[{tick_time:%H:%M:%S}] 🎯 GREEN-SIG {sym} TF={TIMEFRAME_MIN}m → watch NEXT HIGH {bar['h']} (SL {bar['l']})")

        t = trigger.get(sym)
        if not t: return

        if tick_time >= t["active_start"] + dt.timedelta(minutes=TIMEFRAME_MIN):
            trigger.pop(sym, None)
            return

        if tick_time < t["active_start"] or t["triggered"]: return
        if ONE_POSITION_AT_A_TIME and has_open_positions():
            trigger.pop(sym, None)
            return

        if dt.datetime.now().time() >= ENTRY_CUTOFF_MCX:
            trigger.pop(sym, None)
            return

        threshold = round_to_tick(t["high"] + ENTRY_BUFFER)
        prev_for_cross = prev_ltp_cache.get(sym)
        if (prev_for_cross is not None) and (prev_for_cross <= threshold) and (ltp > threshold):
            t["triggered"] = True

            lot_size = get_lot_size(sym)
            qty = LOT_MULTIPLIER * lot_size

            entry = ceil_to_tick(ltp)
            sl = t["low"]
            risk = entry - sl
            if risk <= 0:
                trigger.pop(sym, None)
                return

            tgt = round_to_tick(entry + (R_MULTIPLIER * risk))

            place_order(fy, sym, side=1, qty=qty, tag="GreenHammerBuy")
            save_trade(sym, entry, sl, tgt, qty)
            trigger.pop(sym, None)
            print(f"[{tick_time:%H:%M:%S}] ✅ LONG {sym} @ {entry}, SL={sl}, TGT={tgt}, QTY={qty} shares")

    return onmsg

# ===================== EXIT MONITOR (for LONG positions) =====================
def monitor_loop(fy: fyersModel.FyersModel):
    global FORCE_CLOSED_ALL_MCX
    while True:
        try:
            now_dt, now_time = dt.datetime.now(), dt.datetime.now().time()

            if not FORCE_CLOSED_ALL_MCX and now_time >= EXIT_ALL_TIME_MCX:
                mcx_trades = [s for s, t in active_trades.items() if t['status'] == 'open' and s.startswith("MCX:")]
                if mcx_trades:
                    print(f"[{now_dt:%H:%M:%S}] ⏳ EXIT_ALL (MCX) triggered — closing {len(mcx_trades)} trades")
                    for sym in mcx_trades:
                        exit_long_by_sell_market(fy, sym, active_trades[sym]['qty'])
                        active_trades.pop(sym, None)
                FORCE_CLOSED_ALL_MCX = True

            for sym in list(active_trades.keys()):
                trade = active_trades.get(sym)
                if not trade or trade["status"] != "open": continue

                ltp = get_ltp(fy, sym)
                if ltp is None: continue

                if ltp <= trade["sl"]:
                    print(f"[{dt.datetime.now():%H:%M:%S}] ❌ SL HIT {sym} @ {ltp}")
                    exit_long_by_sell_market(fy, sym, trade["qty"])
                    active_trades.pop(sym, None)
                elif ltp >= trade["tgt"]:
                    print(f"[{dt.datetime.now():%H:%M:%S}] 🎯 TARGET HIT {sym} @ {ltp}")
                    exit_long_by_sell_market(fy, sym, trade["qty"])
                    active_trades.pop(sym, None)
        except Exception as e:
            print(f"⚠️ Monitor loop error: {e}")
        time.sleep(1.5)

# ===================== MAIN =====================
def main():
    global TIMEFRAME_MIN, R_MULTIPLIER
    parser = argparse.ArgumentParser()
    parser.add_argument("--tf", type=int, default=TIMEFRAME_MIN, help="Timeframe in minutes")
    parser.add_argument("--rmult", type=float, default=R_MULTIPLIER, help="Risk:Reward multiple")
    args = parser.parse_args()
    TIMEFRAME_MIN, R_MULTIPLIER = max(1, args.tf), float(args.rmult)

    # Pre-fetch lot sizes
    for sym in SYMBOLS:
        get_lot_size(sym)

    app_id, token_str, raw_access = ensure_access_token()
    fy = fyersModel.FyersModel(client_id=app_id, token=raw_access, log_path=".")

    ws = data_ws.FyersDataSocket(
        access_token=token_str, log_path=".", on_message=make_onmsg(fy),
        on_error=lambda m: print("🚨", m), on_close=lambda m: print("❌", m),
        on_connect=lambda: (
            print(f"🔌 Connected → subscribing {len(SYMBOLS)} symbols | TF={TIMEFRAME_MIN}m") or
            ws.subscribe(symbols=SYMBOLS)
        )
    )
    threading.Thread(target=monitor_loop, args=(fy,), daemon=True).start()

    print("\n========== Green-Hammer/PINBAR Scanner ==========")
    print(f"🧩 Python: {sys.version.split()[0]} | Symbols: {len(SYMBOLS)} | TF={TIMEFRAME_MIN}m | Rmult={R_MULTIPLIER}")
    print("🚀 Real-time LONG scanner started …\n")
    ws.connect()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Exiting on user interrupt.")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        sys.exit(1)
