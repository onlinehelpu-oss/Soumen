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

import os, json, time, datetime, hashlib, re
from urllib.parse import urlparse, parse_qs, quote
from typing import Dict, Any, Optional, Tuple

import requests
import pandas as pd
import numpy as np
import pytz
from datetime import datetime as dt, timedelta

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
    app_id = creds["api_key"]; secret_id = creds["api_secret"]; redirect_uri = creds["redirect_url"]
    if os.path.exists(TOKENS_DIR) and os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, "r") as f:
            tok = json.load(f)
        if isinstance(tok, str) and tok:
            print("[auth] Using saved access token:", TOKEN_PATH)
            return {"app_id": app_id, "secret_id": secret_id, "redirect_uri": redirect_uri, "access_token": tok}
    url = build_auth_url(app_id, redirect_uri)
    print("\nLogin URL (open in browser, complete login):\n", url)
    user_val = input("\nPaste FULL redirect URL or just the 'code' value here: ").strip()
    code = extract_code(user_val)
    token_resp = validate_authcode(app_id, secret_id, code)
    access_token = token_resp.get("access_token")
    if not access_token:
        raise RuntimeError(f"Unexpected token response: {token_resp}")
    os.makedirs(TOKENS_DIR, exist_ok=True)
    with open(TOKEN_PATH, "w") as f: json.dump(access_token, f)
    print(f"[auth] Token saved to {TOKEN_PATH}")
    return {"app_id": app_id, "secret_id": secret_id, "redirect_uri": redirect_uri, "access_token": access_token}

# ============================== STRATEGY CONFIG ===============================
TIMEZONE = "Asia/Kolkata"
IST = pytz.timezone(TIMEZONE)

UNDERLYING_INDEX = "NSE:NIFTY50-INDEX"  # NIFTY spot LTP feed & option chain base
EMA_SPAN = 5
ROW_LOOKBACK = -2

# -------- Customizable timeframe & R:R (supports 1/2/3/5/10/15/30/60) --------
TIMEFRAME_MIN = 3        # <-- set to 1 / 2 / 3 / 5 / 10 / 15 / 30 / 60
R_MULTIPLIER = 2.0       # Risk:Reward multiple (target = entry - R_MULTIPLIER * range)
DEFAULT_QTY   = 1
EPS = 1e-6
# -----------------------------------------------------------------------------

R_SELL = R_MULTIPLIER

# Buffers
ENTRY_BUFFER = 3.0       # entry trigger = prev_low - 3.0 (spot)
SL_BUFFER    = 2.0       # stoploss     = prev_high + 2.0 (spot)

# Market-hours gate
MARKET_START = dt.strptime("09:15", "%H:%M").time()
MARKET_END   = dt.strptime("15:20", "%H:%M").time()

# Tick heartbeat & preview cadence
TICK_COUNT = 0
HEARTBEAT_LIMIT = 5
LAST_PREVIEW_MINUTE = None

# ============================== LOT SIZE & HELPERS ============================
def nifty_lot_size_for_date(d: dt) -> int:
    cutoff = dt(2025, 12, 30, 15, 30)  # same rule you used earlier
    return 75 if d <= cutoff else 65

def round_to_nearest_50(x: float) -> int:
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
    df["datetime"] = pd.to_datetime(df["datetime"], unit="s", utc=True).dt.tz_convert(TIMEZONE).dt.tz_localize(None)
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
def parse_expiry_from_symbol(symbol: str) -> Optional[dt]:
    """
    Parses the expiry date from an NSE NIFTY option symbol.
    Handles two common formats:
    1. Monthly: NIFTYYYMDD... (e.g., NIFTY25N18...)
    2. Weekly:  NIFTYDDMMMYY... (e.g., NIFTY14NOV25...)
    """
    # Pattern 1: Monthly options (NIFTYYYMDD...)
    match_monthly = re.search(r'NIFTY(\d{2})([1-9OND])(\d{2})', symbol)
    if match_monthly:
        year, month_char, day = match_monthly.groups()
        month_map = {'1': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, 'O': 10, 'N': 11, 'D': 12}
        try:
            return dt(2000 + int(year), month_map[month_char], int(day))
        except (ValueError, KeyError):
            pass  # Invalid date components, fall through

    # Pattern 2: Weekly options (NIFTYDDMMMYY...)
    match_weekly = re.search(r'NIFTY(\d{2})([A-Z]{3})(\d{2})', symbol)
    if match_weekly:
        day, month_str, year = match_weekly.groups()
        try:
            return dt.strptime(f"{day}{month_str}{year}", "%d%b%y")
        except ValueError:
            pass  # Not a valid date format, fall through

    return None

def resolve_option_symbol(fyers: fyersModel.FyersModel, is_ce: bool, spot_ltp: float) -> Tuple[str, Optional[str]]:
    """
    Queries FYERS option chain for NIFTY and returns (symbol, 'YYYY-MM-DD' expiry)
    for nearest 50-strike of earliest expiry for the requested type (CE/PE).
    """
    chain = []
    for root in ("NSE:NIFTY50-INDEX", "NSE:NIFTY50", "NSE:NIFTY"):
        try:
            resp = fyers.optionchain(data={"symbol": root}) or {}
            data = (resp.get("data") or {}).get("optionChain", [])
            if data:
                chain = data
                break
        except Exception as e:
            print(f"[optionchain] root {root} failed: {e}")
    if not chain:
        raise RuntimeError("Optionchain response empty for NIFTY roots.")

    target = round_to_nearest_50(spot_ltp)
    opt_type = "CE" if is_ce else "PE"
    filt = [row for row in chain if str(row.get("option_type", "")).upper() == opt_type]
    if not filt:
        raise RuntimeError(f"Optionchain has no rows for type {opt_type}")

    decorated = []
    for row in filt:
        symbol = row.get("symbol")
        if symbol:
            expiry = parse_expiry_from_symbol(symbol)
            if expiry:
                decorated.append({**row, "_expiry_dt": expiry})

    if not decorated:
        raise RuntimeError("Could not parse expiry from any option symbol.")

    earliest_expiry = min(decorated, key=lambda x: x["_expiry_dt"])["_expiry_dt"]
    filt = [row for row in decorated if row["_expiry_dt"] == earliest_expiry]
    expiry_pick = earliest_expiry.strftime("%Y-%m-%d")

    def strike_key(row):
        try:
            sp = row.get("strike_price")
            return abs(float(sp) - target)
        except Exception:
            return 1e12

    best = min(filt, key=strike_key)
    symbol = best.get("symbol")
    if not symbol:
        raise RuntimeError("Optionchain did not provide a symbol.")
    return symbol, expiry_pick

def earliest_expiry_string(fyers: fyersModel.FyersModel) -> Optional[str]:
    """Best-effort earliest expiry from NIFTY INDEX root."""
    try:
        resp = fyers.optionchain(data={"symbol": "NSE:NIFTY50-INDEX"}) or {}
        chain = (resp.get("data") or {}).get("optionChain", [])
        expiries = {parse_expiry_from_symbol(r["symbol"]) for r in chain if r.get("symbol")}
        valid_expiries = sorted([e for e in expiries if e])
        return valid_expiries[0].strftime("%Y-%m-%d") if valid_expiries else None
    except Exception:
        return None

# ============================== STATE =========================================
class State:
    def __init__(self):
        self
        self.fmflag = 0                 # TF refresh guard
        self.emadata = pd.DataFrame()   # holds candles for TIMEFRAME_MIN
        # trade mgmt
        self.entry = 0.0
        self.stoploss = 0.0
        self.target = 0.0
        self.side = None                # "sell_ce"
        self.opt_symbol = None
        self.qty = 0
        self.spos = 0                   # active position flag
        self.sflag = 0                  # rearm guard
        self.pnl_cum = 0.0

    def reset_trade(self):
        self.entry = self.stoploss = self.target = 0.0
        self.side = None; self.opt_symbol = None; self.qty = 0
        self.spos = 0

STATE = State()

# ============================== LIVE STATUS / NOTIFS ==========================
def print_live_status():
    """Single TF status line only."""
    try:
        c  = STATE.emadata.iloc[ROW_LOOKBACK]
        e  = float(c["ema"])
        fully_above = (c["open"]>e and c["high"]>e and c["low"]>e and c["close"]>e)
        rng = float(c["high"] - c["low"])
        print(f"[TF={TIMEFRAME_MIN}m] prev: O={c['open']:.2f} H={c['high']:.2f} L={c['low']:.2f} C={c['close']:.2f} | EMA5={e:.2f} | fully_above={fully_above} | range={rng:.2f}")
    except Exception:
        pass

# ============================== STRATEGY CORE =================================
def refresh_ema_data(fyers: fyersModel.FyersModel, now_local: dt):
    cmin, csec = now_local.minute, now_local.second
    # TF boundary
    if (cmin % TIMEFRAME_MIN == 0) and (csec >= 1) and (STATE.fmflag == 0):
        start = (now_local - timedelta(days=5)).replace(hour=9, minute=15, second=0, microsecond=0)
        df_tf = history(fyers, UNDERLYING_INDEX, TIMEFRAME_MIN, start, now_local)
        if not df_tf.empty:
            df_tf["ema"] = compute_ema(df_tf["close"], EMA_SPAN)
            STATE.emadata = df_tf
            print(f"[data] {TIMEFRAME_MIN}m EMA @", df_tf.index[-1])
        STATE.fmflag = 1
        if STATE.spos == 0: STATE.sflag = 0
    if (cmin % TIMEFRAME_MIN != 0) and (STATE.fmflag == 1):
        STATE.fmflag = 0

def has_prev_row(df: pd.DataFrame) -> bool:
    try:
        _ = df.iloc[ROW_LOOKBACK]
        return True
    except Exception:
        return False

# ============================== ORDER HELPERS =================================
def place_market_buy(symbol: str, qty: int) -> dict:
    data = {
        "symbol": symbol, "qty": qty, "type": 2,  # Market
        "side": 1,                                 # BUY
        "productType": "INTRADAY",
        "limitPrice": 0, "stopPrice": 0,
        "validity": "DAY", "disclosedQty": 0, "offlineOrder": False,
    }
    print("[order] BUY:", data)
    return FYERS.place_order(data=data)

def place_market_sell(symbol: str, qty: int) -> dict:
    data = {
        "symbol": symbol, "qty": qty, "type": 2,  # Market
        "side": -1,                                # SELL
        "productType": "INTRADAY",
        "limitPrice": 0, "stopPrice": 0,
        "validity": "DAY", "disclosedQty": 0, "offlineOrder": False,
    }
    print("[order] SELL:", data)
    return FYERS.place_order(data=data)

# ============================== TICK HANDLER ==================================
def on_message(msg: Dict[str, Any]):
    global TICK_COUNT, LAST_PREVIEW_MINUTE

    if "ltp" not in msg:
        print("[ws] ", msg); return

    TICK_COUNT += 1
    if TICK_COUNT <= HEARTBEAT_LIMIT:
        print(f"[tick#{TICK_COUNT}] {msg.get('symbol')} LTP={msg.get('ltp')}")

    try:
        ltp = float(msg.get("ltp"))
    except Exception:
        return

    now_local = dt.now(IST).replace(tzinfo=None)

    # market-hours gate
    if not (MARKET_START <= now_local.time() <= MARKET_END):
        return

    # refresh TF data
    refresh_ema_data(FYERS, now_local)
    if STATE.emadata.empty:
        return

    # status + triggers + preview (only TF)
    if now_local.second == 0 or (now_local.minute % TIMEFRAME_MIN == 0 and now_local.second <= 2):
        print(f"[now][TF={TIMEFRAME_MIN}m][R:R=1:{R_SELL:.2f}] LTP={ltp:.2f}")
        print_live_status()
        try:
            c  = STATE.emadata.iloc[ROW_LOOKBACK]
            sell_trig = float(c["low"]) - ENTRY_BUFFER
            print(f"[triggers] SELL< {sell_trig:.2f}")
        except Exception:
            pass
        if LAST_PREVIEW_MINUTE != now_local.minute:
            LAST_PREVIEW_MINUTE = now_local.minute
            try:
                exp = earliest_expiry_string(FYERS)
                if exp:
                    print("[expiry-check] Earliest expiry (bot will use):", exp)
                ce_sym, _ = resolve_option_symbol(FYERS, is_ce=True, spot_ltp=ltp)
                print(f"[preview] CE≈ {ce_sym}")
            except Exception as e:
                print("[preview] failed:", e)

    # ===== SELL logic (TF=TIMEFRAME_MIN): SELL CE on strict breakdown =====
    if STATE.spos == 0 and STATE.sflag == 0 and has_prev_row(STATE.emadata):
        c = STATE.emadata.iloc[ROW_LOOKBACK]; ema5 = c["ema"]
        if (c["open"] > ema5 and c["high"] > ema5 and c["low"] > ema5 and c["close"] > ema5
                and ltp < (float(c["low"]) - ENTRY_BUFFER)):
            try:
                ce_symbol, exp_yyyy_mm_dd = resolve_option_symbol(FYERS, is_ce=True, spot_ltp=ltp)
                lots = nifty_lot_size_for_date(
                    dt.strptime(exp_yyyy_mm_dd, "%Y-%m-%d") if exp_yyyy_mm_dd else now_local
                )
                resp = place_market_sell(ce_symbol, qty=lots)  # ENTRY = SELL CE
                if resp.get("s") == "ok":
                    STATE.spos = STATE.sflag = 1
                    STATE.opt_symbol = ce_symbol
                    STATE.qty = lots
                    STATE.side = "sell_ce"
                    STATE.entry = ltp
                    # SL with buffer on spot
                    STATE.stoploss = float(c["high"]) + SL_BUFFER
                    # risk = previous TF candle range (spot)
                    rng = float(c["high"] - c["low"]) if (c["high"] - c["low"]) > 0 else max(1.0, abs(c["close"]) * 0.001)
                    # target via custom R multiple (bearish target)
                    STATE.target = STATE.entry - (rng * R_SELL)
                    print(f"[SELL][TF={TIMEFRAME_MIN}m][R:R=1:{R_SELL:.2f}] LIVE ENTRY OK | CE={ce_symbol} | LTP={STATE.entry:.2f} SL={STATE.stoploss:.2f} TGT={STATE.target:.2f} | Lot={lots}")
                    print(f"Entry={STATE.entry:.2f}  Target={STATE.target:.2f}  SL={STATE.stoploss:.2f}")
                else:
                    print("[order] SELL CE failed:", resp)
            except Exception as e:
                print("[SELL] entry error:", e)

    # ===== EXIT management (CE BUY to close short) — spot-based =====
    if STATE.spos == 1 and STATE.side == "sell_ce" and STATE.opt_symbol:
        if STATE.stoploss > 0 and ltp > STATE.stoploss:
            try:
                resp = place_market_buy(STATE.opt_symbol, qty=STATE.qty)  # EXIT = BUY CE (SL)
                pnl = (STATE.entry - STATE.stoploss)
                STATE.pnl_cum += pnl
                print(f"[SELL] STOP OUT | PnL≈{pnl:.2f} (spot-based) | Cum: {STATE.pnl_cum:.2f} | resp={resp}")
            finally:
                STATE.reset_trade()
        elif STATE.target < STATE.entry and ltp <= STATE.target:
            try:
                resp = place_market_buy(STATE.opt_symbol, qty=STATE.qty)  # EXIT = BUY CE (Target)
                pnl = (STATE.entry - STATE.target)
                STATE.pnl_cum += pnl
                print(f"[SELL] TARGET HIT | PnL≈{pnl:.2f} (spot-based) | Cum: {STATE.pnl_cum:.2f} | resp={resp}")
            finally:
                STATE.reset_trade()

# ============================== WS EVENTS =====================================
def on_error(msg): print("[ws:error]", msg)
def on_close(msg): print("[ws:close]", msg)

def on_open():
    fyers_socket.subscribe(symbols=[UNDERLYING_INDEX], data_type="SymbolUpdate")
    fyers_socket.keep_running()

# ============================== BOOT ==========================================
if __name__ == "__main__":
    # token
    auth = get_access_token()
    APP_ID = auth["app_id"]; ACCESS_TOKEN = auth["access_token"]

    # REST
    FYERS = fyersModel.FyersModel(client_id=APP_ID, is_async=False, token=ACCESS_TOKEN, log_path="")

    # warmup TF candles only
    now_ist = dt.now(IST).replace(tzinfo=None)
    try:
        start_tf = (now_ist - timedelta(days=5)).replace(hour=9, minute=15, second=0, microsecond=0)
        df_tf  = history(FYERS, UNDERLYING_INDEX, TIMEFRAME_MIN, start_tf, now_ist)
        if not df_tf.empty:
            df_tf["ema"] = compute_ema(df_tf["close"], EMA_SPAN); STATE.emadata = df_tf
            print(f"[warmup] {TIMEFRAME_MIN}m EMA ready @", df_tf.index[-1])
    except Exception as e:
        print("[warmup] failed:", e)

    # one-time config notification (TF & R:R)
    print(f"[config] TF={TIMEFRAME_MIN}m | R:R=1:{R_SELL:.2f} | EntryBuf={ENTRY_BUFFER:g} | SLBuf={SL_BUFFER:g}")

    # best-effort earliest expiry (preview resolver still tries all NIFTY roots)
    try:
        earliest = earliest_expiry_string(FYERS)
        if earliest:
            print("[expiry-check] Earliest expiry (bot will use):", earliest)
        else:
            print("[expiry-check] Could not determine earliest expiry.")
    except Exception as e:
        print("[expiry-check] failed:", e)

    # WS
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
