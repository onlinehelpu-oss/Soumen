# -*- coding: utf-8 -*-
"""
Red-ShootingStar / Red-Pinbar NEXT-candle first-touch breakout (RED candle only)
WITH FULLY CUSTOMIZABLE CE & PE STRIKES AND COMBINED PREMIUM TRACKING
INCLUDING BSE:SENSEX-INDEX SUPPORT
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
from typing import Optional, Dict, List, Tuple, Any

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
            # Return mock changing prices for testing
            import random
            val = 100.0 + random.uniform(-5, 5)
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
            # Start mock data stream
            if self._subscribed and self._on_message:
                threading.Thread(target=self._mock_data_stream, daemon=True).start()

        def _mock_data_stream(self):
            import random
            while True:
                time.sleep(2)  # Send mock data every 2 seconds
                for symbol in self._subscribed:
                    mock_price = 100 + random.uniform(-5, 5)
                    mock_msg = {
                        "type": "sf",
                        "symbol": symbol,
                        "ltp": mock_price
                    }
                    try:
                        self._on_message(mock_msg)
                    except Exception as e:
                        print(f"[MOCK] Error in mock data stream: {e}")

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
TIMEFRAME_MIN = 5  # change to 5 / 15 / 30 / 60 etc., or override with --tf
R_MULTIPLIER = 1.0  # default Risk:Reward (1:2)
LOT_MULTIPLIER = 1  # Number of lots to trade
EPS = 1e-6

# ===================== OPTION SETTINGS =====================
STRIKE_CHOICE_TYPE = "custom"  # Options: "distance" or "custom"

# ===================== COMBINED PREMIUM SETTINGS =====================
TRACK_COMBINED_PREMIUM = True  # Track combined CE+PE premium

# Default strike distances (can be overridden by command line)
CE_STRIKE_DISTANCES = [-2]  # Default CE strike distances from ATM
PE_STRIKE_DISTANCES = [2]  # Default PE strike distances from ATM

# OR Custom strike prices (can be overridden by command line)
CE_CUSTOM_STRIKES = [26300]  # Custom CE strike prices
PE_CUSTOM_STRIKES = [26000]  # Custom PE strike prices

# ===================== CANDLE GEOMETRY SETTINGS =====================
UPPER_WICK_MIN = 50
UPPER_WICK_MAX = 80
BODY_MIN = 5
BODY_MAX = 30
LOWER_WICK_MAX = 25

# One-position-at-a-time control
ONE_POSITION_AT_A_TIME = True

# Tick setup
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
    'NSE:FINNIFTY-INDEX',
    'BSE:SENSEX-INDEX'
]


# ===================== CUSTOMIZABLE PREMIUM CHART MANAGER =====================
class CustomizablePremiumChartManager:
    """Manages customizable CE and PE premium chart."""

    # Fallback LOT SIZES
    FALLBACK_LOT_SIZES = {
        "NIFTY": 65,
        "BANKNIFTY": 30,
        "FINNIFTY": 60,
        "SENSEX": 20,
    }

    # Strike intervals
    STRIKE_INTERVALS = {
        "NIFTY": 50,
        "BANKNIFTY": 100,
        "FINNIFTY": 50,
        "SENSEX": 100,
    }

    def __init__(self, fy, timeframe_min):
        self.fy = fy
        self.timeframe_min = timeframe_min
        self.lot_cache = {}
        self.ce_symbols = []  # Can track multiple CE strikes
        self.pe_symbols = []  # Can track multiple PE strikes
        self.ce_data = {}  # symbol -> data dict
        self.pe_data = {}  # symbol -> data dict
        self.combined_premium_history = []
        self.candle_premium_history = []
        self.total_ce_premium = 0.0
        self.total_pe_premium = 0.0
        self.total_combined_premium = 0.0
        self.last_chart_display = 0  # Initialize as 0 to force first display
        self.candle_open_premium = 0.0
        self.candle_high_premium = 0.0
        self.candle_low_premium = 0.0
        self.candle_close_premium = 0.0
        self.premium_bars = {}  # Store premium candles for shooting star detection
        self.premium_processed_candles = set()
        self.premium_trigger = {}
        self.premium_ltp_cache = {}  # Cache for combined premium LTP

    def get_lot_size(self, symbol: str, exchange: str) -> int:
        """Fetch real-time lot size from Fyers Symbol Master CSV."""
        if not HAS_FYERS:
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
                return 65

            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                return self.FALLBACK_LOT_SIZES.get('NIFTY', 65)
            else:
                df = pd.read_csv(io.StringIO(resp.text), header=None)
                symbol_col = 9
                lot_col = 3
                matching_row = df[df.iloc[:, symbol_col] == symbol]
                if not matching_row.empty:
                    lot_size = int(matching_row.iloc[0, lot_col])
                else:
                    lot_size = self.FALLBACK_LOT_SIZES.get('NIFTY', 65)

            self.lot_cache[key] = lot_size
            return lot_size

        except Exception:
            return 65

    def get_index_ltp(self, index_symbol: str) -> Optional[float]:
        """Get current LTP for index."""
        try:
            response = self.fy.quotes({"symbols": index_symbol})
            if response.get('s') == 'ok' and response.get('d'):
                return float(response['d'][0]['v']['lp'])
        except Exception:
            return None

    def get_option_by_strike(self, index_symbol: str, target_strike: float, option_type: str = "CE") -> Optional[Dict]:
        """Get option details for a specific strike price and option type."""
        try:
            index_ltp = self.get_index_ltp(index_symbol)
            if not index_ltp:
                return None

            response = self.fy.optionchain({"symbol": index_symbol})

            if response.get('s') != 'ok':
                return None

            chain_data = response.get('data', {})
            options = chain_data.get('optionsChain', [])

            if not options:
                return None

            selected_option = None
            min_diff = float('inf')

            for opt in options:
                try:
                    if opt.get('option_type', '').upper() != option_type.upper():
                        continue

                    strike = float(opt.get('strike_price', 0))
                    diff = abs(strike - target_strike)

                    if diff < min_diff:
                        min_diff = diff
                        selected_option = opt
                except:
                    continue

            if not selected_option or min_diff > 25:  # Allow some tolerance
                return None

            symbol = selected_option.get('symbol', '')
            strike = float(selected_option.get('strike_price', 0))
            ltp = float(selected_option.get('ltp', 0))
            volume = int(selected_option.get('volume', 0))
            oi = int(selected_option.get('oi', 0))

            if symbol.startswith('NSE:'):
                exchange = 'NSE'
            elif symbol.startswith('BSE:'):
                exchange = 'BSE'
            else:
                exchange = 'UNKNOWN'

            lot_size = self.get_lot_size(symbol, exchange)

            index_name = self._get_index_short_name(index_symbol)

            return {
                'symbol': symbol,
                'strike': strike,
                'ltp': ltp,
                'volume': volume,
                'oi': oi,
                'lot_size': lot_size,
                'index_symbol': index_symbol,
                'index_name': index_name,
                'option_type': option_type
            }

        except Exception:
            return None

    def get_ce_options_by_distances(self, index_symbol: str, distances: List[int]) -> Dict[str, Dict]:
        """Get CE options for specified strike distances."""
        options_data = {}

        index_ltp = self.get_index_ltp(index_symbol)
        if not index_ltp:
            return options_data

        index_name = self._get_index_short_name(index_symbol)
        strike_interval = self.STRIKE_INTERVALS.get(index_name, 50)

        for distance in distances:
            atm_strike = round(index_ltp / strike_interval) * strike_interval

            if distance < 0:  # Negative distance = ITM for Calls (lower strike)
                target_strike = atm_strike - (abs(distance) * strike_interval)
                strike_type = "ITM"
            elif distance > 0:  # Positive distance = OTM for Calls (higher strike)
                target_strike = atm_strike + (distance * strike_interval)
                strike_type = "OTM"
            else:
                target_strike = atm_strike
                strike_type = "ATM"

            print(f"\n🔍 Getting {strike_type} CE (distance {distance})...")

            option_data = self.get_option_by_strike(index_symbol, target_strike, "CE")

            if option_data:
                options_data[option_data['symbol']] = option_data
                self.ce_symbols.append(option_data['symbol'])
                print(
                    f"✅ CE: {option_data['symbol'].split(':')[-1][:20]} | Strike: ₹{option_data['strike']:.2f} | Premium: ₹{option_data['ltp']:.2f}")
            else:
                print(f"❌ Failed to get CE for distance {distance}")

            time.sleep(0.5)

        return options_data

    def get_pe_options_by_distances(self, index_symbol: str, distances: List[int]) -> Dict[str, Dict]:
        """Get PE options for specified strike distances."""
        options_data = {}

        index_ltp = self.get_index_ltp(index_symbol)
        if not index_ltp:
            return options_data

        index_name = self._get_index_short_name(index_symbol)
        strike_interval = self.STRIKE_INTERVALS.get(index_name, 50)

        for distance in distances:
            atm_strike = round(index_ltp / strike_interval) * strike_interval

            if distance < 0:
                target_strike = atm_strike + (abs(distance) * strike_interval)
                strike_type = "ITM"
            elif distance > 0:
                target_strike = atm_strike - (distance * strike_interval)
                strike_type = "OTM"
            else:
                target_strike = atm_strike
                strike_type = "ATM"

            print(f"\n🔍 Getting {strike_type} PE (distance {distance})...")

            option_data = self.get_option_by_strike(index_symbol, target_strike, "PE")

            if option_data:
                options_data[option_data['symbol']] = option_data
                self.pe_symbols.append(option_data['symbol'])
                print(
                    f"✅ PE: {option_data['symbol'].split(':')[-1][:20]} | Strike: ₹{option_data['strike']:.2f} | Premium: ₹{option_data['ltp']:.2f}")
            else:
                print(f"❌ Failed to get PE for distance {distance}")

            time.sleep(0.5)

        return options_data

    def get_ce_options_by_custom_strikes(self, index_symbol: str, custom_strikes: List[float]) -> Dict[str, Dict]:
        """Get CE options for custom strike prices."""
        options_data = {}

        for strike_price in custom_strikes:
            print(f"\n🔍 Getting CE strike: ₹{strike_price:.2f}...")

            option_data = self.get_option_by_strike(index_symbol, strike_price, "CE")

            if option_data:
                options_data[option_data['symbol']] = option_data
                self.ce_symbols.append(option_data['symbol'])
                print(
                    f"✅ CE: {option_data['symbol'].split(':')[-1][:20]} | Strike: ₹{option_data['strike']:.2f} | Premium: ₹{option_data['ltp']:.2f}")
            else:
                print(f"❌ Failed to get CE for strike ₹{strike_price:.2f}")

            time.sleep(0.5)

        return options_data

    def get_pe_options_by_custom_strikes(self, index_symbol: str, custom_strikes: List[float]) -> Dict[str, Dict]:
        """Get PE options for custom strike prices."""
        options_data = {}

        for strike_price in custom_strikes:
            print(f"\n🔍 Getting PE strike: ₹{strike_price:.2f}...")

            option_data = self.get_option_by_strike(index_symbol, strike_price, "PE")

            if option_data:
                options_data[option_data['symbol']] = option_data
                self.pe_symbols.append(option_data['symbol'])
                print(
                    f"✅ PE: {option_data['symbol'].split(':')[-1][:20]} | Strike: ₹{option_data['strike']:.2f} | Premium: ₹{option_data['ltp']:.2f}")
            else:
                print(f"❌ Failed to get PE for strike ₹{strike_price:.2f}")

            time.sleep(0.5)

        return options_data

    def update_premiums(self, is_candle_close=False):
        """Update all premium calculations."""
        # Calculate total CE premium
        self.total_ce_premium = 0.0
        for symbol, data in self.ce_data.items():
            self.total_ce_premium += data.get('ltp', 0)

        # Calculate total PE premium
        self.total_pe_premium = 0.0
        for symbol, data in self.pe_data.items():
            self.total_pe_premium += data.get('ltp', 0)

        # Calculate combined premium
        current_premium = self.total_ce_premium + self.total_pe_premium
        self.total_combined_premium = current_premium

        # Update candle OHLC
        if is_candle_close:
            # Close current candle
            self.candle_close_premium = current_premium

            # Store candle data
            self.candle_premium_history.append({
                'timestamp': dt.datetime.now(),
                'open': self.candle_open_premium,
                'high': self.candle_high_premium,
                'low': self.candle_low_premium,
                'close': self.candle_close_premium,
                'ce_premium': self.total_ce_premium,
                'pe_premium': self.total_pe_premium,
                'combined_premium': self.total_combined_premium
            })

            # Keep only last 50 candles
            if len(self.candle_premium_history) > 50:
                self.candle_premium_history.pop(0)

            # Start new candle
            self.candle_open_premium = current_premium
            self.candle_high_premium = current_premium
            self.candle_low_premium = current_premium
            self.candle_close_premium = current_premium

        else:
            # Update intra-candle values
            if current_premium > self.candle_high_premium:
                self.candle_high_premium = current_premium

            if current_premium < self.candle_low_premium:
                self.candle_low_premium = current_premium

            self.candle_close_premium = current_premium

        # Add to history
        self.combined_premium_history.append({
            'timestamp': dt.datetime.now(),
            'ce_premium': self.total_ce_premium,
            'pe_premium': self.total_pe_premium,
            'combined_premium': self.total_combined_premium,
            'is_candle_close': is_candle_close
        })

        # Keep only last 500 data points
        if len(self.combined_premium_history) > 500:
            self.combined_premium_history.pop(0)

        return self.total_combined_premium

    def display_customizable_chart(self, is_candle_close=False):
        """Display customizable premium chart."""
        timestamp = dt.datetime.now()
        combined_premium = self.update_premiums(is_candle_close)

        # Display at candle close, or first time, or every 30 seconds
        current_time = time.time()
        should_display = False

        if is_candle_close:
            should_display = True
            display_reason = "Candle Close"
        elif self.last_chart_display == 0:  # First time
            should_display = True
            display_reason = "Initial Display"
        elif current_time - self.last_chart_display >= 30:  # Every 30 seconds
            should_display = True
            display_reason = "30s Interval"

        if should_display:
            print(f"\n📊 CUSTOMIZABLE CE & PE PREMIUM CHART - {timestamp.strftime('%H:%M:%S')} ({display_reason})")
            print(f"📈 Timeframe: {self.timeframe_min} minute{'s' if self.timeframe_min > 1 else ''}")
            print("=" * 80)

            # Display CE Options
            if self.ce_data:
                print(f"📈 CALL OPTIONS (CE): {len(self.ce_data)} strikes")
                print("-" * 80)
                for symbol, data in self.ce_data.items():
                    strike = data.get('strike', 0)
                    premium = data.get('ltp', 0)
                    print(f"  {symbol.split(':')[-1]:<25} | Strike: ₹{strike:7.2f} | Premium: ₹{premium:6.2f}")
                print(f"  {'CE TOTAL':<25} | {' ':7} | Premium: ₹{self.total_ce_premium:6.2f}")

            # Display PE Options
            if self.pe_data:
                print(f"\n📉 PUT OPTIONS (PE): {len(self.pe_data)} strikes")
                print("-" * 80)
                for symbol, data in self.pe_data.items():
                    strike = data.get('strike', 0)
                    premium = data.get('ltp', 0)
                    print(f"  {symbol.split(':')[-1]:<25} | Strike: ₹{strike:7.2f} | Premium: ₹{premium:6.2f}")
                print(f"  {'PE TOTAL':<25} | {' ':7} | Premium: ₹{self.total_pe_premium:6.2f}")

            # Combined premium calculation
            print("\n" + "=" * 80)
            print("💰 COMBINED PREMIUM CALCULATION:")
            print("-" * 80)
            print(f"  Total CE Premium:    ₹{self.total_ce_premium:8.2f}")
            print(f"  Total PE Premium:    ₹{self.total_pe_premium:8.2f}")
            print(f"  {'-' * 40}")
            print(f"  TOTAL COMBINED:      ₹{self.total_combined_premium:8.2f}")

            # Candle OHLC information
            if len(self.candle_premium_history) > 0:
                print(f"\n🕯️ CURRENT {self.timeframe_min}M COMBINED PREMIUM CANDLE:")
                print("-" * 80)
                print(f"  Open:  ₹{self.candle_open_premium:8.2f}")
                print(f"  High:  ₹{self.candle_high_premium:8.2f}")
                print(f"  Low:   ₹{self.candle_low_premium:8.2f}")
                print(f"  Close: ₹{self.candle_close_premium:8.2f}")
                print(f"  Range: ₹{self.candle_high_premium - self.candle_low_premium:8.2f}")

            # Show premium trend from start
            if len(self.combined_premium_history) > 1:
                # Find first candle close
                first_candle = None
                for entry in self.combined_premium_history:
                    if entry.get('is_candle_close', False):
                        first_candle = entry
                        break

                if first_candle:
                    oldest = first_candle['combined_premium']
                    newest = self.total_combined_premium
                    change = newest - oldest
                    percent_change = (change / oldest * 100) if oldest > 0 else 0

                    print(f"\n📈 PREMIUM TREND (Since start):")
                    print(f"  Start: ₹{oldest:.2f}")
                    print(f"  Current: ₹{newest:.2f}")
                    print(f"  Change: ₹{change:+.2f} ({percent_change:+.1f}%)")

            print("=" * 80)

            self.last_chart_display = current_time

    def update_premium_candle(self, combined_premium, is_candle_close=False):
        """Update premium candle for shooting star detection."""
        timestamp = dt.datetime.now()
        cstart = self.candle_start(timestamp, TIMEFRAME_MIN)
        key = f"PREMIUM_{cstart.timestamp()}"

        bar = self.premium_bars.get(key)
        if not bar:
            self.premium_bars[key] = bar = {"o": combined_premium, "h": combined_premium, "l": combined_premium,
                                            "c": combined_premium}
        else:
            bar["h"] = max(bar["h"], combined_premium)
            bar["l"] = min(bar["l"], combined_premium)
            bar["c"] = combined_premium

        # Check for shooting star at candle close
        candle_end = cstart + dt.timedelta(minutes=TIMEFRAME_MIN)
        time_to_candle_end = (candle_end - timestamp).total_seconds()

        if time_to_candle_end <= 1 and key not in self.premium_processed_candles:
            self.premium_processed_candles.add(key)
            prev_cstart = cstart - dt.timedelta(minutes=TIMEFRAME_MIN)
            prev_key = f"PREMIUM_{prev_cstart.timestamp()}"
            prev_bar = self.premium_bars.get(prev_key)

            if prev_bar and is_bearish_shooting_star_candle(
                    bar["o"], bar["h"], bar["l"], bar["c"],
                    prev_bar["o"], prev_bar["c"],
                    min_range_pct=MIN_RANGE_PCT
            ):
                print(f"\n🎯 SHOOTING STAR DETECTED on COMBINED PREMIUM at {timestamp:%H:%M:%S}")
                print(
                    f"   Combined Premium Open: {bar['o']:.2f}, High: {bar['h']:.2f}, Low: {bar['l']:.2f}, Close: {bar['c']:.2f}")
                print(
                    f"   Total CE Premium: ₹{self.total_ce_premium:.2f}, Total PE Premium: ₹{self.total_pe_premium:.2f}")

                # Set trigger for combined premium
                self.premium_trigger = {
                    "low": bar["l"],
                    "high": bar["h"],
                    "active_start": cstart + dt.timedelta(minutes=TIMEFRAME_MIN),
                    "triggered": False,
                    "ce_symbols": list(self.ce_data.keys()),
                    "pe_symbols": list(self.pe_data.keys())
                }

        # Update premium LTP cache
        self.premium_ltp_cache = (combined_premium, time.time())

    def candle_start(self, t: dt.datetime, timeframe_min: int) -> dt.datetime:
        """Calculate candle start time based on timeframe."""
        return t.replace(second=0, microsecond=0) - dt.timedelta(minutes=t.minute % timeframe_min)

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
ENTRY_BUFFER = 0.05
ENTRY_CUTOFF = dt.time(15, 0)
EXIT_ALL_TIME = dt.time(15, 9)
FORCE_CLOSED_ALL = False

MIN_RANGE_PCT = 0.0015
MIN_BODY_TICKS = 0


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
    creds = load_creds()
    client_id = creds["api_key"]
    secret_key = creds["api_secret"]
    redirect_uri = creds["redirect_url"]

    if os.path.exists(TODAY_PATH):
        access_token = _read_json(TODAY_PATH)
        if access_token and isinstance(access_token, str):
            print("🔑 Using today's cached access token.")
            return client_id, access_token

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
            if os.path.exists(TOKENS_STORE):
                _write_json(TOKENS_STORE, {})

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


# ===================== CANDLE DETECTOR =====================
def is_bearish_shooting_star_candle(o, h, l, c, prev_o, prev_c, min_range_pct=0.0015):
    if c == 0 or h <= l:
        return False
    total_range = h - l
    if (total_range / max(abs(c), 1e-9)) < min_range_pct:
        return False
    if prev_c <= prev_o:
        return False
    if c >= o:
        return False

    upper_wick_pct = ((h - o) / total_range) * 100
    body_pct = ((o - c) / total_range) * 100
    lower_wick_pct = ((c - l) / total_range) * 100
    is_valid_geometry = (
            (UPPER_WICK_MIN <= upper_wick_pct <= UPPER_WICK_MAX) and
            (BODY_MIN <= body_pct <= BODY_MAX) and
            (0 <= lower_wick_pct <= LOWER_WICK_MAX)
    )
    return is_valid_geometry


# ===================== ORDER HELPERS =====================
def place_order(fy, sym: str, side: int, qty: int, tag: str, dry_run=False):
    clean_tag = re.sub(r'[^a-zA-Z0-9]', '', tag)

    payload = {
        "symbol": sym,
        "qty": int(qty),
        "type": 2,
        "side": int(side),
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
    qty_shares = qty_lots * lot_size
    return place_order(fy, sym, side=1, qty=qty_shares, tag="ExitShort", dry_run=dry_run)


# ===================== PER-INDEX TRADER CLASS =====================
class IndexTrader:
    def __init__(self, fy, index_symbol, timeframe_min, dry_run=False):
        self.fy = fy
        self.index_symbol = index_symbol
        self.timeframe_min = timeframe_min
        self.dry_run = dry_run
        self.chart_manager = CustomizablePremiumChartManager(fy, timeframe_min)
        self.active_trades = {}
        self.combined_positions = {}
        self.last_chart_update = time.time()

    def has_open_positions(self) -> bool:
        """Check if this trader instance has any open positions."""
        return any(v.get("status") == "open" for v in self.active_trades.values())

    def get_all_symbols(self) -> List[str]:
        """Get all CE and PE symbols this trader is tracking."""
        return self.chart_manager.ce_symbols + self.chart_manager.pe_symbols

    def save_trade(self, sym, entry, sl, tgt, qty_lots, side=-1, lot_size=65, group_id=None, option_type="CE"):
        """Save a trade to the log and the internal state for this trader."""
        row = {
            "Datetime": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Symbol": sym,
            "Option Type": option_type,
            "Entry Price": float(entry),
            "Stop Loss": float(sl),
            "Target": float(tgt),
            "Qty": int(qty_lots),
            "Lot Size": int(lot_size),
            "Total Shares": int(qty_lots * lot_size),
            "Side": "SHORT" if side == -1 else "LONG",
            "Group ID": group_id or "SINGLE",
            "Index": self.index_symbol
        }
        pd.DataFrame([row]).to_csv(
            "trade_log.csv",
            mode='a',
            header=not os.path.exists("trade_log.csv"),
            index=False
        )
        self.active_trades[sym] = {
            "entry": entry, "sl": sl, "tgt": tgt, "qty": qty_lots,
            "status": "open", "side": side, "lot_size": lot_size,
            "group_id": group_id, "option_type": option_type
        }
        if group_id:
            if group_id not in self.combined_positions:
                self.combined_positions[group_id] = []
            if sym not in self.combined_positions[group_id]:
                self.combined_positions[group_id].append(sym)


# ===================== CANDLE BUILD STATE & LTP CACHE =====================
ltp_cache = {}
prev_ltp_cache = {}
_last_quote_error = {}
ERROR_THROTTLE_SECS = 10


def candle_start(t: dt.datetime, timeframe_min: int) -> dt.datetime:
    """Calculate candle start time based on timeframe."""
    return t.replace(second=0, microsecond=0) - dt.timedelta(minutes=t.minute % timeframe_min)


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


# ===================== WEBSOCKET HANDLER =====================
def make_onmsg_customizable(fy, traders: Dict[str, IndexTrader], dry_run=False):
    """Factory for the centralized websocket on_message handler."""

    # Create a reverse map from option symbol to its parent trader instance
    symbol_to_trader_map = {}
    for trader in traders.values():
        for symbol in trader.get_all_symbols():
            symbol_to_trader_map[symbol] = trader

    def onmsg(msg):
        if msg.get("type") != "sf":
            return
        try:
            sym = msg["symbol"]
            ltp = float(msg["ltp"])
        except (KeyError, ValueError):
            return

        # Delegate the message to the correct IndexTrader
        trader = symbol_to_trader_map.get(sym)
        if not trader:
            return

        chart_manager = trader.chart_manager

        # Update CE or PE data for the specific trader
        if sym in chart_manager.ce_data:
            chart_manager.ce_data[sym]['ltp'] = ltp
        elif sym in chart_manager.pe_data:
            chart_manager.pe_data[sym]['ltp'] = ltp

        current_time = dt.datetime.now()
        current_timestamp = time.time()
        combined_premium = chart_manager.update_premiums(is_candle_close=False)

        cstart = chart_manager.candle_start(current_time, chart_manager.timeframe_min)
        candle_end = cstart + dt.timedelta(minutes=chart_manager.timeframe_min)
        time_to_candle_end = (candle_end - current_time).total_seconds()
        is_candle_close = time_to_candle_end <= 2

        chart_manager.update_premium_candle(combined_premium, is_candle_close=is_candle_close)

        if is_candle_close or (current_timestamp - trader.last_chart_update >= 30):
            print(f"\n--------- UPDATE FOR {trader.index_symbol} ---------")
            chart_manager.display_customizable_chart(is_candle_close=is_candle_close)
            trader.last_chart_update = current_timestamp

        # Check premium trigger for breakout
        if chart_manager.premium_trigger and not chart_manager.premium_trigger.get("triggered", False):
            trigger_info = chart_manager.premium_trigger
            if current_time >= trigger_info["active_start"]:
                if current_time >= trigger_info["active_start"] + dt.timedelta(minutes=TIMEFRAME_MIN):
                    chart_manager.premium_trigger = {}
                    return

                if ONE_POSITION_AT_A_TIME and trader.has_open_positions():
                    print(f"[{dt.datetime.now():%H:%M:%S}] [{trader.index_symbol}] 🚫 Skipping entry — position already open.")
                    chart_manager.premium_trigger = {}
                    return

                if current_time.time() >= ENTRY_CUTOFF:
                    print(f"[{dt.datetime.now():%H:%M:%S}] [{trader.index_symbol}] ⏰ Skipping NEW entry — cutoff passed.")
                    chart_manager.premium_trigger = {}
                    return

                threshold = round_to_tick(trigger_info["low"] - ENTRY_BUFFER)
                prev_premium, _ = chart_manager.premium_ltp_cache or (None, 0)

                if prev_premium is not None and prev_premium >= threshold and combined_premium < threshold:
                    print(f"[{current_time:%H:%M:%S}] [{trader.index_symbol}] 🔥 COMBINED PREMIUM BREAKOUT < {threshold:.2f}. Placing trades...")
                    group_id = f"COMBINED_{trader.index_symbol.split(':')[1].split('-')[0]}_{int(time.time())}"

                    for ce_sym in trigger_info.get("ce_symbols", []):
                        if ce_sym in chart_manager.ce_data:
                            ce_data = chart_manager.ce_data[ce_sym]
                            risk = trigger_info["high"] - ce_data.get('ltp', 0)
                            if risk <= 0:
                                print(f"[{current_time:%H:%M:%S}] ✋ Risk <= 0 for {ce_sym}, skipping.")
                                continue

                            trader.save_trade(
                                sym=ce_sym, entry=floor_to_tick(ce_data.get('ltp', 0)),
                                sl=trigger_info["high"], tgt=round_to_tick(ce_data.get('ltp', 0) - (R_MULTIPLIER * risk)),
                                qty_lots=LOT_MULTIPLIER, side=-1, lot_size=ce_data.get('lot_size', 65),
                                group_id=group_id, option_type="CE"
                            )
                            place_order(fy, ce_sym, side=-1, qty=LOT_MULTIPLIER * ce_data.get('lot_size', 65), tag=f"CE_{trader.index_symbol}", dry_run=dry_run)

                    for pe_sym in trigger_info.get("pe_symbols", []):
                        if pe_sym in chart_manager.pe_data:
                            pe_data = chart_manager.pe_data[pe_sym]
                            risk = trigger_info["high"] - pe_data.get('ltp', 0)
                            if risk <= 0:
                                print(f"[{current_time:%H:%M:%S}] ✋ Risk <= 0 for {pe_sym}, skipping.")
                                continue

                            trader.save_trade(
                                sym=pe_sym, entry=floor_to_tick(pe_data.get('ltp', 0)),
                                sl=trigger_info["high"], tgt=round_to_tick(pe_data.get('ltp', 0) - (R_MULTIPLIER * risk)),
                                qty_lots=LOT_MULTIPLIER, side=-1, lot_size=pe_data.get('lot_size', 65),
                                group_id=group_id, option_type="PE"
                            )
                            place_order(fy, pe_sym, side=-1, qty=LOT_MULTIPLIER * pe_data.get('lot_size', 65), tag=f"PE_{trader.index_symbol}", dry_run=dry_run)

                    chart_manager.premium_trigger = {}

    return onmsg


# ===================== POSITION MONITOR =====================
def monitor_all_positions(fy, traders: Dict[str, IndexTrader], dry_run=False):
    """Centralized function to monitor all positions across all traders."""

    force_closed_indices = set()

    while True:
        now_dt = dt.datetime.now()
        now_time = now_dt.time()

        try:
            for index_symbol, trader in traders.items():

                # Force-exit at EXIT_ALL_TIME
                if index_symbol not in force_closed_indices and now_time >= EXIT_ALL_TIME:
                    open_trades = [s for s, t in trader.active_trades.items() if t.get("status") == "open"]
                    if open_trades:
                        print(f"[{now_dt:%H:%M:%S}] [{index_symbol}] ⏳ EXIT_ALL triggered — closing {len(open_trades)} trades")
                        for sym in open_trades:
                            trade = trader.active_trades.get(sym)
                            if trade:
                                exit_short_by_buy_market(fy, sym, trade["qty"], trade["lot_size"], dry_run=dry_run)
                                trader.active_trades[sym]["status"] = "closed"
                        force_closed_indices.add(index_symbol)

                # Monitor active positions for SL/Target
                for sym, trade in list(trader.active_trades.items()):
                    if trade.get("status") != "open":
                        continue

                    ltp = get_ltp(fy, sym)
                    if ltp is None:
                        continue

                    if ltp >= trade["sl"]:
                        print(f"[{now_dt:%H:%M:%S}] [{index_symbol}] ❌ SL HIT {sym} @ {ltp:.2f} → Exiting")
                        exit_short_by_buy_market(fy, sym, trade["qty"], trade["lot_size"], dry_run=dry_run)
                        trader.active_trades[sym]["status"] = "closed"

                    elif ltp <= trade["tgt"]:
                        print(f"[{now_dt:%H:%M:%S}] [{index_symbol}] 🎯 TARGET HIT {sym} @ {ltp:.2f} → Exiting")
                        exit_short_by_buy_market(fy, sym, trade["qty"], trade["lot_size"], dry_run=dry_run)
                        trader.active_trades[sym]["status"] = "closed"

        except Exception as e:
            print(f"⚠️ Position monitor error: {e}")

        time.sleep(1.5)


# ===================== MAIN =====================
def main():
    global TIMEFRAME_MIN, R_MULTIPLIER, TRACK_COMBINED_PREMIUM
    global STRIKE_CHOICE_TYPE, CE_STRIKE_DISTANCES, PE_STRIKE_DISTANCES
    global CE_CUSTOM_STRIKES, PE_CUSTOM_STRIKES

    parser = argparse.ArgumentParser(description="Customizable CE & PE Premium Chart Strategy")
    parser.add_argument("--tf", type=int, default=TIMEFRAME_MIN, help="Timeframe in minutes")
    parser.add_argument("--rmult", type=float, default=R_MULTIPLIER, help="Risk:Reward multiple")
    parser.add_argument("--dry-run", action="store_true", help="Enable dry-run")
    parser.add_argument("--run-tests", action="store_true", help="Run unit tests")

    # Strike selection options
    strike_group = parser.add_mutually_exclusive_group()
    strike_group.add_argument("--ce-distances", type=str, help="CE strike distances from ATM (comma-separated)")
    strike_group.add_argument("--pe-distances", type=str, help="PE strike distances from ATM (comma-separated)")
    strike_group.add_argument("--both-distances", type=str, help="Both CE & PE strike distances (format: CE:PE)")

    strike_group.add_argument("--ce-custom", type=str, help="Custom CE strike prices (comma-separated)")
    strike_group.add_argument("--pe-custom", type=str, help="Custom PE strike prices (comma-separated)")
    strike_group.add_argument("--both-custom", type=str, help="Both CE & PE custom strikes (format: CE:PE)")

    # REMOVED: --choice-type argument, now inferred automatically.

    args, _ = parser.parse_known_args()
    TIMEFRAME_MIN = max(1, int(args.tf))
    R_MULTIPLIER = float(args.rmult)
    TRACK_COMBINED_PREMIUM = True
    dry_run = args.dry_run or (not HAS_FYERS)

    # ---- Determine strike choice type, respecting global default ----
    is_custom_strike_mode = any([args.ce_custom, args.pe_custom, args.both_custom])
    is_distance_strike_mode = any([args.ce_distances, args.pe_distances, args.both_distances])

    # Only override the global default if specific command-line arguments are provided.
    # If no strike-related args are given, the global STRIKE_CHOICE_TYPE from the top of the file is used.
    if is_custom_strike_mode:
        STRIKE_CHOICE_TYPE = "custom"
    elif is_distance_strike_mode:
        STRIKE_CHOICE_TYPE = "distance"

    # ---- Parse strike distances or custom strikes ----
    if args.ce_distances:
        CE_STRIKE_DISTANCES = [int(x.strip()) for x in args.ce_distances.split(',')]
        PE_STRIKE_DISTANCES = []

    if args.pe_distances:
        PE_STRIKE_DISTANCES = [int(x.strip()) for x in args.pe_distances.split(',')]
        CE_STRIKE_DISTANCES = []

    if args.both_distances:
        ce_part, pe_part = args.both_distances.split(':')
        CE_STRIKE_DISTANCES = [int(x.strip()) for x in ce_part.split(',')]
        PE_STRIKE_DISTANCES = [int(x.strip()) for x in pe_part.split(',')]

    if args.ce_custom:
        CE_CUSTOM_STRIKES = [float(x.strip()) for x in args.ce_custom.split(',')]
        PE_CUSTOM_STRIKES = []

    if args.pe_custom:
        PE_CUSTOM_STRIKES = [float(x.strip()) for x in args.pe_custom.split(',')]
        CE_CUSTOM_STRIKES = []

    if args.both_custom:
        ce_part, pe_part = args.both_custom.split(':')
        CE_CUSTOM_STRIKES = [float(x.strip()) for x in ce_part.split(',')]
        PE_CUSTOM_STRIKES = [float(x.strip()) for x in pe_part.split(',')]

    if args.run_tests:
        run_tests()
        return

    # Get credentials
    if HAS_FYERS:
        client_id, access_token = ensure_access_token()
        fy = fyersModel.FyersModel(client_id=client_id, token=access_token, log_path=".")
    else:
        client_id = "MOCK_APP"
        access_token = "MOCK_ACCESS"
        fy = fyersModel.FyersModel(client_id=client_id, token=access_token, log_path=".")

    print("\n🎯 CUSTOMIZABLE CE & PE PREMIUM CHART STRATEGY")
    print("=" * 80)
    print(f"📊 Tracking Mode: Fully Customizable CE & PE Premium")
    print(f"📊 Timeframe: {TIMEFRAME_MIN} minute{'s' if TIMEFRAME_MIN > 1 else ''}")
    print(f"📊 Strike Choice Type: {STRIKE_CHOICE_TYPE}")
    print(f"📊 Shooting Star Detection: ON COMBINED PREMIUM CHART")

    if STRIKE_CHOICE_TYPE == "distance":
        print(f"📊 CE Strike Distances: {CE_STRIKE_DISTANCES}")
        print(f"📊 PE Strike Distances: {PE_STRIKE_DISTANCES}")
    else:
        print(f"📊 CE Custom Strikes: {CE_CUSTOM_STRIKES}")
        print(f"📊 PE Custom Strikes: {PE_CUSTOM_STRIKES}")

    print("\n📋 Select one or more indices to trade (e.g., '1' or '1,2'):")
    for i, idx in enumerate(SPOT_INDICES):
        print(f"  {i + 1}. {idx}")

    selected_indices = []
    try:
        raw_input = input("\nEnter index number(s): ")
        choices = [int(x.strip()) for x in raw_input.split(',')]
        for choice in choices:
            if 1 <= choice <= len(SPOT_INDICES):
                selected_indices.append(SPOT_INDICES[choice - 1])
            else:
                print(f"⚠️ Invalid choice '{choice}', skipping.")
    except Exception as e:
        print(f"❌ Invalid input: {e}. Exiting.")
        return

    if not selected_indices:
        print("❌ No valid indices selected. Exiting.")
        return

    print(f"✅ Selected Indices: {', '.join(selected_indices)}")

    # ---- Create a trader for each selected index ----
    traders = {}
    all_symbols_to_subscribe = []

    for index_symbol in selected_indices:
        print(f"\n{'='*30} INITIALIZING {index_symbol} {'='*30}")
        trader = IndexTrader(fy, index_symbol, TIMEFRAME_MIN, dry_run=dry_run)

        all_options = {}
        if STRIKE_CHOICE_TYPE == "distance":
            if CE_STRIKE_DISTANCES:
                all_options.update(trader.chart_manager.get_ce_options_by_distances(index_symbol, CE_STRIKE_DISTANCES))
            if PE_STRIKE_DISTANCES:
                all_options.update(trader.chart_manager.get_pe_options_by_distances(index_symbol, PE_STRIKE_DISTANCES))
        else: # custom
            if CE_CUSTOM_STRIKES:
                all_options.update(trader.chart_manager.get_ce_options_by_custom_strikes(index_symbol, CE_CUSTOM_STRIKES))
            if PE_CUSTOM_STRIKES:
                all_options.update(trader.chart_manager.get_pe_options_by_custom_strikes(index_symbol, PE_CUSTOM_STRIKES))

        if not all_options:
            print(f"⚠️ No option contracts found for {index_symbol}. Trying default ATM.")
            ce_opts = trader.chart_manager.get_ce_options_by_distances(index_symbol, [0])
            pe_opts = trader.chart_manager.get_pe_options_by_distances(index_symbol, [0])
            all_options.update(ce_opts)
            all_options.update(pe_opts)

        if not all_options:
            print(f"❌ FAILED to find any option contracts for {index_symbol}. Skipping this index.")
            continue

        trader.chart_manager.ce_data.update({k: v for k, v in all_options.items() if v['option_type'] == 'CE'})
        trader.chart_manager.pe_data.update({k: v for k, v in all_options.items() if v['option_type'] == 'PE'})

        traders[index_symbol] = trader
        all_symbols_to_subscribe.extend(trader.get_all_symbols())

        print(f"✅ {index_symbol} Initialized. Tracking {len(trader.get_all_symbols())} option symbols.")

    if not traders:
        raise SystemExit("❌ No traders could be initialized. Exiting.")

    # ---- Final Initialization and Display ----
    print("\n" + "=" * 80)
    print(f"✅ ALL {len(traders)} TRADERS INITIALIZED")
    print("=" * 80)

    for symbol, trader in traders.items():
        print(f"\n----------- INITIAL STATE FOR {symbol} -----------")
        trader.chart_manager.update_premiums(is_candle_close=False)
        trader.chart_manager.display_customizable_chart(is_candle_close=False)

    all_symbols = list(set(all_symbols_to_subscribe))
    print(f"\n[main] Combined subscription list has {len(all_symbols)} unique symbols.")

    on_message = make_onmsg_customizable(fy, traders, dry_run=dry_run)

    global ws_connection
    ws_connection = data_ws.FyersDataSocket(
        access_token=f"{client_id}:{access_token}",
        log_path=".",
        litemode=False,
        write_to_file=False,
        reconnect=True,
        on_message=on_message,
        on_error=lambda m: print(f"🚨 WebSocket Error: {m}"),
        on_close=lambda m: print(f"❌ WebSocket Closed: {m}"),
        on_connect=lambda: (
                print(f"🔌 Connected → subscribing to {len(all_symbols)} symbols.") or
                ws_connection.subscribe(symbols=all_symbols) or
                print(f"[{dt.datetime.now():%H:%M:%S}] ✅ Subscribed to symbols")
        )
    )

    # ---- Start Position Monitor and Websocket ----
    threading.Thread(target=monitor_all_positions, args=(fy, traders, dry_run), daemon=True).start()

    print("\n" + "=" * 80)
    print("🎯 CUSTOMIZABLE CE & PE PREMIUM CHART STRATEGY - REAL-TIME")
    print("=" * 80)

    print(f"📊 CONFIGURATION SUMMARY:")
    print(f"  • Timeframe: {TIMEFRAME_MIN} minute{'s' if TIMEFRAME_MIN > 1 else ''}")
    print(f"  • Strike Choice Type: {STRIKE_CHOICE_TYPE}")
    print(f"  • Shooting Star Detection: ON COMBINED PREMIUM")

    if STRIKE_CHOICE_TYPE == "distance":
        print(f"  • CE Distances: {CE_STRIKE_DISTANCES}")
        print(f"  • PE Distances: {PE_STRIKE_DISTANCES}")
    else:
        print(f"  • CE Custom Strikes: {CE_CUSTOM_STRIKES}")
        print(f"  • PE Custom Strikes: {PE_CUSTOM_STRIKES}")

    print(f"\n📊 TRADING SETTINGS:")
    print(f"  • Shooting Star Detection: {TIMEFRAME_MIN} minute candles ON COMBINED PREMIUM")
    print(
        f"  • Candle Geometry: Upper={UPPER_WICK_MIN}-{UPPER_WICK_MAX}%, Body={BODY_MIN}-{BODY_MAX}%, Lower=0-{LOWER_WICK_MAX}%")
    print(f"  • Risk Multiplier: {R_MULTIPLIER}")

    print("=" * 80)
    print(f"🧩 Python: {sys.version.split()[0]} | Symbols: {len(all_symbols)}")
    print(f"📈 TF={TIMEFRAME_MIN}m | Rmult={R_MULTIPLIER} | Customizable Strikes")
    print("=" * 80)
    print("🚀 Customizable premium chart scanner started …\n")

    ws_connection.connect()


def run_tests():
    print("Running tests...")
    # Corrected test case to match the defined geometry (50-80% upper, 5-30% body, 0-25% lower)
    # Original: upper=54.5%, body=13.6%, lower=31.8% (FAIL: lower wick too long)
    # Corrected: o=98, h=110, l=90, c=94 -> total_range=20
    # -> upper_wick = 12 (60%), body = 4 (20%), lower_wick = 4 (20%) -> PASS
    assert is_bearish_shooting_star_candle(98.0, 110.0, 90.0, 94.0, 95.0, 98.0) is True
    print("All tests passed ✅")


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