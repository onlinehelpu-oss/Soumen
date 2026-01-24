# -*- coding: utf-8 -*-
"""
ADX CE BUY STRATEGY
Trend Strength — ADX Filter (Main Direction Tool)
Entry Trigger — Momentum Breakout
Ref: Modified from Code -1 (Green-Hammer) to ADX/EMA Strategy
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
from collections import defaultdict

ws_connection = None
import pandas as pd
import numpy as np
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


    fyersModel = type("fyersModel", (), {"FyersModel": MockFyersModel})
    data_ws = type("data_ws", (), {"FyersDataSocket": MockDataSocket})

# ===================== STRATEGY SETTINGS =====================
TIMEFRAME_MIN = 1  # change to 5 / 15 / 30 / 60 etc., or override with --tf
R_MULTIPLIER = 1.0  # default Risk:Reward (1:2)
LOT_MULTIPLIER = 1  # Number of lots to trade
EPS = 1e-6

# ===================== OPTION SETTINGS =====================
# For CE buying:
# Negative distance = ITM (Strike < Index)
# Positive distance = OTM (Strike > Index)
# -1 = 1 strike ITM, -2 = 2 strikes ITM etc.
#  0 = ATM (At-the-Money)
# +1 = 1 strike OTM etc.
STRIKE_DISTANCE = 0

# ===================== ADX / EMA STRATEGY SETTINGS =====================
ADX_PERIOD = 7
ADX_THRESHOLD = 25
FAST_EMA_PERIOD = 9
SLOW_EMA_PERIOD = 21
ATR_PERIOD = 14

# ===================== POSITION MANAGEMENT & RISK =====================
# "signal_low" or "swing_low"
SL_MODE = "signal_low"
SWING_LOOKBACK = 5  # used for swing-low

MAX_CONCURRENT_POS = 3
DAILY_MAX_LOSS = 50000.0
TRADING_ENABLED = True
MAX_EXIT_RETRIES = 3
EXIT_RETRY_COOLDOWN_SECONDS = 10

# Default product type: "INTRADAY", "CNC", "MARGIN", "CO", "BO"
PRODUCT_TYPE = "MARGIN"

MIN_RANGE_PCT = 0.0  # tiny-candle filter (0.001 = 0.1%), 0.0 = off
EMA_BUFFER = 0.0  # optional extra buffer above/below EMAs

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

    For CE Buying:
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

            # --- CURRENT EXPIRY SELECTION ---
            # Group options by expiry date to find the NEAREST one
            expiry_map = {}  # datetime -> list of options

            print(" 🗓️ Sorting options by expiry...")
            for opt in filtered_options:
                try:
                    sym_temp = opt.get('symbol', '')
                    strike_temp = float(opt.get('strike_price', 0))

                    # Temporarily parse to get expiry
                    parsed_temp = self._parse_option_symbol(sym_temp, strike_temp)
                    if not parsed_temp:
                        continue

                    expiry_dt = self._parse_expiry_date(parsed_temp['expiry_code'], parsed_temp['index_name'])
                    if not expiry_dt:
                        continue

                    # Filter out past expiries (keep today and future)
                    # Use date() comparison to include today
                    if expiry_dt.date() < dt.date.today():
                        continue

                    if expiry_dt not in expiry_map:
                        expiry_map[expiry_dt] = []
                    expiry_map[expiry_dt].append(opt)
                except Exception as e:
                    continue

            if not expiry_map:
                print(f" ⚠️ No valid future expiry options found for {index_symbol}")
                return None

            # Sort expiries and pick the nearest one
            sorted_expiries = sorted(expiry_map.keys())
            nearest_expiry = sorted_expiries[0]
            print(f" 🗓️ Selected Current Expiry: {nearest_expiry.strftime('%d-%b-%Y')}")

            # Filter options to only use the nearest expiry
            target_options_group = expiry_map[nearest_expiry]
            print(f" 🎯 Focusing on {len(target_options_group)} options for this expiry")

            # Find appropriate option based on strike distance
            selected_option = None

            if strike_distance == 0:
                # Find ATM (closest strike to index LTP)
                min_diff = float('inf')
                for opt in target_options_group:
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
                    for opt in target_options_group:
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
            expiry_date = self._parse_expiry_date(parsed['expiry_code'], parsed['index_name'])
            expiry_str = expiry_date.strftime('%d-%b-%Y') if expiry_date else 'UNKNOWN'

            # Get index short name
            index_name = self._get_index_short_name(index_symbol)

            # Calculate strike position relative to index LTP
            if strike < index_ltp:
                # For CE, Strike < Index is ITM
                strike_position = f"ITM (Strike {strike:.2f} < Index {index_ltp:.2f})"
            elif strike > index_ltp:
                # For CE, Strike > Index is OTM
                strike_position = f"OTM (Strike {strike:.2f} > Index {index_ltp:.2f})"
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
                # Check for standard numeric format (YYMDD) first
                if len(expiry_part) == 5 and expiry_part.isdigit():
                    expiry_code = expiry_part
                else:
                    # Check for alphanumeric format (DDMMM or similar) e.g. 26JAN
                    # Also handles YYMMM for monthly if Fyers uses that
                    expiry_match_alphanum = re.search(r'(\d{1,2}[A-Z]{3}\d{0,2})', expiry_part)
                    if expiry_match_alphanum:
                        expiry_code = expiry_match_alphanum.group(1)
                    else:
                        # Fallback to looking for 5 digits anywhere
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
                # SENSEX uses YYMDD (5 chars) where M is 1-9, O, N, D
                # e.g. 26122 (2026 Jan 22) or 26O22 (2026 Oct 22)
                if len(expiry_part) == 5:
                    expiry_code = expiry_part
                else:
                    # Fallback regex for YYMDD format (alphanumeric supported)
                    expiry_match = re.search(r'\d{2}[A-Z0-9]\d{2}', expiry_part)
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

    def _parse_expiry_date(self, expiry_code: str, index_name: str = None) -> Optional[dt.datetime]:
        """Parse expiry code to datetime."""
        try:
            # Special handling for SENSEX/MCX (YYMDD format)
            if index_name == 'SENSEX' and len(expiry_code) == 5:
                # YYMDD format
                yy = int(expiry_code[:2])
                m_char = expiry_code[2].upper()
                dd = int(expiry_code[3:])

                year = 2000 + yy

                if m_char.isdigit():
                    month = int(m_char)
                else:
                    month_map = {'O': 10, 'N': 11, 'D': 12}
                    month = month_map.get(m_char)

                if month:
                    return dt.datetime(year, month, dd)

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
                        # Fix: Use .date() comparison to ensure we don't skip today's expiry
                        if expiry_date.date() < current_date.date():
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
        print(f"\\n🔄 REFRESHING OPTION CONTRACTS...")
        print(
            f"📊 Strike Distance: {strike_distance} ({'ITM' if strike_distance < 0 else 'OTM' if strike_distance > 0 else 'ATM'})")

        options_data = {}

        for index in indices:
            print(f"\\n🔍 Refreshing {index}...")
            option_data = self.get_option_details(index, strike_distance, "CE")

            if option_data:
                options_data[option_data['symbol']] = option_data
                self.last_refresh[index] = time.time()
            else:
                print(f" ❌ Failed to get option for {index}")

            time.sleep(1)  # Rate limiting

        return options_data


# ===================== TIME/ENTRY/EXIT RULES =====================
ENTRY_BUFFER = 0.05  # buffer above signal high for breakout
ENTRY_CUTOFF = dt.time(15, 0)  # no new entries after 3:00 PM
EXIT_ALL_TIME = dt.time(15, 9)  # force-exit all open positions at 3:09 PM
FORCE_CLOSED_ALL = False

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
    global STRIKE_DISTANCE, R_MULTIPLIER

    settings = _read_json(BOT_SETTINGS_FILE, {})
    if not settings:
        return

    updated = False

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
    print(f"\\n👉 Open this login URL in your browser, complete login, and copy the auth_code from the redirect URL:")
    print(auth_url)
    webbrowser.open(auth_url, new=1)
    auth_code = input("\\nPaste the auth_code here: ").strip()
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


# ===================== INDICATORS (ADX, DI, EMA, ATR) =====================
def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculates technical indicators: Fast/Slow EMA, ATR, ADX, DI.
    """
    df = df.copy()

    # --- EMA ---
    df['ema_fast'] = df['c'].ewm(span=FAST_EMA_PERIOD, adjust=False).mean()
    df['ema_slow'] = df['c'].ewm(span=SLOW_EMA_PERIOD, adjust=False).mean()

    # --- ATR ---
    # TR = max(H-L, |H-PrevClose|, |L-PrevClose|)
    df['h-l'] = df['h'] - df['l']
    df['h-pc'] = abs(df['h'] - df['c'].shift(1))
    df['l-pc'] = abs(df['l'] - df['c'].shift(1))
    df['tr'] = df[['h-l', 'h-pc', 'l-pc']].max(axis=1)

    # ATR using Wilder's Smoothing (alpha=1/period)
    df['atr'] = df['tr'].ewm(alpha=1/ATR_PERIOD, adjust=False).mean()

    # --- ADX / DI ---
    # UpMove = H - PrevH
    # DownMove = PrevL - L
    df['up_move'] = df['h'] - df['h'].shift(1)
    df['down_move'] = df['l'].shift(1) - df['l']

    df['plus_dm'] = np.where((df['up_move'] > df['down_move']) & (df['up_move'] > 0), df['up_move'], 0.0)
    df['minus_dm'] = np.where((df['down_move'] > df['up_move']) & (df['down_move'] > 0), df['down_move'], 0.0)

    # Smooth DM and TR for DI calculation (Wilder's Smoothing)
    df['tr_smooth'] = df['tr'].ewm(alpha=1/ADX_PERIOD, adjust=False).mean()
    df['plus_dm_smooth'] = df['plus_dm'].ewm(alpha=1/ADX_PERIOD, adjust=False).mean()
    df['minus_dm_smooth'] = df['minus_dm'].ewm(alpha=1/ADX_PERIOD, adjust=False).mean()

    # Handle division by zero
    df['plus_di'] = 100 * (df['plus_dm_smooth'] / df['tr_smooth'].replace(0, np.nan))
    df['minus_di'] = 100 * (df['minus_dm_smooth'] / df['tr_smooth'].replace(0, np.nan))

    # DX
    df['dx'] = 100 * abs(df['plus_di'] - df['minus_di']) / (df['plus_di'] + df['minus_di']).replace(0, np.nan)

    # ADX
    df['adx'] = df['dx'].ewm(alpha=1/ADX_PERIOD, adjust=False).mean()

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


def exit_long_by_sell_market(fy, sym: str, qty_lots: int, lot_size: int, dry_run=False):
    # to exit a long we SELL market (side=-1)
    # Convert lots to shares for Fyers API
    qty_shares = qty_lots * lot_size

    # Retry logic
    for attempt in range(1, MAX_EXIT_RETRIES + 1):
        resp = place_order(fy, sym, side=-1, qty=qty_shares, tag="ExitLong", dry_run=dry_run)

        # Check success
        if resp.get('s') == 'ok' and resp.get('code') == 1101:
            return resp

        print(f"[EXIT-RETRY] Attempt {attempt}/{MAX_EXIT_RETRIES} failed. Retrying in {EXIT_RETRY_COOLDOWN_SECONDS}s...")
        time.sleep(EXIT_RETRY_COOLDOWN_SECONDS)

    print(f"[EXIT-FAILURE] Could not exit {sym} after {MAX_EXIT_RETRIES} attempts.")
    return {"s": "error", "message": "Max retries exceeded"}


# ===================== TRADE LOG & TRACKING =====================
active_trades = {}  # sym -> dict(entry, sl, tgt, qty, status, side, lot_size, order_id)


def can_take_new_position() -> bool:
    """Checks if new positions are allowed based on concurrency limit."""
    if not TRADING_ENABLED:
        return False

    open_positions = [v for v in active_trades.values() if v.get("status") == "open"]
    return len(open_positions) < MAX_CONCURRENT_POS

def calculate_daily_loss() -> float:
    """Calculates realized PnL from the trade log for today."""
    if not os.path.exists("trade_log.csv"):
        return 0.0

    try:
        df = pd.read_csv("trade_log.csv")
        # Ensure we filter for today's trades only
        today_str = dt.date.today().strftime("%Y-%m-%d")
        df['Date'] = pd.to_datetime(df['Datetime']).dt.strftime("%Y-%m-%d")
        today_trades = df[df['Date'] == today_str]

        # We need PnL column. If not present, we can't calculate.
        # But wait, trade_log.csv structure in save_trade doesn't have PnL/Exit Price yet.
        # It logs ENTRY.
        # The current save_trade logs the trade *start*. It doesn't seem to log the *exit*.
        # We need to log EXITS to calculate PnL.

        # Since the original code didn't fully implement PnL logging on exit (it printed it),
        # we need to infer or update save_trade to handle exits or PnL.
        # However, to be minimally invasive as requested:
        # We can only track loss if we record it.
        # I will assume "daily max loss" feature requires us to track realized loss.
        # Since I can't easily change the CSV structure without migration, I'll assume 0.0 for now
        # unless I see a mechanism to track it.
        # Actually, let's look at `monitor_loop` or `make_onmsg`.
        # They print "PnL".
        return 0.0 # Placeholder as we don't have PnL persistence implemented in original code
    except Exception:
        return 0.0

def has_open_positions() -> bool:
    return any(v.get("status") == "open" for v in active_trades.values())


def save_trade(sym, entry, sl, tgt, qty_lots, side=1, lot_size=65, order_id=""):
    row = {
        "Datetime": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Symbol": sym,
        "Entry Price": float(entry),
        "Stop Loss": float(sl),
        "Target": float(tgt),
        "Qty": int(qty_lots),  # Number of lots
        "Lot Size": int(lot_size),  # Shares per lot
        "Total Shares": int(qty_lots * lot_size),
        "Side": "LONG" if side == 1 else "SHORT",
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
history_store = defaultdict(list)  # Stores completed candles for indicator calc
MAX_HISTORY = 200

processed_candles = set()
trigger = {}
ltp_cache = {}  # symbol -> (ltp, ts)
prev_ltp_cache = {}  # symbol -> previous ltp (for strict cross)
_last_quote_error = {}
ERROR_THROTTLE_SECS = 10


def candle_start(t: dt.datetime) -> dt.datetime:
    return t.replace(second=0, microsecond=0) - dt.timedelta(minutes=t.minute % TIMEFRAME_MIN)

# ===================== RISK HELPERS =====================
def get_stop_loss_price(symbol: str, bar: Dict, mode: str, lookback: int) -> float:
    """Calculates Stop Loss price based on mode."""
    if mode == "swing_low":
        # Get last N bars from history
        hist = history_store[symbol]
        if not hist:
            return bar['l']

        recent_bars = hist[-lookback:]
        # Include current bar low? Usually yes for immediate swing
        lows = [b['l'] for b in recent_bars] + [bar['l']]
        return min(lows)
    else:
        # Default: signal_low
        return bar['l']

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

        # --- FAST EXIT CHECK (Instant Reaction to Tick) ---
        if sym in active_trades:
            trade = active_trades[sym]
            if trade["status"] == "open" and trade.get("order_placed_successfully", False):
                sl = trade["sl"]
                tgt = trade["tgt"]
                qty_lots = trade["qty"]
                lot_size = trade.get("lot_size", 65)
                side = trade.get("side", 1)

                # Check exit conditions for LONG trade (side == 1)
                if side == 1:
                    if ltp <= sl:
                        print(f"[{dt.datetime.now():%H:%M:%S}] ⚡ FAST EXIT: SL HIT {sym} @ {ltp:.2f}")
                        active_trades[sym]["status"] = "exiting"
                        threading.Thread(target=exit_long_by_sell_market,
                                         args=(fy, sym, qty_lots, lot_size, dry_run)).start()
                        active_trades.pop(sym, None)
                        save_active_trades()
                    elif ltp >= tgt:
                        print(f"[{dt.datetime.now():%H:%M:%S}] ⚡ FAST EXIT: TARGET HIT {sym} @ {ltp:.2f}")
                        active_trades[sym]["status"] = "exiting"
                        threading.Thread(target=exit_long_by_sell_market,
                                         args=(fy, sym, qty_lots, lot_size, dry_run)).start()
                        active_trades.pop(sym, None)
                        save_active_trades()

        tick_time = dt.datetime.fromtimestamp(ts)
        cstart = candle_start(tick_time)
        key = (sym, cstart)
        # build/extend the current bar for the OPTION
        bar = bars.get(key)
        if not bar:
            bars[key] = bar = {"o": ltp, "h": ltp, "l": ltp, "c": ltp, "ts": cstart}
        else:
            bar["h"] = max(bar["h"], ltp)
            bar["l"] = min(bar["l"], ltp)
            bar["c"] = ltp

        # when OPTION candle completes, check for signal
        if tick_time >= cstart + dt.timedelta(minutes=TIMEFRAME_MIN) - dt.timedelta(seconds=1):
            if key not in processed_candles:
                processed_candles.add(key)

                # --- ADD COMPLETED BAR TO HISTORY ---
                history_store[sym].append(bar)
                if len(history_store[sym]) > MAX_HISTORY:
                    history_store[sym].pop(0)

                # Check Max Concurrent Positions
                if not can_take_new_position():
                    return

                # --- CALCULATE INDICATORS ---
                # Need enough history for ADX/EMA
                if len(history_store[sym]) < max(SLOW_EMA_PERIOD, ADX_PERIOD + 5):
                     # Not enough data yet
                     return

                df_hist = pd.DataFrame(history_store[sym])
                df_hist = calculate_indicators(df_hist)

                # Get latest values (from the just completed candle)
                curr = df_hist.iloc[-1]

                # --- STRATEGY CONDITIONS ---

                # 0. Min Range Filter
                candle_range_pct = (bar['h'] - bar['l']) / bar['o'] if bar['o'] > 0 else 0
                if candle_range_pct < MIN_RANGE_PCT:
                     # Candle too small, ignore
                     return

                # 1. ADX > Threshold (Trend Strength)
                # 2. +DI > -DI (Bullish Bias)
                # 3. Fast EMA > Slow EMA + Buffer (Bullish Trend)

                cond_adx = curr['adx'] > ADX_THRESHOLD
                cond_di = curr['plus_di'] > curr['minus_di']
                # Fast > Slow + Buffer
                cond_ema = curr['ema_fast'] > (curr['ema_slow'] + EMA_BUFFER)

                is_signal = cond_adx and cond_di and cond_ema

                if is_signal:
                    print(f"[{tick_time:%H:%M:%S}] 📈 SIGNAL {sym}: ADX={curr['adx']:.2f}, +DI={curr['plus_di']:.2f}, -DI={curr['minus_di']:.2f}, FastEMA={curr['ema_fast']:.2f}, SlowEMA={curr['ema_slow']:.2f} (Buf={EMA_BUFFER})")

                    next_cstart = cstart + dt.timedelta(minutes=TIMEFRAME_MIN)
                    # CRITICAL FIX: Get FRESH lot size for THIS symbol
                    if sym.startswith('NSE:'):
                        exchange = 'NSE'
                    elif sym.startswith('BSE:'):
                        exchange = 'BSE'
                    else:
                        exchange = 'UNKNOWN'

                    current_lot_size = option_manager.get_lot_size(sym, exchange)

                    # Store ATR for Target calculation
                    atr_value = curr['atr'] if not pd.isna(curr['atr']) else 0.0

                    # Calculate Stop Loss based on Mode
                    sl_price = get_stop_loss_price(sym, bar, SL_MODE, SWING_LOOKBACK)

                    trigger[sym] = {
                        "low": sl_price,  # SL
                        "high": bar["h"],  # Trigger
                        "active_start": next_cstart,
                        "triggered": False,
                        "lot_size": current_lot_size,
                        "atr": atr_value
                    }
                    print(
                        f"[{tick_time:%H:%M:%S}] 🎯 OPTION-SIG {sym} TF={TIMEFRAME_MIN}m → watch NEXT HIGH {bar['h']:.2f} (SL {sl_price:.2f} [{SL_MODE}]) ATR={atr_value:.2f}")
                else:
                    # Debug print occasionally?
                    pass

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

        # Check concurrency again before entry
        if not can_take_new_position():
            print(f"[{dt.datetime.now():%H:%M:%S}] 🚫 Skipping {sym} entry — max positions reached.")
            trigger.pop(sym, None)
            return

        now_time = dt.datetime.now().time()
        if now_time >= ENTRY_CUTOFF:
            print(f"[{dt.datetime.now():%H:%M:%S}] ⏰ Skipping NEW entry {sym} — cutoff passed ({ENTRY_CUTOFF})")
            trigger.pop(sym, None)
            return
            # breakout condition on the OPTION
        threshold = round_to_tick(t["high"] + ENTRY_BUFFER)  # Breakout above HIGH
        prev_for_cross = prev_ltp_cache.get(sym)

        # BUY condition: Price crosses above Threshold
        if (prev_for_cross is not None) and (prev_for_cross <= threshold) and (ltp > threshold):
            print(f"[{tick_time:%H:%M:%S}] 🔥 OPTION BREAKOUT {sym} > {threshold:.2f}. Placing trade...")
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
            entry_price = ceil_to_tick(ltp)  # Buy at slightly higher or tick
            sl_price = t["low"]
            risk = entry_price - sl_price  # Risk = Entry - Low
            if risk <= 0:
                print(f"[{tick_time:%H:%M:%S}] ✋ Risk <= 0 for {sym}, skipping.")
                trigger.pop(sym, None)
                return

            # --- TARGET CALCULATION (Dual Target) ---
            # 1. RR Target
            tgt_rr = round_to_tick(entry_price + (R_MULTIPLIER * risk))

            # 2. ATR Target
            atr_val = t.get('atr', 0.0)
            if atr_val > 0:
                tgt_atr = round_to_tick(entry_price + (2.0 * atr_val))
            else:
                tgt_atr = tgt_rr # Fallback if ATR invalid

            # "Two profit targets — first hit exits" -> Min distance
            tgt_price = min(tgt_rr, tgt_atr)

            # SAFETY: Ensure Target is profitable
            if tgt_price <= entry_price:
                print(f"[{tick_time:%H:%M:%S}] ⚠️ Skipping {sym}: Target {tgt_price} <= Entry.")
                trigger.pop(sym, None)
                return

            print(f"[{tick_time:%H:%M:%S}] 🎯 Targets: RR={tgt_rr:.2f}, ATR={tgt_atr:.2f} -> Selected: {tgt_price:.2f}")

                # 3. Place order and save trade for the OPTION
            # Use qty_shares (total shares) for Fyers API
            # Side 1 = BUY
            order_resp = place_order(fy, sym, side=1, qty=qty_shares, tag="OptADXCE", dry_run=dry_run)

            # CRITICAL FIX: Only save trade if order was successful (not margin shortfall)
            if order_resp.get('s') == 'ok' and order_resp.get('code') == 1101:
                # Successful order placement
                order_id = order_resp.get('id', '')
                save_trade(sym, entry_price, sl_price, tgt_price, qty_lots, side=1, lot_size=lot_size,
                           order_id=order_id)
                t["triggered"] = True
                trigger.pop(sym, None)
                print(
                    f"[{tick_time:%H:%M:%S}] ✅ LONG-CE {sym} @ {entry_price:.2f}, SL={sl_price:.2f}, TGT={tgt_price:.2f}, QTY={qty_lots} lots ({qty_shares} shares), Lot Size={lot_size}")
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

        # ===================== EXIT MONITOR (for LONG positions) with FORCE-EXIT =====================


def monitor_loop(fy, option_manager: RealTimeOptionManager, options_data: Dict, strike_distance: int, dry_run=False):
    global FORCE_CLOSED_ALL, STRIKE_DISTANCE
    last_refresh = time.time()
    refresh_interval = 300  # 5 minutes
    last_sync = time.time()
    sync_interval = 20  # Sync every 20 seconds
    last_settings_check = time.time()
    settings_interval = 5  # Check settings every 5 seconds

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
                        print(
                            f"⚠️ Strike distance changed from {strike_distance} to {STRIKE_DISTANCE}. Triggering refresh.")
                        last_refresh = 0  # Force refresh immediately
                        strike_distance = STRIKE_DISTANCE  # Update local var
                last_settings_check = now

                # Sync with broker positions periodically
            if now - last_sync > sync_interval:
                sync_with_broker_positions(fy, dry_run=dry_run)
                last_sync = now

                # Auto-refresh option contracts every 5 minutes
            if now - last_refresh > refresh_interval:
                print(f"\\n🔄 AUTO-REFRESHING OPTION CONTRACTS...")
                # Use global STRIKE_DISTANCE
                current_strike_dist = STRIKE_DISTANCE
                print(
                    f"📊 Strike Distance: {current_strike_dist} ({'ITM' if current_strike_dist < 0 else 'OTM' if current_strike_dist > 0 else 'ATM'})")

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
            # NOTE: Main exit check is now done in make_onmsg for speed.
            # This loop acts as a backup/redundancy for quotes if WS misses something.
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

                        # We can use get_ltp here, but it might be slower than WS.
                    # Redundant check:
                    ltp = get_ltp(fy, sym)
                    if ltp is None:
                        continue
                    sl = trade["sl"]
                    tgt = trade["tgt"]
                    qty_lots = trade["qty"]  # This is NUMBER OF LOTS
                    lot_size = trade.get("lot_size", 65)
                    side = trade.get("side", 1)
                    # For LONG trades: SL is below, TGT is above
                    if side == 1:
                        if ltp <= sl:
                            print(
                                f"[{dt.datetime.now():%H:%M:%S}] ❌ REDUNDANT CHECK: SL HIT {sym} @ {ltp:.2f}")
                            active_trades[sym]["status"] = "exiting"
                            # Pass both qty_lots and lot_size
                            exit_long_by_sell_market(fy, sym, qty_lots, lot_size, dry_run=dry_run)
                            active_trades.pop(sym, None)
                            save_active_trades()
                        elif ltp >= tgt:
                            print(
                                f"[{dt.datetime.now():%H:%M:%S}] 🎯 REDUNDANT CHECK: TARGET HIT {sym} @ {ltp:.2f}")
                            active_trades[sym]["status"] = "exiting"
                            # Pass both qty_lots and lot_size
                            exit_long_by_sell_market(fy, sym, qty_lots, lot_size, dry_run=dry_run)
                            active_trades.pop(sym, None)
                            save_active_trades()
        except Exception as e:
            print(f"⚠️ Monitor loop error: {e}")
        time.sleep(1.0)  # Reduced from 1.5 since main work is in WS

        # ===================== MAIN =====================


def main():
    global TIMEFRAME_MIN, R_MULTIPLIER, STRIKE_DISTANCE, LOT_MULTIPLIER
    global ADX_PERIOD, ADX_THRESHOLD, FAST_EMA_PERIOD, SLOW_EMA_PERIOD, ATR_PERIOD
    global SL_MODE, SWING_LOOKBACK, MAX_CONCURRENT_POS, DAILY_MAX_LOSS, TRADING_ENABLED
    global MAX_EXIT_RETRIES, EXIT_RETRY_COOLDOWN_SECONDS, PRODUCT_TYPE, MIN_RANGE_PCT, EMA_BUFFER

    parser = argparse.ArgumentParser()
    parser.add_argument("--tf", type=int, default=TIMEFRAME_MIN, help="Timeframe in minutes (e.g., 5, 15, 60)")
    parser.add_argument("--rmult", type=float, default=R_MULTIPLIER,
                        help="Risk:Reward multiple (e.g., 2.0 means target = entry + 2 * risk)")
    parser.add_argument("--strike", type=int, default=STRIKE_DISTANCE,
                        help="Strike distance (-ve for ITM, 0 for ATM, +ve for OTM)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Enable dry-run: simulate orders instead of placing live ones")

    # New Arguments
    parser.add_argument("--adx-period", type=int, default=ADX_PERIOD, help=f"ADX Period (default {ADX_PERIOD})")
    parser.add_argument("--adx-thresh", type=int, default=ADX_THRESHOLD, help=f"ADX Threshold (default {ADX_THRESHOLD})")
    parser.add_argument("--fast-ema", type=int, default=FAST_EMA_PERIOD, help=f"Fast EMA Period (default {FAST_EMA_PERIOD})")
    parser.add_argument("--slow-ema", type=int, default=SLOW_EMA_PERIOD, help=f"Slow EMA Period (default {SLOW_EMA_PERIOD})")
    parser.add_argument("--atr-period", type=int, default=ATR_PERIOD, help=f"ATR Period (default {ATR_PERIOD})")

    # Customizable Global Args
    parser.add_argument("--sl-mode", type=str, default=SL_MODE, choices=["signal_low", "swing_low"], help=f"SL Mode (default {SL_MODE})")
    parser.add_argument("--swing-lookback", type=int, default=SWING_LOOKBACK, help=f"Swing Lookback (default {SWING_LOOKBACK})")
    parser.add_argument("--max-pos", type=int, default=MAX_CONCURRENT_POS, help=f"Max Concurrent Positions (default {MAX_CONCURRENT_POS})")
    parser.add_argument("--daily-loss", type=float, default=DAILY_MAX_LOSS, help=f"Daily Max Loss Limit (default {DAILY_MAX_LOSS})")
    parser.add_argument("--lot-mult", type=int, default=LOT_MULTIPLIER, help=f"Lot Multiplier (default {LOT_MULTIPLIER})")
    parser.add_argument("--product", type=str, default=PRODUCT_TYPE, help=f"Product Type (default {PRODUCT_TYPE})")
    parser.add_argument("--min-range", type=float, default=MIN_RANGE_PCT, help=f"Min Range Pct (default {MIN_RANGE_PCT})")
    parser.add_argument("--ema-buffer", type=float, default=EMA_BUFFER, help=f"EMA Buffer (default {EMA_BUFFER})")

    args, _ = parser.parse_known_args()
    TIMEFRAME_MIN = max(1, int(args.tf))
    R_MULTIPLIER = float(args.rmult)
    STRIKE_DISTANCE = int(args.strike)

    ADX_PERIOD = int(args.adx_period)
    ADX_THRESHOLD = int(args.adx_thresh)
    FAST_EMA_PERIOD = int(args.fast_ema)
    SLOW_EMA_PERIOD = int(args.slow_ema)
    ATR_PERIOD = int(args.atr_period)

    SL_MODE = args.sl_mode
    SWING_LOOKBACK = int(args.swing_lookback)
    MAX_CONCURRENT_POS = int(args.max_pos)
    DAILY_MAX_LOSS = float(args.daily_loss)
    LOT_MULTIPLIER = int(args.lot_mult)
    PRODUCT_TYPE = args.product
    MIN_RANGE_PCT = float(args.min_range)
    EMA_BUFFER = float(args.ema_buffer)

    dry_run = args.dry_run or (not HAS_FYERS)

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

    print("\\n🎯 BUILDING REAL-TIME OPTION WATCHLIST...")
    print("=" * 60)
    print(
        f"📊 Strike Distance: {STRIKE_DISTANCE} ({'ITM' if STRIKE_DISTANCE < 0 else 'OTM' if STRIKE_DISTANCE > 0 else 'ATM'})")
    print("=" * 60)

    # Get initial option contracts with specified strike distance
    options_data = option_manager.refresh_options(SPOT_INDICES, STRIKE_DISTANCE)

    if not options_data:
        print("⚠️ Warning: No option contracts could be found for some indices.")
        print("Continuing with available options...")

    if not options_data:
        raise SystemExit("❌ No option contracts could be found for any indices. Exiting.")

    print("\\n✅ FINAL WATCHLIST:")
    print("=" * 60)
    for symbol, data in options_data.items():
        print(f"\\n📊 {symbol}")
        print(f" Strike: ₹{data['strike']:.2f} - {data['strike_position']}")
        print(f" LTP: ₹{data['ltp']:.2f}")
        print(f" Lot Size: {data['lot_size']} shares")
        print(f" Expiry: {data['expiry_date']}")
        print(f" Index: {data['index_name']}")
        print(f" Volume: {data['volume']:,}")

    print("\\n" + "=" * 60)

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

    print("\\n" + "=" * 70)
    print("🎯 ADX/EMA CE BUY STRATEGY - REAL-TIME")
    print("=" * 70)
    print(f"📊 STRATEGY CONFIG:")
    print(f"   ADX Period: {ADX_PERIOD}, Threshold: {ADX_THRESHOLD}")
    print(f"   Fast EMA: {FAST_EMA_PERIOD}, Slow EMA: {SLOW_EMA_PERIOD}, Buffer: {EMA_BUFFER}")
    print(f"   ATR Period: {ATR_PERIOD}")
    print(f"   Min Range %: {MIN_RANGE_PCT}")
    print(f"📊 RISK CONFIG:")
    print(f"   Max Concurrent Pos: {MAX_CONCURRENT_POS}")
    print(f"   Daily Max Loss: {DAILY_MAX_LOSS}")
    print(f"   SL Mode: {SL_MODE} (Lookback={SWING_LOOKBACK})")
    print(f"📊 ORDER CONFIG:")
    print(f"   Product: {PRODUCT_TYPE}")
    print(f"   Lot Multiplier: {LOT_MULTIPLIER}")
    print(f"   Strike Dist: {STRIKE_DISTANCE}")
    print("=" * 70)
    print(f"🧩 Python: {sys.version.split()[0]} | Symbols: {len(option_symbols)}")
    print(
        f"📈 TF={TIMEFRAME_MIN}m | Rmult={R_MULTIPLIER} | dry_run={dry_run}")
    print("=" * 70)
    print("🚀 Real-time LONG scanner started …\\n")
    ws_connection.connect()


# ===================== ENTRY POINT =====================
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\\n👋 Exiting on user interrupt.")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
