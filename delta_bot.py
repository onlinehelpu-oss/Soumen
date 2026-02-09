# -- coding: utf-8 --
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
import os
from datetime import datetime as dt, timedelta
from typing import Dict, Optional, List

# ==============================================================================
# CONFIGURATION
# ==============================================================================
def load_config():
    try:
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        with open(config_path, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading config.json: {e}. Using defaults.")
        return {}

CONFIG = load_config()

BASE_URL = CONFIG.get("base_url", "https://api.india.delta.exchange")
WS_URL = CONFIG.get("ws_url", "wss://socket.india.delta.exchange")

SYMBOLS_TO_MONITOR = CONFIG.get("symbols", ["BTCUSD", "ETHUSD", "SOLUSD"])

TIMEFRAME_MINUTES = CONFIG.get("timeframe_minutes", 5)

def get_resolution_str(minutes):
    if minutes < 60:
        return f"{minutes}m"
    elif minutes % 60 == 0 and minutes < 1440:
        return f"{minutes // 60}h"
    elif minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    else:
        return "1m"

TIMEFRAME_RES = get_resolution_str(TIMEFRAME_MINUTES)
LOOKBACK_CANDLES = CONFIG.get("lookback_candles", 1000)

STRATEGY = CONFIG.get("strategy", {})
# REMOVED FAST EMA
ENTRY_SLOW_EMA = STRATEGY.get("entry_slow_ema", 50)
SUPERTREND_PERIOD = STRATEGY.get("supertrend_period", 10)
SUPERTREND_MULTIPLIER = STRATEGY.get("supertrend_multiplier", 3.0)

EMA_BUFFER = STRATEGY.get("ema_buffer", 0.0)
REQUIRE_GREEN_SIGNAL = STRATEGY.get("require_green_signal", False) # Default to False for SELL strategy?
MIN_RANGE_PCT = STRATEGY.get("min_range_pct", 0.0)

SL_MODE = STRATEGY.get("sl_mode", "signal_high") # Default to signal_high for SELL
SWING_LOOKBACK = STRATEGY.get("swing_lookback", 5)
SWING_HIGH_LOOKBACK = STRATEGY.get("swing_high_lookback", 100)
TRAIL_ATR_MULT = STRATEGY.get("trail_atr_mult", 1.0)

PAPER_CFG = CONFIG.get("paper_trading", {})
PAPER_TRADE = PAPER_CFG.get("enabled", True)
MAX_CONCURRENT_POS = PAPER_CFG.get("max_concurrent_pos", 3)
PAPER_BALANCE = PAPER_CFG.get("balance", 10000.0)
TRADE_ALLOCATION = PAPER_CFG.get("trade_allocation", 275.0)
TAKER_FEE_PCT = PAPER_CFG.get("taker_fee_pct", 0.05) / 100.0
MAKER_FEE_PCT = PAPER_CFG.get("maker_fee_pct", 0.02) / 100.0
PAPER_PNL = 0.0

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
        except Exception:
            return None

    lat_india = check_server(india)
    if lat_india is not None:
        log("init", f"  - India: {lat_india:.1f}ms")
        if lat_india < 2000:
            BASE_URL = india[1]
            WS_URL = india[2]
            return

    lat_global = check_server(global_srv)
    if lat_global is not None:
        BASE_URL = global_srv[1]
        WS_URL = global_srv[2]
        return

    # Fallback
    if lat_india is not None:
        BASE_URL = india[1]
        WS_URL = india[2]
    elif lat_global is not None:
        BASE_URL = global_srv[1]
        WS_URL = global_srv[2]
    else:
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
    return tr.ewm(alpha=1 / length, adjust=False).mean()

def compute_supertrend(df, period, multiplier):
    # Ensure df is sorted
    # df = df.sort_index() # Assumed sorted by caller

    high = df['high']
    low = df['low']
    close = df['close']

    atr = compute_atr(df, period)

    hl2 = (high + low) / 2
    basic_upper = hl2 + (multiplier * atr)
    basic_lower = hl2 - (multiplier * atr)

    final_upper = pd.Series(index=df.index, dtype='float64')
    final_lower = pd.Series(index=df.index, dtype='float64')
    supertrend = pd.Series(index=df.index, dtype='float64')
    trend = pd.Series(index=df.index, dtype='int64') # 1: Green, -1: Red

    # Initialize first values
    final_upper.iloc[0] = basic_upper.iloc[0]
    final_lower.iloc[0] = basic_lower.iloc[0]
    trend.iloc[0] = 1
    supertrend.iloc[0] = final_lower.iloc[0]

    for i in range(1, len(df)):
        # Calculate Final Upper
        if basic_upper.iloc[i] < final_upper.iloc[i-1] or close.iloc[i-1] > final_upper.iloc[i-1]:
            final_upper.iloc[i] = basic_upper.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i-1]

        # Calculate Final Lower
        if basic_lower.iloc[i] > final_lower.iloc[i-1] or close.iloc[i-1] < final_lower.iloc[i-1]:
            final_lower.iloc[i] = basic_lower.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i-1]

        # Trend
        prev_trend = trend.iloc[i-1]

        if prev_trend == 1: # Uptrend
            if close.iloc[i] < final_lower.iloc[i-1]: # Trend Reversal to Down
                trend.iloc[i] = -1
                supertrend.iloc[i] = final_upper.iloc[i]
            else:
                trend.iloc[i] = 1
                supertrend.iloc[i] = final_lower.iloc[i]
        else: # Downtrend
            if close.iloc[i] > final_upper.iloc[i-1]: # Trend Reversal to Up
                trend.iloc[i] = 1
                supertrend.iloc[i] = final_lower.iloc[i]
            else:
                trend.iloc[i] = -1
                supertrend.iloc[i] = final_upper.iloc[i]

    df['supertrend'] = supertrend
    df['supertrend_trend'] = trend
    df['atr'] = atr # Make sure ATR is available for trailing logic if needed
    return df

def compute_indicators(df):
    if df is None or df.empty:
        return df
    df = df.copy()
    # Compute Slow EMA
    df["ema_slow_entry"] = compute_ema(df["close"], ENTRY_SLOW_EMA)
    # Compute Supertrend
    df = compute_supertrend(df, SUPERTREND_PERIOD, SUPERTREND_MULTIPLIER)
    return df

# ==============================================================================
# STATE & CANDLE MANAGER
# ==============================================================================
class SymbolState:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.data = pd.DataFrame()
        self.status = "watch" # watch, entry_pending, position, cooldown

        self.contract_value = 1.0
        self.is_inverse = False
        self.change_24h = 0.0

        self.signal_candle = None
        self.signal_close_ts = None
        self.signal_expiry = None

        self.entry_price = 0.0
        self.qty = 0
        self.stop_price = 0.0
        self.target_price = None
        self.potential_target_price = None

        self.atr_at_entry = 0.0
        self.sl_trailed = False

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
        self.partial = {}

    def _floor_ts(self, ts: dt):
        minute = (ts.minute // self.tf) * self.tf
        return ts.replace(second=0, microsecond=0, minute=minute)

    def process_tick(self, symbol, ltp, ts_val):
        try:
            ts = None
            if isinstance(ts_val, (int, float)):
                if ts_val > 32503680000: ts_val /= 1000000.0
                if ts_val > 4102444800: ts_val /= 1000.0
                ts = dt.fromtimestamp(ts_val, pytz.utc)
            else:
                ts = pd.to_datetime(ts_val).to_pydatetime()

            if ts.tzinfo is None: ts = pytz.utc.localize(ts)
            ts_ist = ts.astimezone(IST).replace(tzinfo=None)

            candle_start = self._floor_ts(ts_ist)
            p = self.partial.get(symbol)

            if p is None:
                p = {"ts": candle_start, "open": ltp, "high": ltp, "low": ltp, "close": ltp, "ticks": 1}
                self.partial[symbol] = p
                return None

            if candle_start > p["ts"]:
                completed = p.copy()
                self.partial[symbol] = {"ts": candle_start, "open": ltp, "high": ltp, "low": ltp, "close": ltp, "ticks": 1}
                return completed

            p["high"] = max(p["high"], ltp)
            p["low"] = min(p["low"], ltp)
            p["close"] = ltp
            p["ticks"] += 1
            self.partial[symbol] = p
            return None
        except Exception as e:
            return None

SYMBOL_STATES: Dict[str, SymbolState] = {s: SymbolState(s) for s in SYMBOLS_TO_MONITOR}
CANDLE_MANAGER = CandleManager(TIMEFRAME_MINUTES)

# ==============================================================================
# DELTA EXCHANGE API CLIENT
# ==============================================================================
class DeltaClient:
    def __init__(self):
        self.products = {}
        self.id_to_symbol = {}

    def fetch_tickers(self):
        log("delta", f"Fetching 24h ticker data from {BASE_URL}...")
        try:
            url = f"{BASE_URL}/v2/tickers"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("success"):
                for t in data.get("result", []):
                    sym = t.get("symbol")
                    if sym in SYMBOL_STATES:
                        try:
                            change = float(t.get("ltp_change_24h", 0.0))
                            SYMBOL_STATES[sym].change_24h = change
                        except: pass
        except Exception as e:
            log("error", f"Error fetching tickers: {e}")

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
                        c_val = float(p.get("contract_value", 1.0))
                        settling_sym = p.get("settling_asset", {}).get("symbol", "")
                        quoting_sym = p.get("quoting_asset", {}).get("symbol", "")
                        is_inverse = (settling_sym and quoting_sym and (settling_sym != quoting_sym))
                        st = SYMBOL_STATES.get(sym)
                        if st:
                            st.contract_value = c_val
                            st.is_inverse = is_inverse
                        log("delta", f"Mapped {sym} -> ID {pid} | Val: {c_val} | Inv: {is_inverse}")
        except Exception as e:
            log("error", f"Error fetching products: {e}")

    def fetch_history(self, symbol, resolution, num_candles):
        now_ts = int(time.time())
        res_map = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1d": 86400}
        res_sec = res_map.get(resolution, 60)
        start_ts = now_ts - ((num_candles + 50) * res_sec)
        params = {"symbol": symbol, "resolution": resolution, "start": start_ts, "end": now_ts}
        try:
            url = f"{BASE_URL}/v2/history/candles"
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if data.get("success"):
                candles = data.get("result", [])
                df = pd.DataFrame(candles)
                if not df.empty:
                    df = df.sort_values(by="time").reset_index(drop=True)
                    df["ts"] = pd.to_datetime(df["time"], unit='s', utc=True).dt.tz_convert(IST).dt.tz_localize(None)
                    df = df.set_index("ts")[["open", "high", "low", "close", "volume"]]
                    return df
            return pd.DataFrame()
        except Exception as e:
            log("error", f"History fetch failed for {symbol}: {e}")
            return pd.DataFrame()

# ==============================================================================
# STRATEGY LOGIC
# ==============================================================================
def compute_prev_swing_high_for_entry(state, lookback, reference_price):
    try:
        df = state.data
        if df.empty: return None
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
                if highs[i - j] >= current or highs[i + j] >= current:
                    is_peak = False; break
            if is_peak: peaks.append(current)

        if reference_price is not None:
            valid_peaks = [p for p in peaks if p > reference_price]
            if valid_peaks: return valid_peaks[-1]
        return prior["high"].max()
    except Exception as e:
        log("error", f"Swing high error: {e}")
        return None

def compute_swing_low_for_signal(state, lookback):
    # Not used for SELL usually, maybe swing high for SL
    return float("nan")

def compute_swing_high_for_signal(state, lookback):
    try:
        if state.signal_candle:
            df = state.data.loc[:state.signal_candle["ts"]]
        else:
            df = state.data
        if df.empty: return float("nan")
        return df.tail(lookback)["high"].max()
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

    # Strategy Variables
    ema_slow = curr.get("ema_slow_entry")
    ema_slow_prev = prev.get("ema_slow_entry")

    st_trend = curr.get("supertrend_trend") # 1 or -1
    st_val = curr.get("supertrend")

    # ENTRY SIGNAL (SELL)
    if st.status == "watch":
        # 1. Candle crossed and closed below Slow EMA
        # Meaning: Prev Close >= Prev Slow EMA (or simply was above) AND Curr Close < Curr Slow EMA
        # Or just "Crossed Below"
        # Using strict crossover logic:
        # Check if previous candle was NOT strictly below EMA (i.e. >=)
        # Check if current candle IS strictly below EMA (<)

        # User said: "any candle crossed and closed below Slow Ema"
        crossed_below_slow_ema = (prev["close"] >= ema_slow_prev) and (curr_close < ema_slow)

        # 2. Supertrend must be red (Trend == -1)
        supertrend_red = (st_trend == -1)

        if crossed_below_slow_ema and supertrend_red:
            st.signal_candle = {
                "ts": curr.name,
                "high": curr_high,
                "low": curr_low,
                "close": curr_close
            }

            # Expiry: Signal valid for next candle only (or user configurable? Assume next candle as per original logic)
            # "if next candle break the low of signal candle take entry"
            st.signal_expiry = curr.name + timedelta(minutes=TIMEFRAME_MINUTES * 2)
            st.status = "entry_pending"

            log("signal", f"🔴 SELL SIGNAL {st.symbol} | Low: {curr_low} | Supertrend: RED | Wait for break < Low")

    # EXIT SIGNAL (Supertrend turns Green)
    if st.status == "position":
        # Exit when super trend just convert into green candle close basis
        if st_trend == 1: # Green
            st.exit_signal_candle = {
                "ts": curr.name,
                "close": curr_close
            }
            # Trigger exit immediately because it is "close basis"
            # We are evaluating AFTER candle close. So we exit NOW.
            st.exit_pending = True
            log("signal", f"🟢 EXIT SIGNAL {st.symbol} | Supertrend turned GREEN | Exiting now...")

            # Since exit is on close, and this function runs on close, we can set flag to force exit in on_tick or just log it.
            # In simulation, we need to execute it.
            # The 'evaluate_on_new_candle' is called from on_tick after candle close processing.
            # So next tick will handle exit if we set exit_pending?
            # Or we should execute exit here?
            # Original code sets exit_pending = True and waits for break.
            # But user said "exit will be when super trend just convert into green candle close basis".
            # So no need to wait for break. Just exit.

            # We'll set a special flag or just execute immediately in on_tick via a forced trigger.
            # Let's set exit_pending and force trigger price to be current close (or effectively market)
            # Actually, we can handle this in on_tick: if st.status == "position" and st_trend == 1...

def on_tick(symbol, ltp, ts):
    st = SYMBOL_STATES.get(symbol)
    if not st: return

    st.current_ltp = ltp

    # 1. Update Candle Manager
    closed_candle = CANDLE_MANAGER.process_tick(symbol, ltp, ts)
    if closed_candle:
        row = pd.DataFrame([closed_candle])
        row = row.set_index("ts")[["open", "high", "low", "close"]]

        if st.data.empty:
            st.data = row
        else:
            st.data = pd.concat([st.data, row])
            st.data = st.data.loc[~st.data.index.duplicated(keep='last')]

        if len(st.data) > LOOKBACK_CANDLES:
            st.data = st.data.tail(LOOKBACK_CANDLES)

        st.data = compute_indicators(st.data)
        st.last_candle_ts = closed_candle["ts"]

        evaluate_on_new_candle(st)

    # 2. Check Triggers (LTP)

    # ENTRY TRIGGER (SELL)
    if st.status == "entry_pending" and st.signal_candle:
        # Check Expiration
        ts_obj = None
        if isinstance(ts, (int, float)):
             ts_obj = dt.fromtimestamp(ts, pytz.utc).astimezone(IST).replace(tzinfo=None)

        if st.signal_expiry and ts_obj and ts_obj > st.signal_expiry:
            log("signal-expired", f"⏰ Signal Expired for {symbol}. Resetting to Watch.")
            st.status = "watch"
            st.signal_candle = None
            return

        trigger = st.signal_candle["low"]
        # SELL if LTP < Trigger (Low of signal candle)
        if ltp < trigger:
            st.entry_price = ltp
            alloc = TRADE_ALLOCATION

            # Calculate Qty (Negative for Short? Or just maintain side?)
            # Usually quantity is positive, side is Sell.
            # Delta API uses side=-1 for Sell.
            # For paper trading, we track side.

            if st.is_inverse:
                st.qty = int(alloc / st.contract_value) if st.contract_value > 0 else 0
            else:
                denom = st.contract_value * ltp
                st.qty = int(alloc / denom) if denom > 0 else 0

            log("trade", f"🚀 [PAPER] ENTER SELL {symbol} @ {ltp} (Trigger {trigger}) | Qty: {st.qty}")
            st.status = "position"

            # Stop Loss (Signal High)
            if SL_MODE == "signal_high":
                st.stop_price = st.signal_candle["high"]
            else:
                # Swing High
                swing = compute_swing_high_for_signal(st, SWING_HIGH_LOOKBACK)
                st.stop_price = st.signal_candle["high"] if np.isnan(swing) else swing

            if not st.data.empty:
                st.atr_at_entry = st.data.iloc[-1]["atr"]

            log("trade", f"   Stop: {st.stop_price}")
            st.signal_candle = None

    # EXIT TRIGGERS (SELL POSITION)
    if st.status == "position":
        global PAPER_BALANCE, PAPER_PNL
        exit_price = 0.0
        reason = ""

        # Check Supertrend Exit (Green)
        # We can check current candle or just the last closed candle.
        # User said "candle close basis". This is checked in evaluate_on_new_candle.
        # If evaluate_on_new_candle detected Green Supertrend, we should exit.
        # How do we pass that state?
        # Check if last closed candle supertrend is Green (1).

        if not st.data.empty:
            last_trend = st.data.iloc[-1].get("supertrend_trend")
            if last_trend == 1:
                exit_price = ltp
                reason = "SUPERTREND REVERSAL (GREEN)"

        # Stop Loss (LTP > Stop Price for Short)
        if st.stop_price and ltp >= st.stop_price:
            exit_price = ltp
            reason = "STOP LOSS HIT"

        if exit_price > 0:
            # PnL Calculation (Short)
            # Profit = (Entry - Exit)

            gross_pnl = 0.0
            entry_notional = 0.0
            exit_notional = 0.0

            if st.is_inverse:
                # Inverse Short PnL = Notional_USD * (Entry - Exit) / Exit ???
                # No. Inverse PnL (in BTC) = Qty * Val * (1/Entry - 1/Exit)
                # PnL in USD = PnL_BTC * ExitPrice = Qty * Val * (1/Entry - 1/Exit) * Exit
                # = Qty * Val * (Exit/Entry - 1) * -1 (for short) -> complicated.
                # Simplified: (Entry - Exit) / Entry * Notional (Approx)

                notional_usd = st.qty * st.contract_value
                entry_notional = notional_usd
                exit_notional = notional_usd

                if st.entry_price > 0:
                    gross_pnl = notional_usd * (st.entry_price - exit_price) / st.entry_price
            else:
                # Linear Short PnL = (Entry - Exit) * Qty * Val
                entry_notional = st.qty * st.contract_value * st.entry_price
                exit_notional = st.qty * st.contract_value * exit_price

                gross_pnl = (st.entry_price - exit_price) * st.qty * st.contract_value

            entry_fee = entry_notional * TAKER_FEE_PCT
            exit_fee = exit_notional * TAKER_FEE_PCT
            total_fee = entry_fee + exit_fee

            net_pnl = gross_pnl - total_fee

            PAPER_BALANCE += net_pnl
            PAPER_PNL += net_pnl
            icon = "✅" if net_pnl >= 0 else "❌"

            log("trade", f"{icon} [PAPER] {reason} {symbol} @ {exit_price} | PnL: ${net_pnl:.2f}")
            log("trade", f"   Account Balance: ${PAPER_BALANCE:.2f}")

            st.status = "watch"
            st.qty = 0

# ==============================================================================
# MAIN LOGIC
# ==============================================================================
def main():
    select_best_server()
    client = DeltaClient()
    client.fetch_products()
    client.fetch_tickers()

    log("warmup", "Fetching historical data...")
    for sym in SYMBOLS_TO_MONITOR:
        df = client.fetch_history(sym, TIMEFRAME_RES, LOOKBACK_CANDLES)
        if not df.empty:
            df = compute_indicators(df)
            SYMBOL_STATES[sym].data = df
            SYMBOL_STATES[sym].last_candle_ts = df.index[-1]
            log("warmup", f"Loaded {len(df)} candles for {sym}")

    log("warmup", "Historical data loaded")

    # WebSocket
    log("main", "Starting WebSocket connection...")

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

    def on_ws_message(ws, message):
        try:
            data = json.loads(message)
            if data.get("type") == "v2/ticker":
                sym = data.get("symbol")
                ltp = data.get("close") or data.get("mark_price")
                ts = data.get("timestamp") or time.time()

                if "ltp_change_24h" in data:
                    try:
                        SYMBOL_STATES[sym].change_24h = float(data["ltp_change_24h"])
                    except: pass

                if sym and ltp:
                    if not ts: ts = time.time()
                    else:
                        if ts > 4102444800: ts = ts / 1000000.0
                    on_tick(sym, float(ltp), ts)
        except Exception:
            pass

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
            log("heartbeat", f"Bot active. Monitoring {len(SYMBOLS_TO_MONITOR)} symbols...")
            for sym in SYMBOLS_TO_MONITOR:
                st = SYMBOL_STATES[sym]
                if not st.data.empty:
                    last = st.data.iloc[-1]
                    trend_val = last.get("supertrend_trend", 0)
                    trend = "🟢" if trend_val == 1 else "🔴"
                    display_ltp = st.current_ltp if st.current_ltp > 0 else last['close']
                    print(f"[heartbeat]   {trend} {sym:<12} | LTP: $ {display_ltp:,.2f} | Status: {st.status}")
            time.sleep(60)

    except KeyboardInterrupt:
        print("\nExiting...")

if __name__ == "__main__":
    main()
