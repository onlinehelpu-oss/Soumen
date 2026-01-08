# -*- coding: utf-8 -*-
"""
VWAP-EMA-RSI STRATEGY - OPTIONS EXECUTION
- Tracks NIFTY50, BANKNIFTY, FINNIFTY spot indices
- Executes trades in corresponding options
- Entry Signal: VWAP body cross with EMA and RSI confirmation
- Exit Signal: Stop-loss or RSI-based exit signal
- STRICT NEXT CANDLE ENTRY
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
import glob
import hashlib
import datetime
from urllib.parse import urlparse, parse_qs, quote
from typing import Dict, Optional, Tuple, List
from datetime import datetime as dt, timedelta

import requests
import pandas as pd
import numpy as np
import pytz

try:
    from fyers_apiv3 import fyersModel
    from fyers_apiv3.FyersWebsocket import data_ws
except Exception:
    fyersModel = None
    data_ws = None

# ============================== CONFIGURATION ==============================
TIMEFRAME_MIN = 5  # Any TF in minutes (1,2,3,5,10,15,30,60,...)

# RSI Parameters
RSI_PERIOD = 14
RSI_ENTRY_MIN = 40
RSI_ENTRY_MAX = 55
RSI_EXIT_MIN = 70
RSI_EXIT_MAX = 75

# EMA for ENTRY signal confirmation
ENTRY_FAST_EMA = 50  # e.g., EMA 9 (fast)

MIN_RANGE_PCT = 0.0  # tiny-candle filter (0.001 = 0.1%), 0.0 = off
EMA_BUFFER = 0.0  # optional extra buffer above/below EMAs
REQUIRE_GREEN_SIGNAL = True

# Spot indices to track
SPOT_INDICES = [
    "NSE:NIFTY50-INDEX",
    "NSE:NIFTYBANK-INDEX",
    "BSE:SENSEX-INDEX"
]

# Correct Fyers lot sizes (January 2024)
MIN_LOT_SIZES = {
    "NSE:NIFTY50-INDEX": 65,  # NIFTY: 65 shares per lot
    "NSE:NIFTYBANK-INDEX": 30,  # BANKNIFTY: 30 shares per lot
    "NSE:FINNIFTY-INDEX": 60,  # FINNIFTY: 60 shares per lot,
    "BSE:SENSEX-INDEX": 20,  # SENSEX: 20 shares per lot
}

LOG_FILE = "trade_log.csv"
STATE_DUMP = "symbol_states.json"
PARTIAL_CANDLES_FILE = "partial_candles.json"

# Strike selection: 0=ATM, 1=1st OTM, -1=1st ITM
STRIKE_DISTANCE = 0

# Position management
SL_MODE = "signal_low"  # "signal_low" or "swing_low"
SWING_LOOKBACK = 5  # used for swing-low
LOT_MULTIPLIER = 1  # Lot multiplier

MAX_CONCURRENT_POS = 3
DAILY_MAX_LOSS = 50000.0
TRADING_ENABLED = True
MAX_EXIT_RETRIES = 3
EXIT_RETRY_COOLDOWN_SECONDS = 10

# Market Hours
MARKET_START = dt.strptime("09:15", "%H:%M").time()
MARKET_END = dt.strptime("15:20", "%H:%M").time()
TIMEZONE = "Asia/Kolkata"
IST = pytz.timezone(TIMEZONE)

# Config files
CONFIG_FILE = "fyers_login_details.json"
TOKENS_DIR = "AccessToken"
TODAY = str(datetime.date.today())
TOKEN_PATH = os.path.join(TOKENS_DIR, f"{TODAY}.json")
SETTINGS_FILE = "settings.json"

# Re-auth guard to avoid infinite recursion
REAUTH_ATTEMPTS = 0
MAX_REAUTH_ATTEMPTS = 3

# Default product type: "INTRADAY", "CNC", "MARGIN", "CO", "BO"
PRODUCT_TYPE = "INTRADAY"


# ============================== OPTION SPECIFIC FUNCTIONS ==============================
def get_lot_size(index_symbol: str) -> int:
    """Get minimum lot size for each index"""
    return MIN_LOT_SIZES.get(index_symbol, 65)


def round_to_nearest_strike(spot_price: float, index_symbol: str) -> int:
    """Round to nearest strike based on index"""
    if "NIFTY50" in index_symbol or "FINNIFTY" in index_symbol:
        return int(round(spot_price / 50.0) * 50)
    elif "BANKNIFTY" in index_symbol or "SENSEX" in index_symbol:
        return int(round(spot_price / 100.0) * 100)
    return int(round(spot_price / 50.0) * 50)


def resolve_option_symbol(fyers: fyersModel.FyersModel, index_symbol: str, is_ce: bool, spot_ltp: float) -> Tuple[
    str, Optional[str], float]:
    """
    Find the nearest CE/PE option for given index, supporting ITM/OTM selection.
    Returns: (option_symbol, expiry_date, strike_price)
    """
    root_map = {
        "NSE:NIFTY50-INDEX": "NSE:NIFTY50-INDEX",
        "NSE:NIFTYBANK-INDEX": "NSE:NIFTYBANK-INDEX",
        "NSE:FINNIFTY-INDEX": "NSE:FINNIFTY-INDEX",
        "BSE:SENSEX-INDEX": "BSE:SENSEX-INDEX",
    }
    root = root_map.get(index_symbol)
    if not root:
        raise RuntimeError(f"No root symbol mapped for {index_symbol}")

    opt_type = "CE" if is_ce else "PE"
    print(f"[optionchain] Looking for {opt_type} option for {index_symbol} with strike distance {STRIKE_DISTANCE}")
    print(f"[optionchain] Spot: {spot_ltp:.2f}, Using root symbol: {root}")

    try:
        resp = fyers.optionchain(data={"symbol": root}) or {}
        data = (resp.get("data") or {}).get("optionsChain") or []
        if not data:
            raise RuntimeError(f"Optionchain response empty for root: {root}")

        filt = [row for row in data if str(row.get("option_type", "")).upper() == opt_type]
        if not filt:
            raise RuntimeError(f"Optionchain has no rows for type {opt_type}")

        def expiry_key(row):
            exp = row.get("expiry")
            try:
                return dt.strptime(exp, "%Y-%m-%d")
            except Exception:
                return dt.max

        earliest_expiry = min(filt, key=expiry_key).get("expiry")
        filt = [r for r in filt if r.get("expiry") == earliest_expiry]

        all_strikes = sorted(list(set(float(r.get("strike_price", r.get("strikePrice"))) for r in filt)))
        if not all_strikes:
            raise RuntimeError("No strikes found for the earliest expiry.")

        atm_strike = min(all_strikes, key=lambda s: abs(s - spot_ltp))
        target_strike = atm_strike

        if STRIKE_DISTANCE != 0:
            try:
                atm_index = all_strikes.index(atm_strike)
                effective_distance = STRIKE_DISTANCE if is_ce else -STRIKE_DISTANCE
                target_index = atm_index + effective_distance

                if 0 <= target_index < len(all_strikes):
                    target_strike = all_strikes[target_index]
                else:
                    print(
                        f"[optionchain] Warning: Strike distance {STRIKE_DISTANCE} is out of bounds. Falling back to ATM.")
            except (ValueError, IndexError):
                print(f"[optionchain] Error finding strike with distance {STRIKE_DISTANCE}. Falling back to ATM.")

        print(f"[optionchain] ATM Strike: {atm_strike}, Target Strike: {target_strike}")

        best = min(filt, key=lambda row: abs(float(row.get("strike_price", row.get("strikePrice"))) - target_strike))
        symbol = best.get("symbol") or best.get("tradingsymbol") or best.get("tsym")
        strike = float(best.get("strike_price", best.get("strikePrice", target_strike)))

        if not symbol:
            raise RuntimeError("Optionchain did not provide a symbol.")

        print(f"[optionchain] Resolved: {symbol}")
        print(f"[optionchain] Strike: {strike}, Expiry: {earliest_expiry}")

        return symbol, earliest_expiry, strike

    except Exception as e:
        print(f"[optionchain] Error for {index_symbol}: {e}")
        raise RuntimeError(f"Failed to resolve option for {index_symbol}: {str(e)}")


def is_market_hours() -> bool:
    """Check if current time is within market hours"""
    now = dt.now(IST).time()
    return MARKET_START <= now <= MARKET_END


# ============================== FYERS LOGIN ==============================
def load_or_prompt_creds():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, dict) and "api_key" in data and "api_secret" in data and "redirect_url" in data:
                return data
        except Exception:
            pass

    print("---- Enter your Fyers Login Credentials (v3) ----")
    creds = {
        "api_key": input("Enter APP ID (e.g., ABCDE12345-100): ").strip(),
        "api_secret": input("Enter SECRET ID: ").strip(),
        "redirect_url": input("Enter Redirect URL (must match app): ").strip(),
    }
    if input("Save to 'fyers_login_details.json'? (Y/N): ").strip().upper() == "Y":
        try:
            base = {}
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r") as f:
                    base = json.load(f) or {}
            base.update(creds)
            with open(CONFIG_FILE, "w") as f:
                json.dump(base, f, indent=2)
            print(f"Saved '{CONFIG_FILE}'.")
        except Exception as e:
            print(f"[auth] Could not save creds: {e}")
    else:
        print("Skipping save.")
    return creds


def build_auth_url(app_id, redirect_uri, state="sample_state"):
    base = "https://api-t1.fyers.in/api/v3/generate-authcode"
    params = (
        f"client_id={quote(app_id)}"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        f"&response_type=code"
        f"&state={quote(state)}"
        f"&scope=openid"
        f"&nonce={int(time.time())}"
    )
    return f"{base}?{params}"


def extract_code(user_input):
    if user_input.startswith("http://") or user_input.startswith("https://"):
        q = parse_qs(urlparse(user_input).query)
        code = q.get("code", [None])[0]
        if not code:
            raise ValueError("No 'code' param found in the provided URL.")
        return code
    return user_input


def sha256_appIdHash(app_id, secret_id):
    return hashlib.sha256(f"{app_id}:{secret_id}".encode("utf-8")).hexdigest()


def validate_authcode(app_id, secret_id, auth_code, max_retries=5):
    url = "https://api-t1.fyers.in/api/v3/validate-authcode"
    payload = {
        "grant_type": "authorization_code",
        "appIdHash": sha256_appIdHash(app_id, secret_id),
        "code": auth_code,
    }
    headers = {"Content-Type": "application/json"}

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=20)
            if r.status_code == 503:
                sleep_s = min(2 ** attempt, 30)
                print(f"[{attempt}/{max_retries}] 503 from auth server. Retrying in {sleep_s}s...")
                time.sleep(sleep_s)
                continue
            r.raise_for_status()
            data = r.json()
            if data.get("s") == "error":
                raise RuntimeError(f"Fyers error {data.get('code')}: {data.get('message')}")
            return data
        except requests.RequestException as e:
            if attempt == max_retries:
                raise
            sleep_s = min(2 ** attempt, 30)
            print(f"[{attempt}/{max_retries}] Network error: {e}. Retrying in {sleep_s}s...")
            time.sleep(sleep_s)


def get_access_token() -> dict:
    """Get or refresh access token"""
    # Try to load from file first
    if os.path.exists(TOKEN_PATH):
        try:
            with open(TOKEN_PATH, "r") as f:
                data = json.load(f)
            if isinstance(data, str):
                access_token = data.strip()
                return {"access_token": access_token}
        except Exception:
            pass

            # Interactive login
    print("[auth] No existing access token found. Starting interactive login...")
    creds = load_or_prompt_creds()
    app_id = creds["api_key"]
    secret_id = creds["api_secret"]
    redirect_uri = creds["redirect_url"]

    auth_url = build_auth_url(app_id, redirect_uri)
    print("\nLogin URL (open in browser, allow & complete login):")
    print(auth_url)

    user_val = input("\nPaste the FULL redirect URL after login, or just the 'code=' value here: ").strip()
    try:
        auth_code = extract_code(user_val)
    except Exception as e:
        print(f"Could not extract code: {e}")
        raise

    token_resp = validate_authcode(app_id, secret_id, auth_code)
    access_token = token_resp.get("access_token")
    if not access_token:
        raise RuntimeError(f"Unexpected token response: {token_resp}")

        # Save token
    os.makedirs(TOKENS_DIR, exist_ok=True)
    with open(TOKEN_PATH, "w") as f:
        json.dump(access_token, f)

    print(f"[auth] Token saved to {TOKEN_PATH}")
    return {"access_token": access_token}


# ============================== SYMBOL STATE ==============================
class SymbolState:
    def __init__(self, symbol: str):
        self.symbol = symbol  # SPOT symbol (e.g., NSE:NIFTY50-INDEX)
        self.option_symbol = None  # Option symbol for execution
        self.option_ltp = 0.0  # Current option LTP
        self.option_entry_price = 0.0  # Entry price of option
        self.data = pd.DataFrame()  # Spot price data for analysis
        self.status = "watch"  # watch, entry_pending, position, cooldown
        self.signal_candle = None
        self.signal_close_ts = None
        self.spot_entry_price = 0.0
        self.spot_stop_price = 0.0
        self.option_stop_price = 0.0
        self.option_high_price = 0.0
        self.qty = 0
        self.gtt_order_id = None

        # Candle tracking
        self.last_candle_ts = None
        self.just_entered = False

        # For option execution
        self.lot_size = get_lot_size(symbol)
        self.strike_price = 0.0
        self.expiry = None
        self.last_option_update = 0

    def reset_position(self):
        """Reset position state"""
        self.option_symbol = None
        self.option_ltp = 0.0
        self.option_entry_price = 0.0
        self.spot_entry_price = 0.0
        self.spot_stop_price = 0.0
        self.option_stop_price = 0.0
        self.option_high_price = 0.0
        self.qty = 0
        self.strike_price = 0.0
        self.expiry = None
        self.just_entered = False
        self.gtt_order_id = None
        self.last_option_update = 0

    def __repr__(self):
        return f"<State {self.symbol} {self.status} opt={self.option_symbol} qty={self.qty}>"


SYMBOL_STATES: Dict[str, SymbolState] = {s: SymbolState(s) for s in SPOT_INDICES}

# Active subscriptions for WebSocket
ACTIVE_SUBSCRIPTIONS: List[str] = SPOT_INDICES.copy()


# ============================== CANDLE MANAGER ==============================
class CandleManager:
    def __init__(self, timeframe_min: int = 5, on_candle=None, tz="Asia/Kolkata"):
        self.tf = int(timeframe_min)
        self.on_candle = on_candle
        self.tz = pytz.timezone(tz)
        self.lock = threading.RLock()
        self.partial: Dict[str, dict] = {}
        self.history: Dict[str, pd.DataFrame] = {}

    def _floor_ts(self, ts: dt) -> dt:
        if ts.tzinfo is None:
            ts = self.tz.localize(ts)
        else:
            ts = ts.astimezone(self.tz)
        ts = ts.replace(tzinfo=None)
        minute = (ts.minute // self.tf) * self.tf
        return ts.replace(second=0, microsecond=0, minute=minute)

    def _parse_ts(self, ts_val) -> dt:
        if ts_val is None:
            return dt.now(self.tz).replace(tzinfo=None)
        if isinstance(ts_val, (int, float)):
            return dt.fromtimestamp(float(ts_val), self.tz).replace(tzinfo=None)
        if isinstance(ts_val, str):
            try:
                dtobj = pd.to_datetime(ts_val)
                if dtobj.tzinfo is None:
                    dtobj = self.tz.localize(dtobj).replace(tzinfo=None)
                else:
                    dtobj = dtobj.astimezone(self.tz).replace(tzinfo=None)
                return dtobj
            except Exception:
                return dt.now(self.tz).replace(tzinfo=None)
        if isinstance(ts_val, dt):
            if ts_val.tzinfo is None:
                return self.tz.localize(ts_val).replace(tzinfo=None)
            return ts_val.astimezone(self.tz).replace(tzinfo=None)
        return dt.now(self.tz).replace(tzinfo=None)

    def process_tick(self, tick: dict):
        try:
            symbol = tick.get("symbol")
            if not symbol:
                return

            ltp = tick.get("ltp")
            if ltp is None:
                return
            ltp = float(ltp)
            vtt = int(tick.get("vtt", 0))

            ts = self._parse_ts(tick.get("timestamp"))
            candle_start = self._floor_ts(ts)

            with self.lock:
                p = self.partial.get(symbol)
                if p is None:
                    new_p = {"ts": candle_start, "open": ltp, "high": ltp,
                             "low": ltp, "close": ltp, "ticks": 1,
                             "start_vtt": vtt, "end_vtt": vtt}
                    self.partial[symbol] = new_p
                    return

                if candle_start == p["ts"]:
                    p["high"] = max(p["high"], ltp)
                    p["low"] = min(p["low"], ltp)
                    p["close"] = ltp
                    p["ticks"] = p.get("ticks", 0) + 1
                    p["end_vtt"] = vtt
                    return

                    # Complete the candle
                completed = dict(p)
                candle_volume = completed.get("end_vtt", 0) - completed.get("start_vtt", 0)
                candle_out = {
                    "symbol": symbol,
                    "ts": completed["ts"],
                    "open": completed["open"],
                    "high": completed["high"],
                    "low": completed["low"],
                    "close": completed["close"],
                    "volume": candle_volume,
                    "ticks": completed.get("ticks", 1),
                }

                # Append to history (only for spot indices)
                if symbol in SPOT_INDICES:
                    df = self.history.get(symbol)
                    row = {"open": candle_out["open"], "high": candle_out["high"],
                           "low": candle_out["low"], "close": candle_out["close"],
                           "volume": candle_out["volume"]}
                    ts_idx = pd.to_datetime(candle_out["ts"])

                    if df is None:
                        df = pd.DataFrame([row], index=[ts_idx])
                    else:
                        df = pd.concat([df, pd.DataFrame([row], index=[ts_idx])])
                        if len(df) > 2000:
                            df = df.tail(2000)
                    df.index.name = "datetime"
                    self.history[symbol] = df

                    # Call callback for spot candles only
                    if callable(self.on_candle):
                        try:
                            self.on_candle(symbol, candle_out)
                        except Exception as e:
                            print(f"[CandleManager] callback error: {e}")

                            # Start new partial candle
                new_partial = {"ts": candle_start, "open": ltp, "high": ltp,
                               "low": ltp, "close": ltp, "ticks": 1,
                               "start_vtt": vtt, "end_vtt": vtt}
                self.partial[symbol] = new_partial

        except Exception as e:
            print(f"[CandleManager:process_tick] error: {e}")


CANDLE_MANAGER: Optional[CandleManager] = None

# ============================== ORDER HELPERS ==============================
FYERS = None
FYERS_SOCKET = None
ACCESS_TOKEN = None


def update_subscriptions():
    """Update WebSocket subscriptions to include active option positions"""
    global ACTIVE_SUBSCRIPTIONS

    # Start with spot indices
    new_subs = SPOT_INDICES.copy()

    # Add any active option positions
    for state in SYMBOL_STATES.values():
        if state.option_symbol and state.status == "position":
            new_subs.append(state.option_symbol)

            # Update if changed
    if set(new_subs) != set(ACTIVE_SUBSCRIPTIONS):
        ACTIVE_SUBSCRIPTIONS = new_subs
        if FYERS_SOCKET:
            try:
                FYERS_SOCKET.subscribe(symbols=ACTIVE_SUBSCRIPTIONS, data_type="SymbolUpdate")
                print(f"[ws] Updated subscriptions: {len(ACTIVE_SUBSCRIPTIONS)} symbols")
            except Exception as e:
                print(f"[ws] Subscription update failed: {e}")


def get_order_details(order_id: str):
    """Get order details including fill price"""
    if FYERS is None:
        return None

    try:
        # Get orderbook to find our order
        orderbook = FYERS.orderbook()
        if isinstance(orderbook, dict) and orderbook.get("s") == "ok":
            for order in orderbook.get("orderBook", []):
                if str(order.get("id")) == str(order_id):
                    return order
    except Exception as e:
        print(f"[order] Failed to get details for {order_id}: {e}")

    return None


def place_market_order(symbol: str, qty: int, side: int) -> dict:
    """Place market order for options and try to get fill price"""
    if not TRADING_ENABLED:
        return {"s": "error", "message": "trading disabled"}

    if not is_market_hours():
        return {"s": "error", "message": "outside market hours"}

    side_str = "BUY" if side == 1 else "SELL"

    data = {
        "symbol": symbol,
        "qty": qty,
        "type": 2,  # Market order
        "side": side,
        "productType": "INTRADAY",  # Options are always intraday
        "limitPrice": 0,
        "stopPrice": 0,
        "validity": "DAY",
        "disclosedQty": 0,
        "offlineOrder": False,
    }

    print(f"[order] Placing market {side_str} for {qty} of {symbol}")

    if FYERS is None:
        return {"s": "error", "message": "no fyers client"}

    for attempt in range(1, 4):
        try:
            resp = FYERS.place_order(data=data)
            print(f"[order] Response: {resp}")

            if isinstance(resp, dict) and resp.get("s") == "ok":
                # Try to get fill price
                order_id = resp.get("id")
                if order_id:
                    # Wait a moment for order to process
                    time.sleep(1)
                    order_details = get_order_details(order_id)
                    if order_details:
                        traded_price = order_details.get("averagePrice") or order_details.get("tradedPrice")
                        if traded_price and float(traded_price) > 0:
                            resp["fill_price"] = float(traded_price)
                            print(f"[order] Fill price: ₹{traded_price}")

                            # Update subscriptions if this is a new option position
                if side == 1:
                    update_subscriptions()

            return resp
        except Exception as e:
            print(f"[order] Attempt {attempt} failed: {e}")
            time.sleep(1 * attempt)

    return {"s": "error", "message": "order failed after retries"}


# ============================== INDICATORS ==============================
def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def rsi(series: pd.Series, length: int) -> pd.Series:
    """Calculate RSI"""
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.ewm(com=length - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=length - 1, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi_val = 100 - (100 / (1 + rs))
    return rsi_val


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "volume" not in df.columns:
        return df
    df = df.copy()
    df["ema_fast_entry"] = ema(df["close"], ENTRY_FAST_EMA)
    df["rsi"] = rsi(df["close"], RSI_PERIOD)

    # Daily resetting VWAP
    df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
    df['tpv'] = df['typical_price'] * df['volume']
    df.index = pd.to_datetime(df.index)
    df['date'] = df.index.date
    df['cumulative_tpv'] = df.groupby('date')['tpv'].cumsum()
    df['cumulative_volume'] = df.groupby('date')['volume'].cumsum()
    df['vwap'] = df['cumulative_tpv'] / df['cumulative_volume']
    df.drop(columns=['typical_price', 'tpv', 'date', 'cumulative_tpv', 'cumulative_volume'], inplace=True)

    rng = (df["high"] - df["low"]) / df["close"].replace(0, pd.NA)
    df["ok_signal"] = rng >= MIN_RANGE_PCT if MIN_RANGE_PCT > 0 else True
    return df


# ============================== CANDLE CALLBACK ==============================
def on_completed_candle(symbol: str, candle: dict):
    """Process completed candle - all calculations on SPOT price"""
    st = SYMBOL_STATES.get(symbol)
    if st is None:
        return

    try:
        row = {"open": candle["open"], "high": candle["high"],
               "low": candle["low"], "close": candle["close"], "volume": candle.get("volume", 0)}
        idx = pd.to_datetime(candle["ts"])

        df = st.data
        if df is None or df.empty:
            df = pd.DataFrame([row], index=[idx])
        else:
            df = pd.concat([df, pd.DataFrame([row], index=[idx])])
            df = df.loc[~df.index.duplicated(keep='last')]
            df = df.tail(2000)

        df.index.name = "datetime"
        st.data = compute_indicators(df)
        st.last_candle_ts = idx

        # Evaluate strategy
        evaluate_on_new_candle(st)

    except Exception as e:
        print(f"[on_completed_candle] error for {symbol}: {e}")

        # ============================== TICK HANDLER ==============================


def on_tick(tick: dict):
    """Handle incoming ticks for both spot and option symbols"""
    symbol = tick.get("symbol")
    ltp = float(tick.get("ltp", 0.0))
    ts_val = tick.get("timestamp")
    vtt = tick.get("vtt", 0)

    ts = None
    if ts_val:
        ts = dt.fromtimestamp(ts_val, IST).replace(tzinfo=None)
    else:
        ts = dt.now(IST).replace(tzinfo=None)

        # Process through candle manager (for spot indices only)
    if CANDLE_MANAGER:
        try:
            CANDLE_MANAGER.process_tick(
                {"symbol": symbol, "ltp": ltp, "vtt": vtt, "timestamp": ts.isoformat()}
            )
        except Exception as e:
            print(f"[on_tick:candle_manager] error: {e}")

            # Handle spot price updates
    if symbol in SPOT_INDICES:
        handle_spot_tick(symbol, ltp, ts)
    else:
        # Handle option price updates
        handle_option_tick(symbol, ltp, ts)


def handle_spot_tick(symbol: str, ltp: float, ts: dt):
    """Handle spot price ticks - FIXED VERSION"""
    state = SYMBOL_STATES.get(symbol)
    if state is None:
        return

        # ENTRY LOGIC - NEXT CANDLE ENTRY
    if state.status == "entry_pending" and state.signal_candle is not None:
        try:
            # Check if we're in the next candle
            sig_start = pd.to_datetime(state.signal_candle["ts"])
            next_allowed_start = sig_start + pd.Timedelta(minutes=TIMEFRAME_MIN)

            current_ts = pd.to_datetime(ts)
            candle_start = CANDLE_MANAGER._floor_ts(current_ts.to_pydatetime())

            if pd.to_datetime(candle_start) == next_allowed_start:
                # Check for breakout above signal high
                signal_high = float(state.signal_candle["high"])
                if ltp > signal_high and is_market_hours():
                    print(f"\n[{symbol}] ENTRY TRIGGERED: LTP {ltp:.2f} > signal_high {signal_high:.2f}")

                    # Resolve option symbol for execution
                    try:
                        option_symbol, expiry, strike = resolve_option_symbol(FYERS, symbol, is_ce=True, spot_ltp=ltp)
                        state.option_symbol = option_symbol
                        state.strike_price = strike
                        state.expiry = expiry

                        # Calculate quantity
                        qty = state.lot_size * LOT_MULTIPLIER

                        print(f"[entry] Lot size: {state.lot_size}, Qty: {qty} shares ({LOT_MULTIPLIER} lots)")

                        # Place order
                        resp = place_market_order(option_symbol, qty, side=1)

                        if isinstance(resp, dict) and resp.get("s") == "ok":
                            state.spot_entry_price = ltp
                            state.qty = qty
                            state.status = "position"
                            state.just_entered = True

                            # CRITICAL: Get actual option fill price or estimate
                            fill_price = resp.get("fill_price")
                            if fill_price and fill_price > 0:
                                state.option_entry_price = fill_price
                                print(f"[entry] Actual fill price: ₹{fill_price:.2f}")
                            else:
                                # Estimate option price (roughly 0.5-1% of spot for ATM options)
                                state.option_entry_price = ltp * 0.007  # 0.7% estimate
                                print(f"[entry] Estimated option price: ₹{state.option_entry_price:.2f}")

                                # Set stop loss based on SL_MODE
                            if SL_MODE == "signal_low":
                                state.spot_stop_price = float(state.signal_candle["low"])
                            elif SL_MODE == "swing_low":
                                recent_lows = state.data['low'].tail(SWING_LOOKBACK)
                                state.spot_stop_price = recent_lows.min()

                            print(f"\n[ENTRY CONFIRMED] {symbol} -> {option_symbol}")
                            print(f"  Spot Price: {ltp:.2f}")
                            print(f"  Option Entry: ₹{state.option_entry_price:.2f}")
                            print(f"  Spot SL: {state.spot_stop_price:.2f}")
                            print(f"  Qty: {state.qty} shares ({LOT_MULTIPLIER} lots)")
                            print(f"  Strike: {strike}, Expiry: {expiry}")

                            # Clear signal
                            state.signal_candle = None
                            state.signal_close_ts = None
                        else:
                            print(f"[ENTRY FAILED] {symbol}: {resp}")
                            state.status = "watch"
                            state.reset_position()

                    except Exception as e:
                        print(f"[ENTRY ERROR] {symbol}: {e}")
                        import traceback
                        traceback.print_exc()
                        state.status = "watch"
                        state.reset_position()

        except Exception as e:
            print(f"[handle_spot_tick:entry] error: {e}")
            import traceback
            traceback.print_exc()

            # EXIT LOGIC - Stop-Loss Only
    if state.status == "position":
        exit_reason = None

        # Stop Loss Check
        if state.spot_stop_price > 0 and ltp <= state.spot_stop_price:
            exit_reason = f"STOP LOSS HIT: LTP {ltp:.2f} <= SL {state.spot_stop_price:.2f}"

        if exit_reason and is_market_hours():
            print(f"\n[{symbol}] {exit_reason}")
            try:
                resp = place_market_order(state.option_symbol, state.qty, side=-1)
                if isinstance(resp, dict) and resp.get("s") == "ok":
                    print(f"[EXIT CONFIRMED] {symbol} -> {state.option_symbol}")
                    if state.option_ltp > 0:
                        pnl = (state.option_ltp - state.option_entry_price) * state.qty
                        print(f"  Approx P&L: ₹{pnl:.2f}")
                    state.status = "cooldown"
                    state.reset_position()
                else:
                    print(f"[EXIT FAILED] {symbol}: {resp}")
            except Exception as e:
                print(f"[handle_spot_tick:exit] error: {e}")
                import traceback
                traceback.print_exc()


def handle_option_tick(symbol: str, ltp: float, ts: dt):
    """Handle option price ticks for exit management - FIXED"""
    # Find which state has this option symbol
    for state in SYMBOL_STATES.values():
        if state.option_symbol == symbol and state.status == "position":
            # Skip first few seconds after entry
            if state.just_entered:
                current_time = time.time()
                if current_time - state.last_option_update < 5:  # 5 second cooldown
                    return
                state.just_entered = False

            state.option_ltp = ltp

            # Update option high price
            if ltp > state.option_high_price:
                state.option_high_price = ltp

            current_time = time.time()

            # Update option price every 30 seconds to avoid spam
            if current_time - state.last_option_update > 30:
                # Calculate current P&L
                current_pnl = (ltp - state.option_entry_price) * state.qty
                pnl_percent = (
                    ((ltp - state.option_entry_price) / state.option_entry_price) * 100
                    if state.option_entry_price > 0 else 0.0
                )

                print(
                    f"[{state.symbol}] Option: ₹{ltp:.2f} | Entry: ₹{state.option_entry_price:.2f} | P&L: ₹{current_pnl:.2f} ({pnl_percent:.1f}%) | High: ₹{state.option_high_price:.2f}")
                state.last_option_update = current_time

                # OPTION-BASED EXIT LOGIC HAS BEEN REMOVED. EXIT IS NOW HANDLED IN handle_spot_tick
            break

            # ============================== STRATEGY EVALUATION ==============================


def evaluate_on_new_candle(st: SymbolState):
    """Evaluate strategy on new candle - ALL CALCULATIONS ON SPOT PRICE"""
    df = st.data
    if df is None or df.shape[0] < 2:
        return

    last_ts = st.last_candle_ts
    if last_ts is None or last_ts not in df.index:
        return

    curr = df.loc[last_ts]
    prev = df.iloc[-2]

    curr_open = float(curr["open"])
    curr_close = float(curr["close"])
    curr_high = float(curr["high"])
    curr_low = float(curr["low"])

    ema_fast = float(curr.get("ema_fast_entry", float("nan")))
    vwap = float(curr.get("vwap", float("nan")))
    rsi_val = float(curr.get("rsi", float("nan")))

    # ENTRY SIGNAL (BULLISH - VWAP BODY CROSS)
    if st.status == "watch" and is_market_hours():
        open_below_vwap = curr_open <= vwap
        closed_above_vwap = curr_close > vwap
        fast_ema_above_vwap = ema_fast > vwap
        green_ok = (not REQUIRE_GREEN_SIGNAL) or (curr_close > curr_open)
        ok_signal = bool(curr.get("ok_signal", True))
        rsi_ok = RSI_ENTRY_MIN <= rsi_val <= RSI_ENTRY_MAX

        if open_below_vwap and closed_above_vwap and fast_ema_above_vwap and green_ok and ok_signal and rsi_ok:
            st.signal_candle = {
                "ts": curr.name,
                "open": curr_open,
                "high": curr_high,
                "low": curr_low,
                "close": curr_close,
            }

            st.status = "entry_pending"
            print(f"\n[SIGNAL] {st.symbol}: VWAP CROSS ENTRY SIGNAL")
            print(f"  Spot: {curr_close:.2f}")
            print(f"  VWAP: {vwap:.2f}, Fast EMA: {ema_fast:.2f}, RSI: {rsi_val:.2f}")
            print(f"  Signal High: {curr_high:.2f}, Low: {curr_low:.2f}")
            print(f"  Waiting for next candle breakout...\n")

            # EXIT SIGNAL (RSI) - Based on spot price
    elif st.status == "position":
        rsi_profit_taking = RSI_EXIT_MIN <= rsi_val <= RSI_EXIT_MAX
        rsi_stop_loss = rsi_val < RSI_ENTRY_MIN
        exit_reason = None

        if rsi_profit_taking:
            exit_reason = f"RSI PROFIT TAKE on candle close: RSI {rsi_val:.2f} is between {RSI_EXIT_MIN}-{RSI_EXIT_MAX}"
        elif rsi_stop_loss:
            exit_reason = f"RSI STOP LOSS on candle close: RSI {rsi_val:.2f} < {RSI_ENTRY_MIN}"

        if exit_reason and is_market_hours():
            print(f"\n[{st.symbol}] {exit_reason}")
            try:
                resp = place_market_order(st.option_symbol, st.qty, side=-1)
                if isinstance(resp, dict) and resp.get("s") == "ok":
                    print(f"[EXIT CONFIRMED] {st.symbol} -> {st.option_symbol}")
                    if st.option_ltp > 0:
                        pnl = (st.option_ltp - st.option_entry_price) * st.qty
                        print(f"  Approx P&L: ₹{pnl:.2f}")
                    st.status = "cooldown"
                    st.reset_position()
                else:
                    print(f"[EXIT FAILED] {st.symbol}: {resp}")
            except Exception as e:
                print(f"[evaluate_on_new_candle:exit] error: {e}")
                import traceback
                traceback.print_exc()

                # ============================== WEBSOCKET HANDLERS ==============================


def on_ws_message(raw):
    try:
        if not isinstance(raw, list):
            msgs = [raw]
        else:
            msgs = raw

        for m in msgs:
            symbol = m.get("symbol")
            ltp = m.get("ltp")
            if symbol and ltp is not None:
                # Pass the entire message dictionary to on_tick
                on_tick(m)

    except Exception as e:
        print(f"[ws] on_message error: {e}")


def on_ws_open():
    print(f"[ws:open] Subscribing to {len(ACTIVE_SUBSCRIPTIONS)} symbols")
    try:
        FYERS_SOCKET.subscribe(symbols=ACTIVE_SUBSCRIPTIONS, data_type="SymbolUpdate")
    except Exception as e:
        print("[ws:open] subscribe failed:", e)


def on_ws_error(err):
    print("[ws:error]", err)


def on_ws_close(msg):
    print("[ws:close]", msg)


# ============================== DATA WARMUP ==============================
def fetch_historical_data(fyers, symbol: str, days: int = 3) -> pd.DataFrame:
    """Fetch historical data for warmup"""
    if fyers is None:
        return pd.DataFrame()

    end = dt.now(IST)
    start = end - timedelta(days=days)

    try:
        payload = {
            "symbol": symbol,
            "resolution": str(TIMEFRAME_MIN),
            "date_format": "1",
            "range_from": start.strftime("%Y-%m-%d"),
            "range_to": end.strftime("%Y-%m-%d"),
            "cont_flag": "1",
        }

        r = fyers.history(data=payload)
        if isinstance(r, dict) and r.get("s") == "ok":
            df = pd.DataFrame(
                r["candles"],
                columns=["ts", "open", "high", "low", "close", "volume"],
            )
            df["ts"] = (
                pd.to_datetime(df["ts"], unit="s", utc=True)
                .dt.tz_convert(TIMEZONE)
                .dt.tz_localize(None)
            )
            df = df.set_index("ts")[["open", "high", "low", "close", "volume"]]
            return df
    except Exception as e:
        print(f"[warmup] Failed for {symbol}: {e}")

    return pd.DataFrame()


def warmup_data():
    """Warmup historical data for all symbols"""
    if FYERS is None:
        return

    print("[warmup] Fetching historical data...")
    for symbol in SPOT_INDICES:
        try:
            df = fetch_historical_data(FYERS, symbol, days=3)
            if not df.empty:
                SYMBOL_STATES[symbol].data = compute_indicators(df)
                SYMBOL_STATES[symbol].last_candle_ts = df.index[-1]
                print(f"[warmup] {symbol}: {len(df)} candles loaded")
            else:
                print(f"[warmup] {symbol}: No data")
        except Exception as e:
            print(f"[warmup] Error for {symbol}: {e}")

            # ============================== MAIN ==============================


def main():
    global FYERS, FYERS_SOCKET, ACCESS_TOKEN, CANDLE_MANAGER
    global TIMEFRAME_MIN, ENTRY_FAST_EMA, LOT_MULTIPLIER
    global RSI_PERIOD, RSI_ENTRY_MIN, RSI_ENTRY_MAX, RSI_EXIT_MIN, RSI_EXIT_MAX
    global TRADING_ENABLED

    # Parse arguments
    parser = argparse.ArgumentParser(description="VWAP-EMA-RSI Strategy - Options Execution")
    parser.add_argument("--timeframe", "-t", type=int, default=TIMEFRAME_MIN)
    parser.add_argument("--entry-fast-ema", type=int, default=ENTRY_FAST_EMA)
    parser.add_argument("--rsi-period", type=int, default=RSI_PERIOD)
    parser.add_argument("--rsi-entry", type=str, default=f"{RSI_ENTRY_MIN}-{RSI_ENTRY_MAX}", help="RSI entry range (e.g., 40-55)")
    parser.add_argument("--rsi-exit", type=str, default=f"{RSI_EXIT_MIN}-{RSI_EXIT_MAX}", help="RSI exit range (e.g., 70-75)")
    parser.add_argument("--lot-multiplier", type=int, default=LOT_MULTIPLIER, help="Number of lots per trade")
    parser.add_argument("--test", action="store_true", help="Test mode without live connection")
    parser.add_argument("--no-trade", action="store_true", help="Disable trading")

    args = parser.parse_args()

    # Update globals
    TIMEFRAME_MIN = args.timeframe
    ENTRY_FAST_EMA = args.entry_fast_ema
    RSI_PERIOD = args.rsi_period
    try:
        RSI_ENTRY_MIN, RSI_ENTRY_MAX = map(int, args.rsi_entry.split('-'))
        RSI_EXIT_MIN, RSI_EXIT_MAX = map(int, args.rsi_exit.split('-'))
    except ValueError:
        print("Invalid RSI range format. Use min-max (e.g., 40-55).")
        sys.exit(1)

    LOT_MULTIPLIER = args.lot_multiplier

    if args.no_trade:
        TRADING_ENABLED = False

    print("\n" + "=" * 80)
    print("VWAP-EMA-RSI STRATEGY - OPTIONS EXECUTION")
    print("=" * 80)
    print(f"Timeframe: {TIMEFRAME_MIN} minutes")
    print(f"Entry EMA: {ENTRY_FAST_EMA}")
    print(f"RSI Period: {RSI_PERIOD}")
    print(f"RSI Entry Range: {RSI_ENTRY_MIN}-{RSI_ENTRY_MAX}")
    print(f"RSI Exit Range: {RSI_EXIT_MIN}-{RSI_EXIT_MAX}")
    print(f"Lot Size per trade: {LOT_MULTIPLIER}")
    print(f"Spot Indices: {SPOT_INDICES}")
    print(f"Fyers Lot Sizes: NIFTY=65, BANKNIFTY=30, FINNIFTY=60")
    print(f"Trading Enabled: {TRADING_ENABLED}")
    print("=" * 80 + "\n")

    if not TRADING_ENABLED:
        print("[WARNING] Trading is DISABLED (--no-trade flag). Running in paper trading mode.\n")

        # Initialize Candle Manager
    CANDLE_MANAGER = CandleManager(TIMEFRAME_MIN, on_candle=on_completed_candle, tz=TIMEZONE)

    if args.test:
        print("[TEST MODE] Running without live connection")
        return

        # Get access token and initialize Fyers
    try:
        auth = get_access_token()
        ACCESS_TOKEN = auth["access_token"]
        client_id = ACCESS_TOKEN.split(":")[0] if ":" in ACCESS_TOKEN else ACCESS_TOKEN

        FYERS = fyersModel.FyersModel(
            client_id=client_id,
            is_async=False,
            token=ACCESS_TOKEN,
            log_path=""
        )

        print("[auth] Fyers model initialized successfully")

        # Warmup historical data
        warmup_data()

    except Exception as e:
        print(f"[auth] Failed to initialize Fyers: {e}")
        return

        # Initialize WebSocket
    FYERS_SOCKET = data_ws.FyersDataSocket(
        access_token=ACCESS_TOKEN,
        log_path="",
        litemode=True,
        write_to_file=False,
        reconnect=True,
        on_connect=on_ws_open,
        on_close=on_ws_close,
        on_error=on_ws_error,
        on_message=on_ws_message,
    )

    print("[start] Connecting WebSocket...")
    try:
        FYERS_SOCKET.connect()

        # Keep running
        print("\n[bot] Strategy is running. Press Ctrl+C to stop.\n")
        while True:
            # Periodic subscription updates
            update_subscriptions()
            time.sleep(5)

    except KeyboardInterrupt:
        print("\n[exit] Interrupted by user")
    except Exception as e:
        print(f"[fatal] Error: {e}")


if __name__ == "__main__":
    main()