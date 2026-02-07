# Fast Slow EMA Strategy - Delta Exchange Port (Fyers Logic)
# -*- coding: utf-8 -*-

import time
import json
import threading
import requests
import pandas as pd
import numpy as np
import pytz
import websocket
import sys
import traceback
from datetime import datetime as dt, timedelta
from typing import Dict, Optional, List

# ==============================================================================
# CONFIGURATION
# ==============================================================================
def load_config():
    try:
        with open("config.json", "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading config.json: {e}. Using defaults.")
        return {}

CONFIG = load_config()

# Default to India, but will be updated by auto-selection
BASE_URL = CONFIG.get("base_url", "https://api.india.delta.exchange")
WS_URL = CONFIG.get("ws_url", "wss://socket.india.delta.exchange")

SYMBOLS_TO_MONITOR = CONFIG.get("symbols", ["BTCUSD", "ETHUSD", "SOLUSD"])

# TIMEFRAME_RES: The resolution string required by Delta Exchange API (e.g., "1m", "15m", "1h", "1d").
TIMEFRAME_RES = CONFIG.get("timeframe_res", "1m")

# TIMEFRAME_MINUTES: The numeric duration of the candle in minutes, used for internal logic
# (e.g., determining candle closure, signal expiry time calculations).
TIMEFRAME_MINUTES = CONFIG.get("timeframe_minutes", 1)

LOOKBACK_CANDLES = CONFIG.get("lookback_candles", 1000)

# Strategy Params
STRATEGY = CONFIG.get("strategy", {})
EXIT_EMA = STRATEGY.get("exit_ema", 50)
ENTRY_FAST_EMA = STRATEGY.get("entry_fast_ema", 9)
ENTRY_SLOW_EMA = STRATEGY.get("entry_slow_ema", 15)

EMA_BUFFER = STRATEGY.get("ema_buffer", 0.0)
REQUIRE_GREEN_SIGNAL = STRATEGY.get("require_green_signal", True)
MIN_RANGE_PCT = STRATEGY.get("min_range_pct", 0.0)

SL_MODE = STRATEGY.get("sl_mode", "signal_low")  # "signal_low" or "swing_low"
SWING_LOOKBACK = STRATEGY.get("swing_lookback", 5)
SWING_HIGH_LOOKBACK = STRATEGY.get("swing_high_lookback", 50)
TRAIL_ATR_MULT = STRATEGY.get("trail_atr_mult", 1.0)  # None = disabled

# Simulation / Paper Trading
PAPER_CFG = CONFIG.get("paper_trading", {})
PAPER_TRADE = PAPER_CFG.get("enabled", True)
MAX_CONCURRENT_POS = PAPER_CFG.get("max_concurrent_pos", 3)
PAPER_BALANCE = PAPER_CFG.get("balance", 10000.0) # Starting Balance in USD
PAPER_PNL = 0.0

# Timezone
TIMEZONE = CONFIG.get("timezone", "Asia/Kolkata")
IST = pytz.timezone(TIMEZONE)

# ==============================================================================
# LOGGING HELPER
# ==============================================================================
def log(tag, message):
    timestamp = dt.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{tag}] {message}")

# ==============================================================================
# SERVER SELECTION
# ==============================================================================
def select_best_server():
    global BASE_URL, WS_URL

    # Define endpoints
    india = ("India", "https://api.india.delta.exchange", "wss://socket.india.delta.exchange")
    global_srv = ("Global", "https://api.delta.exchange", "wss://socket.delta.exchange")

    log("init", "Checking server connectivity...")

    def check_server(srv):
        name, api, ws = srv
        try:
            start = time.time()
            requests.head(f"{api}/v2/products", timeout=5)
            lat = (time.time() - start) * 1000
            return lat
        except Exception as e:
            return None

    # 1. Check India First (Preferred)
    lat_india = check_server(india)
    if lat_india is not None:
        log("init", f"  - India: {lat_india:.1f}ms")
        if lat_india < 2000:
            log("init", "India server is healthy. Selecting India.")
            BASE_URL = india[1]
            WS_URL = india[2]
            return

    # 2. If India failed or is slow, Check Global
    if lat_india is None:
        log("init", "  - India: Failed/Timeout")
    else:
        log("init", "  - India: Slow (>2s)")

    lat_global = check_server(global_srv)
    if lat_global is not None:
        log("init", f"  - Global: {lat_global:.1f}ms")
        log("init", "Selecting Global server due to India connectivity issues.")
        BASE_URL = global_srv[1]
        WS_URL = global_srv[2]
        return
    else:
        log("init", "  - Global: Failed/Timeout")

    # 3. Fallback
    if lat_india is not None:
         log("warning", "Both servers slow/failed check, but India responded. Using India.")
         BASE_URL = india[1]
         WS_URL = india[2]
    elif lat_global is not None:
         log("warning", "India failed, Global responded (slow). Using Global.")
         BASE_URL = global_srv[1]
         WS_URL = global_srv[2]
    else:
         log("error", "CRITICAL: Unable to connect to any Delta Exchange server.")
         BASE_URL = india[1]
         WS_URL = india[2]

# ==============================================================================
# INDICATORS
# ==============================================================================
def compute_ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def compute_atr(df, length=14):
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
    df["ema_exit"] = compute_ema(df["close"], EXIT_EMA)
    df["ema_fast_entry"] = compute_ema(df["close"], ENTRY_FAST_EMA)
    df["ema_slow_entry"] = compute_ema(df["close"], ENTRY_SLOW_EMA)
    df["atr"] = compute_atr(df, 14)
    return df

# ==============================================================================
# STATE & CANDLE MANAGER
# ==============================================================================
class SymbolState:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.data = pd.DataFrame()
        self.status = "watch"  # watch, entry_pending, position, cooldown

        # Signal tracking
        self.signal_candle = None # dict
        self.signal_close_ts = None
        self.signal_expiry = None

        # Position tracking
        self.entry_price = 0.0
        self.qty = 0
        self.stop_price = 0.0
        self.target_price = None
        self.potential_target_price = None

        # Trailing
        self.atr_at_entry = 0.0
        self.sl_trailed = False

        # Exit logic
        self.exit_pending = False
        self.exit_signal_candle = None
        self.exit_try_count = 0
        self.last_failed_exit_ts = None

        self.last_candle_ts = None
        self.entry_time = 0.0
        self.just_entered = False
        self.current_ltp = 0.0

class CandleManager:
    def __init__(self, timeframe_min=15):
        self.tf = timeframe_min
        self.partial = {} # symbol -> dict

    def _floor_ts(self, ts: dt):
        # Round down to nearest timeframe interval
        minute = (ts.minute // self.tf) * self.tf
        return ts.replace(second=0, microsecond=0, minute=minute)

    def process_tick(self, symbol, ltp, ts_val):
        # ts_val is either int timestamp (seconds/ms) or ISO string
        try:
            ts = None
            # Heuristic check for milliseconds timestamp (year 56105 implies micro/milliseconds mismatch)
            # Current timestamp ~ 1.7e9 (seconds). 1.7e12 (ms), 1.7e15 (us)

            if isinstance(ts_val, (int, float)):
                 # If timestamp is huge (> 3000-01-01), assume MS or US
                 if ts_val > 32503680000: # Year 3000 in seconds
                     ts_val = ts_val / 1000000.0 # Try converting US to S? or MS to S

                 # Recheck reasonable range (Year 2000 - 2100)
                 # 946684800 (2000) to 4102444800 (2100)
                 if ts_val > 4102444800:
                     ts_val = ts_val / 1000.0 # Maybe it was US?

                 # Important: fromtimestamp() returns Local Time. We want UTC aware first.
                 ts = dt.fromtimestamp(ts_val, pytz.utc)
            else:
                 # String parsing
                 ts = pd.to_datetime(ts_val).to_pydatetime()

            # Localize if naive (Delta sends UTC usually)
            if ts.tzinfo is None:
                ts = pytz.utc.localize(ts)

            # Convert to IST for logic consistency
            ts_ist = ts.astimezone(IST).replace(tzinfo=None)

            candle_start = self._floor_ts(ts_ist)

            p = self.partial.get(symbol)

            # Initialize partial candle if none
            if p is None:
                p = {
                    "ts": candle_start,
                    "open": ltp, "high": ltp, "low": ltp, "close": ltp,
                    "ticks": 1
                }
                self.partial[symbol] = p
                return None # No closed candle yet

            # Check if we moved to a new candle bucket
            if candle_start > p["ts"]:
                # The previous candle is complete
                completed = p.copy()

                # Start new candle
                self.partial[symbol] = {
                    "ts": candle_start,
                    "open": ltp, "high": ltp, "low": ltp, "close": ltp,
                    "ticks": 1
                }
                return completed # Return the CLOSED candle

            # Update current candle
            p["high"] = max(p["high"], ltp)
            p["low"] = min(p["low"], ltp)
            p["close"] = ltp
            p["ticks"] += 1
            self.partial[symbol] = p
            return None

        except Exception as e:
            # log("error", f"CandleManager error: {e} | Val: {ts_val}")
            return None

# ==============================================================================
# GLOBAL STATE
# ==============================================================================
SYMBOL_STATES: Dict[str, SymbolState] = {s: SymbolState(s) for s in SYMBOLS_TO_MONITOR}
CANDLE_MANAGER = CandleManager(TIMEFRAME_MINUTES)

# ==============================================================================
# DELTA EXCHANGE API CLIENT
# ==============================================================================
class DeltaClient:
    def __init__(self):
        self.products = {}
        self.id_to_symbol = {}

    def fetch_products(self):
        log("delta", f"Fetching product list from {BASE_URL}...")
        try:
            url = f"{BASE_URL}/v2/products"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("success"):
                for p in data.get("result", []):
                    sym = p.get("symbol")
                    pid = p.get("id")
                    if sym in SYMBOLS_TO_MONITOR:
                        self.products[sym] = pid
                        self.id_to_symbol[pid] = sym
                        log("delta", f"Mapped {sym} -> product_id {pid}")
            else:
                log("error", "Failed to fetch products: " + str(data))
        except Exception as e:
            log("error", f"Error fetching products: {e}")

    def fetch_history(self, symbol, resolution, num_candles):
        now_ts = int(time.time())
        res_map = {
            "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200, "1d": 86400
        }
        res_sec = res_map.get(resolution, 60)
        start_ts = now_ts - ((num_candles + 50) * res_sec)

        params = {
            "symbol": symbol, "resolution": resolution,
            "start": start_ts, "end": now_ts
        }
        try:
            url = f"{BASE_URL}/v2/history/candles"
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            if data.get("success"):
                candles = data.get("result", [])
                df = pd.DataFrame(candles)
                if not df.empty:
                    # Delta returns: close, high, low, open, time, volume
                    df = df.sort_values(by="time").reset_index(drop=True)
                    # Convert timestamp to datetime (UTC -> IST naive)
                    df["ts"] = pd.to_datetime(df["time"], unit='s', utc=True).dt.tz_convert(IST).dt.tz_localize(None)
                    df = df.set_index("ts")[["open", "high", "low", "close", "volume"]]
                    return df
            return pd.DataFrame()
        except Exception as e:
            log("error", f"History fetch failed for {symbol}: {e}")
            return pd.DataFrame()

# ==============================================================================
# STRATEGY LOGIC (Ported)
# ==============================================================================
def compute_prev_swing_high_for_entry(state, lookback, reference_price):
    try:
        df = state.data
        if df.empty: return None

        # Prior data only
        if state.signal_candle:
            sig_ts = state.signal_candle["ts"]
            df_up_to = df.loc[:sig_ts]
        else:
            df_up_to = df

        prior = df_up_to.iloc[:-1].tail(lookback).copy()
        if prior.empty: return None

        highs = prior["high"].values
        if len(highs) < 5: return prior["high"].max()

        pivot_width = 2
        peaks = []
        for i in range(pivot_width, len(highs) - pivot_width):
            current = highs[i]
            is_peak = True
            for j in range(1, pivot_width + 1):
                if highs[i-j] >= current or highs[i+j] >= current:
                    is_peak = False
                    break
            if is_peak: peaks.append(current)

        if reference_price is not None:
            valid_peaks = [p for p in peaks if p > reference_price]
            if valid_peaks: return valid_peaks[-1] # Latest peak

        return prior["high"].max()
    except Exception as e:
        log("error", f"Swing high error: {e}")
        return None

def compute_swing_low_for_signal(state, lookback):
    try:
        if state.signal_candle:
            df = state.data.loc[:state.signal_candle["ts"]]
        else:
            df = state.data
        if df.empty: return float("nan")
        return df.tail(lookback)["low"].min()
    except Exception:
        return float("nan")

def evaluate_on_new_candle(st: SymbolState):
    df = st.data
    if df.shape[0] < 2: return

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    curr_open = curr["open"]
    curr_close = curr["close"]
    curr_high = curr["high"]
    curr_low = curr["low"]

    ema_fast = curr.get("ema_fast_entry")
    ema_slow = curr.get("ema_slow_entry")
    ema_slow_prev = prev.get("ema_slow_entry")
    ema_exit = curr.get("ema_exit")

    # ENTRY SIGNAL
    if st.status == "watch":
        ema_sequence_ok = ema_fast > ema_slow
        rising_slow_ema = ema_slow > ema_slow_prev

        highest_ema = max(ema_fast, ema_slow)

        touched_slow_ema = curr_low <= ema_slow
        closed_above_both = curr_close > (highest_ema + EMA_BUFFER)
        green_ok = (not REQUIRE_GREEN_SIGNAL) or (curr_close > curr_open)
        higher_high = curr_high > prev["high"]

        if (ema_sequence_ok and rising_slow_ema and touched_slow_ema
            and closed_above_both and green_ok and higher_high):

            target = compute_prev_swing_high_for_entry(st, SWING_HIGH_LOOKBACK, curr_high)

            if target is None or target <= curr_high:
                log("signal-filtered", f"{st.symbol} Signal ignored. No target > signal_high ({curr_high})")
                return

            st.potential_target_price = target
            st.signal_candle = {
                "ts": curr.name,
                "high": curr_high,
                "low": curr_low
            }
            # Delta Time-based expiry: Signal valid for next candle only
            # curr.name is the start time of the *just closed* signal candle.
            # We want to allow entry during the *entire* next candle (which just started).
            # So expiry = start_time + TF (end of signal candle) + TF (end of next candle) = +2*TF total.
            st.signal_expiry = curr.name + timedelta(minutes=TIMEFRAME_MINUTES * 2)
            st.status = "entry_pending"

            log("signal", f"🔵 ENTRY SIGNAL {st.symbol} | High: {curr_high} | Target: {target:.2f} | Wait for break > High (Expires: {st.signal_expiry})")

    # EXIT SIGNAL (Red candle close below Exit EMA)
    if st.status == "position":
        intrabar_up = (curr_open < ema_exit) and (curr_high > ema_exit)
        closed_below = curr_close < (ema_exit - EMA_BUFFER)
        is_red = curr_close < curr_open

        if is_red and intrabar_up and closed_below:
            st.exit_signal_candle = {
                "ts": curr.name,
                "low": curr_low
            }
            st.exit_pending = True
            log("signal", f"🟠 EXIT SIGNAL {st.symbol} | Low: {curr_low} | Wait for break < Low")

def on_tick(symbol, ltp, ts):
    st = SYMBOL_STATES.get(symbol)
    if not st: return

    # Update real-time LTP
    st.current_ltp = ltp

    # 1. Update Candle Manager
    closed_candle = CANDLE_MANAGER.process_tick(symbol, ltp, ts)
    if closed_candle:
        # Append to DataFrame
        row = pd.DataFrame([closed_candle])
        row = row.set_index("ts")[["open", "high", "low", "close"]]

        if st.data.empty:
            st.data = row
        else:
            st.data = pd.concat([st.data, row])
            # Drop dupes
            st.data = st.data.loc[~st.data.index.duplicated(keep='last')]

        # Truncate
        if len(st.data) > LOOKBACK_CANDLES:
            st.data = st.data.tail(LOOKBACK_CANDLES)

        # Recompute Indicators
        st.data = compute_indicators(st.data)
        st.last_candle_ts = closed_candle["ts"]

        # Run Strategy
        evaluate_on_new_candle(st)

    # 2. Check Triggers (LTP)

    # ENTRY TRIGGER
    if st.status == "entry_pending" and st.signal_candle:
        # Check Expiration (Strict Next Candle Rule)
        # Convert ts (float/int or naive dt) to timezone-aware datetime for comparison
        ts_obj = None
        if isinstance(ts, (int, float)):
             # Ensure we convert the incoming UTC timestamp to IST correctly for comparison with signal_expiry (which is IST)
             ts_obj = dt.fromtimestamp(ts, pytz.utc).astimezone(IST).replace(tzinfo=None)
        elif isinstance(ts, dt):
             ts_obj = ts
             if ts_obj.tzinfo:
                 ts_obj = ts_obj.astimezone(IST).replace(tzinfo=None)

        # Ensure naive for comparison if signal_expiry is naive
        if st.signal_expiry and st.signal_expiry.tzinfo is None and ts_obj.tzinfo is not None:
             ts_obj = ts_obj.replace(tzinfo=None) # Naive comparison

        if st.signal_expiry and ts_obj and ts_obj > st.signal_expiry:
             log("signal-expired", f"⏰ Signal Expired for {symbol} (No entry in next candle). Resetting to Watch.")
             st.status = "watch"
             st.signal_candle = None
             return

        trigger = st.signal_candle["high"]
        if ltp > trigger:
            st.entry_price = ltp
            # Let's say $1000 alloc per trade
            alloc = 1000.0
            st.qty = alloc / ltp

            log("trade", f"🚀 [PAPER] ENTER BUY {symbol} @ {ltp} (Trigger {trigger}) | Qty: {st.qty:.4f}")
            st.status = "position"
            st.target_price = st.potential_target_price

            # Stop Loss
            if SL_MODE == "signal_low":
                st.stop_price = st.signal_candle["low"]
            else:
                swing = compute_swing_low_for_signal(st, SWING_LOOKBACK)
                st.stop_price = st.signal_candle["low"] if np.isnan(swing) else swing

            # ATR
            if not st.data.empty:
                st.atr_at_entry = st.data.iloc[-1]["atr"]

            log("trade", f"   Target: {st.target_price} | Stop: {st.stop_price}")
            st.signal_candle = None

    # EXIT TRIGGERS
    if st.status == "position":
        global PAPER_BALANCE
        exit_price = 0.0
        reason = ""

        # Target
        if st.target_price and ltp >= st.target_price:
            exit_price = ltp
            reason = "TARGET HIT"

        # Stop Loss
        elif st.stop_price and ltp <= st.stop_price:
            exit_price = ltp
            reason = "STOP LOSS HIT"

        # Exit EMA Trigger
        elif st.exit_pending and st.exit_signal_candle:
            trigger = st.exit_signal_candle["low"]
            if ltp < trigger:
                exit_price = ltp
                reason = "EXIT EMA TRIGGER"

        if exit_price > 0:
            pnl = (exit_price - st.entry_price) * st.qty
            PAPER_BALANCE += pnl
            PAPER_PNL += pnl
            icon = "✅" if pnl >= 0 else "❌"

            log("trade", f"{icon} [PAPER] {reason} {symbol} @ {exit_price} | PnL: ${pnl:.2f}")
            log("trade", f"   Account Balance: ${PAPER_BALANCE:.2f}")

            st.status = "watch"
            st.qty = 0

        # Trailing Stop
        if st.status == "position" and TRAIL_ATR_MULT and st.atr_at_entry > 0 and not st.sl_trailed:
            dist = st.atr_at_entry * TRAIL_ATR_MULT
            if ltp >= (st.entry_price + dist):
                st.stop_price = st.entry_price
                st.sl_trailed = True
                log("trade", f"🛡️ TRAILING STOP moved to Breakeven {st.entry_price}")

# ==============================================================================
# MAIN LOGIC
# ==============================================================================
def main():
    select_best_server()
    client = DeltaClient()
    client.fetch_products()

    # Warmup
    log("warmup", "Fetching historical data...")
    for sym in SYMBOLS_TO_MONITOR:
        df = client.fetch_history(sym, TIMEFRAME_RES, LOOKBACK_CANDLES)
        if not df.empty:
            df = compute_indicators(df)
            SYMBOL_STATES[sym].data = df
            SYMBOL_STATES[sym].last_candle_ts = df.index[-1]
            log("warmup", f"Loaded {len(df)} candles for {sym}")

    log("warmup", "Historical data loaded")

    print("\n" + "="*70)
    print("📊 CURRENT MARKET PRICES (LTP)")
    print("="*70)
    for sym in SYMBOLS_TO_MONITOR:
        st = SYMBOL_STATES[sym]
        if not st.data.empty:
            last = st.data.iloc[-1]
            print(f"{sym:<12} | LTP: $ {last['close']:,.2f} | 24h Change: 📈   +0.00% | Vol: {int(last.get('volume',0)):,}")
    print("="*70)
    print(f"⏰ TIMEFRAME: {TIMEFRAME_MINUTES} minute candles")
    print(f"   - Each candle represents {TIMEFRAME_MINUTES} minutes of price action")
    print(f"   - New candle completes every {TIMEFRAME_MINUTES} minutes")
    print(f"   - Strategy: Fast({ENTRY_FAST_EMA}) / Slow({ENTRY_SLOW_EMA}) EMA Crossover")
    print(f"   - Server: {BASE_URL}")
    print("="*70 + "\n")

    # WebSocket
    log("main", "Starting WebSocket connection...")
    log("main", "Bot running. Press Ctrl+C to exit.")

    def on_ws_open(ws):
        log("ws", f"Connected to Delta Exchange ({WS_URL})")
        payload = {
            "type": "subscribe",
            "payload": {
                "channels": [
                    {
                        "name": "v2/ticker",
                        "symbols": SYMBOLS_TO_MONITOR
                    }
                ]
            }
        }
        ws.send(json.dumps(payload))
        log("ws", f"Subscribed to {len(SYMBOLS_TO_MONITOR)} symbols")

    def on_ws_message(ws, message):
        try:
            data = json.loads(message)
            if data.get("type") == "v2/ticker":
                # Ticker update
                # Format: {"type": "v2/ticker", "symbol": "BTCUSD", "mark_price": ..., "close": ...}
                # Delta Ticker channel sends full object
                sym = data.get("symbol")
                # Use close or mark_price as LTP
                ltp = data.get("close") or data.get("mark_price")
                ts = data.get("timestamp") or time.time()

                if sym and ltp:
                     # Check if ts is None/missing and fallback to time.time()
                     if not ts: ts = time.time()
                     on_tick(sym, float(ltp), ts)

        except Exception:
            log("ws_error", f"Error in WebSocket processing: {traceback.format_exc()}")

    def on_ws_error(ws, error):
        log("ws_error", f"WebSocket Error: {error}")

    def on_ws_close(ws, close_status_code, close_msg):
        log("ws_close", "WebSocket Closed. Reconnecting...")

    def run_ws():
        while True:
            try:
                wsa = websocket.WebSocketApp(
                    WS_URL,
                    on_open=on_ws_open,
                    on_message=on_ws_message,
                    on_error=on_ws_error,
                    on_close=on_ws_close
                )
                wsa.run_forever(ping_interval=10, ping_timeout=5)
            except Exception as e:
                log("ws_exception", f"Exception: {e}")
            time.sleep(5)

    ws_thread = threading.Thread(target=run_ws)
    ws_thread.daemon = True
    ws_thread.start()

    try:
        while True:
            # Wait for 1 minute before first heartbeat, then loop
            time.sleep(60)
            log("heartbeat", f"Bot active. Monitoring {len(SYMBOLS_TO_MONITOR)} symbols...")
            log("heartbeat", f"📊 Current Market Prices:")
            for sym in SYMBOLS_TO_MONITOR:
                st = SYMBOL_STATES[sym]
                if not st.data.empty:
                    last = st.data.iloc[-1]
                    # Determine trend based on fast/slow
                    fast = last.get("ema_fast_entry", 0)
                    slow = last.get("ema_slow_entry", 0)
                    trend = "🟢" if fast > slow else "🔴"

                    # Use real-time LTP if available, else last close
                    display_ltp = st.current_ltp if st.current_ltp > 0 else last['close']

                    # Log
                    print(f"[heartbeat]   {trend} {sym:<12} | LTP: $ {display_ltp:,.2f} | Status: {st.status}")

    except KeyboardInterrupt:
        print("\nExiting...")

if __name__ == "__main__":
    main()
