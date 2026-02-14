# -*- coding: utf-8 -*-
"""
Deribit Gamma Compression Scalper (0DTE BTC Options)
Based on Delta Exchange Bot Structure (Code-1)

Strategy: Advanced Gamma Compression Harvesting
- Entry: Sell Strangle (Delta ~0.18) at specific time.
- Adjust: If Leg Premium >= 1.3x Reset Premium -> Close Loser, Sell New Leg @ Winner's Premium.
- Compression: If Strike Width <= Threshold -> Iron Fly (Buy Wings).
- Exit: Global PnL (+45% / -35%) or Time-based.

Usage:
  python deribit_gamma.py --api-key <KEY> --api-secret <SECRET> --testnet --live
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
import websocket  # pip install websocket-client

# ---------------------------- CONFIGURATION ----------------------------

# DERIBIT CREDENTIALS (from Env or Args)
API_KEY = os.getenv("DERIBIT_API_KEY", "")
API_SECRET = os.getenv("DERIBIT_API_SECRET", "")

# ENVIRONMENT
USE_TESTNET = False
ENABLE_LIVE_TRADING = False  # Safety default, can be overridden by args

# URLs
DERIBIT_MAINNET_URL = "https://www.deribit.com"
DERIBIT_TESTNET_URL = "https://test.deribit.com"
DERIBIT_MAINNET_WS = "wss://www.deribit.com/ws/api/v2"
DERIBIT_TESTNET_WS = "wss://test.deribit.com/ws/api/v2"

# STRATEGY PARAMETERS
ENTRY_TIME_UTC = "13:00"  # 13:00 UTC (18:30 IST)
ENTRY_DELTA = 0.18
ADJUST_THRESHOLD = 1.30   # 30% premium increase triggers adjustment
COMPRESSION_WIDTH = 500   # Strike width to trigger Iron Fly
IRON_FLY_WING_WIDTH = 500 # Width of wings for Iron Fly
GLOBAL_TP_PCT = 0.45      # 45% of collected credit
GLOBAL_SL_PCT = -0.35     # -35% of collected credit
LEG_BLOWOUT_MULT = 2.5    # 2.5x credit loss on single leg -> flatten
MAX_JUMP_PCT = 0.80       # 80% jump in price -> flatten
WS_TIMEOUT_SEC = 15       # 15s disconnect -> flatten
EXPIRY_CLOSE_MIN = 30     # Close positions 30 mins before expiry
FIXED_QTY = 1.0           # 1 Contract

LOG_FILE = "deribit_gamma_log.csv"
STATE_FILE = "deribit_gamma_state.json"

# GLOBAL STATE
BOT_STATE = {
    "status": "idle",  # idle, monitoring, compressed, iron_fly, closed
    "legs": {},        # store leg details: { "C": {...}, "P": {...} }
    "wings": {},       # store wings for Iron Fly: { "C": ..., "P": ... }
    "collected_credit": 0.0,
    "realized_pnl": 0.0,
    "start_time": None,
    "initial_entry_done": False
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

# ---------------------------- DERIBIT CLIENT ----------------------------

class DeribitClient:
    def __init__(self, api_key, api_secret, testnet=False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = DERIBIT_TESTNET_URL if testnet else DERIBIT_MAINNET_URL
        self.session = requests.Session()
        self.token = None
        self.refresh_token = None
        self.token_expiry = 0

    def _get_auth_token(self):
        url = f"{self.base_url}/api/v2/public/auth"
        params = {
            "grant_type": "client_credentials",
            "client_id": self.api_key,
            "client_secret": self.api_secret
        }
        try:
            resp = self.session.get(url, params=params).json()
            if "result" in resp:
                self.token = resp["result"]["access_token"]
                self.refresh_token = resp["result"]["refresh_token"]
                self.token_expiry = time.time() + resp["result"]["expires_in"] - 60
                return True
        except Exception as e:
            print(f"[auth] Token fetch failed: {e}")
        return False

    def request(self, method, endpoint, params=None, auth=False):
        if auth:
            if not self.token or time.time() > self.token_expiry:
                if not self._get_auth_token():
                    return {"error": "Auth failed"}

            headers = {"Authorization": f"Bearer {self.token}"}
        else:
            headers = {}

        url = f"{self.base_url}/api/v2/{endpoint}"
        try:
            if method == "GET":
                resp = self.session.get(url, params=params, headers=headers, timeout=10)
            else:
                resp = self.session.post(url, json=params, headers=headers, timeout=10)

            if resp.status_code != 200:
                pass
            return resp.json()
        except Exception as e:
            print(f"[api] Request error: {e}")
            return {"error": str(e)}

    def get_index_price(self, index_name="btc_usd"):
        res = self.request("GET", "public/get_index_price", {"index_name": index_name})
        return res.get("result", {}).get("index_price")

    def get_instruments(self, currency="BTC", kind="option", expired=False):
        res = self.request("GET", "public/get_instruments", {
            "currency": currency,
            "kind": kind,
            "expired": str(expired).lower()
        })
        return res.get("result", [])

    def place_order(self, instrument_name, amount, side, order_type="limit", price=None, label=None):
        endpoint = "private/buy" if side.lower() == "buy" else "private/sell"
        params = {
            "instrument_name": instrument_name,
            "amount": amount,
            "type": order_type,
        }
        if price:
            params["price"] = price
        if label:
            params["label"] = label

        return self.request("POST", endpoint, params, auth=True) # Use POST for orders

    def close_position(self, instrument_name, order_type="market"):
        params = {
            "instrument_name": instrument_name,
            "type": order_type
        }
        return self.request("POST", "private/close_position", params, auth=True) # Use POST

    def close_all(self, legs):
        for leg in legs.values():
            if leg.get("instrument"):
                print(f"[close_all] Closing {leg['instrument']}...")
                self.close_position(leg['instrument'])

# ---------------------------- WEBSOCKET ----------------------------

class DeribitWS:
    def __init__(self, url, api_key, api_secret, on_message_callback):
        self.url = url
        self.api_key = api_key
        self.api_secret = api_secret
        self.on_message_callback = on_message_callback
        self.ws = None
        self.thread = None
        self.should_run = True
        self.authenticated = False
        self.id_counter = 0
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

    def _next_id(self):
        self.id_counter += 1
        return self.id_counter

    def send_auth(self):
        msg = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "public/auth",
            "params": {
                "grant_type": "client_credentials",
                "client_id": self.api_key,
                "client_secret": self.api_secret
            }
        }
        self.ws.send(json.dumps(msg))

    def subscribe(self, channels):
        msg = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "public/subscribe",
            "params": {
                "channels": channels
            }
        }
        if self.ws and self.ws.sock and self.ws.sock.connected:
            self.ws.send(json.dumps(msg))

    def subscribe_private(self, channels):
        msg = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "private/subscribe",
            "params": {
                "channels": channels
            }
        }
        if self.ws and self.ws.sock and self.ws.sock.connected:
            self.ws.send(json.dumps(msg))

    def on_open(self, ws):
        print(f"[ws] Connected to Deribit ({self.url})")
        self.last_message_time = time.time()
        self.send_auth()
        # Subscribe to public heartbeat channel to ensure message flow
        self.subscribe(["deribit_price_index.btc_usd"])

    def on_message(self, ws, message):
        self.last_message_time = time.time()
        try:
            data = json.loads(message)

            # Auth response check
            if "result" in data and isinstance(data["result"], dict) and "access_token" in data["result"]:
                self.authenticated = True
                print("[ws] Authenticated successfully.")
                # Subscribe to portfolio/orders
                channels = [
                    "user.portfolio.BTC",
                    "user.orders.any.any.100ms",
                    "user.trades.any.any.100ms"
                ]
                self.subscribe_private(channels)

            if "method" in data and data["method"] == "heartbeat":
                return # Just keep alive

            if "error" in data:
                print(f"[ws] API Error: {data['error']}")

            self.on_message_callback(data)

        except Exception as e:
            print(f"[ws] Parse error: {e}")

    def on_error(self, ws, error):
        print(f"[ws] Error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        print(f"[ws] Closed: {close_msg}")
        self.authenticated = False
        if self.should_run:
            time.sleep(5)
            self.connect()

# ---------------------------- STRATEGY LOGIC ----------------------------

class GammaScalper:
    def __init__(self, client, ws_client):
        self.client = client
        self.ws = ws_client
        self.lock = threading.RLock()
        self.tickers = {}  # Symbol -> {mark, mid}

    def get_0dte_expiry(self):
        # 0DTE = Expiry < 24h. Deribit daily options expire at 08:00 UTC.
        # If now is 13:00 UTC, the next expiry is tomorrow 08:00 UTC.
        instruments = self.client.get_instruments(currency="BTC", kind="option")
        if not instruments: return None, None

        now = dt.now(datetime.timezone.utc)
        candidates = []
        for i in instruments:
            exp_ts = i['expiration_timestamp'] / 1000.0
            exp_dt = dt.fromtimestamp(exp_ts, datetime.timezone.utc)
            if exp_dt > now:
                candidates.append((i, exp_dt))

        if not candidates: return None, None

        candidates.sort(key=lambda x: x[1])
        target_expiry = candidates[0][1] # Soonest expiry

        # Check if it's "today/tomorrow" (0DTEish)
        # If > 48h, maybe no daily options?
        hours_diff = (target_expiry - now).total_seconds() / 3600.0
        print(f"[expiry] Closest expiry is {target_expiry} ({hours_diff:.1f} hours away)")

        expiry_instrs = [x[0] for x in candidates if x[1] == target_expiry]
        return expiry_instrs, target_expiry

    def find_strikes(self, instruments, index_price):
        # Find Delta ~ 0.18
        # We need greeks.
        summaries = self.client.request("GET", "public/get_book_summary_by_currency", {"currency": "BTC", "kind": "option"})
        if "result" not in summaries: return None, None, {}

        summary_map = {s['instrument_name']: s for s in summaries['result']}

        calls = [i for i in instruments if i['option_type'] == 'call']
        puts = [i for i in instruments if i['option_type'] == 'put']

        best_call = None
        min_call_diff = 1.0

        for i in calls:
            s = summary_map.get(i['instrument_name'])
            if s and 'greeks' in s:
                delta = s['greeks'].get('delta', 0)
                if abs(delta - ENTRY_DELTA) < min_call_diff:
                    min_call_diff = abs(delta - ENTRY_DELTA)
                    best_call = i

        best_put = None
        min_put_diff = 1.0
        for i in puts:
            s = summary_map.get(i['instrument_name'])
            if s and 'greeks' in s:
                delta = s['greeks'].get('delta', 0)
                if abs(abs(delta) - ENTRY_DELTA) < min_put_diff:
                    min_put_diff = abs(abs(delta) - ENTRY_DELTA)
                    best_put = i

        return best_call, best_put, summary_map

    def execute_entry(self):
        with self.lock:
            if BOT_STATE["initial_entry_done"]: return

            print("[entry] Searching for entry opportunities...")
            idx_price = self.client.get_index_price()
            if not idx_price: return

            instrs, expiry = self.get_0dte_expiry()
            if not instrs: return

            call_leg, put_leg, summaries = self.find_strikes(instrs, idx_price)
            if not call_leg or not put_leg:
                print("[entry] Could not find suitable legs.")
                return

            print(f"[entry] Legs Found (Idx {idx_price}):\n  Call: {call_leg['instrument_name']}\n  Put:  {put_leg['instrument_name']}")

            # GET PRICES
            c_summ = summaries.get(call_leg['instrument_name'], {})
            p_summ = summaries.get(put_leg['instrument_name'], {})

            c_price = c_summ.get('mid_price') or c_summ.get('mark_price') or 0
            p_price = p_summ.get('mid_price') or p_summ.get('mark_price') or 0

            if c_price == 0 or p_price == 0:
                print("[entry] Prices are zero, aborting.")
                return

            if not ENABLE_LIVE_TRADING:
                print(f"[sim] Selling Strangle @ Call {c_price} / Put {p_price}")
                BOT_STATE["legs"]["C"] = {
                    "instrument": call_leg['instrument_name'],
                    "strike": call_leg['strike'],
                    "entry_price": c_price,
                    "reset_price": c_price,
                    "qty": FIXED_QTY
                }
                BOT_STATE["legs"]["P"] = {
                    "instrument": put_leg['instrument_name'],
                    "strike": put_leg['strike'],
                    "entry_price": p_price,
                    "reset_price": p_price,
                    "qty": FIXED_QTY
                }
                BOT_STATE["collected_credit"] = (c_price + p_price)
                BOT_STATE["initial_entry_done"] = True
                BOT_STATE["status"] = "monitoring"
                BOT_STATE["expiry"] = expiry

                # Subscribe
                self.ws.subscribe([
                    f"ticker.{call_leg['instrument_name']}.100ms",
                    f"ticker.{put_leg['instrument_name']}.100ms"
                ])
                log_trade_event("ENTRY", f"{call_leg['instrument_name']}+{put_leg['instrument_name']}", c_price+p_price, FIXED_QTY, "Initial Strangle")
            else:
                # Real Order Placement
                print("[real] Placing Sell Orders...")
                c_resp = self.client.place_order(call_leg['instrument_name'], FIXED_QTY, "sell", "market")
                p_resp = self.client.place_order(put_leg['instrument_name'], FIXED_QTY, "sell", "market")

                if "result" in c_resp and "result" in p_resp:
                    # Assume filled for simplicity or track order
                    print("[real] Orders placed.")
                    BOT_STATE["initial_entry_done"] = True
                    BOT_STATE["status"] = "monitoring"
                    # In real mode, we need to reconcile positions via WS/REST, but here we set initial state
                    BOT_STATE["legs"]["C"] = {
                        "instrument": call_leg['instrument_name'],
                        "strike": call_leg['strike'],
                        "entry_price": c_price,
                        "reset_price": c_price,
                        "qty": FIXED_QTY
                    }
                    BOT_STATE["legs"]["P"] = {
                        "instrument": put_leg['instrument_name'],
                        "strike": put_leg['strike'],
                        "entry_price": p_price,
                        "reset_price": p_price,
                        "qty": FIXED_QTY
                    }
                    BOT_STATE["expiry"] = expiry
                    self.ws.subscribe([
                        f"ticker.{call_leg['instrument_name']}.100ms",
                        f"ticker.{put_leg['instrument_name']}.100ms"
                    ])
                else:
                    print(f"[real] Order placement failed: {c_resp} {p_resp}")

    def adjust_position(self, leg_type, current_mid):
        with self.lock:
            loser = BOT_STATE["legs"][leg_type]
            winner_type = "P" if leg_type == "C" else "C"
            winner = BOT_STATE["legs"][winner_type]

            print(f"[adjust] >>> ADJUSTMENT TRIGGERED on {leg_type} leg <<<")
            print(f"  Loser: {loser['instrument']} | Reset Price: {loser['reset_price']} | Current: {current_mid}")

            winner_mid = self.tickers.get(winner["instrument"], {}).get("mid_price", winner["reset_price"])

            if not ENABLE_LIVE_TRADING:
                # Close Loser
                pnl = (loser["entry_price"] - current_mid) * loser["qty"]
                BOT_STATE["realized_pnl"] += pnl
                log_trade_event("CLOSE", loser['instrument'], current_mid, loser['qty'], "Adjustment Close", pnl)
                print(f"[sim] Closed Loser. PnL: {pnl:.6f}")

                # Open New Leg
                target_premium = winner_mid
                print(f"[adjust] Finding new {leg_type} leg @ ~{target_premium:.6f}")

                instrs, _ = self.get_0dte_expiry()
                if not instrs: return

                summaries = self.client.request("GET", "public/get_book_summary_by_currency", {"currency": "BTC", "kind": "option"})
                if "result" not in summaries: return
                summary_map = {s['instrument_name']: s for s in summaries['result']}

                candidates = [i for i in instrs if i['option_type'] == ('call' if leg_type == 'C' else 'put')]

                best_instr = None
                min_diff = 1.0
                best_price = 0.0

                for i in candidates:
                    s = summary_map.get(i['instrument_name'])
                    if s:
                        p = s.get('mid_price') or s.get('mark_price') or 0
                        if abs(p - target_premium) < min_diff:
                            min_diff = abs(p - target_premium)
                            best_instr = i
                            best_price = p

                if best_instr:
                    print(f"[sim] New Leg: {best_instr['instrument_name']} @ {best_price:.6f}")
                    BOT_STATE["legs"][leg_type] = {
                        "instrument": best_instr['instrument_name'],
                        "strike": best_instr['strike'],
                        "entry_price": best_price,
                        "reset_price": best_price,
                        "qty": loser["qty"]
                    }
                    BOT_STATE["collected_credit"] += best_price
                    log_trade_event("OPEN", best_instr['instrument_name'], best_price, loser['qty'], "Adjustment Open")

                    self.ws.subscribe([f"ticker.{best_instr['instrument_name']}.100ms"])
            else:
                # Real execution
                print(f"[real] Closing Loser {loser['instrument']}...")
                self.client.close_position(loser['instrument'])

                # Find New Leg
                instrs, _ = self.get_0dte_expiry()
                if instrs:
                    summaries = self.client.request("GET", "public/get_book_summary_by_currency", {"currency": "BTC", "kind": "option"})
                    if "result" in summaries:
                        summary_map = {s['instrument_name']: s for s in summaries['result']}
                        candidates = [i for i in instrs if i['option_type'] == ('call' if leg_type == 'C' else 'put')]

                        target_premium = winner_mid
                        best_instr = None
                        min_diff = 1.0
                        best_price = 0.0

                        for i in candidates:
                            s = summary_map.get(i['instrument_name'])
                            if s:
                                p = s.get('mid_price') or s.get('mark_price') or 0
                                if abs(p - target_premium) < min_diff:
                                    min_diff = abs(p - target_premium)
                                    best_instr = i
                                    best_price = p

                        if best_instr:
                            print(f"[real] Opening New Leg {best_instr['instrument_name']} @ ~{best_price:.6f}")
                            resp = self.client.place_order(best_instr['instrument_name'], loser['qty'], "sell", "market")
                            if "result" in resp:
                                BOT_STATE["legs"][leg_type] = {
                                    "instrument": best_instr['instrument_name'],
                                    "strike": best_instr['strike'],
                                    "entry_price": best_price, # Approx, real fill needed from ws trades
                                    "reset_price": best_price,
                                    "qty": loser["qty"]
                                }
                                # collected_credit and PnL will be updated by on_user_trade (fill listener)
                                self.ws.subscribe([f"ticker.{best_instr['instrument_name']}.100ms"])
                            else:
                                print(f"[real] Failed to open new leg: {resp}")

            # Check Compression
            c_strike = BOT_STATE["legs"]["C"]["strike"]
            p_strike = BOT_STATE["legs"]["P"]["strike"]
            width = abs(c_strike - p_strike)
            print(f"[adjust] New Width: {width} (Threshold: {COMPRESSION_WIDTH})")

            if width <= COMPRESSION_WIDTH and BOT_STATE["status"] != "iron_fly":
                self.convert_to_iron_fly()

    def convert_to_iron_fly(self):
        print("[hedge] Converting to IRON FLY...")
        idx = self.client.get_index_price()
        if not idx: return

        atm = round(idx / 500) * 500
        w_call = atm + IRON_FLY_WING_WIDTH
        w_put = atm - IRON_FLY_WING_WIDTH

        instrs, _ = self.get_0dte_expiry()
        wc_instr = next((i for i in instrs if i['strike'] == w_call and i['option_type'] == 'call'), None)
        wp_instr = next((i for i in instrs if i['strike'] == w_put and i['option_type'] == 'put'), None)

        if wc_instr and wp_instr:
            if not ENABLE_LIVE_TRADING:
                print(f"[sim] Buying Wings: {wc_instr['instrument_name']} & {wp_instr['instrument_name']}")
                BOT_STATE["wings"]["C"] = wc_instr['instrument_name']
                BOT_STATE["wings"]["P"] = wp_instr['instrument_name']
                BOT_STATE["status"] = "iron_fly"
                log_trade_event("HEDGE", f"{wc_instr['instrument_name']}+{wp_instr['instrument_name']}", 0, FIXED_QTY, "Iron Fly Wings")
            else:
                self.client.place_order(wc_instr['instrument_name'], FIXED_QTY, "buy", "market")
                self.client.place_order(wp_instr['instrument_name'], FIXED_QTY, "buy", "market")
                BOT_STATE["status"] = "iron_fly"

    def on_ticker_update(self, data):
        params = data.get("params", {})
        d = params.get("data", {})
        inst = d.get("instrument_name")
        mark = d.get("mark_price")
        best_bid = d.get("best_bid_price", 0)
        best_ask = d.get("best_ask_price", 0)
        mid = (best_bid + best_ask) / 2 if (best_bid and best_ask) else mark

        if inst:
            with self.lock:
                # 1. Tick Jump Check (Safety)
                old = self.tickers.get(inst, {}).get("mid_price")
                if old and old > 0 and abs(mid - old) / old > MAX_JUMP_PCT:
                    print(f"[safety] 🚨 PRICE JUMP DETECTED {inst}: {old} -> {mid} ({((mid-old)/old)*100:.1f}%)")
                    if ENABLE_LIVE_TRADING:
                         print("[safety] Flattening All...")
                         self.client.close_all(BOT_STATE["legs"])
                         sys.exit(1)

                self.tickers[inst] = {"mark": mark, "mid_price": mid}

                # 2. Leg Blowout Check (Safety)
                # "Single leg mtm loss > 2.5 × its original credit → emergency flatten"
                for leg in BOT_STATE["legs"].values():
                    if leg["instrument"] == inst:
                        entry = leg["entry_price"]
                        # Short PnL = Entry - Current
                        # Loss = Current - Entry
                        loss = mid - entry
                        if loss > (entry * LEG_BLOWOUT_MULT):
                            print(f"[safety] 🚨 LEG BLOWOUT {inst}: Loss {loss:.6f} > 2.5x Credit {entry:.6f}")
                            if ENABLE_LIVE_TRADING:
                                 self.client.close_all(BOT_STATE["legs"])
                                 sys.exit(1)

                # 3. Strategy Triggers
                if BOT_STATE["status"] in ["monitoring", "iron_fly"]:
                    # Check Adjustment (only if not yet iron fly? Strategy says "Repeat this... until compression complete")
                    if BOT_STATE["status"] == "monitoring":
                        for leg_type in ["C", "P"]:
                            leg = BOT_STATE["legs"].get(leg_type)
                            if leg and leg["instrument"] == inst:
                                if mid >= leg["reset_price"] * ADJUST_THRESHOLD:
                                    self.adjust_position(leg_type, mid)

    def on_user_trade(self, data):
        # Process fills to update PnL accurately
        trades = data.get("params", {}).get("data", [])
        for t in trades:
            inst = t.get("instrument_name")
            direction = t.get("direction") # buy/sell
            price = float(t.get("price", 0))
            amount = float(t.get("amount", 0))

            print(f"[trade] Filled {direction} {inst}: {amount} @ {price}")

            with self.lock:
                # Check if it's closing a leg or opening one
                matched = False
                for leg_type, leg in BOT_STATE["legs"].items():
                    if leg["instrument"] == inst:
                        matched = True
                        if direction == "buy": # Closing Short
                            pnl = (leg["entry_price"] - price) * amount
                            BOT_STATE["realized_pnl"] += pnl
                            print(f"[pnl] Realized PnL from close: {pnl:.6f}")
                        elif direction == "sell": # Opening/Adding Short
                            # Update entry price? Usually we open new legs.
                            # Or if it's the initial entry.
                            pass

                if not matched:
                    # Maybe new leg?
                    pass

                if direction == "sell":
                    BOT_STATE["collected_credit"] += (price * amount)

# ---------------------------- MAIN ----------------------------

def main():
    global API_KEY, API_SECRET, USE_TESTNET, ENABLE_LIVE_TRADING

    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", default=API_KEY)
    parser.add_argument("--api-secret", default=API_SECRET)
    parser.add_argument("--testnet", action="store_true")
    parser.add_argument("--live", action="store_true", help="Enable Real Orders")
    args = parser.parse_args()

    API_KEY = args.api_key
    API_SECRET = args.api_secret
    USE_TESTNET = args.testnet or USE_TESTNET
    ENABLE_LIVE_TRADING = args.live

    url = DERIBIT_TESTNET_WS if USE_TESTNET else DERIBIT_MAINNET_WS

    print(f"--- Deribit Gamma Scalper ---")
    print(f"Mode: {'REAL TRADING' if ENABLE_LIVE_TRADING else 'SIMULATION'}")
    print(f"Network: {'TESTNET' if USE_TESTNET else 'MAINNET'}")

    if not API_KEY or not API_SECRET:
        print("⚠️  WARNING: API Credentials Missing! Bot will run in Public-Only mode (limited functionality).")
        print("   Set DERIBIT_API_KEY and DERIBIT_API_SECRET env vars or use --api-key/--api-secret args.")

    client = DeribitClient(API_KEY, API_SECRET, testnet=USE_TESTNET)

    # WebSocket
    def ws_callback(msg):
        method = msg.get("method")
        if method == "subscription":
            channel = msg.get("params", {}).get("channel", "")
            if channel.startswith("ticker."):
                scalper.on_ticker_update(msg)
            elif channel.startswith("user.trades."):
                scalper.on_user_trade(msg)

    ws = DeribitWS(url, API_KEY, API_SECRET, ws_callback)
    ws.connect()

    scalper = GammaScalper(client, ws)

    # Graceful Exit
    def signal_handler(sig, frame):
        print("\n[stop] Stopping Bot...")
        if ENABLE_LIVE_TRADING:
            print("[stop] Closing all positions (Emergency)...")
            client.close_all(BOT_STATE["legs"])
            # Close wings too if any
            # client.close_all(BOT_STATE["wings"]) # Logic needed if stored similarly
        sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler)

    print("[main] Bot Running. Press Ctrl+C to stop.")

    while True:
        try:
            now = dt.now(datetime.timezone.utc)
            now_str = now.strftime("%H:%M")

            # WS Watchdog (Safety)
            if time.time() - ws.last_message_time > WS_TIMEOUT_SEC:
                 print(f"[safety] 🚨 WS DISCONNECT DETECTED (> {WS_TIMEOUT_SEC}s)!")
                 if ENABLE_LIVE_TRADING:
                     print("[safety] Flattening...")
                     client.close_all(BOT_STATE["legs"])
                     sys.exit(1)

            # Entry Check
            if not BOT_STATE["initial_entry_done"]:
                # Simple check: If time is right OR manual override (args)
                # For this demo, we check time.
                if now_str == ENTRY_TIME_UTC:
                    scalper.execute_entry()

            # Global PnL Check
            # Calc current floating PnL
            current_pnl = 0.0
            with scalper.lock:
                legs = BOT_STATE["legs"]
                if legs:
                    for leg in legs.values():
                        # Short position: Entry - Current
                        # If sim, use tickers. If real, use ws portfolio?
                        # Using tickers for consistency in logic
                        tick = scalper.tickers.get(leg["instrument"])
                        if tick:
                            curr = tick["mid_price"]
                            pnl = (leg["entry_price"] - curr) * leg["qty"]
                            current_pnl += pnl

            total_pnl = BOT_STATE["realized_pnl"] + current_pnl
            collected = BOT_STATE["collected_credit"]

            if collected > 0:
                pnl_pct = total_pnl / collected
                # Log Status periodically
                if now.second % 10 == 0:
                   print(f"[status] Net PnL: {total_pnl:.4f} ({pnl_pct*100:.1f}%) | Collected: {collected:.4f} | State: {BOT_STATE['status']}")

                # Global Exit
                if pnl_pct >= GLOBAL_TP_PCT:
                    print(f"[exit] GLOBAL TP HIT ({pnl_pct*100:.1f}%). Closing All.")
                    if ENABLE_LIVE_TRADING:
                        client.close_all(BOT_STATE["legs"])
                    sys.exit(0)
                if pnl_pct <= GLOBAL_SL_PCT:
                    print(f"[exit] GLOBAL SL HIT ({pnl_pct*100:.1f}%). Closing All.")
                    if ENABLE_LIVE_TRADING:
                        client.close_all(BOT_STATE["legs"])
                    sys.exit(0)

            # Time-Based Exit (< 30 min to expiry)
            if BOT_STATE.get("expiry"):
                mins_to_expiry = (BOT_STATE["expiry"] - now).total_seconds() / 60
                if mins_to_expiry < EXPIRY_CLOSE_MIN:
                    print(f"[exit] EXPIRY CLOSE ({mins_to_expiry:.1f} min left). Closing All.")
                    if ENABLE_LIVE_TRADING:
                        client.close_all(BOT_STATE["legs"])
                    sys.exit(0)

            time.sleep(1)

        except Exception as e:
            print(f"[main] Loop Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
