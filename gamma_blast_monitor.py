#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Combined & Rectified (Updated):
# - Robust auth (APPID:token, dual-host exchange, upfront quotes() probe with forced re-auth on -15)
# - Batched quotes + short-term ROC
# - Option-chain fetch with symbol-variant fallback
# - Real-time expiry selection (skip same-day after 15:25 IST)
# - Gamma Blast (ΔOI window + dynamic threshold + price ROC confirm)
# - Gamma Wall Detection (Max OI Levels)
# - VWAP & EMA (9, 21) Indication
# - Candle Pattern Recognition (Hammer, Shooting Star)
# - CSV logging
# - Customizable Candle Timeframe (CLI)
# - Automated Trading (Gamma Blast + Trend Confirmation)
# - State Persistence (JSON) and Broker Sync
#
# Requirements:
#   pip install fyers-apiv3 requests pandas numpy
#
# NOTE: 'redirect_url' in fyers_login_details.json MUST match your Fyers app config exactly.

import os
import sys
import json
import time
import random
import datetime
import math
import hashlib
import csv
import requests
import argparse
import pandas as pd
import numpy as np
from urllib.parse import urlparse, parse_qs, quote
from fyers_apiv3 import fyersModel

# ===============================
# Configuration
# ===============================
CONFIG_FILE = "fyers_login_details.json"
TOKENS_DIR = "AccessToken"
TODAY = str(datetime.date.today())
TOKEN_PATH = os.path.join(TOKENS_DIR, f"{TODAY}.json")

SYMBOLS = [
    "NSE:NIFTY50-INDEX",
    "NSE:NIFTYBANK-INDEX",
    "NSE:FINNIFTY-INDEX",
    "BSE:SENSEX-INDEX",
]

INITIAL_REFRESH_INTERVAL = 60  # seconds
OPTIONCHAIN_EVERY = 3  # heavy OC every Nth cycle
PER_SYMBOL_DELAY = 0.4  # seconds spacing for heavy calls
MAX_CYCLES = None  # None = run indefinitely

SAVE_CSV = True
CSV_OUTDIR = "optionchain_csv"

# BS/Greeks defaults (fallback only)
RISK_FREE_RATE = 0.06
DIVIDEND_YIELD = 0.0

# Expiry selection / cutoff
SAME_DAY_CUTOFF = (15, 25)  # IST 15:25 — after this, avoid same-day expiry
MIN_T_SECONDS = 5 * 60
MIN_T_YEARS = MIN_T_SECONDS / (365.0 * 24 * 3600)

# Retry / backoff
HTTP_MAX_ATTEMPTS = 3
HTTP_BACKOFF_BASE = 1.2
HTTP_JITTER = (0.1, 0.6)

# -------- Gamma Blast params --------
GB_STRIKES_AROUND_ATM = 8  # window on each side
GB_OICH_MIN_ABS_FLOOR = 100.0  # absolute floor for threshold
GB_OICH_SCALE = 0.05  # scale * max(CE_abs, PE_abs)
GB_ROC_BP_THRESHOLD = 5.0  # price ROC (bp) confirmation
ROC_WINDOW_SEC = 70  # price confirm lookback

# -------- Indicator Params --------
EMA_FAST = 9
EMA_SLOW = 21
CANDLE_TIMEFRAME = "5" # Default 5 minutes, can be overridden by CLI or editing this variable

# -------- Trade State --------
TRADE_ENABLED = False
LOT_MULTIPLIER = 1
RISK_REWARD = 2.0
STATE_FILE = "gamma_bot_state.json"

class TradeState:
    def __init__(self):
        self.positions = {} # {symbol: {'type': 'CE'/'PE', 'entry': float, 'sl': float, 'target': float, 'qty': int, 'opt_sym': str}}
        self.load_state()

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    self.positions = json.load(f)
                print(f"[STATE] Loaded {len(self.positions)} positions from {STATE_FILE}")
            except Exception as e:
                print(f"[STATE] Error loading state: {e}")

    def save_state(self):
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(self.positions, f, indent=2)
        except Exception as e:
            print(f"[STATE] Error saving state: {e}")

    def in_position(self, symbol):
        return symbol in self.positions

    def get_position(self, symbol):
        return self.positions.get(symbol)

    def open_position(self, symbol, type_, entry, sl, target, qty, opt_sym):
        self.positions[symbol] = {
            'type': type_, 'entry': entry, 'sl': sl, 'target': target, 'qty': qty, 'opt_sym': opt_sym
        }
        self.save_state()
        print(f"[TRADE] OPEN {type_} on {symbol} | Spot Entry: {entry:.2f}, SL: {sl:.2f}, TGT: {target:.2f} | Opt: {opt_sym} x {qty}")

    def close_position(self, symbol, reason, price):
        if symbol in self.positions:
            p = self.positions[symbol]
            print(f"[TRADE] CLOSE {p['type']} on {symbol} | Reason: {reason} | Spot Price: {price:.2f}")
            del self.positions[symbol]
            self.save_state()

    def update_quantity(self, symbol, new_qty):
        if symbol in self.positions:
            self.positions[symbol]['qty'] = new_qty
            self.save_state()
            print(f"[SYNC] Updated {symbol} quantity to {new_qty}")

GLOBAL_TRADE_STATE = TradeState()

# ===============================
# Symbol Master (Lot Size / Step)
# ===============================
SYMBOL_MASTER_MAP = {}  # {symbol_name: {"lot_size": int, "step": float}}


def fetch_symbol_master():
    """Downloads Fyers NSE/BSE master CSVs to learn Lot Size and Strike Step."""
    print("Fetching Symbol Master CSVs...")
    urls = {
        "NSE": "https://public.fyers.in/sym_details/NSE_FO.csv",
        "BSE": "https://public.fyers.in/sym_details/BSE_FO.csv"
    }
    # Temporary storage to calculate step from strikes
    # {symbol_root: {strikes: set(), lot: int}}
    temp_data = {}

    for exch, url in urls.items():
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            lines = r.text.strip().split("\n")
            reader = csv.reader(lines)
            header = next(reader, None)
            if header and "Fytoken" in str(header[0]):
                pass
            else:
                reader = csv.reader(lines)

            for row in reader:
                if len(row) < 14: continue
                root = row[13].strip().upper()
                if not root: continue
                try:
                    lot = int(row[3])
                except:
                    continue
                if root not in temp_data:
                    temp_data[root] = {"lot": lot, "strikes": set()}
                temp_data[root]["lot"] = lot

        except Exception as e:
            print(f"Warning: Failed to fetch/parse {exch} master: {e}")

    mapping = {
        "NSE:NIFTY50-INDEX": "NIFTY",
        "NSE:NIFTYBANK-INDEX": "BANKNIFTY",
        "NSE:FINNIFTY-INDEX": "FINNIFTY",
        "BSE:SENSEX-INDEX": "SENSEX",
        "NSE:MIDCPNIFTY-INDEX": "MIDCPNIFTY",
        "BSE:BANKEX-INDEX": "BANKEX",
    }

    defaults = {
        "NIFTY": {"step": 50, "lot": 25},
        "BANKNIFTY": {"step": 100, "lot": 15},
        "FINNIFTY": {"step": 50, "lot": 25},
        "SENSEX": {"step": 100, "lot": 10},
        "MIDCPNIFTY": {"step": 25, "lot": 50},
        "BANKEX": {"step": 100, "lot": 15},
    }

    for sym_full, root in mapping.items():
        d = defaults.get(root, {"step": 100, "lot": 1})
        final_lot = d["lot"]
        final_step = d["step"]
        if root in temp_data:
            fetched_lot = temp_data[root]["lot"]
            if fetched_lot > 0:
                final_lot = fetched_lot
        SYMBOL_MASTER_MAP[sym_full] = {"lot_size": final_lot, "step": final_step}

    print("Symbol Master loaded.")


# ===============================
# Login helpers
# ===============================
def load_or_prompt_creds():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, str):
                        data[k] = v.strip()
            return data
    print("---- Enter your Fyers Login Credentials (v3) ----")
    creds = {
        "api_key": input("Enter APP ID (e.g., ABCDE12345-100): ").strip(),
        "api_secret": input("Enter SECRET ID: ").strip(),
        "redirect_url": input("Enter Redirect URL (must match app): ").strip(),
    }
    if input("Save to 'fyers_login_details.json'? (Y/N): ").strip().upper() == "Y":
        with open(CONFIG_FILE, "w") as f:
            json.dump(creds, f, indent=2)
        print(f"Saved '{CONFIG_FILE}'.")
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
    s = user_input.strip()
    if s.startswith("http://") or s.startswith("https://"):
        code = parse_qs(urlparse(s).query).get("code", [None])[0]
        if not code:
            raise ValueError("No 'code' param found in the provided URL.")
        return code
    return s


def validate_authcode(app_id, secret_id, auth_code, max_retries=5):
    def _exchange_on(host):
        url = f"https://{host}.fyers.in/api/v3/validate-authcode"
        payload = {
            "grant_type": "authorization_code",
            "appIdHash": hashlib.sha256(f"{app_id}:{secret_id}".encode("utf-8")).hexdigest(),
            "code": auth_code,
        }
        headers = {"Content-Type": "application/json"}
        for attempt in range(1, max_retries + 1):
            try:
                r = requests.post(url, headers=headers, json=payload, timeout=20)
                if r.status_code == 503:
                    sleep_s = min(2 ** attempt, 30)
                    print(f"[{attempt}/{max_retries}] 503 from auth server ({host}). Retrying in {sleep_s}s...")
                    time.sleep(sleep_s)
                    continue
                r.raise_for_status()
                data = r.json()
                if data.get("s") == "error":
                    raise RuntimeError(f"Fyers error {data.get('code')}: {data.get('message')}")
                return data
            except requests.RequestException as e:
                if attempt == max_retries: raise
                sleep_s = min(2 ** attempt, 30)
                print(f"[{attempt}/{max_retries}] Network error on {host}: {e}. Retrying in {sleep_s}s...")
                time.sleep(sleep_s)

    try:
        return _exchange_on("api-t1")
    except Exception as e1:
        print(f"[auth] api-t1 exchange failed: {e1}")
        print("[auth] retrying on 'api'...")
        return _exchange_on("api")


def get_access_token():
    creds = load_or_prompt_creds()
    app_id = creds["api_key"]
    secret_id = creds["api_secret"]
    redirect_uri = creds["redirect_url"]
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, "r") as f:
            access_token = json.load(f)
        print(f"Access Token loaded from file for {app_id}.")
        if isinstance(access_token, dict) and "access_token" in access_token:
            access_token = access_token["access_token"]
        return access_token, app_id
    auth_url = build_auth_url(app_id, redirect_uri)
    print("\nLogin URL (open in browser, allow & complete login):")
    print(auth_url)
    user_val = input("\nPaste the FULL redirect URL after login, or just the 'code=' value here: ").strip()
    try:
        auth_code = extract_code(user_val)
    except Exception as e:
        print(f"Could not extract code: {e}")
        sys.exit(1)
    try:
        token_resp = validate_authcode(app_id, secret_id, auth_code)
        access_token = token_resp.get("access_token")
        if not access_token:
            raise RuntimeError(f"Unexpected token response: {token_resp}")
        os.makedirs(TOKENS_DIR, exist_ok=True)
        with open(TOKEN_PATH, "w") as f:
            json.dump(token_resp, f)
        print("\nLogin successful.")
        print(f"Saved token to: {TOKEN_PATH}")
        return access_token, app_id
    except Exception as e:
        print(f"\nLogin Failed: {e}")
        sys.exit(1)

# ===============================
# Retry wrapper for Fyers calls
# ===============================
def call_with_retries(func, payload, max_attempts=HTTP_MAX_ATTEMPTS):
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = func(data=payload) if "data" in func.__code__.co_varnames else func(payload)
        except TypeError:
            resp = func(payload)
        except Exception as e:
            err = {"status": "error", "error_code": None, "message": str(e)}
            if attempt >= max_attempts: return None, err
            sleep_s = HTTP_BACKOFF_BASE ** attempt + random.uniform(*HTTP_JITTER)
            print(f"DEBUG: exception -> backing off {sleep_s:.1f}s then retrying...")
            time.sleep(sleep_s)
            continue

        if not isinstance(resp, dict):
            err = {"status": "error", "error_code": None, "message": "Non-dict response"}
            if attempt >= max_attempts: return None, err
            sleep_s = HTTP_BACKOFF_BASE ** attempt + random.uniform(*HTTP_JITTER)
            print(f"DEBUG: non-dict -> backing off {sleep_s:.1f}s then retrying...")
            time.sleep(sleep_s)
            continue

        s = resp.get("s")
        code = resp.get("code")
        if s == "ok": return resp, None
        err = {"status": "error", "error_code": code, "message": resp.get("message")}
        print(
            f"DEBUG: call returned error (s='{s}', code={code}, message={resp.get('message')}). Attempt {attempt}/{max_attempts}")
        if attempt >= max_attempts: return None, err
        base = 1.0 if code == 429 else 0.8
        sleep_s = (base * (HTTP_BACKOFF_BASE ** attempt)) + random.uniform(*HTTP_JITTER)
        if code == 429:
            print(f"DEBUG: 429 -> backing off {sleep_s:.1f}s then retrying...")
        time.sleep(sleep_s)

# ===============================
# Quotes (batched) + cache + ROC
# ===============================
_QUOTE_CACHE = {}  # {symbol: (ltp, ts)}
_CACHE_TTL = 10
_PRICE_HISTORY = {}  # {symbol: [(ts, ltp), ...]}


def _variants_for_quotes(symbol):
    return [symbol, symbol.split(":", 1)[-1]] if symbol.upper().startswith("NSE:") else [symbol, "NSE:" + symbol]


def get_ltps_batched(fy, symbols):
    now = time.time()
    result, to_fetch = {}, []
    for s in symbols:
        if s in _QUOTE_CACHE and (now - _QUOTE_CACHE[s][1]) < _CACHE_TTL:
            result[s] = (_QUOTE_CACHE[s][0], None)
        else:
            to_fetch.append(s)
    if not to_fetch: return result

    first_variants, first_map = [], {}
    for s in to_fetch:
        vlist = _variants_for_quotes(s)
        first_map[s] = vlist[0]
        first_variants.append(vlist[0])
    payload = {"symbols": ",".join(first_variants)}
    resp, err = call_with_retries(fy.quotes, payload)
    dlist = resp.get("d") if (resp and isinstance(resp.get("d"), list)) else []

    def _extract_map(dlist):
        mp = {}
        for item in dlist or []:
            if not isinstance(item, dict): continue
            name = item.get("n")
            v = item.get("v") or {}
            if isinstance(v, dict) and not v.get("errmsg"):
                lp = try_float(v.get("lp"), v.get("lastPrice"), v.get("last_price"))
                if name and lp is not None: mp[name] = float(lp)
        return mp

    ret_map = _extract_map(dlist) if not err else {}
    failures = []
    for s in to_fetch:
        var = first_map[s]
        alt = var.split(":", 1)[-1] if ":" in var else ("NSE:" + var)
        ltp = ret_map.get(var) or ret_map.get(alt)
        if ltp is not None:
            _QUOTE_CACHE[s] = (ltp, now)
            result[s] = (ltp, None)
        else:
            failures.append(s)

    if failures:
        second_variants, second_map = [], {}
        for s in failures:
            vlist = _variants_for_quotes(s)
            second_map[s] = vlist[1] if len(vlist) > 1 else vlist[0]
            second_variants.append(second_map[s])
        resp2, err2 = call_with_retries(fy.quotes, {"symbols": ",".join(second_variants)})
        dlist2 = resp2.get("d") if (resp2 and isinstance(resp2.get("d"), list)) else []
        ret_map2 = _extract_map(dlist2) if not err2 else {}
        for s in failures:
            var2 = second_map[s]
            alt2 = var2.split(":", 1)[-1] if ":" in var2 else ("NSE:" + var2)
            ltp2 = ret_map2.get(var2) or ret_map2.get(alt2)
            if ltp2 is not None:
                _QUOTE_CACHE[s] = (ltp2, now)
                result[s] = (ltp2, None)
            else:
                e = err2 or err or {"status": "error", "error_code": None, "message": "could not parse LTP"}
                print(f"DEBUG: get_ltp failed. Last attempted variants: {_variants_for_quotes(s)}")
                result[s] = (None, e)
    return result


def update_price_history(symbol, ltp, keep_secs=300):
    if ltp is None: return
    now = time.time()
    arr = _PRICE_HISTORY.setdefault(symbol, [])
    arr.append((now, float(ltp)))
    cutoff = now - keep_secs
    while len(arr) > 2 and arr[0][0] < cutoff:
        arr.pop(0)


def roc_bp_last(symbol, window_sec=ROC_WINDOW_SEC):
    arr = _PRICE_HISTORY.get(symbol) or []
    if len(arr) < 2: return 0.0
    now = time.time()
    recent = [p for p in arr if now - p[0] <= window_sec] or arr[-2:]
    if len(recent) < 2: return 0.0
    p0, p1 = recent[0][1], recent[-1][1]
    if p0 <= 0: return 0.0
    return (p1 / p0 - 1.0) * 10000.0  # basis points


# ===============================
# Utilities / formatting
# ===============================
def try_float(*vals):
    for v in vals:
        if v is None: continue
        try:
            return float(v)
        except Exception:
            continue
    return None


def format_ltp(v):
    if v is None: return "N/A"
    try:
        return f"{float(v):,.2f}"
    except Exception:
        return str(v)


def format_oi(v):
    if v is None: return "N/A"
    try:
        return f"{int(round(float(v))):,}"
    except Exception:
        return str(v)

# ===============================
# Indicators & Patterns
# ===============================
def fetch_candles(fy, symbol, resolution=None, days=5):
    """
    Fetches historical candle data for indicators.
    """
    if resolution is None:
        resolution = CANDLE_TIMEFRAME

    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=days)

    payload = {
        "symbol": symbol,
        "resolution": str(resolution),
        "date_format": "1",
        "range_from": start_date.strftime("%Y-%m-%d"),
        "range_to": end_date.strftime("%Y-%m-%d"),
        "cont_flag": "1"
    }

    try:
        response = fy.history(data=payload)
        if response.get("s") != "ok":
            return None
        candles = response.get("candles", [])
        if not candles:
            return None

        df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
        # timestamp is epoch
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="s").dt.tz_localize('UTC').dt.tz_convert('Asia/Kolkata')
        return df
    except Exception as e:
        print(f"Error fetching candles for {symbol}: {e}")
        return None

def calculate_vwap(df):
    """
    Calculates Intraday VWAP.
    Resets at the start of each day.
    """
    df = df.copy()
    df['date'] = df['datetime'].dt.date
    df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
    df['pv'] = df['typical_price'] * df['volume']

    # Group by date and calculate cumulative sums
    df['cum_pv'] = df.groupby('date')['pv'].cumsum()
    df['cum_vol'] = df.groupby('date')['volume'].cumsum()
    df['vwap'] = df['cum_pv'] / df['cum_vol']

    return df

def calculate_indicators(df):
    """
    Calculates EMA and Patterns.
    """
    if df is None or df.empty:
        return None

    # EMA
    df[f'ema_{EMA_FAST}'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
    df[f'ema_{EMA_SLOW}'] = df['close'].ewm(span=EMA_SLOW, adjust=False).mean()

    # VWAP
    df = calculate_vwap(df)

    return df

def detect_patterns(df):
    """
    Detects Hammer and Shooting Star on the LAST COMPLETE candle.
    """
    if df is None or len(df) < 2:
        return "N/A"

    # We look at the last closed candle (second to last row if current is forming,
    # but strictly speaking history API might not return the forming candle depending on params.
    # Assuming last row is the latest available candle.)
    # Let's use the last row.
    row = df.iloc[-1]

    O, H, L, C = row['open'], row['high'], row['low'], row['close']
    body = abs(C - O)
    upper_wick = H - max(C, O)
    lower_wick = min(C, O) - L
    range_len = H - L

    pattern = []

    # Green Hammer: Green Body, Lower Wick >= 2 * Body, Upper Wick small
    if C > O and lower_wick >= 2 * body and upper_wick <= 0.5 * body:
        pattern.append("Green Hammer")

    # Red Shooting Star: Red Body, Upper Wick >= 2 * Body, Lower Wick small
    if C < O and upper_wick >= 2 * body and lower_wick <= 0.5 * body:
        pattern.append("Red Shooting Star")

    return ", ".join(pattern) if pattern else "None"

def find_gamma_walls(strikes_map):
    """
    Finds strikes with highest OI for CE (Resistance) and PE (Support).
    """
    max_ce_oi = -1
    max_ce_strike = -1
    max_pe_oi = -1
    max_pe_strike = -1

    for strike, data in strikes_map.items():
        ce = data.get("CE")
        pe = data.get("PE")

        if ce and ce.get("oi") is not None:
            if ce["oi"] > max_ce_oi:
                max_ce_oi = ce["oi"]
                max_ce_strike = strike

        if pe and pe.get("oi") is not None:
            if pe["oi"] > max_pe_oi:
                max_pe_oi = pe["oi"]
                max_pe_strike = strike

    return max_ce_strike, max_ce_oi, max_pe_strike, max_pe_oi

# ===============================
# Black-Scholes (fallback greeks)
# ===============================
def std_norm_pdf(x): return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def std_norm_cdf(x): return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S, K, T, r, q, sigma, option_type):
    if T <= 0: return max(S - K, 0.0) if option_type == "CE" else max(K - S, 0.0)
    if sigma <= 0:
        return math.exp(-r * T) * max((S * math.exp(-q * T) - K) if option_type == "CE" else (K - S * math.exp(-q * T)),
                                      0.0)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if option_type == "CE":
        return S * math.exp(-q * T) * std_norm_cdf(d1) - K * math.exp(-r * T) * std_norm_cdf(d2)
    return K * math.exp(-r * T) * std_norm_cdf(-d2) - S * math.exp(-q * T) * std_norm_cdf(-d1)


def bs_delta(S, K, T, r, q, sigma, option_type):
    if T <= 0: return 1.0 if (option_type == "CE" and S > K) else (
        0.0 if option_type == "CE" else (-1.0 if S < K else 0.0))
    if sigma <= 0: return 1.0 if (option_type == "CE" and S > K) else 0.0
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return math.exp(-q * T) * (std_norm_cdf(d1) if option_type == "CE" else (std_norm_cdf(d1) - 1.0))


def bs_gamma(S, K, T, r, q, sigma):
    if T <= 0 or sigma <= 0: return 0.0
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return (math.exp(-q * T) * std_norm_pdf(d1)) / (S * sigma * math.sqrt(T))


def implied_vol_bisect(market_price, S, K, T, r, q, option_type, tol=1e-6, maxiter=80):
    intrinsic = bs_price(S, K, 0.0, r, q, 0.0, option_type)
    if market_price < 0 or market_price < intrinsic - 1e-12: return None
    low, high = 1e-8, 5.0
    for _ in range(maxiter):
        mid = 0.5 * (low + high)
        diff = bs_price(S, K, T, r, q, mid, option_type) - market_price
        if abs(diff) < tol: return mid
        if diff < 0:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


# ===============================
# Option chain parsing & expiry
# ===============================
def safe_get(d, *keys):
    for k in keys:
        if isinstance(d, dict) and k in d:
            return d[k]
    return None


def extract_side_fields(item):
    ltp = try_float(safe_get(item, "ltp", "lastPrice", "lp"))
    oi = try_float(safe_get(item, "oi", "openInterest", "open_interest", "OI"))
    prev_oi = try_float(safe_get(item, "prev_oi", "prevOI", "prev_oi"))
    oich = try_float(safe_get(item, "oich", "oi_change", "oiCh", "oich"))
    oichp = try_float(safe_get(item, "oichp", "oi_change_pct", "oiChp", "oichp"))
    if oich is None and oi is not None and prev_oi is not None:
        try:
            oich = float(oi) - float(prev_oi)
        except Exception:
            oich = None
    vol = try_float(safe_get(item, "volume", "vol", "volumeTraded"))
    delta = try_float(safe_get(item, "delta", "DELTA", "Delta"))
    gamma = try_float(safe_get(item, "gamma", "GAMMA", "Gamma"))
    iv = try_float(safe_get(item, "iv", "IV", "impliedVolatility", "implied_volatility", "ivol"))
    for gk in ("greeks", "greek", "greeksData", "greek_values", "greekMap"):
        g = item.get(gk)
        if isinstance(g, dict):
            delta = delta or try_float(safe_get(g, "delta", "DELTA", "Delta"))
            gamma = gamma or try_float(safe_get(g, "gamma", "GAMMA", "Gamma"))
            iv = iv or try_float(safe_get(g, "iv", "impliedVolatility", "ivol"))
    return {"ltp": ltp, "oi": oi, "oich": oich, "oichp": oichp, "prev_oi": prev_oi,
            "volume": vol, "delta": delta, "gamma": gamma, "iv": iv}


def find_strikes_list(response):
    if isinstance(response, dict):
        data = response.get("data", {})
        if isinstance(data, dict):
            oc = data.get("optionsChain")
            if isinstance(oc, list): return oc
            # recursive fallback

    def find_list(obj):
        if isinstance(obj, list):
            if obj and isinstance(obj[0], dict) and any(k in obj[0] for k in ("strike_price", "strikePrice", "strike")):
                return obj
            for it in obj:
                r = find_list(it)
                if r: return r
        elif isinstance(obj, dict):
            for v in obj.values():
                r = find_list(v)
                if r: return r
        return None

    return find_list(response)


def parse_expiry_to_T_from_date(expiry_date_str, expiry_hour=15, expiry_minute=30):
    try:
        d = datetime.datetime.strptime(expiry_date_str.strip(), "%d-%m-%Y")
    except Exception:
        return None
    expiry_dt = datetime.datetime(d.year, d.month, d.day, expiry_hour, expiry_minute)
    now = datetime.datetime.now()
    delta = expiry_dt - now
    if delta.total_seconds() <= 0: return 0.0
    return delta.total_seconds() / (365.0 * 24 * 3600)


def choose_best_expiry(response, same_day_cutoff=SAME_DAY_CUTOFF):
    if not isinstance(response, dict): return None
    data = response.get("data") or {}
    expiry_list = data.get("expiryData") or []
    if not isinstance(expiry_list, list) or not expiry_list: return None

    dates = []
    for item in expiry_list:
        if isinstance(item, dict) and isinstance(item.get("date"), str):
            dates.append(item["date"].strip())

    def date_only(dt_str):
        try:
            return datetime.datetime.strptime(dt_str, "%d-%m-%Y").date()
        except Exception:
            return None

    today = datetime.date.today()
    # Prefer next future date
    for dstr in dates:
        dd = date_only(dstr)
        if dd and dd > today:
            return dstr
            # Same-day only if before cutoff
    now = datetime.datetime.now()
    cutoff = datetime.datetime(now.year, now.month, now.day, same_day_cutoff[0], same_day_cutoff[1])
    for dstr in dates:
        dd = date_only(dstr)
        if dd and dd == today and now < cutoff:
            return dstr
            # Fallback: first listed
    return dates[0] if dates else None


def parse_optionchain_response_with_expiry(response):
    options_chain = find_strikes_list(response)
    chosen_expiry = choose_best_expiry(response)
    if not isinstance(options_chain, list):
        return None, chosen_expiry
    strikes = {}
    for item in options_chain:
        if not isinstance(item, dict): continue
        strike = item.get("strike_price") or item.get("strikePrice") or item.get("strike")
        opt_type = item.get("option_type") or item.get("optionType") or item.get("option")
        if strike is None: continue
        try:
            strike = int(float(strike))
        except Exception:
            continue
        if strike not in strikes:
            strikes[strike] = {"CE": None, "PE": None}
        side = extract_side_fields(item)
        placed = False
        if isinstance(opt_type, str) and opt_type.strip():
            if opt_type.upper() == "CE":
                strikes[strike]["CE"] = side
                placed = True
            elif opt_type.upper() == "PE":
                strikes[strike]["PE"] = side
                placed = True
        if not placed:
            sym = item.get("symbol", "")
            if isinstance(sym, str) and sym.endswith("CE"):
                strikes[strike]["CE"] = side
            elif isinstance(sym, str) and sym.endswith("PE"):
                strikes[strike]["PE"] = side
            else:
                for v in item.values():
                    if isinstance(v, dict):
                        t = safe_get(v, "option_type", "optionType", "option")
                        if isinstance(t, str) and t.upper() == "CE": strikes[strike]["CE"] = side; break
                        if isinstance(t, str) and t.upper() == "PE": strikes[strike]["PE"] = side; break
    return strikes, chosen_expiry


# ===============================
# OC fetcher with variant fallback
# ===============================
def _variants_for_optionchain(symbol):
    v = []
    if ":" in symbol:
        v.append(symbol)
        v.append(symbol.split(":", 1)[-1])
    else:
        v.append(symbol)
        v.append("NSE:" + symbol)
    out, seen = [], set()
    for x in v:
        if x not in seen:
            out.append(x)
            seen.add(x)
    return out


def get_optionchain_response(fy, symbol):
    for var in _variants_for_optionchain(symbol):
        resp, err = call_with_retries(fy.optionchain, {"symbol": var, "strikecount": 17})
        if err:
            if err.get("error_code") == -300 or (resp and resp.get("s") == "error"):
                print(f"DEBUG: optionchain() for '{symbol}' -> error on variant '{var}', trying next...")
                continue
            else:
                return resp, err, var
        return resp, None, var
    return None, {"status": "error", "error_code": -300, "message": "Please provide a valid symbol"}, None


# ===============================
# Gamma Blast computation
# ===============================
def aggregate_doi_around_atm(strikes_map, atm_strike, step, window=GB_STRIKES_AROUND_ATM):
    lo = atm_strike - window * step
    hi = atm_strike + window * step
    CE_sum = PE_sum = CE_abs = PE_abs = 0.0
    for K in sorted([k for k in strikes_map.keys() if lo <= k <= hi]):
        ce = strikes_map[K].get("CE") or {}
        pe = strikes_map[K].get("PE") or {}
        ce_oich = ce.get("oich")
        pe_oich = pe.get("oich")
        if ce_oich is not None:
            CE_sum += float(ce_oich)
            CE_abs += abs(float(ce_oich))
        if pe_oich is not None:
            PE_sum += float(pe_oich)
            PE_abs += abs(float(pe_oich))
    return CE_sum, PE_sum, CE_abs, PE_abs


def gamma_blast_decision(CE_sum, PE_sum, CE_abs, PE_abs, roc_bp):
    eff_thr = max(GB_OICH_MIN_ABS_FLOOR, GB_OICH_SCALE * max(CE_abs, PE_abs))
    ce_mag, pe_mag = abs(CE_sum), abs(PE_sum)
    if max(ce_mag, pe_mag) < eff_thr:
        return False, "NA", eff_thr, "below_threshold"
        # RECTIFIED: Gamma Blast requires POSITIVE ΔOI (buildup) to imply dealer short gamma
    if ce_mag > pe_mag and roc_bp > +GB_ROC_BP_THRESHOLD and CE_sum > 0:
        return True, "BULL", eff_thr, f"ce_oich={CE_sum:.3g}"
    if pe_mag > ce_mag and roc_bp < -GB_ROC_BP_THRESHOLD and PE_sum > 0:
        return True, "BEAR", eff_thr, f"pe_oich={PE_sum:.3g}"
    return False, "NA", eff_thr, "no_price_confirm"


# ===============================
# Printer / CSV for one symbol (with Gamma Blast)
# ===============================
def print_and_save_chain_for_symbol(fy, symbol, S, num_strikes=8):
    # Determine Step from Master Map
    meta = SYMBOL_MASTER_MAP.get(symbol, {})
    step = meta.get("step", 100)
    lot_size = meta.get("lot_size", 1)

    # Fallback if map missing
    if not step:
        step = 100 if ("NIFTYBANK" in symbol or "BANKNIFTY" in symbol) else 50

    atm_strike = round(S / step) * step
    print(f"\nLive LTP for {symbol} is: {S}")
    print(f"ATM strike is: {atm_strike} (Step: {step}, Lot: {lot_size})")

    # ---- Fetch Indicators (New) ----
    print("Fetching indicators...")
    vwap = 0
    pattern = "None"
    try:
        df_hist = fetch_candles(fy, symbol)
        if df_hist is not None:
            df_ind = calculate_indicators(df_hist)
            if df_ind is not None and not df_ind.empty:
                last = df_ind.iloc[-1]
                vwap = last.get('vwap', 0)
                ema_f = last.get(f'ema_{EMA_FAST}', 0)
                ema_s = last.get(f'ema_{EMA_SLOW}', 0)
                pattern = detect_patterns(df_ind)

                print(f"INDICATORS [{CANDLE_TIMEFRAME}m]: VWAP={vwap:.2f} | EMA{EMA_FAST}={ema_f:.2f} | EMA{EMA_SLOW}={ema_s:.2f}")
                print(f"PATTERNS   [{CANDLE_TIMEFRAME}m]: {pattern}")
            else:
                print("Indicators: Not enough data.")
        else:
            print("Indicators: Failed to fetch history.")
    except Exception as e:
        print(f"Indicators Error: {e}")

    resp, err, used_sym = get_optionchain_response(fy, symbol)
    if err or not isinstance(resp, dict):
        print("Warning: could not parse optionchain response.")
        return {"status": "oc_error", "detail": err or {}}

    strikes_map, expiry_date = parse_optionchain_response_with_expiry(resp)
    if strikes_map is None:
        print("Warning: could not parse strikes from optionchain response.")
        return {"status": "oc_error", "detail": {"message": "no strikes"}}

    # ---- Gamma Wall (New) ----
    cw_k, cw_v, pw_k, pw_v = find_gamma_walls(strikes_map)
    print(f"GAMMA WALLS: Max Call OI: {format_oi(cw_v)} @ {cw_k} | Max Put OI: {format_oi(pw_v)} @ {pw_k}")

    # Expiry -> time to expiry (years)
    T = None
    skip_iv = False
    if expiry_date:
        T = parse_expiry_to_T_from_date(expiry_date, 15, 30)
    if T is None or T <= MIN_T_YEARS:
        ed = (resp.get("data") or {}).get("expiryData") or []
        for item in ed:
            if isinstance(item, dict) and isinstance(item.get("date"), str):
                T_try = parse_expiry_to_T_from_date(item["date"], 15, 30)
                if T_try and T_try > MIN_T_YEARS:
                    expiry_date = item["date"]
                    T = T_try
                    break
    if T is None or T <= MIN_T_YEARS:
        T = 1.0 / 365.0
        skip_iv = True

    print(f"(Using expiry: {expiry_date if expiry_date else 'N/A'}, T={T:.6f} years, skip_iv={skip_iv})")

    # ---- Gamma Blast core ----
    CE_sum = PE_sum = CE_abs = PE_abs = 0.0
    if strikes_map:
        CE_sum, PE_sum, CE_abs, PE_abs = aggregate_doi_around_atm(strikes_map, atm_strike, step, GB_STRIKES_AROUND_ATM)
    roc_bp = roc_bp_last(symbol, window_sec=ROC_WINDOW_SEC)
    blast, blast_dir, thr, reason = gamma_blast_decision(CE_sum, PE_sum, CE_abs, PE_abs, roc_bp)

    print(f"\nΔOI window (±{GB_STRIKES_AROUND_ATM}):  CE_sum={CE_sum:.3g}  PE_sum={PE_sum:.3g}  "
          f"CE_abs={CE_abs:.3g}  PE_abs={PE_abs:.3g}  | ROC{ROC_WINDOW_SEC}s={roc_bp:.1f}bp  thr={thr:.1f}")
    if blast:
        print(f"*** GAMMA BLAST {blast_dir} *** ({reason})")
        if blast_dir == "BULL":
            print("Dealer hedge pressure: short calls -> BUY index/futures (↑).")
        else:
            print("Dealer hedge pressure: short puts  -> SELL index/futures (↓).")
    else:
        print(f"No blast ({reason})")

    # ---- Trading Logic (New) ----
    if TRADE_ENABLED:
        try:
            if not GLOBAL_TRADE_STATE.in_position(symbol):
                # ENTRY LOGIC
                signal_bull = (blast and blast_dir == "BULL")
                signal_bear = (blast and blast_dir == "BEAR")

                # Confirmations (Strict: Price vs VWAP)
                # Patterns can be additive
                valid_bull = signal_bull and (S > vwap)
                valid_bear = signal_bear and (S < vwap)

                if valid_bull:
                    print(f"[TRADE SIGNAL] BULLISH ENTRY on {symbol}")
                    # Find CE symbol
                    ce_strike = atm_strike
                    ce_sym_full = ""
                    # Locate option symbol in map if possible
                    if strikes_map.get(ce_strike) and strikes_map[ce_strike].get("CE"):
                         # The map stores data, but we need the symbol string.
                         # parse_optionchain.. extracts data. It doesn't save the symbol string explicitly in the simplified map.
                         # We need to re-resolve or assume standard format.
                         # Safer to call resolve_option_symbol helper if we import it, or just use Fyers naming convention
                         # For robust trading, let's use the helper we are about to add.
                         pass

                    # Resolve Symbol
                    opt_sym, _ = resolve_option_symbol(fy, True, S, symbol)
                    if opt_sym:
                        sl_price = S - (S * 0.005) # 0.5% SL
                        tgt_price = S + (S * 0.005 * RISK_REWARD)

                        resp = place_market_order(fy, opt_sym, lot_size * LOT_MULTIPLIER, "BUY")
                        if resp.get("s") == "ok":
                            GLOBAL_TRADE_STATE.open_position(symbol, "CE", S, sl_price, tgt_price, lot_size * LOT_MULTIPLIER, opt_sym)

                elif valid_bear:
                    print(f"[TRADE SIGNAL] BEARISH ENTRY on {symbol}")
                    # Resolve Symbol (Buy PE)
                    opt_sym, _ = resolve_option_symbol(fy, False, S, symbol)
                    if opt_sym:
                        sl_price = S + (S * 0.005)
                        tgt_price = S - (S * 0.005 * RISK_REWARD)

                        resp = place_market_order(fy, opt_sym, lot_size * LOT_MULTIPLIER, "BUY")
                        if resp.get("s") == "ok":
                            GLOBAL_TRADE_STATE.open_position(symbol, "PE", S, sl_price, tgt_price, lot_size * LOT_MULTIPLIER, opt_sym)

            else:
                # EXIT LOGIC
                pos = GLOBAL_TRADE_STATE.get_position(symbol)
                p_type = pos['type']
                entry = pos['entry']
                sl = pos['sl']
                target = pos['target']
                opt_sym = pos['opt_sym']
                qty = pos['qty']

                exit_signal = False
                reason = ""

                if p_type == "CE":
                    if S <= sl:
                        exit_signal = True; reason = "SL Hit"
                    elif S >= target:
                        exit_signal = True; reason = "Target Hit"
                    elif blast and blast_dir == "BEAR": # Reversal
                        exit_signal = True; reason = "Reversal Signal"
                elif p_type == "PE":
                    if S >= sl:
                        exit_signal = True; reason = "SL Hit"
                    elif S <= target:
                        exit_signal = True; reason = "Target Hit"
                    elif blast and blast_dir == "BULL": # Reversal
                        exit_signal = True; reason = "Reversal Signal"

                if exit_signal:
                    resp = place_market_order(fy, opt_sym, qty, "SELL") # Close Long Option
                    if resp.get("s") == "ok":
                        GLOBAL_TRADE_STATE.close_position(symbol, reason, S)

        except Exception as e:
            print(f"[TRADE ERROR] {e}")

    # ---- Pretty OC table around ATM ----
    minK = atm_strike - (num_strikes * step)
    maxK = atm_strike + (num_strikes * step)

    header = (
        f"{'CE LTP':>9} {'CE Δ':>8} {'CE Γ':>8} {'CE IV%':>8} {'CE OIΔ':>11} | "
        f"{'STRIKE':>7} | "
        f"{'PE LTP':>9} {'PE Δ':>8} {'PE Γ':>8} {'PE IV%':>8} {'PE OIΔ':>11}"
    )
    print(f"\n--- Option Chain for {symbol} (ATM +/- {num_strikes} strikes) ---")
    print(header)
    print("-" * len(header))

    rows = []
    for K in sorted([k for k in strikes_map.keys() if minK <= k <= maxK]):
        ce = strikes_map[K].get("CE") or {}
        pe = strikes_map[K].get("PE") or {}

        # CE greeks (fallback if missing)
        ce_ltp, ce_iv, ce_delta, ce_gamma = ce.get("ltp"), ce.get("iv"), ce.get("delta"), ce.get("gamma")
        if ce_ltp is not None and not skip_iv:
            # Step 1: Calculate IV only if it's missing.
            if ce_iv is None:
                iv_est = implied_vol_bisect(ce_ltp, S, K, T, RISK_FREE_RATE, DIVIDEND_YIELD, "CE")
                if iv_est is not None:
                    ce_iv = iv_est  # Use the estimated IV

            # Step 2: With a valid IV, calculate missing greeks.
            if ce_iv is not None:
                if ce_delta is None:
                    ce_delta = bs_delta(S, K, T, RISK_FREE_RATE, DIVIDEND_YIELD, ce_iv, "CE")
                if ce_gamma is None:
                    ce_gamma = bs_gamma(S, K, T, RISK_FREE_RATE, DIVIDEND_YIELD, ce_iv)

        # PE greeks (fallback if missing)
        pe_ltp, pe_iv, pe_delta, pe_gamma = pe.get("ltp"), pe.get("iv"), pe.get("delta"), pe.get("gamma")
        if pe_ltp is not None and not skip_iv:
            # Step 1: Calculate IV only if it's missing.
            if pe_iv is None:
                iv_est = implied_vol_bisect(pe_ltp, S, K, T, RISK_FREE_RATE, DIVIDEND_YIELD, "PE")
                if iv_est is not None:
                    pe_iv = iv_est  # Use the estimated IV

            # Step 2: With a valid IV, calculate missing greeks.
            if pe_iv is not None:
                if pe_delta is None:
                    pe_delta = bs_delta(S, K, T, RISK_FREE_RATE, DIVIDEND_YIELD, pe_iv, "PE")
                if pe_gamma is None:
                    pe_gamma = bs_gamma(S, K, T, RISK_FREE_RATE, DIVIDEND_YIELD, pe_iv)

        # OIΔ recompute if missing
        ce_oich = ce.get("oich")
        if ce_oich is None and ce.get("oi") is not None and ce.get("prev_oi") is not None:
            try:
                ce_oich = float(ce.get("oi")) - float(ce.get("prev_oi"))
            except Exception:
                ce_oich = None
        pe_oich = pe.get("oich")
        if pe_oich is None and pe.get("oi") is not None and pe.get("prev_oi") is not None:
            try:
                pe_oich = float(pe.get("oi")) - float(pe.get("prev_oi"))
            except Exception:
                pe_oich = None

        print(
            f"{format_ltp(ce_ltp):>9} {(f'{ce_delta:,.4f}' if ce_delta is not None else 'N/A'):>8} {(f'{ce_gamma:,.6f}' if ce_gamma is not None else 'N/A'):>8} {(f'{(ce_iv * 100):.2f}' if ce_iv is not None else 'N/A'):>8} {format_oi(ce_oich):>11} | "
            f"{K:>7} | "
            f"{format_ltp(pe_ltp):>9} {(f'{pe_delta:,.4f}' if pe_delta is not None else 'N/A'):>8} {(f'{pe_gamma:,.6f}' if pe_gamma is not None else 'N/A'):>8} {(f'{(pe_iv * 100):.2f}' if pe_iv is not None else 'N/A'):>8} {format_oi(pe_oich):>11}"
        )

        rows.append({
            "timestamp": f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}",
            "symbol": symbol, "ltp": S,
            "expiry": expiry_date, "T_years": T, "strike": K,
            "ce_ltp": ce_ltp, "ce_iv": ce_iv, "ce_delta": ce_delta, "ce_gamma": ce_gamma,
            "ce_oi": ce.get("oi"), "ce_oich": ce_oich, "ce_prev_oi": ce.get("prev_oi"), "ce_vol": ce.get("volume"),
            "pe_ltp": pe_ltp, "pe_iv": pe_iv, "pe_delta": pe_delta, "pe_gamma": pe_gamma,
            "pe_oi": pe.get("oi"), "pe_oich": pe_oich, "pe_prev_oi": pe.get("prev_oi"), "pe_vol": pe.get("volume"),
            "atm_strike": atm_strike, "window_strikes": GB_STRIKES_AROUND_ATM,
            "CE_sum": CE_sum, "PE_sum": PE_sum, "CE_abs": CE_abs, "PE_abs": PE_abs,
            "thr": thr, "roc_bp": roc_bp, "blast": blast, "blast_dir": blast_dir, "blast_reason": reason
        })

    if SAVE_CSV and rows:
        os.makedirs(CSV_OUTDIR, exist_ok=True)
        safe_symbol = symbol.replace(":", "_").replace("/", "_").replace(" ", "_")
        csv_path = os.path.join(CSV_OUTDIR, f"{safe_symbol}_optionchain_{TODAY}.csv")
        file_exists = os.path.exists(csv_path)
        keys = list(rows[0].keys())
        try:
            with open(csv_path, "a", newline="", encoding="utf-8") as cf:
                writer = csv.DictWriter(cf, fieldnames=keys)
                if not file_exists: writer.writeheader()
                writer.writerows(rows)
            print(f"\nAppended CSV: {csv_path}")
        except Exception as e:
            print(f"Failed to save CSV: {e}")

    return {"status": "ok"}


# ===============================
# Auth probe & client init (rectified)
# ===============================
def _quotes_probe(fy):
    try:
        resp = fy.quotes(data={"symbols": "NSE:NIFTY50-INDEX"})
    except Exception as e:
        return False, (None, f"exception: {e}")
    if isinstance(resp, dict) and resp.get("s") == "ok":
        return True, None
    code = resp.get("code") if isinstance(resp, dict) else None
    msg = resp.get("message") if isinstance(resp, dict) else str(resp)[:200]
    return False, (code, msg)


def ensure_valid_token_and_client():
    # Try existing token
    access_token, app_id = get_access_token()
    token_prefixed = access_token if str(access_token).startswith(f"{app_id}:") else f"{app_id}:{access_token}"
    fy = fyersModel.FyersModel(client_id=app_id, token=token_prefixed, log_path=os.getcwd())

    ok, err = _quotes_probe(fy)
    if ok:
        return fy, app_id, token_prefixed

    code, msg = err or (None, "")
    if code == -15:
        # Delete today's token and re-auth cleanly
        try:
            if os.path.exists(TOKEN_PATH):
                os.remove(TOKEN_PATH)
                print(f"Probe: token invalid (-15). Deleted {TOKEN_PATH}. Re-auth required…")
        except Exception:
            pass

        creds = load_or_prompt_creds()
        print("\nLogin URL (open in browser, allow & complete login):")
        print(build_auth_url(creds["api_key"], creds["redirect_url"]))
        user_val = input("\nPaste FULL redirect URL or just the code: ").strip()
        auth_code = extract_code(user_val)
        token_resp = validate_authcode(creds["api_key"], creds["api_secret"], auth_code)
        new_token = token_resp.get("access_token")
        if not new_token:
            raise RuntimeError(f"Unexpected token response: {token_resp}")

        os.makedirs(TOKENS_DIR, exist_ok=True)
        with open(TOKEN_PATH, "w") as f:
            json.dump(token_resp, f)

        # ---- ROBUST TOKEN PREFIX LOGIC ----
        app_id = creds["api_key"].strip()
        raw_token = str(new_token).strip()

        # 1. Standard: AppID:Token
        token_v1 = raw_token if raw_token.startswith(f"{app_id}:") else f"{app_id}:{raw_token}"
        print(f"DEBUG: Trying token format 1 (AppID:Token): {token_v1[:15]}...***")

        fy = fyersModel.FyersModel(client_id=app_id, token=token_v1, log_path=os.getcwd())
        ok, err = _quotes_probe(fy)
        if ok:
            print("quotes() OK with Standard format.")
            return fy, app_id, token_v1

        # 2. Raw Token (some envs/versions)
        print(f"DEBUG: Format 1 failed {err}. Trying format 2 (Raw Token)...")
        token_v2 = raw_token
        fy2 = fyersModel.FyersModel(client_id=app_id, token=token_v2, log_path=os.getcwd())
        ok2, err2 = _quotes_probe(fy2)
        if ok2:
            print("quotes() OK with Raw Token format.")
            return fy2, app_id, token_v2

        # 3. Base ID (if hyphenated, e.g. XYZ-100 -> XYZ)
        if "-" in app_id:
            base_id = app_id.split("-")[0]
            print(f"DEBUG: Format 2 failed {err2}. Trying format 3 (BaseID:Token) with {base_id}...")
            token_v3 = raw_token if raw_token.startswith(f"{base_id}:") else f"{base_id}:{raw_token}"
            fy3 = fyersModel.FyersModel(client_id=app_id, token=token_v3, log_path=os.getcwd())
            ok3, err3 = _quotes_probe(fy3)
            if ok3:
                print("quotes() OK with BaseID format.")
                return fy3, app_id, token_v3

        raise RuntimeError(f"quotes() still failing after re-auth strategies. Last error: {err2}")

    # Other error codes
    raise RuntimeError(f"quotes() probe failed: code={code}, message={msg}")

# ===============================
# Order Helpers (Added)
# ===============================
def resolve_option_symbol(fyers, is_ce, spot_ltp, symbol_root):
    # Mapping for roots (simplified)
    # The monitor loop iterates SYMBOLS = ["NSE:NIFTY50-INDEX", ...]
    # We need to map "NSE:NIFTY50-INDEX" -> "NSE:NIFTY50-INDEX" for option chain fetching.
    try:
        resp = fyers.optionchain(data={"symbol": symbol_root}) or {}
        chain = (resp.get("data") or {}).get("optionChain", []) or (resp.get("data") or {}).get("optionsChain", [])
        if not chain: return None, None

        step = SYMBOL_MASTER_MAP.get(symbol_root, {}).get("step", 50)
        target = round(spot_ltp / step) * step
        opt_type = "CE" if is_ce else "PE"

        filt = [row for row in chain if str(row.get("option_type", "")).upper() == opt_type]
        if not filt: return None, None

        # Sort by Expiry
        def expiry_key(row):
            exp = row.get("expiry_date", row.get("expiry"))
            try:
                if not exp: return datetime.datetime.max
                exp = str(exp).strip()
                # Try multiple formats
                for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d%b%y"):
                    try:
                        return datetime.datetime.strptime(exp, fmt)
                    except ValueError:
                        continue
                return datetime.datetime.max
            except:
                return datetime.datetime.max

        # Earliest expiry
        filt.sort(key=expiry_key)
        best_expiry = filt[0].get("expiry_date", filt[0].get("expiry"))

        # Filter for this expiry
        filt = [r for r in filt if r.get("expiry_date", r.get("expiry")) == best_expiry]

        # Nearest strike
        def strike_key(row):
            sp = row.get("strike_price", row.get("strikePrice"))
            return abs(float(sp) - target)

        best = min(filt, key=strike_key)
        sym = best.get("symbol") or best.get("tradingsymbol") or best.get("tsym")
        return sym, best_expiry
    except Exception as e:
        print(f"Option resolve error: {e}")
        return None, None

def place_market_order(fyers, symbol, qty, side):
    """
    side: 'BUY' or 'SELL'
    """
    s_map = {"BUY": 1, "SELL": -1}
    # User requested to carry position, so use MARGIN (Normal) instead of INTRADAY
    data = {
        "symbol": symbol, "qty": qty, "type": 2,  # Market
        "side": s_map.get(side, 1),
        "productType": "MARGIN",
        "limitPrice": 0, "stopPrice": 0,
        "validity": "DAY", "disclosedQty": 0, "offlineOrder": False,
    }
    print(f"[ORDER] Placing {side} {qty} on {symbol}...")
    try:
        return fyers.place_order(data=data)
    except Exception as e:
        return {"s": "error", "message": str(e)}

# ===============================
# Sync Logic (New)
# ===============================
def sync_with_broker(fy):
    """
    Syncs internal TradeState with actual broker positions.
    - If Position in State but NOT in Broker -> Remove from State (Manual Close).
    - If Position in State AND in Broker (diff qty) -> Update State Qty.
    - If Position in Broker but NOT in State -> Ignore.
    """
    if not TRADE_ENABLED: return

    print("[SYNC] Checking broker positions...")
    try:
        resp = fy.positions()
        if resp.get("s") != "ok":
            print(f"[SYNC] Error fetching positions: {resp.get('message')}")
            return

        net_positions = resp.get("netPositions", [])

        # Create a map of broker positions: {symbol: qty}
        # We focus on open positions (netQty != 0)
        broker_map = {}
        for p in net_positions:
            sym = p.get("symbol")
            qty = p.get("netQty", 0)
            if sym:
                broker_map[sym] = int(qty)

        # Iterate over LOCAL state to reconcile
        # We need a list of keys to avoid runtime error during deletion
        local_symbols = list(GLOBAL_TRADE_STATE.positions.keys())

        for index_symbol in local_symbols:
            pos_data = GLOBAL_TRADE_STATE.positions[index_symbol]
            opt_sym = pos_data['opt_sym']
            local_qty = pos_data['qty']

            # Check if this option symbol exists in broker map
            broker_qty = broker_map.get(opt_sym, 0)

            if broker_qty == 0:
                # Case 1: Fully Closed manually
                print(f"[SYNC] Position {opt_sym} missing in broker. Assuming manual close.")
                GLOBAL_TRADE_STATE.close_position(index_symbol, "Manual Close Detected", 0.0)

            elif broker_qty != local_qty:
                # Case 2: Partial Close or Mismatch
                # Note: We assume positive qty for Long. If strategy supports short, check signs.
                # Gamma Blast buys options (Long), so qty > 0.
                print(f"[SYNC] Qty mismatch for {opt_sym}. Local: {local_qty}, Broker: {broker_qty}. Syncing...")
                GLOBAL_TRADE_STATE.update_quantity(index_symbol, broker_qty)

            # Case 3: Match -> Do nothing

    except Exception as e:
        print(f"[SYNC] Exception: {e}")

# ===============================
# Main loop
# ===============================
def main():
    global CANDLE_TIMEFRAME, TRADE_ENABLED, LOT_MULTIPLIER, RISK_REWARD

    # Parse CLI arguments for timeframe
    parser = argparse.ArgumentParser(description="Gamma Blast Monitor with Customizable Timeframe")
    parser.add_argument("--timeframe", "-tf", type=str, default=None,
                        help=f"Candle timeframe in minutes (e.g., 1, 3, 5, 15). Default: {CANDLE_TIMEFRAME}")
    parser.add_argument("--trade", action="store_true", help="Enable Automated Trading")
    parser.add_argument("--lots", type=int, default=1, help="Lot Multiplier (default 1)")
    parser.add_argument("--rr", type=float, default=2.0, help="Risk:Reward Ratio (default 2.0)")

    args = parser.parse_args()

    # Update timeframe if provided via CLI
    if args.timeframe:
        tf_input = args.timeframe.lower().replace("m", "").strip()
        if tf_input.isdigit() and int(tf_input) > 0:
            CANDLE_TIMEFRAME = tf_input
        else:
            print(f"Warning: Invalid timeframe '{args.timeframe}'. Using configured default: {CANDLE_TIMEFRAME}")

    TRADE_ENABLED = args.trade
    LOT_MULTIPLIER = args.lots
    RISK_REWARD = args.rr

    print(f"Starting Gamma Blast Monitor... (Candle Timeframe: {CANDLE_TIMEFRAME}m)")
    if TRADE_ENABLED:
        print(f"*** AUTOMATED TRADING ENABLED (Lots: {LOT_MULTIPLIER}, R:R: 1:{RISK_REWARD}) ***")
        print(f"*** State Persistence: {STATE_FILE} ***")
    else:
        print("Monitoring Mode (Trading Disabled)")

    try:
        fy, app_id, token_prefixed = ensure_valid_token_and_client()
        print(f"\nAccess Token ready for {app_id}.")
    except Exception as e:
        print(f"Login/init error: {e}")
        sys.exit(1)

    # Fetch Master CSVs once
    fetch_symbol_master()

    refresh_interval = INITIAL_REFRESH_INTERVAL
    cycle = 0

    try:
        while True:
            cycle += 1
            print("\n" + "=" * 60)
            print(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  ->  Refresh cycle #{cycle}")
            print("=" * 60)

            # 0) Sync with Broker (if trading)
            if TRADE_ENABLED:
                sync_with_broker(fy)

            # 1) Batched quotes for all symbols
            ltps = get_ltps_batched(fy, SYMBOLS)
            any_429 = False

            # 2) Decide whether to pull heavy optionchain this cycle
            pull_chain = (cycle % OPTIONCHAIN_EVERY == 1)

            for sym in SYMBOLS:
                ltp, lerr = ltps[sym]
                if lerr or ltp is None:
                    if lerr and lerr.get("error_code") == 429:
                        any_429 = True
                    print(f"Skipping {sym}: could not parse LTP.")
                    continue

                # update ROC history
                update_price_history(sym, ltp)

                if pull_chain:
                    res = print_and_save_chain_for_symbol(fy, sym, ltp)
                    if isinstance(res, dict) and res.get("status") in ("ltp_error", "oc_error"):
                        det = res.get("detail") or {}
                        if det.get("error_code") == 429:
                            any_429 = True
                    time.sleep(PER_SYMBOL_DELAY)
                else:
                    # Lightweight line when skipping OC
                    r = roc_bp_last(sym, window_sec=ROC_WINDOW_SEC)
                    print(f"{sym}: LTP {format_ltp(ltp)}  | ROC{ROC_WINDOW_SEC}s={r:.1f}bp")

            # 3) Adaptive throttle if we hit 429 on first cycle
            if any_429 and cycle == 1:
                refresh_interval = max(refresh_interval, 180)
                print(f"DEBUG: 429 on first cycle -> bumping refresh interval to {refresh_interval}s")

            if MAX_CYCLES and cycle >= MAX_CYCLES:
                break

            time.sleep(refresh_interval)

    except KeyboardInterrupt:
        print("\nStop requested. Exiting...")
    except Exception as e:
        print(f"\nFatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
