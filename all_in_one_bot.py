# -*- coding: utf-8 -*-
"""
Red-ShootingStar / Red-Pinbar NEXT-candle first-touch breakout (RED candle only)
WITH REAL-TIME OPTION MANAGEMENT AND CORRECT LOT SIZES
INCLUDING BSE:SENSEX-INDEX SUPPORT
WITH CUSTOMIZABLE STRIKE DISTANCE (ITM/ATM/OTM)
UPDATED: More realistic shooting star geometry (50-80% upper wick, 5-30% body, 0-25% lower wick)
WITH FIXED MARGIN SHORTFALL HANDLING
*** SHORT CE VERSION (BEARISH BIAS) ***
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
R_MULTIPLIER = 1.5  # default Risk:Reward (1:2)
LOT_MULTIPLIER = 1  # Number of lots to trade
EPS = 1e-6

# ===================== OPTION SETTINGS =====================
# For CE selling:
# Negative distance = ITM (Strike < Index)
# Positive distance = OTM (Strike > Index)
# -1 = 1 strike ITM, -2 = 2 strikes ITM etc.
#  0 = ATM (At-the-Money)
# +1 = 1 strike OTM etc.
STRIKE_DISTANCE = 0
REGIME_EMA_PERIOD = 50

# ===================== CANDLE GEOMETRY SETTINGS =====================
# UPDATED: More realistic shooting star geometry
UPPER_WICK_MIN = 50  # was 55 (50-80% → Clear rejection but not extreme)
UPPER_WICK_MAX = 80  # was 90
BODY_MIN = 5  # was 5 (5-30% → Small to medium body)
BODY_MAX = 30  # was 20
LOWER_WICK_MAX = 25  # was 12 (0-25% → Permits small lower shadows)

# One-position-at-a-time control
ONE_POSITION_AT_A_TIME = True

# ===================== PRODUCT TYPE SETTINGS =====================
# "MARGIN" for carry-forward options/futures (F&O)
# "CNC" for carry-forward equity (stocks)
# "INTRADAY" for intraday-only trades (squared off same day)
# "MTF" for Margin Trading Facility (equity only)
PRODUCT_TYPE = "MARGIN"

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
ACTIVE_TRADES_FILE = "active_trades.json"
BOT_SETTINGS_FILE = "bot_settings.json"
TOKENS_DIR = "AccessToken"
TOKENS_STORE = "tokens_store.json"
TODAY = dt.date.today()
TODAY_PATH = os.path.join(TOKENS_DIR, f"{TODAY}.json")
API_HOST = "https://api-t1.fyers.in"

# ===================== WATCHLIST =====================
SPOT_INDICES = [
    'NSE:NIFTY50-INDEX',
    'NSE:NIFTYBANK-INDEX',
    'BSE:SENSEX-INDEX'  # Added SENSEX
]


# ===================== OPTION HELPERS =====================
def get_strike_from_index_ltp(index_ltp: float, strike_distance: int = 0) -> float:
    """
    Calculate the appropriate strike price based on index LTP and strike distance.

    For CE selling:
    - Negative distance: ITM strikes (Strike < Index)
    - Zero distance: ATM strike (closest to index LTP)
    - Positive distance: OTM strikes (Strike > Index)

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
        # ITM: Lower strike for CE (Strike < Index)
        target_strike = atm_strike - (abs(strike_distance) * strike_interval)
    elif strike_distance > 0:
        # OTM: Higher strike for CE (Strike > Index)
        target_strike = atm_strike + (strike_distance * strike_interval)
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

    def get_option_details(self, index_symbol: str, strike_distance: int = 0, option_type: str = "CE") -> Optional[
        Dict]:
        """Get real-time option details with correct lot size and expiry."""
        try:
            # Get index LTP
            index_ltp = self.get_index_ltp(index_symbol)
            if not index_ltp:
                print(f" ⚠️ Could not get LTP for {index_symbol}")
                return None

                # Get strike interval based on index
            index_name = self._get_index_short_name(index_symbol)
            strike_interval = self.STRIKE_INTERVALS.get(index_name, 50)

            print(f" 📊 {index_symbol} LTP: {index_ltp:.2f}")
            print(
                f" 📊 Strike Distance: {strike_distance} ({'ITM' if strike_distance < 0 else 'OTM' if strike_distance > 0 else 'ATM'})")
            print(f" 📊 Strike Interval: {strike_interval} points")

            # Calculate target strike based on distance
            if strike_distance != 0:
                # Calculate ATM strike first
                atm_strike = round(index_ltp / strike_interval) * strike_interval

                if strike_distance < 0:
                    # ITM: Lower strike for CE (Strike < Index)
                    target_strike = atm_strike - (abs(strike_distance) * strike_interval)
                    strike_type = "ITM"
                else:
                    # OTM: Higher strike for CE (Strike > Index)
                    target_strike = atm_strike + (strike_distance * strike_interval)
                    strike_type = "OTM"

                print(f" 📊 ATM Strike: {atm_strike:.2f}")
                print(f" 📊 Target {strike_type} Strike: {target_strike:.2f}")
            else:
                # ATM
                target_strike = None
                print(f" 📊 Looking for ATM strike (closest to {index_ltp:.2f})")

                # Get option chain - handle different exchange formats
            if index_symbol.startswith('BSE:'):
                # BSE indices might need different symbol format
                fyers_symbol = index_symbol.replace('BSE:', 'BSE:')  # Keep as is
            else:
                fyers_symbol = index_symbol

            print(f" 📡 Fetching option chain...")
            response = self.fy.optionchain({"symbol": fyers_symbol})

            if response.get('s') != 'ok':
                print(f" ⚠️ Option chain failed: {response.get('message', 'Unknown error')}")
                return None

            chain_data = response.get('data', {})
            options = chain_data.get('optionsChain', [])

            if not options:
                print(f" ⚠️ No options in chain for {index_symbol}")
                return None

                # Filter by option type
            filtered_options = [
                opt for opt in options
                if opt.get('option_type', '').upper() == option_type.upper()
            ]

            if not filtered_options:
                print(f" ⚠️ No {option_type} options found for {index_symbol}")
                return None

            print(f" Found {len(filtered_options)} {option_type} options")

            # Find appropriate option based on strike distance
            selected_option = None

            if strike_distance == 0:
                # Find ATM (closest strike to index LTP)
                min_diff = float('inf')
                for opt in filtered_options:
                    try:
                        strike = float(opt.get('strike_price', 0))
                        diff = abs(strike - index_ltp)

                        if diff < min_diff:
                            min_diff = diff
                            selected_option = opt
                    except:
                        continue
            else:
                # Find strike closest to target_strike
                if target_strike:
                    min_diff = float('inf')
                    for opt in filtered_options:
                        try:
                            strike = float(opt.get('strike_price', 0))
                            diff = abs(strike - target_strike)

                            if diff < min_diff:
                                min_diff = diff
                                selected_option = opt
                        except:
                            continue

            if not selected_option:
                print(f" ⚠️ Could not find appropriate {option_type} option for strike distance {strike_distance}")
                return None

                # Extract details - FIXED VARIABLE NAME
            symbol = selected_option.get('symbol', '')
            strike = float(selected_option.get('strike_price', 0))
            ltp = float(selected_option.get('ltp', 0))
            volume = int(selected_option.get('volume', 0))
            oi = int(selected_option.get('oi', 0))  # FIXED: selected_option, not selectedOption

            # Parse symbol for expiry and index name
            parsed = self._parse_option_symbol(symbol, strike)
            if not parsed:
                print(f" ⚠️ Could not parse option symbol: {symbol}")
                print(f" ⚠️ Raw symbol data: {symbol}")
                # Try direct extraction for debugging
                if 'NIFTY' in symbol and 'BANKNIFTY' not in symbol:
                    print(f" Debug: This appears to be a NIFTY option")
                    # Try to extract manually
                    if '26106' in symbol:
                        print(f" Debug: Found 26106 expiry code in symbol")
                return None

                # Get lot size - DYNAMIC from Symbol Master
            lot_size = self.get_lot_size(parsed['full_symbol'], parsed['exchange'])

            # Get expiry
            expiry_date = self._parse_expiry_date(parsed['expiry_code'])
            expiry_str = expiry_date.strftime('%d-%b-%Y') if expiry_date else 'UNKNOWN'

            # Get index short name
            index_name = self._get_index_short_name(index_symbol)

            # Calculate strike position relative to index LTP
            if strike > index_ltp:
                strike_position = f"ITM (Strike {strike:.2f} > Index {index_ltp:.2f})"
            elif strike < index_ltp:
                strike_position = f"OTM (Strike {strike:.2f} < Index {index_ltp:.2f})"
            else:
                strike_position = "ATM"

            print(f" ✅ Selected: {symbol}")
            print(f" Strike: ₹{strike:.2f} - {strike_position}")
            print(f" LTP: ₹{ltp:.2f}")
            print(f" Lot Size: {lot_size} shares")
            print(f" Expiry: {expiry_str}")
            print(f" Index: {index_name}")
            print(f" Volume: {volume:,}")
            print(f" OI: {oi:,}")

            return {
                'symbol': symbol,
                'strike': strike,
                'ltp': ltp,
                'volume': volume,
                'oi': oi,
                'lot_size': lot_size,
                'index_ltp': index_ltp,
                'expiry_date': expiry_str,
                'expiry_datetime': expiry_date,
                'index_symbol': index_symbol,
                'index_name': index_name,
                'option_type': option_type,
                'distance_to_atm': strike_distance,
                'strike_position': strike_position
            }

        except Exception as e:
            print(f"❌ Error getting option details for {index_symbol}: {e}")
            import traceback
            traceback.print_exc()
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

    def refresh_options(self, indices: List[str], strike_distance: int = 0) -> Dict[str, Dict]:
        """Refresh all option contracts with specified strike distance."""
        print(f"\n🔄 REFRESHING OPTION CONTRACTS...")
        print(
            f"📊 Strike Distance: {strike_distance} ({'ITM' if strike_distance < 0 else 'OTM' if strike_distance > 0 else 'ATM'})")

        options_data = {}

        for index in indices:
            print(f"\n🔍 Refreshing {index}...")
            option_data = self.get_option_details(index, strike_distance, "CE")

            if option_data:
                options_data[option_data['symbol']] = option_data
                self.last_refresh[index] = time.time()
            else:
                print(f" ❌ Failed to get option for {index}")

            time.sleep(1)  # Rate limiting

        return options_data

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


def load_active_trades():
    """Loads active trades from the JSON file."""
    global active_trades
    if os.path.exists(ACTIVE_TRADES_FILE):
        active_trades = _read_json(ACTIVE_TRADES_FILE, {})
        if active_trades:
            print(f"✅ Loaded {len(active_trades)} active trade(s) from '{ACTIVE_TRADES_FILE}'.")


def save_active_trades():
    """Saves the current active_trades dictionary to the JSON file."""
    _write_json(ACTIVE_TRADES_FILE, active_trades)


def load_dynamic_settings():
    """Loads dynamic settings from JSON file and updates globals."""
    global REGIME_EMA_PERIOD, STRIKE_DISTANCE, R_MULTIPLIER

    settings = _read_json(BOT_SETTINGS_FILE, {})
    if not settings:
        return

    updated = False

    if "regime_ema_period" in settings:
        new_val = int(settings["regime_ema_period"])
        if new_val != REGIME_EMA_PERIOD:
            print(f"🔄 Dynamic Setting Update: REGIME_EMA_PERIOD {REGIME_EMA_PERIOD} -> {new_val}")
            REGIME_EMA_PERIOD = new_val
            updated = True

    if "strike_distance" in settings:
        new_val = int(settings["strike_distance"])
        if new_val != STRIKE_DISTANCE:
            print(f"🔄 Dynamic Setting Update: STRIKE_DISTANCE {STRIKE_DISTANCE} -> {new_val}")
            STRIKE_DISTANCE = new_val
            updated = True

    if "r_multiplier" in settings:
        new_val = float(settings["r_multiplier"])
        if new_val != R_MULTIPLIER:
            print(f"🔄 Dynamic Setting Update: R_MULTIPLIER {R_MULTIPLIER} -> {new_val}")
            R_MULTIPLIER = new_val
            updated = True

    return updated

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
    if c >= o:  # Current candle must be red
        return False
    if prev_c <= prev_o:  # Previous candle must be green
        return False
    if c == 0 or h <= l:
        return False
    total_range = h - l
    if (total_range / max(abs(c), 1e-9)) < min_range_pct:
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
    # Define conditions for clarity, starting with the most important ones
    cond_red = c < o
    cond_prev_green = prev_c > prev_o
    cond_min_range = (total_range / c.abs().where(c != 0, 1e-9)) >= min_range_pct
    cond_geom = (
            (upper_wick_pct >= UPPER_WICK_MIN) & (upper_wick_pct <= UPPER_WICK_MAX) &
            (body_pct >= BODY_MIN) & (body_pct <= BODY_MAX) &
            (lower_wick_pct >= 0) & (lower_wick_pct <= LOWER_WICK_MAX)
    )
    df["BearishShoot"] = cond_red & cond_prev_green & cond_min_range & cond_geom
    return df


# ===================== ORDER HELPERS =====================
def place_order(fy, sym: str, side: int, qty: int, tag: str, dry_run=False) -> Dict:
    # Fix order tag - remove special characters
    clean_tag = re.sub(r'[^a-zA-Z0-9]', '', tag)  # Keep only alphanumeric

    payload = {
        "symbol": sym,
        "qty": int(qty),  # CRITICAL: This must be TOTAL SHARES
        "type": 2,  # market
        "side": int(side),  # 1=buy, -1=sell
        "productType": PRODUCT_TYPE,
        "validity": "DAY",
        "orderTag": clean_tag[:15] if clean_tag else ""
    }

    if dry_run:
        print(f"[DRY-RUN] Would place order: {payload}")
        return {"s": "ok", "order_id": "DRYRUN"}

    try:
        resp = fy.place_order(payload)

        # Only print success messages for executed orders
        if resp.get('s') == 'ok' and resp.get('code') == 1101:  # 1101 = Successfully placed order
            print(f"[{dt.datetime.now():%H:%M:%S}] ✅ ORDER EXECUTED {tag}: {sym} {side} {qty} shares")
        elif resp.get('s') == 'error':
            # Check for margin shortfall error
            error_msg = resp.get('message', '')
            if 'Margin Shortfall' in error_msg or 'RED:' in error_msg:
                print(f"[{dt.datetime.now():%H:%M:%S}] ❌ MARGIN SHORTFALL - Order NOT placed: {error_msg}")
            else:
                print(f"[{dt.datetime.now():%H:%M:%S}] ❌ Order error {sym} {tag}: {error_msg}")
        else:
            print(f"[{dt.datetime.now():%H:%M:%S}] 📌 {tag} Response: {resp}")

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
active_trades = {}  # sym -> dict(entry, sl, tgt, qty, status, side, lot_size, order_id)


def has_open_positions() -> bool:
    return any(v.get("status") == "open" for v in active_trades.values())


def save_trade(sym, entry, sl, tgt, qty_lots, side=-1, lot_size=65, order_id=""):
    row = {
        "Datetime": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Symbol": sym,
        "Entry Price": float(entry),
        "Stop Loss": float(sl),
        "Target": float(tgt),
        "Qty": int(qty_lots),  # Number of lots
        "Lot Size": int(lot_size),  # Shares per lot
        "Total Shares": int(qty_lots * lot_size),
        "Side": "SHORT" if side == -1 else "LONG",
        "Order ID": order_id
    }
    pd.DataFrame([row]).to_csv(
        "trade_log.csv",
        mode='a',
        header=not os.path.exists("trade_log.csv"),
        index=False
    )
    active_trades[sym] = {
        "entry": entry,
        "sl": sl,
        "tgt": tgt,
        "qty": qty_lots,
        "status": "open",
        "side": side,
        "lot_size": lot_size,
        "order_id": order_id,
        "order_placed_successfully": True if order_id else False
    }
    save_active_trades()


# ===================== CANDLE BUILD STATE & LTP CACHE =====================
bars = {}
processed_candles = set()
trigger = {}
ltp_cache = {}  # symbol -> (ltp, ts)
prev_ltp_cache = {}  # symbol -> previous ltp (for strict cross)
ema_store = {} # symbol -> current ema value
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


# ===================== WEBSOCKET HANDLER (Option-Direct Signal & Trade) =====================
def make_onmsg(fy, option_manager: RealTimeOptionManager, options_data: Dict, dry_run=False):
    def onmsg(msg):
        if msg.get("type") != "sf":
            return
        try:
            sym = msg["symbol"]  # This is now an OPTION symbol
            ltp = float(msg["ltp"])
            ts = int(msg.get("timestamp", time.time()))
        except Exception:
            return
            # Update option data with latest price
        if sym in options_data:
            options_data[sym]['ltp'] = ltp

            # track prev LTP for strict cross
        prev_ltp = ltp_cache.get(sym, (None, None))[0]
        if prev_ltp is not None:
            prev_ltp_cache[sym] = float(prev_ltp)
            # update websocket LTP cache
        ltp_cache[sym] = (ltp, time.time())
        tick_time = dt.datetime.fromtimestamp(ts)
        cstart = candle_start(tick_time)
        key = (sym, cstart)
        # build/extend the current bar for the OPTION
        bar = bars.get(key)
        if not bar:
            bars[key] = bar = {"o": ltp, "h": ltp, "l": ltp, "c": ltp}
        else:
            bar["h"] = max(bar["h"], ltp)
            bar["l"] = min(bar["l"], ltp)
            bar["c"] = ltp
            # when OPTION candle completes, check for signal
        if tick_time >= cstart + dt.timedelta(minutes=TIMEFRAME_MIN) - dt.timedelta(seconds=1):
            if key not in processed_candles:
                processed_candles.add(key)

                # --- UPDATE REGIME EMA ---
                current_close = bar["c"]
                prev_ema = ema_store.get(sym)
                if prev_ema is None:
                    # Initialize with current close if no history
                    new_ema = current_close
                else:
                    k = 2 / (REGIME_EMA_PERIOD + 1)
                    new_ema = (current_close * k) + (prev_ema * (1 - k))
                ema_store[sym] = new_ema

                prev_cstart = cstart - dt.timedelta(minutes=TIMEFRAME_MIN)
                prev_bar = bars.get((sym, prev_cstart))
                if ONE_POSITION_AT_A_TIME and has_open_positions():
                    return

                # --- CHECK SIGNAL + REGIME EMA ---
                # "if price below Regime Ema , then if signal generate then only signal valid"
                is_below_ema = current_close < new_ema

                if prev_bar and is_bearish_shooting_star_candle(
                        bar["o"], bar["h"], bar["l"], bar["c"],
                        prev_bar["o"], prev_bar["c"],
                        min_range_pct=MIN_RANGE_PCT
                ):
                    # EMA Filter Check
                    if not is_below_ema:
                        print(f"[{tick_time:%H:%M:%S}] ⚠️ Signal on {sym} ignored: Price {current_close:.2f} >= Regime EMA {new_ema:.2f}")
                        return

                    next_cstart = cstart + dt.timedelta(minutes=TIMEFRAME_MIN)
                    # CRITICAL FIX: Get FRESH lot size for THIS symbol
                    if sym.startswith('NSE:'):
                        exchange = 'NSE'
                    elif sym.startswith('BSE:'):
                        exchange = 'BSE'
                    else:
                        exchange = 'UNKNOWN'

                    current_lot_size = option_manager.get_lot_size(sym, exchange)

                    trigger[sym] = {
                        "low": bar["l"],
                        "high": bar["h"],
                        "active_start": next_cstart,
                        "triggered": False,
                        "lot_size": current_lot_size  # Use FRESH lot size
                    }
                    print(
                        f"[{tick_time:%H:%M:%S}] 🎯 OPTION-SIG {sym} TF={TIMEFRAME_MIN}m → watch NEXT LOW {bar['l']:.2f} (SL {bar['h']:.2f})")
                    # Log candle geometry for debugging
                    total_range = bar["h"] - bar["l"]
                    if total_range > 0:
                        upper_pct = ((bar["h"] - bar["o"]) / total_range) * 100
                        body_pct = ((bar["o"] - bar["c"]) / total_range) * 100
                        lower_pct = ((bar["c"] - bar["l"]) / total_range) * 100
                        print(
                            f"[{tick_time:%H:%M:%S}] 📊 Candle Geometry: U={upper_pct:.1f}%, B={body_pct:.1f}%, L={lower_pct:.1f}%")
                        # check active trigger for the OPTION
        t = trigger.get(sym)
        if not t:
            return
            # expire trigger if window passed
        if tick_time >= t["active_start"] + dt.timedelta(minutes=TIMEFRAME_MIN):
            trigger.pop(sym, None)
            return
            # only act in NEXT candle window and if not already triggered
        if tick_time < t["active_start"] or t["triggered"]:
            return
        if ONE_POSITION_AT_A_TIME and has_open_positions():
            print(f"[{dt.datetime.now():%H:%M:%S}] 🚫 Skipping {sym} entry — position already open.")
            trigger.pop(sym, None)
            return
        now_time = dt.datetime.now().time()
        if now_time >= ENTRY_CUTOFF:
            print(f"[{dt.datetime.now():%H:%M:%S}] ⏰ Skipping NEW entry {sym} — cutoff passed ({ENTRY_CUTOFF})")
            trigger.pop(sym, None)
            return
            # breakout condition on the OPTION
        threshold = round_to_tick(t["low"] - ENTRY_BUFFER)
        prev_for_cross = prev_ltp_cache.get(sym)
        if (prev_for_cross is not None) and (prev_for_cross >= threshold) and (ltp < threshold):
            print(f"[{tick_time:%H:%M:%S}] 🔥 OPTION BREAKOUT {sym} < {threshold:.2f}. Placing trade...")
            # 1. Get lot size from trigger (already fresh)
            lot_size = t.get("lot_size", 65)
            if lot_size == 0:
                print(f"[{tick_time:%H:%M:%S}] ✋ Could not determine lot size for {sym}, skipping trade.")
                trigger.pop(sym, None)
                return

                # CRITICAL FIX: qty = TOTAL SHARES (lots × lot_size) for Fyers API
            qty_shares = LOT_MULTIPLIER * lot_size  # e.g., 1 * 20 = 20 shares
            qty_lots = LOT_MULTIPLIER  # Number of lots for logging

            # 2. Define risk based on the OPTION candle's range
            entry_price = floor_to_tick(ltp)
            sl_price = t["high"]
            risk = sl_price - entry_price
            if risk <= 0:
                print(f"[{tick_time:%H:%M:%S}] ✋ Risk <= 0 for {sym}, skipping.")
                trigger.pop(sym, None)
                return
            tgt_price = round_to_tick(entry_price - (R_MULTIPLIER * risk))

            # SAFETY: Ensure Target is at least 0.05
            if tgt_price < 0.05:
                # Check if trade is still viable
                max_possible_reward = entry_price - 0.05
                effective_rr = max_possible_reward / risk if risk > 0 else 0

                if effective_rr < R_MULTIPLIER:
                    print(f"[{tick_time:%H:%M:%S}] ⚠️ Skipping {sym}: Target {tgt_price} implies Max RR {effective_rr:.2f} < Required {R_MULTIPLIER}")
                    trigger.pop(sym, None)
                    return

                # If we are here, we accept the capped target (implied RR matches or exceeds requirement)
                # But wait, if tgt_price < 0.05, entry - 0.05 < entry - tgt_price (which is R * Risk).
                # So effective_rr MUST be < R_MULTIPLIER unless risk is negative.
                # So we should probably ALWAYS skip or strictly clamp and warn.
                # Given user wants "Proper Risk to Reward", we should SKIP.
                print(f"[{tick_time:%H:%M:%S}] ⚠️ Skipping {sym}: Target {tgt_price} < 0.05 (Low Premium). Cannot ensure RR.")
                trigger.pop(sym, None)
                return

            # 3. Place order and save trade for the OPTION
            # Use qty_shares (total shares) for Fyers API
            order_resp = place_order(fy, sym, side=-1, qty=qty_shares, tag="OptRedShootCE", dry_run=dry_run)

            # CRITICAL FIX: Only save trade if order was successful (not margin shortfall)
            if order_resp.get('s') == 'ok' and order_resp.get('code') == 1101:
                # Successful order placement
                order_id = order_resp.get('id', '')
                save_trade(sym, entry_price, sl_price, tgt_price, qty_lots, side=-1, lot_size=lot_size,
                           order_id=order_id)
                t["triggered"] = True
                trigger.pop(sym, None)
                print(
                    f"[{tick_time:%H:%M:%S}] ✅ SHORT-CE {sym} @ {entry_price:.2f}, SL={sl_price:.2f}, TGT={tgt_price:.2f}, QTY={qty_lots} lots ({qty_shares} shares), Lot Size={lot_size}")
            else:
                # Order failed (margin shortfall or other error)
                print(f"[{tick_time:%H:%M:%S}] ❌ Order NOT placed for {sym}, cleaning trigger...")
                trigger.pop(sym, None)

    return onmsg


def sync_with_broker_positions(fy, dry_run=False):
    """Compares Fyers positions with local state and removes manually closed trades."""
    if dry_run or not HAS_FYERS:
        print("[SYNC] Skipping broker sync in dry-run mode.")
        return

    try:
        # print("🔄 Syncing positions with broker...") # Silenced as per user request
        response = fy.positions()
        if response.get('s') != 'ok':
            print(f"⚠️ Broker sync failed: {response.get('message', 'Unknown error')}")
            return

        net_positions = response.get('netPositions', [])
        # Only consider positions with positive net quantity, as closed positions can linger with qty 0
        broker_symbols = {pos['symbol'] for pos in net_positions if
                          pos.get('symbol') and int(pos.get('netQty', 0)) != 0}

        bot_symbols = list(active_trades.keys())
        trades_changed = False

        for symbol in bot_symbols:
            if symbol not in broker_symbols:
                print(f"✅ Position for {symbol} appears to be manually closed. Removing from active monitoring.")
                active_trades.pop(symbol, None)
                trades_changed = True

        if trades_changed:
            save_active_trades()
            print("✅ Active trades file updated after sync.")

    except Exception as e:
        print(f"🚨 Error during broker sync: {e}")


# ===================== EXIT MONITOR (for SHORT positions) with FORCE-EXIT =====================
def monitor_loop(fy, option_manager: RealTimeOptionManager, options_data: Dict, strike_distance: int, dry_run=False):
    global FORCE_CLOSED_ALL, STRIKE_DISTANCE
    last_refresh = time.time()
    refresh_interval = 300  # 5 minutes
    last_sync = time.time()
    sync_interval = 20  # Sync every 20 seconds
    last_settings_check = time.time()
    settings_interval = 5 # Check settings every 5 seconds

    while True:
        try:
            now = time.time()
            now_dt = dt.datetime.now()
            now_time = now_dt.time()

            # Check for dynamic settings updates
            if now - last_settings_check > settings_interval:
                if load_dynamic_settings():
                    # If strike distance changed, force refresh of options
                    if STRIKE_DISTANCE != strike_distance:
                        print(f"⚠️ Strike distance changed from {strike_distance} to {STRIKE_DISTANCE}. Triggering refresh.")
                        last_refresh = 0 # Force refresh immediately
                        strike_distance = STRIKE_DISTANCE # Update local var
                last_settings_check = now

            # Sync with broker positions periodically
            if now - last_sync > sync_interval:
                sync_with_broker_positions(fy, dry_run=dry_run)
                last_sync = now

            # Auto-refresh option contracts every 5 minutes
            if now - last_refresh > refresh_interval:
                print(f"\n🔄 AUTO-REFRESHING OPTION CONTRACTS...")
                # Use global STRIKE_DISTANCE
                current_strike_dist = STRIKE_DISTANCE
                print(
                    f"📊 Strike Distance: {current_strike_dist} ({'ITM' if current_strike_dist < 0 else 'OTM' if current_strike_dist > 0 else 'ATM'})")
                print(
                    f"📊 Candle Geometry: Upper={UPPER_WICK_MIN}-{UPPER_WICK_MAX}%, Body={BODY_MIN}-{BODY_MAX}%, Lower=0-{LOWER_WICK_MAX}%")

                new_options = option_manager.refresh_options(SPOT_INDICES, current_strike_dist)
                if new_options:
                    # Update options data
                    options_data.clear()
                    options_data.update(new_options)

                    # Resubscribe to new symbols if WebSocket is connected
                    global ws_connection
                    if ws_connection:
                        new_symbols = list(new_options.keys())
                        print(f" 🔄 Resubscribing to {len(new_symbols)} symbols")
                        ws_connection.subscribe(symbols=new_symbols)

                last_refresh = now

            # Normal SL/TGT monitoring for open option trades
            if active_trades:
                for sym in list(active_trades.keys()):
                    trade = active_trades.get(sym)
                    if not trade or trade["status"] != "open":
                        continue

                        # CRITICAL FIX: Only monitor if order was actually placed successfully
                    if not trade.get("order_placed_successfully", False):
                        print(f"[{now_dt:%H:%M:%S}] ⚠️ Skipping monitoring for {sym} - order not placed successfully")
                        # Remove from active trades since order wasn't placed
                        active_trades.pop(sym, None)
                        continue

                    ltp = get_ltp(fy, sym)
                    if ltp is None:
                        continue
                    sl = trade["sl"]
                    tgt = trade["tgt"]
                    qty_lots = trade["qty"]  # This is NUMBER OF LOTS
                    lot_size = trade.get("lot_size", 65)
                    side = trade.get("side", -1)
                    # For short trades: SL is above, TGT is below
                    if side == -1:
                        if ltp >= sl:
                            print(
                                f"[{dt.datetime.now():%H:%M:%S}] ❌ SL HIT {sym} @ {ltp:.2f} → BUY market to cover (exit)")
                            active_trades[sym]["status"] = "exiting"
                            # Pass both qty_lots and lot_size
                            exit_short_by_buy_market(fy, sym, qty_lots, lot_size, dry_run=dry_run)
                            active_trades.pop(sym, None)
                            save_active_trades()
                        elif ltp <= tgt:
                            print(
                                f"[{dt.datetime.now():%H:%M:%S}] 🎯 TARGET HIT {sym} @ {ltp:.2f} → BUY market to cover (exit)")
                            active_trades[sym]["status"] = "exiting"
                            # Pass both qty_lots and lot_size
                            exit_short_by_buy_market(fy, sym, qty_lots, lot_size, dry_run=dry_run)
                            active_trades.pop(sym, None)
                            save_active_trades()
        except Exception as e:
            print(f"⚠️ Monitor loop error: {e}")
        time.sleep(1.5)

        # ===================== MAIN =====================


def main():
    global TIMEFRAME_MIN, R_MULTIPLIER, STRIKE_DISTANCE, REGIME_EMA_PERIOD

    parser = argparse.ArgumentParser()
    parser.add_argument("--tf", type=int, default=TIMEFRAME_MIN, help="Timeframe in minutes (e.g., 5, 15, 60)")
    parser.add_argument("--rmult", type=float, default=R_MULTIPLIER,
                        help="Risk:Reward multiple (e.g., 2.0 means target = entry - 2 * risk)")
    parser.add_argument("--strike", type=int, default=STRIKE_DISTANCE,
                        help="Strike distance (-ve for ITM, 0 for ATM, +ve for OTM)")
    parser.add_argument("--regime-ema", type=int, default=REGIME_EMA_PERIOD,
                        help="Period for Regime EMA filter (default 50)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Enable dry-run: simulate orders instead of placing live ones")
    parser.add_argument("--run-tests", action="store_true", help="Run unit tests for detector logic and exit")

    args, _ = parser.parse_known_args()
    TIMEFRAME_MIN = max(1, int(args.tf))
    R_MULTIPLIER = float(args.rmult)
    STRIKE_DISTANCE = int(args.strike)
    REGIME_EMA_PERIOD = int(args.regime_ema)
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

        # Load any existing trades from previous sessions
    load_active_trades()

    # Initialize real-time option manager
    option_manager = RealTimeOptionManager(fy)

    print("\n🎯 BUILDING REAL-TIME OPTION WATCHLIST...")
    print("=" * 60)
    print(
        f"📊 Strike Distance: {STRIKE_DISTANCE} ({'ITM' if STRIKE_DISTANCE < 0 else 'OTM' if STRIKE_DISTANCE > 0 else 'ATM'})")
    print(
        f"📊 Candle Geometry: Upper={UPPER_WICK_MIN}-{UPPER_WICK_MAX}%, Body={BODY_MIN}-{BODY_MAX}%, Lower=0-{LOWER_WICK_MAX}%")
    print("=" * 60)

    # Get initial option contracts with specified strike distance
    options_data = option_manager.refresh_options(SPOT_INDICES, STRIKE_DISTANCE)

    if not options_data:
        print("⚠️ Warning: No option contracts could be found for some indices.")
        print("Continuing with available options...")

    if not options_data:
        raise SystemExit("❌ No option contracts could be found for any indices. Exiting.")

    print("\n✅ FINAL WATCHLIST:")
    print("=" * 60)
    for symbol, data in options_data.items():
        print(f"\n📊 {symbol}")
        print(f" Strike: ₹{data['strike']:.2f} - {data['strike_position']}")
        print(f" LTP: ₹{data['ltp']:.2f}")
        print(f" Lot Size: {data['lot_size']} shares")
        print(f" Expiry: {data['expiry_date']}")
        print(f" Index: {data['index_name']}")
        print(f" Volume: {data['volume']:,}")

    print("\n" + "=" * 60)

    # WebSocket setup
    on_message = make_onmsg(fy, option_manager, options_data, dry_run=dry_run)
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

    # Start exit monitor with strike distance
    threading.Thread(target=monitor_loop, args=(fy, option_manager, options_data, STRIKE_DISTANCE, dry_run),
                     daemon=True).start()

    print("\n" + "=" * 70)
    print("🎯 RED-SHOOTING STAR (CE SELL) STRATEGY - REAL-TIME")
    print("=" * 70)
    print(f"📊 DYNAMIC LOT SIZES (fetched from Fyers Symbol Master)")
    print(f"  (Fallbacks: NIFTY=65, BANKNIFTY=30, FINNIFTY=60, SENSEX=20)")
    print(
        f"📊 STRIKE DISTANCE: {STRIKE_DISTANCE} ({'ITM' if STRIKE_DISTANCE < 0 else 'OTM' if STRIKE_DISTANCE > 0 else 'ATM'})")
    print(f"📊 CANDLE GEOMETRY:")
    print(f"   Upper Wick: {UPPER_WICK_MIN}-{UPPER_WICK_MAX}% (Clear rejection)")
    print(f"   Body: {BODY_MIN}-{BODY_MAX}% (Small-medium body)")
    print(f"   Lower Wick: 0-{LOWER_WICK_MAX}% (Small/no lower shadow)")
    print(f"📊 REGIME EMA: {REGIME_EMA_PERIOD} (Signal valid only if Price < EMA)")
    print(f"📊 LOT MULTIPLIER: {LOT_MULTIPLIER} lot(s) per trade")
    print("=" * 70)
    print(f"🧩 Python: {sys.version.split()[0]} | Symbols: {len(option_symbols)}")
    print(f"📈 TF={TIMEFRAME_MIN}m | Rmult={R_MULTIPLIER} | Strike={STRIKE_DISTANCE} | EMA={REGIME_EMA_PERIOD} | dry_run={dry_run}")
    print("=" * 70)
    print("🚀 Real-time SHORT scanner started …\n")
    ws_connection.connect()


# ===================== SIMPLE UNIT TESTS FOR DETECTOR =====================
def run_tests():
    print("Running tests for UPDATED bearish shooting-star detector...")
    # Test 1: Valid shooting star with updated geometry
    # Upper: ~54.5%, Body: ~20.5%, Lower: 25.0%
    assert is_bearish_shooting_star_candle(100.0, 112.0, 90.0, 95.5, 95.0, 98.0) is True, "Test 1 Failed"
    # Test 2: Upper wick too short (40%)
    assert is_bearish_shooting_star_candle(105.0, 109.0, 95.0, 102.0, 100.0, 102.0) is False, "Test 2 Failed"
    # Test 3: Body too large (40%)
    assert is_bearish_shooting_star_candle(108.0, 112.0, 98.0, 100.0, 100.0, 102.0) is False, "Test 3 Failed"
    # Test 4: Lower wick too long (30%)
    assert is_bearish_shooting_star_candle(108.0, 112.0, 98.0, 101.0, 100.0, 102.0) is False, "Test 4 Failed"
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
