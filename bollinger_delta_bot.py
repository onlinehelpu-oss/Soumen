# Bollinger Band Strategy - Delta Exchange Port
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
        import os
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        with open(config_path, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading config.json: {e}. Using defaults.")
        return {}


CONFIG = load_config()

# Default to India, but will be updated by auto-selection
BASE_URL = CONFIG.get("base_url", "https://api.india.delta.exchange")
WS_URL = CONFIG.get("ws_url", "wss://socket.india.delta.exchange")

SYMBOLS_TO_MONITOR = CONFIG.get("symbols", ["BTCUSD", "ETHUSD", "SOLUSD"])

# TIMEFRAME CONFIGURATION
TIMEFRAME_MINUTES = CONFIG.get("timeframe_minutes", 15)

# List of natively supported resolutions by Delta API
SUPPORTED_RESOLUTIONS_MIN = [1, 3, 5, 15, 30, 60, 120, 240, 360, 720, 1440]


def get_resolution_str(minutes):
    """
    Returns the API resolution string if supported.
    If not supported, returns None or the closest base resolution string is handled in fetch_history.
    """
    if minutes in SUPPORTED_RESOLUTIONS_MIN:
        if minutes < 60:
            return f"{minutes}m"
        elif minutes % 60 == 0 and minutes < 1440:
            return f"{minutes // 60}h"
        elif minutes % 1440 == 0:
            return f"{minutes // 1440}d"
    return None  # Unsupported natively


LOOKBACK_CANDLES = CONFIG.get("lookback_candles", 1000)

# Strategy Params
STRATEGY = CONFIG.get("strategy", {})
# BB Settings
BB_PERIOD = STRATEGY.get("bb_period", 20)
BB_STD = STRATEGY.get("bb_std", 2.0)

SL_MODE = STRATEGY.get("sl_mode", "signal_low")  # "signal_low" or "swing_low"
SWING_LOOKBACK = STRATEGY.get("swing_lookback", 5)
SWING_HIGH_LOOKBACK = STRATEGY.get("swing_high_lookback", 100)
TRAIL_ATR_MULT = STRATEGY.get("trail_atr_mult", 1.0)  # None = disabled

# Simulation / Paper Trading
PAPER_CFG = CONFIG.get("paper_trading", {})
PAPER_TRADE = PAPER_CFG.get("enabled", True)
MAX_CONCURRENT_POS = PAPER_CFG.get("max_concurrent_pos", 3)
PAPER_BALANCE = PAPER_CFG.get("balance", 10000.0)  # Starting Balance in USD
TAKER_FEE_PCT = PAPER_CFG.get("taker_fee_pct", 0.05) / 100.0
MAKER_FEE_PCT = PAPER_CFG.get("maker_fee_pct", 0.02) / 100.0
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


def compute_indicators(df):
    if df is None or df.empty:
        return df
    df = df.copy()

    # Bollinger Bands
    df["ma"] = df["close"].rolling(window=BB_PERIOD).mean()
    df["std"] = df["close"].rolling(window=BB_PERIOD).std()
    df["upper_bb"] = df["ma"] + (df["std"] * BB_STD)
    df["lower_bb"] = df["ma"] - (df["std"] * BB_STD)

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

        # Product Specs
        self.contract_value = 1.0  # Default to 1 (USD)
        self.is_inverse = False  # Default assumption (Linear)

        # Signal tracking
        self.signal_candle = None  # dict
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
        self.force_exit = False # New flag for BB Exit

        self.last_candle_ts = None
        self.entry_time = 0.0
        self.just_entered = False
        self.current_ltp = 0.0


class CandleManager:
    def __init__(self, timeframe_min=15):
        self.tf = timeframe_min
        self.partial = {}  # symbol -> dict

    def _floor_ts(self, ts: dt):
        # Round down to nearest timeframe interval (works for >60m too)
        total_minutes = ts.hour * 60 + ts.minute
        floored_minutes = (total_minutes // self.tf) * self.tf

        hour = (floored_minutes // 60) % 24
        minute = floored_minutes % 60

        return ts.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def process_tick(self, symbol, ltp, ts_val):
        # ts_val is either int timestamp (seconds/ms) or ISO string
        try:
            ts = None
            # Heuristic check for milliseconds timestamp (year 56105 implies micro/milliseconds mismatch)
            # Current timestamp ~ 1.7e9 (seconds). 1.7e12 (ms), 1.7e15 (us)

            if isinstance(ts_val, (int, float)):
                # If timestamp is huge (> 3000-01-01), assume MS or US
                if ts_val > 32503680000:  # Year 3000 in seconds
                    ts_val = ts_val / 1000000.0  # Try converting US to S? or MS to S

                # Recheck reasonable range (Year 2000 - 2100)
                # 946684800 (2000) to 4102444800 (2100)
                if ts_val > 4102444800:
                    ts_val = ts_val / 1000.0  # Maybe it was US?

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
                return None  # No closed candle yet

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
                return completed  # Return the CLOSED candle

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

                        c_val = float(p.get("contract_value", 1.0))
                        settling_sym = p.get("settling_asset", {}).get("symbol", "")
                        quoting_sym = p.get("quoting_asset", {}).get("symbol", "")

                        is_inverse = False
                        if settling_sym and quoting_sym and (settling_sym != quoting_sym):
                            is_inverse = True

                        st = SYMBOL_STATES.get(sym)
                        if st:
                            st.contract_value = c_val
                            st.is_inverse = is_inverse

                        log("delta", f"Mapped {sym} -> ID {pid} | Val: {c_val} | Inv: {is_inverse}")

                found_symbols = set(self.products.keys())
                for s in SYMBOLS_TO_MONITOR:
                    if s not in found_symbols:
                        log("warning", f"Symbol {s} not found in Delta Exchange products! Check spelling.")
            else:
                log("error", "Failed to fetch products: " + str(data))
        except Exception as e:
            log("error", f"Error fetching products: {e}")

    def fetch_history(self, symbol, timeframe_minutes, num_candles):
        """
        Fetches historical candles. Handles unsupported resolutions by resampling.
        """
        now_ts = int(time.time())

        # Determine the resolution to use for the API call
        res_str = get_resolution_str(timeframe_minutes)
        api_res_str = res_str
        base_minutes = timeframe_minutes

        needs_resampling = False

        if not api_res_str:
            # Unsupported resolution (e.g., 10m). Find largest supported factor.
            # SUPPORTED: 1, 3, 5, 15, 30, 60...
            # For 10: 5 is largest factor.
            # For 7: 1 is largest factor.

            best_base = 1
            for supported in SUPPORTED_RESOLUTIONS_MIN:
                if supported >= timeframe_minutes:
                    break
                if timeframe_minutes % supported == 0:
                    best_base = supported

            api_res_str = get_resolution_str(best_base)
            base_minutes = best_base
            needs_resampling = True
            log("warmup", f"Timeframe {timeframe_minutes}m not natively supported. Fetching {api_res_str} and resampling.")

        # Calculate start time based on the API resolution we are fetching
        # We fetch 'num_candles' of the target timeframe if possible, but limited by API max.
        # If we need 1000 10m candles, and we fetch 5m candles, we'd need 2000 5m candles.
        # Delta limit is usually ~1000-2000. We will stick to fetching max safely (1500 or so).
        # To be safe, we just fetch a fixed large window.

        limit_per_req = 2000 # Assume we can fetch this much

        # Duration in seconds for the fetch
        # If we want 1000 candles of TARGET timeframe:
        duration_needed = num_candles * timeframe_minutes * 60
        start_ts = now_ts - duration_needed

        # API request
        params = {
            "symbol": symbol,
            "resolution": api_res_str,
            "start": start_ts,
            "end": now_ts
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
                    df = df.sort_values(by="time").reset_index(drop=True)
                    df["ts"] = pd.to_datetime(df["time"], unit='s', utc=True).dt.tz_convert(IST).dt.tz_localize(None)
                    df = df.set_index("ts")[["open", "high", "low", "close", "volume"]]

                    if needs_resampling:
                        # Resample to target timeframe
                        # Logic: Resample 5m to 10m
                        logic = {
                            'open': 'first',
                            'high': 'max',
                            'low': 'min',
                            'close': 'last',
                            'volume': 'sum'
                        }

                        # Resample string: e.g. "10min"
                        freq_str = f"{timeframe_minutes}min"

                        # Pandas resample
                        df_resampled = df.resample(freq_str, origin='epoch').agg(logic)

                        # Drop incomplete last candle if it doesn't match the current time bucket?
                        # Usually safe to keep. Drop NaNs.
                        df_resampled = df_resampled.dropna()

                        return df_resampled

                    return df
            return pd.DataFrame()
        except Exception as e:
            log("error", f"History fetch failed for {symbol}: {e}")
            return pd.DataFrame()


# ==============================================================================
# STRATEGY LOGIC (Bollinger Band)
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
                    is_peak = False
                    break
            if is_peak: peaks.append(current)

        if reference_price is not None:
            valid_peaks = [p for p in peaks if p > reference_price]
            if valid_peaks: return valid_peaks[-1]

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

    # BB
    lower_bb = curr.get("lower_bb")
    upper_bb = curr.get("upper_bb")

    # ENTRY SIGNAL
    if st.status == "watch":
        # 1. Previous candle RED
        prev_is_red = prev["close"] < prev["open"]

        # 2. Current (Signal) candle GREEN
        curr_is_green = curr["close"] > curr["open"]

        # 3. Signal candle Open < Lower BB
        open_below_lower = curr_open < lower_bb

        # 4. Signal candle Close > Lower BB
        close_above_lower = curr_close > lower_bb

        if prev_is_red and curr_is_green and open_below_lower and close_above_lower:

            # Trigger = Break High of Signal Candle
            trigger_price = curr_high

            # Target (Optional in prompt, logic from code-1 uses Swing High or similar.
            # Prompt says "target: if any candle closed above upper bolinger band then exit", so fixed target is less relevant but code-1 sets one.
            # I will keep calculating it but primary exit is BB.
            target = compute_prev_swing_high_for_entry(st, SWING_HIGH_LOOKBACK, curr_high)

            st.potential_target_price = target
            st.signal_candle = {
                "ts": curr.name,
                "high": curr_high,
                "low": curr_low
            }

            st.signal_expiry = curr.name + timedelta(minutes=TIMEFRAME_MINUTES * 2)
            st.status = "entry_pending"

            log("signal",
                f"🔵 ENTRY SIGNAL {st.symbol} | High: {curr_high} | Open: {curr_open} | Close: {curr_close} (Green) | LowerBB: {lower_bb:.2f} | Wait for break > High")

    # EXIT SIGNAL (Close above Upper BB)
    if st.status == "position":
        if curr_close > upper_bb:
            st.force_exit = True
            log("signal", f"🟠 EXIT SIGNAL {st.symbol} | Closed above Upper BB ({curr_close:.2f} > {upper_bb:.2f})")


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
            ts_obj = dt.fromtimestamp(ts, pytz.utc).astimezone(IST).replace(tzinfo=None)
        elif isinstance(ts, dt):
            ts_obj = ts
            if ts_obj.tzinfo:
                ts_obj = ts_obj.astimezone(IST).replace(tzinfo=None)

        if st.signal_expiry and st.signal_expiry.tzinfo is None and ts_obj.tzinfo is not None:
            ts_obj = ts_obj.replace(tzinfo=None)

        if st.signal_expiry and ts_obj and ts_obj > st.signal_expiry:
            log("signal-expired", f"⏰ Signal Expired for {symbol} (No entry in next candle). Resetting to Watch.")
            st.status = "watch"
            st.signal_candle = None
            return

        trigger = st.signal_candle["high"]
        if ltp > trigger:
            st.entry_price = ltp
            alloc = 1000.0

            if st.is_inverse:
                st.qty = alloc / st.contract_value if st.contract_value > 0 else 0
            else:
                denom = st.contract_value * ltp
                st.qty = alloc / denom if denom > 0 else 0

            st.qty = int(st.qty)

            log("trade", f"🚀 [PAPER] ENTER BUY {symbol} @ {ltp} (Trigger {trigger}) | Qty: {st.qty} Contracts")
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
        global PAPER_BALANCE, PAPER_PNL
        exit_price = 0.0
        reason = ""

        # Target (Optional / Safety)
        if st.target_price and ltp >= st.target_price:
            # We don't necessarily exit on target anymore, purely BB exit?
            # Prompt: "target : if any candle closed above upper bolinger band then exit."
            # It doesn't explicitly remove the potential target calculation, but implies BB is the main target mechanism.
            # I'll keep this as a "Take Profit" if defined, but relying on BB.
            # Actually, user said "target : if any candle closed above upper bolinger band then exit."
            # This sounds like the ONLY target condition.
            # I will disable the fixed price target exit unless user re-enables it.
            # But wait, `evaluate_on_new_candle` sets `st.potential_target_price`.
            # I will COMMENT OUT this fixed target exit to strictly follow "if closed above BB".
            pass

        # Stop Loss (LTP)
        if st.stop_price and ltp <= st.stop_price:
            exit_price = ltp
            reason = "STOP LOSS HIT"

        # BB Exit Trigger (from evaluate_on_new_candle)
        elif st.force_exit:
            exit_price = ltp
            reason = "BB UPPER EXIT"
            st.force_exit = False # Reset flag

        if exit_price > 0:
            gross_pnl = 0.0
            entry_notional = 0.0
            exit_notional = 0.0

            if st.is_inverse:
                notional_usd = st.qty * st.contract_value
                entry_notional = notional_usd
                exit_notional = notional_usd

                if st.entry_price > 0:
                    gross_pnl = notional_usd * (exit_price - st.entry_price) / st.entry_price
            else:
                entry_notional = st.qty * st.contract_value * st.entry_price
                exit_notional = st.qty * st.contract_value * exit_price
                gross_pnl = (exit_price - st.entry_price) * st.qty * st.contract_value

            entry_fee = entry_notional * TAKER_FEE_PCT
            exit_fee = exit_notional * TAKER_FEE_PCT
            total_fee = entry_fee + exit_fee

            net_pnl = gross_pnl - total_fee

            PAPER_BALANCE += net_pnl
            PAPER_PNL += net_pnl
            icon = "✅" if net_pnl >= 0 else "❌"

            log("trade",
                f"{icon} [PAPER] {reason} {symbol} @ {exit_price} | PnL: ${net_pnl:.2f} (Gross: ${gross_pnl:.2f}, Fees: ${total_fee:.2f})")
            log("trade", f"   Account Balance: ${PAPER_BALANCE:.2f}")

            st.status = "watch"
            st.qty = 0
            st.stop_price = 0.0
            st.target_price = None

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
        df = client.fetch_history(sym, TIMEFRAME_MINUTES, LOOKBACK_CANDLES)
        if not df.empty:
            df = compute_indicators(df)
            SYMBOL_STATES[sym].data = df
            SYMBOL_STATES[sym].last_candle_ts = df.index[-1]
            log("warmup", f"Loaded {len(df)} candles for {sym}")

    log("warmup", "Historical data loaded")

    print("\n" + "=" * 70)
    print("📊 CURRENT MARKET PRICES (LTP)")
    print("=" * 70)
    for sym in SYMBOLS_TO_MONITOR:
        st = SYMBOL_STATES[sym]
        if not st.data.empty:
            last = st.data.iloc[-1]
            print(
                f"{sym:<12} | LTP: $ {last['close']:,.2f} | 24h Change: 📈   +0.00% | Vol: {int(last.get('volume', 0)):,}")
    print("=" * 70)
    print(f"⏰ TIMEFRAME: {TIMEFRAME_MINUTES} minute candles")
    print(f"   - Each candle represents {TIMEFRAME_MINUTES} minutes of price action")
    print(f"   - New candle completes every {TIMEFRAME_MINUTES} minutes")
    print(f"   - Strategy: Bollinger Bands ({BB_PERIOD}, {BB_STD})")
    print(f"   - Server: {BASE_URL}")
    print("=" * 70 + "\n")

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
                sym = data.get("symbol")
                ltp = data.get("close") or data.get("mark_price")
                ts = data.get("timestamp") or time.time()

                if sym and ltp:
                    if not ts:
                        ts = time.time()
                    else:
                        if ts > 4102444800:
                            ts = ts / 1000000.0

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
        time.sleep(TIMEFRAME_MINUTES * 60)

        while True:
            log("heartbeat", f"Bot active. Monitoring {len(SYMBOLS_TO_MONITOR)} symbols...")
            log("heartbeat", f"📊 Current Market Prices:")
            for sym in SYMBOLS_TO_MONITOR:
                st = SYMBOL_STATES[sym]
                if not st.data.empty:
                    last = st.data.iloc[-1]

                    # Candle color (last completed candle)
                    open_price = last.get("open", 0)
                    close_price = last.get("close", 0)
                    candle_color = "🟢" if close_price > open_price else "🔴"

                    # Trend based on Price vs Middle BB
                    ma = last.get("ma", 0)
                    trend = "↑" if close_price > ma else "↓"

                    display_ltp = st.current_ltp if st.current_ltp > 0 else last['close']

                    print(f"[heartbeat]   {candle_color} {trend} {sym:<12} | LTP: $ {display_ltp:,.2f} | Status: {st.status}")

            time.sleep(TIMEFRAME_MINUTES * 60)

    except KeyboardInterrupt:
        print("\nExiting...")


if __name__ == "__main__":
    main()
