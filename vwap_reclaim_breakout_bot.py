"""
VWAP-Reclaim Breakout Algo Bot -- single-file, any-broker-compatible
================================================================
Run directly in PyCharm: fill in CONFIG below, then run this file.

pip install fyers-apiv3 pandas requests pytz numpy

--------------------------------------------------------------------------
STRATEGY (Multi-Index Option Price Based + Dynamic Strike Shifting)
  - Tracks multiple spot indices (NIFTY, SENSEX, or any added symbol).
  - Index-specific option premium ranges:
      * NIFTY: 180 to 220 premium range (target ~200)
      * SENSEX: 400 to 450 premium range (target ~425)
  - Signal candle: Green option candle whose low dips below option VWAP,
    closes back above option VWAP, AND close price is within index premium range.
  - Position Management:
      * SL_MODE = "signal_low"
      * LOT_MULTIPLIER = 1
      * DAILY_MAX_LOSS = 50000.0
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
# Position management settings
SL_MODE = "signal_low"       # "signal_low"
LOT_MULTIPLIER = 1           # Multiplier for exchange lot size
DAILY_MAX_LOSS = 50000.0     # Daily loss circuit breaker threshold

# Spot indices to track and support
SPOT_INDICES = [
    "NSE:NIFTY50-INDEX",
    "BSE:SENSEX-INDEX",
]

# Index-specific configurations
INDEX_CONFIGS = {
    "NSE:NIFTY50-INDEX": {
        "name": "NIFTY",
        "lot_size": 65,
        "min_premium": 180.0,
        "max_premium": 220.0,
        "option_type": "CE",
        "default_symbol": "NSE:NIFTY25JAN23500CE",
    },
    "BSE:SENSEX-INDEX": {
        "name": "SENSEX",
        "lot_size": 20,
        "min_premium": 400.0,
        "max_premium": 450.0,
        "option_type": "CE",
        "default_symbol": "BSE:SENSEX25JAN80000CE",
    },
}

CONFIG = {
    "broker": {
        "proxy": {
            "enabled": False,
            "http": "",
            "https": "",
        },
    },

    "symbol": {
        "spot_indices": SPOT_INDICES,
        "auto_select_option": True,
    },

    "strategy": {
        "timeframe": "M15",    # M1, M3, M5, M15, M30, H1, H4, D1
        "risk_reward": 2.0,
        "sl_mode": SL_MODE,
        "lot_multiplier": LOT_MULTIPLIER,
        "daily_max_loss": DAILY_MAX_LOSS,
        "max_open_positions": 1,
    },

    "sizing": {
        "mode": "quantity",
    },

    "order": {
        "product_type": "INTRADAY",
    },

    "polling": {
        "position_monitor_seconds": 5,
    },

    "market_hours_ist": {"start": "09:15", "end": "15:30"},

    "state_file": "position_state.json",
    "log_file": "algo_bot.log",
}

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
                    logging.info(f"Subscribed new symbol {symbol} to WebSocket feed.")
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
# SYMBOL PARSING / STARTUP SUMMARY TABLE
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


def print_startup_summary(broker, tracking_dict: Dict[str, Dict]):
    header = (
        f"{'Index':<8} | {'Spot Symbol':<18} | {'Spot LTP':>10} | "
        f"{'Option Contract':<24} | {'Opt LTP':>9} | {'Target Range':<12} | "
        f"{'Lot Size':<9} | {'Qty':>6}"
    )
    divider = "-" * len(header)
    double_divider = "=" * len(header)

    logging.info(double_divider)
    logging.info("MULTI-INDEX TRACKING SUMMARY TABLE")
    logging.info(double_divider)
    logging.info(header)
    logging.info(divider)

    for spot_sym, info in tracking_dict.items():
        opt_sym = info.get("trade_symbol", "-")
        index_name = info.get("name", spot_sym)
        try:
            spot_ltp_val = broker.get_ltp(spot_sym)
            spot_ltp_str = f"{spot_ltp_val:.2f}"
        except Exception:
            spot_ltp_str = "N/A"

        try:
            opt_ltp_val = broker.get_ltp(opt_sym) if opt_sym else None
            opt_ltp_str = f"{opt_ltp_val:.2f}" if opt_ltp_val is not None else "N/A"
        except Exception:
            opt_ltp_str = "N/A"

        range_str = f"{info.get('min_premium', 0):.0f}-{info.get('max_premium', 0):.0f}"
        lot_str = f"{info.get('lot_size', 0)}x{LOT_MULTIPLIER}"
        qty_val = info.get("quantity", 0)

        row_str = (
            f"{index_name:<8} | {spot_sym:<18} | {spot_ltp_str:>10} | "
            f"{opt_sym:<24} | {opt_ltp_str:>9} | {range_str:<12} | "
            f"{lot_str:<9} | {qty_val:>6}"
        )
        logging.info(row_str)

    logging.info(double_divider)
    logging.info(f"SL_MODE: {SL_MODE}  |  LOT_MULTIPLIER: {LOT_MULTIPLIER}  |  DAILY_MAX_LOSS: {DAILY_MAX_LOSS:.1f}")
    logging.info(double_divider)


# =========================================================================
# STRATEGY (OPTION CANDLE PRICE BASED)
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

    def __init__(self, timeframe: str, risk_reward: float, min_premium: float = 180.0, max_premium: float = 220.0):
        if timeframe not in TIMEFRAME_MINUTES:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        self.timeframe = timeframe
        self.timeframe_minutes = TIMEFRAME_MINUTES[timeframe]
        self.risk_reward = risk_reward
        self.min_premium = min_premium
        self.max_premium = max_premium
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

    def _is_signal_candle(self, row) -> bool:
        if pd.isna(row.get("vwap")):
            return False
        return (
            row["close"] > row["open"]
            and row["low"] < row["vwap"]
            and row["close"] > row["vwap"]
            and (self.min_premium <= row["close"] <= self.max_premium)
        )

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
            logging.info(f"ARMED OPTION SIGNAL: ts={ts} High={candle['high']:.2f} "
                         f"Low={candle['low']:.2f} VWAP={candle['vwap']:.2f} "
                         f"Close={candle['close']:.2f} (in range {self.min_premium}-{self.max_premium})")

    def check_breakout(self, ltp: float, now_ts: pd.Timestamp):
        if self.armed is None:
            return None
        if now_ts >= self.armed.expires_at:
            logging.info(f"Armed signal expired at {now_ts}")
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
def compute_quantity(sizing_cfg: dict, lot_size: int, entry_price: float, lot_multiplier: int = LOT_MULTIPLIER) -> int:
    mode = sizing_cfg.get("mode", "quantity")
    base_lot = lot_size * lot_multiplier
    if mode == "quantity":
        qty = base_lot
    elif mode == "amount":
        amount = float(sizing_cfg.get("amount", 50000))
        if entry_price <= 0:
            raise ValueError("entry_price must be > 0 to size by amount")
        lots = math.floor((amount / entry_price) / lot_size)
        lots = max(1, lots) * lot_multiplier
        qty = lots * lot_size
    else:
        qty = base_lot

    if qty % lot_size != 0:
        qty = (qty // lot_size) * lot_size
    return max(lot_size, qty)


# =========================================================================
# PERSISTENT POSITION STORE WITH DAILY PNL TRACKING
# =========================================================================
class PositionStore:

    def __init__(self, path: str):
        self.path = path
        if not os.path.exists(self.path):
            self._write({"open_position": None, "daily_pnl": 0.0, "pnl_date": str(datetime.now(IST).date())})

    def _read(self) -> Dict:
        if not os.path.exists(self.path):
            return {"open_position": None, "daily_pnl": 0.0, "pnl_date": str(datetime.now(IST).date())}
        try:
            with open(self.path, "r") as f:
                data = json.load(f)
                today_str = str(datetime.now(IST).date())
                if data.get("pnl_date") != today_str:
                    data["daily_pnl"] = 0.0
                    data["pnl_date"] = today_str
                return data
        except json.JSONDecodeError as e:
            logging.error(f"Error decoding state file {self.path}: {e}. Resetting position state.")
            data = {"open_position": None, "daily_pnl": 0.0, "pnl_date": str(datetime.now(IST).date())}
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

    def get_daily_pnl(self) -> float:
        return float(self._read().get("daily_pnl", 0.0))

    def open_position(self, position: Dict):
        data = self._read()
        if data.get("open_position") is not None:
            raise RuntimeError("A position is already open -- refusing to overwrite.")
        data["open_position"] = position
        self._write(data)

    def close_position(self, pnl: float = 0.0):
        data = self._read()
        data["open_position"] = None
        data["daily_pnl"] = float(data.get("daily_pnl", 0.0)) + pnl
        self._write(data)


# =========================================================================
# DYNAMIC STRIKE SHIFTING
# =========================================================================
def check_and_shift_strike(broker: FyersBroker, store: PositionStore,
                           strat: VwapReclaimBreakoutStrategy, cfg: dict,
                           spot_symbol: str) -> str:
    info = INDEX_CONFIGS.get(spot_symbol, {
        "name": spot_symbol, "lot_size": 65, "min_premium": 180.0, "max_premium": 220.0, "option_type": "CE"
    })
    current_symbol = info.get("trade_symbol")
    if not current_symbol or store.has_open_position():
        return current_symbol or ""

    min_prem = info.get("min_premium", 180.0)
    max_prem = info.get("max_premium", 220.0)

    try:
        current_ltp = broker.get_ltp(current_symbol)
    except Exception as e:
        logging.warning(f"Could not fetch LTP for tracking symbol {current_symbol}: {e}")
        current_ltp = None

    if current_ltp is None or current_ltp < min_prem or current_ltp > max_prem:
        logging.info(f"[{info['name']}] Contract {current_symbol} LTP ({current_ltp}) is outside target range ({min_prem}-{max_prem}). "
                     f"Re-scanning option chain for dynamic strike shift...")
        auto_picked = auto_resolve_atm_option(broker, cfg, spot_symbol=spot_symbol, option_type=info.get("option_type", "CE"))
        if auto_picked and auto_picked["trade_symbol"] != current_symbol:
            new_symbol = auto_picked["trade_symbol"]
            logging.info(f"[{info['name']}] DYNAMIC STRIKE SHIFT: Shifting from {current_symbol} (LTP={current_ltp}) "
                         f"to {new_symbol} (New LTP={broker.get_ltp(new_symbol):.2f})")
            info["trade_symbol"] = new_symbol
            info["lot_size"] = auto_picked["lot_size"]
            info["quantity"] = auto_picked["quantity"]

            broker.subscribe_symbol(new_symbol)

            strat.armed = None
            strat._last_seen_candle_ts = None
            return new_symbol

    return current_symbol


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
    logging.info(f"Monitoring open option position ({target_symbol}): entry={pos['entry']:.2f} "
                 f"sl={pos['sl']:.2f} target={pos['target']:.2f} option_ltp={ltp:.2f}")

    exit_reason = None
    if ltp <= pos["sl"]:
        exit_reason = "STOPLOSS"
    elif ltp >= pos["target"]:
        exit_reason = "TARGET"

    if exit_reason:
        order_id = broker.place_order(symbol=target_symbol, qty=pos["qty"], side="SELL",
                                       product_type=pos["product_type"], order_type="MARKET")
        trade_pnl = (ltp - pos["entry"]) * pos["qty"]
        logging.info(f"Exit triggered ({exit_reason}) for {target_symbol} at option_ltp={ltp:.2f}, "
                     f"PnL={trade_pnl:.2f}, order_id={order_id}")
        store.close_position(pnl=trade_pnl)


def handle_signal_and_entry(broker: FyersBroker, store: PositionStore,
                             strat: VwapReclaimBreakoutStrategy, cfg: dict, spot_symbol: str):
    if store.has_open_position():
        return

    info = INDEX_CONFIGS.get(spot_symbol)
    if not info or not info.get("trade_symbol"):
        return

    symbol = info["trade_symbol"]
    now_ist = datetime.now(IST)

    daily_pnl = store.get_daily_pnl()
    if daily_pnl <= -DAILY_MAX_LOSS:
        logging.warning(f"[CIRCUIT BREAKER] Daily loss limit reached ({daily_pnl:.2f} <= -{DAILY_MAX_LOSS:.2f}). "
                        f"Skipping new entry for {symbol}.")
        return

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

    lot_size = info["lot_size"]
    qty = compute_quantity(cfg["sizing"], lot_size, result["entry"], lot_multiplier=LOT_MULTIPLIER)

    order_id = broker.place_order(symbol=symbol, qty=qty, side="BUY",
                                   product_type=cfg["order"]["product_type"],
                                   order_type="MARKET")
    logging.info(f"ENTRY (OPTION [{info['name']}]): symbol={symbol} qty={qty} entry_premium~={result['entry']:.2f} "
                 f"sl={result['sl']:.2f} target={result['target']:.2f} order_id={order_id}")

    store.open_position({
        "symbol": symbol, "qty": qty, "entry": result["entry"], "sl": result["sl"],
        "target": result["target"], "product_type": cfg["order"]["product_type"],
        "opened_at": str(now_ist), "index_name": info["name"],
    })


# =========================================================================
# LIVE OPTION CHAIN LOOKUP & AUTOMATIC EXPIRY / PREMIUM RANGE RESOLUTION
# =========================================================================
def get_expiry_candidates(chosen_expiry: dict) -> list:
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


def auto_resolve_atm_option(broker: FyersBroker, cfg: dict, spot_symbol: str, option_type: str = "CE") -> Optional[Dict]:
    info = INDEX_CONFIGS.get(spot_symbol, {
        "name": spot_symbol, "lot_size": 65, "min_premium": 180.0, "max_premium": 220.0
    })
    min_prem = info.get("min_premium", 180.0)
    max_prem = info.get("max_premium", 220.0)
    target_prem = (min_prem + max_prem) / 2.0
    fyers = broker.fyers

    resp = fyers.optionchain(data={
        "symbol": spot_symbol, "strikecount": 1, "timestamp": "",
    })
    if resp.get("s") != "ok":
        logging.warning(f"Option chain fetch failed for {spot_symbol}: {resp}")
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
            spot_ltp = broker.get_ltp(spot_symbol)
        except Exception as e:
            logging.warning(f"Could not fetch spot LTP for {spot_symbol}: {e}")

    expiries = data.get("expiryData", [])
    if not expiries:
        logging.warning(f"No expiries returned for {spot_symbol}: {data}")
        return None

    chosen_expiry = expiries[0]
    exp_display = chosen_expiry.get("expiry") or chosen_expiry.get("date") or chosen_expiry.get("expiry_date")

    candidates = get_expiry_candidates(chosen_expiry)
    resp2 = None
    for cand in candidates:
        r = fyers.optionchain(data={
            "symbol": spot_symbol,
            "strikecount": 35,
            "timestamp": cand,
        })
        if r.get("s") == "ok":
            resp2 = r
            break

    if not resp2:
        logging.warning(f"[{info['name']}] Option chain fetch failed for expiry candidates {candidates}")
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
        logging.warning(f"[{info['name']}] No strike rows parsed from option chain.")
        return None

    valid_candidates = []
    for strike_val, s_dict in rows_list:
        side_row = s_dict.get(option_type, {})
        sym = side_row.get("symbol")
        ltp_val = side_row.get("ltp")
        if sym and ltp_val is not None and float(ltp_val) > 0:
            valid_candidates.append({
                "strike": strike_val,
                "symbol": sym,
                "ltp": float(ltp_val),
            })

    if not valid_candidates:
        logging.warning(f"[{info['name']}] No valid option contracts found for side {option_type}.")
        return None

    in_range = [c for c in valid_candidates if min_prem <= c["ltp"] <= max_prem]
    if in_range:
        best = min(in_range, key=lambda c: abs(c["ltp"] - target_prem))
    else:
        best = min(valid_candidates, key=lambda c: abs(c["ltp"] - target_prem))

    chosen_strike = best["strike"]
    symbol = best["symbol"]
    opt_ltp = best["ltp"]

    lot_size = info.get("lot_size", 65)
    quantity = compute_quantity(cfg["sizing"], lot_size, opt_ltp or target_prem, lot_multiplier=LOT_MULTIPLIER)

    return {
        "trade_symbol": symbol,
        "lot_size": lot_size,
        "quantity": quantity,
        "expiry": exp_display,
        "strike": chosen_strike,
        "option_type": option_type,
    }


def main():
    setup_logging(CONFIG["log_file"])

    broker = BROKER_CLASS(CONFIG)
    logging.info("Logging in...")
    broker.login()
    logging.info("Login successful.")

    strategies: Dict[str, VwapReclaimBreakoutStrategy] = {}
    ws_subscribe_symbols: List[str] = []

    for spot_sym in SPOT_INDICES:
        info = INDEX_CONFIGS.setdefault(spot_sym, {
            "name": spot_sym, "lot_size": 65, "min_premium": 180.0, "max_premium": 220.0, "option_type": "CE"
        })
        auto_picked = auto_resolve_atm_option(broker, CONFIG, spot_symbol=spot_sym, option_type=info.get("option_type", "CE"))
        if auto_picked:
            info["trade_symbol"] = auto_picked["trade_symbol"]
            info["lot_size"] = auto_picked["lot_size"]
            info["quantity"] = auto_picked["quantity"]
            ws_subscribe_symbols.extend([spot_sym, auto_picked["trade_symbol"]])
        else:
            info["trade_symbol"] = info.get("default_symbol", "")
            ws_subscribe_symbols.append(spot_sym)

        strategies[spot_sym] = VwapReclaimBreakoutStrategy(
            timeframe=CONFIG["strategy"]["timeframe"],
            risk_reward=CONFIG["strategy"]["risk_reward"],
            min_premium=info["min_premium"],
            max_premium=info["max_premium"],
        )

    print_startup_summary(broker, INDEX_CONFIGS)

    if isinstance(broker, FyersBroker):
        broker.start_websocket(symbols=ws_subscribe_symbols)

    store = PositionStore(CONFIG["state_file"])
    monitor_interval = CONFIG["polling"]["position_monitor_seconds"]

    while True:
        try:
            now_ist = datetime.now(IST)

            if store.has_open_position():
                open_pos = store.get_open_position()
                symbol = open_pos.get("symbol", "")
                handle_open_position(broker, store, symbol)
            elif in_market_hours(CONFIG, now_ist):
                for spot_sym in SPOT_INDICES:
                    if store.has_open_position():
                        break
                    strat = strategies[spot_sym]
                    symbol = check_and_shift_strike(broker, store, strat, CONFIG, spot_symbol=spot_sym)
                    if symbol:
                        handle_signal_and_entry(broker, store, strat, CONFIG, spot_symbol=spot_sym)

            time.sleep(monitor_interval)

        except KeyboardInterrupt:
            logging.info("Stopped by user.")
            break
        except Exception as e:
            logging.exception(f"Loop error: {e}")
            time.sleep(monitor_interval)


if __name__ == "__main__":
    main()
