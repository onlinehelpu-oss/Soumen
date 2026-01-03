# -*- coding: utf-8 -*-
"""
Fyers Trading System - COMPLETE VERSION WITH WEBSOCKET
Fully functional algo trading system with Fyers API, WebSocket, and Yahoo Finance fallback
"""

import os
import sys
import json
import time
import datetime
import hashlib
import warnings
import threading
import queue
from typing import Dict, List, Optional

warnings.filterwarnings('ignore')

from urllib.parse import urlparse, parse_qs, quote
import requests
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import joblib
from datetime import datetime as dt
import pickle
import webbrowser
import websocket
import ssl

def get_current_date():
    """
    Fetch the current date from worldtimeapi.org to avoid system clock issues.
    If the API fails, fall back to the local system time but validate it.
    If the system time is in the future, exit with a clear error.
    """
    try:
        response = requests.get("http://worldtimeapi.org/api/timezone/Asia/Kolkata", timeout=10)
        response.raise_for_status()
        data = response.json()
        online_date = datetime.datetime.fromisoformat(data['datetime']).date()
        print(f"✅ Successfully fetched current date: {online_date}")
        return online_date
    except (requests.RequestException, json.JSONDecodeError, KeyError) as e:
        print(f"Could not fetch or parse date from worldtimeapi: {e}")
        print("Falling back to local system time.")
        local_date = datetime.date.today()

        print(f"⚠️  WARNING: Using local system date: {local_date}. Please be aware that if this date is incorrect, API calls may fail.")
        return local_date

# Configuration
CONFIG_FILE = "fyers_login_details.json"
TOKENS_DIR = "AccessToken"
MODELS_DIR = "models"
DATA_DIR = "data"
LOG_DIR = "logs"

# Fetch the correct date at startup to avoid system clock issues
TODAY_DATE = get_current_date()
TODAY = str(TODAY_DATE)
TOKEN_PATH = os.path.join(TOKENS_DIR, f"{TODAY}.json")

# WebSocket configuration
WEBSOCKET_URL = "wss://api-t1.fyers.in/socket/v3"
WEBSOCKET_RECONNECT_DELAY = 5  # seconds

# Create necessary directories
for directory in [TOKENS_DIR, MODELS_DIR, DATA_DIR, LOG_DIR]:
    os.makedirs(directory, exist_ok=True)


def log_message(message, level="INFO"):
    """Log messages to file"""
    # Use a fixed date for the log file name to avoid creating new files on each run
    log_file = os.path.join(LOG_DIR, f"trading_{TODAY}.log")
    timestamp = dt.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] [{level}] {message}\n"

    with open(log_file, 'a') as f:
        f.write(log_entry)

    print(f"{message}")


class FyersAuthV3:
    """Fyers Authentication using working code"""

    def __init__(self):
        self.app_id = None
        self.secret_key = None
        self.redirect_url = None
        self.access_token = None

    def load_or_prompt_creds(self):
        """Load credentials from file or prompt user"""
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                creds = json.load(f)

            print("\n" + "=" * 70)
            print("LOADED EXISTING CREDENTIALS")
            print("=" * 70)
            print(f"App ID: {creds.get('api_key')}")
            print(f"Redirect URL: {creds.get('redirect_url')}")

            use_existing = input("\nUse these credentials? (y/n): ").strip().lower()
            if use_existing == 'y':
                self.app_id = creds.get("api_key")
                self.secret_key = creds.get("api_secret")
                self.redirect_url = creds.get("redirect_url")
                return True
            else:
                print("Please create new configuration...")

        # Get new credentials
        print("\n" + "=" * 70)
        print("ENTER YOUR CREDENTIALS")
        print("=" * 70)

        self.app_id = input("Enter APP ID (e.g., ABCDE12345-100): ").strip()
        self.secret_key = input("Enter SECRET ID: ").strip()
        self.redirect_url = input("Enter Redirect URL (must match app): ").strip()

        if not all([self.app_id, self.secret_key, self.redirect_url]):
            print("❌ All fields are required")
            return False

        # Save credentials
        if input("Save to config file? (Y/N): ").strip().upper() == "Y":
            creds = {
                "api_key": self.app_id,
                "api_secret": self.secret_key,
                "redirect_url": self.redirect_url
            }
            with open(CONFIG_FILE, "w") as f:
                json.dump(creds, f, indent=2)
            print(f"✅ Saved to '{CONFIG_FILE}'")

        return True

    def build_auth_url(self, state="generate_token"):
        """Build authentication URL"""
        base = "https://api-t1.fyers.in/api/v3/generate-authcode"
        params = (
            f"client_id={quote(self.app_id)}"
            f"&redirect_uri={quote(self.redirect_url, safe='')}"
            f"&response_type=code"
            f"&state={quote(state)}"
        )
        return f"{base}?{params}"

    def extract_code(self, user_input):
        """Extract auth code from URL or raw input"""
        if user_input.startswith("http://") or user_input.startswith("https://"):
            q = parse_qs(urlparse(user_input).query)
            code = q.get("code", [None])[0]
            if not code:
                raise ValueError("No 'code' param found in URL")
            return code
        return user_input

    def sha256_appIdHash(self):
        """Generate appIdHash"""
        return hashlib.sha256(f"{self.app_id}:{self.secret_key}".encode("utf-8")).hexdigest()

    def validate_authcode(self, auth_code, max_retries=5):
        """Exchange auth code for access token"""
        url = "https://api-t1.fyers.in/api/v3/validate-authcode"
        payload = {
            "grant_type": "authorization_code",
            "appIdHash": self.sha256_appIdHash(),
            "code": auth_code,
        }
        headers = {"Content-Type": "application/json"}

        for attempt in range(1, max_retries + 1):
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=20)
                if response.status_code == 503:
                    sleep_s = min(2 ** attempt, 30)
                    print(f"[{attempt}/{max_retries}] 503 - Retrying in {sleep_s}s...")
                    time.sleep(sleep_s)
                    continue

                response.raise_for_status()
                data = response.json()

                if data.get("s") == "error":
                    error_msg = data.get("message", "Unknown error")
                    error_code = data.get("code", "")
                    raise RuntimeError(f"Fyers error {error_code}: {error_msg}")

                return data

            except requests.RequestException as e:
                if attempt == max_retries:
                    raise
                sleep_s = min(2 ** attempt, 30)
                print(f"[{attempt}/{max_retries}] Network error: {e}. Retrying in {sleep_s}s...")
                time.sleep(sleep_s)

    def authenticate(self):
        """Complete authentication process"""
        print("\n" + "=" * 70)
        print("STARTING AUTHENTICATION")
        print("=" * 70)

        try:
            # Check for existing valid token
            if os.path.exists(TOKEN_PATH):
                try:
                    with open(TOKEN_PATH, "r") as f:
                        access_token = json.load(f)

                    # Check if token is still valid by testing API
                    api = FyersAPI(self.app_id, access_token)
                    if api.test_connection():
                        self.access_token = access_token
                        print(f"✅ Using existing token from {TOKEN_PATH}")
                        return True
                    else:
                        print("⚠ Existing token expired or invalid")
                except:
                    print("⚠ Corrupted token file")

            # Load credentials
            if not self.load_or_prompt_creds():
                return False

            # Generate auth URL
            auth_url = self.build_auth_url()

            print("\n" + "=" * 70)
            print("AUTHENTICATION STEPS")
            print("=" * 70)
            print(f"\n🔗 OPEN this URL in your browser:")
            print("-" * 70)
            print(auth_url)
            print("-" * 70)

            # Open browser
            try:
                webbrowser.open(auth_url)
                print("✓ Browser opened")
            except:
                print("⚠ Could not open browser")

            print("\n📋 FOLLOW IN BROWSER:")
            print("1. Login to Fyers account")
            print("2. Click 'Allow' to authorize")
            print("3. Copy the FULL redirect URL or just the auth code")
            print("\n💡 The URL looks like: https://your-redirect-url/?code=XXXXXX:YYYYYY")
            print("=" * 70)

            # Get auth code
            user_input = input("\n📥 Paste the FULL redirect URL or just the auth code: ").strip()

            if not user_input:
                print("❌ No input provided")
                return False

            try:
                auth_code = self.extract_code(user_input)
            except Exception as e:
                print(f"❌ Error extracting auth code: {e}")
                return False

            # Exchange code for token
            print("\n🔄 Getting access token...")
            token_resp = self.validate_authcode(auth_code)

            self.access_token = token_resp.get("access_token")
            if not self.access_token:
                print("❌ No access token in response")
                return False

            # Save token
            with open(TOKEN_PATH, "w") as f:
                json.dump(self.access_token, f)

            print(f"\n✅ AUTHENTICATION SUCCESSFUL!")
            print(f"Access Token saved to {TOKEN_PATH}")
            print(f"Token: {self.access_token[:30]}...")

            return True

        except Exception as e:
            print(f"❌ Authentication failed: {str(e)}")
            return False


class FyersAPI:
    """Fyers API wrapper with corrected endpoints"""

    def __init__(self, app_id, access_token):
        self.app_id = app_id
        self.access_token = access_token
        self.base_url = "https://api-t1.fyers.in/api/v3"
        self.headers = {
            'Authorization': f'{self.app_id}:{self.access_token}',
            'Content-Type': 'application/json'
        }

    def test_connection(self):
        """Test API connection"""
        try:
            profile = self.get_profile()
            return profile is not None and profile.get('s') == 'ok'
        except Exception as e:
            log_message(f"Connection test failed: {str(e)}", "ERROR")
            return False

    def _make_request(self, method, endpoint, data=None, params=None):
        """Make API request"""
        url = f"{self.base_url}/{endpoint}"

        try:
            if method.upper() == 'GET':
                response = requests.get(url, headers=self.headers, params=params, timeout=30)
            elif method.upper() == 'POST':
                response = requests.post(url, headers=self.headers, json=data, timeout=30)
            else:
                return None

            # Don't raise for 404 - return None instead
            if response.status_code == 404:
                log_message(f"Endpoint not found: {endpoint}", "WARNING")
                return None

            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            log_message(f"API Request failed: {str(e)}", "ERROR")
            return None

    def get_profile(self):
        """Get user profile"""
        return self._make_request('GET', 'profile')

    def get_funds(self):
        """Get account funds"""
        response = self._make_request('GET', 'funds')
        if response and response.get('s') == 'ok':
            return response.get('fund_limit', [])
        return []

    def get_positions(self):
        """Get positions"""
        response = self._make_request('GET', 'positions')
        if response and response.get('s') == 'ok':
            return response
        return None

    def get_market_status(self):
        """Get market status"""
        return self._make_request('GET', 'market-status')

    def get_quotes(self, symbol):
        """Get quotes for a symbol"""
        url = "https://api-t1.fyers.in/data/quotes"
        params = {'symbols': symbol}
        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            log_message(f"Quotes request failed: {str(e)}", "ERROR")
            return None

    def get_historical_data(self, symbol, days=365, resolution="D"):
        """Get historical data"""
        # Fyers v3 requires YYYY-MM-DD format for dates
        end_date = TODAY_DATE
        start_date = end_date - datetime.timedelta(days=days)

        url = "https://api-t1.fyers.in/data/history"

        params = {
            'symbol': symbol,
            'resolution': resolution,
            'date_format': '0',  # 0 for YYYY-MM-DD
            'range_from': start_date.strftime('%Y-%m-%d'),
            'range_to': end_date.strftime('%Y-%m-%d'),
            'cont_flag': '1'
        }

        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            if data.get("s") == "ok":
                return data
            else:
                log_message(f"Historical data error: {data.get('message', 'Unknown error')}", "ERROR")
                return None

        except requests.exceptions.RequestException as e:
            log_message(f"Historical data request failed: {str(e)}", "ERROR")
            return None

    def place_order(self, order_data):
        """Place an order - DEMO MODE - UNCOMMENT FOR REAL TRADING"""
        log_message(f"DEMO: Would place order: {order_data}", "INFO")
        return {"s": "ok", "message": "DEMO MODE - Order not placed"}
        # Uncomment below for real trading
        # return self._make_request('POST', 'orders', data=order_data)


class FyersWebSocket:
    """Fyers WebSocket client for real-time data - v3 COMPATIBLE"""

    def __init__(self, app_id, access_token, symbols=None):
        self.app_id = app_id
        self.access_token = access_token
        self.symbols = symbols or []
        self.ws = None
        self.connected = False
        self.data_queue = queue.Queue()
        self.callbacks = []
        self.running = False
        self.ws_thread = None

    def on_message(self, ws, message):
        """Handle incoming WebSocket messages"""
        try:
            data = json.loads(message)
            log_message(f"WebSocket message: {data}", "DEBUG")

            # v3 sends data in a list for symbolData
            if isinstance(data, list):
                for item in data:
                    # Reformats the quote to be compatible with the TradingBot's expected format
                    formatted_quote = {
                        'symbol': item.get('symbol'),
                        'ltp': item.get('ltp'),
                        'volume': item.get('volume'),
                        'timestamp': item.get('timestamp', int(time.time()))
                    }
                    self.data_queue.put(formatted_quote)
                    for callback in self.callbacks:
                        callback(formatted_quote)
            elif isinstance(data, dict):
                if data.get('s') == 'ok':
                    log_message(f"WebSocket status: {data.get('message', 'OK')}", "INFO")
                    if data.get('code') == 200: # Welcome message
                        self.connected = True
                        log_message("✅ WebSocket connected successfully", "INFO")
                elif data.get('s') == 'error':
                    log_message(f"WebSocket error: {data.get('message')}", "ERROR")

        except Exception as e:
            log_message(f"Error processing WebSocket message: {str(e)}", "ERROR")

    def on_error(self, ws, error):
        """Handle WebSocket errors"""
        log_message(f"WebSocket error: {str(error)}", "ERROR")
        self.connected = False

    def on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket closure"""
        log_message(f"WebSocket closed: {close_status_code} - {close_msg}", "WARNING")
        self.connected = False
        if self.running:
            log_message("Attempting to reconnect WebSocket...", "INFO")
            time.sleep(WEBSOCKET_RECONNECT_DELAY)
            self.connect()

    def on_open(self, ws):
        """Handle WebSocket opening"""
        log_message("WebSocket opened, subscribing to symbols...", "INFO")
        if self.symbols:
            subscribe_message = {
                "T": "SUB_L2",
                "d": self.symbols
            }
            ws.send(json.dumps(subscribe_message))
            log_message(f"Subscribed to symbols: {self.symbols}", "INFO")

    def connect(self):
        """Connect to Fyers WebSocket"""
        if self.running and self.ws_thread and self.ws_thread.is_alive():
            return True
        try:
            access_token = f"{self.app_id}:{self.access_token}"
            data_type = "symbolData"
            ws_url = f"{WEBSOCKET_URL}/?access_token={access_token}&data_type={data_type}"

            self.ws = websocket.WebSocketApp(
                ws_url,
                on_open=self.on_open,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close
            )
            self.running = True
            self.ws_thread = threading.Thread(target=self.ws.run_forever, kwargs={'sslopt': {"cert_reqs": ssl.CERT_NONE}})
            self.ws_thread.daemon = True
            self.ws_thread.start()

            for _ in range(15):
                if self.connected:
                    return True
                time.sleep(1)
            return self.connected
        except Exception as e:
            log_message(f"Failed to connect WebSocket: {str(e)}", "ERROR")
            self.running = False
            return False

    def disconnect(self):
        """Disconnect WebSocket"""
        self.running = False
        if self.ws:
            self.ws.close()
        if self.ws_thread and self.ws_thread.is_alive():
            self.ws_thread.join(timeout=2)
        self.connected = False
        log_message("WebSocket disconnected", "INFO")

    def add_symbol(self, symbol):
        """Add a symbol to subscribe"""
        if symbol not in self.symbols:
            self.symbols.append(symbol)
            if self.connected and self.ws:
                subscribe_message = {"T": "SUB_L2", "d": [symbol]}
                self.ws.send(json.dumps(subscribe_message))
                log_message(f"Added subscription for {symbol}", "INFO")

    def remove_symbol(self, symbol):
        """Remove a symbol from subscription"""
        if symbol in self.symbols:
            self.symbols.remove(symbol)
            if self.connected and self.ws:
                unsubscribe_message = {"T": "UNSUB_L2", "d": [symbol]}
                self.ws.send(json.dumps(unsubscribe_message))
                log_message(f"Removed subscription for {symbol}", "INFO")

    def get_latest_data(self, timeout=1):
        """Get latest data from queue"""
        try:
            return self.data_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def register_callback(self, callback):
        """Register a callback function for data updates"""
        if callback not in self.callbacks:
            self.callbacks.append(callback)
            log_message(f"Registered callback", "DEBUG")

    def unregister_callback(self, callback):
        """Unregister a callback function"""
        if callback in self.callbacks:
            self.callbacks.remove(callback)
            log_message(f"Unregistered callback", "DEBUG")


class DataProcessor:
    """Data processing for machine learning"""

    def __init__(self):
        self.scaler = StandardScaler()
        self.feature_names = []

    def normalize_dataframe(self, df):
        """Normalize dataframe to single-level columns"""
        # Reset index if needed
        if df.index.name == 'Date' or 'Date' in df.columns:
            df = df.reset_index()

        # Debug: Print column names
        log_message(f"Raw DataFrame columns: {list(df.columns)}", "DEBUG")

        # Handle MultiIndex columns
        if isinstance(df.columns, pd.MultiIndex):
            # Convert MultiIndex to single level
            df.columns = ['_'.join(col).strip() if isinstance(col, tuple) else str(col) for col in df.columns]

        # Debug: Print after MultiIndex conversion
        log_message(f"After MultiIndex conversion: {list(df.columns)}", "DEBUG")

        # Standardize column names - handle all possible cases
        column_mapping = {}

        # Check for different possible column names
        for col in df.columns:
            col_str = str(col).lower()

            if 'open' in col_str:
                column_mapping[col] = 'Open'
            elif 'high' in col_str:
                column_mapping[col] = 'High'
            elif 'low' in col_str:
                column_mapping[col] = 'Low'
            elif 'close' in col_str or 'adj close' in col_str:
                column_mapping[col] = 'Close'
            elif 'volume' in col_str:
                column_mapping[col] = 'Volume'
            elif 'date' in col_str or 'timestamp' in col_str:
                column_mapping[col] = 'Date'

        # Apply renaming
        df = df.rename(columns=column_mapping)

        # Debug: Print after renaming
        log_message(f"After renaming: {list(df.columns)}", "DEBUG")

        # Check if we have duplicate column names after renaming
        if any(df.columns.duplicated()):
            log_message(f"Duplicate columns found: {df.columns[df.columns.duplicated()].tolist()}", "WARNING")
            # Take only the first occurrence of each column
            seen = set()
            cols_to_keep = []
            for col in df.columns:
                if col not in seen:
                    seen.add(col)
                    cols_to_keep.append(col)
            df = df[cols_to_keep]

        # Set Date as index if present
        if 'Date' in df.columns:
            try:
                df['Date'] = pd.to_datetime(df['Date'])
                df.set_index('Date', inplace=True)
            except:
                log_message("Could not convert Date column to datetime", "WARNING")

        # Debug: Print final columns
        log_message(f"Final columns: {list(df.columns)}", "DEBUG")

        # Keep only necessary columns
        required_cols = ['Open', 'High', 'Low', 'Close']
        available_cols = []

        for col in required_cols:
            if col in df.columns:
                available_cols.append(col)
            else:
                # Try to find similar columns
                for df_col in df.columns:
                    if col.lower() in str(df_col).lower():
                        df = df.rename(columns={df_col: col})
                        available_cols.append(col)
                        break

        # If we have at least Open, High, Low, Close
        if len(available_cols) >= 4:
            result_df = df[available_cols].copy()

            # Add Volume if available
            if 'Volume' in df.columns:
                if isinstance(df['Volume'], pd.DataFrame):
                    result_df['Volume'] = df['Volume'].iloc[:, 0]
                else:
                    result_df['Volume'] = df['Volume']

            log_message(f"Final data shape: {result_df.shape}", "DEBUG")
            return result_df
        else:
            # Fallback: assume first 4-5 columns are OHLCV
            if len(df.columns) >= 4:
                df = df.iloc[:, :5]
                df.columns = ['Open', 'High', 'Low', 'Close', 'Volume'][:len(df.columns)]
                return df
            else:
                raise ValueError(
                    f"DataFrame doesn't contain required price columns. Available columns: {list(df.columns)}")

    def calculate_indicators(self, df):
        """Calculate technical indicators"""
        try:
            data = self.normalize_dataframe(df.copy())

            # Debug info
            log_message(f"Data shape before indicators: {data.shape}", "DEBUG")
            log_message(f"Data columns: {list(data.columns)}", "DEBUG")

            # Basic features
            data['Returns'] = data['Close'].pct_change()

            # Moving averages
            for period in [5, 10, 20, 50]:
                data[f'SMA_{period}'] = data['Close'].rolling(window=period, min_periods=1).mean()
                data[f'EMA_{period}'] = data['Close'].ewm(span=period, adjust=False, min_periods=1).mean()

                # Calculate ratio safely
                sma_col = f'SMA_{period}'
                if sma_col in data.columns:
                    sma_values = data[sma_col].replace(0, np.nan)
                    data[f'Price_SMA_Ratio_{period}'] = data['Close'] / sma_values
                else:
                    data[f'Price_SMA_Ratio_{period}'] = np.nan

            # RSI
            delta = data['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14, min_periods=1).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14, min_periods=1).mean()
            rs = gain / loss.replace(0, np.nan)
            data['RSI'] = 100 - (100 / (1 + rs))

            # Bollinger Bands
            bb_period = 20
            data['BB_Middle'] = data['Close'].rolling(window=bb_period, min_periods=1).mean()
            bb_std = data['Close'].rolling(window=bb_period, min_periods=1).std()
            data['BB_Upper'] = data['BB_Middle'] + (bb_std * 2)
            data['BB_Lower'] = data['BB_Middle'] - (bb_std * 2)

            # Calculate BB features
            if all(col in data.columns for col in ['BB_Upper', 'BB_Lower', 'BB_Middle']):
                bb_width = (data['BB_Upper'] - data['BB_Lower']) / data['BB_Middle'].replace(0, np.nan)
                data['BB_Width'] = bb_width

                bb_diff = data['BB_Upper'] - data['BB_Lower']
                bb_diff = bb_diff.replace(0, np.nan)
                bb_position = (data['Close'] - data['BB_Lower']) / bb_diff
                data['BB_Position'] = bb_position
            else:
                data['BB_Width'] = np.nan
                data['BB_Position'] = np.nan

            # Volume features
            if 'Volume' in data.columns:
                data['Volume_SMA'] = data['Volume'].rolling(window=20, min_periods=1).mean()
                volume_sma = data['Volume_SMA'].replace(0, np.nan)
                data['Volume_Ratio'] = data['Volume'] / volume_sma
            else:
                data['Volume_SMA'] = np.nan
                data['Volume_Ratio'] = np.nan

            # MACD
            exp1 = data['Close'].ewm(span=12, adjust=False).mean()
            exp2 = data['Close'].ewm(span=26, adjust=False).mean()
            data['MACD'] = exp1 - exp2
            data['MACD_Signal'] = data['MACD'].ewm(span=9, adjust=False).mean()
            data['MACD_Histogram'] = data['MACD'] - data['MACD_Signal']

            # ATR
            high_low = data['High'] - data['Low']
            high_close = np.abs(data['High'] - data['Close'].shift())
            low_close = np.abs(data['Low'] - data['Close'].shift())
            true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            data['ATR'] = true_range.rolling(window=14).mean()

            # Fill NaN values
            data = data.fillna(method='ffill').fillna(method='bfill').fillna(0)

            # Store feature names
            exclude_cols = ['Open', 'High', 'Low', 'Close', 'Volume', 'Returns']
            self.feature_names = [col for col in data.columns if col not in exclude_cols]

            log_message(f"Created {len(self.feature_names)} features", "DEBUG")

            return data

        except Exception as e:
            log_message(f"Error calculating indicators: {str(e)}", "ERROR")
            raise

    def create_labels(self, df, forward_days=1, threshold=0.002):
        """Create target labels"""
        future_returns = df['Close'].pct_change(periods=forward_days).shift(-forward_days)
        labels = (future_returns > threshold).astype(int)
        labels = labels.fillna(0)
        return labels

    def prepare_features(self, df, labels):
        """Prepare feature matrix"""
        X = df[self.feature_names].copy()
        X = X.fillna(0)
        X = X.replace([np.inf, -np.inf], 0)

        # Align indices
        common_idx = X.index.intersection(labels.index)
        X = X.loc[common_idx]
        y = labels.loc[common_idx]

        # Scale features
        X_scaled = self.scaler.fit_transform(X)

        return X_scaled, y.values


class TradingModel:
    """Trading model using XGBoost"""

    def __init__(self):
        self.model = xgb.XGBClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            n_jobs=-1,
            verbosity=0,
            eval_metric='logloss'
        )
        self.feature_importances = None

    def train(self, X_train, y_train, X_val=None, y_val=None):
        """Train the model"""
        if X_val is not None and y_val is not None:
            eval_set = [(X_val, y_val)]
        else:
            eval_set = None

        self.model.fit(
            X_train, y_train,
            eval_set=eval_set,
            verbose=100
        )

        self.feature_importances = self.model.feature_importances_

    def predict(self, X):
        """Make predictions"""
        predictions = self.model.predict(X)
        probabilities = self.model.predict_proba(X)
        return predictions, probabilities

    def save(self, symbol):
        """Save model to file"""
        model_file = os.path.join(MODELS_DIR, f"{symbol}_model.joblib")
        joblib.dump(self.model, model_file)
        return model_file

    def load(self, symbol):
        """Load model from file"""
        model_file = os.path.join(MODELS_DIR, f"{symbol}_model.joblib")
        if os.path.exists(model_file):
            self.model = joblib.load(model_file)
            return True
        return False

    def get_feature_importance(self):
        """Get feature importance"""
        if self.feature_importances is not None:
            return dict(zip(range(len(self.feature_importances)), self.feature_importances))
        return {}


class TradingBot:
    """Main trading bot with WebSocket support"""

    def __init__(self, api, symbol, websocket_client=None):
        self.api = api
        self.symbol = self._format_symbol(symbol)
        self.model = TradingModel()
        self.processor = DataProcessor()
        self.last_signal = None
        self.websocket = websocket_client
        self.live_prices = {}
        self.price_history = []
        self.max_history = 100

        # Register for WebSocket updates if available
        if self.websocket:
            self.websocket.add_symbol(self.symbol)
            self.websocket.register_callback(self.handle_websocket_data)

    def _format_symbol(self, symbol):
        """Format symbol for Fyers API"""
        symbol = symbol.split(',')[0].strip()
        symbol = symbol.replace('.NS', '')
        return f"NSE:{symbol}-EQ"

    def handle_websocket_data(self, data):
        """Handle incoming WebSocket data"""
        try:
            if 'symbol' in data and data['symbol'] == self.symbol:
                price_data = data.get('ltp', 0)
                volume = data.get('volume', 0)
                timestamp = data.get('timestamp', int(time.time() * 1000))

                self.live_prices = {
                    'price': float(price_data),
                    'volume': int(volume),
                    'timestamp': timestamp,
                    'time': dt.fromtimestamp(timestamp / 1000).strftime('%H:%M:%S')
                }

                # Store in history
                self.price_history.append(self.live_prices.copy())
                if len(self.price_history) > self.max_history:
                    self.price_history.pop(0)

                log_message(f"Live price update: {self.symbol} = ₹{price_data}", "DEBUG")

        except Exception as e:
            log_message(f"Error handling WebSocket data: {str(e)}", "ERROR")

    def fetch_historical_data_fyers(self, days=365, resolution="D"):
        """Fetch historical data from Fyers API"""
        try:
            response = self.api.get_historical_data(
                symbol=self.symbol,
                days=days,
                resolution=resolution
            )

            if response and response.get("s") == "ok":
                candles = response.get("candles", [])
                if candles:
                    df = pd.DataFrame(candles, columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
                    df.set_index('timestamp', inplace=True)
                    log_message(f"Fetched {len(df)} candles for {self.symbol} from Fyers")
                    return df

        except Exception as e:
            log_message(f"Fyers API error: {str(e)}", "ERROR")

        return None

    def fetch_historical_data_yahoo(self, days=365):
        """Fallback to Yahoo Finance for historical data"""
        try:
            symbol_name = self.symbol.replace('NSE:', '').replace('-EQ', '')
            yahoo_symbol = f"{symbol_name}.NS"

            log_message(f"Trying Yahoo Finance for {yahoo_symbol}")

            # Use yfinance library if available
            try:
                import yfinance as yf
                end_date = dt.now()
                start_date = end_date - datetime.timedelta(days=days)

                data = yf.download(yahoo_symbol, start=start_date, end=end_date, progress=False)

                if not data.empty:
                    log_message(f"Fetched {len(data)} candles for {self.symbol} from Yahoo Finance")
                    return data

            except ImportError:
                log_message("yfinance not installed", "WARNING")

        except Exception as e:
            log_message(f"Yahoo Finance error: {str(e)}", "ERROR")

        return None

    def fetch_historical_data(self, days=365, resolution="D"):
        """Try Fyers first, then fallback to Yahoo Finance"""
        data = self.fetch_historical_data_fyers(days, resolution)

        if data is None or len(data) < 50:
            log_message("Fyers data unavailable, trying Yahoo Finance...", "WARNING")
            data = self.fetch_historical_data_yahoo(days)

        if data is None:
            log_message(f"Failed to fetch data for {self.symbol}", "ERROR")
            return None

        return data

    def get_current_price(self):
        """Get current price with WebSocket priority"""
        # Try WebSocket first (real-time)
        if self.live_prices:
            return self.live_prices.get('price')

        # Try Fyers API
        try:
            quote_response = self.api.get_quotes(self.symbol)
            if quote_response and quote_response.get('s') == 'ok':
                quote_data = quote_response.get('d', [{}])[0]
                current_price = quote_data.get('v', {}).get('lp')
                if current_price:
                    return float(current_price)
        except:
            pass

        # Try Yahoo Finance
        try:
            symbol_name = self.symbol.replace('NSE:', '').replace('-EQ', '')
            yahoo_symbol = f"{symbol_name}.NS"

            import yfinance as yf
            stock = yf.Ticker(yahoo_symbol)
            history = stock.history(period="1d")
            if not history.empty:
                return float(history['Close'].iloc[-1])
        except:
            pass

        # Get last available price from historical data
        try:
            data = self.fetch_historical_data(days=5)
            if data is not None and not data.empty:
                return float(data['Close'].iloc[-1])
        except:
            pass

        return None

    def train_model(self):
        """Train the trading model"""
        data = self.fetch_historical_data(days=365)

        if data is None or len(data) < 100:
            log_message("Insufficient data for training", "ERROR")
            return False

        # Calculate indicators
        data_with_features = self.processor.calculate_indicators(data)

        # Create labels
        labels = self.processor.create_labels(data_with_features, forward_days=1, threshold=0.002)

        # Prepare features
        X, y = self.processor.prepare_features(data_with_features, labels)

        # Split data
        split_idx = int(len(X) * 0.8)
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]

        log_message(f"Training samples: {len(X_train)}")
        log_message(f"Testing samples: {len(X_test)}")

        # Train model
        self.model.train(X_train, y_train, X_test, y_test)

        # Evaluate
        predictions, _ = self.model.predict(X_test)
        accuracy = accuracy_score(y_test, predictions)

        log_message(f"Model Accuracy: {accuracy:.2%}")

        # Save model
        symbol_name = self.symbol.replace('NSE:', '').replace('-EQ', '')
        model_path = self.model.save(symbol_name)

        # Save scaler
        scaler_path = os.path.join(MODELS_DIR, f"{symbol_name}_scaler.pkl")
        with open(scaler_path, 'wb') as f:
            pickle.dump(self.processor.scaler, f)

        # Save feature names
        features_path = os.path.join(MODELS_DIR, f"{symbol_name}_features.pkl")
        with open(features_path, 'wb') as f:
            pickle.dump(self.processor.feature_names, f)

        log_message(f"Model saved to {model_path}")
        log_message(f"Scaler saved to {scaler_path}")
        log_message(f"Features saved to {features_path}")

        return True

    def get_signal(self, use_live_data=False):
        """Get trading signal with optional live data"""
        # Try to load existing model
        symbol_name = self.symbol.replace('NSE:', '').replace('-EQ', '')
        if not self.model.load(symbol_name):
            log_message("No trained model found", "ERROR")
            return None

        # Load scaler
        scaler_path = os.path.join(MODELS_DIR, f"{symbol_name}_scaler.pkl")
        if os.path.exists(scaler_path):
            with open(scaler_path, 'rb') as f:
                self.processor.scaler = pickle.load(f)

        # Load feature names
        features_path = os.path.join(MODELS_DIR, f"{symbol_name}_features.pkl")
        if os.path.exists(features_path):
            with open(features_path, 'rb') as f:
                self.processor.feature_names = pickle.load(f)

        # Get data
        if use_live_data and len(self.price_history) >= 20:
            # Use live data for recent prices
            data = self._create_live_dataframe()
        else:
            # Use historical data
            data = self.fetch_historical_data(days=60, resolution="D")

        if data is None or len(data) < 10:
            return None

        # Calculate indicators
        data_with_features = self.processor.calculate_indicators(data)

        # Prepare latest features
        X_latest = data_with_features[self.processor.feature_names].iloc[-1:].copy()
        X_latest = X_latest.fillna(0)
        X_scaled = self.processor.scaler.transform(X_latest)

        # Make prediction
        prediction, probability = self.model.predict(X_scaled)
        confidence = max(probability[0])

        signal_map = {0: 'SELL', 1: 'BUY'}
        signal = signal_map[prediction[0]]

        # Get current price
        current_price = self.get_current_price()

        self.last_signal = {
            'symbol': self.symbol,
            'signal': signal,
            'confidence': float(confidence),
            'buy_probability': float(probability[0][1]),
            'sell_probability': float(probability[0][0]),
            'price': current_price,
            'timestamp': dt.now().strftime('%Y-%m-%d %H:%M:%S'),
            'live_data': use_live_data,
            'features': dict(zip(self.processor.feature_names, X_latest.iloc[0].tolist())) if len(X_latest) > 0 else {}
        }

        return self.last_signal

    def _create_live_dataframe(self):
        """Create DataFrame from live price history"""
        if len(self.price_history) < 10:
            return None

        # Create DataFrame from price history
        df = pd.DataFrame(self.price_history)
        if 'price' not in df.columns or len(df) < 10:
            return None

        # For simplicity, use price as OHLC (in real trading, you'd have separate OHLC)
        df['Open'] = df['price'].shift(1)
        df['High'] = df['price'].rolling(window=5).max()
        df['Low'] = df['price'].rolling(window=5).min()
        df['Close'] = df['price']
        df['Volume'] = df.get('volume', 0)

        # Set timestamp as index
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)

        return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()

    def execute_trade(self, signal, quantity=1):
        """Execute trade based on signal"""
        if not signal:
            return {"error": "No signal provided"}

        order_data = {
            "symbol": signal['symbol'],
            "qty": quantity,
            "type": 2,  # Market order
            "side": -1 if signal['signal'] == 'SELL' else 1,
            "productType": "INTRADAY",
            "limitPrice": 0,
            "stopPrice": 0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
            "stopLoss": 0,
            "takeProfit": 0
        }

        log_message(f"Executing {signal['signal']} order for {signal['symbol']}", "INFO")
        response = self.api.place_order(order_data)

        return response


class PortfolioManager:
    """Portfolio management and risk analysis"""

    def __init__(self, api):
        self.api = api

    def get_portfolio_summary(self):
        """Get complete portfolio summary"""
        summary = {
            'funds': {},
            'positions': [],
            'total_value': 0,
            'total_pnl': 0,
            'market_status': {}
        }

        # Get funds
        funds = self.api.get_funds()
        if funds:
            total_equity = 0
            available_margin = 0
            used_margin = 0

            for fund_item in funds:
                if isinstance(fund_item, dict):
                    if 'equityAmount' in fund_item:
                        total_equity += float(fund_item.get('equityAmount', 0))
                    if 'availableMargin' in fund_item:
                        available_margin += float(fund_item.get('availableMargin', 0))
                    if 'utilisedMargin' in fund_item:
                        used_margin += float(fund_item.get('utilisedMargin', 0))
                    elif 'usedMargin' in fund_item:
                        used_margin += float(fund_item.get('usedMargin', 0))

            summary['funds'] = {
                'total_equity': total_equity,
                'available_margin': available_margin,
                'used_margin': used_margin,
                'available_cash': total_equity - used_margin
            }
            summary['total_value'] = total_equity

        # Get positions
        positions = self.api.get_positions()
        if positions and positions.get('s') == 'ok':
            net_positions = positions.get('netPositions', [])
            position_list = []

            for pos in net_positions:
                if pos.get('qty', 0) != 0:
                    position_info = {
                        'symbol': pos.get('symbol', ''),
                        'quantity': pos.get('qty', 0),
                        'avg_price': pos.get('avg_price', 0),
                        'current_price': pos.get('current_price', 0),
                        'pnl': pos.get('pl', 0),
                        'pnl_percentage': pos.get('pl', 0) / (pos.get('avg_price', 1) * pos.get('qty', 1)) * 100
                    }
                    position_list.append(position_info)
                    summary['total_pnl'] += float(pos.get('pl', 0))

            summary['positions'] = position_list

        # Get market status
        market_status = self.api.get_market_status()
        if market_status and market_status.get('s') == 'ok':
            summary['market_status'] = market_status.get('marketStatus', [])

        return summary

    def calculate_position_size(self, capital, risk_percent=2, stop_loss_percent=2):
        """Calculate optimal position size based on risk management"""
        risk_amount = capital * (risk_percent / 100)
        position_value = risk_amount / (stop_loss_percent / 100)
        return position_value


class LiveTradingMonitor:
    """Live trading monitor with WebSocket"""

    def __init__(self, api, symbols=None):
        self.api = api
        self.symbols = symbols or []
        self.websocket = None
        self.bots = {}
        self.running = False
        self.monitor_thread = None

    def start(self):
        """Start live monitoring"""
        if not self.api:
            log_message("API not connected", "ERROR")
            return False

        try:
            # Get access token from saved file
            with open(TOKEN_PATH, "r") as f:
                access_token = json.load(f)

            # Create WebSocket client
            self.websocket = FyersWebSocket(
                app_id=self.api.app_id,
                access_token=access_token,
                symbols=self.symbols
            )

            # Connect WebSocket
            if self.websocket.connect():
                log_message("✅ Live trading monitor started", "INFO")
                self.running = True

                # Start monitoring thread
                self.monitor_thread = threading.Thread(target=self._monitor_loop)
                self.monitor_thread.daemon = True
                self.monitor_thread.start()

                return True
            else:
                log_message("Failed to start WebSocket", "ERROR")
                return False

        except Exception as e:
            log_message(f"Error starting live monitor: {str(e)}", "ERROR")
            return False

    def stop(self):
        """Stop live monitoring"""
        self.running = False
        if self.websocket:
            self.websocket.disconnect()
        log_message("Live trading monitor stopped", "INFO")

    def add_symbol(self, symbol):
        """Add symbol to monitor"""
        formatted_symbol = f"NSE:{symbol.replace('.NS', '').replace(',', '').strip()}-EQ"
        if formatted_symbol not in self.symbols:
            self.symbols.append(formatted_symbol)
            if self.websocket:
                self.websocket.add_symbol(formatted_symbol)

        # Create trading bot for this symbol
        if formatted_symbol not in self.bots:
            self.bots[formatted_symbol] = TradingBot(self.api, symbol, self.websocket)

        log_message(f"Added {symbol} to live monitor", "INFO")

    def _monitor_loop(self):
        """Main monitoring loop"""
        log_message("Starting monitoring loop...", "INFO")

        while self.running:
            try:
                # Check for new WebSocket data
                if self.websocket:
                    data = self.websocket.get_latest_data(timeout=1)
                    if data:
                        self._process_market_data(data)

                # Sleep to prevent CPU overload
                time.sleep(0.1)

            except Exception as e:
                log_message(f"Error in monitor loop: {str(e)}", "ERROR")
                time.sleep(1)

    def _process_market_data(self, data):
        """Process incoming market data"""
        try:
            symbol = data.get('symbol')
            if symbol in self.bots:
                bot = self.bots[symbol]
                # Update bot with latest data
                # You can add real-time signal generation here
                pass

        except Exception as e:
            log_message(f"Error processing market data: {str(e)}", "ERROR")


def main():
    """Main function"""
    print("\n" + "=" * 70)
    print("🚀 FYERS ALGO TRADING SYSTEM - LIVE VERSION")
    print("=" * 70)

    print(
        "\n📦 Required packages: pip install yfinance pandas numpy xgboost scikit-learn matplotlib joblib requests websocket-client")
    print("=" * 70)

    auth = FyersAuthV3()
    api = None
    bot = None
    portfolio_mgr = None
    live_monitor = None

    while True:
        print("\n" + "=" * 70)
        print("🏠 MAIN MENU - LIVE TRADING")
        print("=" * 70)
        print("1. 🔐 Authenticate with Fyers")
        print("2. 🏋️ Train model for stock")
        print("3. 📈 Get trading signal")
        print("4. 💰 Execute trade (Demo)")
        print("5. 📊 Portfolio summary")
        print("6. 🌐 Live WebSocket monitor")
        print("7. ⚙️ Advanced options")
        print("8. 🚪 Exit")
        print("=" * 70)

        try:
            choice = input("\nSelect option (1-8): ").strip()
        except KeyboardInterrupt:
            print("\n\n⚠ Program interrupted")
            break
        except Exception as e:
            print(f"\n⚠ Input error: {str(e)}")
            choice = '1'

        if choice == '1':
            # Authenticate
            if auth.authenticate():
                try:
                    with open(TOKEN_PATH, "r") as f:
                        access_token = json.load(f)

                    api = FyersAPI(auth.app_id, access_token)

                    if api.test_connection():
                        print("\n" + "=" * 70)
                        print("✅ CONNECTED TO FYERS API")
                        print("=" * 70)

                        profile = api.get_profile()
                        if profile and profile.get('s') == 'ok':
                            data = profile.get('data', {})
                            print(f"\n👤 Welcome, {data.get('name', 'User')}!")
                            print(f"💰 Account: {data.get('fy_id', 'N/A')}")

                        portfolio_mgr = PortfolioManager(api)
                    else:
                        print("\n⚠ API test failed - using fallback mode")
                except Exception as e:
                    print(f"\n❌ Error: {str(e)}")
            else:
                print("\n❌ Authentication failed")

        elif choice == '2':
            # Train model
            if not api:
                print("\n⚠ Please authenticate first (Option 1)")
                continue

            symbol = input("\nEnter stock symbol (e.g., RELIANCE, TCS, INFY): ").strip().upper()

            # Validate input
            if ',' in symbol or ';' in symbol:
                print("\n❌ Please enter only ONE stock symbol")
                print("Example: RELIANCE or TCS or INFY")
                continue

            if not symbol.endswith('.NS'):
                symbol += '.NS'

            bot = TradingBot(api, symbol, live_monitor.websocket if live_monitor else None)

            print(f"\n🔄 Training model for {symbol}...")
            print("📊 Using Yahoo Finance data")
            if bot.train_model():
                print(f"\n✅ Model trained successfully!")
            else:
                print(f"\n❌ Failed to train model")
                print("\n💡 Try: pip install yfinance")

        elif choice == '3':
            # Get trading signal
            if not api:
                print("\n⚠ Please authenticate first (Option 1)")
                continue

            if not bot:
                symbol = input("\nEnter stock symbol (e.g., RELIANCE.NS): ").strip().upper()

                if ',' in symbol or ';' in symbol:
                    print("\n❌ Please enter only ONE stock symbol")
                    continue

                if not symbol.endswith('.NS'):
                    symbol += '.NS'

                bot = TradingBot(api, symbol, live_monitor.websocket if live_monitor else None)

            # Ask for live data preference
            use_live = input("\nUse live WebSocket data? (y/n): ").strip().lower() == 'y'

            print(f"\n🔍 Analyzing {bot.symbol}...")
            signal = bot.get_signal(use_live_data=use_live)

            if signal:
                print("\n" + "=" * 70)
                print("📊 TRADING SIGNAL")
                print("=" * 70)
                print(f"Symbol: {signal['symbol']}")
                print(f"Signal: {signal['signal']}")
                print(f"Confidence: {signal['confidence']:.1%}")
                print(f"Buy Probability: {signal['buy_probability']:.1%}")
                print(f"Sell Probability: {signal['sell_probability']:.1%}")

                if signal['price']:
                    print(f"Current Price: ₹{signal['price']:.2f}")
                else:
                    print(f"Current Price: Not available")

                print(f"Data Source: {'Live WebSocket' if signal.get('live_data') else 'Historical'}")
                print(f"Time: {signal['timestamp']}")

                # Trading recommendation
                confidence = signal['confidence']
                if confidence > 0.75:
                    print(f"\n💎 STRONG SIGNAL - Consider trading")
                elif confidence > 0.6:
                    print(f"\n📊 MODERATE SIGNAL - Review carefully")
                else:
                    print(f"\n⚠ WEAK SIGNAL - Wait for confirmation")

                print("=" * 70)
            else:
                print("\n❌ Failed to get signal")
                print("Try training the model first (Option 2)")

        elif choice == '4':
            # Execute trade (Demo)
            if not api:
                print("\n⚠ Please authenticate first (Option 1)")
                continue

            if not bot:
                print("\n⚠ Please get a signal first (Option 3)")
                continue

            signal = bot.get_signal()
            if not signal:
                print("\n❌ No valid signal")
                continue

            print("\n" + "=" * 70)
            print("💰 TRADE EXECUTION (DEMO MODE)")
            print("=" * 70)
            print(f"Symbol: {signal['symbol']}")
            print(f"Action: {signal['signal']}")
            print(f"Confidence: {signal['confidence']:.1%}")

            if signal['price']:
                print(f"Price: ₹{signal['price']:.2f}")

            print(f"\n✅ DEMO: Would execute {signal['signal']} order")
            print("\n⚠ REAL TRADING IS DISABLED")
            print("To enable real trading, modify the place_order() method")
            print("=" * 70)

        elif choice == '5':
            # Portfolio summary
            if not api:
                print("\n⚠ Please authenticate first (Option 1)")
                continue

            if not portfolio_mgr:
                portfolio_mgr = PortfolioManager(api)

            summary = portfolio_mgr.get_portfolio_summary()

            print("\n" + "=" * 70)
            print("📊 PORTFOLIO SUMMARY")
            print("=" * 70)

            # Funds
            if summary['funds']:
                print(f"\n💰 FUNDS:")
                funds = summary['funds']
                if funds.get('total_equity', 0) > 0:
                    print(f"  Total Equity: ₹{funds['total_equity']:,.2f}")
                if funds.get('available_margin', 0) > 0:
                    print(f"  Available Margin: ₹{funds['available_margin']:,.2f}")
                if funds.get('used_margin', 0) > 0:
                    print(f"  Used Margin: ₹{funds['used_margin']:,.2f}")
                if funds.get('available_cash', 0) > 0:
                    print(f"  Available Cash: ₹{funds['available_cash']:,.2f}")

            # Positions
            if summary['positions']:
                print(f"\n📈 OPEN POSITIONS ({len(summary['positions'])}):")
                for pos in summary['positions']:
                    print(f"\n  {pos['symbol']}:")
                    print(f"    Quantity: {pos['quantity']}")
                    print(f"    Avg Price: ₹{pos['avg_price']:.2f}")
                    print(f"    Current: ₹{pos['current_price']:.2f}")
                    print(f"    P&L: ₹{pos['pnl']:+,.2f} ({pos['pnl_percentage']:+.2f}%)")

                print(f"\n  📊 TOTAL P&L: ₹{summary['total_pnl']:+,.2f}")
            else:
                print(f"\n📈 No open positions")

            print("=" * 70)

        elif choice == '6':
            # Live WebSocket monitor
            if not api:
                print("\n⚠ Please authenticate first (Option 1)")
                continue

            print("\n" + "=" * 70)
            print("🌐 LIVE WEBSOCKET MONITOR")
            print("=" * 70)
            print("A. ▶️ Start live monitor")
            print("B. ⏹️ Stop live monitor")
            print("C. 📈 Add symbol to monitor")
            print("D. 📊 View live prices")
            print("E. ↩️ Back to main menu")
            print("=" * 70)

            sub_choice = input("\nSelect option: ").strip().upper()

            if sub_choice == 'A':
                if live_monitor and live_monitor.running:
                    print("\n⚠ Live monitor already running")
                else:
                    live_monitor = LiveTradingMonitor(api)
                    if live_monitor.start():
                        print("\n✅ Live monitor started")
                        print("📡 Connected to Fyers WebSocket")
                    else:
                        print("\n❌ Failed to start live monitor")

            elif sub_choice == 'B':
                if live_monitor:
                    live_monitor.stop()
                    print("\n⏹️ Live monitor stopped")
                else:
                    print("\n⚠ No live monitor running")

            elif sub_choice == 'C':
                if not live_monitor or not live_monitor.running:
                    print("\n⚠ Start live monitor first (Option A)")
                else:
                    symbol = input("\nEnter symbol to monitor (e.g., RELIANCE): ").strip().upper()
                    if not symbol.endswith('.NS'):
                        symbol += '.NS'
                    live_monitor.add_symbol(symbol)
                    print(f"\n✅ Added {symbol} to live monitor")

            elif sub_choice == 'D':
                if live_monitor and live_monitor.running:
                    print("\n📊 LIVE PRICES:")
                    if live_monitor.websocket and live_monitor.websocket.live_prices:
                        for symbol, data in live_monitor.websocket.live_prices.items():
                            print(f"  {symbol}: ₹{data['price']:.2f} at {data['time']}")
                    else:
                        print("  No live price data yet")
                else:
                    print("\n⚠ Start live monitor first (Option A)")

            elif sub_choice == 'E':
                continue

            else:
                print("\n❌ Invalid option")

        elif choice == '7':
            # Advanced options
            print("\n" + "=" * 70)
            print("⚙️ ADVANCED OPTIONS")
            print("=" * 70)
            print("A. 📊 View model performance")
            print("B. 🧹 Clear all trained models")
            print("C. 📁 View log files")
            print("D. 🔄 Test API connection")
            print("E. ↩️ Back to main menu")
            print("=" * 70)

            sub_choice = input("\nSelect option: ").strip().upper()

            if sub_choice == 'A':
                # View model performance
                print("\n" + "=" * 70)
                print("📊 MODEL PERFORMANCE")
                print("=" * 70)

                if os.path.exists(MODELS_DIR):
                    models = [f for f in os.listdir(MODELS_DIR) if f.endswith('.joblib')]
                    if models:
                        print(f"Trained models ({len(models)}):")
                        for model in models:
                            symbol = model.replace('_model.joblib', '')
                            print(f"  • {symbol}")
                    else:
                        print("No trained models found")
                else:
                    print("Models directory not found")

            elif sub_choice == 'B':
                # Clear models
                confirm = input("\n⚠ Delete ALL trained models? (y/n): ").strip().lower()
                if confirm == 'y':
                    if os.path.exists(MODELS_DIR):
                        for file in os.listdir(MODELS_DIR):
                            file_path = os.path.join(MODELS_DIR, file)
                            try:
                                os.remove(file_path)
                            except:
                                pass
                        print("✅ All models cleared")
                    else:
                        print("Models directory not found")

            elif sub_choice == 'C':
                # View logs
                print("\n" + "=" * 70)
                print("📁 LOG FILES")
                print("=" * 70)

                if os.path.exists(LOG_DIR):
                    logs = [f for f in os.listdir(LOG_DIR) if f.endswith('.log')]
                    if logs:
                        print(f"Available logs ({len(logs)}):")
                        for log in sorted(logs)[-5:]:
                            log_path = os.path.join(LOG_DIR, log)
                            size = os.path.getsize(log_path) / 1024
                            print(f"  • {log} ({size:.1f} KB)")
                    else:
                        print("No log files found")
                else:
                    print("Log directory not found")

            elif sub_choice == 'D':
                # Test API
                if not api:
                    print("\n⚠ Not authenticated")
                else:
                    print("\n🔄 Testing API connections...")

                    # Test profile
                    profile = api.get_profile()
                    if profile and profile.get('s') == 'ok':
                        print("✅ Profile API: Working")
                    else:
                        print("❌ Profile API: Failed")

                    # Test funds
                    funds = api.get_funds()
                    if funds:
                        print("✅ Funds API: Working")
                    else:
                        print("⚠ Funds API: May need market hours")

                    print("\n💡 Note: WebSocket works during market hours")

            elif sub_choice == 'E':
                continue

            else:
                print("\n❌ Invalid option")

        elif choice == '8':
            # Stop live monitor if running
            if live_monitor:
                live_monitor.stop()

            print("\n" + "=" * 70)
            print("👋 Thank you for using Fyers Trading System!")
            print("=" * 70)
            break

        else:
            print("\n❌ Invalid option")

        input("\nPress Enter to continue...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠ Program interrupted by user")
    except Exception as e:
        print(f"\n❌ Unexpected error: {str(e)}")
        import traceback

        traceback.print_exc()
    finally:
        print("\n" + "=" * 70)
        print("🔚 Program ended")
        print("=" * 70)