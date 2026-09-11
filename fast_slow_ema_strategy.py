# Fast Slow EMA Strategy - Strict Next Candle Entry (Multi-Broker Abstracted)
# -*- coding: utf-8 -*-
"""
- ENTRY (Bullish, using VWAP & ENTRY_FAST_EMA):
    * Signal candle (must be a fresh cross):
        - Previous candle's close was AT or BELOW its VWAP.
        - Current candle's close is ABOVE its VWAP.
        - EMA_fast > VWAP (trend confirmation).
        - (optional) candle is green if REQUIRE_GREEN_SIGNAL is True.
        - tiny-candle filter via MIN_RANGE_PCT if enabled.
    * ENTRY:
        - Only allowed on the VERY NEXT candle.
        - During that next candle, if any tick LTP > signal_high -> market BUY.

- SIMULTANEOUS STOPLOSS & TARGET EXIT PROTECTION:
    * Placed immediately upon entry execution.
    * Attempts FYERS GTT OCO placement (type 2, leg1=Target Limit, leg2=Stop Loss SL-L)
      or falls back to placing both resting Target Limit Sell and Stop Loss SL-L
      Sell orders simultaneously.
    * For Stop Loss SL-L orders, limitPrice is set strictly below stopPrice
      (sl_trigger - 0.20, rounded to 0.05 tick size) to avoid FYERS error -50.

- AUTOMATIC ORDER CANCELLATION & POSITION RECONCILIATION:
    * Hooked into Order WebSocket updates (on_order_update) for sub-100ms response time
      and backed up by 20-second polling loops (sync_broker_positions).
    * If a position closes (Target hit, Stop Loss triggered, or manually closed in
      the FYERS app), the bot detects net quantity = 0 for that symbol and
      instantly cancels the remaining open protection order leg.
    * Closing one position manually in FYERS will only cancel that symbol's orders,
      leaving all other active positions intact.
"""

from __future__ import annotations
import os
import sys
import json
import time
import math
import argparse
import threading
import atexit
import glob
import socket
import datetime
import webbrowser
from abc import ABC, abstractmethod
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Dict, Optional, Any, List
from datetime import datetime as dt, timedelta

import requests
from requests.adapters import HTTPAdapter
import pandas as pd
import numpy as np
import pytz

PRIMARY_STATIC_IP = None
PROXY_URL = None  # e.g. "https://user:pass@dc-mum-007.staticip.in" -- set via fyers_login_details.json
FORCE_IPV4 = True  # Prefer IPv4 for ALL outbound HTTP calls.


class SourceAddressAdapter(HTTPAdapter):
    """Binds requests HTTP/HTTPS socket connections to a specific local source IP address."""

    def __init__(self, source_address: str, **kwargs):
        self.source_address = (source_address, 0)
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs['source_address'] = self.source_address
        return super().init_poolmanager(*args, **kwargs)


# Try to import Fyers packages - run in test mode if missing
try:
    from fyers_apiv3 import fyersModel
    from fyers_apiv3.FyersWebsocket import data_ws
    from fyers_apiv3.FyersWebsocket import order_ws
    from pkg_resources import resource_filename

    # Monkey-patch fyers_apiv3 SDK bug in SymbolConversion where client_id: is stripped
    if hasattr(data_ws, "SymbolConversion"):
        def _patched_symbol_conversion_init(self, access_token: str, data_type: str, log_path: str):
            self.data_type = data_type
            self.access_token = access_token
            self.log_path = log_path or ""
            self.symbols_token_api = "https://api-t1.fyers.in/data/symbol-token"

        def _patched_symbol_to_hsmtoken(self, symbols: list):
            try:
                data = {"symbols": symbols}
                session = requests.Session()
                if PRIMARY_STATIC_IP:
                    try:
                        adapter = SourceAddressAdapter(PRIMARY_STATIC_IP)
                        session.mount("http://", adapter)
                        session.mount("https://", adapter)
                    except Exception:
                        pass
                response = session.post(
                    url=self.symbols_token_api,
                    headers={
                        "Authorization": self.access_token,
                        "Content-Type": "application/json",
                    },
                    json=data,
                    timeout=15
                )
                response_data = response.json()
                datadict = {}
                file_path = resource_filename('fyers_apiv3.FyersWebsocket', 'map.json')
                with open(file_path, "r") as file:
                    mapper = json.load(file)
                index_dict = mapper["index_dict"]
                exch_seg_dict = mapper["exch_seg_dict"]
                wrong_symbol = []
                dp_index_flag = False

                if response_data.get('s') == "ok":
                    for symbol, fytoken in response_data.get("validSymbol", {}).items():
                        ex_sg = fytoken[:4]
                        if ex_sg not in exch_seg_dict:
                            continue
                        segment = exch_seg_dict[ex_sg]
                        symbol_split = symbol.split("-")
                        update_dict = True
                        if len(symbol_split) > 1 and symbol_split[-1] == "INDEX" and self.data_type != "DepthUpdate":
                            if symbol in index_dict:
                                exch_token = index_dict[symbol]
                            else:
                                exch_token = symbol.split(":")[1].split("-")[0]
                            hsm_symbol = "if" + "|" + segment + "|" + exch_token
                        elif self.data_type == "DepthUpdate" and symbol_split[-1] != "INDEX":
                            exch_token = fytoken[10:]
                            hsm_symbol = "dp" + "|" + segment + "|" + exch_token
                        elif self.data_type == "SymbolUpdate":
                            exch_token = fytoken[10:]
                            hsm_symbol = "sf" + "|" + segment + "|" + exch_token
                        elif self.data_type == "DepthUpdate" and symbol_split[-1] == "INDEX":
                            update_dict = False
                            dp_index_flag = True

                        if update_dict:
                            datadict[hsm_symbol] = symbol
                    if response_data.get("invalidSymbol"):
                        wrong_symbol = response_data.get("invalidSymbol")
                    return (datadict, wrong_symbol, dp_index_flag, "")
                elif response_data.get('s') == "error":
                    return ({}, [], dp_index_flag, response_data.get("message", "Symbol token conversion error"))
            except Exception as e:
                if hasattr(self, "data_logger") and self.data_logger:
                    self.data_logger.exception(e)
                return ({}, [], False, str(e))

        data_ws.SymbolConversion.__init__ = _patched_symbol_conversion_init
        data_ws.SymbolConversion.symbol_to_hsmtoken = _patched_symbol_to_hsmtoken
except Exception:
    fyersModel = None
    data_ws = None
    order_ws = None


class AuthCodeHandler(BaseHTTPRequestHandler):
    """Local HTTP Server request handler to auto-capture auth_code from browser redirect."""
    auth_code = None

    def do_GET(self):
        query = urlparse(self.path).query
        params = parse_qs(query)
        code_val = (params.get("code") or params.get("auth_code") or [None])[0]
        if code_val:
            AuthCodeHandler.auth_code = code_val
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>Login successful! You can close this tab and return to the Python app.</h1></body></html>")
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, format, *args):
        pass


# ---------------------------- USER-CONFIGURED CONSTANTS ----------------------------
TIMEFRAME_MIN = 5  # Any TF in minutes (1,2,3,5,10,15,30,60,...)

RISK_REWARD_RATIO = 1.0  # Target = entry + (entry - stop) * this ratio. 1.0 = 1:1, 2.0 = 1:2, etc.
ENTRY_FAST_EMA = 9

MIN_RANGE_PCT = 0.0
REQUIRE_GREEN_SIGNAL = True
TICK_SIZE = 0.05
LAST_ENTRY_TIME = "15:00"  # No new entries after 3:00 PM IST


def round_to_tick(price: float, tick: float = TICK_SIZE) -> float:
    """Round `price` onto the exchange's tick grid."""
    try:
        price = float(price)
    except (TypeError, ValueError):
        return price
    if price <= 0:
        return price
    return round(round(price / tick) * tick, 2)


SYMBOLS = [
    'NSE:RELIANCE-EQ', 'NSE:HDFCBANK-EQ', 'NSE:ICICIBANK-EQ', 'NSE:SBIN-EQ', 'NSE:INFY-EQ',
    'NSE:AXISBANK-EQ', 'NSE:BHARTIARTL-EQ', 'NSE:TCS-EQ', 'NSE:BAJFINANCE-EQ', 'NSE:KOTAKBANK-EQ',

    'NSE:LT-EQ', 'NSE:ADANIENT-EQ', 'NSE:ADANIPORTS-EQ', 'NSE:TATASTEEL-EQ', 'NSE:HINDALCO-EQ',
    'NSE:MARUTI-EQ', 'NSE:M&M-EQ', 'NSE:NTPC-EQ', 'NSE:POWERGRID-EQ', 'NSE:ONGC-EQ',

    'NSE:COALINDIA-EQ', 'NSE:JSWSTEEL-EQ', 'NSE:BAJAJFINSV-EQ', 'NSE:BPCL-EQ', 'NSE:SUNPHARMA-EQ',
    'NSE:HCLTECH-EQ', 'NSE:TECHM-EQ', 'NSE:ITC-EQ', 'NSE:TATACONSUM-EQ', 'NSE:TITAN-EQ',

    'NSE:EICHERMOT-EQ', 'NSE:BAJAJ-AUTO-EQ', 'NSE:HEROMOTOCO-EQ', 'NSE:ADANIPOWER-EQ', 'NSE:VEDL-EQ',
    'NSE:ETERNAL-EQ', 'NSE:INDUSINDBK-EQ', 'NSE:PNB-EQ', 'NSE:BANKBARODA-EQ', 'NSE:CANBK-EQ',

    'NSE:IDFCFIRSTB-EQ', 'NSE:FEDERALBNK-EQ', 'NSE:RECLTD-EQ', 'NSE:PFC-EQ', 'NSE:GAIL-EQ',
    'NSE:SAIL-EQ', 'NSE:HAL-EQ', 'NSE:BEL-EQ', 'NSE:IRFC-EQ', 'NSE:DLF-EQ'
]

LOG_FILE = "trade_log.csv"
STATE_DUMP = "symbol_states.json"
PARTIAL_CANDLES_FILE = "partial_candles.json"

# Default product type: "CNC" (delivery) or "Intraday"
PRODUCT_TYPE = "CNC"

ALLOC_DEFAULT = 1000.0
ALLOC_MAP = {}

SL_MODE = "signal_low"  # "signal_low" or "swing_low"
SWING_LOOKBACK = 5

MAX_CONCURRENT_POS = 3
DAILY_MAX_LOSS = 50000.0
TRADING_ENABLED = True
RECONCILE_INTERVAL_SECONDS = 20
REARM_INTERVAL_SECONDS = 300
COOLDOWN_CANDLES = 1

TIMEZONE = "Asia/Kolkata"
IST = pytz.timezone(TIMEZONE)

CONFIG_FILE = "fyers_login_details.json"
TOKENS_DIR = "AccessToken"
TODAY = str(datetime.date.today())
TOKEN_PATH = os.path.join(TOKENS_DIR, f"{TODAY}.json")
SETTINGS_FILE = "settings.json"

POSITION_MODE = "qty"
FIXED_QTY = 2
QTY_MAP: Dict[str, int] = {}

REAUTH_ATTEMPTS = 0
MAX_REAUTH_ATTEMPTS = 3
INVALID_SYMBOLS: set = set()

_real_print = print
ALLOWED_SUBSTRINGS = (
    "ENTRY SIGNAL", "[signal:", "[CANDLE]", "[order]", "[auth]", "[ws]",
    "[blocked-entry]", "[entry-debug]", "[reconcile]", "[ENTRY CONFIRMED]",
    "[broker]", "===================", "[notice]", "[mode]", "[cooldown]",
    "[gtt]", "[sync]"
)


def print(*args, **kwargs):
    try:
        s = " ".join(str(x) for x in args)
    except Exception:
        return
    for sub in ALLOWED_SUBSTRINGS:
        if sub in s:
            return _real_print(s, **kwargs)
    return None


# ---------------------------- BROKER ABSTRACTION LAYER ----------------------------
class BaseBrokerClient(ABC):
    """Generic Broker Abstract Base Class allowing seamless multi-broker integration."""

    @abstractmethod
    def place_order(self, symbol: str, qty: int, side: int, product_type: str,
                     order_type: int = 2, limit_price: float = 0.0, stop_price: float = 0.0) -> dict:
        pass

    @abstractmethod
    def place_stoploss_order(self, symbol: str, qty: int, trigger_price: float, product_type: str) -> dict:
        pass

    @abstractmethod
    def place_gtt_oco(self, symbol: str, qty: int, target_price: float, stop_trigger: float, product_type: str) -> dict:
        pass

    @abstractmethod
    def cancel_gtt(self, gtt_id: str) -> dict:
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> dict:
        pass

    @abstractmethod
    def get_history(self, symbol: str, resolution: str, range_from: str, range_to: str) -> dict:
        pass

    @abstractmethod
    def get_positions(self) -> dict:
        pass

    @abstractmethod
    def get_orders(self) -> dict:
        pass


class FyersBrokerAdapter(BaseBrokerClient):
    """Fyers v3 Broker Implementation with Static IP socket binding support."""

    def __init__(self, client_id: str, access_token: str, primary_ip: Optional[str] = None):
        self.client_id = client_id
        self.access_token = access_token
        self.primary_ip = primary_ip
        if fyersModel is not None:
            self.model = fyersModel.FyersModel(
                client_id=client_id, is_async=False, token=access_token, log_path=""
            )
        else:
            self.model = None

    def place_order(self, symbol: str, qty: int, side: int, product_type: str,
                     order_type: int = 2, limit_price: float = 0.0, stop_price: float = 0.0) -> dict:
        if self.model is None:
            return {"s": "error", "message": "Fyers model not initialized"}
        data = {
            "symbol": symbol,
            "qty": qty,
            "type": order_type,
            "side": side,
            "productType": product_type,
            "limitPrice": limit_price if order_type in (1, 4) else 0,
            "stopPrice": stop_price if order_type in (3, 4) else 0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
        }
        return self.model.place_order(data=data)

    def place_stoploss_order(self, symbol: str, qty: int, trigger_price: float, product_type: str) -> dict:
        """Place SELL Stop Loss SL-L (Stop-Limit) order ensuring limitPrice < stopPrice."""
        if self.model is None:
            return {"s": "error", "message": "Fyers model not initialized"}
        sl_trigger = round_to_tick(trigger_price)
        sl_limit = round_to_tick(sl_trigger - 0.20)  # Must be strictly less than trigger for SELL
        return self.place_order(
            symbol=symbol,
            qty=qty,
            side=-1,
            product_type=product_type,
            order_type=4,  # SL-L (Stop-Limit)
            limit_price=sl_limit,
            stop_price=sl_trigger,
        )

    def place_gtt_oco(self, symbol: str, qty: int, target_price: float, stop_trigger: float, product_type: str) -> dict:
        """Place FYERS GTT OCO Order (type: 2 OCO) with Leg1=Target and Leg2=Stoploss."""
        if self.model is None or not hasattr(self.model, "place_gtt"):
            return {"s": "error", "message": "GTT method not supported by this SDK version"}
        tgt_price = round_to_tick(target_price)
        sl_trigger = round_to_tick(stop_trigger)
        sl_limit = round_to_tick(sl_trigger - 0.20)

        data = {
            "type": 2,  # 2 = OCO GTT
            "side": -1,
            "symbol": symbol,
            "productType": product_type,
            "orderInfo": {
                "leg1": {
                    "price": tgt_price,
                    "triggerPrice": tgt_price,
                    "qty": qty
                },
                "leg2": {
                    "price": sl_limit,
                    "triggerPrice": sl_trigger,
                    "qty": qty
                }
            }
        }
        try:
            return self.model.place_gtt(data=data)
        except Exception as e:
            return {"s": "error", "message": str(e)}

    def cancel_gtt(self, gtt_id: str) -> dict:
        if self.model is None:
            return {"s": "error", "message": "Fyers model not initialized"}
        try:
            if hasattr(self.model, "cancel_gtt"):
                return self.model.cancel_gtt(data={"id": gtt_id})
            return {"s": "error", "message": "cancel_gtt not supported"}
        except Exception as e:
            return {"s": "error", "message": str(e)}

    def cancel_order(self, order_id: str) -> dict:
        if self.model is None:
            return {"s": "error", "message": "Fyers model not initialized"}
        return self.model.cancel_order(data={"id": order_id})

    def get_history(self, symbol: str, resolution: str, range_from: str, range_to: str) -> dict:
        if self.model is None:
            return {"s": "error", "message": "Fyers model not initialized"}
        payload = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "1",
            "range_from": range_from,
            "range_to": range_to,
            "cont_flag": "1",
        }
        return self.model.history(data=payload)

    def get_positions(self) -> dict:
        if self.model is None:
            return {"s": "error", "message": "Fyers model not initialized"}
        try:
            return self.model.positions()
        except Exception as e:
            return {"s": "error", "message": str(e)}

    def get_orders(self) -> dict:
        if self.model is None:
            return {"s": "error", "message": "Fyers model not initialized"}
        try:
            return self.model.orderbook(data={})
        except Exception as e:
            return {"s": "error", "message": str(e)}


# ---------------------------- CONFIG & SETTINGS LOADER ----------------------------
def load_settings_file(path: str = SETTINGS_FILE) -> dict:
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception as e:
        _real_print(f"[warn] Could not load settings file {path}: {e}")
        return {}


def load_config():
    global PRIMARY_STATIC_IP, PROXY_URL, FORCE_IPV4
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                if "fyers" in data and isinstance(data["fyers"], dict):
                    data = data["fyers"]
                keys_map = {str(k).lower().strip().replace(" ", "_"): v for k, v in data.items() if isinstance(v, str)}
                PRIMARY_STATIC_IP = (
                        keys_map.get("primary_ip") or
                        keys_map.get("primary_static_ip") or
                        keys_map.get("static_ip") or
                        keys_map.get("ip") or
                        os.getenv("FYERS_PRIMARY_IP")
                )
                PROXY_URL = (
                        keys_map.get("proxy_url") or
                        keys_map.get("proxy") or
                        keys_map.get("static_ip_proxy_url") or
                        os.getenv("FYERS_PROXY_URL")
                )
                if "force_ipv4" in data and isinstance(data["force_ipv4"], bool):
                    FORCE_IPV4 = data["force_ipv4"]
        except Exception:
            pass
    if not PRIMARY_STATIC_IP:
        PRIMARY_STATIC_IP = os.getenv("FYERS_PRIMARY_IP")
    if not PROXY_URL:
        PROXY_URL = os.getenv("FYERS_PROXY_URL")
    env_force_ipv4 = os.getenv("FYERS_FORCE_IPV4")
    if env_force_ipv4 is not None:
        FORCE_IPV4 = env_force_ipv4.strip().lower() not in ("0", "false", "no")

    if PROXY_URL:
        os.environ["HTTP_PROXY"] = PROXY_URL
        os.environ["HTTPS_PROXY"] = PROXY_URL
        _real_print(f"[broker] Routing outbound requests via static-IP proxy tunnel.")


def apply_ipv4_preference():
    if not FORCE_IPV4 or PROXY_URL:
        return
    try:
        import urllib3.util.connection as urllib3_cn

        def _allowed_gai_family():
            return socket.AF_INET

        urllib3_cn.allowed_gai_family = _allowed_gai_family
        _real_print("[network] Forcing IPv4 for all outbound HTTP calls (FORCE_IPV4=True).")
    except Exception as e:
        _real_print(f"[network] Could not force IPv4 preference: {e}")


def check_outbound_ip(timeout: int = 6) -> Optional[str]:
    for url in ("https://api.ipify.org?format=json", "https://ifconfig.me/ip"):
        try:
            r = requests.get(url, timeout=timeout)
            if r.ok:
                text = r.text.strip()
                try:
                    return r.json().get("ip") or text
                except Exception:
                    return text
        except Exception:
            continue
    return None


def load_or_prompt_creds() -> dict:
    global PRIMARY_STATIC_IP
    load_config()

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                if "fyers" in data and isinstance(data["fyers"], dict):
                    data = data["fyers"]
                client_id = data.get("client_id") or data.get("api_key") or os.getenv("FYERS_APP_ID")
                secret_key = data.get("secret_key") or data.get("api_secret") or os.getenv("FYERS_SECRET_ID")
                redirect_uri = data.get("redirect_uri") or data.get("redirect_url") or os.getenv("FYERS_REDIRECT_URL")
                if client_id and secret_key and redirect_uri:
                    return {
                        "client_id": client_id,
                        "secret_key": secret_key,
                        "redirect_uri": redirect_uri,
                        "primary_ip": PRIMARY_STATIC_IP,
                    }
        except Exception:
            pass

    _real_print("---- Enter your Broker Login Credentials ----")
    creds = {
        "client_id": input("Enter APP ID: ").strip(),
        "secret_key": input("Enter SECRET ID: ").strip(),
        "redirect_uri": input("Enter Redirect URL: ").strip(),
    }
    if input("Save to 'fyers_login_details.json'? (Y/N): ").strip().upper() == "Y":
        try:
            base = {}
            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, "r") as f:
                        base = json.load(f) or {}
                        if not isinstance(base, dict):
                            base = {}
                except Exception:
                    base = {}
            base.update(creds)
            if PRIMARY_STATIC_IP:
                base["primary_ip"] = PRIMARY_STATIC_IP
            with open(CONFIG_FILE, "w") as f:
                json.dump(base, f, indent=2)
            _real_print(f"Saved '{CONFIG_FILE}'.")
        except Exception as e:
            _real_print(f"[auth] Could not save creds: {e}")
    else:
        _real_print("Skipping save.")
    return creds


def browser_login(creds: dict) -> str:
    session = fyersModel.SessionModel(
        client_id=creds["client_id"],
        secret_key=creds["secret_key"],
        redirect_uri=creds["redirect_uri"],
        response_type="code",
        grant_type="authorization_code",
    )
    auth_url = session.generate_authcode()

    _real_print("\n================ FYERS AUTOMATED AUTHENTICATION ================")
    _real_print("1. Opening FYERS login URL in your browser...")
    _real_print(f"   {auth_url}")
    try:
        webbrowser.open(auth_url)
    except Exception as e:
        _real_print(f"[auth] Could not auto-open browser: {e}")

    auth_code = None
    parsed_redirect = urlparse(creds["redirect_uri"])
    is_local = parsed_redirect.hostname in ("localhost", "127.0.0.1", "0.0.0.0")

    if is_local:
        port = parsed_redirect.port or 8080
        _real_print(f"2. Local redirect detected ({creds['redirect_uri']}). Listening on port {port} for auth code...")
        try:
            server = HTTPServer(("0.0.0.0", port), AuthCodeHandler)
            server.timeout = 120
            server.handle_request()
            auth_code = AuthCodeHandler.auth_code
            if auth_code:
                _real_print("   [+] Auth code automatically captured from browser redirect!")
        except Exception as e:
            _real_print(f"   [-] Local server listener error: {e}")

    if not auth_code:
        _real_print("2. Authenticate with FYERS (Login + 2FA/PIN) in browser.")
        pasted = input("\n3. Paste the FULL redirect URL after login, or just the 'code=' value here: ").strip()
        if pasted.startswith("http://") or pasted.startswith("https://"):
            qs = parse_qs(urlparse(pasted).query)
            auth_code = (qs.get("code") or qs.get("auth_code") or [None])[0]
            if not auth_code:
                raise ValueError("No 'code' param found in the provided URL.")
        else:
            auth_code = pasted

    session.set_token(auth_code)
    resp = session.generate_token()
    if not isinstance(resp, dict) or "access_token" not in resp:
        raise RuntimeError(f"Login failed: {resp}")
    return resp["access_token"]


def _print_session_dashboard(app_id: str, redirect_uri: str, access_token: str):
    _real_print("\n=================== FYERS SESSION DASHBOARD ===================")
    _real_print(" STATUS       : CONNECTED")
    _real_print(f" App ID       : {app_id}")
    _real_print(f" Redirect URI : {redirect_uri}")
    if PRIMARY_STATIC_IP:
        _real_print(f" Static IP    : {PRIMARY_STATIC_IP}")
    _real_print(f" Access Token : {access_token[:15]}...")
    _real_print("===============================================================\n")


def run_interactive_login() -> str:
    creds = load_or_prompt_creds()
    app_id = creds["client_id"]
    redirect_uri = creds["redirect_uri"]

    if os.path.exists(TOKEN_PATH):
        try:
            with open(TOKEN_PATH, "r") as f:
                data = json.load(f)
            if isinstance(data, str):
                access_token = data
            elif isinstance(data, dict):
                access_token = data.get("access_token") or data.get("token")
            else:
                access_token = None
            if access_token:
                _real_print("[auth] Reusing today's cached token.")
                _print_session_dashboard(app_id, redirect_uri, access_token)
                return access_token
        except Exception:
            pass

    pasted = input("Paste today's access token, or press Enter to log in via browser: ").strip()
    access_token = pasted or browser_login(creds)

    os.makedirs(TOKENS_DIR, exist_ok=True)
    try:
        with open(TOKEN_PATH, "w") as f:
            json.dump({"access_token": access_token}, f)
    except Exception as e:
        _real_print(f"[auth] Failed to save token to {TOKEN_PATH}: {e}")

    try:
        base = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    base = json.load(f) or {}
                    if not isinstance(base, dict):
                        base = {}
            except Exception:
                base = {}
        base["access_token"] = access_token
        with open(CONFIG_FILE, "w") as f:
            json.dump(base, f, indent=2)
    except Exception as e:
        _real_print(f"[auth] Could not store access_token into {CONFIG_FILE}: {e}")

    _print_session_dashboard(app_id, redirect_uri, access_token)
    return access_token


def validate_access_token(client_id: str, access_token: str) -> bool:
    if not client_id or not access_token or fyersModel is None:
        return False
    try:
        model = fyersModel.FyersModel(client_id=client_id, is_async=False, token=access_token, log_path="")
        resp = model.get_profile()
        if isinstance(resp, dict) and resp.get("s") == "ok":
            return True
        _real_print(f"[auth] Saved token failed validation: {resp}")
        return False
    except Exception as e:
        _real_print(f"[auth] Token validation call failed: {e}")
        return False


# ---------------------------- STATE OBJECTS ----------------------------
class SymbolState:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.data = pd.DataFrame()
        self.status = "watch"
        self.signal_candle = None
        self.signal_close_ts = None
        self.signal_expiry = None
        self.signal_notified = False
        self.entry_price = 0.0
        self.qty = 0
        self.stop_price = 0.0
        self.target_price = 0.0
        self.gtt_order_id = None
        self.sl_order_id = None
        self.target_order_id = None
        self.order_mode = "regular_simultaneous"  # "gtt_oco" or "regular_simultaneous"
        self.last_candle_ts = None
        self.last_eval_candle = None
        self.cooldown_until = None
        self.entry_ts = 0.0  # Unix timestamp of entry execution

    def __repr__(self):
        return f"<State {self.symbol} {self.status} qty={self.qty} sl={self.stop_price} tgt={self.target_price}>"


SYMBOL_STATES: Dict[str, SymbolState] = {s: SymbolState(s) for s in SYMBOLS}

if not os.path.exists(LOG_FILE):
    import csv
    with open(LOG_FILE, "w", newline="") as f:
        csv.writer(f).writerow(["ts", "symbol", "action", "qty", "price", "response"])


def log_trade_event(symbol, action, qty, price, response):
    resp_text = json.dumps(response, default=str)
    import csv
    with open(LOG_FILE, "a", newline="") as f:
        csv.writer(f).writerow([dt.now().isoformat(), symbol, action, qty, price, resp_text])


# ---------------------------- INDICATORS ----------------------------
def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "volume" not in df.columns:
        return df
    df = df.copy()
    df["ema_fast_entry"] = ema(df["close"], ENTRY_FAST_EMA)

    df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
    df['tpv'] = df['typical_price'] * df['volume']
    df.index = pd.to_datetime(df.index)
    df['date'] = df.index.date
    df['cumulative_tpv'] = df.groupby('date')['tpv'].cumsum()
    df['cumulative_volume'] = df.groupby('date')['volume'].cumsum()
    df['vwap'] = df['cumulative_tpv'] / df['cumulative_volume']
    df.drop(columns=['typical_price', 'tpv', 'date', 'cumulative_tpv', 'cumulative_volume'], inplace=True)

    rng = (df["high"] - df["low"]) / df["close"].replace(0, pd.NA)
    df["ok_signal"] = rng >= MIN_RANGE_PCT if MIN_RANGE_PCT > 0 else True
    return df


# ---------------------------- CANDLE MANAGER ----------------------------
class CandleManager:
    def __init__(self, timeframe_min: int = 5, on_candle=None, tz="Asia/Kolkata",
                 persist_file=PARTIAL_CANDLES_FILE, max_history=2000):
        self.tf = int(timeframe_min)
        self.on_candle = on_candle
        self.tz = pytz.timezone(tz)
        self.persist_file = persist_file
        self.lock = threading.RLock()
        self.partial: Dict[str, dict] = {}
        self.history: Dict[str, pd.DataFrame] = {}
        self.max_history = max_history

        if os.path.exists(self.persist_file):
            try:
                with open(self.persist_file, "r") as f:
                    saved = json.load(f)
                for sym, p in (saved or {}).items():
                    p["ts"] = pd.to_datetime(p["ts"]).tz_localize(None)
                    self.partial[sym] = p
            except Exception:
                self.partial = {}

    def _floor_ts(self, ts: dt) -> dt:
        if ts.tzinfo is None:
            ts = self.tz.localize(ts)
        else:
            ts = ts.astimezone(self.tz)
        ts = ts.replace(tzinfo=None)
        minute = (ts.minute // self.tf) * self.tf
        return ts.replace(second=0, microsecond=0, minute=minute)

    def _parse_ts(self, ts_val) -> dt:
        if ts_val is None:
            return dt.now(self.tz).replace(tzinfo=None)
        if isinstance(ts_val, (int, float)):
            return dt.fromtimestamp(float(ts_val), self.tz).replace(tzinfo=None)
        if isinstance(ts_val, str):
            try:
                dtobj = pd.to_datetime(ts_val)
                if dtobj.tzinfo is None:
                    dtobj = self.tz.localize(dtobj).replace(tzinfo=None)
                else:
                    dtobj = dtobj.astimezone(self.tz).replace(tzinfo=None)
                return dtobj
            except Exception:
                return dt.now(self.tz).replace(tzinfo=None)
        if isinstance(ts_val, dt):
            if ts_val.tzinfo is None:
                return self.tz.localize(ts_val).replace(tzinfo=None)
            return ts_val.astimezone(self.tz).replace(tzinfo=None)
        return dt.now(self.tz).replace(tzinfo=None)

    def _persist_partial(self):
        try:
            to_save = {}
            for sym, p in self.partial.items():
                out = dict(p)
                out["ts"] = p["ts"].isoformat()
                to_save[sym] = out
            with open(self.persist_file, "w") as f:
                json.dump(to_save, f, indent=2)
        except Exception:
            pass

    def _append_history(self, symbol: str, candle: dict):
        df = self.history.get(symbol)
        row = {"open": candle["open"], "high": candle["high"],
               "low": candle["low"], "close": candle["close"], "volume": candle["volume"]}
        ts = pd.to_datetime(candle["ts"])
        if df is None:
            df = pd.DataFrame([row], index=[ts])
        else:
            df = pd.concat([df, pd.DataFrame([row], index=[ts])])
            if len(df) > self.max_history:
                df = df.tail(self.max_history)
        df.index.name = "datetime"
        self.history[symbol] = df
        if callable(self.on_candle):
            try:
                self.on_candle(symbol, {"ts": ts, **row})
            except Exception:
                pass

    def process_tick(self, tick: dict):
        try:
            symbol = tick.get("symbol")
            if not symbol:
                return
            ltp = tick.get("ltp") or tick.get("last_price") or tick.get("last_traded_price")
            if ltp is None:
                return
            ltp = float(ltp)
            vtt = int(tick.get("vtt", 0))
            ts = self._parse_ts(tick.get("timestamp"))
            candle_start = self._floor_ts(ts)
            with self.lock:
                p = self.partial.get(symbol)
                if p is None:
                    new_p = {"ts": candle_start, "open": ltp, "high": ltp,
                             "low": ltp, "close": ltp, "ticks": 1,
                             "start_vtt": vtt, "end_vtt": vtt}
                    self.partial[symbol] = new_p
                    self._persist_partial()
                    return
                if candle_start == p["ts"]:
                    p["high"] = max(p["high"], ltp)
                    p["low"] = min(p["low"], ltp)
                    p["close"] = ltp
                    p["ticks"] = p.get("ticks", 0) + 1
                    p["end_vtt"] = vtt
                    self._persist_partial()
                    return
                completed = dict(p)
                candle_volume = completed.get("end_vtt", 0) - completed.get("start_vtt", 0)
                candle_out = {
                    "symbol": symbol,
                    "ts": completed["ts"],
                    "open": completed["open"],
                    "high": completed["high"],
                    "low": completed["low"],
                    "close": completed["close"],
                    "volume": candle_volume,
                    "ticks": completed.get("ticks", 1),
                }
                self._append_history(symbol, candle_out)
                new_partial = {"ts": candle_start, "open": ltp, "high": ltp,
                               "low": ltp, "close": ltp, "ticks": 1,
                               "start_vtt": vtt, "end_vtt": vtt}
                self.partial[symbol] = new_partial
                self._persist_partial()
        except Exception:
            return

    def force_close_all_up_to(self, upto_ts: dt = None):
        if upto_ts is None:
            upto_ts = dt.now(self.tz).replace(tzinfo=None)
        with self.lock:
            for symbol, p in list(self.partial.items()):
                if p["ts"] < upto_ts:
                    candle_out = {
                        "symbol": symbol,
                        "ts": p["ts"],
                        "open": p["open"],
                        "high": p["high"],
                        "low": p["low"],
                        "close": p["close"],
                        "ticks": p.get("ticks", 1),
                    }
                    self._append_history(symbol, candle_out)
                    del self.partial[symbol]
            self._persist_partial()

    def get_latest_candle(self, symbol: str):
        df = self.history.get(symbol)
        if df is None or df.empty:
            return None
        last_idx = df.index[-1]
        row = df.loc[last_idx]
        return {"ts": last_idx, **row.to_dict()}


CANDLE_MANAGER: Optional[CandleManager] = None

# ---------------------------- ORDER & RECONCILIATION HELPERS ----------------------------
BROKER_CLIENT: Optional[BaseBrokerClient] = None
FYERS_SOCKET = None
ORDER_SOCKET = None
ACCESS_TOKEN = None
OPEN_POSITIONS = set()
DAILY_PNL = 0.0
RECONCILE_WAKE = threading.Event()


def decide_qty(symbol: str, entry_price: float) -> int:
    sym_upper = symbol.upper()

    if QTY_MAP.get(symbol) is not None:
        try:
            q = int(QTY_MAP.get(symbol))
            return max(0, q)
        except Exception:
            pass

    if sym_upper.startswith("MCX:"):
        if POSITION_MODE == "qty" and FIXED_QTY and FIXED_QTY > 0:
            return max(0, int(FIXED_QTY))
        return 1

    if POSITION_MODE == "qty":
        if FIXED_QTY and FIXED_QTY > 0:
            return max(0, int(FIXED_QTY))

    alloc = ALLOC_MAP.get(symbol, ALLOC_DEFAULT)
    if entry_price <= 0:
        return 0
    try:
        q = int(alloc // entry_price)
        return max(0, q)
    except Exception:
        return 0


def place_market_order(symbol: str, qty: int, side: int) -> dict:
    side_str = "BUY" if side == 1 else "SELL"
    _real_print(f"[order] Placing market {side_str} for {qty} of {symbol}")
    if side == 1 and len([s for s in SYMBOL_STATES.values() if s.status == "position"]) >= MAX_CONCURRENT_POS:
        _real_print(f"[order] MAX_CONCURRENT_POS reached ({MAX_CONCURRENT_POS}). Rejecting new buy for {symbol}.")
        return {"s": "error", "message": "max concurrent positions reached"}
    if BROKER_CLIENT is None:
        err = {"s": "error", "message": "no broker client"}
        log_trade_event(symbol, side_str, qty, None, err)
        return err
    last_resp = None
    for attempt in range(1, 4):
        try:
            resp = BROKER_CLIENT.place_order(symbol=symbol, qty=qty, side=side, product_type=PRODUCT_TYPE)
            log_trade_event(symbol, side_str, qty, None, resp)
            last_resp = resp
            if isinstance(resp, dict) and resp.get("s") == "ok":
                if side == 1:
                    OPEN_POSITIONS.add(symbol)
                else:
                    OPEN_POSITIONS.discard(symbol)
                return resp
            _real_print(f"[order] {side_str} order attempt {attempt} for {symbol} rejected by broker: {resp}")
        except Exception as e:
            last_resp = {"s": "error", "message": str(e)}
            _real_print(f"[order] {side_str} order attempt {attempt} for {symbol} raised an exception: {e}")
        time.sleep(1 * attempt)
    err = last_resp if isinstance(last_resp, dict) else {"s": "error", "message": "order failed after retries"}
    log_trade_event(symbol, side_str, qty, None, err)
    return err


def place_stoploss_order(symbol: str, qty: int, trigger_price: float) -> dict:
    """Place protective Stop Loss SL-L Sell order."""
    if BROKER_CLIENT is None:
        err = {"s": "error", "message": "no broker client for stoploss order"}
        log_trade_event(symbol, "SL_FAIL", qty, trigger_price, err)
        return err
    last_resp = None
    for attempt in range(1, 4):
        try:
            resp = BROKER_CLIENT.place_stoploss_order(symbol=symbol, qty=qty, trigger_price=trigger_price,
                                                        product_type=PRODUCT_TYPE)
            log_trade_event(symbol, "SL_PLACE", qty, trigger_price, resp)
            last_resp = resp
            if isinstance(resp, dict) and resp.get("s") == "ok":
                return resp
            _real_print(f"[order] Stoploss order attempt {attempt} for {symbol} rejected by broker: {resp}")
            time.sleep(1 * attempt)
        except Exception as e:
            last_resp = {"s": "error", "message": str(e)}
            _real_print(f"[order] Stoploss order attempt {attempt} for {symbol} raised exception: {e}")
            time.sleep(1 * attempt)
    err = last_resp if isinstance(last_resp, dict) else {"s": "error", "message": "stoploss order failed after retries"}
    log_trade_event(symbol, "SL_FAIL", qty, trigger_price, err)
    return err


def place_target_limit_order(symbol: str, qty: int, limit_price: float) -> dict:
    """Place resting Target LIMIT Sell order."""
    _real_print(f"[order] Placing target LIMIT sell for {qty} of {symbol} @ {limit_price:.2f}")
    if BROKER_CLIENT is None:
        err = {"s": "error", "message": "no broker client"}
        log_trade_event(symbol, "TARGET_SELL", qty, limit_price, err)
        return err
    last_resp = None
    for attempt in range(1, 4):
        try:
            resp = BROKER_CLIENT.place_order(
                symbol=symbol, qty=qty, side=-1, product_type=PRODUCT_TYPE,
                order_type=1, limit_price=limit_price,
            )
            log_trade_event(symbol, "TARGET_SELL", qty, limit_price, resp)
            last_resp = resp
            if isinstance(resp, dict) and resp.get("s") == "ok":
                return resp
            _real_print(f"[order] Target order attempt {attempt} for {symbol} rejected by broker: {resp}")
            time.sleep(1 * attempt)
        except Exception as e:
            last_resp = {"s": "error", "message": str(e)}
            _real_print(f"[order] Target order attempt {attempt} for {symbol} raised exception: {e}")
            time.sleep(1 * attempt)
    err = last_resp if isinstance(last_resp, dict) else {"s": "error", "message": "target order failed after retries"}
    log_trade_event(symbol, "TARGET_SELL", qty, limit_price, err)
    return err


def place_simultaneous_exit_protection(st: SymbolState) -> bool:
    """Places both Target and Stop Loss exit orders SIMULTANEOUSLY at entry time.
    Attempts GTT OCO order placement first; falls back to simultaneous regular
    orders if GTT is unsupported."""
    if BROKER_CLIENT is None:
        return False

    symbol = st.symbol
    qty = st.qty
    target = st.target_price
    stop = st.stop_price

    # Attempt 1: FYERS GTT OCO Order
    gtt_resp = BROKER_CLIENT.place_gtt_oco(symbol, qty, target_price=target, stop_trigger=stop, product_type=PRODUCT_TYPE)
    if isinstance(gtt_resp, dict) and gtt_resp.get("s") == "ok":
        st.order_mode = "gtt_oco"
        st.gtt_order_id = gtt_resp.get("id") or gtt_resp.get("orderid") or gtt_resp.get("order_id")
        _real_print(f"[gtt] Simultaneous FYERS GTT OCO Exit Order placed successfully for {symbol}: id={st.gtt_order_id}")
        return True
    else:
        _real_print(f"[gtt] GTT OCO placement notice for {symbol} (falling back to simultaneous regular orders): {gtt_resp}")

    # Attempt 2: Simultaneous Regular Orders (Target Limit Sell + Stop Loss SL-L Sell)
    st.order_mode = "regular_simultaneous"

    # Place Stop Loss SL-L
    sl_resp = place_stoploss_order(symbol, qty, trigger_price=stop)
    if isinstance(sl_resp, dict) and sl_resp.get("s") == "ok":
        st.sl_order_id = sl_resp.get("id") or sl_resp.get("orderid") or sl_resp.get("order_id")
        st.gtt_order_id = st.sl_order_id  # for backward compatibility
        _real_print(f"[order] Simultaneous Stoploss SL-L placed for {symbol}: id={st.sl_order_id} @ trigger {stop:.2f}")
    else:
        _real_print(f"[order] WARNING: Simultaneous Stoploss placement failed for {symbol}: {sl_resp}")

    # Place Target Limit Sell
    if target > 0:
        tgt_resp = place_target_limit_order(symbol, qty, target)
        if isinstance(tgt_resp, dict) and tgt_resp.get("s") == "ok":
            st.target_order_id = tgt_resp.get("id") or tgt_resp.get("orderid") or tgt_resp.get("order_id")
            _real_print(f"[order] Simultaneous Target Limit Sell placed for {symbol}: id={st.target_order_id} @ {target:.2f}")
        else:
            _real_print(f"[order] WARNING: Simultaneous Target placement failed for {symbol}: {tgt_resp}")

    return (st.sl_order_id is not None or st.target_order_id is not None)


def cancel_regular_order(order_id: str) -> dict:
    if BROKER_CLIENT is None or not order_id:
        return {"s": "error", "message": "no broker client or invalid order_id"}
    try:
        return BROKER_CLIENT.cancel_order(order_id=order_id)
    except Exception as e:
        return {"s": "error", "message": str(e)}


def cancel_gtt_order(gtt_id: str) -> dict:
    if BROKER_CLIENT is None or not gtt_id:
        return {"s": "error", "message": "no broker client or invalid gtt_id"}
    try:
        return BROKER_CLIENT.cancel_gtt(gtt_id=gtt_id)
    except Exception as e:
        return {"s": "error", "message": str(e)}


def _extract_net_qty_map(positions_resp: dict) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if not isinstance(positions_resp, dict):
        return out
    for key in ("netPositions", "net_positions", "positions"):
        items = positions_resp.get(key)
        if isinstance(items, list):
            for item in items:
                try:
                    if not isinstance(item, dict):
                        continue
                    sym = item.get("symbol") or item.get("Symbol")
                    qty = item.get("netQty")
                    if qty is None:
                        qty = item.get("qty")
                    if sym is not None and qty is not None:
                        out[sym] = out.get(sym, 0.0) + float(qty)
                except Exception:
                    continue
            break
    return out


def sync_broker_positions():
    """Reconciles internal tracked position states against actual broker net positions.
    When a position's net quantity drops to 0 (Target hit, SL triggered, or manual exit
    in FYERS app), it cancels the remaining open protection orders for THAT symbol only."""
    if BROKER_CLIENT is None:
        return
    tracked = [s for s, st in SYMBOL_STATES.items() if st.status == "position" and st.qty > 0]
    if not tracked:
        return

    try:
        resp = BROKER_CLIENT.get_positions()
    except Exception as e:
        _real_print(f"[sync] get_positions() call failed: {e}")
        return
    if not isinstance(resp, dict) or resp.get("s") != "ok":
        return

    net_qty_map = _extract_net_qty_map(resp)

    for symbol in tracked:
        st = SYMBOL_STATES[symbol]
        actual_qty = net_qty_map.get(symbol, 0.0)

        # 1.5s post-entry grace period to avoid broker position table propagation delay
        if (time.time() - st.entry_ts) < 1.5:
            continue

        if actual_qty >= st.qty:
            continue  # Still fully open

        if actual_qty <= 0:
            _real_print(
                f"[sync] {symbol}: Broker shows 0 net positions (Target filled, SL triggered, or Manual Exit). "
                f"Cancelling remaining protection orders for {symbol}..."
            )
            if st.order_mode == "gtt_oco" and st.gtt_order_id:
                cancel_gtt_order(st.gtt_order_id)
                _real_print(f"[sync] Cancelled GTT OCO order {st.gtt_order_id} for {symbol}")
            else:
                if st.sl_order_id:
                    cancel_regular_order(st.sl_order_id)
                    _real_print(f"[sync] Cancelled Stoploss order {st.sl_order_id} for {symbol}")
                elif st.gtt_order_id:
                    cancel_regular_order(st.gtt_order_id)
                if st.target_order_id:
                    cancel_regular_order(st.target_order_id)
                    _real_print(f"[sync] Cancelled Target order {st.target_order_id} for {symbol}")

            st.status = "watch"
            st.qty = 0
            st.entry_price = 0.0
            st.stop_price = 0.0
            st.target_price = 0.0
            st.gtt_order_id = None
            st.sl_order_id = None
            st.target_order_id = None


def _reconcile_loop():
    while True:
        try:
            RECONCILE_WAKE.wait(timeout=RECONCILE_INTERVAL_SECONDS)
            RECONCILE_WAKE.clear()
            sync_broker_positions()
        except Exception as e:
            _real_print(f"[sync] loop error: {e}")


OPEN_ORDER_STATUS_CODES = {4, 6}


def _extract_order_status_map(orders_resp: dict) -> Dict[str, int]:
    out: Dict[str, int] = {}
    if not isinstance(orders_resp, dict):
        return out
    for key in ("orderBook", "orderbook", "orders"):
        items = orders_resp.get(key)
        if isinstance(items, list):
            for item in items:
                try:
                    if not isinstance(item, dict):
                        continue
                    oid = item.get("id") or item.get("orderNumber") or item.get("order_id")
                    status = item.get("status")
                    if oid is not None and status is not None:
                        out[str(oid)] = int(status)
                except Exception:
                    continue
            break
    return out


def verify_and_rearm_legs():
    """Re-arms expired DAY orders across multi-day CNC positions."""
    if BROKER_CLIENT is None:
        return
    tracked = [s for s, st in SYMBOL_STATES.items() if st.status == "position" and st.qty > 0]
    if not tracked:
        return

    try:
        pos_resp = BROKER_CLIENT.get_positions()
    except Exception as e:
        return
    if not isinstance(pos_resp, dict) or pos_resp.get("s") != "ok":
        return
    net_qty_map = _extract_net_qty_map(pos_resp)

    try:
        orders_resp = BROKER_CLIENT.get_orders()
    except Exception as e:
        return
    if not isinstance(orders_resp, dict) or orders_resp.get("s") != "ok":
        return
    order_status_map = _extract_order_status_map(orders_resp)

    for symbol in tracked:
        st = SYMBOL_STATES[symbol]
        actual_qty = net_qty_map.get(symbol, 0.0)

        if actual_qty != st.qty:
            continue

        if st.stop_price > 0:
            sl_id = st.sl_order_id or st.gtt_order_id
            sl_open = sl_id is not None and order_status_map.get(str(sl_id)) in OPEN_ORDER_STATUS_CODES
            if not sl_open:
                _real_print(f"[rearm] {symbol}: Re-arming expired Stoploss @ {st.stop_price:.2f}")
                sl_resp = place_stoploss_order(symbol, st.qty, trigger_price=st.stop_price)
                if isinstance(sl_resp, dict) and sl_resp.get("s") == "ok":
                    st.sl_order_id = sl_resp.get("id") or sl_resp.get("orderid") or sl_resp.get("order_id")
                    st.gtt_order_id = st.sl_order_id

        if st.target_price > 0:
            tgt_open = st.target_order_id is not None and order_status_map.get(str(st.target_order_id)) in OPEN_ORDER_STATUS_CODES
            if not tgt_open:
                _real_print(f"[rearm] {symbol}: Re-arming expired Target @ {st.target_price:.2f}")
                tgt_resp = place_target_limit_order(symbol, st.qty, st.target_price)
                if isinstance(tgt_resp, dict) and tgt_resp.get("s") == "ok":
                    st.target_order_id = tgt_resp.get("id") or tgt_resp.get("orderid") or tgt_resp.get("order_id")


def _rearm_loop():
    while True:
        try:
            time.sleep(REARM_INTERVAL_SECONDS)
            verify_and_rearm_legs()
        except Exception as e:
            _real_print(f"[rearm] loop error: {e}")


# ---------------------------- TOKEN UTILITIES ----------------------------
def load_token() -> str:
    try:
        if os.path.exists(TOKEN_PATH):
            with open(TOKEN_PATH, "r") as f:
                data = json.load(f)
            if isinstance(data, str):
                return data.strip()
            if isinstance(data, dict):
                for k in ("access_token", "accessToken", "token"):
                    if data.get(k):
                        return str(data[k]).strip()
    except Exception:
        pass

    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in ("access_token", "accessToken", "token"):
                    if data.get(k):
                        return str(data[k]).strip()
    except Exception:
        pass

    try:
        if os.path.isdir(TOKENS_DIR):
            files = sorted(glob.glob(os.path.join(TOKENS_DIR, "*")))
            for fn in files:
                try:
                    with open(fn, "r") as f:
                        raw = f.read().strip()
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                        if isinstance(data, str):
                            return data.strip()
                        if isinstance(data, dict):
                            for k in ("access_token", "accessToken", "token"):
                                if data.get(k):
                                    return str(data[k]).strip()
                    except Exception:
                        return raw
                except Exception:
                    continue
    except Exception:
        pass

    raise Exception("No access token found. Please complete login.")


def get_access_token(client_id: Optional[str] = None) -> dict:
    tok = None
    try:
        tok = load_token()
    except Exception:
        tok = None

    if tok:
        if client_id and not validate_access_token(client_id, tok):
            _real_print("[auth] Saved token is invalid or expired. Forcing fresh login...")
            _remove_local_tokens(TOKENS_DIR)
            _clear_config_access_token()
            tok = None
        else:
            return {"access_token": tok}

    _real_print("[auth] No valid access token found. Starting interactive login...")
    access_token = run_interactive_login()
    if not access_token:
        raise Exception("Could not obtain access token.")
    return {"access_token": access_token}


def warmup_all(broker_client):
    if broker_client is None:
        return
    _real_print("[warmup] Fetching history for symbols...")
    for sym in SYMBOLS:
        try:
            _ = broker_client.get_history(
                symbol=sym,
                resolution=f"{TIMEFRAME_MIN}",
                range_from=(dt.now() - timedelta(days=2)).strftime("%Y-%m-%d"),
                range_to=dt.now().strftime("%Y-%m-%d"),
            )
            time.sleep(0.05)
        except Exception:
            continue


# ---------------------------- STRATEGY HELPERS ----------------------------
def compute_swing_low_for_signal(state: SymbolState, lookback: int) -> float:
    try:
        if state.signal_candle is not None:
            end_ts = state.signal_candle["ts"]
            df = state.data.loc[:end_ts]
        else:
            df = state.data
        if df.empty:
            return float("nan")
        tail = df.tail(lookback)
        return float(tail["low"].min())
    except Exception:
        return float("nan")


def _check_cooldown_expiry(st: SymbolState, now_ts: Optional[dt] = None):
    if st.status != "cooldown":
        return
    if now_ts is None:
        now_ts = dt.now(IST).replace(tzinfo=None)
    if st.cooldown_until is None or now_ts >= st.cooldown_until:
        _real_print(f"[cooldown] {st.symbol}: cooldown expired, returning to 'watch'.")
        st.status = "watch"
        st.cooldown_until = None


def on_completed_candle(symbol: str, candle: dict):
    st = SYMBOL_STATES.get(symbol)
    if st is None:
        return
    try:
        row = {"open": candle["open"], "high": candle["high"],
               "low": candle["low"], "close": candle["close"], "volume": candle["volume"]}
        idx = pd.to_datetime(candle["ts"])
        df = st.data
        if df is None or df.empty:
            df = pd.DataFrame([row], index=[idx])
        else:
            df = pd.concat([df, pd.DataFrame([row], index=[idx])])
            df = df.tail(2000)
        df.index.name = "datetime"
        st.data = compute_indicators(df)
        st.last_candle_ts = idx
    except Exception:
        return
    _check_cooldown_expiry(st)
    evaluate_on_new_candle(st)


# ---------------------------- TICK HANDLER ----------------------------
def on_tick(tick: dict):
    symbol = tick.get("symbol")
    ltp = float(tick.get("ltp", 0.0))
    vtt = int(tick.get("vtt", 0))
    ts = tick.get("timestamp") or tick.get("time")

    if ts is None:
        ts = dt.now(IST).replace(tzinfo=None)
    if isinstance(ts, str):
        try:
            ts = pd.to_datetime(ts)
            if ts.tzinfo is not None:
                ts = ts.tz_convert(TIMEZONE).tz_localize(None)
            else:
                ts = IST.localize(ts).replace(tzinfo=None)
        except Exception:
            ts = dt.now(IST).replace(tzinfo=None)
    elif isinstance(ts, dt):
        if ts.tzinfo is not None:
            ts = ts.astimezone(IST).replace(tzinfo=None)
        else:
            ts = IST.localize(ts).replace(tzinfo=None)

    if CANDLE_MANAGER:
        try:
            CANDLE_MANAGER.process_tick(
                {"symbol": symbol, "ltp": ltp, "vtt": vtt, "timestamp": ts.isoformat()}
            )
        except Exception:
            pass

    state = SYMBOL_STATES.get(symbol)
    if state is None:
        return

    # TICK-BY-TICK TARGET EXIT FALLBACK (if same-day CNC target order is blocked by broker)
    if state.status == "position" and state.target_price > 0 and ltp >= state.target_price:
        _real_print(f"[target] {symbol} LTP {ltp:.2f} >= Target {state.target_price:.2f} -> Executing Market Sell Exit")
        resp = place_market_order(symbol, state.qty, side=-1)
        if isinstance(resp, dict) and resp.get("s") == "ok":
            _real_print(f"[target] Market Sell exit succeeded for {symbol}. Triggering sync_broker_positions()")
            sync_broker_positions()
            return

    # ENTRY: strict next candle
    if state.status == "entry_pending" and state.signal_candle is not None:
        # Check time cutoff
        now_time_str = dt.now(IST).strftime("%H:%M")
        if now_time_str >= LAST_ENTRY_TIME:
            _real_print(f"[blocked-entry] {symbol} past {LAST_ENTRY_TIME} IST; cancelling pending signal.")
            state.status = "watch"
            state.signal_candle = None
            return

        try:
            tick_ts = ts if isinstance(ts, dt) else pd.to_datetime(ts).to_pydatetime().replace(tzinfo=None)
            sig_start = state.signal_candle.get("ts")
            if sig_start is None:
                state.status = "watch"
                state.signal_candle = None
                return

            sig_floor = pd.to_datetime(sig_start)
            if sig_floor.tzinfo is not None:
                sig_floor = sig_floor.tz_convert(TIMEZONE).tz_localize(None)
            next_allowed_bucket = sig_floor.to_pydatetime().replace(tzinfo=None) + timedelta(minutes=TIMEFRAME_MIN)

            if CANDLE_MANAGER is not None:
                current_bucket = CANDLE_MANAGER._floor_ts(tick_ts)
            else:
                minute = (tick_ts.minute // TIMEFRAME_MIN) * TIMEFRAME_MIN
                current_bucket = tick_ts.replace(second=0, microsecond=0, minute=minute)

            if isinstance(current_bucket, pd.Timestamp):
                current_bucket = current_bucket.to_pydatetime().replace(tzinfo=None)

            if current_bucket == next_allowed_bucket:
                trigger = float(state.signal_candle["high"])
                signal_vwap = float(state.signal_candle.get("vwap", 0.0))
                signal_ema_fast = float(state.signal_candle.get("ema_fast", 0.0))

                if ltp > trigger and signal_ema_fast > signal_vwap:
                    qty = decide_qty(symbol, ltp)
                    if qty <= 0:
                        state.status = "watch"
                        state.signal_candle = None
                        return

                    _real_print(f"[entry-debug] {symbol} next-candle LTP {ltp} > signal_high {trigger} -> ENTRY")
                    resp = place_market_order(symbol, qty, side=1)
                    if isinstance(resp, dict) and resp.get("s") == "ok":
                        state.entry_price = ltp
                        state.qty = qty
                        state.entry_ts = time.time()

                        if SL_MODE == "signal_low":
                            state.stop_price = float(state.signal_candle["low"])
                        else:
                            swing = compute_swing_low_for_signal(state, SWING_LOOKBACK)
                            state.stop_price = float(state.signal_candle["low"]) if math.isnan(swing) or swing <= 0 else float(swing)

                        state.stop_price = round_to_tick(state.stop_price)
                        risk = state.entry_price - state.stop_price

                        if risk > 0:
                            state.target_price = round_to_tick(state.entry_price + risk * RISK_REWARD_RATIO)
                        else:
                            state.target_price = 0.0

                        _real_print(
                            f"[ENTRY CONFIRMED] {state.symbol}: Entered at {state.entry_price:.2f} | "
                            f"Stoploss={state.stop_price:.2f} | Target={state.target_price:.2f} (R:R = 1:{RISK_REWARD_RATIO:g})"
                        )

                        # PLACE TARGET AND STOP LOSS SIMULTANEOUSLY AT ENTRY
                        place_simultaneous_exit_protection(state)

                        state.status = "position"
                        state.signal_candle = None
                        state.signal_close_ts = None
                    else:
                        _real_print(f"[order] BUY ORDER FAILED for {symbol}: {resp}")
                        state.status = "cooldown"
                        state.signal_candle = None
                        state.cooldown_until = dt.now(IST).replace(tzinfo=None) + timedelta(minutes=TIMEFRAME_MIN * COOLDOWN_CANDLES)
            elif current_bucket > next_allowed_bucket:
                _real_print(f"[entry-debug] {symbol} next candle expired without breakout; cancelling signal.")
                state.status = "watch"
                state.signal_candle = None
        except Exception:
            return


# ---------------------------- STRATEGY EVALUATOR ----------------------------
def evaluate_on_new_candle(st: SymbolState):
    df = st.data
    if df is None or df.empty:
        return

    last_ts = st.last_candle_ts
    if last_ts is None:
        return

    now_time_str = dt.now(IST).strftime("%H:%M")
    if now_time_str >= LAST_ENTRY_TIME:
        return

    curr = df.loc[last_ts]
    curr_open = float(curr["open"])
    curr_low = float(curr["low"])
    curr_high = float(curr["high"])
    curr_close = float(curr["close"])

    ema_fast = float(curr.get("ema_fast_entry", float("nan")))
    vwap = float(curr.get("vwap", float("nan")))

    if st.status == "watch" and len(df) > 1:
        prev = df.iloc[-2]
        prev_close = float(prev["close"])
        prev_vwap = float(prev.get("vwap", float("nan")))

        closed_above_vwap = curr_close > vwap
        fast_ema_above_vwap = ema_fast > vwap
        fresh_cross = prev_close <= prev_vwap
        green_ok = (not REQUIRE_GREEN_SIGNAL) or (curr_close > curr_open)
        ok_signal = bool(curr.get("ok_signal", True))

        if closed_above_vwap and fast_ema_above_vwap and fresh_cross and green_ok and ok_signal:
            st.signal_candle = {
                "ts": curr.name,
                "open": curr_open,
                "high": curr_high,
                "low": curr_low,
                "close": curr_close,
                "vwap": vwap,
                "ema_fast": ema_fast,
            }
            st.status = "entry_pending"
            st.signal_notified = False
            st.qty = decide_qty(st.symbol, curr_high)
            _real_print(f"****** [{st.symbol}] ENTRY SIGNAL (Closed above VWAP & EMA > VWAP) ******")
            _real_print(f"[signal:{st.symbol}] signal_high={curr_high:.2f} signal_low={curr_low:.2f} | waiting for NEXT CANDLE breakout")


# ---------------------------- WEBSOCKET HANDLERS ----------------------------
def on_ws_message(raw):
    try:
        msgs = raw if isinstance(raw, list) else [raw]
        for m in msgs:
            symbol = m.get("symbol") or m.get("scrip") or m.get("instrument")
            ltp = m.get("ltp") or m.get("last_price")
            if symbol and ltp is not None:
                on_tick(m)
    except Exception:
        pass


def on_ws_open():
    subscribe_list = [s for s in SYMBOLS if s not in INVALID_SYMBOLS]
    _real_print(f"[ws:open] Subscribing to {len(subscribe_list)} symbols...")
    try:
        FYERS_SOCKET.subscribe(symbols=subscribe_list, data_type="SymbolUpdate")
    except Exception as e:
        _real_print("[ws:open] subscribe failed:", e)


def _clear_config_access_token():
    try:
        if not os.path.exists(CONFIG_FILE):
            return
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f) or {}
        for k in ("access_token", "accessToken", "token"):
            data.pop(k, None)
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def on_ws_error(err):
    global REAUTH_ATTEMPTS
    _real_print("[ws:error]", err)


def on_ws_close(msg):
    _real_print("[ws:close]", msg)


def on_order_update(message):
    """Order WebSocket push callback for INSTANT (sub-100ms) position syncing."""
    try:
        _real_print(f"[order-ws] Update received -- triggering immediate position reconciliation check")
    except Exception:
        pass
    RECONCILE_WAKE.set()


def on_order_ws_error(err):
    _real_print("[order-ws:error]", err)


def on_order_ws_close(msg):
    _real_print("[order-ws:close]", msg)


def on_order_ws_open():
    _real_print("[order-ws:open] Connected, subscribing to order/trade/position updates...")
    try:
        ORDER_SOCKET.subscribe(data_type="OnOrders,OnTrades,OnPositions")
    except Exception as e:
        _real_print("[order-ws:open] subscribe failed:", e)


def fetch_history(broker_client: BaseBrokerClient, symbol: str, days: int = 2) -> pd.DataFrame:
    if broker_client is None:
        return pd.DataFrame()
    end = dt.now(IST).date()
    start = end - timedelta(days=days)
    try:
        r = broker_client.get_history(
            symbol=symbol,
            resolution=str(TIMEFRAME_MIN),
            range_from=start.strftime("%Y-%m-%d"),
            range_to=end.strftime("%Y-%m-%d"),
        )
        if not isinstance(r, dict) or r.get("s") != "ok":
            return pd.DataFrame()
        df = pd.DataFrame(r["candles"], columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(TIMEZONE).dt.tz_localize(None)
        return df.set_index("ts")[["open", "high", "low", "close", "volume"]]
    except Exception:
        return pd.DataFrame()


def warmup_all_full(broker_client: BaseBrokerClient):
    if broker_client is None:
        return
    for sym in SYMBOLS:
        try:
            df = fetch_history(broker_client, sym, days=3)
            if df is None or df.empty:
                continue
            df = compute_indicators(df)
            SYMBOL_STATES[sym].data = df
            SYMBOL_STATES[sym].last_candle_ts = df.index[-1]
        except Exception:
            continue


def _remove_local_tokens(dir_path=TOKENS_DIR):
    try:
        if not os.path.exists(dir_path):
            return
        for fn in glob.glob(os.path.join(dir_path, "*.json")):
            try:
                os.remove(fn)
            except Exception:
                pass
    except Exception:
        pass


def _serialize_state():
    out = {}
    for sym, st in SYMBOL_STATES.items():
        out[sym] = {
            "status": getattr(st, "status", None),
            "qty": getattr(st, "qty", 0),
            "entry_price": getattr(st, "entry_price", None),
            "stop_price": getattr(st, "stop_price", None),
            "target_price": getattr(st, "target_price", None),
            "gtt_order_id": getattr(st, "gtt_order_id", None),
            "sl_order_id": getattr(st, "sl_order_id", None),
            "target_order_id": getattr(st, "target_order_id", None),
            "order_mode": getattr(st, "order_mode", "regular_simultaneous"),
        }
    return out


def save_state_to_disk():
    try:
        with open(STATE_DUMP, "w") as f:
            json.dump(_serialize_state(), f, indent=2)
    except Exception as e:
        _real_print("[state] Failed to save state:", e)


def load_state_from_disk():
    if not os.path.exists(STATE_DUMP):
        return
    try:
        with open(STATE_DUMP, "r") as f:
            raw = json.load(f)
        for sym, info in raw.items():
            if sym not in SYMBOL_STATES:
                SYMBOL_STATES[sym] = SymbolState(sym)
            st = SYMBOL_STATES[sym]
            st.status = info.get("status", st.status)
            st.qty = info.get("qty", st.qty)
            st.entry_price = info.get("entry_price", st.entry_price)
            st.stop_price = info.get("stop_price", st.stop_price)
            st.target_price = info.get("target_price", st.target_price)
            st.gtt_order_id = info.get("gtt_order_id", st.gtt_order_id)
            st.sl_order_id = info.get("sl_order_id", st.sl_order_id)
            st.target_order_id = info.get("target_order_id", st.target_order_id)
            st.order_mode = info.get("order_mode", st.order_mode)

            if st.status == "entry_pending":
                st.status = "watch"
                st.signal_candle = None
    except Exception as e:
        _real_print("[state] Failed to load state:", e)


atexit.register(lambda: save_state_to_disk())


# ---------------------------- CLI & MAIN ----------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Fast-Slow EMA Strategy - Strict next-candle entry (Multi-Broker)")
    p.add_argument("--timeframe", "-t", type=int, default=TIMEFRAME_MIN)
    p.add_argument("--entry-fast-ema", type=int, default=ENTRY_FAST_EMA)
    p.add_argument("--min-range-pct", type=float, default=MIN_RANGE_PCT)
    p.add_argument("--risk-reward", type=float, default=RISK_REWARD_RATIO)
    p.add_argument("--test-table", action="store_true")
    p.add_argument("--position-mode", choices=["alloc", "qty"], default=POSITION_MODE)
    p.add_argument("--fixed-qty", type=int, default=None)
    p.add_argument("--product-type", type=str, default=PRODUCT_TYPE)
    p.add_argument("--sl-mode", type=str, default=SL_MODE)
    return p.parse_args()


def main():
    global TIMEFRAME_MIN, ENTRY_FAST_EMA, RISK_REWARD_RATIO, MIN_RANGE_PCT
    global CANDLE_MANAGER, BROKER_CLIENT, FYERS_SOCKET, ACCESS_TOKEN
    global POSITION_MODE, FIXED_QTY, PRODUCT_TYPE, SL_MODE

    load_config()
    apply_ipv4_preference()
    args = parse_args()

    TIMEFRAME_MIN = int(args.timeframe)
    ENTRY_FAST_EMA = int(args.entry_fast_ema)
    RISK_REWARD_RATIO = float(args.risk_reward)
    MIN_RANGE_PCT = float(args.min_range_pct)
    POSITION_MODE = str(args.position_mode).lower()
    FIXED_QTY = int(args.fixed_qty) if args.fixed_qty else FIXED_QTY
    PRODUCT_TYPE = str(args.product_type)
    SL_MODE = str(args.sl_mode).lower()

    _real_print(f"[update] Timeframe = {TIMEFRAME_MIN}m | Entry EMA = {ENTRY_FAST_EMA} | R:R = 1:{RISK_REWARD_RATIO:g}")
    _real_print(f"[mode] Product = {PRODUCT_TYPE} | SL_MODE = {SL_MODE} | POS_MODE = {POSITION_MODE} | QTY = {FIXED_QTY}")

    CANDLE_MANAGER = CandleManager(TIMEFRAME_MIN, on_candle=on_completed_candle, tz=TIMEZONE)

    if args.test_table or fyersModel is None:
        BROKER_CLIENT = None
        _real_print("[mode] TEST TABLE mode (synthetic simulation).")
        for sym in SYMBOLS:
            st = SYMBOL_STATES[sym]
            idx = pd.date_range(end=dt.now(), periods=200, freq=f"{TIMEFRAME_MIN}min")
            df = pd.DataFrame(
                {
                    "open": np.linspace(100, 110, len(idx)),
                    "high": np.linspace(101, 111, len(idx)),
                    "low": np.linspace(99, 109, len(idx)),
                    "close": np.linspace(100, 110, len(idx)),
                    "volume": np.full(len(idx), 1000),
                },
                index=idx,
            )
            df.index.name = "datetime"
            st.data = compute_indicators(df)
            st.last_candle_ts = st.data.index[-1]
    else:
        creds = load_or_prompt_creds()
        client_id_hint = creds.get("client_id")

        auth = get_access_token(client_id=client_id_hint)
        ACCESS_TOKEN = auth["access_token"]
        client_id = client_id_hint or (ACCESS_TOKEN.split(":")[0] if ":" in ACCESS_TOKEN else ACCESS_TOKEN)

        BROKER_CLIENT = FyersBrokerAdapter(client_id=client_id, access_token=ACCESS_TOKEN, primary_ip=PRIMARY_STATIC_IP)
        _real_print(f"[mode] LIVE BROKER MODE — Connected with App ID: {client_id}")

        warmup_all(BROKER_CLIENT)
        warmup_all_full(BROKER_CLIENT)

        threading.Thread(target=_reconcile_loop, daemon=True).start()
        _real_print(f"[sync] Position reconciliation engine started.")

        ws_access_token = f"{client_id}:{ACCESS_TOKEN}" if ":" not in ACCESS_TOKEN else ACCESS_TOKEN
        FYERS_SOCKET = data_ws.FyersDataSocket(
            access_token=ws_access_token,
            log_path="",
            litemode=True,
            write_to_file=False,
            reconnect=True,
            on_connect=on_ws_open,
            on_close=on_ws_close,
            on_error=on_ws_error,
            on_message=on_ws_message,
        )
        FYERS_SOCKET.connect()

        if order_ws is not None:
            try:
                global ORDER_SOCKET
                ORDER_SOCKET = order_ws.FyersOrderSocket(
                    access_token=ws_access_token,
                    write_to_file=False,
                    log_path="",
                    on_connect=on_order_ws_open,
                    on_close=on_order_ws_close,
                    on_error=on_order_ws_error,
                    on_orders=on_order_update,
                )
                ORDER_SOCKET.connect()
            except Exception as e:
                _real_print(f"[order-ws] connect notice: {e}")

    load_state_from_disk()

    if not args.test_table and BROKER_CLIENT is not None:
        sync_broker_positions()
        threading.Thread(target=_rearm_loop, daemon=True).start()

    if args.test_table:
        CANDLE_MANAGER.force_close_all_up_to()
        _real_print("[test] Synthetic candles processed. Exiting test mode.")
        return

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _real_print("\n[exit] Interrupted by user. Shutting down.")


if __name__ == "__main__":
    main()
