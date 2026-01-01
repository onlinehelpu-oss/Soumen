# -*- coding: utf-8 -*-
"""
REAL ORDERS — Fyers v3 (api-t1) + 5-EMA Strategy on a Custom Timeframe (NIFTY options)
SELL-ONLY VERSION (now: SELL CE entries / BUY CE exits)

What's included:
- Manual robust login (503-safe), stores token (AccessToken/YYYY-MM-DD.json)
- Customizable timeframe: TIMEFRAME_MIN = 1 / 2 / 3 / 5 / 10 / 15 / 30 / 60
- Custom Risk:Reward: R_MULTIPLIER (target = entry - R_MULTIPLIER * range)
- Entry buffer (spot): 3 pts  |  SL buffer (spot): 2 pts  | Target exact (no buffer)
- Option-chain resolver for earliest expiry + nearest 50 strike (CE)  [NIFTY]
- Live notifications always show [TF] and [R:R]
- 15m data/status COMPLETELY REMOVED
"""

from __future__ import annotations

import os, json, time, datetime, hashlib, logging
from urllib.parse import urlparse, parse_qs, quote
from typing import Dict, Any, Optional, Tuple

import requests
import pandas as pd
import numpy as np

class ColoredFormatter(logging.Formatter):
    COLORS = {
        'DEBUG': '\033[94m',  # Blue
        'INFO': '\033[92m',   # Green
        'WARNING': '\033[93m',# Yellow
        'ERROR': '\033[91m',  # Red
        'CRITICAL': '\033[1m\033[91m', # Bold Red
        'RESET': '\033[0m'
    }

    def format(self, record):
        log_message = super().format(record)
        return f"{self.COLORS.get(record.levelname, self.COLORS['RESET'])}{log_message}{self.COLORS['RESET']}"
import pytz
from datetime import datetime as dt, timedelta

from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws

# ============================== LOGIN (v3 api-t1) =============================
class FyersLogin:
    CONFIG_FILE = "fyers_login_details.json"
    TOKENS_DIR = "AccessToken"

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.creds = self._load_or_prompt_creds()
        self.app_id = self.creds["api_key"]
        self.secret_id = self.creds["api_secret"]
        self.redirect_uri = self.creds["redirect_url"]
        self.token_path = os.path.join(self.TOKENS_DIR, f"{datetime.date.today()}.json")

    def _load_or_prompt_creds(self) -> Dict[str, str]:
        if os.path.exists(self.CONFIG_FILE):
            with open(self.CONFIG_FILE, "r") as f:
                return json.load(f)
        self.logger.info("---- Enter your Fyers Login Credentials (v3) ----")
        creds = {
            "api_key": input("Enter APP ID (e.g., ABCDE12345-100): ").strip(),
            "api_secret": input("Enter SECRET ID: ").strip(),
            "redirect_url": input("Enter Redirect URL (must match app): ").strip(),
        }
        if input("Save to 'fyers_login_details.json'? (Y/N): ").strip().upper() == "Y":
            with open(self.CONFIG_FILE, "w") as f:
                json.dump(creds, f, indent=2)
            self.logger.info(f"Saved '{self.CONFIG_FILE}'.")
        else:
            self.logger.info("Skipping save.")
        return creds

    def _build_auth_url(self, state: str = "sample_state") -> str:
        base = "https://api-t1.fyers.in/api/v3/generate-authcode"
        params = (
            f"client_id={quote(self.app_id)}"
            f"&redirect_uri={quote(self.redirect_uri, safe='')}"
            f"&response_type=code"
            f"&state={quote(state)}"
            f"&scope=openid"
            f"&nonce={int(time.time())}"
        )
        return f"{base}?{params}"

    def _extract_code(self, user_input: str) -> str:
        if user_input.startswith("http://") or user_input.startswith("https://"):
            q = parse_qs(urlparse(user_input).query)
            code = q.get("code", [None])[0]
            if not code:
                raise ValueError("No 'code' param found in the provided URL.")
            return code
        return user_input

    def _sha256_appIdHash(self) -> str:
        return hashlib.sha256(f"{self.app_id}:{self.secret_id}".encode("utf-8")).hexdigest()

    def _validate_authcode(self, auth_code: str, max_retries: int = 5) -> Dict[str, Any]:
        url = "https://api-t1.fyers.in/api/v3/validate-authcode"
        payload = {
            "grant_type": "authorization_code",
            "appIdHash": self._sha256_appIdHash(),
            "code": auth_code,
        }
        headers = {"Content-Type": "application/json"}
        for attempt in range(1, max_retries + 1):
            try:
                r = requests.post(url, headers=headers, json=payload, timeout=20)
                if r.status_code == 503:
                    sleep_s = min(2 ** attempt, 30)
                    self.logger.warning(f"[{attempt}/{max_retries}] 503 from auth server. Retrying in {sleep_s}s...")
                    time.sleep(sleep_s)
                    continue
                r.raise_for_status()
                data = r.json()
                if data.get("s") == "error":
                    raise RuntimeError(f"Fyers error {data.get('code')}: {data.get('message')}")
                return data
            except requests.RequestException as e:
                if attempt == max_retries:
                    self.logger.error("Max retries reached. Could not validate auth code.")
                    raise
                sleep_s = min(2 ** attempt, 30)
                self.logger.warning(f"[{attempt}/{max_retries}] Network error: {e}. Retrying in {sleep_s}s...")
                time.sleep(sleep_s)

    def get_access_token(self) -> str:
        if os.path.exists(self.TOKENS_DIR) and os.path.exists(self.token_path):
            with open(self.token_path, "r") as f:
                tok = json.load(f)
            if isinstance(tok, str) and tok:
                self.logger.info(f"Using saved access token: {self.token_path}")
                return tok

        url = self._build_auth_url()
        self.logger.info(f"\nLogin URL (open in browser, complete login):\n{url}")
        user_val = input("\nPaste FULL redirect URL or just the 'code' value here: ").strip()
        code = self._extract_code(user_val)
        token_resp = self._validate_authcode(code)
        access_token = token_resp.get("access_token")
        if not access_token:
            raise RuntimeError(f"Unexpected token response: {token_resp}")

        os.makedirs(self.TOKENS_DIR, exist_ok=True)
        with open(self.token_path, "w") as f:
            json.dump(access_token, f)
        self.logger.info(f"Token saved to {self.token_path}")
        return access_token

# ============================== STRATEGY CONFIG ===============================
class StrategyConfig:
    TIMEZONE = "Asia/Kolkata"
    IST = pytz.timezone(TIMEZONE)

    UNDERLYING_INDEX = "NSE:NIFTY50-INDEX"
    EMA_SPAN = 5
    ROW_LOOKBACK = -2

    TIMEFRAME_MIN = 3
    R_MULTIPLIER = 1.5
    DEFAULT_QTY = 1

    ENTRY_BUFFER = 3.0
    SL_BUFFER = 2.0

    MARKET_START = dt.strptime("09:15", "%H:%M").time()
    MARKET_END = dt.strptime("15:20", "%H:%M").time()

    HEARTBEAT_LIMIT = 5

    def __init__(self):
        self.r_sell = self.R_MULTIPLIER

    def nifty_lot_size_for_date(self, d: dt) -> int:
        cutoff = dt(2025, 12, 30, 15, 30)
        return 75 if d <= cutoff else 65

    def round_to_nearest_50(self, x: float) -> int:
        return int(round(x / 50.0) * 50)

# ============================== DATA HELPERS ==================================
def compute_ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False, min_periods=span).mean()

def candles_df(resp: Dict[str, Any]) -> pd.DataFrame:
    if not resp or "candles" not in resp:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"]).set_index(
            pd.Index([], name="datetime")
        )
    df = pd.DataFrame(resp["candles"], columns=["datetime", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["datetime"], unit="s", utc=True).dt.tz_convert(StrategyConfig.TIMEZONE).dt.tz_localize(None)
    df = df.set_index("datetime").astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
    return df

def history(fyers: fyersModel.FyersModel, symbol: str, res: int, start: dt, end: dt) -> pd.DataFrame:
    payload = {
        "symbol": symbol, "resolution": str(res), "date_format": "1",
        "range_from": start.strftime("%Y-%m-%d"), "range_to": end.strftime("%Y-%m-%d"),
        "cont_flag": "0",
    }
    r = fyers.history(data=payload)
    return candles_df(r)

# ============================== OPTIONCHAIN RESOLVER ==========================
def resolve_option_symbol(fyers: fyersModel.FyersModel, is_ce: bool, spot_ltp: float, logger: logging.Logger) -> Tuple[str, Optional[str]]:
    """
    Queries FYERS option chain for NIFTY and returns (symbol, 'YYYY-MM-DD' expiry)
    for nearest 50-strike of earliest expiry for the requested type (CE/PE).
    """
    try:
        chain = []
        for root in ("NSE:NIFTY50-INDEX", "NSE:NIFTY50", "NSE:NIFTY"):
            try:
                resp = fyers.optionchain(data={"symbol": root}) or {}
                data = (resp.get("data") or {}).get("optionsChain") or []
                if data:
                    chain = data
                    break
            except Exception as e:
                logger.warning(f"Optionchain root {root} failed: {e}")
        if not chain:
            raise RuntimeError("Optionchain response empty for NIFTY roots.")

        target = StrategyConfig().round_to_nearest_50(spot_ltp)
        opt_type = "CE" if is_ce else "PE"
        filt = [row for row in chain if str(row.get("option_type", "")).upper() == opt_type]
        if not filt:
            raise RuntimeError(f"Optionchain has no rows for type {opt_type}")

        def expiry_key(row):
            exp = row.get("expiry")
            try:
                return dt.strptime(exp, "%Y-%m-%d")
            except Exception:
                return dt.max

        expiries = [r.get("expiry") for r in filt if r.get("expiry")]
        if expiries:
            earliest = min(filt, key=expiry_key).get("expiry")
            filt = [r for r in filt if r.get("expiry") == earliest]
            expiry_pick = earliest
        else:
            expiry_pick = None

        def strike_key(row):
            try:
                sp = row.get("strike_price", row.get("strikePrice"))
                return abs(float(sp) - target)
            except Exception:
                return 1e12

        best = min(filt, key=strike_key)
        symbol = best.get("symbol") or best.get("tradingsymbol") or best.get("tsym")
        if not symbol:
            raise RuntimeError("Optionchain did not provide a symbol.")
        return symbol, expiry_pick
    except Exception as e:
        logger.error(f"Error resolving option symbol: {e}")
        return None, None

def earliest_expiry_string(fyers: fyersModel.FyersModel, logger: logging.Logger) -> Optional[str]:
    """Best-effort earliest expiry from NIFTY INDEX root (preview resolver tries all anyway)."""
    try:
        resp = fyers.optionchain(data={"symbol": "NSE:NIFTY50-INDEX"}) or {}
        chain = (resp.get("data") or {}).get("optionsChain") or []
        dates = [row.get("expiry") for row in chain if row.get("expiry")]
        return sorted(set(dates))[0] if dates else None
    except Exception as e:
        logger.error(f"Could not get earliest expiry string: {e}")
        return None

# ============================== STATE =========================================
class TradingState:
    def __init__(self):
        self.fmflag = 0
        self.emadata = pd.DataFrame()
        self.entry = 0.0
        self.stoploss = 0.0
        self.target = 0.0
        self.side = None
        self.opt_symbol = None
        self.qty = 0
        self.spos = 0
        self.sflag = 0
        self.pnl_cum = 0.0
        self.tick_count = 0
        self.last_preview_minute = None

    def reset_trade(self):
        self.entry = 0.0
        self.stoploss = 0.0
        self.target = 0.0
        self.side = None
        self.opt_symbol = None
        self.qty = 0
        self.spos = 0

# ============================== LIVE STATUS / NOTIFS ==========================
# ============================== FYERS SERVICE =================================
class FyersService:
    def __init__(self, fyers: fyersModel.FyersModel, logger: logging.Logger):
        self.fyers = fyers
        self.logger = logger

    def place_market_buy(self, symbol: str, qty: int) -> dict:
        data = {
            "symbol": symbol, "qty": qty, "type": 2,
            "side": 1, "productType": "INTRADAY",
            "limitPrice": 0, "stopPrice": 0,
            "validity": "DAY", "disclosedQty": 0, "offlineOrder": False,
        }
        self.logger.info(f"Placing BUY order: {data}")
        return self.fyers.place_order(data=data)

    def place_market_sell(self, symbol: str, qty: int) -> dict:
        data = {
            "symbol": symbol, "qty": qty, "type": 2,
            "side": -1, "productType": "INTRADAY",
            "limitPrice": 0, "stopPrice": 0,
            "validity": "DAY", "disclosedQty": 0, "offlineOrder": False,
        }
        self.logger.info(f"Placing SELL order: {data}")
        return self.fyers.place_order(data=data)

# ============================== TICK HANDLER ==================================
class Strategy:
    def __init__(self, fyers_service: FyersService, config: StrategyConfig, state: TradingState, logger: logging.Logger):
        self.fyers_service = fyers_service
        self.config = config
        self.state = state
        self.logger = logger

    def on_message(self, msg: Dict[str, Any]):
        if "ltp" not in msg:
            self.logger.debug(f"WebSocket message: {msg}")
            return

        self.state.tick_count += 1
        if self.state.tick_count <= self.config.HEARTBEAT_LIMIT:
            self.logger.debug(f"Tick #{self.state.tick_count}: {msg.get('symbol')} LTP={msg.get('ltp')}")

        try:
            ltp = float(msg.get("ltp"))
        except Exception:
            return

        now_local = dt.now(self.config.IST).replace(tzinfo=None)

        if not (self.config.MARKET_START <= now_local.time() <= self.config.MARKET_END):
            return

        self.refresh_ema_data(now_local)
        if self.state.emadata.empty:
            return

        if now_local.second == 0 or (now_local.minute % self.config.TIMEFRAME_MIN == 0 and now_local.second <= 2):
            self.print_live_status(ltp)

        self.check_entry_conditions(ltp, now_local)
        self.check_exit_conditions(ltp)

    def refresh_ema_data(self, now_local: dt):
        cmin, csec = now_local.minute, now_local.second
        if (cmin % self.config.TIMEFRAME_MIN == 0) and (csec >= 1) and (self.state.fmflag == 0):
            start = (now_local - timedelta(days=5)).replace(hour=9, minute=15, second=0, microsecond=0)
            df_tf = history(self.fyers_service.fyers, self.config.UNDERLYING_INDEX, self.config.TIMEFRAME_MIN, start, now_local)
            if not df_tf.empty:
                df_tf["ema"] = compute_ema(df_tf["close"], self.config.EMA_SPAN)
                self.state.emadata = df_tf
                self.logger.info(f"Data: {self.config.TIMEFRAME_MIN}m EMA @ {df_tf.index[-1]}")
            self.state.fmflag = 1
            if self.state.spos == 0:
                self.state.sflag = 0
        if (cmin % self.config.TIMEFRAME_MIN != 0) and (self.state.fmflag == 1):
            self.state.fmflag = 0

    def has_prev_row(self) -> bool:
        try:
            _ = self.state.emadata.iloc[self.config.ROW_LOOKBACK]
            return True
        except Exception:
            return False

    def print_live_status(self, ltp: float):
        self.logger.info(f"Status: [TF={self.config.TIMEFRAME_MIN}m][R:R=1:{self.config.r_sell:.2f}] LTP={ltp:.2f}")
        try:
            c = self.state.emadata.iloc[self.config.ROW_LOOKBACK]
            e = float(c["ema"])
            fully_above = (c["open"] > e and c["high"] > e and c["low"] > e and c["close"] > e)
            rng = float(c["high"] - c["low"])
            self.logger.info(f"Prev: O={c['open']:.2f} H={c['high']:.2f} L={c['low']:.2f} C={c['close']:.2f} | EMA5={e:.2f} | fully_above={fully_above} | range={rng:.2f}")
            sell_trig = float(c["low"]) - self.config.ENTRY_BUFFER
            self.logger.info(f"Triggers: SELL< {sell_trig:.2f}")
        except Exception:
            pass

    def check_entry_conditions(self, ltp: float, now_local: dt):
        if self.state.spos == 0 and self.state.sflag == 0 and self.has_prev_row():
            c = self.state.emadata.iloc[self.config.ROW_LOOKBACK]
            ema5 = c["ema"]
            if (c["open"] > ema5 and c["high"] > ema5 and c["low"] > ema5 and c["close"] > ema5 and ltp < (float(c["low"]) - self.config.ENTRY_BUFFER)):
                try:
                    ce_symbol, exp_yyyy_mm_dd = resolve_option_symbol(self.fyers_service.fyers, is_ce=True, spot_ltp=ltp, logger=self.logger)
                    if not ce_symbol:
                        self.logger.warning("Could not resolve option symbol, skipping entry check.")
                        return

                    lots = self.config.nifty_lot_size_for_date(
                        dt.strptime(exp_yyyy_mm_dd, "%Y-%m-%d") if exp_yyyy_mm_dd else now_local
                    )
                    resp = self.fyers_service.place_market_sell(ce_symbol, qty=lots)
                    if resp.get("s") == "ok":
                        self.state.spos = self.state.sflag = 1
                        self.state.opt_symbol = ce_symbol
                        self.state.qty = lots
                        self.state.side = "sell_ce"
                        self.state.entry = ltp
                        self.state.stoploss = float(c["high"]) + self.config.SL_BUFFER
                        rng = float(c["high"] - c["low"]) if (c["high"] - c["low"]) > 0 else max(1.0, abs(c["close"]) * 0.001)
                        self.state.target = self.state.entry - (rng * self.config.r_sell)
                        self.logger.info(f"SELL: [TF={self.config.TIMEFRAME_MIN}m][R:R=1:{self.config.r_sell:.2f}] LIVE ENTRY OK | CE={ce_symbol} | LTP={self.state.entry:.2f} SL={self.state.stoploss:.2f} TGT={self.state.target:.2f} | Lot={lots}")
                        self.logger.info(f"Entry={self.state.entry:.2f}  Target={self.state.target:.2f}  SL={self.state.stoploss:.2f}")
                    else:
                        self.logger.error(f"SELL CE failed: {resp}")
                except Exception as e:
                    self.logger.error(f"SELL entry error: {e}", exc_info=True)

    def check_exit_conditions(self, ltp: float):
        if self.state.spos == 1 and self.state.side == "sell_ce" and self.state.opt_symbol:
            exit_reason = None
            pnl = 0.0
            if self.state.stoploss > 0 and ltp > self.state.stoploss:
                exit_reason = "STOPLOSS"
                pnl = self.state.entry - self.state.stoploss
            elif self.state.target < self.state.entry and ltp <= self.state.target:
                exit_reason = "TARGET"
                pnl = self.state.entry - self.state.target

            if exit_reason:
                try:
                    resp = self.fyers_service.place_market_buy(self.state.opt_symbol, qty=self.state.qty)
                    if resp.get("s") == "ok":
                        self.state.pnl_cum += pnl
                        self.logger.info(f"SELL {exit_reason} HIT: PnL≈{pnl:.2f} (spot-based) | Cum: {self.state.pnl_cum:.2f} | resp={resp}")
                        self.state.reset_trade()
                    else:
                        self.logger.critical(f"EXIT FAILED for {self.state.opt_symbol}. Reason: {exit_reason}. Response: {resp}. POSITION IS STILL OPEN.")
                except Exception as e:
                    self.logger.critical(f"EXCEPTION ON EXIT for {self.state.opt_symbol}. Reason: {exit_reason}. Error: {e}. POSITION IS STILL OPEN.", exc_info=True)

# ============================== MAIN ==========================================
def on_open(fyers_socket, underlying_index):
    fyers_socket.subscribe(symbols=[underlying_index], data_type="SymbolUpdate")
    fyers_socket.keep_running()

def main():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.setFormatter(ColoredFormatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)

    login = FyersLogin(logger)
    access_token = login.get_access_token()

    fyers = fyersModel.FyersModel(client_id=login.app_id, is_async=False, token=access_token, log_path="")
    fyers_service = FyersService(fyers, logger)
    config = StrategyConfig()
    state = TradingState()
    strategy = Strategy(fyers_service, config, state, logger)

    now_ist = dt.now(config.IST).replace(tzinfo=None)
    try:
        start_tf = (now_ist - timedelta(days=5)).replace(hour=9, minute=15, second=0, microsecond=0)
        df_tf = history(fyers, config.UNDERLYING_INDEX, config.TIMEFRAME_MIN, start_tf, now_ist)
        if not df_tf.empty:
            df_tf["ema"] = compute_ema(df_tf["close"], config.EMA_SPAN)
            state.emadata = df_tf
            logger.info(f"Warmup {config.TIMEFRAME_MIN}m EMA ready @ {df_tf.index[-1]}")
    except Exception as e:
        logger.error(f"Warmup failed: {e}")

    logger.info(f"Config: TF={config.TIMEFRAME_MIN}m | R:R=1:{config.r_sell:.2f} | EntryBuf={config.ENTRY_BUFFER:g} | SLBuf={config.SL_BUFFER:g}")

    try:
        earliest = earliest_expiry_string(fyers, logger)
        if earliest:
            logger.info(f"Earliest expiry (bot will use): {earliest}")
        else:
            logger.warning("Could not determine earliest expiry.")
    except Exception as e:
        logger.error(f"Expiry check failed: {e}")

    fyers_socket = data_ws.FyersDataSocket(
        access_token=f"{login.app_id}:{access_token}",
        log_path="",
        litemode=True,
        write_to_file=False,
        reconnect=True,
        on_connect=lambda: on_open(fyers_socket, config.UNDERLYING_INDEX),
        on_close=lambda msg: logger.info(f"WebSocket closed: {msg}"),
        on_error=lambda msg: logger.error(f"WebSocket error: {msg}"),
        on_message=strategy.on_message,
    )

    logger.info("Connecting WebSocket…")
    fyers_socket.connect()

if __name__ == "__main__":
    main()
