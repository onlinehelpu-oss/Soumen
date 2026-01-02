# -*- coding: utf-8 -*-
"""
Fyers Trading System - FIXED API ENDPOINTS VERSION
Fixed historical data and other API endpoints
"""

import os
import sys
import json
import time
import datetime
import hashlib
import warnings

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

# Configuration
CONFIG_FILE = "fyers_login_details.json"
TOKENS_DIR = "AccessToken"
MODELS_DIR = "models"
DATA_DIR = "data"
LOG_DIR = "logs"
TODAY = str(datetime.date.today())
TOKEN_PATH = os.path.join(TOKENS_DIR, f"{TODAY}.json")

# Create necessary directories
for directory in [TOKENS_DIR, MODELS_DIR, DATA_DIR, LOG_DIR]:
    os.makedirs(directory, exist_ok=True)


def log_message(message, level="INFO"):
    """Log messages to file"""
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
            # Load or get credentials first. This sets self.app_id etc.
            if not self.load_or_prompt_creds():
                return False

            # Now that we have creds, check for an existing valid token
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
                        print("⚠ Existing token expired or invalid. Proceeding to get a new one.")
                except Exception:
                    print("⚠ Corrupted token file. Proceeding to get a new one.")

            # If no valid token, generate auth URL and continue with new login
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
        return self._make_request('GET', 'funds')

    def get_positions(self):
        """Get positions"""
        return self._make_request('GET', 'positions')

    def get_market_status(self):
        """Get market status"""
        return self._make_request('GET', 'market-status')

    def get_quotes(self, symbol):
        """Get quotes for a symbol"""
        # Fyers V3 quotes endpoint
        params = {'symbols': symbol}
        return self._make_request('GET', 'quotes', params=params)

    def get_historical_data(self, symbol, days=365, resolution="D"):
        """Get historical data using the correct v3 data endpoint"""
        end_date = dt.now()
        start_date = end_date - datetime.timedelta(days=days)

        params = {
            'symbol': symbol,
            'resolution': resolution,
            'date_format': '0',  # Using YYYY-MM-DD format
            'range_from': start_date.strftime('%Y-%m-%d'),
            'range_to': end_date.strftime('%Y-%m-%d'),
            'cont_flag': '1'
        }

        # Note: The base_url for data endpoints is different
        data_url = "https://api-t1.fyers.in/data/history"

        try:
            response = requests.get(data_url, headers=self.headers, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            if data.get("s") == "ok":
                return data
            else:
                log_message(f"Historical data error: {data.get('message', 'Unknown error')}", "ERROR")
                return None

        except requests.exceptions.RequestException as e:
            if e.response and e.response.status_code == 422:
                log_message("Historical data request failed (422 Error). Your system's clock may be set to a future date.", "ERROR")
            else:
                log_message(f"Historical data request failed: {str(e)}", "ERROR")
            return None

    def place_order(self, order_data):
        """Place an order"""
        return self._make_request('POST', 'orders', data=order_data)


class DataProcessor:
    """Data processing for machine learning"""

    def __init__(self):
        self.scaler = StandardScaler()
        self.feature_names = []

    def calculate_indicators(self, df):
        """Calculate technical indicators"""
        data = df.copy()

        # Ensure we have required columns
        if 'Close' not in data.columns:
            # Rename columns if needed
            if 'close' in data.columns:
                data = data.rename(columns={'close': 'Close', 'open': 'Open',
                                            'high': 'High', 'low': 'Low', 'volume': 'Volume'})

        # Basic features
        close_prices = data['Close']
        if isinstance(close_prices, pd.DataFrame):
            close_prices = close_prices.iloc[:, 0]
        data['Returns'] = close_prices.pct_change()

        # Moving averages
        for period in [5, 10, 20, 50]:
            data[f'SMA_{period}'] = close_prices.rolling(window=period).mean()
            data[f'EMA_{period}'] = close_prices.ewm(span=period, adjust=False).mean()

            # Correctly calculate the ratio as a new Series
            sma = data[f'SMA_{period}'].replace(0, np.nan)
            data[f'Price_SMA_Ratio_{period}'] = close_prices / sma

        # RSI
        delta = close_prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss.replace(0, np.nan)
        data['RSI'] = 100 - (100 / (1 + rs))

        # Bollinger Bands
        bb_period = 20
        data['BB_Middle'] = data['Close'].rolling(window=bb_period).mean()
        bb_std = data['Close'].rolling(window=bb_period).std()
        data['BB_Upper'] = data['BB_Middle'] + (bb_std * 2)
        data['BB_Lower'] = data['BB_Middle'] - (bb_std * 2)
        data['BB_Width'] = (data['BB_Upper'] - data['BB_Lower']) / data['BB_Middle'].replace(0, np.nan)
        data['BB_Position'] = (data['Close'] - data['BB_Lower']) / (data['BB_Upper'] - data['BB_Lower']).replace(0,
                                                                                                                 np.nan)

        # Volume
        data['Volume_SMA'] = data['Volume'].rolling(window=20).mean()
        data['Volume_Ratio'] = data['Volume'] / data['Volume_SMA'].replace(0, np.nan)

        # Fill NaN values
        data = data.fillna(method='ffill').fillna(method='bfill').fillna(0)

        # Store feature names
        exclude_cols = ['Open', 'High', 'Low', 'Close', 'Volume', 'Returns']
        self.feature_names = [col for col in data.columns if col not in exclude_cols]

        return data

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
            verbosity=0
        )
        self.feature_importances = None

    def train(self, X_train, y_train, X_val=None, y_val=None):
        """Train the model"""
        if X_val is not None and y_val is not None:
            self.model.fit(
                X_train, y_train,
                eval_set=[(X_val, y_val)],
                verbose=100
            )
        else:
            self.model.fit(X_train, y_train, verbose=100)

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


class TradingBot:
    """Main trading bot with fallback to Yahoo Finance"""

    def __init__(self, api, symbol):
        self.api = api
        self.symbol = self._format_symbol(symbol)
        self.model = TradingModel()
        self.processor = DataProcessor()

    def _format_symbol(self, symbol):
        """Format symbol for Fyers API"""
        symbol = symbol.replace('.NS', '')
        return f"NSE:{symbol}-EQ"

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
            # Extract symbol name (e.g., "TCS" from "NSE:TCS-EQ")
            symbol_name = self.symbol.replace('NSE:', '').replace('-EQ', '')
            yahoo_symbol = f"{symbol_name}.NS"

            log_message(f"Trying Yahoo Finance for {yahoo_symbol}")

            # Use yfinance library if available, otherwise fallback
            try:
                import yfinance as yf
                end_date = dt.now()
                start_date = end_date - datetime.timedelta(days=days)

                data = yf.download(yahoo_symbol, start=start_date, end=end_date, progress=False)

                if not data.empty:
                    data = data.rename(columns={'Open': 'Open', 'High': 'High',
                                                'Low': 'Low', 'Close': 'Close',
                                                'Volume': 'Volume'})
                    log_message(f"Fetched {len(data)} candles for {self.symbol} from Yahoo Finance")
                    return data

            except ImportError:
                log_message("yfinance not installed, using web API", "WARNING")

                # Alternative: Use pandas datareader
                try:
                    import pandas_datareader.data as web
                    end_date = dt.now()
                    start_date = end_date - datetime.timedelta(days=days)

                    data = web.DataReader(yahoo_symbol, 'yahoo', start_date, end_date)
                    if not data.empty:
                        log_message(f"Fetched {len(data)} candles via pandas datareader")
                        return data
                except:
                    pass

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

        log_message(f"Model saved to {model_path}")
        log_message(f"Scaler saved to {scaler_path}")

        return True

    def get_signal(self):
        """Get trading signal"""
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

        # Get recent data
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

        # Try to get current price from Fyers
        current_price = None
        try:
            quote_response = self.api.get_quotes(self.symbol)
            if quote_response and quote_response.get('s') == 'ok':
                quote_data = quote_response.get('d', [{}])[0]
                current_price = quote_data.get('v', {}).get('lp')
        except:
            # Use last close price if quote fails
            if not data.empty:
                current_price = data['Close'].iloc[-1]

        return {
            'symbol': self.symbol,
            'signal': signal,
            'confidence': float(confidence),
            'buy_probability': float(probability[0][1]),
            'sell_probability': float(probability[0][0]),
            'price': current_price,
            'timestamp': dt.now().strftime('%Y-%m-%d %H:%M:%S')
        }


def main():
    """Main function"""
    print("\n" + "=" * 70)
    print("FYERS ALGO TRADING SYSTEM - FIXED API VERSION")
    print("=" * 70)

    print("\n⚠ IMPORTANT: Install required packages for fallback data:")
    print("pip install yfinance pandas_datareader")
    print("=" * 70)

    auth = FyersAuthV3()
    api = None
    bot = None

    while True:
        print("\n" + "=" * 70)
        print("MAIN MENU - ALL OPTIONS WORKING")
        print("=" * 70)
        print("1. 🔐 Authenticate with Fyers")
        print("2. 🏋️ Train model for stock")
        print("3. 📈 Get trading signal")
        print("4. 💰 Execute trade")
        print("5. 📊 Account summary")
        print("6. 🚪 Exit")
        print("=" * 70)

        try:
            choice = input("\nSelect option (1-6): ").strip()
        except KeyboardInterrupt:
            print("\n\n⚠ Program interrupted")
            break
        except Exception as e:
            print(f"\n⚠ Input error: {str(e)}")
            choice = '1'

        if choice == '1':
            # Authenticate
            if auth.authenticate():
                # Load token from file
                try:
                    with open(TOKEN_PATH, "r") as f:
                        access_token = json.load(f)

                    api = FyersAPI(auth.app_id, access_token)

                    # Test connection
                    if api.test_connection():
                        print("\n" + "=" * 70)
                        print("✅ SUCCESS! Connected to Fyers API")
                        print("=" * 70)

                        # Show profile
                        profile = api.get_profile()
                        if profile and profile.get('s') == 'ok':
                            data = profile.get('data', {})
                            print(f"\n👤 Welcome, {data.get('name', 'User')}!")
                            print(f"📋 Client ID: {data.get('client_id', 'N/A')}")
                            print(f"💰 Account Type: {data.get('fy_id', 'N/A')}")
                    else:
                        print("\n⚠ Connected but API test failed")
                        print("You can still try training models with fallback data")
                except Exception as e:
                    print(f"\n❌ Error setting up API: {str(e)}")
            else:
                print("\n❌ Authentication failed")

        elif choice == '2':
            # Train model
            if not api:
                print("\n⚠ Please authenticate first (Option 1)")
                continue

            symbol_input = input("\nEnter a SINGLE stock symbol (e.g., RELIANCE): ").strip().upper()
            if ',' in symbol_input or ' ' in symbol_input:
                print("❌ Please enter only one symbol at a time.")
                continue

            if not symbol_input.endswith('.NS'):
                symbol_input += '.NS'

            bot = TradingBot(api, symbol_input)

            print(f"\n🔄 Training model for {symbol_input}...")
            print("Note: Using fallback data if Fyers API fails")
            if bot.train_model():
                print(f"\n✅ Model trained successfully for {symbol_input}")
            else:
                print(f"\n❌ Failed to train model for {symbol_input}")
                print("\n💡 Install yfinance for better data:")
                print("pip install yfinance")

        elif choice == '3':
            # Get trading signal
            if not api:
                print("\n⚠ Please authenticate first (Option 1)")
                continue

            if not bot:
                symbol = input("\nEnter stock symbol (e.g., RELIANCE.NS): ").strip().upper()
                if not symbol.endswith('.NS'):
                    symbol += '.NS'

                bot = TradingBot(api, symbol)

            print(f"\n🔍 Analyzing {bot.symbol}...")
            signal = bot.get_signal()

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
                print(f"Time: {signal['timestamp']}")

                # Trading suggestion
                if signal['confidence'] > 0.75:
                    print(f"\n💡 STRONG SIGNAL - Good trading opportunity")
                elif signal['confidence'] > 0.6:
                    print(f"\n💡 MODERATE SIGNAL - Consider trading")
                else:
                    print(f"\n💡 WEAK SIGNAL - Wait for better setup")
                print("=" * 70)
            else:
                print("\n❌ Failed to get trading signal")
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
            print(f"Price: ₹{signal['price']:.2f}")
            print("\n⚠ REAL ORDER PLACEMENT IS DISABLED")
            print("To enable real trading, uncomment order placement code")
            print("=" * 70)

        elif choice == '5':
            # Account summary
            if not api:
                print("\n⚠ Please authenticate first (Option 1)")
                continue

            print("\n" + "=" * 70)
            print("📊 ACCOUNT SUMMARY")
            print("=" * 70)

            # Get funds
            funds = api.get_funds()
            if funds and funds.get('s') == 'ok':
                fund_data = funds.get('fund_limit', {})
                print(f"\n💰 FUNDS:")
                print(f"  Total Equity: ₹{fund_data.get('total_equity', 0):,.2f}")
                print(f"  Available Margin: ₹{fund_data.get('available_margin', 0):,.2f}")
                print(f"  Used Margin: ₹{fund_data.get('used_margin', 0):,.2f}")
            else:
                print(f"\n💰 Funds data not available")

            # Get positions
            positions = api.get_positions()
            if positions and positions.get('s') == 'ok':
                net_positions = positions.get('netPositions', [])
                if net_positions:
                    print(f"\n📈 OPEN POSITIONS:")
                    for pos in net_positions:
                        if pos.get('qty', 0) != 0:
                            symbol = pos.get('symbol', '')
                            qty = pos.get('qty', 0)
                            avg_price = pos.get('avg_price', 0)
                            current_price = pos.get('current_price', avg_price)
                            pnl = pos.get('pl', 0)
                            print(f"  {symbol}:")
                            print(f"    Quantity: {qty}")
                            print(f"    Avg Price: ₹{avg_price:.2f}")
                            print(f"    Current: ₹{current_price:.2f}")
                            print(f"    P&L: ₹{pnl:+,.2f}")
                else:
                    print(f"\n📈 No open positions")
            else:
                print(f"\n📈 Positions data not available")

            print("=" * 70)

        elif choice == '6':
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
        print("\n\n⚠ Program interrupted")
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        import traceback

        traceback.print_exc()
