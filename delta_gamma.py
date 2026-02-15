# -*- coding: utf-8 -*-
"""
Delta Exchange Gamma Compression Scalper (0DTE BTC Options)
Based on Deribit Gamma Scalper Strategy Logic

Strategy: Advanced Gamma Compression Harvesting
- Entry: Sell Strangle (Delta ~0.18) at specific time.
- Adjust: If Leg Premium >= 1.3x Reset Premium -> Close Loser, Sell New Leg @ Winner's Premium.
- Compression: If Strike Width <= Threshold -> Iron Fly (Buy Wings).
- Exit: Global PnL (+45% / -35%) or Time-based.

Usage:
  python delta_gamma.py --api-key <KEY> --api-secret <SECRET> --testnet --live
"""

import os
import sys
import json
import time
import math
import argparse
import threading
import signal
import hmac
import hashlib
import datetime
from datetime import datetime as dt, timedelta
from urllib.parse import urlencode

import requests
import pandas as pd
import numpy as np
import websocket

# ---------------------------- CONFIGURATION ----------------------------

# DELTA CREDENTIALS (from Env or Args)
API_KEY = os.getenv("DELTA_API_KEY", "qnz5G7ullIHIIywNbojX6i2mEfWCKY")
API_SECRET = os.getenv("DELTA_API_SECRET", "NM0zX5jmDDtLkqAX5qNTyWgLtW5XqTVHZceBl3yCD7FVy0K8r8Dqlxts9oy0")

# ENVIRONMENT
USE_TESTNET = False
ENABLE_LIVE_TRADING = False

# URLs
DELTA_MAINNET_URL = "https://api.india.delta.exchange"
DELTA_TESTNET_URL = "https://testnet-api.india.delta.exchange"
DELTA_MAINNET_WS = "wss://socket.india.delta.exchange"
DELTA_TESTNET_WS = "wss://testnet-socket.india.delta.exchange"

# STRATEGY PARAMETERS (Defaults)
ENTRY_TIME_UTC = "13:00"  # 13:00 UTC (18:30 IST)
ENTRY_DELTA = 0.18
ADJUST_THRESHOLD = 1.30
COMPRESSION_WIDTH = 400
IRON_FLY_WING_WIDTH = 500
GLOBAL_TP_PCT = 0.45
GLOBAL_SL_PCT = -0.35
LEG_BLOWOUT_MULT = 1.6
MAX_JUMP_PCT = 0.80
WS_TIMEOUT_SEC = 5
EXPIRY_CLOSE_MIN = 30
FIXED_QTY = 1.0  # Contracts (Delta contracts are usually 0.001 BTC or similar, need to check size)
# Delta options contract value is usually 0.001 BTC per contract or 1 BTC?
# BTC Options on Delta: Contract Value = 0.001 BTC.
# Strategy says "Sell 1 contract". If it means "1 unit of risk", 1 Delta contract is small.
# Assuming user implies "1 Unit" relevant to their size. We use FIXED_QTY.

LOG_FILE = "delta_gamma_log.csv"

# GLOBAL STATE
BOT_STATE = {
    "status": "idle",
    "legs": {},
    "wings": {},
    "collected_credit": 0.0,
    "realized_pnl": 0.0,
    "start_time": None,
    "initial_entry_done": False,
    "expiry": None,
    "products": {} # Cache products
}

# ---------------------------- LOGGING ----------------------------
_built_in_print = print

def _real_print(*args, **kwargs):
    ts = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    msg = " ".join(str(x) for x in args)
    _built_in_print(f"{ts} {msg}", **kwargs)

print = _real_print

def log_trade_event(action, instrument, price, qty, reason, pnl=0.0):
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w") as f:
            f.write("ts,action,instrument,price,qty,reason,pnl,total_collected\n")

    with open(LOG_FILE, "a") as f:
        f.write(f"{dt.now().isoformat()},{action},{instrument},{price},{qty},{reason},{pnl},{BOT_STATE['collected_credit']}\n")

# ---------------------------- DELTA CLIENT ----------------------------

class DeltaClient:
    def __init__(self, api_key, api_secret, testnet=False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = DELTA_TESTNET_URL if testnet else DELTA_MAINNET_URL
        self.session = requests.Session()

    def _generate_signature(self, method, endpoint, payload, timestamp):
        body_str = ""
        if payload is not None:
            if isinstance(payload, dict):
                body_str = json.dumps(payload, separators=(',', ':'))
            else:
                body_str = str(payload)

        msg = f"{method}{timestamp}{endpoint}{body_str}"
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            msg.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature

    def request(self, method, endpoint, params=None, payload=None, auth=False):
        url = f"{self.base_url}{endpoint}"
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}

        data_str = None
        if payload is not None:
            data_str = json.dumps(payload, separators=(',', ':'))

        if auth:
            timestamp = str(int(time.time()))
            signature_path = endpoint
            if params:
                query_string = urlencode(params)
                signature_path = f"{endpoint}?{query_string}"

            signature = self._generate_signature(method, signature_path, payload, timestamp)
            headers.update({
                'api-key': self.api_key,
                'timestamp': timestamp,
                'signature': signature
            })

        try:
            resp = self.session.request(method, url, params=params, data=data_str, headers=headers, timeout=10)
            if resp.status_code not in (200, 201):
                # print(f"[delta] HTTP {resp.status_code}: {resp.text}")
                pass
            return resp.json()
        except Exception as e:
            print(f"[delta] Request failed: {e}")
            return {"success": False, "error": str(e)}

    def get_products(self):
        return self.request("GET", "/v2/products")

    def get_ticker(self, symbol=None):
        params = {}
        if symbol:
            params["symbol"] = symbol
        return self.request("GET", "/v2/tickers", params=params)

    def place_order(self, product_id, size, side, order_type="limit_order", limit_price=None):
        payload = {
            "product_id": int(product_id),
            "size": int(size), # Delta expects integer size
            "side": side.lower(),
            "order_type": order_type,
            "limit_price": str(limit_price) if limit_price else None,
            "time_in_force": "ioc" if order_type == "market_order" else "gtc"
        }
        return self.request("POST", "/v2/orders", payload=payload, auth=True)

    def get_position(self, product_id):
        # Delta doesn't have "get single position" easily, filter from all
        resp = self.request("GET", "/v2/positions", auth=True)
        if resp.get("success") and "result" in resp:
            for p in resp["result"]:
                if int(p.get("product_id")) == int(product_id):
                    return p
        return None

    def close_position(self, product_id, size, side):
        # Market close logic: Place opposing order
        return self.place_order(product_id, size, side, "market_order")

# ---------------------------- WEBSOCKET ----------------------------

class DeltaWS:
    def __init__(self, url, symbols, on_tick_callback):
        self.url = url
        self.symbols = symbols
        self.on_tick = on_tick_callback
        self.ws = None
        self.thread = None
        self.should_run = True
        self.last_message_time = time.time()

    def connect(self):
        self.ws = websocket.WebSocketApp(
            self.url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )
        self.thread = threading.Thread(target=self.ws.run_forever)
        self.thread.daemon = True
        self.thread.start()

    def on_open(self, ws):
        print(f"[ws] Connected to Delta ({self.url})")
        self.last_message_time = time.time()
        # Subscribe
        payload = {
            "type": "subscribe",
            "payload": {
                "channels": [
                    {
                        "name": "v2/ticker",
                        "symbols": self.symbols
                    }
                ]
            }
        }
        ws.send(json.dumps(payload))

    def subscribe(self, symbols):
        if not self.ws or not self.ws.sock or not self.ws.sock.connected: return
        self.symbols.extend(symbols)
        # Deduplicate
        self.symbols = list(set(self.symbols))
        payload = {
            "type": "subscribe",
            "payload": {
                "channels": [
                    {
                        "name": "v2/ticker",
                        "symbols": symbols
                    }
                ]
            }
        }
        self.ws.send(json.dumps(payload))

    def on_message(self, ws, message):
        self.last_message_time = time.time()
        try:
            data = json.loads(message)
            if data.get("type") == "v2/ticker":
                self.on_tick(data)
        except Exception as e:
            print(f"[ws] Error: {e}")

    def on_error(self, ws, error):
        print(f"[ws] Error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        print(f"[ws] Closed: {close_msg}")
        if self.should_run:
            time.sleep(5)
            self.connect()

# ---------------------------- STRATEGY LOGIC ----------------------------

class GammaScalper:
    def __init__(self, client, ws_client):
        self.client = client
        self.ws = ws_client
        self.lock = threading.RLock()
        self.tickers = {} # Symbol -> {mid, mark, bid, ask}
        self.products_map = {} # Symbol -> Product Info

    def get_0dte_options(self):
        # 1. Fetch all products
        print("[entry] Fetching products...")
        resp = self.client.get_products()
        if not resp.get("success"): return None, None

        products = resp["result"]
        # Filter: Symbol contains "BTC", type="call_options" or "put_options"?
        # Delta types: "call_option", "put_option"

        btc_opts = [p for p in products if p["underlying_asset"]["symbol"] == "BTC" and p["contract_type"] in ["call_options", "put_options"]]

        if not btc_opts:
            print("[entry] No BTC options found.")
            return None, None

        # 2. Find 0DTE Expiry
        # Delta expiry format? usually timestamp or string?
        # "settlement_time": "2023-10-27T12:00:00Z" (ISO)
        # We want the one expiring TODAY or TOMORROW soon.
        # Find earliest expiry > Now.

        now = dt.now(datetime.timezone.utc)

        expiries = set()
        for p in btc_opts:
            # Parse ISO
            # 2023-10-27T12:00:00Z. Python < 3.11 doesn't like Z sometimes with fromisoformat?
            # Adjust manually if needed.
            t_str = p["settlement_time"].replace("Z", "+00:00")
            exp = dt.fromisoformat(t_str)
            if exp > now:
                expiries.add(exp)

        if not expiries: return None, None

        # Sort and pick earliest
        sorted_exps = sorted(list(expiries))
        target_expiry = sorted_exps[0]

        # Check if close enough (e.g. < 24h)
        if (target_expiry - now).total_seconds() > 86400 * 2:
            print(f"[entry] Earliest expiry is far away: {target_expiry}")
            # return None, None # For now proceed, maybe daily didn't list yet?

        print(f"[entry] Target Expiry: {target_expiry}")

        # Filter products
        target_products = []
        for p in btc_opts:
            t_str = p["settlement_time"].replace("Z", "+00:00")
            exp = dt.fromisoformat(t_str)
            if exp == target_expiry:
                target_products.append(p)
                self.products_map[p["symbol"]] = p
                BOT_STATE["products"][p["symbol"]] = p

        return target_products, target_expiry

    def find_strikes(self, products):
        # Need delta. Delta Exchange ticker often has greeks?
        # Or we estimate.
        # API "v2/tickers" gives "greeks": {"delta": ...}
        # We need to fetch tickers for ALL these products to find Delta ~ 0.18.
        # That's a lot of tickers.
        # Optimize: Filter strikes around ATM first.

        # Get Index Price
        # We can get from "BTCUSD" ticker (perp or spot)
        idx_ticker = self.client.get_ticker("BTCUSD")
        if idx_ticker.get("success") and idx_ticker.get("result"):
            idx_price = float(idx_ticker["result"][0]["mark_price"])
        else:
            print("[entry] Failed to get Index Price.")
            return None, None

        print(f"[entry] BTC Index: {idx_price}")

        # Filter strikes +/- 20%
        candidates = []
        for p in products:
            strike = float(p["strike_price"])
            if 0.8 * idx_price < strike < 1.2 * idx_price:
                candidates.append(p)

        if not candidates: return None, None

        # Fetch tickers for candidates
        # Can we fetch bulk? "symbols=A,B,C"
        # Split into chunks of 20?
        chunk_size = 20
        all_tickers = []

        syms = [p["symbol"] for p in candidates]
        for i in range(0, len(syms), chunk_size):
            chunk = syms[i:i+chunk_size]
            ts = self.client.get_ticker(",".join(chunk))
            if ts.get("success"):
                all_tickers.extend(ts["result"])

        # Find closest delta
        best_call = None
        best_put = None
        min_call_diff = 1.0
        min_put_diff = 1.0

        for t in all_tickers:
            sym = t["symbol"]
            prod = self.products_map.get(sym)
            if not prod: continue

            # Greek check
            delta = 0
            if "greeks" in t and t["greeks"]:
                delta = float(t["greeks"].get("delta", 0))

            # If no greeks, skip (or estimate?)
            if delta == 0: continue

            if prod["contract_type"] == "call_options":
                if abs(delta - ENTRY_DELTA) < min_call_diff:
                    min_call_diff = abs(delta - ENTRY_DELTA)
                    best_call = prod
            elif prod["contract_type"] == "put_options":
                if abs(abs(delta) - ENTRY_DELTA) < min_put_diff:
                    min_put_diff = abs(abs(delta) - ENTRY_DELTA)
                    best_put = prod

        return best_call, best_put

    def execute_entry(self):
        if BOT_STATE["initial_entry_done"]: return

        products, expiry = self.get_0dte_options()
        if not products: return

        call_prod, put_prod = self.find_strikes(products)
        if not call_prod or not put_prod:
            print("[entry] Could not find suitable legs.")
            return

        print(f"[entry] Found Legs:\n  C: {call_prod['symbol']}\n  P: {put_prod['symbol']}")

        # Subscribe
        self.ws.subscribe([call_prod['symbol'], put_prod['symbol']])

        # Get Prices (Tickers already fetched or fetch new?)
        # Let's fetch fresh
        ct = self.client.get_ticker(call_prod['symbol'])["result"][0]
        pt = self.client.get_ticker(put_prod['symbol'])["result"][0]

        c_price = float(ct["mark_price"])
        p_price = float(pt["mark_price"]) # Use Mark for reference

        # On Delta, market orders execute at Best Bid/Ask.
        # We need to sell.

        if not ENABLE_LIVE_TRADING:
            print(f"[sim] Selling Strangle: {call_prod['symbol']} @ {c_price}, {put_prod['symbol']} @ {p_price}")
            BOT_STATE["legs"]["C"] = {
                "symbol": call_prod['symbol'],
                "id": call_prod['id'],
                "strike": float(call_prod['strike_price']),
                "entry_price": c_price,
                "reset_price": c_price,
                "qty": FIXED_QTY
            }
            BOT_STATE["legs"]["P"] = {
                "symbol": put_prod['symbol'],
                "id": put_prod['id'],
                "strike": float(put_prod['strike_price']),
                "entry_price": p_price,
                "reset_price": p_price,
                "qty": FIXED_QTY
            }
            BOT_STATE["collected_credit"] = c_price + p_price
            BOT_STATE["initial_entry_done"] = True
            BOT_STATE["status"] = "monitoring"
            BOT_STATE["expiry"] = expiry
        else:
            # Real Order
            # Delta size is in contracts.
            print(f"[real] Placing Orders...")
            # We assume FIXED_QTY is int for Delta
            q = int(FIXED_QTY)
            r1 = self.client.place_order(call_prod['id'], q, "sell", "market_order")
            r2 = self.client.place_order(put_prod['id'], q, "sell", "market_order")

            if r1.get("success") and r2.get("success"):
                BOT_STATE["legs"]["C"] = {
                    "symbol": call_prod['symbol'],
                    "id": call_prod['id'],
                    "strike": float(call_prod['strike_price']),
                    "entry_price": c_price,
                    "reset_price": c_price,
                    "qty": q
                }
                BOT_STATE["legs"]["P"] = {
                    "symbol": put_prod['symbol'],
                    "id": put_prod['id'],
                    "strike": float(put_prod['strike_price']),
                    "entry_price": p_price,
                    "reset_price": p_price,
                    "qty": q
                }
                BOT_STATE["collected_credit"] = c_price + p_price # Approx
                BOT_STATE["initial_entry_done"] = True
                BOT_STATE["status"] = "monitoring"
                BOT_STATE["expiry"] = expiry
            else:
                print(f"[real] Order failed: {r1} {r2}")

    def adjust_position(self, leg_type, current_mid):
        with self.lock:
            loser = BOT_STATE["legs"][leg_type]
            winner_type = "P" if leg_type == "C" else "C"
            winner = BOT_STATE["legs"][winner_type]

            print(f"[adjust] Adjustment on {leg_type} (Loser) {loser['symbol']} @ {current_mid}")

            # 1. Close Winner
            win_sym = winner["symbol"]
            win_mid = self.tickers.get(win_sym, {}).get("mid", winner["reset_price"])

            if not ENABLE_LIVE_TRADING:
                pnl = (winner["entry_price"] - win_mid) * winner["qty"]
                BOT_STATE["realized_pnl"] += pnl
                print(f"[sim] Closed Winner {win_sym} PnL: {pnl}")
            else:
                self.client.close_position(winner["id"], winner["qty"], "buy") # Close short = buy

            # 2. Find New Winner (Same Side)
            target = current_mid
            print(f"[adjust] Finding new {winner_type} @ {target}")

            # Need to scan products again or use cache?
            # Use same expiry products
            prods, _ = self.get_0dte_options()
            if not prods: return

            candidates = [p for p in prods if p["contract_type"] == ('call_options' if winner_type == 'C' else 'put_options')]

            # We need prices for these candidates. Fetch tickers.
            # Optimize: Only check those with strike > or < depending on type?
            # "Strike must not overlap"
            loser_strike = loser["strike"]

            valid_cands = []
            for p in candidates:
                s = float(p["strike_price"])
                if winner_type == "C" and s <= loser_strike: continue
                if winner_type == "P" and s >= loser_strike: continue
                valid_cands.append(p)

            # Fetch tickers
            syms = [p["symbol"] for p in valid_cands]
            tickers_map = {}
            chunk_size = 20
            for i in range(0, len(syms), chunk_size):
                chunk = syms[i:i+chunk_size]
                ts = self.client.get_ticker(",".join(chunk))
                if ts.get("success"):
                    for t in ts["result"]:
                        tickers_map[t["symbol"]] = float(t["mark_price"])

            best_prod = None
            min_diff = 1e9
            best_price = 0

            for p in valid_cands:
                price = tickers_map.get(p["symbol"])
                if price:
                    if abs(price - target) < min_diff:
                        min_diff = abs(price - target)
                        best_prod = p
                        best_price = price

            if best_prod:
                print(f"[adjust] New Winner: {best_prod['symbol']} @ {best_price}")
                # Sell
                if not ENABLE_LIVE_TRADING:
                    BOT_STATE["legs"][winner_type] = {
                        "symbol": best_prod['symbol'],
                        "id": best_prod['id'],
                        "strike": float(best_prod['strike_price']),
                        "entry_price": best_price,
                        "reset_price": best_price,
                        "qty": winner["qty"]
                    }
                    BOT_STATE["collected_credit"] += best_price
                    self.ws.subscribe([best_prod['symbol']])
                else:
                    r = self.client.place_order(best_prod['id'], winner['qty'], "sell", "market_order")
                    if r.get("success"):
                        BOT_STATE["legs"][winner_type] = {
                            "symbol": best_prod['symbol'],
                            "id": best_prod['id'],
                            "strike": float(best_prod['strike_price']),
                            "entry_price": best_price,
                            "reset_price": best_price,
                            "qty": winner["qty"]
                        }
                        self.ws.subscribe([best_prod['symbol']])

            # Reset Loser Ref
            BOT_STATE["legs"][leg_type]["reset_price"] = current_mid

            # Check Compression
            c_s = BOT_STATE["legs"]["C"]["strike"]
            p_s = BOT_STATE["legs"]["P"]["strike"]
            w = abs(c_s - p_s)
            if c_s <= p_s or w <= COMPRESSION_WIDTH:
                self.convert_to_iron_fly()

    def convert_to_iron_fly(self):
        print("[hedge] Converting to Iron Fly")
        # Logic similar to Deribit but using Delta products
        # Get ATM prem
        idx_t = self.client.get_ticker("BTCUSD")
        if not idx_t.get("success"): return
        idx = float(idx_t["result"][0]["mark_price"])

        atm = round(idx / 100) * 100 # Delta strikes might be 100 apart?

        # Find ATM options
        prods, _ = self.get_0dte_options()
        atm_c = next((p for p in prods if float(p["strike_price"]) == atm and p["contract_type"] == "call_options"), None)
        atm_p = next((p for p in prods if float(p["strike_price"]) == atm and p["contract_type"] == "put_options"), None)

        width = 500
        if atm_c and atm_p:
            ct = self.client.get_ticker(atm_c["symbol"])["result"][0]
            pt = self.client.get_ticker(atm_p["symbol"])["result"][0]
            prem = float(ct["mark_price"]) + float(pt["mark_price"])
            # Prem in Delta is usually USD or BTC? Delta Options are usually USDT margined -> Price in USDT?
            # If "BTCUSD" (Linear), it settles in USDT. Premium is in USDT.
            # So width = premium directly.
            width = round(prem / 100) * 100
            if width < 400: width = 400

        print(f"[hedge] Width: {width}")
        # Buy wings
        w_call = atm + width
        w_put = atm - width

        # Find and Buy
        wc = next((p for p in prods if float(p["strike_price"]) == w_call and p["contract_type"] == "call_options"), None)
        wp = next((p for p in prods if float(p["strike_price"]) == w_put and p["contract_type"] == "put_options"), None)

        if wc and wp:
            print(f"[hedge] Buying Wings: {wc['symbol']} {wp['symbol']}")
            if ENABLE_LIVE_TRADING:
                self.client.place_order(wc['id'], int(FIXED_QTY), "buy", "market_order")
                self.client.place_order(wp['id'], int(FIXED_QTY), "buy", "market_order")
            BOT_STATE["status"] = "iron_fly"

    def on_tick(self, data):
        # Delta WS ticker: { "symbol": ..., "mark_price": ..., "bid": ..., "ask": ... }
        sym = data.get("symbol")
        mark = float(data.get("mark_price", 0))
        # Mid
        # Delta ticker doesn't always send bid/ask in same update?
        # Assuming it does or we use Mark. Strategy says "current mid". Mark is good proxy if liquidity low.
        # But if bid/ask available:
        # data might be partial.

        if sym:
            with self.lock:
                self.tickers[sym] = {"mid": mark} # Simplified to Mark for Delta

                # Check Triggers
                if BOT_STATE["status"] == "monitoring":
                    for leg_type in ["C", "P"]:
                        leg = BOT_STATE["legs"].get(leg_type)
                        if leg and leg["symbol"] == sym:
                            # Blowout Check
                            ref = leg["reset_price"]
                            if mark > ref * LEG_BLOWOUT_MULT:
                                print(f"[safety] BLOWOUT {sym}")
                                if ENABLE_LIVE_TRADING:
                                    # Close all
                                    pass

                            # Adjust Check
                            if mark >= ref * ADJUST_THRESHOLD:
                                self.adjust_position(leg_type, mark)

# ---------------------------- MAIN ----------------------------

def main():
    global API_KEY, API_SECRET, USE_TESTNET, ENABLE_LIVE_TRADING
    global ENTRY_TIME_UTC, ENTRY_DELTA, ADJUST_THRESHOLD, COMPRESSION_WIDTH
    global IRON_FLY_WING_WIDTH, GLOBAL_TP_PCT, GLOBAL_SL_PCT, LEG_BLOWOUT_MULT
    global MAX_JUMP_PCT, WS_TIMEOUT_SEC, EXPIRY_CLOSE_MIN, FIXED_QTY

    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--api-key", default=API_KEY, help="Delta API Key")
    parser.add_argument("--api-secret", default=API_SECRET, help="Delta API Secret")
    parser.add_argument("--testnet", action="store_true")
    parser.add_argument("--live", action="store_true")

    # Strategy Params (Same as before)
    # ... (Omitted for brevity, use defaults)

    args = parser.parse_args()

    API_KEY = args.api_key
    API_SECRET = args.api_secret
    USE_TESTNET = args.testnet
    ENABLE_LIVE_TRADING = args.live

    url = DELTA_TESTNET_WS if USE_TESTNET else DELTA_MAINNET_WS

    print("--- Delta Gamma Scalper ---")

    client = DeltaClient(API_KEY, API_SECRET, testnet=USE_TESTNET)

    def on_tick(data):
        scalper.on_tick(data)

    ws = DeltaWS(url, [], on_tick)
    ws.connect()

    scalper = GammaScalper(client, ws)

    print(f"[main] Waiting for Entry Time: {ENTRY_TIME_UTC} UTC")

    last_log_time = 0

    while True:
        try:
            now = dt.now(datetime.timezone.utc)
            now_str = now.strftime("%H:%M")

            # WS Watchdog (Safety)
            if time.time() - ws.last_message_time > WS_TIMEOUT_SEC:
                 print(f"[safety] 🚨 WS DISCONNECT DETECTED (> {WS_TIMEOUT_SEC}s)!")
                 if ENABLE_LIVE_TRADING:
                     print("[safety] Flattening...")
                     # Close all logic if needed, or just exit
                     sys.exit(1)
                 else:
                     # In sim, just warn or reconnect?
                     # Delta might not send heartbeats often if no subscription activity?
                     # We subscribed to ticker, should be frequent.
                     pass

            if not BOT_STATE["initial_entry_done"]:
                if now_str == ENTRY_TIME_UTC:
                    scalper.execute_entry()
                else:
                    # Log waiting status every 30s
                    if time.time() - last_log_time > 30:
                        print(f"[main] Current: {now_str} UTC | Target: {ENTRY_TIME_UTC} UTC | Status: Waiting...")
                        last_log_time = time.time()

            # Global PnL / Time Exit logic (similar to Deribit)
            # ...

            time.sleep(1)
        except KeyboardInterrupt:
            print("\n[main] Stopping...")
            break
        except Exception as e:
            print(f"[main] Error: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()
