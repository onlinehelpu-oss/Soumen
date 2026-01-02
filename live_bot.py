# -*- coding: utf-8 -*-
"""
Fyers Live Trading Bot using WebSockets

This script connects to the Fyers WebSocket for real-time data
and executes trades based on a pre-trained model.
"""

import os
import json
import time
import datetime
import warnings
from fyers_apiv3 import fyersModel
import joblib
import pickle
import pandas as pd
import numpy as np

# Suppress warnings
warnings.filterwarnings('ignore')

# --- Configuration ---
CONFIG_FILE = "fyers_login_details.json"
TOKENS_DIR = "AccessToken"
TODAY = str(datetime.date.today())
TOKEN_PATH = os.path.join(TOKENS_DIR, f"{TODAY}.json")

# --- Fyers API Connection ---
def get_fyers_instance():
    """Authenticates and returns a Fyers API instance."""
    if not os.path.exists(CONFIG_FILE):
        print("❌ Configuration file not found. Please run Ghost.py first to generate credentials.")
        return None

    with open(CONFIG_FILE, 'r') as f:
        creds = json.load(f)

    app_id = creds.get("api_key")

    if not os.path.exists(TOKEN_PATH):
        print("❌ Access token not found. Please run Ghost.py to authenticate and generate a token.")
        return None

    with open(TOKEN_PATH, 'r') as f:
        access_token = json.load(f)

    # Initialize FyersModel
    fyers = fyersModel.FyersModel(client_id=app_id, token=access_token, log_path=os.getcwd())

    # Test connection
    profile = fyers.get_profile()
    if profile.get('s') == 'ok':
        print(f"✅ Successfully connected to Fyers API. Welcome, {profile['data']['name']}.")
        return fyers
    else:
        print("❌ Could not connect to Fyers API. Please re-run Ghost.py to authenticate.")
        return None

# --- Main Bot Class ---
class LiveBot:
    def __init__(self, fyers_instance, symbol):
        self.fyers = fyers_instance
        self.symbol = symbol
        self.websocket = None
        self.model = None
        self.scaler = None
        self.data_df = pd.DataFrame()
        self.feature_names = []

    def load_model(self):
        """Loads the pre-trained model and scaler."""
        symbol_name = self.symbol.replace('NSE:', '').replace('-EQ', '')
        model_path = os.path.join("models", f"{symbol_name}_model.joblib")
        scaler_path = os.path.join("models", f"{symbol_name}_scaler.pkl")

        if not os.path.exists(model_path) or not os.path.exists(scaler_path):
            print(f"❌ Model or scaler not found for {self.symbol}. Please train the model using Ghost.py first.")
            return False

        print(f"🔄 Loading model from {model_path}...")
        self.model = joblib.load(model_path)

        print(f"🔄 Loading scaler from {scaler_path}...")
        with open(scaler_path, 'rb') as f:
            self.scaler = pickle.load(f)

        print("✅ Model and scaler loaded successfully.")
        return True

    def on_connect(self, wsapp):
        """Callback on successful WebSocket connection."""
        print("✅ WebSocket connected successfully.")
        wsapp.subscribe(symbol=[self.symbol], data_type="symbolData")

    def on_message(self, wsapp, message):
        """Callback for each incoming message."""
        ltp = message[0]['ltp']
        volume = message[0]['vol_traded_today']

        # Append new data
        new_row = pd.DataFrame({'Close': [ltp], 'Volume': [volume]}, index=[pd.to_datetime(time.time(), unit='s')])
        self.data_df = pd.concat([self.data_df, new_row])

        # Keep the DataFrame to a reasonable size (e.g., last 100 ticks)
        if len(self.data_df) > 100:
            self.data_df = self.data_df.iloc[-100:]

        # Get signal if we have enough data
        if len(self.data_df) > 20:
            self.get_signal()

    def on_error(self, wsapp, error):
        """Callback for WebSocket errors."""
        print(f"❌ WebSocket Error: {error}")

    def on_close(self, wsapp):
        """Callback when WebSocket connection is closed."""
        print("🔌 WebSocket connection closed.")

    def get_signal(self):
        """Analyzes the current data and generates a signal."""
        # Calculate indicators
        data_with_features = self.calculate_indicators(self.data_df)

        # Prepare latest features
        X_latest = data_with_features[self.feature_names].iloc[-1:].copy()
        X_latest = X_latest.fillna(0)
        X_scaled = self.scaler.transform(X_latest)

        # Make prediction
        prediction, probability = self.model.predict(X_scaled)
        confidence = max(probability[0])

        signal = 'BUY' if prediction[0] == 1 else 'SELL'

        print(f"Signal: {signal} | Confidence: {confidence:.2f}")

        # Execute trade if confidence is high
        if confidence > 0.75:
            self.execute_trade(signal, data_with_features['Close'].iloc[-1])

    def execute_trade(self, signal, price):
        """Placeholder for trade execution logic."""
        print(f"--- EXECUTE TRADE ---")
        print(f"Symbol: {self.symbol}")
        print(f"Signal: {signal}")
        print(f"Price: {price}")
        print(f"---------------------")
        # NOTE: Real order placement is disabled.
        # To enable, uncomment the following lines and adjust parameters.
        # order_data = {
        #     "symbol": self.symbol,
        #     "qty": 1,
        #     "type": 2,  # Market order
        #     "side": 1 if signal == 'BUY' else 2,
        #     "productType": "INTRADAY",
        # }
        # response = self.fyers.place_order(order_data)
        # print(response)

    def calculate_indicators(self, df):
        """Calculate technical indicators for the current data."""
        data = df.copy()

        close_prices = data['Close']
        if isinstance(close_prices, pd.DataFrame):
            close_prices = close_prices.iloc[:, 0]

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

        volume = data['Volume']
        if isinstance(volume, pd.DataFrame):
            volume = volume.iloc[:, 0]
        data['Volume_SMA'] = volume.rolling(window=20).mean()
        data['Volume_Ratio'] = volume / data['Volume_SMA'].replace(0, np.nan)

        data = data.fillna(method='ffill').fillna(method='bfill').fillna(0)

        if not self.feature_names:
            exclude_cols = ['Open', 'High', 'Low', 'Close', 'Volume', 'Returns']
            self.feature_names = [col for col in data.columns if col not in exclude_cols]
            # Ensure the feature names match the trained model
            self.feature_names = [f for f in self.feature_names if f in self.scaler.feature_names_in_]

        return data

    def start(self):
        """Starts the WebSocket connection."""
        if not self.load_model():
            return

        print("\nStarting live bot...")

        ws_access_token = f"{self.fyers.client_id}:{self.fyers.token}"

        self.websocket = fyersModel.FyersSocket(access_token=ws_access_token, log_path=os.getcwd())
        self.websocket.on_connect = self.on_connect
        self.websocket.on_message = self.on_message
        self.websocket.on_error = self.on_error
        self.websocket.on_close = self.on_close

        self.websocket.connect()

        try:
            while True:
                time.sleep(1)
        finally:
            self.stop()

    def stop(self):
        """Stops the WebSocket connection."""
        if self.websocket:
            print("\nStopping WebSocket...")
            self.websocket.close_connection()

# --- Main Execution ---
def main():
    """Main function to run the live bot"""
    print("\n" + "=" * 70)
    print("FYERS LIVE TRADING BOT")
    print("=" * 70)

    fyers_instance = get_fyers_instance()

    if fyers_instance:
        symbol_input = input("\nEnter a SINGLE stock symbol to trade (e.g., RELIANCE): ").strip().upper()
        if not symbol_input:
            print("❌ No symbol entered. Exiting.")
            return

        symbol = f"NSE:{symbol_input}-EQ"

        bot = LiveBot(fyers_instance, symbol)
        bot.start()

    print("\nLive bot has been stopped.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nProgram interrupted by user. Shutting down.")
    except Exception as e:
        print(f"\n❌ An unexpected error occurred: {e}")
