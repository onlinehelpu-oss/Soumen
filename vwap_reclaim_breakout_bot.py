"""
VWAP-Reclaim Breakout Algo Bot -- single-file, any-broker-compatible
================================================================
Run directly in PyCharm: fill in CONFIG below, then run this file.

pip install fyers-apiv3 pandas requests pytz numpy

--------------------------------------------------------------------------
STRATEGY
  Signal candle (selected timeframe): green candle whose low dips below
  VWAP but closes back above VWAP (a VWAP reclaim).
  Confirmation: only the immediate next candle gets a chance to break the
  signal candle's high -- checked live via LTP polling, not on candle
  close. No break within that window -> signal discarded.
  Stop-loss = signal candle's low. Target = entry + RR x (entry - SL).
  Only one open position at a time. Once opened, a position is monitored
  continuously -- including across restarts and into later sessions --
  until SL or target is hit.

BROKER ARCHITECTURE (any-broker compatible)
  The strategy, sizing, and position-monitoring code never talk to Fyers
  directly -- they only call five methods: login(), get_historical_1m(),
  get_ltp(), place_order(), get_positions(). FyersBroker below is one
  implementation of that interface. To support a second broker, write a
  new class with the same five methods (see ZerodhaBrokerStub near the
  bottom) and point BROKER_CLASS at it -- nothing else in this file changes.

LOGIN FLOW (same shape for any broker you plug in)
  First run only: you're asked once, in the console, for that broker's app
  credentials (for Fyers: App ID, Secret Key, Redirect URI). These are
  saved locally to broker_credentials.json so you're never asked again.
  Every day after that: most brokers reset the access token daily. Each
  day you either (a) paste in an access token you generated yourself from
  the broker's own dashboard, or (b) press Enter and let this script open
  your browser, where you log in directly on the broker's own page (ID,
  password, PIN/OTP -- entered there, never typed into or stored by this
  script) and the redirect is captured automatically.

SECURITY / COMPLIANCE NOTES (please actually read)
  - This script never asks for or stores your PIN or OTP. Those are only
    ever entered on the broker's own login page in your browser.
  - Never commit broker_credentials.json or the AccessToken/ folder to any
    public repo -- anyone with them can trade on your account.
  - A static IP (VPS-hosted, or via a proxy service -- see PROXY config
    below) is a separate SEBI-compliance requirement: you still need to
    whitelist that IP yourself in your broker's API app settings.
  - Not financial advice, no performance guarantee. Test with minimum
    size / paper logic before scaling. Verify LOT_SIZE and PRODUCT_TYPE
    (CNC is generally not valid for index options) before running live.
--------------------------------------------------------------------------
"""

import json
import logging
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import pytz
import requests
from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws

# =========================================================================
# CONFIG -- edit these values directly
# =========================================================================
CONFIG = {
    "broker": {
        # Redirect URI must exactly match what's registered on your app's
        # page at myapi.fyers.in (or the equivalent for another broker).
        # Only used the first time; asked once and then saved.
        "proxy": {
            "enabled": False,
            "http": "",   # e.g. "http://user:pass@your-static-ip-host:port"
            "https": "",
        },
    },

    "symbol": {
        # Tradable symbol candles are analysed on AND orders placed for.
        "trade_symbol": "NSE:NIFTY25JAN23500CE",
        "lot_size": 65,   # verify current exchange lot size before running
        "spot_symbol": "NSE:NIFTY50-INDEX",
        "auto_select_option": True,  # Auto-resolve current expiry & ATM strike on real-time basis
    },

    "strategy": {
        "timeframe": "M15",   # M1, M3, M5, M15, M30, H1, H4, D1
        "risk_reward": 2.0,
        "max_open_positions": 1,
    },

    "sizing": {
        "mode": "quantity",   # "quantity" or "amount"
        "quantity": 65,        # used when mode == "quantity"
        "amount": 50000,        # used when mode == "amount"
    },

    "order": {
        "product_type": "INTRADAY",   # INTRADAY, MARGIN, or CNC
    },

    "polling": {
        "position_monitor_seconds": 5,
    },

    "market_hours_ist": {"start": "09:15", "end": "15:30"},

    "state_file": "position_state.json",
    "log_file": "algo_bot.log",
}

# Optionally set proxy for static-IP routing.
if CONFIG["broker"]["proxy"]["enabled"]:
    if CONFIG["broker"]["proxy"]["http"]:
        os.environ["HTTP_PROXY"] = CONFIG["broker"]["proxy"]["http"]
    if CONFIG["broker"]["proxy"]["https"]:
        os.environ["HTTPS_PROXY"] = CONFIG["broker"]["proxy"]["https"]

IST = pytz.timezone("Asia/Kolkata")

CREDENTIALS_FILE = "broker_credentials.json"
TOKENS_DIR = "AccessToken"


def load_or_create_credentials(broker_name: str, required_fields: list) -> dict:
    all_creds = {}
    if os.path.exists(CREDENTIALS_FILE):
        try:
            with open(CREDENTIALS_FILE, "r") as f:
                all_creds = json.load(f)
        except Exception as e:
            logging.warning(f"Could not load credentials file {CREDENTIALS_FILE}: {e}")

    if broker_name in all_creds:
        return all_creds[broker_name]

    print(f"\n=== First-time {broker_name} app setup (asked only once) ===")
    print(f"Saved locally to {CREDENTIALS_FILE}. You won't be asked again.\n")
    creds = {key: input(prompt).strip() for key, prompt in required_fields}

    all_creds[broker_name] = creds
    with open(CREDENTIALS_FILE, "w") as f:
        json.dump(all_creds, f, indent=2)
    print("Saved. You won't be asked for these again.\n")
    return creds


def _today_token_path(broker_name: str) -> str:
    os.makedirs(TOKENS_DIR, exist_ok=True)
    today = datetime.now(IST).strftime("%Y-%m-%d")
    return os.path.join(TOKENS_DIR, f"{broker_name}_{today}.json")


def load_cached_token(broker_name: str) -> Optional[str]:
    path = _today_token_path(broker_name)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f).get("access_token")
        except Exception as e:
            logging.warning(f"Could not load cached token from {path}: {e}")
    return None


def save_cached_token(broker_name: str, access_token: str):
    with open(_today_token_path(broker_name), "w") as f:
        json.dump({"access_token": access_token}, f)


# =========================================================================
# FYERS BROKER ADAPTER
# =========================================================================
BASE_URL_2 = "https://api-t1.fyers.in/api/v3"

SIDE_MAP = {"BUY": 1, "SELL": -1}
ORDER_TYPE_MAP = {"MARKET": 2, "LIMIT": 1, "SL": 4, "SL-M": 3}


class FyersBroker:

    NAME = "fyers"
    REQUIRED_FIELDS = [
        ("client_id", "App ID, e.g. ABC12345-100: "),
        ("secret_key", "Secret Key: "),
        ("redirect_uri", "Redirect URI (must match myapi.fyers.in app "
                          "settings exactly): "),
    ]

    def __init__(self, config: dict):
        self.bcfg = config["broker"]
        self.access_token = None
        self.fyers = None
        self.creds = None
        self.ws_socket = None
        self.live_ltp: Dict[str, float] = {}
        self.subscribed_symbols: List[str] = []

    def login(self) -> str:
        self.creds = load_or_create_credentials(self.NAME, self.REQUIRED_FIELDS)

        cached = load_cached_token(self.NAME)
        if cached:
            logging.info("Reusing today's cached access token -- "
                          "no browser, no PIN, no OTP needed.")
            self.access_token = cached
        else:
            logging.info("No cached token for today -- Fyers resets tokens "
                          "daily, so one login is needed.")
            pasted = input(
                "\nIf you already generated today's access token yourself "
                "on myapi.fyers.in, paste it now and press Enter.\n"
                "Otherwise just press Enter to log in via browser instead: "
            ).strip()
            self.access_token = pasted if pasted else self._official_login()
            save_cached_token(self.NAME, self.access_token)

        self.fyers = fyersModel.FyersModel(
            client_id=self.creds["client_id"],
            token=self.access_token,
            is_async=False,
            log_path="",
        )
        return self.access_token

    def start_websocket(self, symbols: List[str]):
        if not self.creds or not self.access_token:
            return

        valid_symbols = [s for s in symbols if s and isinstance(s, str)]
        if not valid_symbols:
            return

        self.subscribed_symbols = list(set(valid_symbols))
        token_str = f"{self.creds['client_id']}:{self.access_token}"

        def on_message(msg):
            if isinstance(msg, dict) and "symbol" in msg and "ltp" in msg:
                try:
                    self.live_ltp[msg["symbol"]] = float(msg["ltp"])
                except Exception:
                    pass

        def on_open():
            if self.ws_socket and self.subscribed_symbols:
                self.ws_socket.subscribe(symbols=self.subscribed_symbols, data_type="SymbolUpdate")

        def on_error(msg):
            logging.warning(f"WebSocket error: {msg}")

        def on_close(msg):
            logging.info(f"WebSocket connection closed: {msg}")

        try:
            self.ws_socket = data_ws.FyersDataSocket(
                access_token=token_str,
                log_path="",
                litemode=True,
                write_to_file=False,
                reconnect=True,
                on_connect=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            ws_thread = threading.Thread(target=self.ws_socket.connect, daemon=True)
            ws_thread.start()
            logging.info(f"Real-time WebSocket client started for symbols: {self.subscribed_symbols}")
        except Exception as e:
            logging.warning(f"Failed to start WebSocket: {e}")

    def subscribe_symbol(self, symbol: str):
        if symbol and symbol not in self.subscribed_symbols:
            self.subscribed_symbols.append(symbol)
            if self.ws_socket:
                try:
                    self.ws_socket.subscribe(symbols=[symbol], data_type="SymbolUpdate")
                except Exception as e:
                    logging.warning(f"Could not subscribe symbol {symbol} to WebSocket: {e}")

    def _official_login(self) -> str:
        import webbrowser
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from urllib.parse import urlparse, parse_qs

        client_id = self.creds["client_id"]
        secret_key = self.creds["secret_key"]
        redirect_uri = self.creds["redirect_uri"]

        session = fyersModel.SessionModel(
            client_id=client_id,
            secret_key=secret_key,
            redirect_uri=redirect_uri,
            response_type="code",
            grant_type="authorization_code",
        )
        auth_url = session.generate_authcode()

        captured = {}
        parsed_redirect = urlparse(redirect_uri)
        is_local = parsed_redirect.hostname in ("localhost", "127.0.0.1", "0.0.0.0")

        logging.info("Opening browser for Fyers login...")
        webbrowser.open(auth_url)

        if is_local:
            port = parsed_redirect.port or 80

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    qs = parse_qs(urlparse(self.path).query)
                    if "auth_code" in qs:
                        captured["auth_code"] = qs["auth_code"][0]
                    elif "code" in qs:
                        captured["auth_code"] = qs["code"][0]
                    self.send_response(200)
                    self.send_header("Content-type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<html><body>Login captured. You can "
                                      b"close this tab and return to "
                                      b"PyCharm.</body></html>")

                def log_message(self, format, *args):
                    pass

            try:
                server = HTTPServer(("127.0.0.1", port), Handler)
                server.timeout = 120
                server.handle_request()
            except Exception as e:
                logging.warning(f"Could not start local HTTP server for OAuth capture: {e}")

        if "auth_code" not in captured:
            pasted = input(
                "Auto-capture didn't complete. Paste the full redirect URL "
                "(or just the code= value) from the browser here: "
            ).strip()
            if pasted.startswith("http"):
                qs = parse_qs(urlparse(pasted).query)
                code_list = qs.get("auth_code") or qs.get("code")
                if not code_list:
                    raise ValueError(f"No auth code found in pasted URL: {pasted}")
                captured["auth_code"] = code_list[0]
            else:
                captured["auth_code"] = pasted

        session.set_token(captured["auth_code"])
        response = session.generate_token()
        if "access_token" not in response:
            raise RuntimeError(f"Fyers official login failed: {response}")

        logging.info("Official login successful.")
        return response["access_token"]

    def get_historical_1m(self, symbol: str, days_back: int) -> pd.DataFrame:
        range_to = datetime.now(IST).date()
        range_from = range_to - timedelta(days=days_back)
        data = {
            "symbol": symbol, "resolution": "1", "date_format": "1",
            "range_from": str(range_from), "range_to": str(range_to),
            "cont_flag": "1",
        }
        resp = self.fyers.history(data=data)
        if resp.get("s") != "ok" or "candles" not in resp:
            raise RuntimeError(f"History fetch failed for {symbol}: {resp}")
        df = pd.DataFrame(resp["candles"],
                           columns=["timestamp", "open", "high", "low", "close", "volume"])
        if not df.empty:
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert(IST)
        else:
            df["timestamp"] = pd.to_datetime([], utc=True)
        return df

    def get_ltp(self, symbol: str) -> float:
        if symbol in self.live_ltp and self.live_ltp[symbol] > 0:
            return self.live_ltp[symbol]
        resp = self.fyers.quotes(data={"symbols": symbol})
        if resp.get("s") != "ok" or "d" not in resp or not resp["d"]:
            raise RuntimeError(f"Quote fetch failed for {symbol}: {resp}")
        try:
            val = float(resp["d"][0]["v"]["lp"])
            self.live_ltp[symbol] = val
            return val
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise RuntimeError(f"Failed to parse LTP for {symbol} from response {resp}: {e}")

    def place_order(self, symbol: str, qty: int, side: str, product_type: str,
                     order_type: str = "MARKET", limit_price: float = 0,
                     stop_price: float = 0) -> str:
        payload = {
            "symbol": symbol, "qty": qty, "type": ORDER_TYPE_MAP[order_type],
            "side": SIDE_MAP[side], "productType": product_type,
            "limitPrice": limit_price, "stopPrice": stop_price,
            "validity": "DAY", "disclosedQty": 0, "offlineOrder": False,
        }
        resp = self.fyers.place_order(data=payload)
        if resp.get("s") != "ok":
            raise RuntimeError(f"Order placement failed: {resp}")
        return str(resp.get("id", ""))

    def get_positions(self) -> List[Dict]:
        resp = self.fyers.positions()
        if resp.get("s") != "ok":
            raise RuntimeError(f"Positions fetch failed: {resp}")
        return resp.get("netPositions", [])


class ZerodhaBrokerStub:
    NAME = "zerodha"
    REQUIRED_FIELDS = [
        ("api_key", "Kite API Key: "),
        ("api_secret", "Kite API Secret: "),
        ("redirect_uri", "Redirect URI registered with Kite Connect: "),
    ]

    def __init__(self, config: dict):
        self.bcfg = config["broker"]

    def login(self) -> str:
        raise NotImplementedError("Implement Kite Connect login here")

    def get_historical_1m(self, symbol: str, days_back: int) -> pd.DataFrame:
        raise NotImplementedError

    def get_ltp(self, symbol: str) -> float:
        raise NotImplementedError

    def place_order(self, symbol: str, qty: int, side: str, product_type: str,
                     order_type: str = "MARKET", limit_price: float = 0,
                     stop_price: float = 0) -> str:
        raise NotImplementedError

    def get_positions(self) -> List[Dict]:
        raise NotImplementedError


BROKER_CLASS = FyersBroker


# =========================================================================
# SYMBOL PARSING / STARTUP SUMMARY
# =========================================================================
_MONTHLY_OPTION_RE = re.compile(
    r"^(?P<exch>NSE|BSE):(?P<underlying>[A-Z]+)(?P<yy>\d{2})"
    r"(?P<mon>[A-Z]{3})(?P<strike>\d+)(?P<opt>CE|PE)$"
)


def parse_option_symbol(symbol: str) -> Optional[Dict]:
    m = _MONTHLY_OPTION_RE.match(symbol)
    if not m:
        return None
    return {
        "underlying": m.group("underlying"),
        "expiry_display": f"20{m.group('yy')}-{m.group('mon')} (monthly expiry)",
        "strike": m.group("strike"),
        "option_type": "CALL" if m.group("opt") == "CE" else "PUT",
    }


def print_startup_summary(broker, cfg: dict):
    trade_symbol = cfg["symbol"]["trade_symbol"]
    spot_symbol = cfg["symbol"]["spot_symbol"]

    try:
        spot_ltp = broker.get_ltp(spot_symbol)
    except Exception as e:
        spot_ltp = None
        logging.warning(f"Could not fetch spot price for {spot_symbol}: {e}")

    try:
        option_ltp = broker.get_ltp(trade_symbol)
    except Exception as e:
        option_ltp = None
        logging.warning(f"Could not fetch quote for {trade_symbol}: {e}")

    parsed = parse_option_symbol(trade_symbol)

    logging.info("=" * 60)
    logging.info("TRACKING SUMMARY")
    logging.info(f"  Underlying spot ({spot_symbol}): "
                 f"{spot_ltp if spot_ltp is not None else 'unavailable'}")
    logging.info(f"  Tracking symbol: {trade_symbol}")
    if parsed:
        logging.info(f"    Underlying : {parsed['underlying']}")
        logging.info(f"    Expiry     : {parsed['expiry_display']}")
        logging.info(f"    Strike     : {parsed['strike']}")
        logging.info(f"    Type       : {parsed['option_type']}")
    else:
        logging.info("    (Weekly or active custom contract format)")
    logging.info(f"  Current premium (LTP): "
                 f"{option_ltp if option_ltp is not None else 'unavailable'}")
    logging.info(f"  Timeframe: {cfg['strategy']['timeframe']}  "
                 f"Risk:Reward: 1:{cfg['strategy']['risk_reward']}")
    logging.info(f"  Sizing mode: {cfg['sizing']['mode']}")
    logging.info(f"  Product type: {cfg['order']['product_type']}")
    logging.info("=" * 60)


# =========================================================================
# STRATEGY
# =========================================================================
TIMEFRAME_MINUTES = {
    "M1": 1, "M3": 3, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440,
}


@dataclass
class ArmedSignal:
    high: float
    low: float
    armed_at: pd.Timestamp
    expires_at: pd.Timestamp


class VwapReclaimBreakoutStrategy:

    def __init__(self, timeframe: str, risk_reward: float):
        if timeframe not in TIMEFRAME_MINUTES:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        self.timeframe = timeframe
        self.timeframe_minutes = TIMEFRAME_MINUTES[timeframe]
        self.risk_reward = risk_reward
        self.armed: Optional[ArmedSignal] = None
        self._last_seen_candle_ts = None

    @staticmethod
    def compute_vwap_1m(df_1m: pd.DataFrame) -> pd.DataFrame:
        df = df_1m.copy()
        if df.empty:
            df["session_date"] = []
            df["tp_vol"] = []
            df["cum_tp_vol"] = []
            df["cum_vol"] = []
            df["vwap"] = []
            return df
        df["session_date"] = df["timestamp"].dt.date
        typical_price = (df["high"] + df["low"] + df["close"]) / 3
        df["tp_vol"] = typical_price * df["volume"]
        df["cum_tp_vol"] = df.groupby("session_date")["tp_vol"].cumsum()
        df["cum_vol"] = df.groupby("session_date")["volume"].cumsum()
        df["vwap"] = df["cum_tp_vol"] / df["cum_vol"].replace(0, np.nan)
        return df

    def build_candles(self, df_1m: pd.DataFrame) -> pd.DataFrame:
        if df_1m.empty:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "vwap"])
        df_1m = self.compute_vwap_1m(df_1m)
        rule = "1D" if self.timeframe == "D1" else f"{self.timeframe_minutes}min"
        ohlc = (
            df_1m.set_index("timestamp")
            .resample(rule, label="right", closed="right")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna()
            .reset_index()
        )
        if ohlc.empty:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "vwap"])
        vwap_series = df_1m[["timestamp", "vwap"]].sort_values("timestamp")
        ohlc = pd.merge_asof(ohlc.sort_values("timestamp"), vwap_series,
                              on="timestamp", direction="backward")
        return ohlc

    @staticmethod
    def _is_signal_candle(row) -> bool:
        if pd.isna(row.get("vwap")):
            return False
        return row["close"] > row["open"] and row["low"] < row["vwap"] and row["close"] > row["vwap"]

    def on_new_closed_candle(self, candle: pd.Series):
        ts = candle["timestamp"]
        if self._last_seen_candle_ts == ts:
            return
        self._last_seen_candle_ts = ts

        if self.armed is not None and ts >= self.armed.expires_at:
            self.armed = None

        if self.armed is None and self._is_signal_candle(candle):
            expires = ts + pd.Timedelta(minutes=self.timeframe_minutes)
            self.armed = ArmedSignal(high=candle["high"], low=candle["low"],
                                      armed_at=ts, expires_at=expires)

    def check_breakout(self, ltp: float, now_ts: pd.Timestamp):
        if self.armed is None:
            return None
        if now_ts >= self.armed.expires_at:
            self.armed = None
            return None
        if ltp > self.armed.high:
            entry = ltp
            sl = self.armed.low
            risk = entry - sl
            if risk <= 0:
                self.armed = None
                return None
            target = entry + self.risk_reward * risk
            self.armed = None
            return {"entry": entry, "sl": sl, "target": target}
        return None


# =========================================================================
# POSITION SIZING
# =========================================================================
def compute_quantity(sizing_cfg: dict, lot_size: int, entry_price: float) -> int:
    mode = sizing_cfg["mode"]
    if mode == "quantity":
        qty = int(sizing_cfg["quantity"])
        if qty % lot_size != 0:
            adjusted = (qty // lot_size) * lot_size
            if adjusted <= 0:
                adjusted = lot_size
            logging.warning(
                f"Configured quantity {qty} is not a multiple of lot size {lot_size}. "
                f"Adjusting quantity to {adjusted}."
            )
            qty = adjusted
    elif mode == "amount":
        amount = float(sizing_cfg["amount"])
        if entry_price <= 0:
            raise ValueError("entry_price must be > 0 to size by amount")
        lots = math.floor((amount / entry_price) / lot_size)
        qty = lots * lot_size
        if qty <= 0:
            raise ValueError(
                f"Allocated amount {amount} is too small for one lot "
                f"({lot_size} units at price {entry_price})"
            )
    else:
        raise ValueError(f"Unknown sizing mode: {mode}")

    if qty % lot_size != 0:
        raise ValueError(f"Quantity {qty} is not a multiple of lot size {lot_size}")
    return qty


# =========================================================================
# PERSISTENT POSITION STORE
# =========================================================================
class PositionStore:

    def __init__(self, path: str):
        self.path = path
        if not os.path.exists(self.path):
            self._write({"open_position": None})

    def _read(self) -> Dict:
        if not os.path.exists(self.path):
            return {"open_position": None}
        try:
            with open(self.path, "r") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logging.error(f"Error decoding state file {self.path}: {e}. Resetting position state.")
            data = {"open_position": None}
            self._write(data)
            return data

    def _write(self, data: Dict):
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_path, self.path)

    def has_open_position(self) -> bool:
        return self._read().get("open_position") is not None

    def get_open_position(self) -> Optional[Dict]:
        return self._read().get("open_position")

    def open_position(self, position: Dict):
        data = self._read()
        if data.get("open_position") is not None:
            raise RuntimeError("A position is already open -- refusing to overwrite.")
        data["open_position"] = position
        self._write(data)

    def close_position(self):
        data = self._read()
        data["open_position"] = None
        self._write(data)


# =========================================================================
# MAIN LOOP
# =========================================================================
def setup_logging(log_file: str):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
    )


def in_market_hours(cfg: dict, now_ist: datetime) -> bool:
    start_h, start_m = map(int, cfg["market_hours_ist"]["start"].split(":"))
    end_h, end_m = map(int, cfg["market_hours_ist"]["end"].split(":"))
    return dtime(start_h, start_m) <= now_ist.time() <= dtime(end_h, end_m)


def handle_open_position(broker: FyersBroker, store: PositionStore, symbol: str):
    pos = store.get_open_position()
    if not pos:
        return
    target_symbol = pos.get("symbol") or symbol
    ltp = broker.get_ltp(target_symbol)
    logging.info(f"Monitoring open position ({target_symbol}): entry={pos['entry']} sl={pos['sl']} "
                 f"target={pos['target']} ltp={ltp}")

    exit_reason = None
    if ltp <= pos["sl"]:
        exit_reason = "STOPLOSS"
    elif ltp >= pos["target"]:
        exit_reason = "TARGET"

    if exit_reason:
        order_id = broker.place_order(symbol=target_symbol, qty=pos["qty"], side="SELL",
                                       product_type=pos["product_type"], order_type="MARKET")
        logging.info(f"Exit triggered ({exit_reason}) for {target_symbol} at ltp={ltp}, order_id={order_id}")
        store.close_position()


def handle_signal_and_entry(broker: FyersBroker, store: PositionStore,
                             strat: VwapReclaimBreakoutStrategy, cfg: dict, symbol: str):
    now_ist = datetime.now(IST)

    df_1m = broker.get_historical_1m(symbol, days_back=5)
    candles = strat.build_candles(df_1m)
    if candles.empty:
        return

    closed_candles = candles[candles["timestamp"] <= pd.Timestamp(now_ist)]
    if not closed_candles.empty:
        last_closed = closed_candles.iloc[-1]
        strat.on_new_closed_candle(last_closed)

    ltp = broker.get_ltp(symbol)
    result = strat.check_breakout(ltp, pd.Timestamp(now_ist))
    if result is None:
        return

    lot_size = cfg["symbol"]["lot_size"]
    qty = compute_quantity(cfg["sizing"], lot_size, result["entry"])

    order_id = broker.place_order(symbol=symbol, qty=qty, side="BUY",
                                   product_type=cfg["order"]["product_type"],
                                   order_type="MARKET")
    logging.info(f"ENTRY: symbol={symbol} qty={qty} entry~={result['entry']} "
                 f"sl={result['sl']} target={result['target']} order_id={order_id}")

    store.open_position({
        "symbol": symbol, "qty": qty, "entry": result["entry"], "sl": result["sl"],
        "target": result["target"], "product_type": cfg["order"]["product_type"],
        "opened_at": str(now_ist),
    })


# =========================================================================
# LIVE OPTION CHAIN LOOKUP & AUTOMATIC EXPIRY / ATM RESOLUTION
# =========================================================================
SUGGESTED_LOT_SIZE = 65


def get_expiry_candidates(chosen_expiry: dict) -> list:
    """Generates candidate values for option chain timestamp query."""
    candidates = []
    exp_val = chosen_expiry.get("expiry")
    if exp_val:
        candidates.append(str(exp_val))
        try:
            candidates.append(int(exp_val))
        except Exception:
            pass

    raw_date = chosen_expiry.get("date") or chosen_expiry.get("expiry_date")
    if raw_date:
        candidates.append(str(raw_date))
        if len(raw_date) == 10 and raw_date[2] == "-" and raw_date[5] == "-":
            d, m, y = raw_date.split("-")
            candidates.append(f"{y}-{m}-{d}")

    seen = set()
    out = []
    for c in candidates:
        if c not in seen and c is not None and c != "":
            seen.add(c)
            out.append(c)
    return out


def auto_resolve_atm_option(broker: FyersBroker, cfg: dict, option_type: str = "CE") -> Optional[Dict]:
    underlying_symbol = cfg["symbol"]["spot_symbol"]
    fyers = broker.fyers

    resp = fyers.optionchain(data={
        "symbol": underlying_symbol, "strikecount": 1, "timestamp": "",
    })
    if resp.get("s") != "ok":
        logging.warning(f"Option chain fetch failed: {resp}")
        return None

    data = resp.get("data", {})
    chain = data.get("optionChain", []) or data.get("optionsChain", [])
    spot_ltp = None
    for row in chain:
        if row.get("option_type") in (None, "", "-"):
            spot_ltp = row.get("ltp")
            break

    if spot_ltp is None or spot_ltp <= 0:
        try:
            spot_ltp = broker.get_ltp(underlying_symbol)
        except Exception as e:
            logging.warning(f"Could not fetch spot LTP for {underlying_symbol}: {e}")

    expiries = data.get("expiryData", [])
    if not expiries:
        logging.warning(f"No expiries returned: {data}")
        return None

    chosen_expiry = expiries[0]
    exp_display = chosen_expiry.get("expiry") or chosen_expiry.get("date") or chosen_expiry.get("expiry_date")
    logging.info(f"Auto-selected current expiry: {exp_display}")

    candidates = get_expiry_candidates(chosen_expiry)
    resp2 = None
    for cand in candidates:
        r = fyers.optionchain(data={
            "symbol": underlying_symbol,
            "strikecount": 10,
            "timestamp": cand,
        })
        if r.get("s") == "ok":
            resp2 = r
            logging.info(f"Option chain fetched successfully for expiry timestamp: {cand}")
            break

    if not resp2:
        logging.warning(f"Option chain fetch failed for expiry candidates {candidates}")
        return None

    data2 = resp2.get("data", {})
    chain2 = data2.get("optionChain", []) or data2.get("optionsChain", [])
    by_strike = {}
    for row in chain2:
        strike = row.get("strike_price")
        if strike is None:
            strike = row.get("strikePrice", row.get("strike"))
        if strike is None:
            continue
        opt_type = row.get("option_type")
        by_strike.setdefault(float(strike), {})[opt_type] = row

    rows_list = sorted(by_strike.items())
    if not rows_list:
        logging.warning("No strike rows parsed from option chain.")
        return None

    ref_spot = spot_ltp if spot_ltp is not None else 0.0
    atm_idx = min(range(len(rows_list)), key=lambda i: abs(rows_list[i][0] - ref_spot))
    strike, sides = rows_list[atm_idx]

    chosen = sides.get(option_type, {})
    symbol = chosen.get("symbol")
    if not symbol:
        logging.warning(f"Selected option side {option_type} not available for strike {strike}.")
        return None

    lot_size = cfg["symbol"].get("lot_size", SUGGESTED_LOT_SIZE)
    quantity = compute_quantity(cfg["sizing"], lot_size, spot_ltp or 100.0)

    logging.info(f"AUTO-RESOLVED ATM CONTRACT: {symbol} (Strike: {strike} {option_type}, Spot: {spot_ltp})")
    return {
        "trade_symbol": symbol,
        "lot_size": lot_size,
        "quantity": quantity,
        "expiry": exp_display,
        "strike": strike,
        "option_type": option_type,
    }


def run_option_chain_lookup(broker: FyersBroker, cfg: dict) -> Optional[Dict]:
    auto_picked = auto_resolve_atm_option(broker, cfg)
    if auto_picked:
        confirm = input(
            f"\nAutomatically selected current expiry contract: {auto_picked['trade_symbol']} "
            f"(Strike: {auto_picked['strike']} {auto_picked['option_type']}).\n"
            f"Use this contract? (Y/n / 'm' for manual pick): "
        ).strip().lower()
        if confirm != "m" and confirm != "n":
            return auto_picked

    underlying_symbol = cfg["symbol"]["spot_symbol"]
    fyers = broker.fyers

    resp = fyers.optionchain(data={
        "symbol": underlying_symbol, "strikecount": 1, "timestamp": "",
    })
    if resp.get("s") != "ok":
        print(f"Option chain fetch failed: {resp}")
        return None

    data = resp.get("data", {})
    chain = data.get("optionChain", []) or data.get("optionsChain", [])
    spot_ltp = None
    for row in chain:
        if row.get("option_type") in (None, "", "-"):
            spot_ltp = row.get("ltp")
            break

    if spot_ltp is None or spot_ltp <= 0:
        try:
            spot_ltp = broker.get_ltp(underlying_symbol)
        except Exception as e:
            logging.warning(f"Could not fetch spot LTP for {underlying_symbol}: {e}")

    expiries = data.get("expiryData", [])
    if not expiries:
        print(f"No expiries returned: {data}")
        return None

    print(f"\nUnderlying: {underlying_symbol}  Spot LTP: {spot_ltp}")
    print("\nAvailable expiries (nearest first -- [0] is the nearest weekly expiry):")
    for i, exp in enumerate(expiries):
        exp_str = exp.get("expiry") or exp.get("expiry_date") or exp.get("date")
        date_param = exp.get("date") or exp.get("expiry_date") or exp.get("expiry")
        print(f"  [{i}] {exp_str}  (date param: {date_param})")

    choice = input(
        "\nPick an expiry number, or press Enter to auto-pick current "
        "(weekly) expiry [0], or type 'c' to cancel: "
    ).strip()
    if choice.lower() == "c":
        return None
    expiry_idx = int(choice) if choice else 0
    chosen_expiry = expiries[expiry_idx]

    candidates = get_expiry_candidates(chosen_expiry)
    resp2 = None
    for cand in candidates:
        r = fyers.optionchain(data={
            "symbol": underlying_symbol,
            "strikecount": 10,
            "timestamp": cand,
        })
        if r.get("s") == "ok":
            resp2 = r
            break

    if not resp2:
        print(f"Option chain fetch failed for expiry {chosen_expiry}")
        return None

    data2 = resp2.get("data", {})
    chain2 = data2.get("optionChain", []) or data2.get("optionsChain", [])
    by_strike = {}
    for row in chain2:
        strike = row.get("strike_price")
        if strike is None:
            strike = row.get("strikePrice", row.get("strike"))
        if strike is None:
            continue
        opt_type = row.get("option_type")
        by_strike.setdefault(float(strike), {})[opt_type] = row

    rows_list = sorted(by_strike.items())
    if not rows_list:
        print("No strike rows parsed from option chain.")
        return None

    ref_spot = spot_ltp if spot_ltp is not None else 0.0
    atm_idx = min(range(len(rows_list)), key=lambda i: abs(rows_list[i][0] - ref_spot))

    for idx, (strike, sides) in enumerate(rows_list):
        ce = sides.get("CE", {})
        pe = sides.get("PE", {})
        marker = "  <- ATM" if idx == atm_idx else ""
        print(f"[{idx:>3}] {strike:>6.1f} | {ce.get('symbol', '-'):<28} "
              f"{ce.get('ltp', '-'):>8} | {pe.get('symbol', '-'):<28} "
              f"{pe.get('ltp', '-'):>8}{marker}")

    pick = input(
        "\nPick a row number, or press Enter to auto-pick the ATM strike "
        "shown above, or type 'c' to cancel: "
    ).strip()
    if pick.lower() == "c":
        return None
    row_idx = int(pick) if pick else atm_idx
    strike, sides = rows_list[row_idx]

    side = input(
        "CE or PE? (press Enter for CE -- this strategy trades bullish "
        "breakouts): "
    ).strip().upper()
    side = side if side in ("CE", "PE") else "CE"
    chosen = sides.get(side, {})
    symbol = chosen.get("symbol")
    if not symbol:
        print(f"Selected option side {side} not available for strike {strike}.")
        return None

    lot_size_input = input(
        f"Confirm/enter current lot size [{SUGGESTED_LOT_SIZE}]: "
    ).strip()
    lot_size = int(lot_size_input) if lot_size_input else SUGGESTED_LOT_SIZE

    lots = input("How many lots do you want to trade [1]: ").strip()
    lots = int(lots) if lots else 1
    quantity = lots * lot_size

    return {"trade_symbol": symbol, "lot_size": lot_size, "quantity": quantity}


def main():
    setup_logging(CONFIG["log_file"])

    broker = BROKER_CLASS(CONFIG)
    logging.info("Logging in...")
    broker.login()
    logging.info("Login successful.")

    if CONFIG["symbol"].get("auto_select_option"):
        auto_picked = auto_resolve_atm_option(broker, CONFIG)
        if auto_picked:
            CONFIG["symbol"]["trade_symbol"] = auto_picked["trade_symbol"]
            CONFIG["symbol"]["lot_size"] = auto_picked["lot_size"]
            CONFIG["sizing"]["mode"] = "quantity"
            CONFIG["sizing"]["quantity"] = auto_picked["quantity"]
            logging.info(f"Auto-selected tracking contract: {auto_picked['trade_symbol']} "
                         f"(qty={auto_picked['quantity']})")

    print_startup_summary(broker, CONFIG)

    spot_symbol = CONFIG["symbol"]["spot_symbol"]
    trade_symbol = CONFIG["symbol"]["trade_symbol"]
    if isinstance(broker, FyersBroker):
        broker.start_websocket(symbols=[spot_symbol, trade_symbol])

    store = PositionStore(CONFIG["state_file"])
    strat = VwapReclaimBreakoutStrategy(
        timeframe=CONFIG["strategy"]["timeframe"],
        risk_reward=CONFIG["strategy"]["risk_reward"],
    )
    symbol = CONFIG["symbol"]["trade_symbol"]
    monitor_interval = CONFIG["polling"]["position_monitor_seconds"]

    while True:
        try:
            now_ist = datetime.now(IST)

            if store.has_open_position():
                handle_open_position(broker, store, symbol)
            elif in_market_hours(CONFIG, now_ist):
                handle_signal_and_entry(broker, store, strat, CONFIG, symbol)

            time.sleep(monitor_interval)

        except KeyboardInterrupt:
            logging.info("Stopped by user.")
            break
        except Exception as e:
            logging.exception(f"Loop error: {e}")
            time.sleep(monitor_interval)


if __name__ == "__main__":
    main()
