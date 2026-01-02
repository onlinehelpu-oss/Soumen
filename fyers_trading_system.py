# -*- coding: utf-8 -*-
"""
Complete Fyers Trading System - Fixed Version
Proper Fyers v3 API integration with working authentication
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
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import joblib
from datetime import datetime as dt
import pickle

# Configuration
CONFIG_FILE = "fyers_credentials.json"
TOKENS_DIR = "tokens"
MODELS_DIR = "models"
DATA_DIR = "data"
LOG_DIR = "logs"
TODAY = str(datetime.date.today())
TOKEN_FILE = os.path.join(TOKENS_DIR, f"token_{TODAY}.json")

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
    """Fyers Authentication Manager - Fixed Version"""

    def __init__(self):
        self.app_id = None
        self.secret_key = None
        self.redirect_url = "https://127.0.0.1:5000/"
        self.access_token = None
        self.auth_token = None  # auth_token is access_token for v3

    def setup_credentials(self):
        """Setup Fyers credentials"""
        print("\n" + "=" * 60)
        print("FYERS API CREDENTIALS SETUP")
        print("=" * 60)
        print("\nGet credentials from: https://myapi.fyers.in/dashboard")
        print("\nIMPORTANT: The Redirect URL you enter must EXACTLY match the one")
        print("           configured in your Fyers App Dashboard.")

        # Try to load from file
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
            self.app_id = config.get('app_id')
            self.secret_key = config.get('secret_key')
            self.redirect_url = config.get('redirect_url', self.redirect_url)
            if self.app_id and self.secret_key:
                print(f"✓ Loaded credentials from {CONFIG_FILE}")
                print(f"  - Using Redirect URL: {self.redirect_url}")
                return True
            else:
                print("! Incomplete credentials in config file. Please provide them now.")

        # Get credentials from user
        self.app_id = input("\nEnter App ID (e.g., XXXXXXXXXX-100): ").strip()
        self.secret_key = input("Enter Secret Key: ").strip()
        self.redirect_url = input(f"Enter Redirect URL [default: {self.redirect_url}]: ").strip() or self.redirect_url

        save = input("\nSave credentials for future use? (y/n): ").strip().lower()
        if save == 'y':
            config = {
                'app_id': self.app_id,
                'secret_key': self.secret_key,
                'redirect_url': self.redirect_url
            }
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=2)
            print(f"✓ Credentials saved to {CONFIG_FILE}")

        return True

    def generate_auth_code(self):
        """Generate authentication URL for user"""
        base_url = "https://api-t1.fyers.in/api/v3/generate-authcode"

        params = {
            'client_id': self.app_id,
            'redirect_uri': self.redirect_url,
            'response_type': 'code',
            'state': 'sample_state',
            'scope': '',
            'nonce': '',
            'code_challenge': '',
            'code_challenge_method': 'S256'
        }

        # Build URL
        query_string = '&'.join([f'{k}={quote(str(v))}' for k, v in params.items() if v])
        auth_url = f"{base_url}?{query_string}"

        print("\n" + "=" * 60)
        print("AUTHENTICATION REQUIRED")
        print("=" * 60)
        print("\n1. Open this URL in your browser:")
        print(f"\n{auth_url}")
        print("\n2. Login to your Fyers account")
        print("3. Authorize the application")
        print("4. You will be redirected to a localhost URL")
        print("5. Copy the FULL redirect URL or just the 'code' parameter")

        return auth_url

    def extract_auth_code(self, user_input):
        """Extract auth code from user input"""
        try:
            # If it's a URL, parse it
            if user_input.startswith('http'):
                parsed = urlparse(user_input)
                query_params = parse_qs(parsed.query)
                auth_code = query_params.get('code', [None])[0]
                if auth_code:
                    return auth_code
                else:
                    raise ValueError("No auth code found in URL")
            else:
                # Assume it's just the code
                return user_input.strip()
        except Exception as e:
            raise ValueError(f"Failed to extract auth code: {str(e)}")

    def get_access_token(self, auth_code):
        """Exchange auth code for access token"""
        url = "https://api-t1.fyers.in/api/v3/validate-authcode"

        # Generate appIdHash
        app_id_hash = hashlib.sha256(f"{self.app_id}:{self.secret_key}".encode()).hexdigest()

        payload = {
            "grant_type": "authorization_code",
            "appIdHash": app_id_hash,
            "code": auth_code
        }

        headers = {
            'Content-Type': 'application/json'
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()

            data = response.json()

            if data.get('s') == 'ok':
                self.access_token = data.get('access_token')
                self.auth_token = self.access_token  # In v3, access_token is auth_token

                # Save token
                token_data = {
                    'access_token': self.access_token,
                    'app_id': self.app_id,
                    'timestamp': dt.now().isoformat(),
                    'expires_in': data.get('expires_in', 86400)
                }

                with open(TOKEN_FILE, 'w') as f:
                    json.dump(token_data, f)

                log_message(f"Authentication successful. Token saved to {TOKEN_FILE}")
                return True
            else:
                error_msg = data.get('message', 'Unknown error')
                raise Exception(f"Authentication failed: {error_msg}")

        except requests.exceptions.RequestException as e:
            raise Exception(f"Network error: {str(e)}")

    def authenticate(self):
        """Complete authentication process"""
        try:
            # Check for existing valid token
            if os.path.exists(TOKEN_FILE):
                with open(TOKEN_FILE, 'r') as f:
                    token_data = json.load(f)

                # Check if token is not expired (give 1 hour buffer)
                token_time = dt.fromisoformat(token_data.get('timestamp', '2000-01-01'))
                expires_in = token_data.get('expires_in', 86400)

                # Token is valid for 24 hours, check if it's less than 23 hours old
                if (dt.now() - token_time).total_seconds() < (expires_in - 3600):
                    self.access_token = token_data.get('access_token')
                    self.auth_token = self.access_token
                    self.app_id = token_data.get('app_id')
                    log_message("Using existing valid token")
                    return True
                else:
                    log_message("Token expired, requesting new one")

            # Setup credentials if not already done
            if not self.app_id or not self.secret_key:
                self.setup_credentials()

            # Generate auth URL
            auth_url = self.generate_auth_code()

            # Get auth code from user
            user_input = input("\nPaste the redirect URL or auth code here: ").strip()
            auth_code = self.extract_auth_code(user_input)

            # Get access token
            return self.get_access_token(auth_code)

        except Exception as e:
            log_message(f"Authentication error: {str(e)}", "ERROR")
            return False


class FyersAPI:
    """Fixed Fyers API wrapper"""

    def __init__(self, app_id, access_token):
        self.app_id = app_id
        self.access_token = access_token
        self.base_url = "https://api-t1.fyers.in/api/v3"
        self.headers = {
            'Authorization': f'{self.app_id}:{self.access_token}',
            'Content-Type': 'application/json'
        }

    def _make_request(self, method, endpoint, data=None, params=None):
        """Make API request with proper error handling"""
        url = f"{self.base_url}/{endpoint}"

        try:
            if method.upper() == 'GET':
                response = requests.get(url, headers=self.headers, params=params, timeout=30)
            elif method.upper() == 'POST':
                response = requests.post(url, headers=self.headers, json=data, timeout=30)
            else:
                raise ValueError(f"Unsupported method: {method}")

            # Check for HTTP errors
            response.raise_for_status()

            # Parse response
            result = response.json()

            if result.get('s') == 'error':
                error_msg = result.get('message', 'Unknown API error')
                log_message(f"API Error: {error_msg}", "ERROR")
                return None

            return result

        except requests.exceptions.RequestException as e:
            log_message(f"Request failed: {str(e)}", "ERROR")
            return None
        except json.JSONDecodeError as e:
            log_message(f"JSON decode error: {str(e)}", "ERROR")
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

    def get_quotes(self, symbols):
        """Get quotes for symbols"""
        if isinstance(symbols, list):
            symbols = ','.join(symbols)

        params = {'symbols': symbols}
        return self._make_request('GET', 'quotes', params=params)

    def get_historical_data(self, symbol, resolution, date_format, range_from, range_to):
        """Get historical data"""
        params = {
            'symbol': symbol,
            'resolution': resolution,
            'date_format': date_format,
            'range_from': range_from,
            'range_to': range_to,
            'cont_flag': '1'
        }
        return self._make_request('GET', 'history', params=params)

    def place_order(self, order_data):
        """Place an order"""
        return self._make_request('POST', 'orders', data=order_data)


class DataProcessor:
    """Data processing for machine learning"""

    def __init__(self):
        self.scaler = StandardScaler()
        self.feature_names = []
        self.scaler_fitted = False

    def create_features(self, df):
        """Create technical features from OHLCV data"""
        data = df.copy()

        # Ensure we have required columns
        required_cols = ['open', 'high', 'low', 'close', 'volume']
        if not all(col in data.columns.str.lower() for col in required_cols):
            # Try to rename columns
            col_map = {}
            for col in data.columns:
                col_lower = col.lower()
                if 'open' in col_lower:
                    col_map[col] = 'open'
                elif 'high' in col_lower:
                    col_map[col] = 'high'
                elif 'low' in col_lower:
                    col_map[col] = 'low'
                elif 'close' in col_lower:
                    col_map[col] = 'close'
                elif 'volume' in col_lower or 'vol' in col_lower:
                    col_map[col] = 'volume'

            data = data.rename(columns=col_map)

        # Now create features
        features = {}

        # Basic price features
        features['close'] = data['close'].iloc[-1]
        features['returns'] = data['close'].pct_change().iloc[-1] if len(data) > 1 else 0

        # Moving averages
        for period in [5, 10, 20, 50]:
            ma = data['close'].rolling(window=period).mean()
            features[f'sma_{period}'] = ma.iloc[-1] if not ma.empty else 0
            features[f'ema_{period}'] = data['close'].ewm(span=period, adjust=False).mean().iloc[-1]
            if features[f'sma_{period}'] != 0:
                features[f'price_sma_ratio_{period}'] = data['close'].iloc[-1] / features[f'sma_{period}']
            else:
                features[f'price_sma_ratio_{period}'] = 0

        # RSI
        delta = data['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss.replace(0, np.nan)
        features['rsi'] = 100 - (100 / (1 + rs)).iloc[-1] if not rs.empty else 50

        # Bollinger Bands
        bb_period = 20
        bb_middle = data['close'].rolling(window=bb_period).mean()
        bb_std = data['close'].rolling(window=bb_period).std()
        features['bb_upper'] = (bb_middle + (bb_std * 2)).iloc[-1] if not bb_middle.empty else 0
        features['bb_lower'] = (bb_middle - (bb_std * 2)).iloc[-1] if not bb_middle.empty else 0
        if bb_middle.iloc[-1] != 0:
            features['bb_width'] = (features['bb_upper'] - features['bb_lower']) / bb_middle.iloc[-1]
        else:
            features['bb_width'] = 0

        if (features['bb_upper'] - features['bb_lower']) != 0:
            features['bb_position'] = (data['close'].iloc[-1] - features['bb_lower']) / (
                        features['bb_upper'] - features['bb_lower'])
        else:
            features['bb_position'] = 0.5

        # Volume indicators
        volume_sma = data['volume'].rolling(window=20).mean()
        features['volume_sma'] = volume_sma.iloc[-1] if not volume_sma.empty else 0
        if features['volume_sma'] != 0:
            features['volume_ratio'] = data['volume'].iloc[-1] / features['volume_sma']
        else:
            features['volume_ratio'] = 0

        # Convert to DataFrame
        feature_df = pd.DataFrame([features])

        # Store feature names
        self.feature_names = list(features.keys())

        return feature_df

    def prepare_training_data(self, historical_data):
        """Prepare data for model training"""
        df = historical_data.copy()

        # Calculate returns for labels
        df['returns'] = df['close'].pct_change()
        df['target'] = (df['returns'].shift(-1) > 0).astype(int)  # Predict next day direction

        # Create features for each row
        features_list = []
        targets = []

        for i in range(20, len(df) - 1):  # Skip first 20 for indicators
            window = df.iloc[i - 20:i + 1]  # Use 20 days for feature calculation
            features = self.create_features(window)
            features_list.append(features.values.flatten())
            targets.append(df['target'].iloc[i])

        if not features_list:
            return None, None, []

        X = np.array(features_list)
        y = np.array(targets)

        # Fit scaler
        self.scaler.fit(X)
        self.scaler_fitted = True
        X_scaled = self.scaler.transform(X)

        return X_scaled, y, self.feature_names

    def prepare_prediction_data(self, historical_data):
        """Prepare latest data for prediction"""
        if len(historical_data) < 20:
            return None

        latest_window = historical_data.iloc[-20:]  # Last 20 days
        features = self.create_features(latest_window)

        if self.scaler_fitted:
            features_scaled = self.scaler.transform(features)
        else:
            features_scaled = features.values

        return features_scaled


class TradingModel:
    """Trading model using XGBoost"""

    def __init__(self):
        self.model = xgb.XGBClassifier(
            n_estimators=100,
            max_depth=5,
            learning_rate=0.1,
            random_state=42,
            verbosity=0,
            n_jobs=4
        )
        self.is_trained = False

    def train(self, X_train, y_train, X_val=None, y_val=None):
        """Train the model"""
        if X_val is not None and y_val is not None:
            self.model.fit(
                X_train, y_train,
                eval_set=[(X_val, y_val)],
                verbose=False
            )
        else:
            self.model.fit(X_train, y_train, verbose=False)

        self.is_trained = True
        return self.model

    def predict(self, X):
        """Make prediction"""
        if not self.is_trained:
            raise ValueError("Model not trained")

        prediction = self.model.predict(X)
        probability = self.model.predict_proba(X)

        return prediction[0], probability[0]

    def save(self, symbol):
        """Save model to file"""
        model_file = os.path.join(MODELS_DIR, f"{symbol}_model.pkl")
        joblib.dump(self.model, model_file)
        log_message(f"Model saved to {model_file}")
        return model_file

    def load(self, symbol):
        """Load model from file"""
        model_file = os.path.join(MODELS_DIR, f"{symbol}_model.pkl")
        if os.path.exists(model_file):
            self.model = joblib.load(model_file)
            self.is_trained = True
            log_message(f"Model loaded from {model_file}")
            return True
        return False


class TradingBot:
    """Main trading bot"""

    def __init__(self, api, symbol):
        self.api = api
        self.symbol = self._format_symbol(symbol)
        self.model = TradingModel()
        self.processor = DataProcessor()
        self.positions = {}

        # Risk parameters
        self.capital_per_trade = 25000
        self.stop_loss_pct = 2.0
        self.take_profit_pct = 4.0

    def _format_symbol(self, symbol):
        """Format symbol for Fyers API"""
        # Fyers format: NSE:SYMBOL-EQ
        return f"NSE:{symbol.upper()}-EQ"

    def get_historical_data(self, days=100, resolution='D'):
        """Get historical data"""
        end_date = dt.now()
        start_date = end_date - datetime.timedelta(days=days)

        response = self.api.get_historical_data(
            symbol=self.symbol,
            resolution=resolution,
            date_format='1',
            range_from=start_date.strftime('%Y-%m-%d'),
            range_to=end_date.strftime('%Y-%m-%d')
        )

        if response and response.get('s') == 'ok':
            candles = response.get('candles', [])
            if candles:
                df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
                df.set_index('timestamp', inplace=True)
                log_message(f"Fetched {len(df)} candles for {self.symbol}")
                return df

        log_message(f"Failed to fetch data for {self.symbol}", "ERROR")
        return None

    def train(self):
        """Train model on historical data"""
        log_message(f"Training model for {self.symbol}")

        # Get historical data
        data = self.get_historical_data(days=365)
        if data is None:
            return False

        # Prepare training data
        X, y, feature_names = self.processor.prepare_training_data(data)

        if X is None or len(X) < 50:
            log_message("Insufficient data for training", "ERROR")
            return False

        # Split data
        split_idx = int(len(X) * 0.8)
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]

        # Train model
        self.model.train(X_train, y_train, X_test, y_test)

        # Evaluate
        predictions = self.model.model.predict(X_test)
        accuracy = accuracy_score(y_test, predictions)

        log_message(f"Training complete. Accuracy: {accuracy:.2%}")
        log_message(f"Training samples: {len(X_train)}, Test samples: {len(X_test)}")

        # Save model and scaler
        self.model.save(self.symbol.replace('NSE:', '').replace('-EQ', ''))

        # Save scaler
        scaler_file = os.path.join(MODELS_DIR, f"{self.symbol.replace('NSE:', '').replace('-EQ', '')}_scaler.pkl")
        with open(scaler_file, 'wb') as f:
            pickle.dump(self.processor.scaler, f)

        return True

    def get_signal(self):
        """Get trading signal"""
        log_message(f"Getting signal for {self.symbol}")

        # Try to load existing model
        symbol_name = self.symbol.replace('NSE:', '').replace('-EQ', '')
        if not self.model.is_trained:
            loaded = self.model.load(symbol_name)
            if not loaded:
                log_message("No trained model found. Please train first.", "ERROR")
                return None

        # Load scaler
        scaler_file = os.path.join(MODELS_DIR, f"{symbol_name}_scaler.pkl")
        if os.path.exists(scaler_file):
            with open(scaler_file, 'rb') as f:
                self.processor.scaler = pickle.load(f)
                self.processor.scaler_fitted = True

        # Get recent data
        data = self.get_historical_data(days=30)
        if data is None:
            return None

        # Prepare for prediction
        X = self.processor.prepare_prediction_data(data)
        if X is None:
            return None

        # Get prediction
        try:
            prediction, probability = self.model.predict(X)
            confidence = max(probability)

            signal_map = {0: 'SELL', 1: 'BUY'}
            signal = signal_map[prediction]

            # Get current price
            quote_response = self.api.get_quotes(self.symbol)
            current_price = None
            if quote_response and quote_response.get('s') == 'ok':
                quote_data = quote_response.get('d', [{}])[0]
                current_price = quote_data.get('v', {}).get('lp')

            result = {
                'symbol': self.symbol,
                'signal': signal,
                'confidence': float(confidence),
                'buy_probability': float(probability[1]),
                'sell_probability': float(probability[0]),
                'price': current_price,
                'timestamp': dt.now().strftime('%Y-%m-%d %H:%M:%S')
            }

            log_message(f"Signal: {signal} (Confidence: {confidence:.1%})")
            return result

        except Exception as e:
            log_message(f"Prediction error: {str(e)}", "ERROR")
            return None

    def execute_trade(self, signal_data):
        """Execute trade based on signal"""
        if signal_data['confidence'] < 0.6:
            log_message(f"Low confidence ({signal_data['confidence']:.1%}), skipping trade")
            return None

        signal = signal_data['signal']
        current_price = signal_data['price']

        if not current_price:
            log_message("No current price available", "ERROR")
            return None

        # Calculate position size
        quantity = max(1, int(self.capital_per_trade / current_price))

        print(f"\n📊 Trade Details:")
        print(f"  Symbol: {self.symbol}")
        print(f"  Signal: {signal}")
        print(f"  Confidence: {signal_data['confidence']:.1%}")
        print(f"  Price: ₹{current_price:.2f}")
        print(f"  Quantity: {quantity}")
        print(f"  Amount: ₹{quantity * current_price:,.2f}")

        confirm = input("\nExecute trade? (yes/no): ").strip().lower()

        if confirm != 'yes':
            log_message("Trade cancelled by user")
            return None

        # Prepare order
        side = 1 if signal == 'BUY' else 2 # 1 for Buy, 2 for Sell

        order_data = {
            "symbol": self.symbol,
            "qty": quantity,
            "type": 2,  # Market order
            "side": side,
            "productType": "INTRADAY",
            "limitPrice": 0,
            "stopPrice": 0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": "False",
            "orderTag": f"bot_{dt.now().strftime('%Y%m%d_%H%M%S')}"
        }

        response = self.api.place_order(order_data)

        if response and response.get('s') == 'ok':
            order_id = response.get('id')
            log_message(f"Order placed successfully. ID: {order_id}")

            # Record position
            self.positions[self.symbol] = {
                'order_id': order_id,
                'side': signal,
                'quantity': quantity,
                'entry_price': current_price,
                'timestamp': dt.now(),
                'stop_loss': current_price * (1 - self.stop_loss_pct / 100),
                'take_profit': current_price * (1 + self.take_profit_pct / 100)
            }

            return order_id
        else:
            error_msg = response.get('message', 'Unknown error') if response else 'No response'
            log_message(f"Order failed: {error_msg}", "ERROR")
            return None


def main():
    """Main function"""
    print("\n" + "=" * 60)
    print("FYERS ALGO TRADING SYSTEM")
    print("=" * 60)

    auth = FyersAuthV3()
    api = None
    bot = None
    current_symbol = None

    while True:
        print("\n" + "=" * 60)
        print("MAIN MENU")
        print("=" * 60)
        if current_symbol:
            print(f"Current Symbol: {current_symbol}")
            print("-" * 60)
        print("1. Authenticate with Fyers")
        print("2. Update Credentials")
        print("3. Select Stock Symbol")
        print("4. Train Model")
        print("5. Get Trading Signal")
        print("6. Execute Trade")
        print("7. Account Summary")
        print("8. Exit")
        print("=" * 60)

        choice = input("\nSelect option (1-8): ").strip()

        if choice == '1':
            # Authenticate
            if auth.authenticate():
                api = FyersAPI(auth.app_id, auth.access_token)
                profile = api.get_profile()
                if profile:
                    print(f"\n✅ Connected successfully! Client ID: {auth.app_id}")
                else:
                    print("⚠ Connection test failed, but proceeding...")

        elif choice == '2':
            # Force update credentials
            if os.path.exists(CONFIG_FILE):
                os.remove(CONFIG_FILE)
            auth.setup_credentials()

        elif choice == '3':
            # Select Symbol
            if not api:
                print("⚠ Please authenticate first (Option 1)")
                continue
            new_symbol = input("\nEnter stock symbol (e.g., RELIANCE, TCS): ").strip().upper()
            if new_symbol:
                current_symbol = new_symbol
                bot = TradingBot(api, current_symbol)
                print(f"✅ Symbol set to {current_symbol}")
            else:
                print("❌ Invalid symbol.")

        elif choice == '4':
            # Train model
            if not bot:
                print("⚠ Please select a stock symbol first (Option 3)")
                continue

            if bot.train():
                print(f"\n✅ Model trained successfully for {current_symbol}")
            else:
                print(f"\n❌ Failed to train model for {current_symbol}")

        elif choice == '5':
            # Get signal
            if not bot:
                print("⚠ Please select a stock symbol first (Option 3)")
                continue

            signal = bot.get_signal()
            if signal:
                print(f"\n📈 Trading Signal:")
                print(f"  Symbol: {signal['symbol']}")
                print(f"  Signal: {signal['signal']}")
                print(f"  Confidence: {signal['confidence']:.1%}")
                print(f"  Buy Probability: {signal['buy_probability']:.1%}")
                print(f"  Sell Probability: {signal['sell_probability']:.1%}")
                if signal['price']:
                    print(f"  Current Price: ₹{signal['price']:.2f}")
                print(f"  Time: {signal['timestamp']}")
            else:
                print("\n❌ Failed to get trading signal")

        elif choice == '6':
            # Execute trade
            if not bot:
                print("⚠ Please select a stock symbol first (Option 3)")
                continue

            signal = bot.get_signal()
            if signal:
                bot.execute_trade(signal)
            else:
                print("❌ No valid signal to execute")

        elif choice == '7':
            # Account summary
            if not api:
                print("⚠ Please authenticate first (Option 1)")
                continue

            print("\n📊 Account Summary:")
            funds = api.get_funds()
            if funds and funds.get('s') == 'ok':
                fund_data = funds.get('fund_limit', [{}])[0]
                print(f"\n💰 Funds:")
                print(f"  Total Equity: ₹{fund_data.get('equityAmount', 0):,.2f}")
                print(f"  Available Margin: ₹{fund_data.get('totalMargin', 0):,.2f}")
                print(f"  Used Margin: ₹{fund_data.get('usedMargin', 0):,.2f}")

            positions = api.get_positions()
            if positions and positions.get('s') == 'ok':
                net_positions = positions.get('netPositions', [])
                if net_positions:
                    print(f"\n📈 Positions:")
                    for pos in net_positions:
                        if pos.get('qty', 0) != 0:
                            pnl = pos.get('pl', 0)
                            print(f"  {pos.get('symbol')}: {pos.get('qty')} @ ₹{pos.get('avgPrice', 0):.2f} "
                                  f"(LTP: ₹{pos.get('ltp', 0):.2f}, P&L: ₹{pnl:+,.2f})")

            market = api.get_market_status()
            if market and market.get('s') == 'ok':
                market_data = market.get('marketStatus', [{}])[0]
                print(f"\n🏛️ Market Status:")
                print(f"  Exchange: {market_data.get('exchange', 'NSE')}")
                print(f"  Status: {market_data.get('status', 'Unknown')}")

        elif choice == '8':
            print("\n👋 Thank you for using Fyers Trading System!")
            break

        else:
            print("\n❌ Invalid option. Please try again.")

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
