"""
Nifty Option-Buying Bot (CE + PE, long-only) - SINGLE FILE / PyCharm build
============================================================================
Python port of VWAP_EMA_Ribbon_EA.mq5, sell/short side removed entirely,
applied to the OPTION'S OWN premium candles instead of the underlying.

Everything (broker interface, Fyers implementation + auto TOTP login,
EMA-ribbon/signal-candle/breakout strategy engine, strike selector,
position manager, main loop) lives in this one file so you can just open
it in PyCharm, edit strategy parameters at the top of this file, hit Run, and go.

Credentials (App ID / Secret / Redirect URL / TOTP secret / PIN) are stored in
broker_credentials.json or config.json so you don't leak sensitive data.

------------------------------------------------------------------------
STRATEGY CONFIGURATION (EDIT PARAMETERS HERE DIRECTLY IN PYCHARM)
------------------------------------------------------------------------
You can change timeframe_minutes, EMA periods, premium bands, lot sizes, etc.
directly in the CONFIG_TEMPLATE dictionary below! Any edits you make here
will automatically sync and be used immediately when you hit Run.
"""

import base64
import hashlib
import io
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, date, time as dtime, timedelta
from typing import Optional, List, Dict, Any, Tuple

import numpy as np
import pandas as pd
import pyotp
import requests
from fyers_apiv3 import fyersModel

# ============================================================================
# 0. CONFIG LOADING & STRATEGY PARAMETERS
# ============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DATA_DIR = os.path.join(BASE_DIR, "data")
TOKEN_CACHE_PATH = os.path.join(DATA_DIR, "token_cache.json")
SYMBOL_MASTER_CACHE_PATH = os.path.join(DATA_DIR, "symbol_master_cache.csv")
os.makedirs(DATA_DIR, exist_ok=True)

# You can edit strategy parameters directly in this dict inside PyCharm!
CONFIG_TEMPLATE = {
    "broker": "fyers",
    "fyers": {
        "app_id": "XXXXX-100",
        "secret_id": "YOUR_SECRET_ID",
        "redirect_uri": "https://trade.fyers.in/api-login/redirect-uri/index.html",
        "auto_totp_login": False,
        "fy_id": "YOUR_FYERS_CLIENT_ID (e.g. XA01234) - only needed if auto_totp_login is True",
        "totp_secret": "YOUR_TOTP_SECRET_KEY_FROM_2FA_SETUP - only needed if auto_totp_login is True",
        "pin": "YOUR_4_DIGIT_TRADING_PIN - only needed if auto_totp_login is True"
    },
    "strategy": {
        "underlying": "NIFTY",
        "spot_symbol": "NSE:NIFTY50-INDEX",
        # timeframe_minutes: ANY positive integer (1, 2, 3, 5, 7, 10, 15, 45, 90, 240, ...).
        # Change this value anytime right here in PyCharm and click Run!
        "timeframe_minutes": 1,
        # EMA periods: ANY positive integers (fast < slow < main recommended).
        "ema_main_period": 34,
        "ema_fast_period": 13,
        "ema_slow_period": 21,
        "premium_band_low": 180,
        "premium_band_high": 200,
        "premium_reselect_tolerance": 40,
        "max_strikes_each_side": 25,
        "use_candle_size_filter": True,
        "max_candle_percent": 3.0,
        "use_origin_filter": True,
        "origin_lookback_bars": 10,
        "use_fractal_origin": True,
        "fractal_lookback_bars": 20,
        "require_fresh_cross": True,
        "risk_reward_ratio": 2.0,
        "sl_buffer_points": 0.5,
        "num_lots": 1,
        "trade_ce": True,
        "trade_pe": True,
        "product_type": "INTRADAY",
        "square_off_time": "15:15"
    }
}


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w") as f:
            json.dump(CONFIG_TEMPLATE, f, indent=2)
        print(f"[CONFIG] Initialized new config.json at {CONFIG_PATH}")
        return CONFIG_TEMPLATE

    with open(CONFIG_PATH, "r") as f:
        try:
            cfg = json.load(f)
        except Exception:
            cfg = CONFIG_TEMPLATE

    # Always allow strategy parameters in this Python file (CONFIG_TEMPLATE) to drive execution!
    # Merge CONFIG_TEMPLATE["strategy"] so any strategy parameter edited in PyCharm is immediately active.
    cfg["strategy"] = {**cfg.get("strategy", {}), **CONFIG_TEMPLATE["strategy"]}

    # Update config.json on disk so disk state stays synchronized
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

    mtime = datetime.fromtimestamp(os.path.getmtime(CONFIG_PATH)).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[CONFIG] Reading strategy settings from code & config.json ({mtime})")

    _warn_if_template_values_untouched(cfg)
    return cfg


def _has_valid_broker_credentials_file() -> bool:
    alt_path = os.path.join(BASE_DIR, "broker_credentials.json")
    if not os.path.exists(alt_path):
        return False
    try:
        with open(alt_path, "r") as f:
            alt = json.load(f)
        fy_alt = alt.get("fyers", {})
        return bool(fy_alt.get("client_id") and fy_alt.get("secret_key") and fy_alt.get("redirect_uri"))
    except Exception:
        return False


def _warn_if_template_values_untouched(cfg: dict) -> None:
    fy = cfg.get("fyers", {})
    template_fy = CONFIG_TEMPLATE["fyers"]

    keys_to_check = ["app_id", "secret_id", "fy_id", "totp_secret", "pin"]
    if _has_valid_broker_credentials_file():
        keys_to_check = [k for k in keys_to_check if k not in ("app_id", "secret_id")]
        print(f"[CONFIG] broker_credentials.json is present and valid - config.json's own "
              f"app_id/secret_id fields are unused.")

    placeholder_hits = [key for key in keys_to_check if fy.get(key) == template_fy.get(key)]
    if placeholder_hits:
        print(f"[WARN] Placeholder credentials detected for: {', '.join(placeholder_hits)}. "
              f"If using manual browser login, app_id and redirect_uri must match your Fyers App.")


CONFIG: Optional[dict] = None


def get_config() -> dict:
    global CONFIG
    if CONFIG is None:
        CONFIG = load_config()
    return CONFIG


# ============================================================================
# 0b. STRATEGY CONFIG VALIDATION
# ============================================================================
FYERS_NATIVE_RESOLUTIONS = {"1", "2", "3", "5", "10", "15", "20", "30", "60", "120", "240", "D"}


def validate_strategy_config(s: dict) -> None:
    errors = []

    tf = s.get("timeframe_minutes")
    if not isinstance(tf, int) or isinstance(tf, bool) or tf <= 0:
        errors.append(f"strategy.timeframe_minutes must be a positive integer, got {tf!r}")

    for key in ("ema_main_period", "ema_fast_period", "ema_slow_period"):
        val = s.get(key)
        if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
            errors.append(f"strategy.{key} must be a positive integer, got {val!r}")

    if errors:
        raise ValueError(
            "Invalid strategy config in config.json - fix these before running:\n  - "
            + "\n  - ".join(errors)
        )

    fast, slow, main = s["ema_fast_period"], s["ema_slow_period"], s["ema_main_period"]
    if not (fast < slow < main):
        print(f"[WARN] EMA periods (fast={fast}, slow={slow}, main={main}) don't follow the "
              f"fast < slow < main ordering. The bot will still run with these values, "
              f"but ensure entry condition alignment is expected.")

    if str(tf) not in FYERS_NATIVE_RESOLUTIONS:
        print(f"[INFO] timeframe_minutes={tf} is not natively offered by Fyers API history. "
              f"The bot will automatically fetch 1-minute candles and resample them per-session to {tf}m candles!")


# ============================================================================
# 1. HELPER FUNCTIONS
# ============================================================================
def round_to_tick(price: float, tick_size: float = 0.05) -> float:
    """Rounds order prices to valid exchange tick sizes (default 0.05 for Indian options)."""
    return round(round(price / tick_size) * tick_size, 2)


# ============================================================================
# 2. BROKER-AGNOSTIC INTERFACE
# ============================================================================
from abc import ABC, abstractmethod


@dataclass
class OptionContract:
    symbol: str          # broker-specific trading symbol, e.g. "NSE:NIFTY24D1922500CE"
    strike: float
    option_type: str     # "CE" or "PE"
    expiry: str           # "YYYY-MM-DD"
    lot_size: int         # LIVE lot size for this contract


@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str]
    message: str
    filled_price: Optional[float] = None


class BaseBroker(ABC):
    @abstractmethod
    def login(self) -> None: ...

    @abstractmethod
    def get_ltp(self, symbol: str) -> float: ...

    def get_ltp_bulk(self, symbols: list) -> dict:
        result = {}
        for sym in symbols:
            try:
                result[sym] = self.get_ltp(sym)
            except Exception:
                continue
        return result

    @abstractmethod
    def get_historical_candles(self, symbol: str, timeframe_minutes: int,
                                lookback_bars: int) -> pd.DataFrame: ...

    @abstractmethod
    def get_current_expiry(self, underlying: str) -> str: ...

    @abstractmethod
    def get_option_chain(self, underlying: str, expiry: str) -> list: ...

    @abstractmethod
    def get_lot_size(self, symbol: str) -> int: ...

    @abstractmethod
    def place_market_order(self, symbol: str, qty: int, side: str, product_type: str) -> OrderResult: ...

    @abstractmethod
    def place_stoploss_market_order(self, symbol: str, qty: int, trigger_price: float,
                                     product_type: str) -> OrderResult: ...

    @abstractmethod
    def place_limit_order(self, symbol: str, qty: int, price: float, side: str,
                           product_type: str) -> OrderResult: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    def get_order_status(self, order_id: str) -> str: ...


# ============================================================================
# 3. FYERS DAILY LOGIN AUTOMATION
# ============================================================================
_BASE_URL_1 = "https://api-t2.fyers.in/vagator/v2"
_BASE_URL_2 = "https://api-t1.fyers.in/api/v3"
_URL_SEND_LOGIN_OTP = _BASE_URL_1 + "/send_login_otp_v2"
_URL_VERIFY_TOTP = _BASE_URL_1 + "/verify_otp"
_URL_VERIFY_PIN = _BASE_URL_1 + "/verify_pin"
_URL_TOKEN = _BASE_URL_2 + "/token"
_URL_VALIDATE_AUTH_CODE = _BASE_URL_2 + "/validate-authcode"

_LOGIN_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
}

LOGIN_DEBUG = True


def _b64(value: str) -> str:
    return base64.b64encode(str(value).encode("utf-8")).decode("utf-8")


def _post_login_step(url: str, payload: dict, step_name: str) -> dict:
    resp = requests.post(url, json=payload, headers=_LOGIN_HEADERS, timeout=15)
    if resp.status_code != 200:
        if LOGIN_DEBUG:
            print(f"[LOGIN] {step_name} failed - HTTP {resp.status_code}\n"
                  f"        Response body: {resp.text[:500]}")
        resp.raise_for_status()
    data = resp.json()
    if data.get("s") == "error":
        if LOGIN_DEBUG:
            print(f"[LOGIN] {step_name} returned an error payload: {data}")
        raise RuntimeError(f"{step_name} failed: {data.get('message', data)}")
    return data


def _load_cached_token() -> Optional[str]:
    if not os.path.exists(TOKEN_CACHE_PATH):
        return None
    try:
        with open(TOKEN_CACHE_PATH, "r") as f:
            cache = json.load(f)
        if cache.get("issued_date") == date.today().isoformat():
            return cache.get("access_token")
    except Exception:
        pass
    return None


def _save_token(access_token: str) -> None:
    with open(TOKEN_CACHE_PATH, "w") as f:
        json.dump({
            "access_token": access_token,
            "issued_date": date.today().isoformat(),
            "issued_at": datetime.now().isoformat(),
        }, f, indent=2)


def get_fyers_credentials(cfg: dict) -> dict:
    alt_path = os.path.join(BASE_DIR, "broker_credentials.json")
    if os.path.exists(alt_path):
        try:
            with open(alt_path, "r") as f:
                alt = json.load(f)
            fy_alt = alt.get("fyers", {})
            if fy_alt.get("client_id") and fy_alt.get("secret_key") and fy_alt.get("redirect_uri"):
                print(f"[LOGIN] Using credentials from broker_credentials.json instead of config.json.")
                return {
                    "app_id": fy_alt["client_id"],
                    "secret_id": fy_alt["secret_key"],
                    "redirect_uri": fy_alt["redirect_uri"],
                }
        except Exception as e:
            print(f"[LOGIN] Couldn't read broker_credentials.json ({e}); falling back to config.json.")

    fy = cfg["fyers"]
    return {"app_id": fy["app_id"], "secret_id": fy["secret_id"], "redirect_uri": fy["redirect_uri"]}


def _fyers_fresh_login(cfg: dict) -> str:
    fy = get_fyers_credentials(cfg)
    app_id = fy["app_id"]
    secret_id = fy["secret_id"]
    redirect_uri = fy["redirect_uri"]
    fy_id = cfg["fyers"]["fy_id"]
    totp_secret = cfg["fyers"]["totp_secret"]
    pin = cfg["fyers"]["pin"]
    client_id = app_id

    r1 = _post_login_step(_URL_SEND_LOGIN_OTP, {"fy_id": _b64(fy_id), "app_id": "2"},
                           "send_login_otp")
    request_key = r1["request_key"]

    totp_code = pyotp.TOTP(totp_secret).now()
    r2 = _post_login_step(_URL_VERIFY_TOTP, {"request_key": request_key, "otp": totp_code},
                           "verify_otp")
    request_key_2 = r2["request_key"]

    r3 = _post_login_step(_URL_VERIFY_PIN, {
        "request_key": request_key_2, "identity_type": "pin", "identifier": pin,
    }, "verify_pin")
    access_token_step = r3["data"]["access_token"]

    headers = {**_LOGIN_HEADERS, "authorization": f"Bearer {access_token_step}"}
    payload = {
        "fyers_id": fy_id,
        "app_id": client_id.split("-")[0],
        "redirect_uri": redirect_uri,
        "appType": client_id.split("-")[1] if "-" in client_id else "100",
        "code_challenge": "", "state": "state", "scope": "", "nonce": "",
        "response_type": "code", "create_cookie": True,
    }
    r4 = requests.post(_URL_TOKEN, headers=headers, json=payload, timeout=15)
    if r4.status_code != 200:
        if LOGIN_DEBUG:
            print(f"[LOGIN] token step failed - HTTP {r4.status_code}\n        Response body: {r4.text[:500]}")
        r4.raise_for_status()
    url = r4.json()["Url"]
    auth_code = url.split("auth_code=")[1].split("&")[0]

    app_id_hash = hashlib.sha256(f"{client_id}:{secret_id}".encode()).hexdigest()
    r5 = _post_login_step(_URL_VALIDATE_AUTH_CODE, {
        "grant_type": "authorization_code", "appIdHash": app_id_hash, "code": auth_code,
    }, "validate-authcode")
    final_access_token = r5["access_token"]

    _save_token(final_access_token)
    return final_access_token


def manual_login_fallback(cfg: dict) -> str:
    import webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse, parse_qs

    fy = get_fyers_credentials(cfg)
    client_id = fy["app_id"]
    redirect_uri = fy["redirect_uri"]
    print(f"[LOGIN] Using app_id/client_id: '{client_id}'  |  redirect_uri: '{redirect_uri}'")

    session = fyersModel.SessionModel(
        client_id=client_id, secret_key=fy["secret_id"], redirect_uri=redirect_uri,
        response_type="code", grant_type="authorization_code",
    )
    login_url = session.generate_authcode()

    parsed_redirect = urlparse(redirect_uri)
    is_local = parsed_redirect.hostname in ("localhost", "127.0.0.1", "0.0.0.0")
    auth_code = None

    print(f"\n{'=' * 72}\nOpening your browser to log in to Fyers...\n{login_url}\n{'=' * 72}\n")
    webbrowser.open(login_url)

    if is_local:
        captured = {}
        port = parsed_redirect.port or 80

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                qs = parse_qs(urlparse(self.path).query)
                if "auth_code" in qs:
                    captured["auth_code"] = qs["auth_code"][0]
                elif "code" in qs:
                    captured["auth_code"] = qs["code"][0]
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body>Login captured - you can close "
                                  b"this tab and return to your terminal.</body></html>")

            def log_message(self, format, *args):
                pass

        try:
            print(f"[LOGIN] Waiting for the browser redirect on 127.0.0.1:{port}...")
            server = HTTPServer(("127.0.0.1", port), _Handler)
            server.timeout = 120
            server.handle_request()
            auth_code = captured.get("auth_code")
        except Exception as e:
            print(f"[LOGIN] Local auto-capture failed ({e}) - falling back to manual paste.")

    if not auth_code:
        redirected_url = input("Paste the FULL redirected URL here: ").strip()
        if redirected_url.startswith("http"):
            qs = parse_qs(urlparse(redirected_url).query)
            code_list = qs.get("auth_code") or qs.get("code")
            if not code_list:
                raise ValueError(f"No auth_code/code found in pasted URL: {redirected_url}")
            auth_code = code_list[0]
        else:
            auth_code = redirected_url

    session.set_token(auth_code)
    token_response = session.generate_token()
    access_token = token_response["access_token"]
    _save_token(access_token)
    print("[LOGIN] Access token acquired and cached for the rest of today.")
    return access_token


def get_fyers_access_token(force_refresh: bool = False) -> str:
    if not force_refresh:
        cached = _load_cached_token()
        if cached:
            return cached

    cfg = get_config()
    auto_totp = cfg.get("fyers", {}).get("auto_totp_login", False)

    if auto_totp:
        try:
            return _fyers_fresh_login(cfg)
        except Exception as e:
            print(f"[LOGIN] Automated TOTP login failed ({e}). Falling back to manual login...")
            return manual_login_fallback(cfg)

    return manual_login_fallback(cfg)


# ============================================================================
# 4. FYERS BROKER IMPLEMENTATION WITH DYNAMIC PER-SESSION RESAMPLING & RATE LIMITING
# ============================================================================
SYMBOL_MASTER_URL = "https://public.fyers.in/sym_details/NSE_FO.csv"
SYMBOL_MASTER_COLUMNS = [
    "fytoken", "symbol_details", "exchange_instrument_type", "minimum_lot_size",
    "tick_size", "isin", "trading_session", "last_update", "expiry_date",
    "symbol_ticker", "exchange", "segment", "scrip_code", "underlying_symbol",
    "underlying_scrip_code", "strike_price", "option_type", "tick_value",
    "trading_symbol", "reserved_1", "reserved_2",
]


def resample_1m_candles(df_1m: pd.DataFrame, timeframe_minutes: int) -> pd.DataFrame:
    """
    Resamples 1-minute historical candles into ANY arbitrary timeframe (e.g. 7m, 14m, 45m, 90m).
    Groups data by trading session date so each day starts independently at 09:15 IST,
    preventing overnight candle merging and timestamp drift.
    """
    if df_1m.empty or timeframe_minutes == 1:
        return df_1m

    df = df_1m.copy()
    if "time" not in df.columns:
        return df

    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").reset_index(drop=True)

    resampled_days = []
    for day, group in df.groupby(df["time"].dt.date):
        group = group.sort_values("time").copy()
        anchor = datetime.combine(day, dtime(9, 15))
        resampler = group.resample(
            rule=f"{timeframe_minutes}min",
            on="time",
            origin=anchor,
            closed="left",
            label="left"
        )

        day_resampled = resampler.agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last"
        }).dropna().reset_index()

        resampled_days.append(day_resampled)

    if not resampled_days:
        return df_1m

    final_df = pd.concat(resampled_days, ignore_index=True)
    return final_df.sort_values("time").reset_index(drop=True)


class FyersBroker(BaseBroker):

    def __init__(self):
        self.cfg = get_config()
        self.client_id = get_fyers_credentials(self.cfg)["app_id"]
        self.access_token = None
        self.fyers = None
        self._symbol_master_df = None
        self._symbol_master_fetched_at = None

    def login(self) -> None:
        self.access_token = get_fyers_access_token()
        self.fyers = fyersModel.FyersModel(
            client_id=self.client_id, token=self.access_token, is_async=False, log_path="",
        )
        profile = self.fyers.get_profile()
        if profile.get("s") != "ok":
            self.access_token = get_fyers_access_token(force_refresh=True)
            self.fyers = fyersModel.FyersModel(
                client_id=self.client_id, token=self.access_token, is_async=False, log_path="",
            )

    def _refresh_symbol_master(self) -> pd.DataFrame:
        now = datetime.now()
        if (self._symbol_master_df is not None and self._symbol_master_fetched_at
                and now - self._symbol_master_fetched_at < timedelta(hours=1)):
            return self._symbol_master_df

        resp = requests.get(SYMBOL_MASTER_URL, timeout=30)
        resp.raise_for_status()
        text = resp.text

        head = text.lstrip()[:300]
        if "<html" in head.lower() or "<!doctype" in head.lower():
            raise RuntimeError(
                f"Expected a CSV from {SYMBOL_MASTER_URL} but got HTML page.\nFirst 300 chars:\n{head}"
            )

        first_line = text.split("\n", 1)[0]
        actual_col_count = len(first_line.split(","))
        expected_col_count = len(SYMBOL_MASTER_COLUMNS)
        if actual_col_count < expected_col_count:
            raise RuntimeError(
                f"Fyers' NSE_FO.csv now has {actual_col_count} columns (expected at least {expected_col_count})."
            )

        df = pd.read_csv(io.StringIO(text), header=None,
                          usecols=range(expected_col_count), names=SYMBOL_MASTER_COLUMNS)

        df.to_csv(SYMBOL_MASTER_CACHE_PATH, index=False)
        self._symbol_master_df = df
        self._symbol_master_fetched_at = now
        return df

    def _match_underlying_rows(self, df: pd.DataFrame, underlying: str) -> pd.DataFrame:
        norm_col = df["underlying_symbol"].astype(str).str.strip().str.upper()
        target = underlying.strip().upper()
        aliases = {target, target.replace(" ", ""), target.replace("-", ""),
                   target.replace(" ", "-"), f"{target}50", f"{target} 50", f"{target}-50"}

        rows = df[norm_col.isin(aliases)]
        if not rows.empty:
            return rows

        stem = target.replace("50", "").replace(" ", "").replace("-", "")
        similar = sorted(norm_col[norm_col.str.replace(" ", "").str.replace("-", "").str.contains(stem, na=False)].unique())
        raise RuntimeError(
            f"No rows found for underlying_symbol matching '{underlying}'.\n"
            f"Closest actual values found in symbol master: {similar[:20]}"
        )

    def get_current_expiry(self, underlying: str) -> str:
        df = self._refresh_symbol_master()
        rows = self._match_underlying_rows(df, underlying)
        rows = rows[rows["option_type"].isin(["CE", "PE"])]
        expiries = sorted(pd.to_datetime(rows["expiry_date"], unit="s", errors="coerce").dropna().unique())
        today = pd.Timestamp(datetime.now().date())
        future_expiries = [e for e in expiries if pd.Timestamp(e) >= today]
        if not future_expiries:
            raise RuntimeError(f"No LIVE expiry found for {underlying}.")
        return pd.Timestamp(future_expiries[0]).strftime("%Y-%m-%d")

    def get_option_chain(self, underlying: str, expiry: str) -> list:
        df = self._refresh_symbol_master()
        rows = self._match_underlying_rows(df, underlying).copy()
        rows["expiry_str"] = pd.to_datetime(rows["expiry_date"], unit="s", errors="coerce").dt.strftime("%Y-%m-%d")
        rows = rows[(rows["expiry_str"] == expiry) & (rows["option_type"].isin(["CE", "PE"]))]

        contracts = []
        for _, r in rows.iterrows():
            contracts.append(OptionContract(
                symbol=r["symbol_ticker"], strike=float(r["strike_price"]),
                option_type=r["option_type"], expiry=expiry,
                lot_size=int(r["minimum_lot_size"]),
            ))
        return contracts

    def get_lot_size(self, symbol: str) -> int:
        df = self._refresh_symbol_master()
        row = df[df["symbol_ticker"] == symbol]
        if row.empty:
            raise RuntimeError(f"Symbol {symbol} not found in symbol master.")
        return int(row.iloc[0]["minimum_lot_size"])

    def get_ltp(self, symbol: str) -> float:
        res = self.get_ltp_bulk([symbol])
        if symbol in res:
            return res[symbol]
        resp = self.fyers.quotes({"symbols": symbol})
        if resp.get("s") != "ok" or not resp.get("d"):
            raise RuntimeError(f"Quote fetch failed for {symbol}: {resp}")
        d = resp["d"][0]["v"]
        return float(d["lp"])

    def get_ltp_bulk(self, symbols: list) -> dict:
        if not symbols:
            return {}
        result = {}
        chunk_size = 50
        for i in range(0, len(symbols), chunk_size):
            chunk = symbols[i:i + chunk_size]
            for attempt in range(1, 4):
                try:
                    resp = self.fyers.quotes({"symbols": ",".join(chunk)})
                    if resp.get("code") == 429 or "request limit reached" in str(resp.get("message", "")).lower():
                        time.sleep(0.5 * attempt)
                        continue
                    if resp.get("s") == "ok" and resp.get("d"):
                        for entry in resp["d"]:
                            try:
                                sym = entry.get("n") or entry.get("v", {}).get("symbol")
                                lp = entry.get("v", {}).get("lp")
                                if sym and lp is not None and float(lp) > 0:
                                    result[sym] = float(lp)
                            except Exception:
                                continue
                        break
                except Exception as e:
                    time.sleep(0.5 * attempt)
                    continue
        return result

    def get_historical_candles(self, symbol: str, timeframe_minutes: int,
                                lookback_bars: int) -> pd.DataFrame:
        """
        Fetches historical candles. For native resolutions (1, 2, 3, 5, 10, 15, 30, 45, 60, 120, 240),
        queries Fyers history API directly. For custom resolutions (e.g., 7m, 14m, 90m), fetches 1m
        candles and resamples locally per trading session.
        """
        is_native = str(timeframe_minutes) in FYERS_NATIVE_RESOLUTIONS
        resolution_to_fetch = str(timeframe_minutes) if is_native else "1"

        bars_needed = lookback_bars if is_native else (lookback_bars * timeframe_minutes)
        trading_minutes_per_day = 375  # 09:15 to 15:30
        trading_days_needed = max(5, int(np.ceil(bars_needed / trading_minutes_per_day)) + 3)

        range_to = datetime.now()
        range_from = range_to - timedelta(days=trading_days_needed)

        data = {
            "symbol": symbol, "resolution": resolution_to_fetch, "date_format": "1",
            "range_from": range_from.strftime("%Y-%m-%d"), "range_to": range_to.strftime("%Y-%m-%d"),
            "cont_flag": "1",
        }
        resp = self.fyers.history(data)
        candles = resp.get("candles", [])
        if not candles:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close"])

        df = pd.DataFrame(candles, columns=["epoch", "open", "high", "low", "close", "volume"])
        df["time"] = pd.to_datetime(df["epoch"], unit="s")
        df = df[["time", "open", "high", "low", "close"]].sort_values("time").reset_index(drop=True)

        if not is_native:
            df = resample_1m_candles(df, timeframe_minutes)

        return df.tail(lookback_bars).reset_index(drop=True)

    def place_market_order(self, symbol: str, qty: int, side: str, product_type: str) -> OrderResult:
        fy_side = 1 if side == "BUY" else -1
        payload = {
            "symbol": symbol, "qty": qty, "type": 2, "side": fy_side,
            "productType": product_type, "limitPrice": 0, "stopPrice": 0,
            "validity": "DAY", "disclosedQty": 0, "offlineOrder": False,
        }
        resp = self.fyers.place_order(payload)
        return OrderResult(success=resp.get("s") == "ok", order_id=resp.get("id"),
                            message=resp.get("message", ""))

    def place_stoploss_market_order(self, symbol: str, qty: int, trigger_price: float,
                                     product_type: str) -> OrderResult:
        payload = {
            "symbol": symbol, "qty": qty, "type": 3, "side": -1,
            "productType": product_type, "limitPrice": 0, "stopPrice": round_to_tick(trigger_price),
            "validity": "DAY", "disclosedQty": 0, "offlineOrder": False,
        }
        resp = self.fyers.place_order(payload)
        return OrderResult(success=resp.get("s") == "ok", order_id=resp.get("id"),
                            message=resp.get("message", ""))

    def place_limit_order(self, symbol: str, qty: int, price: float, side: str,
                           product_type: str) -> OrderResult:
        fy_side = 1 if side == "BUY" else -1
        payload = {
            "symbol": symbol, "qty": qty, "type": 1, "side": fy_side,
            "productType": product_type, "limitPrice": round_to_tick(price), "stopPrice": 0,
            "validity": "DAY", "disclosedQty": 0, "offlineOrder": False,
        }
        resp = self.fyers.place_order(payload)
        return OrderResult(success=resp.get("s") == "ok", order_id=resp.get("id"),
                            message=resp.get("message", ""))

    def cancel_order(self, order_id: str) -> bool:
        resp = self.fyers.cancel_order({"id": order_id})
        return resp.get("s") == "ok"

    def get_order_status(self, order_id: str) -> str:
        if not order_id:
            return "REJECTED"
        resp = self.fyers.orderbook({"id": order_id})
        orders = resp.get("orderBook", [])
        if not orders:
            return "PENDING"
        status_map = {1: "CANCELLED", 2: "FILLED", 4: "PENDING", 5: "REJECTED", 6: "PENDING"}
        return status_map.get(orders[0].get("status"), "PENDING")


def get_broker() -> BaseBroker:
    cfg = get_config()
    name = cfg.get("broker", "fyers").lower()

    if name == "fyers":
        return FyersBroker()

    raise ValueError(f"Unknown broker '{name}' in config.json.")


# ============================================================================
# 5. INDICATORS - EMA + Williams Fractal (same defs as MT5 iMA / iFractals)
# ============================================================================
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def add_emas(df: pd.DataFrame, main_period: int, fast_period: int, slow_period: int,
             price_col: str = "close") -> pd.DataFrame:
    df = df.copy()
    df["ema_main"] = ema(df[price_col], main_period)
    df["ema_fast"] = ema(df[price_col], fast_period)
    df["ema_slow"] = ema(df[price_col], slow_period)
    return df


def williams_fractals(df: pd.DataFrame) -> pd.DataFrame:
    """5-bar fractal, 2-bar confirmation lag - same as MT5's iFractals."""
    df = df.copy()
    n = len(df)
    upper = np.zeros(n, dtype=bool)
    lower = np.zeros(n, dtype=bool)
    highs = df["high"].values
    lows = df["low"].values
    for i in range(2, n - 2):
        window_h = highs[i - 2:i + 3]
        window_l = lows[i - 2:i + 3]
        if highs[i] == window_h.max() and (window_h == highs[i]).sum() == 1:
            upper[i] = True
        if lows[i] == window_l.min() and (window_l == lows[i]).sum() == 1:
            lower[i] = True
    df["fractal_upper"] = upper
    df["fractal_lower"] = lower
    return df


# ----------------------------------------------------------------------
# Candle bucketing - Market Open (09:15) anchored for ANY positive integer
# ----------------------------------------------------------------------
MARKET_OPEN_TIME = dtime(9, 15)


def current_bar_open_time(timeframe_minutes: int, now: datetime = None) -> datetime:
    now = now or datetime.now()
    day_open = datetime.combine(now.date(), MARKET_OPEN_TIME)
    if now < day_open:
        return day_open
    minutes_since_open = int((now - day_open).total_seconds() // 60)
    bucket_minutes = (minutes_since_open // timeframe_minutes) * timeframe_minutes
    return day_open + timedelta(minutes=bucket_minutes)


def next_bar_open_time(timeframe_minutes: int, now: datetime = None) -> datetime:
    return current_bar_open_time(timeframe_minutes, now) + timedelta(minutes=timeframe_minutes)


# ============================================================================
# 6. STRATEGY ENGINE - fully configurable EMA ribbon & signal logic
# ============================================================================
@dataclass
class StrategyConfig:
    timeframe_minutes: int = 1
    ema_main_period: int = 34
    ema_fast_period: int = 13
    ema_slow_period: int = 21
    use_candle_size_filter: bool = True
    max_candle_percent: float = 3.0
    use_origin_filter: bool = True
    origin_lookback_bars: int = 10
    use_fractal_origin: bool = True
    fractal_lookback_bars: int = 20
    require_fresh_cross: bool = True
    risk_reward_ratio: float = 2.0
    sl_buffer_points: float = 0.5


@dataclass
class SignalState:
    pending: bool = False
    signal_high: float = 0.0
    signal_low: float = 0.0
    signal_time: Optional[pd.Timestamp] = None
    target_bar_open: Optional[datetime] = None
    buy_origin_ready: bool = True
    last_origin_reason: str = "N/A"


@dataclass
class TradeSignal:
    entry_trigger_price: float
    stop_loss: float
    target: float
    signal_time: pd.Timestamp


class OptionStrategyEngine:
    """Tracks ONE option contract (CE or PE) using user-configured timeframe & EMAs."""

    def __init__(self, cfg: StrategyConfig, label: str = ""):
        self.cfg = cfg
        self.label = label
        self.state = SignalState()
        self._last_closed_main = 0.0
        self._last_closed_fast = 0.0
        self._last_closed_slow = 0.0

    def on_bar_close(self, df_closed: pd.DataFrame) -> None:
        min_bars_needed = max(
            self.cfg.ema_main_period,
            self.cfg.ema_fast_period,
            self.cfg.ema_slow_period,
            self.cfg.origin_lookback_bars + 2,
            self.cfg.fractal_lookback_bars + 4
        )
        if len(df_closed) < min_bars_needed:
            return

        df = add_emas(df_closed, self.cfg.ema_main_period,
                       self.cfg.ema_fast_period, self.cfg.ema_slow_period)
        df = williams_fractals(df)

        idx = len(df) - 1
        self._last_closed_main = df["ema_main"].iloc[idx]
        self._last_closed_fast = df["ema_fast"].iloc[idx]
        self._last_closed_slow = df["ema_slow"].iloc[idx]

        self._update_origin_regime(df, idx)

        open_p = df["open"].iloc[idx]
        high_p = df["high"].iloc[idx]
        low_p = df["low"].iloc[idx]
        close_p = df["close"].iloc[idx]
        ema_main_val = df["ema_main"].iloc[idx]
        if ema_main_val <= 0:
            return

        if self.cfg.use_candle_size_filter:
            candle_range = high_p - low_p
            pct = (candle_range / close_p * 100.0) if close_p > 0 else 0.0
            if pct > self.cfg.max_candle_percent:
                return

        is_green = close_p > open_p
        low_below = low_p < ema_main_val
        close_above = close_p > ema_main_val
        buy_signal = is_green and low_below and close_above
        if not buy_signal:
            return

        if not self._check_buy_origin_valid(df, idx):
            return

        self.state.pending = True
        self.state.signal_high = float(high_p)
        self.state.signal_low = float(low_p)
        self.state.signal_time = df["time"].iloc[idx]
        self.state.target_bar_open = next_bar_open_time(
            self.cfg.timeframe_minutes, df["time"].iloc[idx].to_pydatetime())

    def on_tick(self, ltp: float, now: datetime = None) -> Optional[TradeSignal]:
        if not self.state.pending:
            return None
        now = now or datetime.now()
        this_bar_open = current_bar_open_time(self.cfg.timeframe_minutes, now)

        if this_bar_open != self.state.target_bar_open:
            self.state.pending = False
            return None

        trigger_price = round_to_tick(self.state.signal_high + self.cfg.sl_buffer_points)
        if ltp < trigger_price:
            return None

        live_main = self._live_ema(self._last_closed_main, ltp, self.cfg.ema_main_period)
        live_fast = self._live_ema(self._last_closed_fast, ltp, self.cfg.ema_fast_period)
        live_slow = self._live_ema(self._last_closed_slow, ltp, self.cfg.ema_slow_period)
        if not (live_fast > live_slow > live_main):
            return None

        sl = round_to_tick(self.state.signal_low - self.cfg.sl_buffer_points)
        risk = trigger_price - sl
        if risk <= 0:
            self.state.pending = False
            return None
        target = round_to_tick(trigger_price + risk * self.cfg.risk_reward_ratio)

        signal = TradeSignal(entry_trigger_price=trigger_price, stop_loss=sl,
                              target=target, signal_time=self.state.signal_time)
        self.state.pending = False
        return signal

    def consume_origin_on_fill(self) -> None:
        if self.cfg.require_fresh_cross:
            self.state.buy_origin_ready = False

    def _update_origin_regime(self, df: pd.DataFrame, idx: int) -> None:
        if not self.cfg.require_fresh_cross:
            return
        ema_val = df["ema_main"].iloc[idx]
        close_val = df["close"].iloc[idx]
        if ema_val <= 0:
            return
        if close_val < ema_val:
            self.state.buy_origin_ready = True

    def _check_buy_origin_valid(self, df: pd.DataFrame, idx: int) -> bool:
        if not self.cfg.use_origin_filter:
            return True

        if self.cfg.require_fresh_cross and not self.state.buy_origin_ready:
            self.state.last_origin_reason = (
                "Buy origin BLOCKED: no fresh close below Main EMA since the last BUY signal used this swing")
            return False

        for s in range(idx - 1, max(-1, idx - 1 - self.cfg.origin_lookback_bars), -1):
            ema_val = df["ema_main"].iloc[s]
            close_val = df["close"].iloc[s]
            if ema_val > 0 and close_val > 0 and close_val < ema_val:
                self.state.last_origin_reason = (
                    f"Buy origin OK: prior close below Main EMA {idx - s} bars before signal")
                return True

        if self.cfg.use_fractal_origin:
            for s in range(idx, max(-1, idx - self.cfg.fractal_lookback_bars), -1):
                if not df["fractal_lower"].iloc[s]:
                    continue
                ema_val = df["ema_main"].iloc[s]
                fractal_val = df["low"].iloc[s]
                if ema_val > 0 and fractal_val < ema_val:
                    self.state.last_origin_reason = (
                        f"Buy origin OK: lower fractal below Main EMA {idx - s} bars before signal")
                    return True

        self.state.last_origin_reason = (
            "Buy origin FAILED: no prior close / lower fractal found below Main EMA in lookback window")
        return False

    @staticmethod
    def _live_ema(prev_closed_ema: float, ltp: float, period: int) -> float:
        if prev_closed_ema <= 0:
            return ltp
        alpha = 2.0 / (period + 1)
        return prev_closed_ema + alpha * (ltp - prev_closed_ema)


# ============================================================================
# 7. STRIKE SELECTOR - CE / PE inside your premium band
# ============================================================================
@dataclass
class SelectedOption:
    contract: OptionContract
    ltp: float


class OptionSelector:
    def __init__(self, broker: BaseBroker, underlying: str,
                 band_low: float, band_high: float, reselect_tolerance: float,
                 spot_symbol: Optional[str] = None, max_strikes_each_side: int = 25):
        self.broker = broker
        self.underlying = underlying
        self.band_low = band_low
        self.band_high = band_high
        self.reselect_tolerance = reselect_tolerance
        self.spot_symbol = spot_symbol
        self.max_strikes_each_side = max_strikes_each_side

    def get_current_expiry(self) -> str:
        return self.broker.get_current_expiry(self.underlying)

    def _band_mid(self) -> float:
        return (self.band_low + self.band_high) / 2.0

    def select(self, option_type: str, expiry: str) -> Optional[SelectedOption]:
        chain = self.broker.get_option_chain(self.underlying, expiry)
        chain = [c for c in chain if c.option_type == option_type]
        if not chain:
            return None

        spot = None
        if self.spot_symbol:
            try:
                spot = self.broker.get_ltp(self.spot_symbol)
            except Exception as e:
                print(f"[{option_type}] Spot price fetch error ({e}) - scanning full chain.")
        if spot:
            chain = sorted(chain, key=lambda c: abs(c.strike - spot))[: self.max_strikes_each_side * 2]

        ltp_map = self.broker.get_ltp_bulk([c.symbol for c in chain])

        effective_low = self.band_low - self.reselect_tolerance
        effective_high = self.band_high + self.reselect_tolerance

        best, best_dist = None, None
        for contract in chain:
            ltp = ltp_map.get(contract.symbol)
            if ltp is None:
                continue
            if effective_low <= ltp <= effective_high:
                dist = abs(ltp - self._band_mid())
                if best is None or dist < best_dist:
                    best, best_dist = SelectedOption(contract=contract, ltp=ltp), dist

        if best is None:
            scanned = [(ltp_map[c.symbol], c.symbol, c.strike) for c in chain if c.symbol in ltp_map]
            closest_to_band = sorted(scanned, key=lambda t: abs(t[0] - self._band_mid()))[:5]
            premiums_only = [t[0] for t in scanned]
            if premiums_only:
                print(f"[{option_type}] Scanned {len(chain)} strikes (spot={spot}), none within range {effective_low}-{effective_high}. "
                      f"Premium range: {min(premiums_only):.2f} to {max(premiums_only):.2f}. Closest: {closest_to_band}")
        return best

    def should_reselect(self, current: SelectedOption, current_ltp: Optional[float] = None) -> bool:
        if current_ltp is not None and current_ltp > 0:
            ltp = current_ltp
        else:
            try:
                ltp = self.broker.get_ltp(current.contract.symbol)
            except Exception:
                return False
        low_bound = self.band_low - self.reselect_tolerance
        high_bound = self.band_high + self.reselect_tolerance
        return not (low_bound <= ltp <= high_bound)


# ============================================================================
# 8. POSITION MANAGER - order management & fail-safe LTP checks
# ============================================================================
@dataclass
class LivePosition:
    symbol: str
    qty: int
    entry_price: float
    stop_loss: float
    target: float
    sl_order_id: Optional[str] = None
    tp_order_id: Optional[str] = None
    closed: bool = False
    close_reason: Optional[str] = None


class PositionManager:
    def __init__(self, broker: BaseBroker, product_type: str):
        self.broker = broker
        self.product_type = product_type
        self.position: Optional[LivePosition] = None

    def open_position(self, symbol: str, qty: int, stop_loss: float, target: float) -> Optional[LivePosition]:
        entry_result = self.broker.place_market_order(symbol, qty, "BUY", self.product_type)
        if not entry_result.success:
            print(f"[ERROR] Entry order failed for {symbol}: {entry_result.message}")
            return None

        entry_price = entry_result.filled_price or self.broker.get_ltp(symbol)
        sl_result = self.broker.place_stoploss_market_order(symbol, qty, stop_loss, self.product_type)
        tp_result = self.broker.place_limit_order(symbol, qty, target, "SELL", self.product_type)

        self.position = LivePosition(
            symbol=symbol, qty=qty, entry_price=entry_price, stop_loss=stop_loss, target=target,
            sl_order_id=sl_result.order_id, tp_order_id=tp_result.order_id,
        )
        print(f"[TRADE] BOUGHT {qty} x {symbol} @ {entry_price:.2f} | SL {stop_loss:.2f} | TGT {target:.2f}")
        return self.position

    def poll(self, current_ltp: Optional[float] = None) -> None:
        if self.position is None or self.position.closed:
            return

        # Check broker order status
        sl_status = self.broker.get_order_status(self.position.sl_order_id)
        tp_status = self.broker.get_order_status(self.position.tp_order_id)

        if sl_status == "FILLED":
            self.broker.cancel_order(self.position.tp_order_id)
            self.position.closed, self.position.close_reason = True, "STOPLOSS"
            print(f"[TRADE] {self.position.symbol} closed via STOPLOSS (broker fill confirmed).")
            return
        elif tp_status == "FILLED":
            self.broker.cancel_order(self.position.sl_order_id)
            self.position.closed, self.position.close_reason = True, "TARGET"
            print(f"[TRADE] {self.position.symbol} closed via TARGET (broker fill confirmed).")
            return

        # Fail-safe LTP check if price has crossed SL or Target
        if current_ltp is not None and current_ltp > 0:
            if current_ltp <= self.position.stop_loss:
                print(f"[FAILSAFE] LTP {current_ltp:.2f} crossed SL level {self.position.stop_loss:.2f}! Executing manual exit.")
                self.square_off_now(reason="FAILSAFE_SL")
            elif current_ltp >= self.position.target:
                print(f"[FAILSAFE] LTP {current_ltp:.2f} reached Target level {self.position.target:.2f}! Executing manual exit.")
                self.square_off_now(reason="FAILSAFE_TP")

    def square_off_now(self, reason: str = "MANUAL/EOD") -> None:
        if self.position is None or self.position.closed:
            return
        if self.position.sl_order_id:
            self.broker.cancel_order(self.position.sl_order_id)
        if self.position.tp_order_id:
            self.broker.cancel_order(self.position.tp_order_id)
        self.broker.place_market_order(self.position.symbol, self.position.qty, "SELL", self.product_type)
        self.position.closed, self.position.close_reason = True, reason
        print(f"[TRADE] {self.position.symbol} squared off ({reason}).")

    def has_open_position(self) -> bool:
        return self.position is not None and not self.position.closed


# ============================================================================
# 9. MAIN LOOP & SIDE RUNNER
# ============================================================================
POLL_SECONDS = 5

SPOT_SYMBOL_MAP = {
    "NIFTY": "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX",
}


def resolve_spot_symbol(s: dict) -> Optional[str]:
    override = s.get("spot_symbol")
    if override:
        return override
    return SPOT_SYMBOL_MAP.get(s["underlying"].upper())


class SideRunner:
    """Runs one side of the bot (CE-only, or PE-only) end to end."""

    def __init__(self, option_type: str, broker: BaseBroker, selector: OptionSelector,
                 strategy_cfg: StrategyConfig, num_lots: int, product_type: str):
        self.option_type = option_type
        self.broker = broker
        self.selector = selector
        self.strategy_cfg = strategy_cfg
        self.num_lots = num_lots
        self.product_type = product_type

        self.selected: Optional[SelectedOption] = None
        self.engine: Optional[OptionStrategyEngine] = None
        self.position_mgr = PositionManager(broker, product_type)
        self._last_bar_open = None

    def _select_contract(self) -> None:
        expiry = self.selector.get_current_expiry()
        picked = self.selector.select(self.option_type, expiry)
        if picked is None:
            return
        if self.selected is None:
            print(f"[{self.option_type}] Tracking {picked.contract.symbol} "
                  f"(strike {picked.contract.strike:.0f}, premium {picked.ltp:.2f}, "
                  f"lot size {picked.contract.lot_size})")
        elif picked.contract.symbol != self.selected.contract.symbol:
            print(f"[{self.option_type}] STRIKE SHIFT: {self.selected.contract.symbol} "
                  f"-> {picked.contract.symbol} (premium {picked.ltp:.2f})")
        self.selected = picked
        self.engine = OptionStrategyEngine(self.strategy_cfg, label=self.option_type)

    def initialize(self) -> bool:
        self._select_contract()
        return self.selected is not None

    def _maybe_reselect(self, current_ltp: Optional[float] = None) -> None:
        if self.position_mgr.has_open_position():
            return
        if self.selected is None:
            self._select_contract()
            return
        if self.selector.should_reselect(self.selected, current_ltp=current_ltp):
            print(f"[{self.option_type}] {self.selected.contract.symbol} drifted out of band, reselecting...")
            self._select_contract()

    def tick(self, ltp_map: Optional[dict] = None) -> None:
        symbol = self.selected.contract.symbol if self.selected else None
        ltp = ltp_map.get(symbol, 0.0) if (ltp_map and symbol) else 0.0

        self._maybe_reselect(current_ltp=ltp)
        if self.selected is None or self.engine is None:
            return

        symbol = self.selected.contract.symbol
        now = datetime.now()

        if ltp <= 0:
            try:
                ltp = self.broker.get_ltp(symbol)
            except Exception as e:
                print(f"[{self.option_type}] LTP fetch failed: {e}")
                ltp = 0.0

        # Poll active position with live LTP for fail-safe triggers
        if self.position_mgr.has_open_position():
            self.position_mgr.poll(current_ltp=ltp)
            return

        # Calculate lookback depth dynamically based on timeframe and EMA periods
        max_ema = max(
            self.strategy_cfg.ema_main_period,
            self.strategy_cfg.ema_fast_period,
            self.strategy_cfg.ema_slow_period
        )
        required_bars = max(250, 3 * max_ema)

        this_bar_open = current_bar_open_time(self.strategy_cfg.timeframe_minutes, now)
        if this_bar_open != self._last_bar_open:
            self._last_bar_open = this_bar_open
            hist = self.broker.get_historical_candles(
                symbol, self.strategy_cfg.timeframe_minutes, lookback_bars=required_bars
            )
            if len(hist) >= 2:
                closed_candles = hist.iloc[:-1] if hist["time"].iloc[-1] >= this_bar_open else hist
                self.engine.on_bar_close(closed_candles)

        if ltp <= 0:
            return

        signal = self.engine.on_tick(ltp, now)
        if signal is None:
            return

        live_lot_size = self.broker.get_lot_size(symbol)
        qty = self.num_lots * live_lot_size

        self.position_mgr.open_position(symbol=symbol, qty=qty, stop_loss=signal.stop_loss, target=signal.target)
        self.engine.consume_origin_on_fill()

    def square_off_eod(self) -> None:
        self.position_mgr.square_off_now(reason="EOD")


def _mask(value: str, keep: int = 4) -> str:
    if not value or len(value) <= keep:
        return "*" * len(value or "")
    return value[:keep] + "*" * (len(value) - keep)


def print_startup_summary(broker: BaseBroker, cfg: dict, s: dict, runners: list) -> None:
    fy = cfg.get("fyers", {})
    underlying = s["underlying"]
    expiry = broker.get_current_expiry(underlying)

    line = "=" * 72
    print("\n" + line)
    print(f"  NIFTY OPTION-BUYING BOT - STARTUP SUMMARY  ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    print(line)

    print("\n-- Broker / Login --")
    print(f"  Broker            : {cfg.get('broker', 'fyers').upper()}")
    print(f"  App ID            : {_mask(fy.get('app_id', ''))}")
    print(f"  Redirect URI      : {fy.get('redirect_uri', '')}")
    print(f"  Login status      : CONNECTED")

    print("\n-- Underlying / Expiry --")
    print(f"  Underlying        : {underlying}")
    print(f"  Current expiry    : {expiry}")

    print("\n-- Position sizing --")
    print(f"  Lots configured   : {s['num_lots']}")

    print("\n-- Tracked contracts --")
    for r in runners:
        if r.selected is None:
            print(f"  {r.option_type:<3}: scanning for contract in band {s['premium_band_low']}-{s['premium_band_high']}")
            continue
        c = r.selected.contract
        live_lot_size = broker.get_lot_size(c.symbol)
        qty = s["num_lots"] * live_lot_size
        print(f"  {r.option_type:<3}: {c.symbol}")
        print(f"        Premium (LTP)  : {r.selected.ltp:.2f}")
        print(f"        Live lot size  : {live_lot_size}")
        print(f"        Order quantity : {s['num_lots']} lot(s) x {live_lot_size} = {qty}")

    print("\n-- Strategy settings --")
    print(f"  Timeframe         : {s['timeframe_minutes']}m (Fully Configurable)")
    print(f"  EMA (Main/Fast/Slow): {s['ema_main_period']} / {s['ema_fast_period']} / {s['ema_slow_period']}")
    print(f"  Premium band      : {s['premium_band_low']} - {s['premium_band_high']}")
    print(f"  Candle size filter: {'ON, max ' + str(s['max_candle_percent']) + '%%' if s['use_candle_size_filter'] else 'OFF'}")
    print(f"  Origin filter     : {'ON' if s['use_origin_filter'] else 'OFF'}")
    print(f"  Fresh-cross filter: {'ON' if s['require_fresh_cross'] else 'OFF'}")
    print(f"  Risk : Reward     : 1 : {s['risk_reward_ratio']}")
    print(f"  SL buffer         : {s['sl_buffer_points']} pts")
    print(f"  Product type      : {s['product_type']}")
    print(f"  Square-off time   : {s['square_off_time']}")
    print(f"  Sides traded      : "
          f"{'CE ' if s.get('trade_ce', True) else ''}{'PE' if s.get('trade_pe', True) else ''}".strip())
    print(line + "\n")


def print_live_status(broker: BaseBroker, spot_symbol: Optional[str], runners: list, ltp_map: Optional[dict] = None) -> None:
    now_str = datetime.now().strftime("%H:%M:%S")

    spot_str = "N/A"
    if spot_symbol:
        if ltp_map and spot_symbol in ltp_map:
            spot_str = f"{ltp_map[spot_symbol]:.2f}"
        else:
            try:
                spot_ltp = broker.get_ltp(spot_symbol)
                spot_str = f"{spot_ltp:.2f}"
            except Exception as e:
                spot_str = f"N/A ({e})"

    parts = [f"[{now_str}] SPOT {spot_symbol or '?'} = {spot_str}"]
    for r in runners:
        if r.selected is None:
            parts.append(f"{r.option_type}: scanning for in-band contract")
            continue
        sym = r.selected.contract.symbol
        if ltp_map and sym in ltp_map:
            ltp = ltp_map[sym]
        else:
            try:
                ltp = broker.get_ltp(sym)
            except Exception:
                ltp = r.selected.ltp
        in_pos = " [IN POSITION]" if r.position_mgr.has_open_position() else ""
        parts.append(f"{r.option_type} strike {r.selected.contract.strike:.0f} "
                     f"({sym}) = {ltp:.2f}{in_pos}")
    print("  |  ".join(parts))


def build_strategy_config(s: dict) -> StrategyConfig:
    return StrategyConfig(
        timeframe_minutes=int(s["timeframe_minutes"]),
        ema_main_period=int(s["ema_main_period"]),
        ema_fast_period=int(s["ema_fast_period"]),
        ema_slow_period=int(s["ema_slow_period"]),
        use_candle_size_filter=s["use_candle_size_filter"],
        max_candle_percent=s["max_candle_percent"],
        use_origin_filter=s["use_origin_filter"],
        origin_lookback_bars=s["origin_lookback_bars"],
        use_fractal_origin=s["use_fractal_origin"],
        fractal_lookback_bars=s["fractal_lookback_bars"],
        require_fresh_cross=s["require_fresh_cross"],
        risk_reward_ratio=s["risk_reward_ratio"],
        sl_buffer_points=s["sl_buffer_points"],
    )


def main():
    cfg = get_config()
    s = cfg["strategy"]

    validate_strategy_config(s)

    print(f"[CONFIG] Effective strategy parameters:")
    print(f"[CONFIG]   timeframe_minutes = {s['timeframe_minutes']}")
    print(f"[CONFIG]   ema_main_period   = {s['ema_main_period']}")
    print(f"[CONFIG]   ema_fast_period   = {s['ema_fast_period']}")
    print(f"[CONFIG]   ema_slow_period   = {s['ema_slow_period']}")

    broker = get_broker()
    print("[INIT] Logging in...")
    broker.login()
    print("[INIT] Login OK.")

    strategy_cfg = build_strategy_config(s)
    spot_symbol = resolve_spot_symbol(s)
    selector = OptionSelector(
        broker=broker, underlying=s["underlying"],
        band_low=s["premium_band_low"], band_high=s["premium_band_high"],
        reselect_tolerance=s["premium_reselect_tolerance"],
        spot_symbol=spot_symbol, max_strikes_each_side=s.get("max_strikes_each_side", 25),
    )

    runners = []
    if s.get("trade_ce", True):
        runners.append(SideRunner("CE", broker, selector, strategy_cfg, s["num_lots"], s["product_type"]))
    if s.get("trade_pe", True):
        runners.append(SideRunner("PE", broker, selector, strategy_cfg, s["num_lots"], s["product_type"]))

    print("[INIT] Selecting initial CE/PE contracts inside the premium band...")
    for r in runners:
        r.initialize()

    print_startup_summary(broker, cfg, s, runners)

    square_off_h, square_off_m = map(int, s["square_off_time"].split(":"))
    square_off_at = dtime(square_off_h, square_off_m)

    squared_off_today = False
    last_status_bar_open = None
    while True:
        now = datetime.now()

        if now.time() >= square_off_at and not squared_off_today:
            for r in runners:
                r.square_off_eod()
            squared_off_today = True

        if not squared_off_today:
            # Gather symbols for a single batch quote request
            symbols_to_quote = []
            if spot_symbol:
                symbols_to_quote.append(spot_symbol)
            for r in runners:
                if r.selected is not None:
                    symbols_to_quote.append(r.selected.contract.symbol)

            ltp_map = broker.get_ltp_bulk(symbols_to_quote) if symbols_to_quote else {}

            for r in runners:
                try:
                    r.tick(ltp_map)
                except Exception as e:
                    print(f"[ERROR] {r.option_type} runner tick failed: {e}")

        this_status_bar_open = current_bar_open_time(strategy_cfg.timeframe_minutes, now)
        if this_status_bar_open != last_status_bar_open:
            last_status_bar_open = this_status_bar_open
            try:
                print_live_status(broker, spot_symbol, runners, ltp_map if 'ltp_map' in locals() else None)
            except Exception as e:
                print(f"[ERROR] Status line failed: {e}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
