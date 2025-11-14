# -*- coding: utf-8 -*-
"""
Trading Bot based on VWAP crossover strategy

Strategy:
- Entry:
    1. When a candle opens below VWAP, crosses and closes above VWAP, enter on the next candle's high break.
    2. When a candle opens above VWAP, touches or crosses below VWAP, and closes above VWAP, enter on the next candle's high break.
- Exit:
    - When a candle crosses and closes below the 10 EMA, exit on the next candle's low break.
"""

import os, sys, json, time, datetime, hashlib
from urllib.parse import urlparse, parse_qs, quote
import requests
import pandas as pd
import pytz
from datetime import datetime as dt, timedelta
from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws

# ===================== STRATEGY SETTINGS =====================
# --- Timeframe and Strategy Parameters ---
TIMEFRAME_MIN = 15         # Timeframe in minutes. Valid options: 1, 2, 3, 5, 10, 15, 30, 60
EXIT_EMA = 10              # EMA period for exit signal. Valid options: 5, 10, 21, 50, 100, 200

# --- Order Sizing and Mode ---
ORDER_MODE = "INTRADAY"      # "CNC" for carry forward, "INTRADAY" for auto-square-off
ALLOC_DEFAULT = 5000.0     # Default capital allocation per trade
ALLOC_MAP = {              # Symbol-specific allocation (overrides default)
    # "NSE:SBIN-EQ": 12000.0,
}
# ===================== OTHER SETTINGS =====================
MARKET_START_TIME_NSE = dt.strptime("09:15", "%H:%M").time()
MARKET_START_TIME_MCX = dt.strptime("09:00", "%H:%M").time()
ONE_POSITION_AT_A_TIME = True # If True, the bot will only hold one position across all symbols.
EXIT_ALL_TIME_NSE = dt.strptime("15:09", "%H:%M").time() # Auto square-off time for INTRADAY positions on NSE
EXIT_ALL_TIME_MCX = dt.strptime("22:50", "%H:%M").time() # Auto square-off time for INTRADAY positions on MCX

def get_exchange(symbol):
    return symbol.split(':')[0]

# ===================== WATCHLIST =====================
SYMBOLS = [
    'NSE:ADANIENT-EQ', 'NSE:ADANIPORTS-EQ', 'NSE:APOLLOHOSP-EQ', 'NSE:ASIANPAINT-EQ', 'NSE:AXISBANK-EQ',
    'NSE:BAJAJ-AUTO-EQ', 'NSE:BAJFINANCE-EQ', 'NSE:BAJAJFINSV-EQ', 'NSE:BPCL-EQ', 'NSE:BHARTIARTL-EQ',
    'NSE:BRITANNIA-EQ', 'NSE:CIPLA-EQ', 'NSE:COALINDIA-EQ', 'NSE:DIVISLAB-EQ', 'NSE:DRREDDY-EQ',
    'NSE:EICHERMOT-EQ', 'NSE:GRASIM-EQ', 'NSE:HCLTECH-EQ', 'NSE:HDFCBANK-EQ', 'NSE:HDFCLIFE-EQ',
    'NSE:HEROMOTOCO-EQ', 'NSE:HINDALCO-EQ', 'NSE:HINDUNILVR-EQ', 'NSE:ICICIBANK-EQ', 'NSE:ITC-EQ',
    'NSE:INFY-EQ', 'NSE:JSWSTEEL-EQ', 'NSE:KOTAKBANK-EQ', 'NSE:LTIM-EQ', 'NSE:LT-EQ',
    'NSE:M&M-EQ', 'NSE:MARUTI-EQ', 'NSE:NTPC-EQ', 'NSE:NESTLEIND-EQ', 'NSE:ONGC-EQ',
    'NSE:POWERGRID-EQ', 'NSE:RELIANCE-EQ', 'NSE:SBILIFE-EQ', 'NSE:SBIN-EQ', 'NSE:SIEMENS-EQ',
    'NSE:SUNPHARMA-EQ', 'NSE:TCS-EQ', 'NSE:TATACONSUM-EQ', 'NSE:TATASTEEL-EQ',
    'NSE:TECHM-EQ', 'NSE:TITAN-EQ', 'NSE:UPL-EQ', 'MCX:NATGASMINI25NOVFUT',
    'MCX:SILVERMIC25NOVFUT',
    'MCX:CRUDEOILM25NOVFUT'
]


# ============================== LOGIN (v3 api-t1) =============================
CONFIG_FILE = "fyers_login_details.json"
TOKENS_DIR = "AccessToken"
TODAY = str(datetime.date.today())
TOKEN_PATH = os.path.join(TOKENS_DIR, f"{TODAY}.json")

def load_or_prompt_creds():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)

    print("---- Enter your Fyers Login Credentials (v3) ----")
    creds = {
        "api_key": input("Enter APP ID (e.g., ABCDE12345-100): ").strip(),
        "api_secret": input("Enter SECRET ID: ").strip(),
        "redirect_url": input("Enter Redirect URL (must match app): ").strip(),
    }
    if input("Save to 'fyers_login_details.json'? (Y/N): ").strip().upper() == "Y":
        with open(CONFIG_FILE, "w") as f:
            json.dump(creds, f, indent=2)
        print(f"Saved '{CONFIG_FILE}'.")
    else:
        print("Skipping save.")
    return creds

def build_auth_url(app_id, redirect_uri, state="sample_state"):
    # v3 auth is served from api-t1
    base = "https://api-t1.fyers.in/api/v3/generate-authcode"
    # scope/nonce are optional but harmless; URL-encode redirect
    params = (
        f"client_id={quote(app_id)}"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        f"&response_type=code"
        f"&state={quote(state)}"
        f"&scope=openid"
        f"&nonce={int(time.time())}"
    )
    return f"{base}?{params}"

def extract_code(user_input):
    """Accept either a raw code or a full redirect URL with ?code=..."""
    if user_input.startswith("http://") or user_input.startswith("https://"):
        q = parse_qs(urlparse(user_input).query)
        code = q.get("code", [None])[0]
        if not code:
            raise ValueError("No 'code' param found in the provided URL.")
        return code
    return user_input # assume raw code

def sha256_appIdHash(app_id, secret_id):
    return hashlib.sha256(f"{app_id}:{secret_id}".encode("utf-8")).hexdigest()

def validate_authcode(app_id, secret_id, auth_code, max_retries=5):
    url = "https://api-t1.fyers.in/api/v3/validate-authcode"
    payload = {
        "grant_type": "authorization_code",
        "appIdHash": sha256_appIdHash(app_id, secret_id),
        "code": auth_code,
    }
    headers = {"Content-Type": "application/json"}

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=20)
            if r.status_code == 503:
                # Service temporarily unavailable -> backoff and retry
                sleep_s = min(2 ** attempt, 30)
                print(f"[{attempt}/{max_retries}] 503 from auth server. Retrying in {sleep_s}s...")
                time.sleep(sleep_s)
                continue
            r.raise_for_status()
            data = r.json()
            if data.get("s") == "error":
                # Bubble up API error messages for clarity
                raise RuntimeError(f"Fyers error {data.get('code')}: {data.get('message')}")
            return data # expected to include access_token, refresh_token, etc.
        except requests.RequestException as e:
            if attempt == max_retries:
                raise
            sleep_s = min(2 ** attempt, 30)
            print(f"[{attempt}/{max_retries}] Network error: {e}. Retrying in {sleep_s}s...")
            time.sleep(sleep_s)

def get_access_token():
    creds = load_or_prompt_creds()
    app_id = creds["api_key"]
    secret_id = creds["api_secret"]
    redirect_uri = creds["redirect_url"]

    # If today's token file already exists, just show it
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, "r") as f:
            access_token = json.load(f)
        print(f"API Key : {app_id}")
        print(f"Access Token (loaded from file) : {access_token}")
        return {"app_id": app_id, "access_token": access_token}

    # Step 1: Get login URL (api-t1) and open it manually in a browser
    auth_url = build_auth_url(app_id, redirect_uri)
    print("\nLogin URL (open in browser, allow & complete login):")
    print(auth_url)

    # Step 2: Paste either the full redirected URL or just the 'code'
    user_val = input("\nPaste the FULL redirect URL after login, or just the 'code=' value here: ").strip()
    try:
        auth_code = extract_code(user_val)
    except Exception as e:
        print(f"Could not extract code: {e}")
        sys.exit(1)

    # Step 3: Exchange code for tokens at the correct endpoint
    try:
        token_resp = validate_authcode(app_id, secret_id, auth_code)
        access_token = token_resp.get("access_token")
        if not access_token:
            raise RuntimeError(f"Unexpected token response: {token_resp}")

        os.makedirs(TOKENS_DIR, exist_ok=True)
        with open(TOKEN_PATH, "w") as f:
            json.dump(access_token, f)
        print("\nLogin successful.")
        print(f"API Key : {app_id}")
        print(f"Access Token : {access_token}")
        print(f"Saved to: {TOKEN_PATH}")
        return {"app_id": app_id, "access_token": access_token}
    except Exception as e:
        print(f"\nLogin Failed: {e}")
        sys.exit(1)

# ============================== DATA HELPERS ==================================
TIMEZONE = "Asia/Kolkata"
IST = pytz.timezone(TIMEZONE)

def candles_df(resp):
    if not resp or "candles" not in resp:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"]).set_index(
            pd.Index([], name="datetime")
        )
    df = pd.DataFrame(resp["candles"], columns=["datetime", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["datetime"], unit="s", utc=True).dt.tz_convert(TIMEZONE).dt.tz_localize(None)
    df = df.set_index("datetime").astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
    return df

def history(fyers, symbol, res, start, end):
    payload = {
        "symbol": symbol, "resolution": str(res), "date_format": "1",
        "range_from": start.strftime("%Y-%m-%d"), "range_to": end.strftime("%Y-%m-%d"),
        "cont_flag": "0",
    }
    r = fyers.history(data=payload)
    return candles_df(r)

def calculate_vwap(df):
    df['TP'] = (df['high'] + df['low'] + df['close']) / 3
    df['TPV'] = df['TP'] * df['volume']
    df['cum_TPV'] = df['TPV'].cumsum()
    df['cum_volume'] = df['volume'].cumsum()
    df['vwap'] = df['cum_TPV'] / df['cum_volume']
    return df

def compute_ema(s, span):
    return s.ewm(span=span, adjust=False, min_periods=span).mean()

# ============================== STATE =========================================
class State:
    def __init__(self):
        self.data = {}
        self.positions = {}

    def init_symbol(self, symbol):
        if symbol not in self.data:
            self.data[symbol] = {
                "candles": pd.DataFrame(),
                "entry_triggered": False,
                "exit_triggered": False,
                "position": None
            }

STATE = State()


# ============================== ORDER HELPERS =================================
def place_market_order(fyers, symbol, qty, side, product_type):
    data = {
        "symbol": symbol, "qty": qty, "type": 2,  # Market
        "side": side,  # 1 for Buy, -1 for Sell
        "productType": product_type,
        "limitPrice": 0, "stopPrice": 0,
        "validity": "DAY", "disclosedQty": 0, "offlineOrder": False,
    }
    print(f"[order] {'BUY' if side == 1 else 'SELL'}:", data)
    return fyers.place_order(data=data)

# ============================== TICK HANDLER ==================================
def on_message(msg):
    symbol = msg.get('symbol')
    ltp = msg.get('ltp')
    if not symbol or not ltp:
        return

    now_local = dt.now(IST).replace(tzinfo=None)

    # Auto-square-off for INTRADAY
    if ORDER_MODE == "INTRADAY":
        exit_time = EXIT_ALL_TIME_NSE if get_exchange(symbol) == "NSE" else EXIT_ALL_TIME_MCX
        if now_local.time() >= exit_time:
            if STATE.data[symbol].get('position'):
                resp = place_market_order(FYERS, symbol, STATE.data[symbol]['position']['qty'], -1, "INTRADAY")
                if resp.get("s") == "ok":
                    print(f"[{symbol}] INTRADAY EXIT triggered at {ltp}")
                    STATE.data[symbol]['position'] = None
        return

    # Initialize symbol if not already
    STATE.init_symbol(symbol)

    # Refresh candles on new bar
    cmin, csec = now_local.minute, now_local.second
    if cmin % TIMEFRAME_MIN == 0 and csec < 5:
        start_time = MARKET_START_TIME_NSE if get_exchange(symbol) == "NSE" else MARKET_START_TIME_MCX
        start = now_local.replace(hour=start_time.hour, minute=start_time.minute, second=0, microsecond=0)
        df = history(FYERS, symbol, TIMEFRAME_MIN, start, now_local)
        if not df.empty:
            df = calculate_vwap(df)
            df['ema'] = compute_ema(df['close'], EXIT_EMA)
            STATE.data[symbol]['candles'] = df

    df = STATE.data[symbol]['candles']
    if df.empty or len(df) < 2:
        return

    prev_candle = df.iloc[-2]

    position = STATE.data[symbol].get('position')

    # Entry Logic
    if not position:
        # Scenario 1: Open below VWAP, close above
        scenario1 = (prev_candle['open'] < prev_candle['vwap'] and
                     prev_candle['close'] > prev_candle['vwap'])

        # Scenario 2: Open above VWAP, touches/crosses below, closes above
        scenario2 = (prev_candle['open'] > prev_candle['vwap'] and
                     prev_candle['low'] <= prev_candle['vwap'] and
                     prev_candle['close'] > prev_candle['vwap'])

        if (scenario1 or scenario2) and ltp > prev_candle['high']:
            if ONE_POSITION_AT_A_TIME and any(s.get('position') for s in STATE.data.values()):
                return

            alloc = ALLOC_MAP.get(symbol, ALLOC_DEFAULT)
            qty = int(alloc / ltp)

            resp = place_market_order(FYERS, symbol, qty, 1, ORDER_MODE)
            if resp.get("s") == "ok":
                STATE.data[symbol]['position'] = {
                    "entry_price": ltp,
                    "qty": qty,
                    "stoploss": prev_candle['low'],
                    "entry_high": prev_candle['high'],
                }
                print(f"[{symbol}] ENTRY triggered at {ltp}")
    # Exit Logic
    else:
        if prev_candle['close'] < prev_candle['ema'] and ltp < prev_candle['low']:
            resp = place_market_order(FYERS, symbol, position['qty'], -1, ORDER_MODE)
            if resp.get("s") == "ok":
                print(f"[{symbol}] EXIT triggered at {ltp}")
                STATE.data[symbol]['position'] = None

# ============================== WS EVENTS =====================================
def on_error(msg): print("[ws:error]", msg)
def on_close(msg): print("[ws:close]", msg)

def on_open():
    print(f"[ws:open] Subscribing to {len(SYMBOLS)} symbols: {SYMBOLS}")
    fyers_socket.subscribe(symbols=SYMBOLS, data_type="SymbolUpdate")
    fyers_socket.keep_running()

if __name__ == "__main__":
    auth = get_access_token()
    APP_ID = auth["app_id"]
    ACCESS_TOKEN = auth["access_token"]

    FYERS = fyersModel.FyersModel(client_id=APP_ID, is_async=False, token=ACCESS_TOKEN, log_path="")

    print("\n----- BOT CONFIGURATION -----")
    print(f"TIMEFRAME: {TIMEFRAME_MIN} min")
    print(f"EXIT EMA: {EXIT_EMA}")
    print(f"ORDER MODE: {ORDER_MODE}")
    print(f"DEFAULT ALLOCATION: {ALLOC_DEFAULT}")
    print("---------------------------\n")

    for symbol in SYMBOLS:
        STATE.init_symbol(symbol)

    fyers_socket = data_ws.FyersDataSocket(
        access_token=f"{APP_ID}:{ACCESS_TOKEN}",
        log_path="",
        litemode=True,
        write_to_file=False,
        reconnect=True,
        on_connect=on_open,
        on_close=on_close,
        on_error=on_error,
        on_message=on_message,
    )

    print("[start] Connecting WebSocket…")
    fyers_socket.connect()
