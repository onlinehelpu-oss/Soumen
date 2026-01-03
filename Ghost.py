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
import traceback

# Configuration
CONFIG_FILE = "fyers_login_details.json"
TOKENS_DIR = "AccessToken"
MODELS_DIR = "models"
DATA_DIR = "data"
LOG_DIR = "logs"

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
        # Use a direct print here to avoid recursion with log_message
        print("[INFO] Fetching current date from worldtimeapi.org...")
        response = requests.get("http://worldtimeapi.org/api/timezone/Etc/UTC", timeout=5)
        response.raise_for_status()
        data = response.json()
        utc_datetime_str = data['utc_datetime'].split('.')[0] # Remove microseconds
        _CURRENT_DATE = dt.fromisoformat(utc_datetime_str).date()
        print(f"[INFO] Successfully fetched API date: {_CURRENT_DATE}")
    except Exception as e:
        print(f"[WARNING] Could not fetch date from API: {e}. Falling back to system date.")
        _CURRENT_DATE = datetime.date.today()
        print(f"[WARNING] Using system date: {_CURRENT_DATE}")

    return _CURRENT_DATE

def get_token_path():
    """Generates the token path using the potentially future-dated system clock."""
    # The token file name should reflect the user's system date, as that's when they think they authenticated.
    today_str = datetime.date.today().strftime("%Y-%m-%d")
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
    """Log messages to file. Uses local system time for file naming."""
    # BUG FIX: Use local system date for log file naming to prevent recursion
    today_str = datetime.date.today().strftime("%Y-%m-%d")
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
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                creds = json.load(f)
            print("\n" + "=" * 70 + "\nLOADED EXISTING CREDENTIALS\n" + "=" * 70)
            print(f"App ID: {creds.get('api_key')}\nRedirect URL: {creds.get('redirect_url')}")
            if input("\nUse these credentials? (y/n): ").strip().lower() == 'y':
                self.app_id = creds.get("api_key")
                self.secret_key = creds.get("api_secret")
                self.redirect_url = creds.get("redirect_url")
                return True
            else:
                print("Please create new configuration...")
        print("\n" + "=" * 70 + "\nENTER YOUR CREDENTIALS\n" + "=" * 70)
        self.app_id = input("Enter APP ID (e.g., ABCDE12345-100): ").strip()
        self.secret_key = input("Enter SECRET ID: ").strip()
        self.redirect_url = input("Enter Redirect URL (must match app): ").strip()
        if not all([self.app_id, self.secret_key, self.redirect_url]):
            print("❌ All fields are required"); return False
        if input("Save to config file? (Y/N): ").strip().upper() == "Y":
            with open(CONFIG_FILE, "w") as f:
                json.dump({"api_key": self.app_id, "api_secret": self.secret_key, "redirect_url": self.redirect_url}, f, indent=2)
            print(f"✅ Saved to '{CONFIG_FILE}'")
        return True

    def build_auth_url(self, state="generate_token"):
        return f"https://api-t1.fyers.in/api/v3/generate-authcode?client_id={quote(self.app_id)}&redirect_uri={quote(self.redirect_url, safe='')}&response_type=code&state={quote(state)}"

    def extract_code(self, user_input):
        if user_input.startswith("http"):
            code = parse_qs(urlparse(user_input).query).get("code", [None])[0]
            if not code: raise ValueError("No 'code' param found in URL")
            return code
        return user_input

    def sha256_appIdHash(self):
        return hashlib.sha256(f"{self.app_id}:{self.secret_key}".encode("utf-8")).hexdigest()

    def validate_authcode(self, auth_code, max_retries=5):
        url = "https://api-t1.fyers.in/api/v3/validate-authcode"
        payload = {"grant_type": "authorization_code", "appIdHash": self.sha256_appIdHash(), "code": auth_code}
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=20)
                if response.status_code == 503:
                    time.sleep(min(2 ** attempt, 30)); continue
                response.raise_for_status()
                data = response.json()
                if data.get("s") == "error": raise RuntimeError(f"Fyers error {data.get('code', '')}: {data.get('message', 'Unknown')}")
                return data
            except requests.RequestException as e:
                if attempt == max_retries: raise
                time.sleep(min(2 ** attempt, 30))

    def authenticate(self):
        print("\n" + "=" * 70 + "\nSTARTING AUTHENTICATION\n" + "=" * 70)
        try:
            if not self.load_or_prompt_creds(): return False
            token_path = get_token_path()
            if os.path.exists(token_path):
                try:
                    with open(token_path, "r") as f:
                        access_token = json.load(f)
                    if FyersAPI(self.app_id, access_token).test_connection():
                        self.access_token = access_token
                        print(f"✅ Using existing token from {token_path}"); return True
                    else: print("⚠ Existing token expired or invalid.")
                except Exception: print("⚠ Corrupted token file.")

            auth_url = self.build_auth_url()
            print(f"\n🔗 OPEN this URL in your browser:\n{'-'*70}\n{auth_url}\n{'-'*70}")
            try: webbrowser.open(auth_url); print("✓ Browser opened")
            except: print("⚠ Could not open browser")

            print("\n📋 FOLLOW IN BROWSER:\n1. Login\n2. Authorize\n3. Copy the FULL redirect URL or auth code")
            user_input = input("\n📥 Paste the URL or auth code: ").strip()
            if not user_input: print("❌ No input provided"); return False

            auth_code = self.extract_code(user_input)
            print("\n🔄 Getting access token...")
            token_resp = self.validate_authcode(auth_code)
            self.access_token = token_resp.get("access_token")
            if not self.access_token: print("❌ No access token in response"); return False

            with open(token_path, "w") as f: json.dump(self.access_token, f)
            print(f"\n✅ AUTHENTICATION SUCCESSFUL!\nToken saved to {token_path}")
            return True
        except Exception as e:
            print(f"❌ Authentication failed: {e}"); return False

class FyersAPI:
    def __init__(self, app_id, access_token):
        self.app_id = app_id; self.access_token = access_token
        self.headers = {'Authorization': f'{self.app_id}:{self.access_token}', 'Content-Type': 'application/json'}

    def test_connection(self):
        return self.get_profile() is not None
    def _make_request(self, method, endpoint, data=None, params=None):
        try:
            response = requests.request(method.upper(), f"https://api-t1.fyers.in/api/v3/{endpoint}", headers=self.headers, json=data, params=params, timeout=30)
            response.raise_for_status(); return response.json()
        except requests.exceptions.RequestException as e:
            log_message(f"API Request failed for {endpoint}: {e}", "ERROR"); return None
    def get_profile(self): return self._make_request('GET', 'profile')
    def get_historical_data(self, symbol, days=365, resolution="D"):
        end_date = get_current_date()
        start_date = end_date - datetime.timedelta(days=days)
        params = {'symbol': symbol, 'resolution': resolution, 'date_format': '0',
                  'range_from': start_date.strftime('%Y-%m-%d'), 'range_to': end_date.strftime('%Y-%m-%d'), 'cont_flag': '1'}
        data_url = "https://api-t1.fyers.in/data/history"
        try:
            response = requests.get(data_url, headers=self.headers, params=params, timeout=30)
            response.raise_for_status(); data = response.json()
            if data.get("s") == "ok": return data
            else: log_message(f"API Error: {data.get('message', 'Unknown')}", "ERROR"); return None
        except requests.exceptions.RequestException as e:
            if hasattr(e, 'response') and e.response is not None and e.response.status_code == 422:
                log_message("CRITICAL: Fyers API rejected request (422 Error). Your system clock might be incorrect.", "ERROR")
            else: log_message(f"Request failed: {e}", "ERROR")
            return None

class DataProcessor:
    def __init__(self): self.scaler = StandardScaler(); self.feature_names = []
    def calculate_indicators(self, df):
        data = df.copy()
        if 'Close' not in data.columns and 'close' in data.columns:
            data = data.rename(columns={'close': 'Close', 'open': 'Open', 'high': 'High', 'low': 'Low', 'volume': 'Volume'})
        close = data['Close'].squeeze(); volume = data['Volume'].squeeze()
        data['Returns'] = close.pct_change()
        for p in [5, 10, 20, 50]:
            data[f'SMA_{p}'] = close.rolling(p).mean(); data[f'EMA_{p}'] = close.ewm(span=p, adjust=False).mean()
            data[f'Price_SMA_Ratio_{p}'] = close / data[f'SMA_{p}'].replace(0, np.nan)
        delta = close.diff(); gain = (delta.where(delta > 0, 0)).rolling(14).mean(); loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        data['RSI'] = 100 - (100 / (1 + (gain / loss.replace(0, np.nan))))
        mid = close.rolling(20).mean(); std = close.rolling(20).std()
        data['BB_Upper'] = mid + (std * 2); data['BB_Lower'] = mid - (std * 2)
        data['BB_Width'] = (data['BB_Upper'] - data['BB_Lower']) / mid.replace(0, np.nan)
        data['BB_Position'] = (close - data['BB_Lower']) / (data['BB_Upper'] - data['BB_Lower']).replace(0, np.nan)
        data['Volume_SMA'] = volume.rolling(20).mean(); data['Volume_Ratio'] = volume / data['Volume_SMA'].replace(0, np.nan)
        data = data.fillna(method='ffill').fillna(method='bfill').fillna(0)
        self.feature_names = [c for c in data.columns if c not in ['Open','High','Low','Close','Volume','Returns']]
        return data
    def create_labels(self, df, n=1, t=0.002): return (df['Close'].squeeze().pct_change(n).shift(-n) > t).astype(int).fillna(0)
    def prepare_features(self, df, labels):
        X = df[self.feature_names].copy().fillna(0).replace([np.inf, -np.inf], 0)
        idx = X.index.intersection(labels.index); X, y = X.loc[idx], labels.loc[idx]
        return self.scaler.fit_transform(X), y.values

class TradingModel:
    def __init__(self): self.model = xgb.XGBClassifier(n_estimators=200, max_depth=6, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, random_state=42, n_jobs=-1, verbosity=0)
    def train(self, X_train, y_train, X_val=None, y_val=None): self.model.fit(X_train, y_train, eval_set=[(X_val, y_val)] if X_val else None, verbose=100)
    def predict(self, X): return self.model.predict(X), self.model.predict_proba(X)
    def save(self, s): f = os.path.join(MODELS_DIR, f"{s.replace(':', '_').replace('-', '_')}_model.joblib"); joblib.dump(self.model, f); return f
    def load(self, s): f = os.path.join(MODELS_DIR, f"{s.replace(':', '_').replace('-', '_')}_model.joblib"); self.model = joblib.load(f) if os.path.exists(f) else None; return self.model is not None

class TradingBot:
    def __init__(self, api, symbol): self.api = api; self.raw_symbol = symbol; self.fyers_symbol = self._format_fyers_symbol(symbol); self.model = TradingModel(); self.processor = DataProcessor()
    def _format_fyers_symbol(self, s):
        s = s.upper().replace('.NS', '')
        if any(k in s for k in ["CRUDEOIL","NATURALGAS","GOLD","SILVER","COPPER","ZINC","LEAD","NICKEL","ALUMINIUM"]):
            log_message(f"MCX symbol {s} detected. Using generic future format.", "WARNING"); return f"MCX:{s}M1"
        return f"NSE:{s}-EQ"
    def fetch_historical_data_fyers(self, d=365, r="D"):
        try:
            resp = self.api.get_historical_data(symbol=self.fyers_symbol, days=d, resolution=r)
            if resp and resp.get("s") == "ok" and resp.get("candles"):
                df = pd.DataFrame(resp["candles"], columns=['timestamp','Open','High','Low','Close','Volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s'); df.set_index('timestamp', inplace=True)
                log_message(f"Fetched {len(df)} candles for {self.fyers_symbol} from Fyers"); return df
        except Exception as e: log_message(f"Fyers API error for {self.fyers_symbol}: {e}", "ERROR")
        return None
    def fetch_historical_data_yahoo(self, d=365):
        if not self.fyers_symbol.startswith("NSE:"): log_message(f"Yahoo fallback skipped for non-NSE: {self.fyers_symbol}", "WARNING"); return None
        try:
            import yfinance as yf
            sym = f"{self.raw_symbol}.NS"; log_message(f"Trying Yahoo Finance for {sym}")
            end = get_current_date(); start = end - datetime.timedelta(days=d)
            data = yf.download(sym, start=start, end=end, progress=False)
            if not data.empty: log_message(f"Fetched {len(data)} candles for {self.raw_symbol} from Yahoo"); return data.rename(columns=str.capitalize)
        except Exception as e: log_message(f"Yahoo Finance error for {self.raw_symbol}: {e}", "ERROR")
        return None
    def fetch_historical_data(self, d=365, r="D"):
        data = self.fetch_historical_data_fyers(d, r)
        if data is None or len(data) < 50:
            log_message("Fyers data unavailable, trying Yahoo...", "WARNING"); data = self.fetch_historical_data_yahoo(d)
        if data is None: log_message(f"Failed to fetch any data for {self.raw_symbol}", "ERROR")
        return data
    def train_model(self):
        data = self.fetch_historical_data(365)
        if data is None or len(data) < 100: log_message(f"Insufficient data for {self.raw_symbol}", "ERROR"); return False
        features = self.processor.calculate_indicators(data); labels = self.processor.create_labels(features)
        X, y = self.processor.prepare_features(features, labels)
        if len(X) < 50: log_message(f"Not enough samples for {self.raw_symbol}", "ERROR"); return False
        split = int(len(X) * 0.8); X_train, X_test, y_train, y_test = X[:split], X[split:], y[:split], y[split:]
        log_message(f"Training {self.raw_symbol} with {len(X_train)} samples...")
        self.model.train(X_train, y_train, X_test, y_test)
        acc = accuracy_score(y_test, self.model.predict(X_test)[0])
        log_message(f"Model Accuracy for {self.raw_symbol}: {acc:.2%}")
        model_path = self.model.save(self.raw_symbol); scaler_path = os.path.join(MODELS_DIR, f"{self.raw_symbol}_scaler.pkl")
        with open(scaler_path, 'wb') as f: pickle.dump(self.processor.scaler, f)
        log_message(f"Model saved: {model_path}\nScaler saved: {scaler_path}"); return True
    def get_signal(self):
        if not self.model.load(self.raw_symbol): log_message(f"No model for {self.raw_symbol}", "ERROR"); return None
        scaler_path = os.path.join(MODELS_DIR, f"{self.raw_symbol}_scaler.pkl")
        if not os.path.exists(scaler_path): log_message(f"No scaler for {self.raw_symbol}", "ERROR"); return None
        with open(scaler_path, 'rb') as f: self.processor.scaler = pickle.load(f)
        data = self.fetch_historical_data(60);
        if data is None or len(data) < 20: return None
        features = self.processor.calculate_indicators(data)
        X = self.processor.scaler.transform(features[self.processor.feature_names].iloc[-1:].copy().fillna(0))
        pred, prob = self.model.predict(X); conf = max(prob[0]); sig = 'BUY' if pred[0] == 1 else 'SELL'
        price = data['Close'].iloc[-1]
        try:
            quote = self.api.get_quotes(self.fyers_symbol)
            if quote and quote.get('s') == 'ok': price = quote['d'][0]['v'].get('lp', price)
        except: pass
        return {'symbol':self.fyers_symbol,'signal':sig,'confidence':float(conf),'price':price,'timestamp':dt.now().strftime('%Y-%m-%d %H:%M:%S')}

def main():
    print("\n" + "="*70 + "\nFYERS ALGO TRADING SYSTEM - FINAL RESILIENT VERSION\n" + "="*70)
    print("\n⚠ For data fallback, run: pip install yfinance\n" + "="*70)
    auth = FyersAuthV3(); api = None
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
            if not api: print("\n⚠ Please authenticate first."); continue
            symbols = [input("\nEnter symbol: ").strip().upper()] if choice == '2' else SYMBOL_LIST
            if choice == '3' and input(f"Train all {len(SYMBOL_LIST)} symbols? (y/n): ").lower() != 'y': continue
            succ, fail = 0, []
            for i, s in enumerate(symbols):
                print("\n" + "="*70 + f"\n[{i+1}/{len(symbols)}] TRAINING: {s}\n" + "="*70)
                try:
                    if TradingBot(api, s).train_model(): succ += 1
                    else: fail.append(s)
                except Exception as e: log_message(f"CRITICAL ERROR training {s}: {e}", "ERROR"); fail.append(s)
                time.sleep(1)
            print("\n" + "="*70 + "\nTRAINING SUMMARY\n" + "="*70 + f"\n✅ Successful: {succ}\n❌ Failed: {len(fail)}")
            if fail: print(f"Failed symbols: {', '.join(fail)}")
        elif choice == '4':
            if not api: print("\n⚠ Please authenticate first."); continue
            s = input("\nEnter symbol for signal: ").strip().upper()
            sig = TradingBot(api, s).get_signal()
            if sig:
                print("\n" + "="*70 + "\n📊 TRADING SIGNAL\n" + "="*70)
                print(f"Symbol: {sig['symbol']}\nSignal: {sig['signal']}\nConfidence: {sig['confidence']:.1%}")
                if sig['price']: print(f"Price: ₹{sig['price']:.2f}")
                print(f"Time: {sig['timestamp']}")
            else: print("\n❌ Failed to get signal.")
        elif choice == '5':
            if not api: print("\n⚠ Please authenticate first."); continue
            print("\n" + "="*70 + "\n📊 ACCOUNT SUMMARY\n" + "="*70)
            funds = api._make_request('GET', 'funds') # Using internal method to bypass potential class changes
            if funds and funds.get('s') == 'ok': print(f"\n💰 Funds: Available Margin: ₹{funds['fund_limit'][0].get('equityAmount', 0):,.2f}")
            else: print("\n💰 Could not fetch funds.")
            pos = api._make_request('GET', 'positions')
            if pos and pos.get('s') == 'ok' and pos.get('netPositions'):
                print(f"\n📈 Open Positions:")
                for p in pos['netPositions']: print(f"  - {p['symbol']}: Qty={p['qty']}, P&L=₹{p['pl']:.2f}")
            else: print(f"\n📈 No open positions.")
        elif choice == '6': print("\n👋 Exiting..."); break
        else: print("\n❌ Invalid option")
        input("\nPress Enter to continue...")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠ Program interrupted")
    except Exception as e:
        print(f"\n❌ Unhandled Error: {e}")
        traceback.print_exc()
