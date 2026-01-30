"""
ALL-IN-ONE SUPER-TREND + VWAP TRADING BOT
=========================================

**Disclaimer:**
This bot is intended for educational and paper trading purposes.
Live trading involves significant financial risk. Use at your own discretion.

**Strategy (SuperTrend + VWAP Confluence):**
- **Timeframe:** 5 Minutes.
- **Long Signal (Buy CE):**
  - Price closes ABOVE SuperTrend(10, 3).
  - Price is ABOVE VWAP.
  - RSI > 55 (Momentum).
- **Short Signal (Buy PE):**
  - Price closes BELOW SuperTrend(10, 3).
  - Price is BELOW VWAP.
  - RSI < 45 (Momentum).
- **Exits:**
  - SuperTrend Reversal.
  - Stop Loss (ATR based).
  - Target (1:2 Risk:Reward).

**Assets:** NSE Indices (NIFTY, BANKNIFTY) -> Options.
"""

import os
import sys
import json
import time
import math
import argparse
import webbrowser
import threading
import traceback
import datetime as dt
import pytz
from urllib.parse import urlparse, parse_qs
import warnings
import re
from typing import Optional, Dict, List, Tuple

# Suppress specific warnings
warnings.filterwarnings("ignore", message=".*pkg_resources is deprecated.*")

import pandas as pd
import numpy as np

# Try to import real Fyers library
try:
    from fyers_apiv3 import fyersModel
    from fyers_apiv3.FyersWebsocket import data_ws

    HAS_FYERS = True
except ImportError:
    print("⚠️ fyers_apiv3 not installed. Running in limited/mock mode.")
    HAS_FYERS = False


    # Mock classes for dependency check passes
    class MockFyersModel:
        def __init__(self, **kwargs):
            self.client_id = kwargs.get("client_id", "MOCK")
            self.token = kwargs.get("token", "MOCK")

        def history(self, data): return {"s": "ok", "candles": []}

        def quotes(self, data): return {"s": "ok", "d": []}

        def option_chain(self, data): return {"s": "ok", "d": []}


    class MockSessionModel:
        def __init__(self, **kwargs): pass

        def generate_authcode(self): return "http://mock-login-url"

        def set_token(self, token): pass

        def generate_token(self): return {"s": "ok", "access_token": "MOCK_TOKEN", "refresh_token": "MOCK_REFRESH"}


    class MockDataSocket:
        def __init__(self, **kwargs): pass

        def connect(self): print("[MOCK] WS Connected")

        def subscribe(self, symbols): print(f"[MOCK] Subscribed to {len(symbols)} symbols")


    fyersModel = type("fyersModel", (), {"FyersModel": MockFyersModel, "SessionModel": MockSessionModel})
    data_ws = type("data_ws", (), {"FyersDataSocket": MockDataSocket})


# ============================================================================
# --- SECTION 1: CONFIGURATION ---
# ============================================================================

class BotConfig:
    """Consolidated configuration for all bot functionalities."""
    # --- File Names ---
    LOGIN_DETAILS_FILE = "fyers_login_details.json"
    TOKENS_STORE = "tokens_store.json"
    TOKENS_DIR = "AccessToken"
    TRADE_LOG_FILE = "paper_trade_log.csv"

    # --- Strategy Settings ---
    TIMEFRAME_MIN = 5

    # SuperTrend
    ST_PERIOD = 10
    ST_MULTIPLIER = 3

    # RSI
    RSI_PERIOD = 14
    RSI_LONG_MIN = 55
    RSI_SHORT_MAX = 45

    # ADX (Advanced Model)
    ADX_PERIOD = 14
    ADX_THRESHOLD = 20

    # Gamma Blast (Short-Term ROC + Option Chain)
    ROC_PERIOD = 2  # Short term momentum
    ROC_THRESHOLD = 0.15  # % Move required
    OI_CHANGE_THRESHOLD = 500000  # Significant OI Chg
    GAMMA_STOP_LOSS = 15  # Points

    # Risk Management
    R_MULTIPLIER = 2.0
    ATR_PERIOD = 14
    ATR_SL_MULT = 1.5
    OPTION_SL_PCT = 0.20  # 20% Stop Loss on Option Premium

    # --- Position Sizing ---
    PAPER_BALANCE = 100000
    ALLOCATION_AMOUNT = 20000  # Capital per trade

    # --- Session Timing ---
    ENTRY_CUTOFF_NSE = dt.time(15, 0)
    EXIT_ALL_NSE = dt.time(15, 15)

    # --- Watchlist (Indices) ---
    SYMBOLS = [
        'NSE:NIFTY50-INDEX',
        'NSE:NIFTYBANK-INDEX'
    ]

    # Map Index to Option Symbol Base
    INDEX_MAP = {
        'NSE:NIFTY50-INDEX': 'NIFTY',
        'NSE:NIFTYBANK-INDEX': 'BANKNIFTY',
        'NSE:FINNIFTY-INDEX': 'FINNIFTY'
    }

    LOT_SIZES = {
        'NIFTY': 50,
        'BANKNIFTY': 15,
        'FINNIFTY': 40
    }


# ============================================================================
# --- SECTION 2: CREDENTIAL SETUP & AUTHENTICATION ---
# ============================================================================

def _read_json(path):
    if os.path.exists(path):
        with open(path, 'r') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}


def _write_json(path, data):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def setup_credentials(app_id, secret_key, redirect_url):
    """Creates the fyers_login_details.json file."""
    if not all([app_id, secret_key, redirect_url]):
        print("ERROR: App ID, Secret Key, and Redirect URL are all required.")
        return

    credentials = {
        "api_key": app_id,
        "api_secret": secret_key,
        "redirect_url": redirect_url
    }

    file_name = BotConfig.LOGIN_DETAILS_FILE
    try:
        _write_json(file_name, credentials)
        print(f"✅ Successfully created '{file_name}' with your credentials.")
    except Exception as e:
        print(f"❌ An error occurred while creating the file: {e}")


def get_fyers_instance():
    creds = _read_json(BotConfig.LOGIN_DETAILS_FILE)
    if not creds:
        print(f"❌ Missing '{BotConfig.LOGIN_DETAILS_FILE}'. Please run 'setup' mode first.")
        sys.exit(1)

    client_id = creds.get("api_key")
    secret_key = creds.get("api_secret")
    redirect_uri = creds.get("redirect_url")

    today_str = str(dt.date.today())
    today_token_path = os.path.join(BotConfig.TOKENS_DIR, f"{today_str}.json")

    # 1. Try Today's Token
    access_token = _read_json(today_token_path)
    if access_token and isinstance(access_token, str):
        return fyersModel.FyersModel(client_id=client_id, token=access_token, log_path="", is_async=False)

        # 2. Try Refresh Token
    store = _read_json(BotConfig.TOKENS_STORE)
    refresh_token = store.get("refresh_token")

    if refresh_token:
        print("🔄 Attempting login via Refresh Token...")
        try:
            session = fyersModel.SessionModel(
                client_id=client_id,
                secret_key=secret_key,
                redirect_uri=redirect_uri,
                response_type="code",
                grant_type="refresh_token"
            )
            session.set_token(refresh_token)
            response = session.generate_token()

            if response.get("s") == "ok":
                new_access_token = response["access_token"]
                new_refresh_token = response.get("refresh_token")

                _write_json(today_token_path, new_access_token)
                if new_refresh_token:
                    _write_json(BotConfig.TOKENS_STORE, {"refresh_token": new_refresh_token})

                print("✅ Access Token Refreshed.")
                return fyersModel.FyersModel(client_id=client_id, token=new_access_token, log_path="", is_async=False)
            else:
                print(f"⚠️ Refresh failed: {response.get('message')}")
        except Exception as e:
            print(f"⚠️ Refresh error: {e}")

            # 3. Manual Login
    print("👉 Initiating Manual Login...")
    session = fyersModel.SessionModel(
        client_id=client_id,
        secret_key=secret_key,
        redirect_uri=redirect_uri,
        response_type="code",
        grant_type="authorization_code"
    )

    auth_url = session.generate_authcode()
    print(f"\nOpen this URL to login:\n{auth_url}")
    webbrowser.open(auth_url, new=1)

    auth_code = input("\nPaste the auth_code here: ").strip()
    session.set_token(auth_code)
    response = session.generate_token()

    if response.get("s") == "ok":
        access_token = response["access_token"]
        refresh_token = response.get("refresh_token")

        _write_json(today_token_path, access_token)
        if refresh_token:
            _write_json(BotConfig.TOKENS_STORE, {"refresh_token": refresh_token})

        print("✅ Login Successful.")
        return fyersModel.FyersModel(client_id=client_id, token=access_token, log_path="", is_async=False)
    else:
        print(f"❌ Login Failed: {response}")
        sys.exit(1)

    # ============================================================================


# --- SECTION 3: STRATEGY LOGIC ---
# ============================================================================

class Greeks:
    """Simplified Black-Scholes Model for Option Greeks."""

    @staticmethod
    def norm_cdf(x):
        return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

    @staticmethod
    def norm_pdf(x):
        return math.exp(-0.5 * x ** 2) / math.sqrt(2.0 * math.pi)

    @staticmethod
    def calculate(S, K, T, r, sigma, type="CE"):
        """
        S: Spot Price
        K: Strike Price
        T: Time to Expiry (in years)
        r: Risk-free rate (decimal, e.g., 0.05)
        sigma: Volatility (decimal, e.g., 0.20)
        type: "CE" or "PE"

        Returns: (Delta, Theta)
        """
        if T <= 0 or sigma <= 0: return 0.5, 0

        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)

        if type == "CE":
            delta = Greeks.norm_cdf(d1)
            # Theta per day approximation
            theta = (- (S * Greeks.norm_pdf(d1) * sigma) / (2 * math.sqrt(T)) - r * K * math.exp(-r * T) * Greeks.norm_cdf(d2)) / 365.0
        else:
            delta = -Greeks.norm_cdf(-d1)
            theta = (- (S * Greeks.norm_pdf(d1) * sigma) / (2 * math.sqrt(T)) + r * K * math.exp(-r * T) * Greeks.norm_cdf(-d2)) / 365.0

        return delta, theta

class Strategy:
    @staticmethod
    def calculate_indicators(df: pd.DataFrame):
        """Calculates SuperTrend, VWAP, RSI, ATR."""
        if df.empty: return df

        # --- ATR ---
        df['tr0'] = abs(df['high'] - df['low'])
        df['tr1'] = abs(df['high'] - df['close'].shift())
        df['tr2'] = abs(df['low'] - df['close'].shift())
        df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
        df['atr'] = df['tr'].rolling(window=BotConfig.ATR_PERIOD).mean()

        # --- SuperTrend ---
        # Basic calculation
        hl2 = (df['high'] + df['low']) / 2
        df['st_upper'] = hl2 + (BotConfig.ST_MULTIPLIER * df['atr'])
        df['st_lower'] = hl2 - (BotConfig.ST_MULTIPLIER * df['atr'])

        # Initialize columns
        df['supertrend'] = np.nan
        df['st_dir'] = 1  # 1=Green/Up, -1=Red/Down

        # Iterative calculation for ST (Vectorization is hard due to recursive logic)
        # Using a simple loop for clarity and correctness
        st = df['supertrend'].copy()
        st_dir = df['st_dir'].copy()

        # Need prev values
        upper_band = df['st_upper'].values
        lower_band = df['st_lower'].values
        close = df['close'].values

        final_upper = np.zeros(len(df))
        final_lower = np.zeros(len(df))
        trend = np.zeros(len(df))  # 1 Up, -1 Down

        for i in range(1, len(df)):
            if np.isnan(upper_band[i]): continue

            # Upper Band Logic
            if final_upper[i - 1] == 0:
                final_upper[i] = upper_band[i]
            else:
                if (upper_band[i] < final_upper[i - 1]) or (close[i - 1] > final_upper[i - 1]):
                    final_upper[i] = upper_band[i]
                else:
                    final_upper[i] = final_upper[i - 1]

            # Lower Band Logic
            if final_lower[i - 1] == 0:
                final_lower[i] = lower_band[i]
            else:
                if (lower_band[i] > final_lower[i - 1]) or (close[i - 1] < final_lower[i - 1]):
                    final_lower[i] = lower_band[i]
                else:
                    final_lower[i] = final_lower[i - 1]

            # Trend Logic
            prev_trend = trend[i - 1] if trend[i - 1] != 0 else 1

            if prev_trend == 1:
                if close[i] < final_lower[i]:
                    trend[i] = -1
                else:
                    trend[i] = 1
            else:
                if close[i] > final_upper[i]:
                    trend[i] = 1
                else:
                    trend[i] = -1

        df['st_trend'] = trend
        # SuperTrend Value is Lower Band when Up, Upper Band when Down
        df['supertrend'] = np.where(df['st_trend'] == 1, final_lower, final_upper)

        # --- RSI ---
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=BotConfig.RSI_PERIOD).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=BotConfig.RSI_PERIOD).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        # --- VWAP ---
        if 'timestamp' in df.columns:
            df['date'] = df['timestamp'].dt.date

            def calc_vwap(group):
                cum_vol = group['volume'].cumsum()
                cum_pv = (group['close'] * group['volume']).cumsum()
                return cum_pv / cum_vol

            df['vwap'] = df.groupby('date')[['close', 'volume']].apply(lambda x: calc_vwap(x)).reset_index(level=0,
                                                                                                           drop=True)

        # --- ADX (Advanced Model) ---
        df['up_move'] = df['high'] - df['high'].shift(1)
        df['down_move'] = df['low'].shift(1) - df['low']

        df['plus_dm'] = np.where((df['up_move'] > df['down_move']) & (df['up_move'] > 0), df['up_move'], 0)
        df['minus_dm'] = np.where((df['down_move'] > df['up_move']) & (df['down_move'] > 0), df['down_move'], 0)

        # Wilder's Smoothing (alpha = 1/n)
        alpha = 1 / BotConfig.ADX_PERIOD

        # Initialize with SMA for the first value if needed, but EWM handles start automatically
        df['tr_smooth'] = df['tr'].ewm(alpha=alpha, adjust=False).mean()
        df['plus_dm_smooth'] = df['plus_dm'].ewm(alpha=alpha, adjust=False).mean()
        df['minus_dm_smooth'] = df['minus_dm'].ewm(alpha=alpha, adjust=False).mean()

        df['plus_di'] = 100 * (df['plus_dm_smooth'] / df['tr_smooth'])
        df['minus_di'] = 100 * (df['minus_dm_smooth'] / df['tr_smooth'])

        df['dx'] = 100 * abs(df['plus_di'] - df['minus_di']) / (df['plus_di'] + df['minus_di'])
        df['adx'] = df['dx'].ewm(alpha=alpha, adjust=False).mean()

        # --- Historical Volatility (HV) ---
        df['log_ret'] = np.log(df['close'] / df['close'].shift(1))
        # Rolling standard deviation of log returns, annualized (Assuming 5min candles -> ~18000 periods/year?? No, sqrt(252*75) approx)
        # Let's keep it simple: Annualized Volatility
        # 5 mins = 75 candles per day. 252 days.
        df['hv'] = df['log_ret'].rolling(window=20).std() * np.sqrt(252 * 75)

        # --- Days to Expiry (Next Thursday) ---
        def get_dte(ts):
            # 0=Mon, 3=Thu, 6=Sun
            # If Thu(3) and time > 15:30, next Thu.
            # Else find next Thu.
            current_date = ts.date()
            days_ahead = 3 - current_date.weekday()
            if days_ahead < 0: # Thu has passed
                 days_ahead += 7
            if days_ahead == 0 and ts.time() > dt.time(15, 30):
                 days_ahead += 7

            expiry_date = current_date + dt.timedelta(days=days_ahead)
            # Time difference in years for BSM
            # Simply return days for logic, BSM needs years
            return (expiry_date - current_date).days

        if 'timestamp' in df.columns:
            df['dte_days'] = df['timestamp'].apply(lambda x: get_dte(x))

        # --- Gamma Blast Indicators (Short-term ROC) ---
        df['roc_price'] = df['close'].pct_change(periods=BotConfig.ROC_PERIOD) * 100
        df['vol_avg'] = df['volume'].rolling(window=20).mean()

        return df

    @staticmethod
    def detect_signal(curr_candle, prev_candle):
        """
        Returns: (SignalType, StopLoss)
        SignalType: "BUY", "SELL", or None
        """
        if curr_candle.empty or prev_candle.empty: return None, 0

        st_trend = curr_candle.get('st_trend', 0)
        vwap = curr_candle.get('vwap', 0)
        rsi = curr_candle.get('rsi', 50)
        atr = curr_candle.get('atr', 0)
        adx = curr_candle.get('adx', 0)
        hv = curr_candle.get('hv', 0)
        dte = curr_candle.get('dte_days', 4)
        roc = curr_candle.get('roc_price', 0)
        vol_curr = curr_candle.get('volume', 0)
        vol_avg = curr_candle.get('vol_avg', 1)

        # Greeks Calculation (ATM)
        spot = curr_candle['close']
        time_years = max(dte / 365.0, 0.001) # Avoid div by zero
        vol = max(hv, 0.10) # Min 10% Vol

        # Calculate ATM Delta/Theta (Strike = Spot)
        delta_call, theta_call = Greeks.calculate(spot, spot, time_years, 0.07, vol, "CE")
        delta_put, theta_put = Greeks.calculate(spot, spot, time_years, 0.07, vol, "PE")

        prev_st_trend = prev_candle.get('st_trend', 0)

        # --- FILTERS (Advance Model) ---
        # 1. HV Filter: Avoid Low Volatility (Dead Markets)
        # 2. Expiry Chop Filter: If DTE < 1, require Strong Trend (ADX > 30)

        valid_vol = hv > 0.10
        valid_expiry = True
        if dte < 1: # Expiry Day
            if adx < 30: valid_expiry = False

        # --- GAMMA BLAST LOGIC (Proxy) ---
        # Live: OptionChainManager.check_gamma_blast(...)
        # Backtest Proxy: Price ROC > Threshold AND Volume > 2x Avg
        gamma_long = (roc > BotConfig.ROC_THRESHOLD) and (vol_curr > 1.5 * vol_avg)
        gamma_short = (roc < -BotConfig.ROC_THRESHOLD) and (vol_curr > 1.5 * vol_avg)

        # --- LONG SIGNAL ---
        # Strategy: SuperTrend + VWAP + (Filters) OR Gamma Blast
        # We allow Gamma Blast to override standard filters if momentum is huge

        is_long_std = (st_trend == 1) and (curr_candle['close'] > vwap) and \
                      (rsi > BotConfig.RSI_LONG_MIN) and (adx > BotConfig.ADX_THRESHOLD) and \
                      valid_vol and valid_expiry

        is_long = is_long_std or gamma_long

        trigger_long = (prev_st_trend == -1 and st_trend == 1) or \
                       (st_trend == 1 and prev_candle['close'] <= prev_candle['vwap'] and curr_candle['close'] > vwap) or \
                       gamma_long # Immediate trigger

        if is_long and trigger_long:
            # For Gamma Blast, use tighter SL
            sl_mult = BotConfig.ATR_SL_MULT if not gamma_long else 0.8
            sl = curr_candle['close'] - (atr * sl_mult)
            return "BUY", {"sl": sl, "delta": delta_call, "theta": theta_call}

        # --- SHORT SIGNAL ---
        is_short_std = (st_trend == -1) and (curr_candle['close'] < vwap) and \
                       (rsi < BotConfig.RSI_SHORT_MAX) and (adx > BotConfig.ADX_THRESHOLD) and \
                       valid_vol and valid_expiry

        is_short = is_short_std or gamma_short

        trigger_short = (prev_st_trend == 1 and st_trend == -1) or \
                        (st_trend == -1 and prev_candle['close'] >= prev_candle['vwap'] and curr_candle['close'] < vwap) or \
                        gamma_short

        if is_short and trigger_short:
            sl_mult = BotConfig.ATR_SL_MULT if not gamma_short else 0.8
            sl = curr_candle['close'] + (atr * sl_mult)
            return "SELL", {"sl": sl, "delta": abs(delta_put), "theta": theta_put}

        return None, 0


# ============================================================================
# --- SECTION 4: BACKTESTER LOGIC ---
# ============================================================================

class OptionChainManager:
    """Handles Expiry Selection and Option Chain Analysis for Gamma Blast."""

    @staticmethod
    def get_fallback_symbol(symbol):
        """Symbol-variant fallback (e.g. NSE:NIFTY50-INDEX -> NIFTY)."""
        base = BotConfig.INDEX_MAP.get(symbol)
        if base: return base
        # Fallback parsing
        if "NIFTY" in symbol: return "NIFTY"
        if "BANK" in symbol: return "BANKNIFTY"
        return symbol

    @staticmethod
    def fetch_expiry(fyers, symbol):
        """
        Fetches option chain to find nearest expiry.
        Real-time expiry selection: Skip same-day if time > 15:25 IST.
        """
        try:
            # Note: This requires the 'fyers' instance to be passed or accessible.
            # Using a simplified mockable logic if fyers not available
            if not HAS_FYERS: return dt.date.today()

            sym_base = OptionChainManager.get_fallback_symbol(symbol)
            # In a real scenario, we'd use fyers.option_chain(symbol=...)
            # or calculate standard expiries (Thu/Wed).
            # For this script's scope, we simulate the logic:

            today = dt.date.today()
            now = dt.datetime.now().time()

            # Simple Logic: If today is Thursday (3) or Wednesday (2 for BANKNIFTY/Monthly?)
            # Let's assume Thursday expiry for NIFTY
            days_ahead = 3 - today.weekday()
            if days_ahead < 0: days_ahead += 7

            expiry = today + dt.timedelta(days=days_ahead)

            # Skip same-day logic
            if expiry == today and now > dt.time(15, 25):
                expiry += dt.timedelta(days=7)

            return expiry
        except Exception as e:
            print(f"Expiry Fetch Error: {e}")
            return dt.date.today()

    @staticmethod
    def check_gamma_blast(fyers, symbol, spot_price, expiry):
        """
        Checks for Gamma Blast conditions:
        1. Delta OI Window (Change in OI for ATM strike)
        2. Dynamic Threshold
        """
        # In Backtest/Mock, we can't fetch live OI change.
        # We rely on the 'Proxy' implemented in Strategy.detect_signal (ROC + Volume).
        # This function is a placeholder for the LIVE implementation.
        if not HAS_FYERS: return 0  # 0 = No Signal

        # Real Implementation would:
        # 1. Get Option Chain for 'expiry'
        # 2. Find ATM Strike
        # 3. Check OI Change (IOI or similar field if available in depth)
        # 4. Return 1 (Call Blast) or -1 (Put Blast) if Threshold met

        return 0

def run_backtester():
    fyers = get_fyers_instance()
    print(f"\n🚀 STARTING BACKTEST (Strategy: SuperTrend+VWAP, TF: {BotConfig.TIMEFRAME_MIN}m)")

    to_date = dt.date.today()
    from_date = to_date - dt.timedelta(days=60)

    for symbol in BotConfig.SYMBOLS:
        print(f"\nProcessing {symbol}...")
        time.sleep(0.5)
        try:
            data = {
                "symbol": symbol, "resolution": str(BotConfig.TIMEFRAME_MIN),
                "date_format": "1", "range_from": from_date.strftime("%Y-%m-%d"),
                "range_to": to_date.strftime("%Y-%m-%d"), "cont_flag": "1"
            }
            resp = fyers.history(data)
            if resp.get("s") != "ok" or not resp.get("candles"):
                print(f"  ⚠️ No data/Error: {resp.get('message')}")
                continue

            cols = ["timestamp", "open", "high", "low", "close", "volume"]
            df = pd.DataFrame(resp["candles"], columns=cols)
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s').dt.tz_localize('UTC').dt.tz_convert(
                'Asia/Kolkata')

            df = Strategy.calculate_indicators(df)

            trades = []
            active_trade = None

            signals_count = 0

            for i in range(21, len(df)):
                curr = df.iloc[i]
                prev = df.iloc[i - 1]

                # Check for Signal
                if active_trade is None:
                    sig_type, packet = Strategy.detect_signal(curr, prev)

                    if sig_type:
                        signals_count += 1
                        entry_price = curr['close']

                        sl = packet['sl']
                        delta = packet['delta']
                        theta = packet['theta']

                        risk = abs(entry_price - sl)
                        if risk == 0: continue

                        if sig_type == "BUY":
                            tgt = entry_price + (risk * BotConfig.R_MULTIPLIER)
                        else:
                            tgt = entry_price - (risk * BotConfig.R_MULTIPLIER)

                        active_trade = {
                            "time": curr['timestamp'],
                            "type": sig_type,
                            "entry": entry_price,
                            "sl": sl,
                            "tgt": tgt,
                            "pnl": 0,
                            "delta": delta,
                            "theta": theta,
                            "outcome": "OPEN"
                        }
                else:
                    # Manage Active Trade
                    t = active_trade
                    delta = t.get('delta', 0.5)

                    if t['type'] == "BUY":
                        # SL Hit
                        if curr['low'] <= t['sl']:
                            t['outcome'] = "LOSS"
                            t['pnl'] = (t['sl'] - t['entry']) * delta
                            trades.append(t)
                            active_trade = None
                        # TGT Hit
                        elif curr['high'] >= t['tgt']:
                            t['outcome'] = "WIN"
                            t['pnl'] = (t['tgt'] - t['entry']) * delta
                            trades.append(t)
                            active_trade = None
                        # Trend Reversal Exit (SuperTrend turns Red)
                        elif curr['st_trend'] == -1:
                            t['outcome'] = "REV"
                            t['pnl'] = (curr['close'] - t['entry']) * delta
                            trades.append(t)
                            active_trade = None

                    elif t['type'] == "SELL":
                        # SL Hit
                        if curr['high'] >= t['sl']:
                            t['outcome'] = "LOSS"
                            t['pnl'] = (t['entry'] - t['sl']) * delta
                            trades.append(t)
                            active_trade = None
                        # TGT Hit
                        elif curr['low'] <= t['tgt']:
                            t['outcome'] = "WIN"
                            t['pnl'] = (t['entry'] - t['tgt']) * delta
                            trades.append(t)
                            active_trade = None
                        # Trend Reversal Exit (SuperTrend turns Green)
                        elif curr['st_trend'] == 1:
                            t['outcome'] = "REV"
                            t['pnl'] = (t['entry'] - curr['close']) * delta
                            trades.append(t)
                            active_trade = None

                    # EOD Exit
                    if active_trade and curr['timestamp'].time() >= BotConfig.EXIT_ALL_NSE:
                        t = active_trade
                        t['outcome'] = "EOD"
                        if t['type'] == "BUY":
                            t['pnl'] = (curr['close'] - t['entry']) * delta
                        else:
                            t['pnl'] = (t['entry'] - curr['close']) * delta
                        trades.append(t)
                        active_trade = None

            # Close last
            if active_trade:
                t = active_trade
                delta = t.get('delta', 0.5)
                t['outcome'] = "OPEN (MTM)"
                if t['type'] == "BUY":
                    t['pnl'] = (df.iloc[-1]['close'] - t['entry']) * delta
                else:
                    t['pnl'] = (t['entry'] - df.iloc[-1]['close']) * delta
                trades.append(t)

            # Summary
            wins = len([t for t in trades if t['pnl'] > 0])
            losses = len([t for t in trades if t['pnl'] <= 0])
            mtm_pnls = sum([t['pnl'] for t in trades])

            print(f"  Signals: {signals_count}")
            print(f"  Results: Wins:{wins} | Losses:{losses} | Net PnL (Spot): {mtm_pnls:.2f}")

            if trades:
                print(f"  Recent Trades:")
                for t in trades[-5:]:
                    ts_str = t['time'].strftime('%Y-%m-%d %H:%M')
                    print(f"    [{ts_str}] {t['type']} @ {t['entry']:.2f} -> {t['outcome']} (PnL: {t['pnl']:.2f})")

        except Exception as e:
            print(f"  ❌ Error: {e}")
            traceback.print_exc()


# ============================================================================
# --- SECTION 5: LIVE PAPER BOT LOGIC ---
# ============================================================================

class PaperPosition:
    def __init__(self, symbol, option_symbol, entry, sl_spot, qty, entry_spot, side):
        self.symbol = symbol
        self.option_symbol = option_symbol
        self.entry = entry  # Option Entry Price
        self.sl_spot = sl_spot  # Spot Level SL
        self.qty = qty
        self.entry_spot = entry_spot
        self.side = side  # "BUY" (Long CE) or "SELL" (Long PE)
        self.status = "OPEN"
        self.pnl = 0.0
        self.delta = 0.5
        self.theta = 0
        self.entry_time = dt.datetime.now()


class LivePaperBot:
    def __init__(self):
        self.fyers = get_fyers_instance()
        self.config = BotConfig()
        self.active_positions: Dict[str, PaperPosition] = {}
        self.candles_build = {}
        self.processed_candles = set()
        self.ltp_cache = {}
        self.history_df = {}
        self.paper_balance = BotConfig.PAPER_BALANCE

    def fetch_initial_state(self):
        print("🔄 Initializing Bot State...")
        to_date = dt.date.today()
        from_date = to_date - dt.timedelta(days=5)

        for sym in BotConfig.SYMBOLS:
            time.sleep(0.2)
            try:
                resp = self.fyers.history({
                    "symbol": sym, "resolution": str(BotConfig.TIMEFRAME_MIN),
                    "date_format": "1", "range_from": from_date.strftime("%Y-%m-%d"),
                    "range_to": to_date.strftime("%Y-%m-%d"), "cont_flag": "1"
                })
                if resp.get("candles"):
                    df = pd.DataFrame(resp["candles"], columns=["timestamp", "open", "high", "low", "close", "volume"])
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s').dt.tz_localize('UTC').dt.tz_convert(
                        'Asia/Kolkata')
                    df = Strategy.calculate_indicators(df)
                    self.history_df[sym] = df
                    print(f"  Loaded {len(df)} candles for {sym}")
            except Exception as e:
                print(f"  Failed to load history for {sym}: {e}")
        print(f"✅ State Initialized.")

    def run(self):
        self.fetch_initial_state()
        access_token = f"{self.fyers.client_id}:{self.fyers.token}"

        def on_message(msg):
            if isinstance(msg, list):
                for m in msg: self.process_tick(m)
            elif isinstance(msg, dict) and msg.get("type") == "sf":
                self.process_tick(msg)

        def on_error(msg):
            print(f"WS Error: {msg}")

        def on_open():
            print("🔌 WebSocket Connected.")
            self.ws.subscribe(symbols=BotConfig.SYMBOLS)

        self.ws = data_ws.FyersDataSocket(
            access_token=access_token, log_path=".", litemode=False, write_to_file=False,
            reconnect=True, on_connect=on_open, on_message=on_message, on_error=on_error
        )
        threading.Thread(target=self.ws.connect, daemon=True).start()
        print(f"🚀 Live Bot Running | Balance: {self.paper_balance}")

        try:
            while True:
                time.sleep(1)
                self.monitor_positions()
        except KeyboardInterrupt:
            print("\n🛑 Bot Stopped.")

    def get_candle_start(self, ts):
        dt_obj = dt.datetime.fromtimestamp(ts)
        return dt_obj.replace(second=0, microsecond=0) - dt.timedelta(minutes=dt_obj.minute % BotConfig.TIMEFRAME_MIN)

    def process_tick(self, msg):
        sym = msg.get("symbol")
        ltp = float(msg.get("ltp"))
        ts = msg.get("timestamp", time.time())
        self.ltp_cache[sym] = ltp

        c_start = self.get_candle_start(ts)
        key = (sym, c_start)
        if key not in self.candles_build:
            self.candles_build[key] = {"o": ltp, "h": ltp, "l": ltp, "c": ltp, "v": 0}
        else:
            c = self.candles_build[key]
            c["h"] = max(c["h"], ltp)
            c["l"] = min(c["l"], ltp)
            c["c"] = ltp

        tick_dt = dt.datetime.fromtimestamp(ts)
        candle_end_time = c_start + dt.timedelta(minutes=BotConfig.TIMEFRAME_MIN)
        if tick_dt >= (candle_end_time - dt.timedelta(seconds=1)):
            if key not in self.processed_candles:
                self.processed_candles.add(key)
                self.analyze_completed_candle(sym, self.candles_build[key], c_start)

    def analyze_completed_candle(self, sym, candle_dict, start_time):
        new_row = {
            "timestamp": pd.Timestamp(start_time).tz_localize(None).tz_localize('UTC').tz_convert('Asia/Kolkata'),
            "open": candle_dict["o"], "high": candle_dict["h"], "low": candle_dict["l"], "close": candle_dict["c"],
            "volume": 0
        }
        df = self.history_df.get(sym)
        if df is None: return
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        if len(df) > 200: df = df.iloc[-200:]
        df = Strategy.calculate_indicators(df)
        self.history_df[sym] = df

        curr, prev = df.iloc[-1], df.iloc[-2]
        sig_type, packet = Strategy.detect_signal(curr, prev)

        if sig_type:
            print(f"🚀 SIGNAL ({sig_type}) on {sym} @ {curr['close']}")
            self.execute_trade_signal(sym, sig_type, curr['close'], packet)

    def execute_trade_signal(self, sym, sig_type, spot_price, packet):
        if sym in self.active_positions: return

        strike_step = 50 if "NIFTY50" in sym else 100
        if "BANK" in sym: strike_step = 100

        # CE for BUY, PE for SELL
        opt_type = "CE" if sig_type == "BUY" else "PE"

        # For PE, we want strike < Spot (OTM) or > Spot (ITM).
        # Standard: ATM.
        atm_strike = round(spot_price / strike_step) * strike_step

        qty = BotConfig.LOT_SIZES.get(BotConfig.INDEX_MAP.get(sym, 'NIFTY'), 50)

        stop_loss_level = packet['sl']

        pos = PaperPosition(sym, f"{opt_type}_{atm_strike}", 100.0, stop_loss_level, qty, spot_price, sig_type)
        pos.delta = packet['delta']
        pos.theta = packet['theta']

        self.active_positions[sym] = pos
        print(f"✅ PAPER {sig_type} OPEN: {sym} | Opt: {opt_type} {atm_strike} | SL: {stop_loss_level}")

    def monitor_positions(self):
        for sym in list(self.active_positions.keys()):
            pos = self.active_positions[sym]
            curr_spot = self.ltp_cache.get(sym)
            if not curr_spot: continue

            # Check Exit
            exit_triggered = False
            pnl = 0

            # Spot Change
            spot_change = curr_spot - pos.entry_spot
            if pos.side == "SELL": spot_change = -spot_change

            # Simulated Option PnL
            # Use calculated Delta
            pos.pnl = (spot_change * pos.delta) * pos.qty

            # 1. Stop Loss
            if pos.side == "BUY" and curr_spot <= pos.sl_spot:
                exit_triggered = True
            elif pos.side == "SELL" and curr_spot >= pos.sl_spot:
                exit_triggered = True

            # 2. Option Premium SL (20%)
            if pos.pnl <= -(pos.entry * 0.20 * pos.qty): exit_triggered = True

            if exit_triggered:
                print(f"❌ CLOSING {sym} | PnL: {pos.pnl:.2f}")
                del self.active_positions[sym]

            # ============================================================================


# --- SECTION 6: MAIN EXECUTION ---
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="All-In-One SuperTrend Bot")
    parser.add_argument("mode", nargs='?', choices=["setup", "backtest", "run"], help="Mode")
    parser.add_argument("--app_id", help="Fyers App ID")
    parser.add_argument("--secret_key", help="Fyers Secret Key")
    parser.add_argument("--redirect_url", help="Fyers Redirect URL")
    parser.add_argument("--retrain", action="store_true", help="Ignored")

    args = parser.parse_args()
    mode = args.mode

    if not mode:
        print("\n--- All-In-One SuperTrend Bot ---")
        print("1. Setup Credentials")
        print("2. Run Backtester")
        print("3. Run Live Paper Trading Bot")
        choice = input("Enter choice (1-3): ")
        if choice == '1':
            mode = 'setup'
        elif choice == '2':
            mode = 'backtest'
        elif choice == '3':
            mode = 'run'

    if mode == "setup":
        app_id = args.app_id or input("App ID: ")
        secret = args.secret_key or input("Secret Key: ")
        url = args.redirect_url or input("Redirect URL: ")
        setup_credentials(app_id, secret, url)
    elif mode == "backtest":
        run_backtester()
    elif mode == "run":
        LivePaperBot().run()


if __name__ == "__main__": main()
