from __future__ import annotations

import os, json, time, datetime, hashlib, sys
from urllib.parse import urlparse, parse_qs, quote
from typing import Dict, Any, Optional, Tuple

import requests
import datetime as dt

from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws


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
    "strike_points": 0,  # 0 for ATM
    "lots": 1,
    "stop_loss": 10.0,
    "tsl": 2.0,
    "lot_size": 50,
}

def get_nifty_ltp(fyers: fyersModel.FyersModel) -> float:
    """
    Retrieves the Last Traded Price (LTP) for the NIFTY 50 index.
    """
    data = {"symbols": "NSE:NIFTY50-INDEX"}
    resp = fyers.quotes(data=data)
    if resp.get("s") == "ok":
        ltp = resp["d"][0]["v"]["lp"]
        return float(ltp)
    raise RuntimeError(f"Failed to fetch NIFTY LTP: {resp.get('message')}")


def round_to_nearest_50(x: float) -> int:
    return int(round(x / 50.0) * 50)


def resolve_option_symbols(fyers: fyersModel.FyersModel, atm_strike: int) -> Tuple[str, str]:
    """
    Queries FYERS option chain for NIFTY and returns (CE_symbol, PE_symbol)
    for the nearest strike to ATM of the earliest expiry.
    """
    data = {"symbol": "NSE:NIFTY50-INDEX", "strikecount": 2}
    resp = fyers.optionchain(data=data)
    if resp.get("s") != "ok":
        raise RuntimeError(f"Failed to fetch option chain: {resp.get('message')}")

    chain = resp["data"]["optionChain"]

    # Find earliest expiry
    expiries = sorted(list(set(opt['expiryDate'] for opt in chain)))
    earliest_expiry = expiries[0]

    # Filter for earliest expiry
    chain = [opt for opt in chain if opt['expiryDate'] == earliest_expiry]

    # Find closest CE and PE to the ATM strike
    ce_options = [opt for opt in chain if opt['optionType'] == 'CE']
    pe_options = [opt for opt in chain if opt['optionType'] == 'PE']

    if not ce_options or not pe_options:
        raise RuntimeError("Could not find both CE and PE options for the earliest expiry.")

    ce_closest = min(ce_options, key=lambda x: abs(x['strikePrice'] - atm_strike))
    pe_closest = min(pe_options, key=lambda x: abs(x['strikePrice'] - atm_strike))

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
    raise RuntimeError(f"Failed to place BUY order: {resp.get('message')}")


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
    raise RuntimeError(f"Failed to place SELL order: {resp.get('message')}")


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
class TradeState:
    def __init__(self):
        self.ce_ltp = 0.0
        self.pe_ltp = 0.0
        self.ce_symbol = None
        self.pe_symbol = None

STATE = TradeState()
fyers_socket = None

def on_message(msg: Dict[str, Any]):
    symbol = msg.get("symbol")
    ltp = msg.get("ltp")
    if symbol and ltp:
        if symbol == STATE.ce_symbol:
            STATE.ce_ltp = float(ltp)
        elif symbol == STATE.pe_symbol:
            STATE.pe_ltp = float(ltp)

def on_error(msg): print("[ws:error]", msg)
def on_close(msg): print("[ws:close]", msg)

def on_open():
    if STATE.ce_symbol and STATE.pe_symbol:
        symbols_to_subscribe = [STATE.ce_symbol, STATE.pe_symbol]
        fyers_socket.subscribe(symbols=symbols_to_subscribe, data_type="SymbolUpdate")
        print(f"[ws:open] Subscribed to {symbols_to_subscribe}")

# ============================== MAIN ==========================================
if __name__ == "__main__":
    # Authenticate and initialize the Fyers API client
    auth = get_access_token()
    APP_ID = auth["app_id"]
    ACCESS_TOKEN = auth["access_token"]
    FYERS = fyersModel.FyersModel(client_id=APP_ID, is_async=False, token=ACCESS_TOKEN, log_path="")

    # Wait until the specified trade entry time
    print("Waiting for trade entry time:", CONFIG["trade_entry_time"])
    while dt.datetime.now().time() < CONFIG["trade_entry_time"]:
        time.sleep(1)

    # 1. Get Nifty LTP and calculate the At-The-Money (ATM) strike price
    nifty_ltp = get_nifty_ltp(FYERS)
    print('Nifty LTP:', nifty_ltp)
    atm_strike = round_to_nearest_50(nifty_ltp)
    print('ATM Strike:', atm_strike)

    # 2. Resolve the trading symbols for the ATM call and put options
    ce_symbol, pe_symbol = resolve_option_symbols(FYERS, atm_strike)
    print('CE Symbol:', ce_symbol)
    print('PE Symbol:', pe_symbol)

    # 3. Place market orders to sell the call and put options
    qty = CONFIG["lots"] * CONFIG["lot_size"]
    ce_order_id = marketorder_sell(FYERS, ce_symbol, qty)
    pe_order_id = marketorder_sell(FYERS, pe_symbol, qty)
    print(f"Placed SELL orders -> CE: {ce_order_id}, PE: {pe_order_id}")

    time.sleep(5)  # Allow time for orders to execute

    # 4. Verify the status of the placed orders
    ce_order = get_order_status(FYERS, ce_order_id)
    pe_order = get_order_status(FYERS, pe_order_id)

    ce_filled = ce_order and ce_order['status'] == 2
    pe_filled = pe_order and pe_order['status'] == 2

    # 5. If both orders are filled, start monitoring the position
    if ce_filled and pe_filled:
        ce_sell_price = float(ce_order['tradedPrice'])
        pe_sell_price = float(pe_order['tradedPrice'])

        print('CE sell price:', round(ce_sell_price, 2))
        print('PE sell price:', round(pe_sell_price, 2))

        sell_premium = ce_sell_price + pe_sell_price
        print('Combined premium:', round(sell_premium, 2))

        premium_base = sell_premium
        sl_premium = sell_premium + CONFIG["stop_loss"]

        # Connect to the WebSocket for real-time price updates
        STATE.ce_symbol = ce_symbol
        STATE.pe_symbol = pe_symbol

        fyers_socket = data_ws.FyersDataSocket(
            access_token=f"{APP_ID}:{ACCESS_TOKEN}",
            log_path="", litemode=True, write_to_file=False, reconnect=True,
            on_connect=on_open, on_close=on_close, on_error=on_error, on_message=on_message
        )
        print("[start] Connecting WebSocket…")
        fyers_socket.connect()

        # Wait for the initial price ticks to be received via the WebSocket
        while STATE.ce_ltp == 0.0 or STATE.pe_ltp == 0.0:
            print("Waiting for initial price ticks via WebSocket...")
            time.sleep(1)

        # Main monitoring loop to manage the open position
        while True:
            current_premium = STATE.ce_ltp + STATE.pe_ltp

            print('===============')
            print('Combined premium: ', round(sell_premium, 2))
            print('Stop loss: ', round(sl_premium, 2))
            print(f'Current Premium: {round(current_premium, 2)} (CE: {STATE.ce_ltp}, PE: {STATE.pe_ltp})')
            print('Gain: ', round((sell_premium - current_premium), 2))
            print('MTM: ', round((sell_premium - current_premium) * qty, 2))

            # Trailing Stop Loss (TSL) logic
            if current_premium < premium_base - CONFIG["tsl"]:
                premium_decrease = premium_base - current_premium
                premium_base = current_premium
                sl_premium = current_premium + CONFIG["stop_loss"]
                print(f"TSL Adjusted: New SL Premium {sl_premium}, Profit Locked {premium_decrease}")

            # Stop-loss exit condition
            if current_premium >= sl_premium:
                print("Exiting positions due to stop loss trigger.")
                marketorder_buy(FYERS, ce_symbol, qty)
                marketorder_buy(FYERS, pe_symbol, qty)
                break

            # Square-off time exit condition
            if dt.datetime.now().time() >= CONFIG["square_off_time"]:
                print("Market close time reached. Exiting positions.")
                marketorder_buy(FYERS, ce_symbol, qty)
                marketorder_buy(FYERS, pe_symbol, qty)
                break

            time.sleep(1)
    else:
        # Handle partial fills or failures
        print("Orders not completely filled. Cleaning up...")
        if ce_order and ce_order['filledQty'] > 0:
            print(f"CE order has filled quantity of {ce_order['filledQty']}. Buying back.")
            marketorder_buy(FYERS, ce_symbol, ce_order['filledQty'])
        elif ce_order and ce_order['status'] == 6: # pending
             cancel_order(FYERS, ce_order_id)

        if pe_order and pe_order['filledQty'] > 0:
            print(f"PE order has filled quantity of {pe_order['filledQty']}. Buying back.")
            marketorder_buy(FYERS, pe_symbol, pe_order['filledQty'])
        elif pe_order and pe_order['status'] == 6: # pending
            cancel_order(FYERS, pe_order_id)

    print('End of Program')
