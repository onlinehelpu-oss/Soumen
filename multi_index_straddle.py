from __future__ import annotations

import os, json, time, datetime, hashlib, sys
from urllib.parse import urlparse, parse_qs, quote
from typing import Dict, Any, Optional, Tuple

import requests
import datetime as dt
import pandas as pd
import io

from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws


# ============================== SYMBOL MASTER =================================
class SymbolMaster:
    """
    Handles downloading, caching, and looking up instrument details from the
    Fyers symbol master CSV file.
    """
    CACHE_FILE = "fno_symbol_master.csv"
    CACHE_EXPIRY_HOURS = 24

    def __init__(self, fyers: fyersModel.FyersModel):
        self._fyers = fyers
        self._df = None
        self._ensure_cache_updated()

    def _is_cache_valid(self) -> bool:
        if not os.path.exists(self.CACHE_FILE):
            return False

        last_mod_time = os.path.getmtime(self.CACHE_FILE)
        expiry_time = last_mod_time + self.CACHE_EXPIRY_HOURS * 3600
        return time.time() < expiry_time

    def _download_master(self):
        print("[SymbolMaster] Cache is invalid or missing. Downloading new symbol master...")
        resp = self._fyers.get_broker_config()

        if resp.get("s") != "ok":
            raise RuntimeError(f"Failed to fetch broker config: {resp.get('message')}")

        fno_details = next((item for item in resp.get("data", {}).get("symbolMaster", []) if item["feed_name"] == "FNO"), None)
        if not fno_details or "url" not in fno_details:
            raise RuntimeError("Could not find FNO symbol master URL in broker config.")

        url = fno_details["url"]
        headers = { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36' }
        try:
            r = requests.get(url, headers=headers, timeout=30)
            r.raise_for_status()

            with open(self.CACHE_FILE, "wb") as f:
                f.write(r.content)
            print(f"[SymbolMaster] Successfully downloaded and saved to '{self.CACHE_FILE}'")
        except requests.RequestException as e:
            raise RuntimeError(f"Failed to download symbol master from {url}: {e}")

    def _ensure_cache_updated(self):
        if not self._is_cache_valid():
            self._download_master()
            self._df = None # Force reload on next access

    def _load_df(self):
        if self._df is None:
            print("[SymbolMaster] Loading symbol master into memory...")
            try:
                self._df = pd.read_csv(self.CACHE_FILE, header=None)
                self._df.columns = [
                    'exchange', 'symbol_id', 'symbol', 'instrument_type', 'lot_size',
                    'tick_size', 'isin', 'trading_segment', 'expiry_date', 'strike_price',
                    'option_type', 'underlying_symbol', 'description'
                ]
                self._df.set_index('symbol', inplace=True)
                print("[SymbolMaster] Load complete.")
            except Exception as e:
                raise RuntimeError(f"Failed to load or parse '{self.CACHE_FILE}': {e}")

    def get_lot_size(self, symbol: str) -> int:
        self._load_df()
        try:
            lot_size = self._df.loc[symbol, 'lot_size']
            return int(lot_size)
        except KeyError:
            raise RuntimeError(f"Symbol '{symbol}' not found in the symbol master.")
        except (ValueError, TypeError):
             raise RuntimeError(f"Could not parse lot size for '{symbol}'.")


# ============================== LOGIN (v3 api-t1) =============================
CONFIG_FILE = "fyers_login_details.json"
TOKENS_DIR = "AccessToken"
TODAY = str(datetime.date.today())
TOKEN_PATH = os.path.join(TOKENS_DIR, f"{TODAY}.json")

def load_or_prompt_creds() -> Dict[str, str]:
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

def build_auth_url(app_id: str, redirect_uri: str, state: str = "sample_state") -> str:
    base = "https://api-t1.fyers.in/api/v3/generate-authcode"
    params = (
        f"client_id={quote(app_id)}"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        f"&response_type=code"
        f"&state={quote(state)}"
        f"&scope=openid"
        f"&nonce={int(time.time())}"
    )
    return f"{base}?{params}"

def extract_code(user_input: str) -> str:
    if user_input.startswith("http://") or user_input.startswith("https://"):
        q = parse_qs(urlparse(user_input).query)
        code = q.get("code", [None])[0]
        if not code:
            raise ValueError("No 'code' param found in the provided URL.")
        return code
    return user_input

def sha256_appIdHash(app_id: str, secret_id: str) -> str:
    return hashlib.sha256(f"{app_id}:{secret_id}".encode("utf-8")).hexdigest()

def validate_authcode(app_id: str, secret_id: str, auth_code: str, max_retries: int = 5) -> Dict[str, Any]:
    url = "https://api-t1.fyers.in/api/v3/validate-authcode"
    payload = {"grant_type": "authorization_code", "appIdHash": sha256_appIdHash(app_id, secret_id), "code": auth_code}
    headers = {"Content-Type": "application/json"}
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=20)
            if r.status_code == 503:
                sleep_s = min(2 ** attempt, 30)
                print(f"[{attempt}/{max_retries}] 503 from auth server. Retrying in {sleep_s}s...")
                time.sleep(sleep_s); continue
            r.raise_for_status()
            data = r.json()
            if data.get("s") == "error":
                raise RuntimeError(f"Fyers error {data.get('code')}: {data.get('message')}")
            return data
        except requests.RequestException as e:
            if attempt == max_retries: raise
            sleep_s = min(2 ** attempt, 30)
            print(f"[{attempt}/{max_retries}] Network error: {e}. Retrying in {sleep_s}s...")
            time.sleep(sleep_s)

def get_access_token() -> Dict[str, str]:
    creds = load_or_prompt_creds()
    app_id = creds["api_key"]
    secret_id = creds["api_secret"]
    redirect_uri = creds["redirect_url"]

    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, "r") as f:
            access_token = json.load(f)
        print(f"Access Token loaded from file for {TODAY}")
        return {"app_id": app_id, "access_token": access_token}

    auth_url = build_auth_url(app_id, redirect_uri)
    print("\nLogin URL (open in browser, allow & complete login):")
    print(auth_url)

    user_val = input("\nPaste the FULL redirect URL after login, or just the 'code=' value here: ").strip()
    try:
        auth_code = extract_code(user_val)
    except Exception as e:
        print(f"Could not extract code: {e}")
        sys.exit(1)

    try:
        token_resp = validate_authcode(app_id, secret_id, auth_code)
        access_token = token_resp.get("access_token")
        if not access_token:
            raise RuntimeError(f"Unexpected token response: {token_resp}")

        os.makedirs(TOKENS_DIR, exist_ok=True)
        with open(TOKEN_PATH, "w") as f:
            json.dump(access_token, f)
        print("\nLogin successful.")
        print(f"Token saved to: {TOKEN_PATH}")
        return {"app_id": app_id, "access_token": access_token}
    except Exception as e:
        print(f"\nLogin Failed: {e}")
        sys.exit(1)

# ============================== CONFIG ========================================
CONFIG = {
    "trade_entry_time": dt.time(hour=9, minute=45, second=10),
    "square_off_time": dt.time(hour=15, minute=10, second=0),
    "lots": 1,
    "stop_loss_pts": 10.0,
    "tsl_pts": 2.0,
    "indices": [
        {"symbol": "NSE:NIFTY50-INDEX", "strike_round": 50},
        {"symbol": "NSE:NIFTYBANK-INDEX", "strike_round": 100},
        {"symbol": "NSE:FINNIFTY-INDEX", "strike_round": 50},
    ]
}

def get_index_ltp(fyers: fyersModel.FyersModel, index_symbol: str) -> float:
    """
    Retrieves the Last Traded Price (LTP) for a given index.
    """
    data = {"symbols": index_symbol}
    resp = fyers.quotes(data=data)
    if resp.get("s") == "ok" and resp.get("d"):
        ltp = resp["d"][0]["v"]["lp"]
        return float(ltp)
    raise RuntimeError(f"Failed to fetch LTP for {index_symbol}: {resp.get('message')}")


def round_to_nearest(x: float, base: int) -> int:
    return int(base * round(float(x)/base))


import re
from datetime import datetime
import calendar

def parse_expiry_from_symbol(symbol: str) -> Optional[datetime]:
    match = re.search(r'([A-Z]+)(\d{2})([A-Z]{3}|[1-9OND])(\d{2})?', symbol)
    if not match:
        return None

    _, year_str, month_str, day_str = match.groups()

    # Make the year calculation robust to century changes (e.g., '99' -> '00')
    current_year_full = datetime.now().year
    current_century = (current_year_full // 100) * 100
    symbol_year_two_digit = int(year_str)
    current_year_two_digit = current_year_full % 100

    full_year = current_century + symbol_year_two_digit
    # Heuristic: If the symbol year is much smaller than the current year
    # (e.g., current is 99, symbol is 01), it's next century.
    if symbol_year_two_digit < current_year_two_digit and current_year_two_digit - symbol_year_two_digit > 80:
        full_year += 100

    month_map = {
        '1':'Jan','2':'Feb','3':'Mar','4':'Apr','5':'May','6':'Jun',
        '7':'Jul','8':'Aug','9':'Sep','O':'Oct','N':'Nov','D':'Dec'
    }

    try:
        if month_str.isalpha(): # Monthly contract like 'DEC'
            month_full = month_str
            temp_date = datetime.strptime(f"{full_year}-{month_full}-01", '%Y-%b-%d')
            month_calendar = calendar.monthcalendar(temp_date.year, temp_date.month)
            last_thursday = [week[calendar.THURSDAY] for week in month_calendar if week[calendar.THURSDAY] != 0][-1]
            return temp_date.replace(day=last_thursday)
        else: # Weekly contract like '9' for Sep
            month_full = month_map[month_str]
            return datetime.strptime(f"{full_year}-{month_full}-{day_str}", '%Y-%b-%d')
    except (ValueError, KeyError):
        return None


def resolve_option_symbols(fyers: fyersModel.FyersModel, index_symbol: str, atm_strike: int) -> Tuple[str, str]:
    # By removing "strikecount", we request the full option chain, including all expiries.
    data = {"symbol": index_symbol}
    resp = fyers.optionchain(data=data)

    if resp.get("s") != "ok":
        raise RuntimeError(f"Failed to fetch option chain for {index_symbol}: {resp.get('message', 'No message')}")

    chain = (resp.get("data") or {}).get("optionsChain", [])
    if not chain:
        raise RuntimeError(f"Option chain for {index_symbol} is empty.")

    for opt in chain:
        opt['parsed_expiry'] = parse_expiry_from_symbol(opt.get('symbol', ''))

    valid_options = [opt for opt in chain if opt.get('parsed_expiry')]
    if not valid_options:
        raise RuntimeError(f"Could not parse expiry from any option symbols for {index_symbol}.")

    today = datetime.now().date()
    future_options = [opt for opt in valid_options if opt['parsed_expiry'].date() >= today]

    target_expiry = None
    if future_options:
        # If we have future options, find the earliest one
        target_expiry = min(opt['parsed_expiry'] for opt in future_options)
        print(f"[{index_symbol}] Nearest future expiry found: {target_expiry.date()}")
    else:
        # Fallback: No future options found, use the latest available expired option
        all_expiries = [opt['parsed_expiry'] for opt in valid_options]
        if all_expiries:
            target_expiry = max(all_expiries)
            print(f"[{index_symbol}] WARNING: No future expiry found. Falling back to latest available expiry: {target_expiry.date()}")
        else:
             raise RuntimeError(f"Could not find any valid expiry dates for {index_symbol}.")

    # Now, filter options based on the selected target_expiry
    nearest_expiry_options = [opt for opt in valid_options if opt['parsed_expiry'] == target_expiry]

    # Find the closest CE and PE to the ATM strike from that filtered list
    ce_options = [opt for opt in nearest_expiry_options if opt.get('option_type') == 'CE']
    pe_options = [opt for opt in nearest_expiry_options if opt.get('option_type') == 'PE']

    if not ce_options or not pe_options:
        raise RuntimeError(f"Could not find both CE/PE options for the selected expiry of {index_symbol}.")

    ce_closest = min(ce_options, key=lambda x: abs(x.get('strike_price', float('inf')) - atm_strike))
    pe_closest = min(pe_options, key=lambda x: abs(x.get('strike_price', float('inf')) - atm_strike))

    return ce_closest['symbol'], pe_closest['symbol']


def marketorder_buy(fyers: fyersModel.FyersModel, symbol: str, quantity: int) -> str:
    """
    Places a market order to buy a specified quantity of an instrument.
    """
    data = {
        "symbol": symbol, "qty": quantity, "type": 2,  # Market
        "side": 1,                                 # BUY
        "productType": "NRML",
        "validity": "DAY",
    }
    resp = fyers.place_order(data=data)
    if resp.get("s") == "ok":
        return resp["id"]

    message = resp.get('message', '')
    if "Could not authenticate the user" in message:
        print("\n--- AUTHENTICATION ERROR ---")
        print("Your access token is invalid for trading.")
        print("Please delete the 'AccessToken' folder and run the script again to force a fresh login.")
        print("--------------------------\n")
        sys.exit(1)

    raise RuntimeError(f"Failed to place BUY order: {message}")


def marketorder_sell(fyers: fyersModel.FyersModel, symbol: str, quantity: int) -> str:
    """
    Places a market order to sell a specified quantity of an instrument.
    """
    data = {
        "symbol": symbol, "qty": quantity, "type": 2,  # Market
        "side": -1,                                # SELL
        "productType": "NRML",
        "validity": "DAY",
    }
    resp = fyers.place_order(data=data)
    if resp.get("s") == "ok":
        return resp["id"]

    message = resp.get('message', '')
    if "Could not authenticate the user" in message:
        print("\n--- AUTHENTICATION ERROR ---")
        print("Your access token is invalid for trading.")
        print("Please delete the 'AccessToken' folder and run the script again to force a fresh login.")
        print("--------------------------\n")
        sys.exit(1)

    raise RuntimeError(f"Failed to place SELL order: {message}")


def get_order_status(fyers: fyersModel.FyersModel, order_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetches the status of a specific order.
    FYERS `status`: 1=cancelled, 2=traded/filled, 4=transit, 5=rejected, 6=pending
    """
    data = {"id": order_id}
    resp = fyers.orderbook(data=data)
    if resp.get("s") == "ok" and resp.get("orderBook"):
        return resp["orderBook"][0]
    return None


def cancel_order(fyers: fyersModel.FyersModel, order_id: str):
    """
    Attempts to cancel an order based on its ID.
    """
    data = {"id": order_id}
    resp = fyers.cancel_order(data=data)
    if resp.get("s") != "ok":
        print(f"Failed to cancel order {order_id}: {resp.get('message')}")


# ============================== STATE & WEBSOCKET =================================
class MultiIndexState:
    def __init__(self):
        self.active_trades = {}  # Key: index_symbol, Value: trade_details

    def add_trade(self, index_symbol, trade_details):
        self.active_trades[index_symbol] = trade_details

    def get_all_symbols(self):
        symbols = []
        for trade in self.active_trades.values():
            symbols.append(trade['ce_symbol'])
            symbols.append(trade['pe_symbol'])
        return symbols

STATE = MultiIndexState()
fyers_socket = None

def on_message(msg: Dict[str, Any]):
    symbol = msg.get("symbol")
    ltp = msg.get("ltp")
    if not (symbol and ltp):
        return

    for index_symbol, trade in STATE.active_trades.items():
        if symbol == trade['ce_symbol']:
            trade['ce_ltp'] = float(ltp)
            break
        elif symbol == trade['pe_symbol']:
            trade['pe_ltp'] = float(ltp)
            break

def on_error(msg): print(f"[ws:error] {msg}")
def on_close(msg): print(f"[ws:close] {msg}")

def on_open():
    symbols_to_subscribe = STATE.get_all_symbols()
    if symbols_to_subscribe:
        fyers_socket.subscribe(symbols=symbols_to_subscribe, data_type="SymbolUpdate")
        print(f"[ws:open] Subscribed to {symbols_to_subscribe}")

# ============================== MAIN ==========================================
if __name__ == "__main__":
    auth = get_access_token()
    APP_ID = auth["app_id"]
    ACCESS_TOKEN = auth["access_token"]
    FYERS = fyersModel.FyersModel(client_id=APP_ID, is_async=False, token=ACCESS_TOKEN, log_path="")

    # Initialize the SymbolMaster to download/load the cache.
    try:
        SYMBOL_MASTER = SymbolMaster(FYERS)
    except Exception as e:
        print(f"FATAL: Could not initialize SymbolMaster: {e}")
        sys.exit(1)

    print("\n--- Performing Pre-trade Symbol and Lot Size Check ---")
    for index_config in CONFIG["indices"]:
        index_symbol = index_config["symbol"]
        strike_round = index_config["strike_round"]
        try:
            ltp = get_index_ltp(FYERS, index_symbol)
            atm_strike = round_to_nearest(ltp, strike_round)
            # Resolve symbols first
            ce_symbol, pe_symbol = resolve_option_symbols(FYERS, index_symbol, atm_strike)
            # Then get lot size from our reliable master
            lot_size = SYMBOL_MASTER.get_lot_size(ce_symbol)

            print(f"[{index_symbol}] LTP: {ltp}, ATM Strike: {atm_strike}, Lot Size: {lot_size}")
            print(f"    -> Potential CE: {ce_symbol}")
            print(f"    -> Potential PE: {pe_symbol}")
        except Exception as e:
            print(f"[{index_symbol}] Error during pre-trade check: {e}")
    print("----------------------------------------------------\n")

    print("Waiting for trade entry time:", CONFIG["trade_entry_time"])
    while dt.datetime.now().time() < CONFIG["trade_entry_time"]:
        time.sleep(1)

    # --- ENTER TRADES FOR ALL CONFIGURED INDICES ---
    for index_config in CONFIG["indices"]:
        index_symbol = index_config["symbol"]
        strike_round = index_config["strike_round"]

        try:
            print(f"\n--- Processing: {index_symbol} ---")
            ltp = get_index_ltp(FYERS, index_symbol)
            atm_strike = round_to_nearest(ltp, strike_round)
            print(f"LTP: {ltp}, ATM Strike: {atm_strike}")

            ce_symbol, pe_symbol = resolve_option_symbols(FYERS, index_symbol, atm_strike)
            print(f"CE Symbol: {ce_symbol}, PE Symbol: {pe_symbol}")

            lot_size = SYMBOL_MASTER.get_lot_size(ce_symbol)
            qty = CONFIG["lots"] * lot_size
            print(f"Lot Size: {lot_size}, Quantity: {qty}")

            ce_order_id = marketorder_sell(FYERS, ce_symbol, qty)
            pe_order_id = marketorder_sell(FYERS, pe_symbol, qty)
            print(f"Placed SELL orders -> CE: {ce_order_id}, PE: {pe_order_id}")

            time.sleep(2)  # Wait for order execution

            ce_order = get_order_status(FYERS, ce_order_id)
            pe_order = get_order_status(FYERS, pe_order_id)

            if ce_order and ce_order['status'] == 2 and pe_order and pe_order['status'] == 2:
                ce_sell_price = float(ce_order['tradedPrice'])
                pe_sell_price = float(pe_order['tradedPrice'])
                sell_premium = ce_sell_price + pe_sell_price

                trade_details = {
                    "ce_symbol": ce_symbol, "pe_symbol": pe_symbol,
                    "ce_ltp": ce_sell_price, "pe_ltp": pe_sell_price,
                    "qty": qty, "sell_premium": sell_premium,
                    "premium_base": sell_premium, "sl_premium": sell_premium + CONFIG["stop_loss_pts"]
                }
                STATE.add_trade(index_symbol, trade_details)
                print(f"Trade for {index_symbol} is active. Combined Premium: {sell_premium:.2f}")
            else:
                print(f"Orders for {index_symbol} not filled. Cleaning up...")
                if ce_order and ce_order['filledQty'] > 0:
                    marketorder_buy(FYERS, ce_symbol, ce_order['filledQty'])
                if pe_order and pe_order['filledQty'] > 0:
                    marketorder_buy(FYERS, pe_symbol, pe_order['filledQty'])
        except Exception as e:
            print(f"Error processing {index_symbol}: {e}")

    if not STATE.active_trades:
        print("No trades were activated. Exiting.")
        sys.exit(0)

    # --- CONNECT WEBSOCKET AND START MONITORING ---
    fyers_socket = data_ws.FyersDataSocket(
        access_token=f"{APP_ID}:{ACCESS_TOKEN}",
        log_path="", litemode=True, write_to_file=False, reconnect=True,
        on_connect=on_open, on_close=on_close, on_error=on_error, on_message=on_message
    )
    print("\n[start] Connecting WebSocket…")
    fyers_socket.connect()

    while True:
        if dt.datetime.now().time() >= CONFIG["square_off_time"]:
            print("\nMarket close time reached. Exiting all positions.")
            for index_symbol, trade in list(STATE.active_trades.items()):
                print(f"Squaring off {index_symbol}")
                marketorder_buy(FYERS, trade['ce_symbol'], trade['qty'])
                marketorder_buy(FYERS, trade['pe_symbol'], trade['qty'])
            break

        for index_symbol, trade in list(STATE.active_trades.items()):
            if trade['ce_ltp'] == 0.0 or trade['pe_ltp'] == 0.0:
                continue

            current_premium = trade['ce_ltp'] + trade['pe_ltp']

            print(f"\n--- {index_symbol} ---")
            print(f"Sell Premium: {trade['sell_premium']:.2f}, SL: {trade['sl_premium']:.2f}")
            print(f"Current Premium: {current_premium:.2f} (CE: {trade['ce_symbol']} @ {trade['ce_ltp']}, PE: {trade['pe_symbol']} @ {trade['pe_ltp']})")
            print(f"Gain: {(trade['sell_premium'] - current_premium):.2f}, MTM: {((trade['sell_premium'] - current_premium) * trade['qty']):.2f}")

            # Trailing Stop Loss (TSL)
            if current_premium < trade['premium_base'] - CONFIG["tsl_pts"]:
                trade['premium_base'] = current_premium
                trade['sl_premium'] = current_premium + CONFIG["stop_loss_pts"]
                print(f"TSL Adjusted -> New SL Premium: {trade['sl_premium']:.2f}")

            # Stop-loss
            if current_premium >= trade['sl_premium']:
                print(f"Exiting {index_symbol} due to stop loss trigger.")
                marketorder_buy(FYERS, trade['ce_symbol'], trade['qty'])
                marketorder_buy(FYERS, trade['pe_symbol'], trade['qty'])
                del STATE.active_trades[index_symbol]

        if not STATE.active_trades:
            print("All trades have been closed. Exiting.")
            break

        time.sleep(1)

    print('End of Program')
