#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GAMMA CONFLUENCE LIVE OPTIONS BOT v3 (MERGED & ROBUST)
------------------------------------------------------
Features:
- Robust Auth & Token Management (Code -1)
- Advanced Gamma Blast Logic with ROC (Code -1)
- Reliable Option Chain & Expiry Parsing (Code -1)
- Extended Candle Patterns: Engulfing, Hammer, Shooting Star (Code -3)
- Boosters: Gamma Blast, VWAP, EMA, SR, Gamma Wall (Code -3)
- Safe Import / Mock Mode (Code -3)
- Dynamic Lot Sizes (Code -1)
"""

import os
import sys
import json
import time
import math
import datetime
import random
import hashlib
import requests
import csv
from urllib.parse import urlparse, parse_qs, quote

# =====================================================
# SAFE IMPORT / MOCK CLASSES (Code -3)
# =====================================================
try:
    from fyers_apiv3 import fyersModel
    from fyers_apiv3.FyersWebsocket import data_ws
    FYERS_AVAILABLE = True
except ModuleNotFoundError:
    FYERS_AVAILABLE = False
    print("⚠️  fyers_apiv3 not found. Using MOCK mode.")

    SIM_STATE = {"price": 19500.0}

    class MockFyers:
        def place_order(self, data):
            print(f"[SIM ORDER] {data}")
            return {"s": "ok", "id": "12345"}

        def optionchain(self, data):
            price = SIM_STATE['price']
            atm = round(price / 50) * 50
            chain = []
            # Minimal mock chain structure compatible with parse_chain
            for i in range(-10, 11):
                strike = atm + i * 50
                # Random OI for gamma testing
                ce_oich = random.randint(-500, 1500)
                pe_oich = random.randint(-500, 1500)
                chain.append({
                    "strike_price": strike, "option_type": "CE",
                    "oi": random.randint(1000, 5000), "oich": ce_oich,
                    "ltp": 100, "symbol": f"NSE:NIFTY{strike}CE"
                })
                chain.append({
                    "strike_price": strike, "option_type": "PE",
                    "oi": random.randint(1000, 5000), "oich": pe_oich,
                    "ltp": 100, "symbol": f"NSE:NIFTY{strike}PE"
                })
            return {"data": {"optionsChain": chain}}

        def quotes(self, data):
            # Mock quote
            return {"s": "ok", "d": [{"n": "NSE:NIFTY50-INDEX", "v": {"lp": SIM_STATE['price']}}]}

    class MockSocket:
        def __init__(self, on_message, **kwargs):
            self.on_message = on_message

        def subscribe(self, symbols, data_type):
            pass

        def keep_running(self):
            print("🧪 SIMULATION MODE ACTIVE")
            while True:
                move = random.uniform(-10, 10)
                SIM_STATE['price'] += move
                # Code -1 loops expect throttled OC, so we mimic ticks
                self.on_message({"data": [{"ltp": SIM_STATE['price']}]})
                time.sleep(1)

    if not FYERS_AVAILABLE:
        fyersModel = type("fyersModel", (), {"FyersModel": lambda **k: MockFyers()})
        data_ws = type("data_ws", (), {"FyersDataSocket": lambda **k: MockSocket(k['on_message'], **k)})


# ================= CONFIGURATION =================
CONFIG_FILE = "fyers_login_details.json"
TOKENS_DIR = "AccessToken"
TODAY = str(datetime.date.today())
TOKEN_PATH = os.path.join(TOKENS_DIR, f"{TODAY}.json")

UNDERLYING_SYMBOL = "NSE:NIFTY50-INDEX"

# Trading Config (Code -3)
SL_POINTS = 40
TRAIL_TRIGGER = 20
TRAIL_STEP = 10
PRODUCT_TYPE = "INTRADAY"

# Gamma Blast Params (Code -1)
GB_STRIKES_AROUND_ATM = 8
GB_OICH_MIN_ABS_FLOOR = 100.0
GB_OICH_SCALE = 0.05
GB_ROC_BP_THRESHOLD = 5.0
ROC_WINDOW_SEC = 70

# Expiry Selection
SAME_DAY_CUTOFF = (15, 25)
MIN_T_SECONDS = 5 * 60
MIN_T_YEARS = MIN_T_SECONDS / (365.0 * 24 * 3600)

# API Throttle
OC_INTERVAL = 30

# HTTP Retry Params
HTTP_MAX_ATTEMPTS = 3
HTTP_BACKOFF_BASE = 1.2
HTTP_JITTER = (0.1, 0.6)


# ================= GLOBAL STATE =================
SYMBOL_MASTER_MAP = {}
PRICE_HISTORY = [] # List of (ts, ltp) for Code -3 / Code -1 logic
VWAP_DATA = {"pv_sum": 0.0, "vol_sum": 0.0, "last_reset": datetime.date.today()}
candles = []
CANDLE_SEC = 60

# Trade State
position = None
entry_price = None
sl_price = None
traded_symbol = None
traded_qty = 0

last_oc_fetch_time = 0
current_chain_analysis = {}


# ================= UTILS & MATH =================
def try_float(*vals):
    for v in vals:
        if v is None: continue
        try: return float(v)
        except: continue
    return None

def safe_get(d, *keys):
    for k in keys:
        if isinstance(d, dict) and k in d: return d[k]
    return None

def call_with_retries(func, payload, max_attempts=HTTP_MAX_ATTEMPTS):
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = func(data=payload) if "data" in func.__code__.co_varnames else func(payload)
        except TypeError:
            resp = func(payload)
        except Exception as e:
            err = {"status": "error", "error_code": None, "message": str(e)}
            if attempt >= max_attempts: return None, err
            time.sleep(1)
            continue

        if not isinstance(resp, dict):
            if attempt >= max_attempts: return None, {"message": "Non-dict response"}
            time.sleep(1)
            continue

        s = resp.get("s")
        code = resp.get("code")
        if s == "ok": return resp, None
        if attempt >= max_attempts:
            return None, {"status": "error", "error_code": code, "message": resp.get("message")}
        time.sleep(1)


# ================= AUTHENTICATION (Code -1 ROBUST) =================
def load_or_prompt_creds():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f: return json.load(f)
    print("---- Enter Fyers Credentials ----")
    creds = {
        "api_key": input("App ID: ").strip(),
        "api_secret": input("Secret ID: ").strip(),
        "redirect_url": input("Redirect URL: ").strip()
    }
    with open(CONFIG_FILE, "w") as f: json.dump(creds, f, indent=2)
    return creds

def build_auth_url(app_id, redirect_uri):
    base = "https://api-t1.fyers.in/api/v3/generate-authcode"
    params = f"client_id={quote(app_id)}&redirect_uri={quote(redirect_uri, safe='')}&response_type=code&state=s&scope=openid&nonce={int(time.time())}"
    return f"{base}?{params}"

def extract_code(user_input):
    s = user_input.strip()
    if "code=" in s: return parse_qs(urlparse(s).query).get("code", [None])[0]
    return s

def validate_authcode(app_id, secret_id, auth_code):
    url = "https://api-t1.fyers.in/api/v3/validate-authcode"
    payload = {
        "grant_type": "authorization_code",
        "appIdHash": hashlib.sha256(f"{app_id}:{secret_id}".encode("utf-8")).hexdigest(),
        "code": auth_code,
    }
    r = requests.post(url, json=payload, headers={"Content-Type":"application/json"})
    r.raise_for_status()
    return r.json()

def get_access_token():
    creds = load_or_prompt_creds()
    app_id, secret = creds["api_key"], creds["api_secret"]
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH) as f:
            data = json.load(f)
            return data.get("access_token"), app_id
    print(f"Login URL: {build_auth_url(app_id, creds['redirect_url'])}")
    code = extract_code(input("Paste URL/Code: "))
    resp = validate_authcode(app_id, secret, code)
    os.makedirs(TOKENS_DIR, exist_ok=True)
    with open(TOKEN_PATH, "w") as f: json.dump(resp, f)
    return resp["access_token"], app_id

def _quotes_probe(fy):
    try:
        r = fy.quotes(data={"symbols": "NSE:NIFTY50-INDEX"})
        return (r.get("s") == "ok"), r
    except Exception as e:
        return False, str(e)

def ensure_valid_client():
    if not FYERS_AVAILABLE:
        return fyersModel.FyersModel(), "MOCK_APP", "MOCK_TOKEN"

    token, app_id = get_access_token()
    token_str = f"{app_id}:{token}"
    fy = fyersModel.FyersModel(client_id=app_id, token=token_str, log_path=os.getcwd())
    ok, _ = _quotes_probe(fy)
    if not ok:
        print("Token expired (-15). Re-authenticating...")
        if os.path.exists(TOKEN_PATH): os.remove(TOKEN_PATH)
        return ensure_valid_client()
    return fy, app_id, token_str


# ================= SYMBOL MASTER (Code -1) =================
def fetch_symbol_master():
    """Downloads Fyers NSE/BSE master CSVs to learn Lot Size and Strike Step."""
    print("Fetching Symbol Master CSVs...")
    urls = {
        "NSE": "https://public.fyers.in/sym_details/NSE_FO.csv",
        "BSE": "https://public.fyers.in/sym_details/BSE_FO.csv"
    }
    temp_data = {}
    for exch, url in urls.items():
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            lines = r.text.strip().split("\n")
            reader = csv.reader(lines)
            header = next(reader, None)
            if header and "Fytoken" in str(header[0]): pass
            else: reader = csv.reader(lines)
            for row in reader:
                if len(row) < 14: continue
                root = row[13].strip().upper()
                if not root: continue
                try: lot = int(row[3])
                except: continue
                if root not in temp_data: temp_data[root] = {"lot": lot}
                temp_data[root]["lot"] = lot
        except Exception as e:
            print(f"Warning: Failed to fetch/parse {exch} master: {e}")

    mapping = {
        "NSE:NIFTY50-INDEX": "NIFTY",
        "NSE:NIFTYBANK-INDEX": "BANKNIFTY",
        "NSE:FINNIFTY-INDEX": "FINNIFTY",
        "BSE:SENSEX-INDEX": "SENSEX",
    }
    defaults = {
        "NIFTY": {"step": 50, "lot": 25},
        "BANKNIFTY": {"step": 100, "lot": 15},
        "FINNIFTY": {"step": 50, "lot": 25},
        "SENSEX": {"step": 100, "lot": 10},
    }
    for sym_full, root in mapping.items():
        d = defaults.get(root, {"step": 50, "lot": 1})
        final_lot = d["lot"]
        final_step = d["step"]
        if root in temp_data:
            fetched_lot = temp_data[root]["lot"]
            if fetched_lot > 0: final_lot = fetched_lot
        SYMBOL_MASTER_MAP[sym_full] = {"lot_size": final_lot, "step": final_step}
    print("Symbol Master loaded.")

def get_lot_size(root):
    for k, v in SYMBOL_MASTER_MAP.items():
        if k in root: return v["lot_size"]
    return 25 # Default


# ================= PRICE HISTORY & INDICATORS (Code -3) =================
def update_price_history_and_get_roc(symbol, ltp):
    now = time.time()
    PRICE_HISTORY.append((now, float(ltp)))
    cutoff = now - 300
    while PRICE_HISTORY and PRICE_HISTORY[0][0] < cutoff: PRICE_HISTORY.pop(0)

    # Calc ROC (Code -1 logic)
    if len(PRICE_HISTORY) < 2: return 0.0
    target_ts = now - ROC_WINDOW_SEC
    p0 = PRICE_HISTORY[0][1]
    for t, p in PRICE_HISTORY:
        if t >= target_ts:
            p0 = p
            break
    p1 = PRICE_HISTORY[-1][1]
    if p0 <= 0: return 0.0
    return (p1 / p0 - 1.0) * 10000.0

def ema(values, period=21):
    if len(values) < period: return None
    k = 2/(period+1)
    e = values[0]
    for v in values[1:]: e = v*k + e*(1-k)
    return e

def update_vwap(price, vol=1):
    global VWAP_DATA
    if VWAP_DATA["last_reset"] != datetime.date.today():
        VWAP_DATA = {"pv_sum": 0.0, "vol_sum": 0.0, "last_reset": datetime.date.today()}
    VWAP_DATA["pv_sum"] += price * vol
    VWAP_DATA["vol_sum"] += vol

def get_vwap():
    if VWAP_DATA["vol_sum"] == 0: return None
    return VWAP_DATA["pv_sum"] / VWAP_DATA["vol_sum"]


# ================= CANDLES (Code -3) =================
def build_candle(price):
    now = int(time.time())
    if not candles or now - candles[-1]["start"] >= CANDLE_SEC:
        candles.append({"start":now,"o":price,"h":price,"l":price,"c":price})
    else:
        c = candles[-1]
        c["h"] = max(c["h"],price)
        c["l"] = min(c["l"],price)
        c["c"] = price

def bullish_engulfing():
    if len(candles)<2: return False
    p,c = candles[-2],candles[-1]
    return c["c"]>c["o"] and p["c"]<p["o"] and c["c"]>p["o"] and c["o"]<p["c"]

def bearish_engulfing():
    if len(candles)<2: return False
    p,c = candles[-2],candles[-1]
    return c["c"]<c["o"] and p["c"]>p["o"] and c["o"]>p["c"] and c["c"]<p["o"]

def hammer():
    if len(candles)<1: return False
    c=candles[-1]
    body=abs(c["c"]-c["o"])
    rng=c["h"]-c["l"]
    lower_wick=min(c["c"],c["o"]) - c["l"]
    upper_wick=c["h"] - max(c["c"],c["o"])
    return rng>0 and lower_wick>2*body and upper_wick<body

def shooting_star():
    if len(candles)<1: return False
    c=candles[-1]
    body=abs(c["c"]-c["o"])
    rng=c["h"]-c["l"]
    upper_wick=c["h"]-max(c["c"],c["o"])
    lower_wick=min(c["c"],c["o"]) - c["l"]
    return rng>0 and upper_wick>2*body and lower_wick<body


# ================= GAMMA & CHAIN (Code -1) =================
def choose_best_expiry(response):
    data = response.get("data", {})
    expiry_list = data.get("expiryData", [])
    today = datetime.date.today()
    now = datetime.datetime.now()
    cutoff = datetime.datetime(now.year, now.month, now.day, SAME_DAY_CUTOFF[0], SAME_DAY_CUTOFF[1])
    valid_dates = []
    for item in expiry_list:
        dstr = item.get("date")
        try:
            dd = datetime.datetime.strptime(dstr, "%d-%m-%Y").date()
            valid_dates.append((dd, dstr))
        except: continue
    valid_dates.sort()
    for dd, dstr in valid_dates:
        if dd > today: return dstr
        if dd == today and now < cutoff: return dstr
    return valid_dates[0][1] if valid_dates else None

def parse_chain(response, expiry):
    # Logic simplified: assume response aligns with request or filter by symbol logic
    raw_list = response.get("data", {}).get("optionsChain", [])
    strikes = {}
    for item in raw_list:
        st = item.get("strike_price")
        op_type = item.get("option_type")
        sym = item.get("symbol")
        if not st: continue
        if st not in strikes: strikes[st] = {"CE": {}, "PE": {}}
        side_data = {
            "ltp": item.get("ltp"),
            "oi": item.get("oi"),
            "oich": item.get("oi_change"),
            "symbol": sym
        }
        if op_type == "CE": strikes[st]["CE"] = side_data
        elif op_type == "PE": strikes[st]["PE"] = side_data
    return strikes

def calc_gamma_stats(strikes_map, atm, step):
    lo = atm - GB_STRIKES_AROUND_ATM * step
    hi = atm + GB_STRIKES_AROUND_ATM * step
    ce_sum = pe_sum = ce_abs = pe_abs = 0.0
    for k, v in strikes_map.items():
        if lo <= k <= hi:
            c_oich = v["CE"].get("oich") or 0
            p_oich = v["PE"].get("oich") or 0
            ce_sum += c_oich
            pe_sum += p_oich
            ce_abs += abs(c_oich)
            pe_abs += abs(p_oich)
    return ce_sum, pe_sum, ce_abs, pe_abs

def gamma_blast_decision(ce_sum, pe_sum, ce_abs, pe_abs, roc_bp):
    # Code -1 Advanced Logic
    thr = max(GB_OICH_MIN_ABS_FLOOR, GB_OICH_SCALE * max(ce_abs, pe_abs))
    if max(abs(ce_sum), abs(pe_sum)) < thr: return None

    # Positive OI build-up on one side with matching price velocity
    ce_mag = abs(ce_sum)
    pe_mag = abs(pe_sum)

    if ce_mag > pe_mag and roc_bp > GB_ROC_BP_THRESHOLD and ce_sum > 0:
        return "BULL"
    if pe_mag > ce_mag and roc_bp < -GB_ROC_BP_THRESHOLD and pe_sum > 0:
        return "BEAR"
    return None


# ================= BOOSTERS (Code -3) =================
def gamma_blast_booster_check(direction):
    # Checks the cached result from the last heavy analysis
    blast = current_chain_analysis.get("blast")
    return (blast == direction)

def gamma_wall_boost(strikes_map, price):
    # Find strike with max OI
    oi_map = {}
    for k, v in strikes_map.items():
        ce_oi = v["CE"].get("oi") or 0
        pe_oi = v["PE"].get("oi") or 0
        oi_map[k] = oi_map.get(k, 0) + abs(ce_oi) + abs(pe_oi)
    if not oi_map: return False
    wall = max(oi_map, key=oi_map.get)
    return abs(price - wall) <= 50

def ema_boost(price, direction):
    values = [p for _, p in PRICE_HISTORY]
    # Need at least period history. PRICE_HISTORY has seconds resolution approx (ticks)
    # EMA func expects a list of values.
    # Code -3 used PRICE_HISTORY[-50:]
    if not values: return False
    e = ema(values[-50:], 21)
    if not e: return False
    return price > e if direction == "BULL" else price < e

def sr_boost(direction):
    if len(candles) < 2: return False
    prev = candles[-2]
    if direction == "BULL": return candles[-1]["c"] > prev["h"]
    else: return candles[-1]["c"] < prev["l"]

def vwap_boost(price, direction):
    v = get_vwap()
    if not v: return False
    return price > v if direction == "BULL" else price < v


# ================= TRADE MANAGEMENT (Merged) =================
def place_order(fy, symbol, qty, side):
    # side: 1=Buy, -1=Sell
    try:
        resp = fy.place_order(data={
            "symbol": symbol,
            "qty": qty,
            "type": 2, # Market
            "side": side,
            "productType": PRODUCT_TYPE,
            "limitPrice": 0,
            "stopPrice": 0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
        })
        print(f"Order Placed: {side} {symbol} {qty} -> {resp}")
        return resp
    except Exception as e:
        print(f"Order Failed: {e}")
        return None

def execute_entry(fy, direction, ltp, strikes_map, atm):
    global position, entry_price, sl_price, traded_symbol, traded_qty
    row = strikes_map.get(atm)
    if not row: return

    opt_type = "CE" if direction == "BULL" else "PE"
    target_opt = row.get(opt_type)
    if not target_opt or not target_opt.get("symbol"): return

    sym = target_opt["symbol"]
    # Dynamic Qty from Master
    qty = get_lot_size(UNDERLYING_SYMBOL)

    print(f"🚀 SIGNAL: {direction} | Executing {sym} Qty {qty}")
    resp = place_order(fy, sym, qty, 1)
    if resp and (resp.get("s") == "ok" or "id" in resp):
        position = direction
        entry_price = ltp
        sl_price = ltp - SL_POINTS if direction == "BULL" else ltp + SL_POINTS
        traded_symbol = sym
        traded_qty = qty
        print(f"✅ ENTRY CONFIRMED @ {ltp} (Index) | SL: {sl_price}")

def manage_trade(fy, ltp):
    global position, sl_price, entry_price
    if not position: return
    hit_sl = ((position == "BULL" and ltp <= sl_price) or
              (position == "BEAR" and ltp >= sl_price))
    if hit_sl:
        print(f"🛑 SL HIT @ {ltp}. Exiting {traded_symbol}...")
        place_order(fy, traded_symbol, traded_qty, -1)
        position = None
        return
    # Trail
    if position == "BULL":
        if ltp - entry_price > TRAIL_TRIGGER:
            new_sl = ltp - TRAIL_STEP
            if new_sl > sl_price:
                sl_price = new_sl
                print(f"♻️ Trailed SL to {sl_price}")
    elif position == "BEAR":
        if entry_price - ltp > TRAIL_TRIGGER:
            new_sl = ltp + TRAIL_STEP
            if new_sl < sl_price:
                sl_price = new_sl
                print(f"♻️ Trailed SL to {sl_price}")


# ================= ENGINE =================
def on_message(msg):
    global last_oc_fetch_time, current_chain_analysis
    data = msg.get("data", [])
    for tick in data:
        ltp = tick.get("ltp")
        if not ltp: continue

        # 1. Update Indicators
        roc_bp = update_price_history_and_get_roc(UNDERLYING_SYMBOL, ltp)
        update_vwap(ltp)
        build_candle(ltp)

        # 2. Heavy Option Chain Analysis (Throttled per Code -1)
        now = time.time()
        strikes_map = current_chain_analysis.get("strikes", {})
        atm = current_chain_analysis.get("atm", round(ltp/50)*50)

        if now - last_oc_fetch_time > OC_INTERVAL:
            print(f"⏳ Fetching OC... (LTP: {ltp})")
            try:
                resp, err = call_with_retries(fy_client.optionchain, {
                    "symbol": UNDERLYING_SYMBOL,
                    "strikecount": 20
                })
                if not err:
                    exp = choose_best_expiry(resp)
                    strikes_map = parse_chain(resp, exp)
                    step = SYMBOL_MASTER_MAP.get(UNDERLYING_SYMBOL, {}).get("step", 50)
                    atm = round(ltp / step) * step
                    c_sum, p_sum, c_abs, p_abs = calc_gamma_stats(strikes_map, atm, step)
                    blast = gamma_blast_decision(c_sum, p_sum, c_abs, p_abs, roc_bp)

                    current_chain_analysis = {
                        "strikes": strikes_map,
                        "blast": blast,
                        "atm": atm,
                    }
                    if blast: print(f"💥 GAMMA BLAST DETECTED: {blast}")
                last_oc_fetch_time = now
            except Exception as e:
                print(f"OC Fetch Error: {e}")

        # 3. Strategy Logic (Code -3)
        # Check Candles
        direction = None
        if bullish_engulfing() or hammer(): direction = "BULL"
        elif bearish_engulfing() or shooting_star(): direction = "BEAR"

        if direction:
            boosters = [
                gamma_blast_booster_check(direction),
                vwap_boost(ltp, direction),
                gamma_wall_boost(strikes_map, ltp),
                ema_boost(ltp, direction),
                sr_boost(direction)
            ]
            # Entry
            if not position and any(boosters):
                print(f"🎯 CONFLUENCE: Candle {direction} + Boosters {boosters}")
                execute_entry(fy_client, direction, ltp, strikes_map, atm)

        # 4. Manage
        manage_trade(fy_client, ltp)


# ================= MAIN =================
fy_client = None

def _run_tests():
    print("✅ Running Internal Logic Tests...")
    assert ema([1]*25, 21) == 1
    assert gamma_blast_decision(0,0,0,0,0) is None
    print("✅ Tests Passed.")

def main():
    global fy_client
    print("Initializing Gamma Bot v3...")
    _run_tests()

    # 1. Auth
    fy_client, app_id, token_str = ensure_valid_client()
    print(f"Authenticated as {app_id} (Mock: {not FYERS_AVAILABLE})")

    # 2. Master
    fetch_symbol_master()

    # 3. WebSocket
    print(f"Connecting WebSocket for {UNDERLYING_SYMBOL}...")
    ws = data_ws.FyersDataSocket(
        access_token=token_str,
        log_path=os.getcwd(),
        litemode=False,
        write_to_file=False,
        reconnect=True,
        on_message=on_message
    )
    ws.subscribe(symbols=[UNDERLYING_SYMBOL], data_type="symbolData")
    ws.keep_running()

if __name__ == "__main__":
    main()
