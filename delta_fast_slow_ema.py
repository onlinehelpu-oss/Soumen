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
import os
from datetime import datetime as dt, timedelta
from typing import Dict, Optional, List

# ==============================================================================
# CONFIGURATION MANAGEMENT
# ==============================================================================
class Config:
    def __init__(self):
        self.base_url = "https://api.india.delta.exchange"
        self.ws_url = "wss://socket.india.delta.exchange"
        self.monitored_symbols = []
        self.timeframe_minutes = 15
        self.lookback_candles = 1000

        # Strategy
        self.exit_ema = 50
        self.entry_fast_ema = 20
        self.entry_slow_ema = 50
        self.ema_buffer = 0.0
        self.require_green_signal = True
        self.min_range_pct = 0.0
        self.sl_mode = "signal_low"
        self.swing_lookback = 5
        self.swing_high_lookback = 100
        self.trail_atr_mult = 1.0

        # Paper Trading
        self.paper_enabled = True
        self.paper_balance = 10000.0
        self.trade_allocation = 275.0
        self.taker_fee_pct = 0.0005
        self.maker_fee_pct = 0.0002

        # System
        self.timezone = "Asia/Kolkata"
        self.ist = pytz.timezone(self.timezone)

        self.last_modified = 0

    def load(self):
        try:
            config_path = os.path.join(os.path.dirname(__file__), "config.json")
            if not os.path.exists(config_path):
                return

            # Check modification time to avoid unnecessary reads
            mtime = os.path.getmtime(config_path)
            if mtime <= self.last_modified:
                return

            with open(config_path, "r") as f:
                data = json.load(f)

            # Helper to detect changes in critical params
            old_tf = self.timeframe_minutes
            old_syms = set(self.monitored_symbols)

            # --- Load Values ---
            self.base_url = data.get("base_url", self.base_url)
            self.ws_url = data.get("ws_url", self.ws_url)

            # Default fallback for symbols
            default_syms = [
                "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "BNBUSD",
                "DOGEUSD", "ADAUSD", "DOTUSD", "AVAXUSD", "LINKUSD",
                "LTCUSD", "BCHUSD", "XMRUSD", "ATOMUSD", "TRXUSD",
                "NEARUSD", "FILUSD", "APTUSD", "INJUSD", "STXUSD",
                "ARBUSD", "OPUSD", "AAVEUSD", "UNIUSD", "SUIUSD",
                "HBARUSD", "ETCUSD", "ALGOUSD", "POLUSD", "TIAUSD",
                "ENSUSD", "LDOUSD", "GALAUSD", "MANAUSD", "SANDUSD",
                "CAKEUSD", "DYDXUSD", "RUNEUSD", "ZECUSD", "ZROUSD",
                "API3USD", "KSMUSD", "SKLUSD", "IOTAUSD", "JUPUSD",
                "WLDUSD", "ONDOUSD", "SEIUSD", "ARBUSD", "ENSUSD",
                "PIPPINUSD", "YALAUSD"
            ]
            self.monitored_symbols = data.get("monitored_symbols", default_syms)

            self.timeframe_minutes = data.get("timeframe_minutes", 15)
            self.lookback_candles = data.get("lookback_candles", 1000)

            strat = data.get("strategy", {})
            self.exit_ema = strat.get("exit_ema", 50)
            self.entry_fast_ema = strat.get("entry_fast_ema", 20)
            self.entry_slow_ema = strat.get("entry_slow_ema", 50)
            self.ema_buffer = strat.get("ema_buffer", 0.0)
            self.require_green_signal = strat.get("require_green_signal", True)
            self.min_range_pct = strat.get("min_range_pct", 0.0)
            self.sl_mode = strat.get("sl_mode", "signal_low")
            self.swing_lookback = strat.get("swing_lookback", 5)
            self.swing_high_lookback = strat.get("swing_high_lookback", 100)
            self.trail_atr_mult = strat.get("trail_atr_mult", 1.0)

            paper = data.get("paper_trading", {})
            self.paper_enabled = paper.get("enabled", True)
            # Balance is stateful, only set initial if needed, but we usually track it in memory
            # We can update allocation dynamically though
            self.trade_allocation = paper.get("trade_allocation", 275.0)
            self.taker_fee_pct = paper.get("taker_fee_pct", 0.05) / 100.0
            self.maker_fee_pct = paper.get("maker_fee_pct", 0.02) / 100.0

            tz = data.get("timezone", "Asia/Kolkata")
            if tz != self.timezone:
                self.timezone = tz
                try:
                    self.ist = pytz.timezone(self.timezone)
                except:
                    self.ist = pytz.utc

            self.last_modified = mtime

            # --- Detect Restart-Required Changes ---
            if self.timeframe_minutes != old_tf and old_tf != 15: # Ignore initial load
                 print(f"[CONFIG] ⚠️ Timeframe changed from {old_tf} to {self.timeframe_minutes}. Restart bot to apply!")

            new_syms = set(self.monitored_symbols)
            if new_syms != old_syms and old_syms:
                 print(f"[CONFIG] ⚠️ Symbol list changed. Restart bot to apply subscription changes!")

            print(f"[CONFIG] Configuration loaded/updated. Alloc: ${self.trade_allocation} | Fast: {self.entry_fast_ema} | Slow: {self.entry_slow_ema}")

        except Exception as e:
            print(f"Error loading config.json: {e}")

# Global Config Instance
CFG = Config()
CFG.load() # Initial load

# Derived constants (for initial setup)
TIMEFRAME_RES = "15m" # Will be updated in main if needed, but simple map:
def get_resolution_str(minutes):
    if minutes < 60: return f"{minutes}m"
    elif minutes % 60 == 0 and minutes < 1440: return f"{minutes // 60}h"
    elif minutes % 1440 == 0: return f"{minutes // 1440}d"
    else: return "1m"

TIMEFRAME_RES = get_resolution_str(CFG.timeframe_minutes)

# Paper Trading State (Global)
PAPER_BALANCE = CFG.paper_balance
PAPER_PNL = 0.0


# ==============================================================================
# LOGGING HELPER
# ==============================================================================
def log(tag, message):
    timestamp = dt.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{tag}] {message}")


def format_price(price):
    if price is None:
        return "0.00"
    if price == 0:
        return "0.00"
    if abs(price) < 0.0001:
        return f"{price:.8f}"
    if abs(price) < 0.01:
        return f"{price:.6f}"
    if abs(price) < 1.0:
        return f"{price:.4f}"
    return f"{price:,.2f}"


# ==============================================================================
# SERVER SELECTION
# ==============================================================================
def select_best_server():
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
            CFG.base_url = india[1]
            CFG.ws_url = india[2]
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
        CFG.base_url = global_srv[1]
        CFG.ws_url = global_srv[2]
        return
    else:
        log("init", "  - Global: Failed/Timeout")

    # 3. Fallback
    if lat_india is not None:
        log("warning", "Both servers slow/failed check, but India responded. Using India.")
        CFG.base_url = india[1]
        CFG.ws_url = india[2]
    elif lat_global is not None:
        log("warning", "India failed, Global responded (slow). Using Global.")
        CFG.base_url = global_srv[1]
        CFG.ws_url = global_srv[2]
    else:
        log("error", "CRITICAL: Unable to connect to any Delta Exchange server.")
        CFG.base_url = india[1]
        CFG.ws_url = india[2]


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


def compute_indicators(df):
    if df is None or df.empty:
        return df
    df = df.copy()
    # Use dynamic config values
    df["ema_exit"] = compute_ema(df["close"], CFG.exit_ema)
    df["ema_fast_entry"] = compute_ema(df["close"], CFG.entry_fast_ema)
    df["ema_slow_entry"] = compute_ema(df["close"], CFG.entry_slow_ema)
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
        self.change_24h = 0.0  # Percentage change over 24h

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
        self.exit_try_count = 0
        self.last_failed_exit_ts = None

        self.last_candle_ts = None
        self.entry_time = 0.0
        self.just_entered = False
        self.current_ltp = 0.0
        self.bot_order_id = None  # To track positions made by this bot

    def to_dict(self):
        return {
            "symbol": self.symbol,
            "status": self.status,
            "entry_price": self.entry_price,
            "qty": self.qty,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "bot_order_id": self.bot_order_id
        }

    def from_dict(self, data):
        self.status = data.get("status", "watch")
        self.entry_price = data.get("entry_price", 0.0)
        self.qty = data.get("qty", 0)
        self.stop_price = data.get("stop_price", 0.0)
        self.target_price = data.get("target_price")
        self.bot_order_id = data.get("bot_order_id")


class PositionManager:
    def __init__(self, filename="bot_state.json"):
        self.filename = os.path.join(os.path.dirname(__file__), filename)

    def save_state(self):
        try:
            state_data = {}
            for sym, st in SYMBOL_STATES.items():
                if st.status == "position":
                    state_data[sym] = st.to_dict()
            with open(self.filename, "w") as f:
                json.dump(state_data, f, indent=4)
        except Exception as e:
            log("error", f"Failed to save state: {e}")

    def load_state(self):
        try:
            if not os.path.exists(self.filename):
                return
            with open(self.filename, "r") as f:
                state_data = json.load(f)

            for sym, data in state_data.items():
                if sym in SYMBOL_STATES:
                    st = SYMBOL_STATES[sym]
                    st.from_dict(data)
                    log("state", f"Restored state for {sym}: {st.status} | Entry: {st.entry_price}")
        except Exception as e:
            log("error", f"Failed to load state: {e}")


class CandleManager:
    def __init__(self, timeframe_min=15):
        self.tf = timeframe_min
        self.partial = {}  # symbol -> dict

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
                if ts_val > 4102444800000000:  # Microseconds (16+ digits)
                    ts_val = ts_val / 1000000.0  # Convert US to S
                elif ts_val > 4102444800000:  # Milliseconds (13+ digits)
                    ts_val = ts_val / 1000.0  # Convert MS to S

                # Check for "future" timestamps due to incorrect scaling
                if ts_val > 4102444800: # Year 2100 in seconds
                    ts_val = time.time()

                # Important: fromtimestamp() returns Local Time. We want UTC aware first.
                ts = dt.fromtimestamp(ts_val, pytz.utc)
            else:
                # String parsing
                ts = pd.to_datetime(ts_val).to_pydatetime()

            # Localize if naive (Delta sends UTC usually)
            if ts.tzinfo is None:
                ts = pytz.utc.localize(ts)

            # Convert to IST for logic consistency
            ts_ist = ts.astimezone(CFG.ist).replace(tzinfo=None)

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
# Initialize with current config
SYMBOL_STATES: Dict[str, SymbolState] = {s: SymbolState(s) for s in CFG.monitored_symbols}
CANDLE_MANAGER = CandleManager(CFG.timeframe_minutes)
POSITION_MANAGER = PositionManager()


# ==============================================================================
# DELTA EXCHANGE API CLIENT
# ==============================================================================
class DeltaClient:
    def __init__(self):
        self.products = {}
        self.id_to_symbol = {}

    def fetch_tickers(self):
        log("delta", f"Fetching 24h ticker data from {CFG.base_url}...")
        try:
            url = f"{CFG.base_url}/v2/tickers"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("success"):
                for t in data.get("result", []):
                    sym = t.get("symbol")
                    if sym in SYMBOL_STATES:
                        try:
                            # ltp_change_24h is percentage string e.g. "-1.8692"
                            change_val = t.get("ltp_change_24h")
                            if change_val is not None:
                                change = float(change_val)
                                SYMBOL_STATES[sym].change_24h = change
                        except (ValueError, TypeError):
                            pass
            else:
                log("error", "Failed to fetch tickers: " + str(data))
        except Exception as e:
            log("error", f"Error fetching tickers: {e}")

    def fetch_positions(self):
        # log("delta", "Syncing positions with broker...")
        try:
            url = f"{CFG.base_url}/v2/positions"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("success"):
                return data.get("result", [])
            else:
                log("error", f"Failed to fetch positions: {data}")
                return None  # Return None on failure to distinguish from 0 positions
        except Exception as e:
            log("error", f"Error fetching positions: {e}")
            return None  # Return None on error

    def fetch_products(self):
        log("delta", f"Fetching product list from {CFG.base_url}...")
        try:
            url = f"{CFG.base_url}/v2/products"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("success"):
                for p in data.get("result", []):
                    sym = p.get("symbol")
                    pid = p.get("id")

                    if sym in CFG.monitored_symbols:
                        self.products[sym] = pid
                        self.id_to_symbol[pid] = sym

                        # Extract contract specs
                        c_val = float(p.get("contract_value", 1.0))

                        # Determine if Inverse or Linear
                        # Inverse: Settled in Base (e.g. BTC), Quoted in Quote (e.g. USD) -> Settling != Quoting
                        # Linear: Settled in Quote (e.g. USDT), Quoted in Quote (e.g. USDT) -> Settling == Quoting
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

                # Verify all configured symbols were found
                found_symbols = set(self.products.keys())
                for s in CFG.monitored_symbols:
                    if s not in found_symbols:
                        log("warning", f"Symbol {s} not found in Delta Exchange products! Check spelling.")
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
            url = f"{CFG.base_url}/v2/history/candles"
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
                    df["ts"] = pd.to_datetime(df["time"], unit='s', utc=True).dt.tz_convert(CFG.ist).dt.tz_localize(None)
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
                if highs[i - j] >= current or highs[i + j] >= current:
                    is_peak = False
                    break
            if is_peak: peaks.append(current)

        if reference_price is not None:
            valid_peaks = [p for p in peaks if p > reference_price]
            if valid_peaks: return valid_peaks[-1]  # Latest peak

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
        closed_above_both = curr_close > (highest_ema + CFG.ema_buffer)
        green_ok = (not CFG.require_green_signal) or (curr_close > curr_open)
        higher_high = curr_high > prev["high"]

        if (ema_sequence_ok and rising_slow_ema and touched_slow_ema
                and closed_above_both and green_ok and higher_high):

            target = compute_prev_swing_high_for_entry(st, CFG.swing_high_lookback, curr_high)

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
            st.signal_expiry = curr.name + timedelta(minutes=CFG.timeframe_minutes * 2)
            st.status = "entry_pending"

            log("signal",
                f"🔵 ENTRY SIGNAL {st.symbol} | High: {format_price(curr_high)} | Target: {format_price(target)} | Wait for break > High (Expires: {st.signal_expiry})")

    # EXIT SIGNAL (Red candle close below Exit EMA)
    if st.status == "position":
        intrabar_up = (curr_open < ema_exit) and (curr_high > ema_exit)
        closed_below = curr_close < (ema_exit - CFG.ema_buffer)
        is_red = curr_close < curr_open

        if is_red and intrabar_up and closed_below:
            st.exit_signal_candle = {
                "ts": curr.name,
                "low": curr_low
            }
            st.exit_pending = True
            log("signal", f"🟠 EXIT SIGNAL {st.symbol} | Low: {format_price(curr_low)} | Wait for break < Low")


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
        if len(st.data) > CFG.lookback_candles:
            st.data = st.data.tail(CFG.lookback_candles)

        # Recompute Indicators (using current CFG)
        st.data = compute_indicators(st.data)
        st.last_candle_ts = closed_candle["ts"]

        # Run Strategy
        evaluate_on_new_candle(st)

    # 2. Check Triggers (LTP)

    # ENTRY TRIGGER
    if st.status == "entry_pending" and st.signal_candle:
        # Check Expiration (Strict Next Candle Rule)
        ts_obj = None
        if isinstance(ts, (int, float)):
            ts_obj = dt.fromtimestamp(ts, pytz.utc).astimezone(CFG.ist).replace(tzinfo=None)
        elif isinstance(ts, dt):
            ts_obj = ts
            if ts_obj.tzinfo:
                ts_obj = ts_obj.astimezone(CFG.ist).replace(tzinfo=None)

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
            alloc = CFG.trade_allocation

            # Calculate Quantity based on Product Type
            if st.is_inverse:
                st.qty = alloc / st.contract_value if st.contract_value > 0 else 0
            else:
                denom = st.contract_value * ltp
                st.qty = alloc / denom if denom > 0 else 0

            st.qty = int(st.qty)

            log("trade", f"🚀 [PAPER] ENTER BUY {symbol} @ {format_price(ltp)} (Trigger {format_price(trigger)}) | Qty: {st.qty} Contracts")
            st.status = "position"
            st.target_price = st.potential_target_price

            # Stop Loss
            if CFG.sl_mode == "signal_low":
                st.stop_price = st.signal_candle["low"]
            else:
                swing = compute_swing_low_for_signal(st, CFG.swing_lookback)
                st.stop_price = st.signal_candle["low"] if np.isnan(swing) else swing

            # ATR
            if not st.data.empty:
                st.atr_at_entry = st.data.iloc[-1]["atr"]

            log("trade", f"   Target: {format_price(st.target_price)} | Stop: {format_price(st.stop_price)}")
            st.signal_candle = None
            POSITION_MANAGER.save_state()

    # EXIT TRIGGERS
    if st.status == "position":
        global PAPER_BALANCE, PAPER_PNL
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
            # PnL Calculation (Gross)
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

            entry_fee = entry_notional * CFG.taker_fee_pct
            exit_fee = exit_notional * CFG.taker_fee_pct
            total_fee = entry_fee + exit_fee

            net_pnl = gross_pnl - total_fee

            PAPER_BALANCE += net_pnl
            PAPER_PNL += net_pnl
            icon = "✅" if net_pnl >= 0 else "❌"

            log("trade",
                f"{icon} [PAPER] {reason} {symbol} @ {format_price(exit_price)} | PnL: ${net_pnl:.2f} (Gross: ${gross_pnl:.2f}, Fees: ${total_fee:.2f})")
            log("trade", f"   Account Balance: ${PAPER_BALANCE:.2f}")

            st.status = "watch"
            st.qty = 0
            st.bot_order_id = None
            POSITION_MANAGER.save_state()

        # Trailing Stop
        if st.status == "position" and CFG.trail_atr_mult and st.atr_at_entry > 0 and not st.sl_trailed:
            dist = st.atr_at_entry * CFG.trail_atr_mult
            if ltp >= (st.entry_price + dist):
                st.stop_price = st.entry_price
                st.sl_trailed = True
                log("trade", f"🛡️ TRAILING STOP moved to Breakeven {format_price(st.entry_price)}")


# ==============================================================================
# MAIN LOGIC
# ==============================================================================
def main():
    select_best_server()
    client = DeltaClient()
    client.fetch_products()
    client.fetch_tickers()

    # Load State from Disk
    POSITION_MANAGER.load_state()

    # Warmup
    log("warmup", "Fetching historical data...")
    for sym in CFG.monitored_symbols:
        df = client.fetch_history(sym, TIMEFRAME_RES, CFG.lookback_candles)
        if not df.empty:
            df = compute_indicators(df)
            SYMBOL_STATES[sym].data = df
            SYMBOL_STATES[sym].last_candle_ts = df.index[-1]
            log("warmup", f"Loaded {len(df)} candles for {sym}")

    log("warmup", "Historical data loaded")

    print("\n" + "=" * 70)
    print("📊 CURRENT MARKET PRICES (LTP)")
    print("=" * 70)
    for sym in CFG.monitored_symbols:
        st = SYMBOL_STATES[sym]
        if not st.data.empty:
            last = st.data.iloc[-1]

            # Determine trend based on fast/slow
            fast = last.get("ema_fast_entry", 0)
            slow = last.get("ema_slow_entry", 0)
            trend = "🟢" if fast > slow else "🔴"

            chg = st.change_24h
            icon = "📈" if chg >= 0 else "📉"

            # Use real-time LTP if available, else last close
            display_ltp = st.current_ltp if st.current_ltp > 0 else last['close']

            print(
                f"{trend} {sym:<12} | LTP: $ {format_price(display_ltp)} | 24h Change: {icon} {chg:>+7.2f}% | Vol: {int(last.get('volume', 0)):,} | Status: {st.status}")
    print("=" * 70)
    print(f"⏰ TIMEFRAME: {CFG.timeframe_minutes} minute candles")
    print(f"   - Each candle represents {CFG.timeframe_minutes} minutes of price action")
    print(f"   - New candle completes every {CFG.timeframe_minutes} minutes")
    print(f"   - Strategy: Fast({CFG.entry_fast_ema}) / Slow({CFG.entry_slow_ema}) EMA Crossover")
    print(f"   - Trade Allocation: ${CFG.trade_allocation} per trade")
    print(f"   - Paper Trading: {'ENABLED' if CFG.paper_enabled else 'DISABLED (Live Mode)'}")
    print(f"   - Server: {CFG.base_url}")
    print("=" * 70 + "\n")

    # WebSocket
    log("main", "Starting WebSocket connection...")
    log("main", "Bot running. Press Ctrl+C to exit.")

    def on_ws_open(ws):
        log("ws", f"Connected to Delta Exchange ({CFG.ws_url})")
        payload = {
            "type": "subscribe",
            "payload": {
                "channels": [
                    {
                        "name": "v2/ticker",
                        "symbols": CFG.monitored_symbols
                    }
                ]
            }
        }
        ws.send(json.dumps(payload))
        log("ws", f"Subscribed to {len(CFG.monitored_symbols)} symbols")

    def on_ws_message(ws, message):
        try:
            data = json.loads(message)
            if data.get("type") == "v2/ticker":
                # Ticker update
                sym = data.get("symbol")
                ltp = data.get("close") or data.get("mark_price")
                ts = data.get("timestamp") or time.time()

                if "ltp_change_24h" in data:
                    try:
                        SYMBOL_STATES[sym].change_24h = float(data["ltp_change_24h"])
                    except:
                        pass

                if sym and ltp:
                    if not ts:
                        ts = time.time()
                    else:
                        if isinstance(ts, (int, float)):
                            if ts > 4102444800000000:
                                ts = ts / 1000000.0
                            elif ts > 4102444800000:
                                ts = ts / 1000.0

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
                    CFG.ws_url,
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
        # Initial sleep before first heartbeat (align to timeframe or just wait)
        time.sleep(10)

        last_heartbeat_ts = 0
        last_sync_ts = 0
        last_config_check_ts = 0

        HEARTBEAT_INTERVAL = CFG.timeframe_minutes * 60
        SYNC_INTERVAL = 60 # Sync every 1 minute
        CONFIG_CHECK_INTERVAL = 10 # Check config every 10s

        while True:
            now = time.time()

            # 1. Config Reload Check
            if (now - last_config_check_ts) >= CONFIG_CHECK_INTERVAL:
                last_config_check_ts = now
                CFG.load()

            # 2. Periodic Sync with Broker
            if not CFG.paper_enabled and (now - last_sync_ts) >= SYNC_INTERVAL:
                last_sync_ts = now
                try:
                    has_active_bot_positions = any(s.status == "position" for s in SYMBOL_STATES.values())

                    if has_active_bot_positions:
                        open_positions = client.fetch_positions()

                        if open_positions is not None:
                            broker_pos_map = {}
                            if isinstance(open_positions, list):
                                for p in open_positions:
                                    psym = p.get("product_symbol") or p.get("symbol")
                                    size = float(p.get("size", 0))
                                    if size != 0:
                                        broker_pos_map[psym] = size

                            for sym, st in SYMBOL_STATES.items():
                                if st.status == "position":
                                    if sym not in broker_pos_map:
                                        log("sync", f"⚠️ Position for {sym} missing on broker (Manual Close?). Resetting to WATCH.")
                                        st.status = "watch"
                                        st.qty = 0
                                        st.bot_order_id = None
                                        POSITION_MANAGER.save_state()
                except Exception as e:
                    log("error", f"Sync error: {e}")

            # 3. Heartbeat Logic
            if (now - last_heartbeat_ts) >= (CFG.timeframe_minutes * 60):
                last_heartbeat_ts = now
                log("heartbeat", f"Bot active. Monitoring {len(CFG.monitored_symbols)} symbols...")

                for sym in CFG.monitored_symbols:
                    st = SYMBOL_STATES[sym]
                    if not st.data.empty:
                        last = st.data.iloc[-1]
                        fast = last.get("ema_fast_entry", 0)
                        slow = last.get("ema_slow_entry", 0)
                        trend = "🟢" if fast > slow else "🔴"
                        display_ltp = st.current_ltp if st.current_ltp > 0 else last['close']
                        chg = st.change_24h
                        icon = "📈" if chg >= 0 else "📉"
                        print(f"[heartbeat]   {trend} {sym:<12} | LTP: $ {format_price(display_ltp)} | 24h: {icon} {chg:>+7.2f}% | Vol: {int(last.get('volume', 0)):,} | Status: {st.status}")

            time.sleep(10)

    except KeyboardInterrupt:
        print("\nExiting...")


if __name__ == "__main__":
    main()
