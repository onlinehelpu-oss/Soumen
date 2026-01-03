# -*- coding: utf-8 -*-
"""
Fyers Trading System - Final Resilient Version
This version uses an internet time server to get the correct date, permanently fixing the 422 error.
It also retains the Yahoo Finance fallback as a secondary resilience measure.
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
# TODAY is now determined dynamically using get_current_date()
# TOKEN_PATH is also now dynamic

# Create necessary directories
for directory in [TOKENS_DIR, MODELS_DIR, DATA_DIR, LOG_DIR]:
    os.makedirs(directory, exist_ok=True)

# --- GLOBAL DATE MANAGEMENT ---
_CURRENT_DATE = None

def get_current_date():
    """
    Fetches the current UTC date from an online API to bypass local clock issues.
    Falls back to system date if the API fails.
    """
    global _CURRENT_DATE
    if _CURRENT_DATE is not None:
        return _CURRENT_DATE

    try:
        log_message("Fetching current date from worldtimeapi.org...")
        response = requests.get("http://worldtimeapi.org/api/timezone/Etc/UTC", timeout=5)
        response.raise_for_status()
        data = response.json()
        utc_datetime_str = data['utc_datetime'].split('.')[0] # Remove microseconds
        _CURRENT_DATE = dt.fromisoformat(utc_datetime_str).date()
        log_message(f"Successfully fetched API date: {_CURRENT_DATE}", "INFO")
    except Exception as e:
        log_message(f"Could not fetch date from API: {e}. Falling back to system date.", "WARNING")
        _CURRENT_DATE = datetime.date.today()
        log_message(f"Using system date: {_CURRENT_DATE}", "WARNING")

    return _CURRENT_DATE

# Update TOKEN_PATH to be dynamic
def get_token_path():
    today_str = get_current_date().strftime("%Y-%m-%d")
    return os.path.join(TOKENS_DIR, f"{today_str}.json")


# SYMBOL LIST
SYMBOL_LIST = [
    "ACC", "ADANIENT", "ADANIPORTS", "AMBUJACEM", "APOLLOHOSP", "ASIANPAINT",
    "AXISBANK", "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BHARTIARTL",
    "BPCL", "BRITANNIA", "CIPLA", "COALINDIA", "DIVISLAB", "DRREDDY",
    "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO",
    "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK", "INFY", "ITC",
    "JSWSTEEL", "KOTAKBANK", "LT", "LTIM", "M&M", "MARUTI", "NESTLEIND",
    "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SUNPHARMA",
    "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TCS", "TECHM", "TITAN",
    "ULTRACEMCO", "UPL", "WIPRO", "CRUDEOIL", "NATURALGAS", "GOLD", "SILVER",
    "COPPER", "ZINC", "LEAD", "NICKEL", "ALUMINIUM"
]


def log_message(message, level="INFO"):
    """Log messages to file"""
    today_str = get_current_date().strftime("%Y-%m-%d")
    log_file = os.path.join(LOG_DIR, f"trading_{today_str}.log")
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
        print("\n" + "=" * 70)
        print("ENTER YOUR CREDENTIALS")
        print("=" * 70)
        self.app_id = input("Enter APP ID (e.g., ABCDE12345-100): ").strip()
        self.secret_key = input("Enter SECRET ID: ").strip()
        self.redirect_url = input("Enter Redirect URL (must match app): ").strip()
        if not all([self.app_id, self.secret_key, self.redirect_url]):
            print("❌ All fields are required")
            return False
        if input("Save to config file? (Y/N): ").strip().upper() == "Y":
            creds = {"api_key": self.app_id, "api_secret": self.secret_key, "redirect_url": self.redirect_url}
            with open(CONFIG_FILE, "w") as f:
                json.dump(creds, f, indent=2)
            print(f"✅ Saved to '{CONFIG_FILE}'")
        return True

    def build_auth_url(self, state="generate_token"):
        """Build authentication URL"""
        base = "https://api-t1.fyers.in/api/v3/generate-authcode"
        params = (f"client_id={quote(self.app_id)}&redirect_uri={quote(self.redirect_url, safe='')}"
                  f"&response_type=code&state={quote(state)}")
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
        payload = {"grant_type": "authorization_code", "appIdHash": self.sha256_appIdHash(), "code": auth_code}
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
            if not self.load_or_prompt_creds():
                return False

            token_path = get_token_path()
            if os.path.exists(token_path):
                try:
                    with open(token_path, "r") as f:
                        access_token = json.load(f)
                    api = FyersAPI(self.app_id, access_token)
                    if api.test_connection():
                        self.access_token = access_token
                        print(f"✅ Using existing token from {token_path}")
                        return True
                    else:
                        print("⚠ Existing token expired or invalid. Proceeding to get a new one.")
                except Exception:
                    print("⚠ Corrupted token file. Proceeding to get a new one.")

            auth_url = self.build_auth_url()
            print("\n" + "=" * 70)
            print("AUTHENTICATION STEPS")
            print("=" * 70)
            print(f"\n🔗 OPEN this URL in your browser:\n{'-'*70}\n{auth_url}\n{'-'*70}")
            try:
                webbrowser.open(auth_url)
                print("✓ Browser opened")
            except:
                print("⚠ Could not open browser")
            print("\n📋 FOLLOW IN BROWSER:\n1. Login to Fyers account\n2. Click 'Allow' to authorize"
                  "\n3. Copy the FULL redirect URL or just the auth code"
                  "\n💡 The URL looks like: https://your-redirect-url/?code=XXXXXX:YYYYYY\n" + "=" * 70)
            user_input = input("\n📥 Paste the FULL redirect URL or just the auth code: ").strip()
            if not user_input:
                print("❌ No input provided")
                return False
            try:
                auth_code = self.extract_code(user_input)
            except Exception as e:
                print(f"❌ Error extracting auth code: {e}")
                return False
            print("\n🔄 Getting access token...")
            token_resp = self.validate_authcode(auth_code)
            self.access_token = token_resp.get("access_token")
            if not self.access_token:
                print("❌ No access token in response")
                return False
            with open(token_path, "w") as f:
                json.dump(self.access_token, f)
            print(f"\n✅ AUTHENTICATION SUCCESSFUL!\nAccess Token saved to {token_path}\nToken: {self.access_token[:30]}...")
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
        self.headers = {'Authorization': f'{self.app_id}:{self.access_token}', 'Content-Type': 'application/json'}

    def test_connection(self):
        try:
            profile = self.get_profile()
            return profile is not None and profile.get('s') == 'ok'
        except Exception as e:
            log_message(f"Connection test failed: {str(e)}", "ERROR")
            return False

    def _make_request(self, method, endpoint, data=None, params=None):
        url = f"{self.base_url}/{endpoint}"
        try:
            response = requests.request(method.upper(), url, headers=self.headers, json=data, params=params, timeout=30)
            if response.status_code == 404:
                log_message(f"Endpoint not found: {endpoint}", "WARNING")
                return None
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            log_message(f"API Request failed for {url}: {str(e)}", "ERROR")
            return None

    def get_profile(self): return self._make_request('GET', 'profile')
    def get_funds(self): return self._make_request('GET', 'funds')
    def get_positions(self): return self._make_request('GET', 'positions')
    def get_market_status(self): return self._make_request('GET', 'market-status')
    def get_quotes(self, symbol): return self._make_request('GET', 'quotes', params={'symbols': symbol})
    def place_order(self, order_data): return self._make_request('POST', 'orders', data=order_data)

    def get_historical_data(self, symbol, days=365, resolution="D"):
        end_date = get_current_date()
        start_date = end_date - datetime.timedelta(days=days)
        params = {'symbol': symbol, 'resolution': resolution, 'date_format': '0',
                  'range_from': start_date.strftime('%Y-%m-%d'), 'range_to': end_date.strftime('%Y-%m-%d'), 'cont_flag': '1'}
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
            if hasattr(e, 'response') and e.response is not None and e.response.status_code == 422:
                log_message("CRITICAL: Fyers API rejected the historical data request (422 Error). This can be due to an incorrect system clock.", "ERROR")
            else:
                log_message(f"Historical data request failed: {str(e)}", "ERROR")
            return None


class DataProcessor:
    """Data processing for machine learning"""
    def __init__(self):
        self.scaler = StandardScaler()
        self.feature_names = []

    def calculate_indicators(self, df):
        data = df.copy()
        if 'Close' not in data.columns and 'close' in data.columns:
            data = data.rename(columns={'close': 'Close', 'open': 'Open', 'high': 'High', 'low': 'Low', 'volume': 'Volume'})
        close_prices = data['Close'].squeeze()
        volume_prices = data['Volume'].squeeze()
        data['Returns'] = close_prices.pct_change()
        for period in [5, 10, 20, 50]:
            data[f'SMA_{period}'] = close_prices.rolling(window=period).mean()
            data[f'EMA_{period}'] = close_prices.ewm(span=period, adjust=False).mean()
            sma = data[f'SMA_{period}'].replace(0, np.nan)
            data[f'Price_SMA_Ratio_{period}'] = close_prices / sma
        delta = close_prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss.replace(0, np.nan)
        data['RSI'] = 100 - (100 / (1 + rs))
        bb_period = 20
        data['BB_Middle'] = close_prices.rolling(window=bb_period).mean()
        bb_std = close_prices.rolling(window=bb_period).std()
        data['BB_Upper'] = data['BB_Middle'] + (bb_std * 2)
        data['BB_Lower'] = data['BB_Middle'] - (bb_std * 2)
        data['BB_Width'] = (data['BB_Upper'] - data['BB_Lower']) / data['BB_Middle'].replace(0, np.nan)
        data['BB_Position'] = (close_prices - data['BB_Lower']) / (data['BB_Upper'] - data['BB_Lower']).replace(0, np.nan)
        data['Volume_SMA'] = volume_prices.rolling(window=20).mean()
        data['Volume_Ratio'] = volume_prices / data['Volume_SMA'].replace(0, np.nan)
        data = data.fillna(method='ffill').fillna(method='bfill').fillna(0)
        self.feature_names = [col for col in data.columns if col not in ['Open', 'High', 'Low', 'Close', 'Volume', 'Returns']]
        return data

    def create_labels(self, df, forward_days=1, threshold=0.002):
        future_returns = df['Close'].squeeze().pct_change(periods=forward_days).shift(-forward_days)
        return (future_returns > threshold).astype(int).fillna(0)

    def prepare_features(self, df, labels):
        X = df[self.feature_names].copy().fillna(0).replace([np.inf, -np.inf], 0)
        common_idx = X.index.intersection(labels.index)
        X, y = X.loc[common_idx], labels.loc[common_idx]
        return self.scaler.fit_transform(X), y.values


class TradingModel:
    """Trading model using XGBoost"""
    def __init__(self):
        self.model = xgb.XGBClassifier(n_estimators=200, max_depth=6, learning_rate=0.05, subsample=0.8,
                                     colsample_bytree=0.8, random_state=42, n_jobs=-1, verbosity=0)
    def train(self, X_train, y_train, X_val=None, y_val=None):
        self.model.fit(X_train, y_train, eval_set=[(X_val, y_val)] if X_val is not None else None, verbose=100)
    def predict(self, X): return self.model.predict(X), self.model.predict_proba(X)
    def save(self, symbol):
        clean_symbol = symbol.replace(':', '_').replace('-', '_')
        model_file = os.path.join(MODELS_DIR, f"{clean_symbol}_model.joblib")
        joblib.dump(self.model, model_file)
        return model_file
    def load(self, symbol):
        clean_symbol = symbol.replace(':', '_').replace('-', '_')
        model_file = os.path.join(MODELS_DIR, f"{clean_symbol}_model.joblib")
        if os.path.exists(model_file):
            self.model = joblib.load(model_file)
            return True
        return False


class TradingBot:
    """Main trading bot with fallback to Yahoo Finance"""
    def __init__(self, api, symbol):
        self.api = api
        self.raw_symbol = symbol
        self.fyers_symbol = self._format_fyers_symbol(symbol)
        self.model = TradingModel()
        self.processor = DataProcessor()

    def _format_fyers_symbol(self, symbol):
        symbol = symbol.upper().replace('.NS', '')
        mcx_keywords = ["CRUDEOIL", "NATURALGAS", "GOLD", "SILVER", "COPPER", "ZINC", "LEAD", "NICKEL", "ALUMINIUM"]
        if any(keyword in symbol for keyword in mcx_keywords):
            log_message(f"MCX symbol detected: {symbol}. Using generic future format.", "WARNING")
            return f"MCX:{symbol}M1"
        return f"NSE:{symbol}-EQ"

    def fetch_historical_data_fyers(self, days=365, resolution="D"):
        try:
            response = self.api.get_historical_data(symbol=self.fyers_symbol, days=days, resolution=resolution)
            if response and response.get("s") == "ok" and response.get("candles"):
                df = pd.DataFrame(response["candles"], columns=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
                df.set_index('timestamp', inplace=True)
                log_message(f"Fetched {len(df)} candles for {self.fyers_symbol} from Fyers")
                return df
        except Exception as e:
            log_message(f"Fyers API error for {self.fyers_symbol}: {str(e)}", "ERROR")
        return None

    def fetch_historical_data_yahoo(self, days=365):
        if not self.fyers_symbol.startswith("NSE:"):
            log_message(f"Yahoo Finance fallback skipped for non-NSE symbol: {self.fyers_symbol}", "WARNING")
            return None
        try:
            import yfinance as yf
            yahoo_symbol = f"{self.raw_symbol}.NS"
            log_message(f"Trying Yahoo Finance for {yahoo_symbol}")
            end_date = get_current_date()
            start_date = end_date - datetime.timedelta(days=days)
            data = yf.download(yahoo_symbol, start=start_date, end=end_date, progress=False)
            if not data.empty:
                log_message(f"Fetched {len(data)} candles for {self.raw_symbol} from Yahoo Finance")
                return data.rename(columns={'Open': 'Open', 'High': 'High', 'Low': 'Low', 'Close': 'Close', 'Volume': 'Volume'})
        except Exception as e:
            log_message(f"Yahoo Finance error for {self.raw_symbol}: {str(e)}", "ERROR")
        return None

    def fetch_historical_data(self, days=365, resolution="D"):
        data = self.fetch_historical_data_fyers(days, resolution)
        if data is None or len(data) < 50:
            log_message("Fyers data unavailable, trying Yahoo Finance...", "WARNING")
            data = self.fetch_historical_data_yahoo(days)
        if data is None:
            log_message(f"Failed to fetch any data for {self.raw_symbol}", "ERROR")
        return data

    def train_model(self):
        data = self.fetch_historical_data(days=365)
        if data is None or len(data) < 100:
            log_message(f"Insufficient data for training {self.raw_symbol}", "ERROR")
            return False

        data_with_features = self.processor.calculate_indicators(data)
        labels = self.processor.create_labels(data_with_features)
        X, y = self.processor.prepare_features(data_with_features, labels)

        if len(X) < 50:
             log_message(f"Not enough training samples for {self.raw_symbol}", "ERROR")
             return False

        split_idx = int(len(X) * 0.8)
        X_train, X_test, y_train, y_test = X[:split_idx], X[split_idx:], y[:split_idx], y[split_idx:]

        log_message(f"Training {self.raw_symbol} with {len(X_train)} samples...")
        self.model.train(X_train, y_train, X_test, y_test)

        accuracy = accuracy_score(y_test, self.model.predict(X_test)[0])
        log_message(f"Model Accuracy for {self.raw_symbol}: {accuracy:.2%}")

        model_path = self.model.save(self.raw_symbol)
        scaler_path = os.path.join(MODELS_DIR, f"{self.raw_symbol}_scaler.pkl")
        with open(scaler_path, 'wb') as f: pickle.dump(self.processor.scaler, f)
        log_message(f"Model saved: {model_path}\nScaler saved: {scaler_path}")
        return True

    def get_signal(self):
        if not self.model.load(self.raw_symbol):
            log_message(f"No trained model found for {self.raw_symbol}", "ERROR")
            return None
        scaler_path = os.path.join(MODELS_DIR, f"{self.raw_symbol}_scaler.pkl")
        if not os.path.exists(scaler_path):
            log_message(f"No scaler found for {self.raw_symbol}", "ERROR")
            return None
        with open(scaler_path, 'rb') as f: self.processor.scaler = pickle.load(f)
        data = self.fetch_historical_data(days=60, resolution="D")
        if data is None or len(data) < 20: return None

        data_with_features = self.processor.calculate_indicators(data)
        X_latest = data_with_features[self.processor.feature_names].iloc[-1:].copy().fillna(0)
        X_scaled = self.processor.scaler.transform(X_latest)
        prediction, probability = self.model.predict(X_scaled)
        confidence = max(probability[0])
        signal = 'BUY' if prediction[0] == 1 else 'SELL'
        current_price = data['Close'].iloc[-1]
        try:
            quote = self.api.get_quotes(self.fyers_symbol)
            if quote and quote.get('s') == 'ok':
                current_price = quote['d'][0]['v'].get('lp', current_price)
        except: pass
        return {'symbol': self.fyers_symbol, 'signal': signal, 'confidence': float(confidence),
                'price': current_price, 'timestamp': dt.now().strftime('%Y-%m-%d %H:%M:%S')}


def main():
    """Main function"""
    print("\n" + "=" * 70)
    print("FYERS ALGO TRADING SYSTEM - FINAL RESILIENT VERSION")
    print("=" * 70)
    print("\n⚠ For data fallback, please install yfinance: pip install yfinance")
    print("=" * 70)

    auth = FyersAuthV3()
    api = None

    while True:
        print("\n" + "="*70 + "\nMAIN MENU\n" + "="*70)
        print("1. 🔐 Authenticate\n2. 🏋️ Train a SINGLE symbol\n3. 🤖 Train ALL symbols"
              "\n4. 📈 Get trading signal\n5. 📊 Account summary\n6. 🚪 Exit\n" + "="*70)
        choice = input("\nSelect option (1-6): ").strip()
        if choice == '1':
            if auth.authenticate():
                api = FyersAPI(auth.app_id, auth.access_token)
                if api.test_connection(): print("\n✅ SUCCESS! Connected to Fyers API")
                else: print("\n⚠ API connection test failed.")
            else: print("\n❌ Authentication failed")
        elif choice in ['2', '3']:
            if not api: print("\n⚠ Please authenticate first (Option 1)"); continue
            symbols_to_train = [input("\nEnter a SINGLE symbol: ").strip().upper()] if choice == '2' else SYMBOL_LIST
            if choice == '3' and input(f"Train all {len(SYMBOL_LIST)} symbols? (y/n): ").lower() != 'y': continue

            successful, failed = 0, []
            for i, symbol in enumerate(symbols_to_train):
                print("\n" + "="*70 + f"\n[{i+1}/{len(symbols_to_train)}] TRAINING: {symbol}\n" + "="*70)
                try:
                    bot = TradingBot(api, symbol)
                    if bot.train_model(): successful += 1
                    else: failed.append(symbol)
                except Exception as e:
                    log_message(f"CRITICAL ERROR training {symbol}: {e}", "ERROR")
                    failed.append(symbol)
                time.sleep(1)
            print("\n" + "="*70 + "\nTRAINING SUMMARY\n" + "="*70)
            print(f"✅ Successful: {successful}\n❌ Failed: {len(failed)}")
            if failed: print(f"Failed symbols: {', '.join(failed)}")
            print("="*70)
        elif choice == '4':
            if not api: print("\n⚠ Please authenticate first (Option 1)"); continue
            symbol = input("\nEnter symbol for signal: ").strip().upper()
            bot = TradingBot(api, symbol)
            print(f"\n🔍 Analyzing {symbol}...")
            signal = bot.get_signal()
            if signal:
                print("\n" + "="*70 + "\n📊 TRADING SIGNAL\n" + "="*70)
                print(f"Symbol: {signal['symbol']}\nSignal: {signal['signal']}\nConfidence: {signal['confidence']:.1%}")
                if signal['price']: print(f"Indicative Price: ₹{signal['price']:.2f}")
                print(f"Time: {signal['timestamp']}\n" + "="*70)
            else: print("\n❌ Failed to get signal. Model may need training.")
        elif choice == '5':
            if not api: print("\n⚠ Please authenticate first (Option 1)"); continue
            print("\n" + "="*70 + "\n📊 ACCOUNT SUMMARY\n" + "="*70)
            funds = api.get_funds()
            if funds and funds.get('s') == 'ok':
                fund_data = funds.get('fund_limit', [{}])[0]
                print(f"\n💰 Funds:\n  Available Margin: ₹{fund_data.get('equityAmount', 0):,.2f}")
            else: print("\n💰 Could not fetch funds.")
            positions = api.get_positions()
            if positions and positions.get('s') == 'ok' and positions.get('netPositions'):
                print(f"\n📈 Open Positions:")
                for pos in positions['netPositions']: print(f"  - {pos['symbol']}: Qty={pos['qty']}, P&L=₹{pos['pl']:.2f}")
            else: print(f"\n📈 No open positions.")
            print("="*70)
        elif choice == '6': print("\n👋 Exiting..."); break
        else: print("\n❌ Invalid option")
        input("\nPress Enter to continue...")

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: print("\n\n⚠ Program interrupted")
    except Exception as e:
        print(f"\n❌ Unhandled Error: {str(e)}")
        import traceback
        traceback.print_exc()
