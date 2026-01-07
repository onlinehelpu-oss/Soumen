# -*- coding: utf-8 -*-
"""
Red-ShootingStar / Red-Pinbar NEXT-candle first-touch breakout (RED candle only)
WITH REAL-TIME OPTION MANAGEMENT AND CORRECT LOT SIZES
INCLUDING BSE:SENSEX-INDEX SUPPORT
WITH CUSTOMIZABLE STRIKE DISTANCE (ITM/ATM/OTM)
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
            return {"s": "ok", "order_id": order_id}

        def quotes(self, payload):
            symbols = payload.get("symbols")
            val = 100.0
            return {"s": "ok", "d": [{"v": {"lp": val, "last_price": val}}]}

        def optionchain(self, payload):
            return {"s": "ok", "data": {"optionsChain": []}}

        def symbol_details(self, payload):
            return {"s": "ok", "d": {payload.get("symbol"): {"lot_size": 65}}}


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


    # point names used later
    fyersModel = type("fyersModel", (), {"FyersModel": MockFyersModel})
    data_ws = type("data_ws", (), {"FyersDataSocket": MockDataSocket})

# ===================== STRATEGY SETTINGS =====================
TIMEFRAME_MIN = 1  # change to 5 / 15 / 30 / 60 etc., or override with --tf
R_MULTIPLIER = 1.0  # default Risk:Reward (1:2)
LOT_MULTIPLIER = 1  # Number of lots to trade
EPS = 1e-6

# ===================== OPTION SETTINGS =====================
# This script now uses user-defined strikes via command-line arguments.
# The STRIKE_DISTANCE constant is no longer used for selection.
STRIKE_DISTANCE = 0  # Deprecated

# ===================== CANDLE GEOMETRY SETTINGS =====================
# UPDATED: More realistic shooting star geometry
UPPER_WICK_MIN = 50  # was 55 (50-80% → Clear rejection but not extreme)
UPPER_WICK_MAX = 80  # was 90
BODY_MIN = 5  # was 5 (5-30% → Small to medium body)
BODY_MAX = 30  # was 20
LOWER_WICK_MAX = 25  # was 12 (0-25% → Permits small lower shadows)

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


def floor_to_tick(x, tick=TICK_SIZE):
    k = math.floor(x / tick)
    return round(k * tick, 2)


# ===================== CONSTANTS & PATHS =====================
CONFIG_FILE = "fyers_login_details.json"
TOKENS_DIR = "AccessToken"
TOKENS_STORE = "tokens_store.json"
TODAY = dt.date.today()
TODAY_PATH = os.path.join(TOKENS_DIR, f"{TODAY}.json")
API_HOST = "https://api-t1.fyers.in"

# ===================== WATCHLIST =====================
SPOT_INDICES = [
    'NSE:NIFTY50-INDEX',
    'NSE:NIFTYBANK-INDEX',
    'BSE:SENSEX-INDEX',
    'NSE:FINNIFTY-INDEX'
]

INDEX_MAP = {
    'NIFTY': 'NSE:NIFTY50-INDEX',
    'BANKNIFTY': 'NSE:NIFTYBANK-INDEX',
    'SENSEX': 'BSE:SENSEX-INDEX',
    'FINNIFTY': 'NSE:FINNIFTY-INDEX'
}


# ===================== OPTION HELPERS =====================
def get_strike_from_index_ltp(index_ltp: float, strike_distance: int = 0) -> float:
    """
    Calculate the appropriate strike price based on index LTP and strike distance.

    For CE selling:
    - Negative distance: ITM strikes (higher than index LTP)
    - Zero distance: ATM strike (closest to index LTP)
    - Positive distance: OTM strikes (lower than index LTP)

    Args:
        index_ltp: Current index LTP
        strike_distance: Number of strikes away from ATM (-ve for ITM, +ve for OTM)

    Returns:
        Calculated strike price
    """
    # Define strike intervals for different indices (in points)
    # NIFTY: 50 points strike interval
    # BANKNIFTY: 100 points strike interval
    # FINNIFTY: 50 points strike interval
    # SENSEX: 100 points strike interval

    # For now, we'll use a default of 50 points for NIFTY/FINNIFTY
    # and 100 points for BANKNIFTY/SENSEX
    # This will be refined based on index name in the main function

    strike_interval = 50  # Default for NIFTY/FINNIFTY

    # Calculate ATM strike (rounded to nearest strike interval)
    atm_strike = round(index_ltp / strike_interval) * strike_interval

    # Adjust based on strike distance
    if strike_distance < 0:
        # ITM: Higher strike for CE selling
        target_strike = atm_strike + (abs(strike_distance) * strike_interval)
    elif strike_distance > 0:
        # OTM: Lower strike for CE selling
        target_strike = atm_strike - (strike_distance * strike_interval)
    else:
        # ATM
        target_strike = atm_strike

    return target_strike


# ===================== REAL-TIME OPTION MANAGER =====================
class RealTimeOptionManager:
    """Manages real-time option selection, lot sizes, and expiry."""

    # Fallback LOT SIZES (in case API fetch fails)
    FALLBACK_LOT_SIZES = {
        "NIFTY": 65,
        "BANKNIFTY": 30,
        "FINNIFTY": 60,
        "SENSEX": 20,
    }

    # Strike intervals for different indices
    STRIKE_INTERVALS = {
        "NIFTY": 50,
        "BANKNIFTY": 100,
        "FINNIFTY": 50,
        "SENSEX": 100,
    }

    def __init__(self, fy):
        self.fy = fy
        self.option_cache = {}
        self.last_refresh = {}
        self.refresh_interval = 300  # Refresh every 5 minutes
        self.lot_cache = {}  # Cache for symbol lot sizes

    def get_lot_size(self, symbol: str, exchange: str) -> int:
        """Fetch real-time lot size from Fyers Symbol Master CSV."""
        if not HAS_FYERS:
            print("⚠️ Mock mode: Using fallback lot size 65")
            return 65

        key = (symbol, exchange)
        if key in self.lot_cache:
            return self.lot_cache[key]

        try:
            if exchange == 'NSE':
                url = 'https://public.fyers.in/sym_details/NSE_FO.csv'
            elif exchange == 'BSE':
                url = 'https://public.fyers.in/sym_details/BSE_FO.csv'
            else:
                print(f"⚠️ Unknown exchange {exchange} for {symbol}, using fallback 65")
                return 65

            print(f"📡 Fetching lot size for {symbol} from {url}...")
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                print(f"⚠️ Failed to fetch master CSV: {resp.status_code}, using fallback")
                lot_size = self.FALLBACK_LOT_SIZES.get('NIFTY', 65)
            else:
                # Read CSV without headers (positional columns)
                df = pd.read_csv(io.StringIO(resp.text), header=None)
                # Symbol is in column 9 (0-indexed), Lot Size in column 3
                symbol_col = 9
                lot_col = 3
                matching_row = df[df.iloc[:, symbol_col] == symbol]
                if not matching_row.empty:
                    lot_size = int(matching_row.iloc[0, lot_col])
                    print(f"✅ Fetched lot size {lot_size} for {symbol}")
                else:
                    print(f"⚠️ Symbol {symbol} not found in master, using fallback")
                    lot_size = self.FALLBACK_LOT_SIZES.get('NIFTY', 65)

            self.lot_cache[key] = lot_size
            return lot_size

        except Exception as e:
            print(f"❌ Error fetching lot size for {symbol}: {e}, using fallback 65")
            return 65

    def get_index_ltp(self, index_symbol: str) -> Optional[float]:
        """Get current LTP for index."""
        try:
            response = self.fy.quotes({"symbols": index_symbol})
            if response.get('s') == 'ok' and response.get('d'):
                return float(response['d'][0]['v']['lp'])
        except Exception as e:
            print(f"❌ Error getting index LTP: {e}")
        return None

    def get_specific_options(self, index_symbol: str, ce_strike: int, pe_strike: int) -> Optional[Tuple[Dict, Dict]]:
        """Finds the nearest-expiry CE and PE options for the given strikes."""
        try:
            print(f"📡 Fetching option chain for {index_symbol}...")
            response = self.fy.optionchain({"symbol": index_symbol})

            if response.get('s') != 'ok':
                print(f" ⚠️ Option chain failed: {response.get('message', 'Unknown error')}")
                return None

            chain_data = response.get('data', {})
            options = chain_data.get('optionsChain', [])
            if not options:
                print(f" ⚠️ No options in chain for {index_symbol}")
                return None

            # Find the nearest expiry date
            expiry_dates = sorted(list(set(opt.get('expiry_date') for opt in options if opt.get('expiry_date'))))
            if not expiry_dates:
                print(" ⚠️ Could not determine expiry dates from option chain.")
                return None
            nearest_expiry = expiry_dates[0]
            print(f"✅ Found nearest expiry: {nearest_expiry}")

            ce_option = None
            pe_option = None

            # Find the matching CE and PE options for the nearest expiry
            for opt in options:
                if opt.get('expiry_date') == nearest_expiry:
                    strike = float(opt.get('strike_price', 0))
                    opt_type = opt.get('option_type', '').upper()

                    if opt_type == 'CE' and math.isclose(strike, ce_strike):
                        ce_option = opt
                    elif opt_type == 'PE' and math.isclose(strike, pe_strike):
                        pe_option = opt

                if ce_option and pe_option:
                    break

            if not ce_option:
                print(f"❌ Could not find CE option for strike {ce_strike} with expiry {nearest_expiry}")
            if not pe_option:
                print(f"❌ Could not find PE option for strike {pe_strike} with expiry {nearest_expiry}")

            if not ce_option or not pe_option:
                return None

            # Now, get full details for both
            ce_details = self._process_option_details(ce_option, index_symbol)
            pe_details = self._process_option_details(pe_option, index_symbol)

            if not ce_details or not pe_details:
                return None

            return ce_details, pe_details

        except Exception as e:
            print(f"❌ Error getting specific options for {index_symbol}: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _process_option_details(self, option_data: Dict, index_symbol: str) -> Optional[Dict]:
        """Helper to process a single option's details from chain data."""
        try:
            symbol = option_data.get('symbol', '')
            strike = float(option_data.get('strike_price', 0))
            ltp = float(option_data.get('ltp', 0))
            volume = int(option_data.get('volume', 0))
            oi = int(option_data.get('oi', 0))

            parsed = self._parse_option_symbol(symbol, strike)
            if not parsed:
                print(f" ⚠️ Could not parse option symbol: {symbol}")
                return None

            lot_size = self.get_lot_size(parsed['full_symbol'], parsed['exchange'])
            expiry_date = self._parse_expiry_date(parsed['expiry_code'])
            expiry_str = expiry_date.strftime('%d-%b-%Y') if expiry_date else 'UNKNOWN'
            index_name = self._get_index_short_name(index_symbol)
            option_type = parsed['option_type']

            return {
                'symbol': symbol,
                'strike': strike,
                'ltp': ltp,
                'volume': volume,
                'oi': oi,
                'lot_size': lot_size,
                'expiry_date': expiry_str,
                'expiry_datetime': expiry_date,
                'index_symbol': index_symbol,
                'index_name': index_name,
                'option_type': option_type,
            }
        except Exception as e:
            print(f"❌ Error processing option details for {option_data.get('symbol')}: {e}")
            return None

    def _parse_option_symbol(self, symbol: str, strike: float) -> Optional[Dict]:
        """Parse Fyers option symbol to extract index name and expiry code."""
        try:
            # Remove exchange prefix
            if symbol.startswith('NSE:'):
                clean_symbol = symbol[4:]  # Remove 'NSE:'
                exchange = 'NSE'
            elif symbol.startswith('BSE:'):
                clean_symbol = symbol[4:]  # Remove 'BSE:'
                exchange = 'BSE'
            else:
                clean_symbol = symbol
                exchange = 'UNKNOWN'

            # Determine option type
            if clean_symbol.endswith('CE'):
                option_type = 'CE'
                base_symbol = clean_symbol[:-2]
            elif clean_symbol.endswith('PE'):
                option_type = 'PE'
                base_symbol = clean_symbol[:-2]
            else:
                print(f" ⚠️ Unknown option type in {symbol}")
                return None

            # Use known strike to slice
            strike_int = int(strike)
            strike_str = str(strike_int)
            if not base_symbol.endswith(strike_str):
                print(
                    f" ⚠️ Strike mismatch in {base_symbol}: expected '{strike_str}', got '{base_symbol[-len(strike_str):]}'")
                return None

            base_without_strike = base_symbol[:-len(strike_str)]

            # Extract index name and expiry code
            index_name = None
            expiry_code = None

            if base_without_strike.startswith('NIFTY') and 'BANKNIFTY' not in base_without_strike:
                index_name = 'NIFTY'
                expiry_part = base_without_strike[5:]
                if len(expiry_part) == 5 and expiry_part.isdigit():
                    expiry_code = expiry_part
                else:
                    expiry_match = re.search(r'\d{5}', expiry_part)
                    if expiry_match:
                        expiry_code = expiry_match.group(0)

            elif base_without_strike.startswith('BANKNIFTY'):
                index_name = 'BANKNIFTY'
                expiry_part = base_without_strike[9:]
                expiry_match = re.search(r'(\d{2}[A-Z]{3}\d{0,2})', expiry_part)
                if expiry_match:
                    expiry_code = expiry_match.group(1)

            elif base_without_strike.startswith('FINNIFTY'):
                index_name = 'FINNIFTY'
                expiry_part = base_without_strike[8:]
                expiry_match = re.search(r'(\d{2}[A-Z]{3}\d{0,2})', expiry_part)
                if expiry_match:
                    expiry_code = expiry_match.group(1)

            elif base_without_strike.startswith('SENSEX'):
                index_name = 'SENSEX'
                expiry_part = base_without_strike[6:]
                if len(expiry_part) == 5 and expiry_part.isdigit():
                    expiry_code = expiry_part
                else:
                    expiry_match = re.search(r'\d{5}', expiry_part)
                    if expiry_match:
                        expiry_code = expiry_match.group(0)

            if not index_name or not expiry_code:
                print(f" ⚠️ Failed to parse: index_name={index_name}, expiry_code={expiry_code}")
                print(f" ⚠️ Base without strike: '{base_without_strike}'")
                return None

            return {
                'index_name': index_name,
                'expiry_code': expiry_code,
                'strike': strike_int,
                'option_type': option_type,
                'full_symbol': symbol,
                'exchange': exchange
            }

        except Exception as e:
            print(f"⚠️ Error parsing option symbol '{symbol}': {e}")
            import traceback
            traceback.print_exc()
            return None

    def _parse_expiry_date(self, expiry_code: str) -> Optional[dt.datetime]:
        """Parse expiry code to datetime."""
        try:
            # Format 1: 26106 (NIFTY) - DDMMY format
            if expiry_code.isdigit() and len(expiry_code) == 5:
                day = int(expiry_code[:2])
                month = int(expiry_code[2:4])
                year_digit = int(expiry_code[4])

                # Handle NIFTY expiry year (single digit: 0=2020, 1=2021, etc.)
                year_map = {
                    0: 2020, 1: 2021, 2: 2022, 3: 2023,
                    4: 2024, 5: 2025, 6: 2026, 7: 2027,
                    8: 2028, 9: 2029
                }

                if year_digit in year_map:
                    year = year_map[year_digit]
                else:
                    # Fallback: assume current decade
                    current_year = dt.datetime.now().year
                    base_year = (current_year // 10) * 10
                    year = base_year + year_digit

                return dt.datetime(year, month, day)

            # Format 2: 26JAN or 26JAN24 (BANKNIFTY/FINNIFTY/SENSEX)
            match = re.match(r'(\d{1,2})([A-Z]{3})(\d{2})?', expiry_code, re.IGNORECASE)
            if match:
                day = int(match.group(1))
                month_str = match.group(2).upper()
                year_str = match.group(3)

                month_dict = {
                    'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4,
                    'MAY': 5, 'JUN': 6, 'JUL': 7, 'AUG': 8,
                    'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12
                }

                month = month_dict.get(month_str, 1)

                if year_str:
                    year = 2000 + int(year_str)
                else:
                    # Find nearest valid date
                    current_date = dt.datetime.now()
                    current_year = current_date.year

                    # Try current year
                    try:
                        expiry_date = dt.datetime(current_year, month, day)
                        if expiry_date < current_date:
                            expiry_date = dt.datetime(current_year + 1, month, day)
                        return expiry_date
                    except ValueError:
                        # Try next year
                        try:
                            return dt.datetime(current_year + 1, month, day)
                        except ValueError:
                            return None

                try:
                    return dt.datetime(year, month, day)
                except ValueError:
                    return None

        except Exception as e:
            print(f"⚠️ Error parsing expiry code '{expiry_code}': {e}")

        return None

    def _get_real_lot_size(self, index_name: str, chain_data: Dict) -> int:
        """Deprecated: Use get_lot_size(symbol, exchange) instead."""
        return 65  # Should not be called

    def _get_index_short_name(self, index_symbol: str) -> str:
        """Get short name for index symbol."""
        if 'NIFTY50' in index_symbol:
            return 'NIFTY'
        elif 'NIFTYBANK' in index_symbol:
            return 'BANKNIFTY'
        elif 'FINNIFTY' in index_symbol:
            return 'FINNIFTY'
        elif 'SENSEX' in index_symbol:
            return 'SENSEX'
        return index_symbol



# ===================== TIME/ENTRY/EXIT RULES =====================
ENTRY_BUFFER = 0.05  # buffer below signal low for breakout
ENTRY_CUTOFF = dt.time(15, 0)  # no new entries after 3:00 PM
EXIT_ALL_TIME = dt.time(15, 9)  # force-exit all open positions at 3:09 PM
FORCE_CLOSED_ALL = False

# ===================== SMALL CANDLE GUARDS =====================
MIN_RANGE_PCT = 0.0015  # ignore if (H-L)/Close < 0.15%
MIN_BODY_TICKS = 0  # optional minimum body size; 0 disables


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
    """
    Ensures a valid Fyers access token is available.
    """
    creds = load_creds()
    client_id = creds["api_key"]
    secret_key = creds["api_secret"]
    redirect_uri = creds["redirect_url"]
    # Check if a token file for today already exists
    if os.path.exists(TODAY_PATH):
        access_token = _read_json(TODAY_PATH)
        if access_token and isinstance(access_token, str):
            print("🔑 Using today's cached access token.")
            return client_id, access_token
    # If today's token doesn't exist, try to use a refresh token
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
            # Clear stored tokens on failure
            if os.path.exists(TOKENS_STORE):
                _write_json(TOKENS_STORE, {})
    # Fallback to interactive login
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


# ===================== CANDLE DETECTOR (Bearish Shooting Star) - UPDATED =====================
def is_bearish_shooting_star_candle(o, h, l, c, prev_o, prev_c, min_range_pct=0.0015):
    """
    RED shooting-star / pin-bar with UPDATED geometry (more realistic):
    - Previous candle GREEN
    - Current candle RED
    - Geometry Constraints (as % of total candle range H-L):
      - Upper Wick: 50% - 80% (UPDATED: was 55-90%)
      - Body: 5% - 30% (UPDATED: was 5-20%)
      - Lower Wick: 0% - 25% (UPDATED: was 0-12%)
    """
    # --- Initial Filters ---
    if c == 0 or h <= l:
        return False
    total_range = h - l
    if (total_range / max(abs(c), 1e-9)) < min_range_pct:
        return False
    if prev_c <= prev_o:  # Previous candle must be green
        return False
    if c >= o:  # Current candle must be red
        return False
    # --- UPDATED Geometric Calculation ---
    upper_wick_pct = ((h - o) / total_range) * 100
    body_pct = ((o - c) / total_range) * 100
    lower_wick_pct = ((c - l) / total_range) * 100
    is_valid_geometry = (
            (UPPER_WICK_MIN <= upper_wick_pct <= UPPER_WICK_MAX) and
            (BODY_MIN <= body_pct <= BODY_MAX) and
            (0 <= lower_wick_pct <= LOWER_WICK_MAX)
    )
    return is_valid_geometry


def flag_bearish_shooting_star(df: pd.DataFrame, min_range_pct=0.0015):
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    prev_o, prev_c = o.shift(1), c.shift(1)
    total_range = h - l
    total_range_safe = total_range.where(total_range > 0, 1e-9)
    upper_wick_pct = ((h - o) / total_range_safe) * 100
    body_pct = ((o - c) / total_range_safe) * 100
    lower_wick_pct = ((c - l) / total_range_safe) * 100
    cond_min_range = (total_range / c.abs().where(c != 0, 1e-9)) >= min_range_pct
    cond_prev_green = prev_c > prev_o
    cond_red = c < o
    cond_geom = (
            (upper_wick_pct >= UPPER_WICK_MIN) & (upper_wick_pct <= UPPER_WICK_MAX) &
            (body_pct >= BODY_MIN) & (body_pct <= BODY_MAX) &
            (lower_wick_pct >= 0) & (lower_wick_pct <= LOWER_WICK_MAX)
    )
    df["BearishShoot"] = cond_min_range & cond_prev_green & cond_red & cond_geom
    return df


# ===================== ORDER HELPERS =====================
def place_order(fy, sym: str, side: int, qty: int, tag: str, dry_run=False):
    # Fix order tag - remove special characters
    clean_tag = re.sub(r'[^a-zA-Z0-9]', '', tag)  # Keep only alphanumeric

    payload = {
        "symbol": sym,
        "qty": int(qty),  # CRITICAL: This must be TOTAL SHARES
        "type": 2,  # market
        "side": int(side),  # 1=buy, -1=sell
        "productType": "INTRADAY",
        "validity": "DAY",
        "orderTag": clean_tag[:15] if clean_tag else ""
    }
    if dry_run:
        print(f"[DRY-RUN] Would place order: {payload}")
        return {"s": "ok", "order_id": "DRYRUN"}
    try:
        resp = fy.place_order(payload)
        print(f"[{dt.datetime.now():%H:%M:%S}] 📌 {tag} {resp}")
        return resp
    except Exception as e:
        print(f"[{dt.datetime.now():%H:%M:%S}] 🚨 Order error {sym} {tag}: {e}")
        return {"s": "error", "message": str(e)}


def exit_short_by_buy_market(fy, sym: str, qty_lots: int, lot_size: int, dry_run=False):
    # to exit a short we BUY market (side=1)
    # Convert lots to shares for Fyers API
    qty_shares = qty_lots * lot_size
    return place_order(fy, sym, side=1, qty=qty_shares, tag="ExitShort", dry_run=dry_run)


# ===================== TRADE LOG & TRACKING =====================
active_trade = {}  # Manages the state of the single combined position


def has_open_positions() -> bool:
    return active_trade.get("status") == "open"


def save_trade(ce_sym, pe_sym, combined_entry, combined_sl, combined_tgt, qty_lots, lot_size):
    global active_trade
    # Log both legs to the CSV
    for sym in [ce_sym, pe_sym]:
        row = {
            "Datetime": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Symbol": sym,
            "Entry Price": "N/A (Combined)",
            "Stop Loss": "N/A (Combined)",
            "Target": "N/A (Combined)",
            "Qty": int(qty_lots),
            "Lot Size": int(lot_size),
            "Total Shares": int(qty_lots * lot_size),
            "Side": "SHORT"
        }
        pd.DataFrame([row]).to_csv(
            "trade_log.csv",
            mode='a',
            header=not os.path.exists("trade_log.csv"),
            index=False
        )
    # Store one entry for the combined position
    active_trade = {
        "ce_symbol": ce_sym,
        "pe_symbol": pe_sym,
        "entry": combined_entry,
        "sl": combined_sl,
        "tgt": combined_tgt,
        "qty_lots": qty_lots,
        "lot_size": lot_size,
        "status": "open",
    }


# ===================== CANDLE BUILD STATE & LTP CACHE =====================
bars = {}
processed_candles = set()
trigger = {}
ltp_cache = {}  # symbol -> (ltp, ts)
prev_ltp_cache = {}  # symbol -> previous ltp (for strict cross)
_last_quote_error = {}
ERROR_THROTTLE_SECS = 10


def candle_start(t: dt.datetime) -> dt.datetime:
    return t.replace(second=0, microsecond=0) - dt.timedelta(minutes=t.minute % TIMEFRAME_MIN)


# ===================== SAFE QUOTES (cache-first, REST fallback) =====================
def get_ltp(fy, sym, cache_ttl=10, max_retries=3):
    now = time.time()
    cached = ltp_cache.get(sym)
    if cached:
        ltp_val, ts = cached
        if (now - ts) <= cache_ttl:
            return float(ltp_val)
    base_sleep = 1.0
    for attempt in range(1, max_retries + 1):
        try:
            q = fy.quotes({"symbols": sym})
            if q.get("s") != "ok" or not q.get("d"):
                last = _last_quote_error.get(sym, 0)
                if now - last > ERROR_THROTTLE_SECS:
                    print(f"⚠️ Quote fetch failed {sym}: {q}")
                    _last_quote_error[sym] = now
                if isinstance(q, dict) and q.get("code") == 429:
                    time.sleep(min(base_sleep * (2 ** attempt), 10))
                    continue
                time.sleep(base_sleep * attempt)
                continue
            v = q["d"][0].get("v", {})
            ltp = v.get("lp") or v.get("last_price")
            if ltp is None:
                last = _last_quote_error.get(sym, 0)
                if now - last > ERROR_THROTTLE_SECS:
                    print(f"⚠️ Quote fetch missing price {sym}: {q}")
                    _last_quote_error[sym] = now
                time.sleep(base_sleep * attempt)
                continue
            ltp_cache[sym] = (float(ltp), time.time())
            return float(ltp)
        except Exception as e:
            last = _last_quote_error.get(sym, 0)
            if now - last > ERROR_THROTTLE_SECS:
                print(f"⚠️ Quote fetch exception {sym}: {e}")
                _last_quote_error[sym] = now
            sleep_s = min(base_sleep * (2 ** (attempt - 1)), 10)
            time.sleep(sleep_s)
            continue
    cached = ltp_cache.get(sym)
    if cached:
        return float(cached[0])
    return None


# ===================== WEBSOCKET HANDLER (Combined Premium) =====================
def make_onmsg(fy, ce_details: Dict, pe_details: Dict, dry_run=False):
    ce_sym = ce_details['symbol']
    pe_sym = pe_details['symbol']
    lot_size = ce_details['lot_size']
    CHART_ID = "COMBINED"  # A unique ID for our virtual chart

    def onmsg(msg):
        if msg.get("type") != "sf":
            return
        try:
            sym = msg["symbol"]
            ltp = float(msg["ltp"])
            ts = int(msg.get("timestamp", time.time()))
        except (KeyError, ValueError):
            return

        # Update LTP cache and track previous LTP for strict cross checks
        prev_ltp = ltp_cache.get(sym, (None, None))[0]
        if prev_ltp is not None:
            prev_ltp_cache[sym] = float(prev_ltp)
        ltp_cache[sym] = (ltp, time.time())

        # Ensure we have LTP for both legs before proceeding
        ce_data = ltp_cache.get(ce_sym)
        pe_data = ltp_cache.get(pe_sym)
        if not (ce_data and pe_data):
            return

        combined_ltp = ce_data[0] + pe_data[0]
        tick_time = dt.datetime.fromtimestamp(ts)
        cstart = candle_start(tick_time)
        key = (CHART_ID, cstart)

        # Build/extend the virtual combined premium bar
        bar = bars.get(key)
        if not bar:
            bars[key] = bar = {"o": combined_ltp, "h": combined_ltp, "l": combined_ltp, "c": combined_ltp}
        else:
            bar["h"] = max(bar["h"], combined_ltp)
            bar["l"] = min(bar["l"], combined_ltp)
            bar["c"] = combined_ltp

        # When the COMBINED candle completes, check for a signal
        if tick_time >= cstart + dt.timedelta(minutes=TIMEFRAME_MIN) - dt.timedelta(seconds=1):
            if key not in processed_candles:
                processed_candles.add(key)
                prev_cstart = cstart - dt.timedelta(minutes=TIMEFRAME_MIN)
                prev_bar = bars.get((CHART_ID, prev_cstart))

                if ONE_POSITION_AT_A_TIME and has_open_positions():
                    return

                if prev_bar and is_bearish_shooting_star_candle(
                        bar["o"], bar["h"], bar["l"], bar["c"],
                        prev_bar["o"], prev_bar["c"],
                        min_range_pct=MIN_RANGE_PCT
                ):
                    next_cstart = cstart + dt.timedelta(minutes=TIMEFRAME_MIN)
                    trigger[CHART_ID] = {
                        "low": bar["l"], "high": bar["h"],
                        "active_start": next_cstart, "triggered": False,
                    }
                    print(
                        f"[{tick_time:%H:%M:%S}] 🎯 COMBINED-SIG TF={TIMEFRAME_MIN}m → Watch LOW {bar['l']:.2f} (SL {bar['h']:.2f})")

        # Check for an active trigger on the COMBINED chart
        t = trigger.get(CHART_ID)
        if not t or t["triggered"]:
            return

        # Expire trigger if its time window has passed
        if tick_time >= t["active_start"] + dt.timedelta(minutes=TIMEFRAME_MIN):
            trigger.pop(CHART_ID, None)
            return

        # Check for entry conditions
        if tick_time >= t["active_start"]:
            if (dt.datetime.now().time() >= ENTRY_CUTOFF) or has_open_positions():
                trigger.pop(CHART_ID, None)
                return

            threshold = round_to_tick(t["low"] - ENTRY_BUFFER)

            # Recalculate previous combined premium for accurate cross check
            ce_prev_ltp, _ = ltp_cache.get(ce_sym, (0, 0))
            pe_prev_ltp, _ = ltp_cache.get(pe_sym, (0, 0))
            prev_combined_ltp = ce_prev_ltp + pe_prev_ltp


            if prev_combined_ltp >= threshold and combined_ltp < threshold:
                print(f"[{tick_time:%H:%M:%S}] 🔥 COMBINED BREAKOUT < {threshold:.2f}. Placing straddle...")
                t["triggered"] = True

                qty_shares = LOT_MULTIPLIER * lot_size
                qty_lots = LOT_MULTIPLIER

                # Risk defined on the COMBINED chart
                entry_price = floor_to_tick(combined_ltp)
                sl_price = t["high"]
                risk = sl_price - entry_price
                if risk <= 0:
                    print(f"[{tick_time:%H:%M:%S}] ✋ Combined Risk <= 0, skipping trade.")
                    trigger.pop(CHART_ID, None)
                    return

                tgt_price = round_to_tick(entry_price - (R_MULTIPLIER * risk))

                # Place synchronized orders
                print(f"[{tick_time:%H:%M:%S}]  Placing SHORT order for {ce_sym}...")
                place_order(fy, ce_sym, side=-1, qty=qty_shares, tag="CombPremCE", dry_run=dry_run)
                print(f"[{tick_time:%H:%M:%S}]  Placing SHORT order for {pe_sym}...")
                place_order(fy, pe_sym, side=-1, qty=qty_shares, tag="CombPremPE", dry_run=dry_run)

                save_trade(ce_sym, pe_sym, entry_price, sl_price, tgt_price, qty_lots, lot_size)
                trigger.pop(CHART_ID, None)
                print(
                    f"[{tick_time:%H:%M:%S}] ✅ STRADDLE SOLD @ Combined {entry_price:.2f}, SL={sl_price:.2f}, TGT={tgt_price:.2f}")

    return onmsg


# ===================== EXIT MONITOR (for Combined Position) =====================
def monitor_loop(fy, dry_run=False):
    global FORCE_CLOSED_ALL, active_trade

    while True:
        try:
            now_dt = dt.datetime.now()
            now_time = now_dt.time()

            # --- Force Exit Logic ---
            if not FORCE_CLOSED_ALL and now_time >= EXIT_ALL_TIME:
                if has_open_positions():
                    print(f"[{now_dt:%H:%M:%S}] ⏳ EXIT_ALL triggered. Closing open position.")
                    ce_sym, pe_sym = active_trade["ce_symbol"], active_trade["pe_symbol"]
                    qty_lots, lot_size = active_trade["qty_lots"], active_trade["lot_size"]

                    exit_short_by_buy_market(fy, ce_sym, qty_lots, lot_size, dry_run=dry_run)
                    exit_short_by_buy_market(fy, pe_sym, qty_lots, lot_size, dry_run=dry_run)

                    active_trade["status"] = "closed_force"
                FORCE_CLOSED_ALL = True

            # --- SL/TGT Monitoring for the Combined Position ---
            if has_open_positions():
                trade = active_trade
                ce_sym, pe_sym = trade["ce_symbol"], trade["pe_symbol"]

                ce_ltp = get_ltp(fy, ce_sym)
                pe_ltp = get_ltp(fy, pe_sym)

                if ce_ltp is not None and pe_ltp is not None:
                    combined_ltp = ce_ltp + pe_ltp
                    sl = trade["sl"]
                    tgt = trade["tgt"]
                    qty_lots, lot_size = trade["qty_lots"], trade["lot_size"]

                    exit_reason = None
                    if combined_ltp >= sl:
                        exit_reason = f"❌ SL HIT @ {combined_ltp:.2f}"
                    elif combined_ltp <= tgt:
                        exit_reason = f"🎯 TARGET HIT @ {combined_ltp:.2f}"

                    if exit_reason:
                        print(f"[{now_dt:%H:%M:%S}] {exit_reason}. Exiting straddle...")
                        active_trade["status"] = "exiting"

                        exit_short_by_buy_market(fy, ce_sym, qty_lots, lot_size, dry_run=dry_run)
                        exit_short_by_buy_market(fy, pe_sym, qty_lots, lot_size, dry_run=dry_run)

                        active_trade["status"] = "closed"

        except Exception as e:
            print(f"⚠️ Monitor loop error: {e}")
        time.sleep(1.5)


# ===================== MAIN =====================
def main():
    global TIMEFRAME_MIN, R_MULTIPLIER, STRIKE_DISTANCE

    parser = argparse.ArgumentParser(description="Combined Premium Shooting Star Strategy")
    parser.add_argument("--tf", type=int, default=TIMEFRAME_MIN, help="Timeframe in minutes")
    parser.add_argument("--rmult", type=float, default=R_MULTIPLIER, help="Risk:Reward multiple")
    parser.add_argument("--index", type=str, required=True, choices=INDEX_MAP.keys(), help="Index to trade")
    parser.add_argument("--ce", type=int, required=True, help="Call strike price")
    parser.add_argument("--pe", type=int, required=True, help="Put strike price")
    parser.add_argument("--dry-run", action="store_true", help="Enable dry-run mode")
    parser.add_argument("--run-tests", action="store_true", help="Run unit tests and exit")

    args = parser.parse_args()
    TIMEFRAME_MIN = max(1, int(args.tf))
    R_MULTIPLIER = float(args.rmult)
    dry_run = args.dry_run or (not HAS_FYERS)

    if args.run_tests:
        run_tests()
        return

    # Get credentials and initialize Fyers
    if HAS_FYERS:
        client_id, access_token = ensure_access_token()
        fy = fyersModel.FyersModel(client_id=client_id, token=access_token, log_path=".")
    else:
        client_id = "MOCK_APP"
        access_token = "MOCK_ACCESS"
        fy = fyersModel.FyersModel(client_id=client_id, token=access_token, log_path=".")

    # Initialize real-time option manager
    option_manager = RealTimeOptionManager(fy)

    print("\n🎯 SETTING UP COMBINED PREMIUM STRATEGY...")
    print("=" * 60)
    print(f"📊 Index: {args.index} | CE Strike: {args.ce} | PE Strike: {args.pe}")
    print(
        f"📊 Candle Geometry: Upper={UPPER_WICK_MIN}-{UPPER_WICK_MAX}%, Body={BODY_MIN}-{BODY_MAX}%, Lower=0-{LOWER_WICK_MAX}%")
    print("=" * 60)

    # Get the specific option pair based on user input
    index_symbol = INDEX_MAP[args.index]
    option_pair = option_manager.get_specific_options(index_symbol, args.ce, args.pe)

    if not option_pair:
        raise SystemExit("❌ Could not fetch details for the specified CE/PE strike pair. Exiting.")

    ce_details, pe_details = option_pair

    # Verify lot sizes are the same, which they should be for the same index/expiry
    if ce_details['lot_size'] != pe_details['lot_size']:
        print(f"⚠️ Warning: Lot sizes differ! CE={ce_details['lot_size']}, PE={pe_details['lot_size']}. Using CE lot size.")

    lot_size = ce_details['lot_size']

    # Store option details in a structured way
    options_data = {
        ce_details['symbol']: ce_details,
        pe_details['symbol']: pe_details
    }

    print("\n✅ OPTIONS TO TRADE:")
    print("=" * 60)
    print(f"📊 CALL OPTION: {ce_details['symbol']}")
    print(f"   LTP: ₹{ce_details['ltp']:.2f} | Strike: {ce_details['strike']:.0f} | Lot Size: {lot_size}")
    print(f"📊 PUT OPTION: {pe_details['symbol']}")
    print(f"   LTP: ₹{pe_details['ltp']:.2f} | Strike: {pe_details['strike']:.0f} | Lot Size: {lot_size}")
    print(f"📊 Initial Combined Premium: ₹{ce_details['ltp'] + pe_details['ltp']:.2f}")

    print("\n" + "=" * 60)

    # WebSocket setup
    on_message = make_onmsg(fy, ce_details, pe_details, dry_run=dry_run)
    option_symbols = list(options_data.keys())
    global ws_connection
    ws_connection = data_ws.FyersDataSocket(
        access_token=f"{client_id}:{access_token}",
        log_path=".",
        litemode=False,
        write_to_file=False,
        reconnect=True,
        on_message=on_message,
        on_error=lambda m: print("🚨", m),
        on_close=lambda m: print("❌", m),
        on_connect=lambda: (
            print(f"🔌 Connected → subscribing to {len(option_symbols)} option contracts.") or
            ws_connection.subscribe(symbols=option_symbols)
        )
    )

    # Start exit monitor
    threading.Thread(target=monitor_loop, args=(fy, dry_run), daemon=True).start()

    print("\n" + "=" * 70)
    print("🎯 COMBINED PREMIUM SHOOTING STAR STRATEGY")
    print("=" * 70)
    print(f"📊 Index: {args.index} | CE: {args.ce} | PE: {args.pe}")
    print(f"📊 DYNAMIC LOT SIZES (fetched from Fyers Symbol Master)")
    print(f"📊 CANDLE GEOMETRY:")
    print(f"   Upper Wick: {UPPER_WICK_MIN}-{UPPER_WICK_MAX}% (Clear rejection)")
    print(f"   Body: {BODY_MIN}-{BODY_MAX}% (Small-medium body)")
    print(f"   Lower Wick: 0-{LOWER_WICK_MAX}% (Small/no lower shadow)")
    print(f"📊 LOT MULTIPLIER: {LOT_MULTIPLIER} lot(s) per trade")
    print("=" * 70)
    print(f"🧩 Python: {sys.version.split()[0]} | Symbols: {len(option_symbols)}")
    print(f"📈 TF={TIMEFRAME_MIN}m | Rmult={R_MULTIPLIER} | Strike Distance={STRIKE_DISTANCE} | dry_run={dry_run}")
    print("=" * 70)
    print("🚀 Real-time SHORT scanner started …\n")
    ws_connection.connect()


# ===================== SIMPLE UNIT TESTS FOR DETECTOR =====================
def run_tests():
    print("Running tests for UPDATED bearish shooting-star detector...")
    # Test 1: Valid shooting star with updated geometry
    # Corrected values to produce: Upper: 60%, Body: 15%, Lower: 25%
    assert is_bearish_shooting_star_candle(98.0, 110.0, 90.0, 95.0, 95.0, 98.0) is True, "Test 1 Failed"
    # Test 2: Upper wick too short (< 50%)
    assert is_bearish_shooting_star_candle(105.0, 109.0, 95.0, 102.0, 100.0, 102.0) is False, "Test 2 Failed"
    # Test 3: Body too large (> 30%)
    assert is_bearish_shooting_star_candle(108.0, 112.0, 98.0, 100.0, 100.0, 102.0) is False, "Test 3 Failed"
    # Test 4: Lower wick too long (> 25%)
    assert is_bearish_shooting_star_candle(104.0, 110.0, 90.0, 96.0, 90.0, 95.0) is False, "Test 4 Failed"
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
