# Delta Exchange Fast Slow EMA Strategy
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

- Delta Exchange:
    * Uses v2 REST and WebSocket APIs.
    * Auto-maps symbols to Product IDs.
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
import hashlib
import hmac
import datetime
from typing import Dict, Optional, List
from datetime import datetime as dt, timedelta
from urllib.parse import urlencode

import requests
import pandas as pd
import numpy as np
import websocket

# ---------------------------- CONFIGURATION ----------------------------
# DELTA EXCHANGE CREDENTIALS
API_KEY = "qnz5G7ullIHIIywNbojX6i2mEfWCKY"
API_SECRET = "NM0zX5jmDDtLkqAX5qNTyWgLtW5XqTVHZceBl3yCD7FVy0K8r8Dqlxts9oy0"

# --- TRADING ENVIRONMENT ---
USE_TESTNET = False  # Set True for Testnet
ENABLE_LIVE_TRADING = True  # Set False for Paper Trading (Simulated Orders)

if USE_TESTNET:
    BASE_URL = "https://testnet-api.india.delta.exchange"
    WS_URL = "wss://testnet-socket.india.delta.exchange"
else:
    BASE_URL = "https://api.india.delta.exchange"
    WS_URL = "wss://socket.india.delta.exchange"

# STRATEGY PARAMETERS
TIMEFRAME_MIN = 5  # Default 5m as per log example
EXIT_EMA = 50
ENTRY_FAST_EMA = 20
ENTRY_SLOW_EMA = 50

MIN_RANGE_PCT = 0.0
EMA_BUFFER = 0.0
REQUIRE_GREEN_SIGNAL = True

# SYMBOLS TO TRADE
SYMBOLS = [
    "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "BNBUSD",
    "DOGEUSD", "ADAUSD", "DOTUSD", "AVAXUSD", "LINKUSD",
    "LTCUSD", "BCHUSD", "XMRUSD", "ATOMUSD", "TRXUSD",
    "NEARUSD", "FILUSD", "APTUSD", "INJUSD", "STXUSD",
    "ARBUSD", "OPUSD", "AAVEUSD", "UNIUSD", "SUIUSD",
    "HBARUSD", "ETCUSD", "ALGOUSD", "POLUSD", "TIAUSD",
    "ENSUSD", "LDOUSD", "GALAUSD", "MANAUSD", "SANDUSD",
    "CAKEUSD", "DYDXUSD", "RUNEUSD", "ZECUSD", "ZROUSD",
    "API3USD", "KSMUSD", "SKLUSD", "IOTAUSD", "JUPUSD",
    "WLDUSD", "ONDOUSD", "SEIUSD"
]

LOG_FILE = "trade_log.csv"
STATE_DUMP = "bot_state.json"
PARTIAL_CANDLES_FILE = "partial_candles.json"

ALLOC_DEFAULT = 275.0  # From log example
SL_MODE = "signal_low"  # or "swing_low"
SWING_LOOKBACK = 5
SWING_HIGH_LOOKBACK = 50
MAX_CONCURRENT_POS = 3
TRAIL_ATR_MULT = 1.0  # Trailing Stop Multiplier

# GLOBAL STATE
SYMBOL_STATES = {}
PRODUCT_MAP = {}  # Symbol -> {id, contract_value, tick_size, is_inverse}
ID_TO_SYMBOL = {}

# ---------------------------- LOGGING ----------------------------
_built_in_print = print

def _real_print(*args, **kwargs):
    ts = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    msg = " ".join(str(x) for x in args)
    _built_in_print(f"{ts} {msg}", **kwargs)

print = _real_print

def log_trade_event(symbol, action, qty, price, response):
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w") as f:
            f.write("ts,symbol,action,qty,price,response\n")

    with open(LOG_FILE, "a") as f:
        f.write(f"{dt.now().isoformat()},{symbol},{action},{qty},{price},{json.dumps(response, default=str)}\n")

# ---------------------------- DELTA CLIENT ----------------------------
class DeltaClient:
    def __init__(self, api_key, api_secret, base_url):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()

    def _generate_signature(self, method, endpoint, payload, timestamp):
        # Signature string: method + timestamp + endpoint + payload
        # Payload is empty string if None, else JSON string
        body_str = ""
        if payload is not None:
            if isinstance(payload, dict):
                body_str = json.dumps(payload, separators=(',', ':'))
            else:
                body_str = str(payload)

        msg = f"{method}{timestamp}{endpoint}{body_str}"
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            msg.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature

    def request(self, method, endpoint, params=None, payload=None, auth=False):
        url = f"{self.base_url}{endpoint}"
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}

        # Build URL with params for signature if needed
        # Delta docs say: "The request path is the path part of the URL, e.g. /v2/orders"
        # Query params are appended to URL but NOT included in signature path usually

        if auth:
            timestamp = str(int(time.time()))
            signature = self._generate_signature(method, endpoint, payload, timestamp)
            headers.update({
                'api-key': self.api_key,
                'timestamp': timestamp,
                'signature': signature
            })

        try:
            resp = self.session.request(method, url, params=params, data=json.dumps(payload) if payload else None, headers=headers, timeout=10)
            if resp.status_code not in (200, 201):
                # print(f"[delta] HTTP {resp.status_code}: {resp.text}")
                pass
            return resp.json()
        except Exception as e:
            # _real_print(f"[delta] Request failed: {e}")
            return {"success": False, "error": str(e)}

    def get_products(self):
        return self.request("GET", "/v2/products")

    def get_history(self, symbol, resolution, start, end):
        # resolution: "1", "5", "15", "60" (1h), "240" (4h), "D" (1d)
        # start/end: unix timestamp (seconds) -> API expects 'from' and 'to'
        params = {
            "symbol": symbol,
            "resolution": resolution,
            "from": start,
            "to": end
        }
        return self.request("GET", "/v2/chart/history", params=params)

    def place_order(self, product_id, size, side, order_type="limit_order", limit_price=None, stop_price=None):
        payload = {
            "product_id": int(product_id),
            "size": int(size) if size >= 1 else size, # Delta size is usually int for contracts, check specs
            "side": side.lower(), # "buy" or "sell"
            "order_type": order_type,
            "limit_price": str(limit_price) if limit_price else None,
            "stop_price": str(stop_price) if stop_price else None,
            "time_in_force": "ioc" if order_type == "market_order" else "gtc"
        }
        # Filter None
        payload = {k: v for k, v in payload.items() if v is not None}
        return self.request("POST", "/v2/orders", payload=payload, auth=True)

    def get_positions(self):
        return self.request("GET", "/v2/positions", auth=True)

    def get_ticker_24h(self):
        return self.request("GET", "/v2/tickers")

# ---------------------------- STATE & MANAGER ----------------------------
class SymbolState:
    def __init__(self, symbol):
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
        self.target_price = None
        self.potential_target_price = None

        # Exit
        self.exit_signal_candle = None
        self.exit_signal_expiry = None
        self.exit_pending = False
        self.exit_try_count = 0
        self.last_failed_exit_ts = None

        # Tracking
        self.last_candle_ts = None
        self.just_entered = False
        self.entry_time = 0.0
        self.atr_at_entry = 0.0
        self.sl_trailed = False

        # 24h stats
        self.ltp_change_24h = 0.0
        self.volume_24h = 0.0

SYMBOL_STATES: Dict[str, SymbolState] = {s: SymbolState(s) for s in SYMBOLS}

class CandleManager:
    def __init__(self, timeframe_min, on_candle_callback):
        self.tf = timeframe_min
        self.on_candle = on_candle_callback
        self.partial = {}
        self.lock = threading.RLock()

    def _floor_ts(self, ts):
        # Round down to nearest timeframe minute
        minute = (ts.minute // self.tf) * self.tf
        return ts.replace(minute=minute, second=0, microsecond=0)

    def process_tick(self, symbol, price, ts_dt):
        with self.lock:
            bucket = self._floor_ts(ts_dt)
            p = self.partial.get(symbol)

            if p is None:
                self.partial[symbol] = {
                    "ts": bucket, "open": price, "high": price, "low": price, "close": price, "ticks": 1
                }
                return

            if bucket == p["ts"]:
                # Update current candle
                p["high"] = max(p["high"], price)
                p["low"] = min(p["low"], price)
                p["close"] = price
                p["ticks"] += 1
            else:
                # Candle closed
                prev_candle = p.copy()
                prev_candle["symbol"] = symbol
                # Emit candle
                self.on_candle(symbol, prev_candle)

                # Start new
                self.partial[symbol] = {
                    "ts": bucket, "open": price, "high": price, "low": price, "close": price, "ticks": 1
                }

# ---------------------------- INDICATORS ----------------------------
def ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def atr(df, length=14):
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1/length, adjust=False).mean()

def compute_indicators(df):
    if df is None or df.empty:
        return df
    df = df.copy()
    df["ema_exit"] = ema(df["close"], EXIT_EMA)
    df["ema_fast_entry"] = ema(df["close"], ENTRY_FAST_EMA)
    df["ema_slow_entry"] = ema(df["close"], ENTRY_SLOW_EMA)
    df["atr"] = atr(df, 14)
    # Range pct
    rng = (df["high"] - df["low"]) / df["close"].replace(0, float("nan"))
    df["ok_signal"] = rng >= MIN_RANGE_PCT if MIN_RANGE_PCT > 0 else True
    return df

# ---------------------------- WEBSOCKET ----------------------------
class DeltaWS:
    def __init__(self, url, symbols, on_tick_callback):
        self.url = url
        self.symbols = symbols
        self.on_tick = on_tick_callback
        self.ws = None
        self.thread = None
        self.should_run = True

    def connect(self):
        self.ws = websocket.WebSocketApp(
            self.url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )
        self.thread = threading.Thread(target=self.ws.run_forever)
        self.thread.daemon = True
        self.thread.start()

    def on_open(self, ws):
        print(f"[ws] Connected to Delta Exchange ({self.url})")
        # Subscribe to ticker for all symbols
        payload = {
            "type": "subscribe",
            "payload": {
                "channels": [
                    {
                        "name": "v2/ticker",
                        "symbols": self.symbols
                    }
                ]
            }
        }
        ws.send(json.dumps(payload))
        print(f"[ws] Subscribed to {len(self.symbols)} symbols")

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            if data.get("type") == "v2/ticker":
                # Handle tick
                sym = data.get("symbol")
                # Delta sends everything as strings mostly
                if sym:
                    ltp = 0.0
                    if "close" in data and data["close"]:
                         ltp = float(data["close"])
                    elif "mark_price" in data and data["mark_price"]:
                         ltp = float(data["mark_price"])

                    if ltp > 0:
                        # Update 24h stats if present
                        if "open" in data and data["open"]:
                            op = float(data["open"])
                            if op > 0:
                                change = ((ltp - op) / op) * 100
                                if sym in SYMBOL_STATES:
                                    SYMBOL_STATES[sym].ltp_change_24h = change

                        if "volume" in data and data["volume"]:
                            if sym in SYMBOL_STATES:
                                SYMBOL_STATES[sym].volume_24h = float(data["volume"])

                        self.on_tick(sym, ltp)
        except Exception as e:
            pass

    def on_error(self, ws, error):
        print(f"[ws] Error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        # print(f"[ws] Closed: {close_msg}")
        if self.should_run:
            time.sleep(5)
            self.connect()

# ---------------------------- MAIN LOGIC ----------------------------
CLIENT = DeltaClient(API_KEY, API_SECRET, BASE_URL)
CANDLE_MGR = None

def compute_prev_swing_high_for_entry(state, lookback, reference_price):
    df = state.data
    if df is None or df.empty:
        return float("nan")

    # Exclude current signal candle logic if needed, but here we just take tail
    # Code-1 logic:
    pivot_width = 2
    highs = df["high"].values[:-1] # Exclude incomplete/latest? No, assume completed candles.

    if len(highs) < lookback:
        return float(df["high"].max())

    # We need a bit more history for pivots
    peaks = []
    # Loop needs enough padding
    for i in range(pivot_width, len(highs) - pivot_width):
        curr = highs[i]
        is_peak = True
        for j in range(1, pivot_width+1):
            if highs[i-j] >= curr or highs[i+j] >= curr:
                is_peak = False
                break
        if is_peak:
            peaks.append(curr)

    valid = [p for p in peaks if p > reference_price]
    if valid:
        return valid[-1] # Most recent

    # Fallback to max high in lookback period if no fractal peak found
    recent_highs = highs[-lookback:]
    return float(np.max(recent_highs))

def on_completed_candle(symbol, candle):
    # candle dict: {ts, open, high, low, close, ticks}
    st = SYMBOL_STATES.get(symbol)
    if not st: return

    # Update DataFrame
    # Avoid duplicate index issues
    ts = candle["ts"]

    # If partial candle update, we might overwrite. But here we get COMPLETED candle.
    # Just append.
    new_data = {
        "open": float(candle["open"]),
        "high": float(candle["high"]),
        "low": float(candle["low"]),
        "close": float(candle["close"])
    }

    df = st.data
    if df.empty:
        df = pd.DataFrame([new_data], index=[ts])
        df.index.name = "datetime"
    else:
        # Check if index exists
        if ts in df.index:
            # Update specific columns to avoid overwriting others (like bool indicators) with NaN
            df.loc[ts, list(new_data.keys())] = list(new_data.values())
        else:
            row = pd.DataFrame([new_data], index=[ts])
            df = pd.concat([df, row])

    # Trim
    if len(df) > 1000:
        df = df.iloc[-1000:]

    st.data = compute_indicators(df)
    st.last_candle_ts = ts

    # print(f"[candle] {symbol} {TIMEFRAME_MIN}m Candle Closed | Close: {candle['close']} | Evaluating Signals...")
    evaluate_on_new_candle(st)

def evaluate_on_new_candle(st):
    df = st.data
    if len(df) < 3: return

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    ema_fast = curr["ema_fast_entry"]
    ema_slow = curr["ema_slow_entry"]
    ema_slow_prev = prev["ema_slow_entry"]
    ema_exit = curr["ema_exit"]

    # ENTRY SIGNAL
    if st.status == "watch":
        # 1. EMA Fast > EMA Slow
        # 2. Rising Slow EMA
        # 3. Candle Low <= EMA Slow
        # 4. Close > Max(EMAs)
        # 5. Higher High

        cond1 = ema_fast > ema_slow
        cond2 = ema_slow > ema_slow_prev
        cond3 = curr["low"] <= ema_slow
        cond4 = curr["close"] > (max(ema_fast, ema_slow) + EMA_BUFFER)
        cond5 = curr["high"] > prev["high"]
        cond_green = (curr["close"] > curr["open"]) if REQUIRE_GREEN_SIGNAL else True
        ok_signal = bool(curr.get("ok_signal", True))

        if cond1 and cond2 and cond3 and cond4 and cond5 and cond_green and ok_signal:
            # Check Target
            target = compute_prev_swing_high_for_entry(st, SWING_HIGH_LOOKBACK, curr["high"])
            if target > curr["high"]:
                st.signal_candle = {
                    "ts": curr.name, "high": curr["high"], "low": curr["low"]
                }
                # Expires in next candle duration
                st.signal_expiry = curr.name + timedelta(minutes=TIMEFRAME_MIN*2)

                st.status = "entry_pending"
                st.potential_target_price = target
                st.qty = 0 # Calculated at entry

                print(f"[signal] 🔵 ENTRY SIGNAL {st.symbol} | High: {curr['high']} | Target: {target:.2f} | Wait for break > High (Expires: {st.signal_expiry})")

    # EXIT SIGNAL
    if st.status == "position":
        # Red candle crosses & closes below EXIT EMA
        is_red = curr["close"] < curr["open"]
        # crossed_below = (curr["open"] > ema_exit) and (curr["close"] < ema_exit) # Simple cross check

        # Code-1 logic:
        # intrabar_up = (curr_open < ema_exit) and (curr_high > ema_exit)
        # closed_below = curr_close < ema_exit - EMA_BUFFER
        # is_red = curr_close < curr_open

        intrabar_up = (curr["open"] < ema_exit) and (curr["high"] > ema_exit) # This was the Code-1 logic...
        # Wait, Code-1 logic for intrabar_up: (curr_open < ema_exit) and (curr_high > ema_exit)
        # This means the candle opened BELOW EMA, went ABOVE EMA (touched it), and then closed BELOW EMA.
        # This signifies a rejection from the EMA.

        closed_below = curr["close"] < (ema_exit - EMA_BUFFER)

        if is_red and intrabar_up and closed_below:
             st.exit_pending = True
             st.exit_signal_candle = {"low": curr["low"]}
             print(f"[exit-signal] 🔴 EXIT SIGNAL {st.symbol} | Low: {curr['low']} | Wait for break < Low")

def decide_qty(symbol, price):
    # Allocation based
    if price <= 0: return 0
    # Check contract value
    info = PRODUCT_MAP.get(symbol)
    if not info: return 0

    c_val = float(info["contract_value"])

    # Approximation for Delta:
    # Size = Alloc / (Price * Contract_Value)
    try:
        notional_per_contract = price * c_val
        if notional_per_contract <= 0: return 0
        qty = int(ALLOC_DEFAULT / notional_per_contract)
        return max(1, qty)
    except:
        return 0

def place_market_order_wrapper(symbol, qty, side):
    info = PRODUCT_MAP.get(symbol)
    if not info: return {"success": False}

    if not ENABLE_LIVE_TRADING:
        print(f"[sim] Simulated {side.upper()} Order for {qty} {symbol} placed successfully.")
        # Return mock success response structure similar to Delta API
        return {
            "success": True,
            "result": {
                "id": f"sim-{int(time.time())}",
                "product_id": info["id"],
                "size": qty,
                "side": side,
                "order_type": "market_order",
                "state": "closed",
                "average_price": "0" # Will be updated with LTP in real logic
            }
        }

    try:
        resp = CLIENT.place_order(
            product_id=info["id"],
            size=qty,
            side=side,
            order_type="market_order"
        )
        return resp
    except Exception as e:
        return {"success": False, "error": str(e)}

def on_tick(symbol, ltp):
    ts = dt.now()
    if CANDLE_MGR:
        CANDLE_MGR.process_tick(symbol, ltp, ts)

    st = SYMBOL_STATES.get(symbol)
    if not st: return

    # ENTRY EXECUTION
    if st.status == "entry_pending":
        # Check expiry
        if st.signal_expiry and ts > st.signal_expiry:
            st.status = "watch"
            st.signal_candle = None
            return

        # Check Breakout
        trigger = st.signal_candle["high"]
        if ltp > trigger:
            qty = decide_qty(symbol, ltp)
            print(f"[entry] Executing BUY {symbol} Qty: {qty} @ {ltp} (Break > {trigger})")
            resp = place_market_order_wrapper(symbol, qty, "buy")

            # Delta returns order object in 'result' if successful
            success = False
            if isinstance(resp, dict) and "result" in resp:
                success = True

            if success:
                st.status = "position"
                st.entry_price = ltp
                st.qty = qty
                st.target_price = st.potential_target_price

                # Set Stop Loss
                if SL_MODE == "signal_low":
                    st.stop_price = st.signal_candle["low"]
                else:
                    st.stop_price = st.signal_candle["low"] # Fallback

                # ATR Trailing setup
                if not st.data.empty and "atr" in st.data.columns:
                    st.atr_at_entry = st.data["atr"].iloc[-1]

                log_trade_event(symbol, "BUY", qty, ltp, resp)
                save_state()
            else:
                print(f"[entry] Failed: {resp}")
                st.status = "watch"

    # EXIT EXECUTION
    if st.status == "position":
        # Target
        if st.target_price and ltp >= st.target_price:
             print(f"[exit] TARGET HIT {symbol} @ {ltp} (Target: {st.target_price})")
             place_market_order_wrapper(symbol, st.qty, "sell")
             st.status = "watch"
             st.qty = 0
             log_trade_event(symbol, "SELL_TARGET", st.qty, ltp, {})
             save_state()
             return

        # Stop Loss
        if st.stop_price and ltp <= st.stop_price:
             print(f"[exit] STOP LOSS HIT {symbol} @ {ltp} (SL: {st.stop_price})")
             place_market_order_wrapper(symbol, st.qty, "sell")
             st.status = "watch"
             st.qty = 0
             log_trade_event(symbol, "SELL_SL", st.qty, ltp, {})
             save_state()
             return

        # EMA Exit
        if st.exit_pending and st.exit_signal_candle:
            trigger = st.exit_signal_candle["low"]
            if ltp < trigger:
                 print(f"[exit] EMA EXIT {symbol} @ {ltp} (Break < {trigger})")
                 place_market_order_wrapper(symbol, st.qty, "sell")
                 st.status = "watch"
                 log_trade_event(symbol, "SELL_EMA", st.qty, ltp, {})
                 save_state()
                 return

        # Trailing SL
        if TRAIL_ATR_MULT and st.atr_at_entry > 0 and not st.sl_trailed:
            dist = st.atr_at_entry * TRAIL_ATR_MULT
            if ltp >= (st.entry_price + dist):
                print(f"[trail] Moving SL to Entry for {symbol}")
                st.stop_price = st.entry_price
                st.sl_trailed = True

def save_state():
    # Minimal save
    data = {}
    for s, st in SYMBOL_STATES.items():
        if st.status == "position":
            data[s] = {
                "status": st.status,
                "qty": st.qty,
                "entry_price": st.entry_price,
                "stop_price": st.stop_price,
                "target_price": st.target_price,
                "sl_trailed": st.sl_trailed
            }
    try:
        with open(STATE_DUMP, "w") as f:
            json.dump(data, f)
    except: pass

def load_state():
    if os.path.exists(STATE_DUMP):
        try:
            with open(STATE_DUMP, "r") as f:
                data = json.load(f)
            for s, info in data.items():
                if s in SYMBOL_STATES:
                    st = SYMBOL_STATES[s]
                    st.status = info.get("status", "watch")
                    st.qty = info.get("qty", 0)
                    st.entry_price = info.get("entry_price", 0.0)
                    st.stop_price = info.get("stop_price", 0.0)
                    st.target_price = info.get("target_price")
                    st.sl_trailed = info.get("sl_trailed", False)
        except: pass

# ---------------------------- INITIALIZATION ----------------------------
def parse_args():
    parser = argparse.ArgumentParser(description="Delta Exchange EMA Strategy Bot")
    parser.add_argument("--timeframe", type=int, default=TIMEFRAME_MIN, help="Timeframe in minutes (default: 5)")
    parser.add_argument("--exit-ema", type=int, default=EXIT_EMA, help="Exit EMA period (default: 50)")
    parser.add_argument("--entry-fast-ema", type=int, default=ENTRY_FAST_EMA, help="Entry Fast EMA period (default: 20)")
    parser.add_argument("--entry-slow-ema", type=int, default=ENTRY_SLOW_EMA, help="Entry Slow EMA period (default: 50)")
    parser.add_argument("--min-range-pct", type=float, default=MIN_RANGE_PCT, help="Minimum candle range %% (default: 0.0)")
    parser.add_argument("--ema-buffer", type=float, default=EMA_BUFFER, help="EMA Buffer (default: 0.0)")
    parser.add_argument("--trail-atr-mult", type=float, default=TRAIL_ATR_MULT, help="Trailing ATR Multiplier (default: 1.0)")
    return parser.parse_args()

def main():
    global TIMEFRAME_MIN, EXIT_EMA, ENTRY_FAST_EMA, ENTRY_SLOW_EMA, MIN_RANGE_PCT, EMA_BUFFER, TRAIL_ATR_MULT

    args = parse_args()
    TIMEFRAME_MIN = args.timeframe
    EXIT_EMA = args.exit_ema
    ENTRY_FAST_EMA = args.entry_fast_ema
    ENTRY_SLOW_EMA = args.entry_slow_ema
    MIN_RANGE_PCT = args.min_range_pct
    EMA_BUFFER = args.ema_buffer
    TRAIL_ATR_MULT = args.trail_atr_mult

    print(f"[init] Checking server connectivity...")
    # Ping or simple get
    try:
        t1 = time.time()
        products = CLIENT.get_products()
        latency = (time.time() - t1) * 1000
        print(f"[init]   - India: {latency:.1f}ms")
        print(f"[init] India server is healthy. Selecting India.")
    except Exception as e:
        print(f"[init] Failed to connect: {e}")
        return

    print(f"[delta] Fetching product list from {BASE_URL}...")
    if "result" in products:
        for p in products["result"]:
            sym = p.get("symbol")
            if sym in SYMBOL_STATES:
                pid = p.get("id")
                cval = p.get("contract_value", "1")
                # Inverse check: settling_asset.symbol == underlying_asset.symbol (roughly)
                # But we saw earlier that BTCUSD (Linear) has settling USD, underlying BTC.
                # If settling == quoting, it's Linear.
                settle = p.get("settling_asset", {}).get("symbol", "")
                quote = p.get("quoting_asset", {}).get("symbol", "")
                is_inv = (settle != quote)

                PRODUCT_MAP[sym] = {
                    "id": pid,
                    "contract_value": float(cval),
                    "is_inverse": is_inv
                }
                ID_TO_SYMBOL[pid] = sym
                print(f"[delta] Mapped {sym} -> ID {pid} | Val: {float(cval)} | Inv: {is_inv}")

    # Warmup
    print(f"[delta] Fetching 24h ticker data from {BASE_URL}...")
    tickers = CLIENT.get_ticker_24h()
    if tickers and "result" in tickers:
        for t in tickers["result"]:
            sym = t.get("symbol")
            if sym in SYMBOL_STATES:
                if "close" in t and "open" in t:
                    c = float(t["close"])
                    o = float(t["open"])
                    chg = ((c - o)/o)*100 if o > 0 else 0
                    SYMBOL_STATES[sym].ltp_change_24h = chg
                if "volume" in t:
                    SYMBOL_STATES[sym].volume_24h = float(t["volume"])

    print(f"[warmup] Fetching historical data...")
    now_ts = int(time.time())
    start_ts = now_ts - (TIMEFRAME_MIN * 60 * 1000) # 1000 candles?

    # We want ~1000 candles
    duration = 1000 * TIMEFRAME_MIN * 60
    start_time = now_ts - duration

    # Resolution string for Delta: TIMEFRAME_MIN as string (e.g. "5")
    # Allowed: 1, 3, 5, 15, 30, 60, 120, 240, 360, D, W, ...
    res_str = str(TIMEFRAME_MIN)

    for sym in SYMBOLS:
        try:
            resp = CLIENT.get_history(sym, res_str, start_time, now_ts)
            if resp and "result" in resp:
                result = resp["result"]
                # Structure: {"t": [], "o": [], "h": [], "l": [], "c": [], "v": [], "s": "ok"}
                if "t" in result and len(result["t"]) > 0:
                    times = result["t"]
                    opens = result["o"]
                    highs = result["h"]
                    lows = result["l"]
                    closes = result["c"]
                    volumes = result["v"] if "v" in result else [0]*len(times)

                    count = len(times)
                    print(f"[warmup] Loaded {count} candles for {sym}")

                    data = []
                    for i in range(count):
                        ts = dt.fromtimestamp(times[i])
                        data.append({
                            "ts": ts,
                            "open": float(opens[i]),
                            "high": float(highs[i]),
                            "low": float(lows[i]),
                            "close": float(closes[i]),
                            "volume": float(volumes[i])
                        })

                    df = pd.DataFrame(data).set_index("ts").sort_index()
                    df.index.name = "datetime"
                    st = SYMBOL_STATES[sym]
                    st.data = compute_indicators(df)
                    if not df.empty:
                        st.last_candle_ts = df.index[-1]
                else:
                    # Try alternate format just in case (list of dicts)
                    if isinstance(result, list):
                        candles = result
                        count = len(candles)
                        print(f"[warmup] Loaded {count} candles for {sym} (list format)")
                        data = []
                        for c in candles:
                            ts = dt.fromtimestamp(c.get("time", c.get("t")))
                            data.append({
                                "ts": ts,
                                "open": float(c.get("open", c.get("o"))),
                                "high": float(c.get("high", c.get("h"))),
                                "low": float(c.get("low", c.get("l"))),
                                "close": float(c.get("close", c.get("c"))),
                                "volume": float(c.get("volume", c.get("v", 0)))
                            })
                        df = pd.DataFrame(data).set_index("ts").sort_index()
                        df.index.name = "datetime"
                        st = SYMBOL_STATES[sym]
                        st.data = compute_indicators(df)
                        if not df.empty:
                            st.last_candle_ts = df.index[-1]
            else:
                 pass
        except Exception as e:
            pass

    print(f"[warmup] Historical data loaded")

    # Print Table
    print("\n" + "="*70)
    print("📊 CURRENT MARKET PRICES (LTP)")
    print("="*70)
    for sym in SYMBOLS:
        st = SYMBOL_STATES[sym]
        chg = st.ltp_change_24h
        icon = "📈" if chg >= 0 else "📉"
        ltp = 0.0
        if not st.data.empty:
            ltp = st.data.iloc[-1]["close"]

        # Format: LTP with commas, Vol with commas
        print(f"{sym:<12} | LTP: $ {ltp:,.2f} | 24h Change: {icon} {chg:>6.2f}% | Vol: {st.volume_24h:,.0f}")

    print("="*70)
    print(f"⏰ TIMEFRAME: {TIMEFRAME_MIN} minute candles")
    print(f"   - Each candle represents {TIMEFRAME_MIN} minutes of price action")
    print(f"   - New candle completes every {TIMEFRAME_MIN} minutes")
    print(f"   - Strategy: Fast({ENTRY_FAST_EMA}) / Slow({ENTRY_SLOW_EMA}) EMA Crossover")
    print(f"   - Trade Allocation: ${ALLOC_DEFAULT} per trade")
    print(f"   - Server: {BASE_URL}")
    print("="*70)
    print("")

    # Start WS
    global CANDLE_MGR
    CANDLE_MGR = CandleManager(TIMEFRAME_MIN, on_completed_candle)

    load_state()

    ws_client = DeltaWS(WS_URL, SYMBOLS, on_tick)
    print(f"[main] Starting WebSocket connection...")
    print(f"[main] Bot running. Press Ctrl+C to exit.")
    ws_client.connect()

    # Heartbeat loop
    while True:
        # Sleep for the strategy timeframe (e.g., 5m -> 300s)
        time.sleep(TIMEFRAME_MIN * 60)

        # Refresh 24h ticker data via REST to ensure accuracy
        print(f"[delta] Fetching 24h ticker data from {BASE_URL}...")
        try:
            tickers = CLIENT.get_ticker_24h()
            if tickers and "result" in tickers:
                for t in tickers["result"]:
                    sym = t.get("symbol")
                    if sym in SYMBOL_STATES:
                        if "close" in t and "open" in t:
                            c = float(t["close"])
                            o = float(t["open"])
                            chg = ((c - o)/o)*100 if o > 0 else 0
                            SYMBOL_STATES[sym].ltp_change_24h = chg
                        if "volume" in t:
                            SYMBOL_STATES[sym].volume_24h = float(t["volume"])
        except Exception as e:
            print(f"[delta] Ticker fetch failed: {e}")

        print(f"[heartbeat] Bot active. Monitoring {len(SYMBOLS)} symbols...")
        print(f"[heartbeat] 📊 Current Market Prices:")
        for sym in SYMBOLS:
             st = SYMBOL_STATES[sym]
             chg = st.ltp_change_24h

             # Calculate Trend Icon based on EMA crossover
             # Default to Neutral/Green if no data
             trend_icon = "🟢"
             if not st.data.empty:
                 last = st.data.iloc[-1]
                 fast = float(last.get("ema_fast_entry", 0))
                 slow = float(last.get("ema_slow_entry", 0))
                 trend_icon = "🟢" if fast > slow else "🔴"

             # Use close or cached ltp
             ltp = 0.0
             if not st.data.empty:
                 ltp = st.data.iloc[-1]["close"]

             status = st.status
             print(f"[heartbeat]   {trend_icon} {sym:<12} | LTP: $ {ltp:,.2f} | 24h Change: {'📈' if chg>=0 else '📉'} {chg:>6.2f}% | Vol: {st.volume_24h:,.0f} | Status: {status}")

if __name__ == "__main__":
    main()
