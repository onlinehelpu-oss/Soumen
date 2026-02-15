import time
import json
import hmac
import hashlib
import requests
import threading
import sys
import os
import argparse
from urllib.parse import urlencode
from datetime import datetime, timezone, timedelta
import websocket

# Configuration
API_URL = "https://api.india.delta.exchange"
WS_URL = "wss://socket.india.delta.exchange"

# Strategy Defaults
ENTRY_TIME_UTC_START = "00:00"
ENTRY_TIME_UTC_END = "23:59"
ENTRY_DELTA = 0.18
ADJUST_TRIGGER = 1.30  # 30% increase
COMPRESSION_WIDTH = 400
PROFIT_TARGET = 0.45
STOP_LOSS = -0.35
LEG_MAX_LOSS = 2.5
CONTRACT_SIZE = 1  # 1 contract per leg
PRINT_INTERVAL = 3  # Seconds


class DeltaClient:
    def __init__(self, api_key, api_secret, base_url=API_URL):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.session = requests.Session()

    def _generate_signature(self, method, endpoint, payload):
        timestamp = str(int(time.time()))
        if method == "GET":
            query_string = urlencode(payload) if payload else ""
            message = method + timestamp + endpoint + query_string
        else:
            body = json.dumps(payload, separators=(',', ':')) if payload else ""
            message = method + timestamp + endpoint + body

        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        return signature, timestamp

    def request(self, method, endpoint, params=None, payload=None):
        url = self.base_url + endpoint

        data_str = json.dumps(payload, separators=(',', ':')) if payload else None

        signature, timestamp = self._generate_signature(method, endpoint, params if method == "GET" else payload)
        headers = {
            "api-key": self.api_key,
            "signature": signature,
            "timestamp": timestamp,
            "Content-Type": "application/json"
        }

        try:
            if method == "GET":
                response = self.session.get(url, params=params, headers=headers)
            else:
                response = self.session.post(url, data=data_str, headers=headers)

            if response.status_code >= 400:
                # Suppress 429 or 500 noise unless critical
                if response.status_code not in [429, 502, 503]:
                    print(f"API Error {response.status_code}: {response.text}")
                return None
            return response.json()
        except requests.exceptions.RequestException as e:
            # print(f"API Request Error: {e}")
            return None

    def get_tickers(self):
        return self.request("GET", "/v2/tickers")

    def get_ticker(self, symbol):
        return self.request("GET", "/v2/tickers", params={"symbol": symbol})

    def place_order(self, product_id, size, side, order_type="market_order", limit_price=None):
        payload = {
            "product_id": int(product_id),
            "size": int(size),
            "side": side,
            "order_type": order_type,
            "time_in_force": "ioc" if order_type == "market_order" else "gtc"
        }
        if limit_price:
            payload["limit_price"] = str(limit_price)

        return self.request("POST", "/v2/orders", payload=payload)

    def cancel_order(self, product_id, order_id):
        payload = {
            "product_id": int(product_id),
            "order_id": int(order_id)
        }
        return self.request("DELETE", "/v2/orders", payload=payload)

    def get_index_price(self, symbol="BTC"):
        t = self.get_ticker(f"{symbol}USDT")
        if t and t.get('result'):
            return float(t['result'][0]['spot_price'])
        return None


class StrategyState:
    WAITING = "WAITING"
    STRANGLE_OPEN = "STRANGLE_OPEN"
    COMPRESSING = "COMPRESSING"
    IRON_FLY = "IRON_FLY"
    EXITED = "EXITED"


class GammaBot:
    def __init__(self, api_key, api_secret, dry_run=False, force_entry=False):
        self.client = DeltaClient(api_key, api_secret)
        self.dry_run = dry_run
        self.force_entry = force_entry
        self.state = StrategyState.WAITING

        self.positions = {}
        self.cumulative_credit = 0.0
        self.realized_pnl = 0.0

        self.tickers = {}
        self.products = {}
        self.symbol_map = {}
        self.initial_subscription_list = []

        self.lock = threading.RLock()
        self.ws = None
        self.ws_thread = None
        self.should_stop = False
        self.last_message_time = time.time()

        # Display cache
        self.last_print_time = 0
        self.cached_spot = 0.0

        # Sudden Jump Detection
        self.last_mid_prices = {}

    def log(self, msg):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def send_alert(self, msg):
        self.log(f"ALERT: {msg}")

    def load_products(self):
        self.log("Loading products...")
        tickers = self.client.get_tickers()
        if tickers and tickers.get('result'):
            for t in tickers['result']:
                self.products[t['product_id']] = t
                self.symbol_map[t['symbol']] = t['product_id']
                self.tickers[t['symbol']] = t
        self.log(f"Loaded {len(self.products)} products.")

        target_date = self.get_0dte_expiry_date()
        symbols_to_sub = []
        for sym, t in self.tickers.items():
            if t.get('underlying_asset_symbol') == 'BTC' and t.get('contract_type') in ['call_options', 'put_options']:
                info = self.parse_symbol(sym)
                if info and info['date'] == target_date:
                    symbols_to_sub.append(sym)

        symbols_to_sub.append("BTCUSDT")
        self.initial_subscription_list = symbols_to_sub
        self.log(f"Identified {len(symbols_to_sub)} BTC options for expiry {target_date} to monitor.")

    def get_0dte_expiry_date(self):
        # Delta Exchange Daily Options expire at 12:00 UTC (approx).
        # If current time > 12:00 UTC, return tomorrow's date.
        # Otherwise return today's date.
        now_utc = datetime.now(timezone.utc)
        if now_utc.hour >= 12:
            target_date = now_utc + timedelta(days=1)
        else:
            target_date = now_utc
        return target_date.strftime("%d%m%y")

    def parse_symbol(self, symbol):
        parts = symbol.split('-')
        if len(parts) >= 4:
            try:
                return {
                    'type': 'call' if parts[0] == 'C' else 'put',
                    'asset': parts[1],
                    'strike': float(parts[2]),
                    'date': parts[3]
                }
            except:
                return None
        return None

    # --- WebSocket Handling ---
    def on_ws_message(self, ws, message):
        self.last_message_time = time.time()
        try:
            data = json.loads(message)
            if data.get('type') == 'v2/ticker':
                if 'symbol' in data and 'mark_price' in data:
                    with self.lock:
                        sym = data['symbol']
                        if sym not in self.tickers:
                            self.tickers[sym] = {}
                        self.tickers[sym].update(data)

                        if sym == "BTCUSDT":
                            self.cached_spot = float(data.get('spot_price', 0) or data.get('mark_price', 0))

                        self.on_tick(sym)
        except Exception as e:
            # self.log(f"WS Error: {e}")
            pass

    def on_ws_error(self, ws, error):
        self.log(f"WS Error: {error}")

    def on_ws_close(self, ws, close_status_code, close_msg):
        self.log("WS Closed")

    def on_ws_open(self, ws):
        self.log("WS Open")
        if self.initial_subscription_list:
            chunk_size = 50
            for i in range(0, len(self.initial_subscription_list), chunk_size):
                self.subscribe(self.initial_subscription_list[i:i + chunk_size])
        else:
            self.subscribe(["BTCUSDT"])

    def subscribe(self, symbols):
        if not self.ws: return
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

    def start_ws(self):
        self.ws = websocket.WebSocketApp(
            WS_URL,
            on_open=self.on_ws_open,
            on_message=self.on_ws_message,
            on_error=self.on_ws_error,
            on_close=self.on_ws_close
        )
        self.ws_thread = threading.Thread(target=self.ws.run_forever)
        self.ws_thread.daemon = True
        self.ws_thread.start()

    # --- Market Data Helpers ---
    def get_mid_price(self, symbol):
        t = self.tickers.get(symbol)
        if not t: return 0.0

        quotes = t.get('quotes', {})
        bid = float(quotes.get('best_bid', 0)) if quotes else float(t.get('best_bid', 0) or 0)
        ask = float(quotes.get('best_ask', 0)) if quotes else float(t.get('best_ask', 0) or 0)

        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        return float(t.get('mark_price', 0))

    # --- Output & Display ---
    def print_status_table(self):
        now = time.time()
        if now - self.last_print_time < PRINT_INTERVAL:
            return
        self.last_print_time = now

        spot = self.cached_spot
        if spot == 0:
            t = self.tickers.get("BTCUSDT")
            if t: spot = float(t.get('spot_price', 0) or t.get('mark_price', 0))

        if spot == 0: return  # Wait for data

        # Round ATM to nearest 100 for BTC
        atm_strike = int(round(spot / 100.0) * 100)

        # Clear screen (ANSI) - Optional, maybe just print new block
        # print("\033[H\033[J", end="")
        print("\n" * 2)

        print(f"Live LTP for BTCUSDT is: {spot:.1f}")
        print(f"ATM strike is: {atm_strike}")
        print(f"(Using expiry: {self.get_0dte_expiry_date()})")
        print(f"State: {self.state}")

        # Calculate PnL Snapshot
        unrealized = 0.0
        for leg, pos in self.positions.items():
            curr = self.get_mid_price(pos['symbol'])
            is_long = leg.startswith('long_')
            if is_long:
                pnl = (curr - pos['entry_price']) * pos['size']
            else:
                pnl = (pos['entry_price'] - curr) * pos['size']
            unrealized += pnl

        net_pnl = self.realized_pnl + unrealized
        print(
            f"Net PnL: {net_pnl:.2f} | Realized: {self.realized_pnl:.2f} | Unrealized: {unrealized:.2f} | Cum Credit: {self.cumulative_credit:.2f}")

        # Build Table
        # Columns: CE LTP, CE Delta, CE Gamma, CE IV%, CE OI | STRIKE | PE ...
        header = f"{'CE LTP':>8} {'CE Δ':>8} {'CE Γ':>8} {'CE IV%':>8} {'CE OI':>10} | {'STRIKE':^8} | {'PE LTP':>8} {'PE Δ':>8} {'PE Γ':>8} {'PE IV%':>8} {'PE OI':>10}"
        print(f"--- Option Chain for BTC (ATM +/- 8 strikes) ---")
        print(header)
        print("-" * len(header))

        target_date = self.get_0dte_expiry_date()

        # Collect relevant tickers
        chain_data = {}  # strike -> {'C': ticker, 'P': ticker}

        with self.lock:
            all_tickers = list(self.tickers.values())

        for t in all_tickers:
            if t.get('underlying_asset_symbol') == 'BTC' and t.get('contract_type') in ['call_options', 'put_options']:
                info = self.parse_symbol(t['symbol'])
                if info and info['date'] == target_date:
                    strike = int(info['strike'])
                    if strike not in chain_data: chain_data[strike] = {}

                    typ = 'C' if info['type'] == 'call' else 'P'
                    chain_data[strike][typ] = t

        # Sort strikes
        strikes = sorted(chain_data.keys())
        if not strikes:
            print("No strikes found for target date.")
            return

        # Find index of ATM
        closest_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - atm_strike))

        start_idx = max(0, closest_idx - 8)
        end_idx = min(len(strikes), closest_idx + 9)

        subset_strikes = strikes[start_idx:end_idx]

        for k in subset_strikes:
            row_data = chain_data[k]
            c = row_data.get('C', {})
            p = row_data.get('P', {})

            # CE Data
            c_ltp = self.get_mid_price(c.get('symbol')) if c else 0.0
            c_greeks = c.get('greeks') or {}
            c_delta = float(c_greeks.get('delta', 0))
            c_gamma = float(c_greeks.get('gamma', 0))
            c_iv = float(c.get('quotes', {}).get('mark_iv', 0) or 0) * 100  # usually decimal
            c_oi = int(float(c.get('oi_contracts', 0) or 0))  # Using contracts, not value

            # PE Data
            p_ltp = self.get_mid_price(p.get('symbol')) if p else 0.0
            p_greeks = p.get('greeks') or {}
            p_delta = float(p_greeks.get('delta', 0))
            p_gamma = float(p_greeks.get('gamma', 0))
            p_iv = float(p.get('quotes', {}).get('mark_iv', 0) or 0) * 100
            p_oi = int(float(p.get('oi_contracts', 0) or 0))

            # Fmt
            c_str = f"{c_ltp:8.2f} {c_delta:8.4f} {c_gamma:8.6f} {c_iv:8.2f} {c_oi:10,}"
            p_str = f"{p_ltp:8.2f} {p_delta:8.4f} {p_gamma:8.6f} {p_iv:8.2f} {p_oi:10,}"

            print(f"{c_str} | {k:^8} | {p_str}")

        print("")

    # --- Strategy Logic ---
    def on_tick(self, symbol):
        # Trigger display update
        self.print_status_table()

        # Sudden Jump Check (> 80% from previous tick)
        curr_mid = self.get_mid_price(symbol)
        if curr_mid > 0:
            last_mid = self.last_mid_prices.get(symbol, curr_mid)
            if last_mid > 0 and curr_mid / last_mid > 1.80:
                self.send_alert(f"SUDDEN JUMP DETECTED (>80%) on {symbol}. Flattening!")
                self.flatten_all()
                return
            self.last_mid_prices[symbol] = curr_mid

        if self.state == StrategyState.WAITING:
            now_utc = datetime.now(timezone.utc).strftime("%H:%M")
            should_enter = (ENTRY_TIME_UTC_START <= now_utc <= ENTRY_TIME_UTC_END) or self.force_entry

            if should_enter:
                if not self.positions:
                    self.enter_initial_positions()
                    if self.force_entry:
                        self.force_entry = False

        elif self.state in [StrategyState.STRANGLE_OPEN, StrategyState.COMPRESSING]:
            self.check_adjustments()
            self.check_compression()
            self.check_global_pnl()

        elif self.state == StrategyState.IRON_FLY:
            self.check_global_pnl()

        # Time to Expiry Check (< 30 mins)
        target_date_str = self.get_0dte_expiry_date()
        # Parse target date and check if current time is within 30 mins of 12:00 UTC on that date
        try:
            # target_date_str is ddmmyy
            expiry_dt = datetime.strptime(target_date_str + " 12:00", "%d%m%y %H:%M").replace(tzinfo=timezone.utc)
            now_utc_full = datetime.now(timezone.utc)
            if now_utc_full < expiry_dt and (expiry_dt - now_utc_full) < timedelta(minutes=30):
                self.send_alert("Less than 30 minutes to expiry. Flattening.")
                self.flatten_all()
        except Exception as e:
            # self.log(f"Expiry check error: {e}")
            pass

    def find_entry_candidates(self):
        candidates = []
        target_date = self.get_0dte_expiry_date()

        with self.lock:
            items = list(self.tickers.values())

        for t in items:
            if t.get('contract_type') in ['call_options', 'put_options'] and t.get('underlying_asset_symbol') == 'BTC':
                info = self.parse_symbol(t['symbol'])
                if info and info['date'] == target_date:
                    greeks = t.get('greeks')
                    delta = float(greeks['delta']) if greeks and 'delta' in greeks else 0.0
                    if delta == 0.0: continue

                    t['parsed_strike'] = info['strike']
                    t['parsed_delta'] = delta
                    candidates.append(t)

        if not candidates:
            return None, None

        calls = [c for c in candidates if c['contract_type'] == 'call_options']
        puts = [p for p in candidates if p['contract_type'] == 'put_options']

        calls.sort(key=lambda x: abs(abs(x['parsed_delta']) - ENTRY_DELTA))
        puts.sort(key=lambda x: abs(abs(x['parsed_delta']) - ENTRY_DELTA))

        best_call = calls[0] if calls else None
        best_put = puts[0] if puts else None

        return best_call, best_put

    def enter_initial_positions(self):
        self.send_alert("Attempting Initial Entry...")
        call, put = self.find_entry_candidates()

        if not call or not put:
            self.log("No suitable candidates found yet.")
            return

        self.log(
            f"Selected: {call['symbol']} (Delta {call.get('parsed_delta')}) & {put['symbol']} (Delta {put.get('parsed_delta')})")

        # Sell Strangle (Short Call, Short Put)
        # leg_name used to track positions: 'call', 'put'
        if self.execute_trade(call, "sell", "call") and self.execute_trade(put, "sell", "put"):
            self.state = StrategyState.STRANGLE_OPEN
            self.send_alert("Strangle Entered Successfully.")

    def execute_trade(self, ticker, action, leg_name):
        # Determine intent:
        # action="sell", leg_name='call' or 'put' -> OPEN SHORT (Credit)
        # action="buy", leg_name='call' or 'put' -> CLOSE SHORT (Debit/Realized PnL)
        # action="buy", leg_name='long_call' or 'long_put' -> OPEN LONG (Debit)
        # action="sell", leg_name='long_call' or 'long_put' -> CLOSE LONG (Credit/Realized PnL)

        symbol = ticker['symbol']
        product_id = ticker['product_id']
        price = self.get_mid_price(symbol)
        if price == 0: price = float(ticker.get('mark_price', 0))

        self.log(f"{action.upper()} {symbol} @ {price} (Leg: {leg_name})")

        if not self.dry_run:
            side = "sell" if action == "sell" else "buy"
            resp = self.client.place_order(product_id, CONTRACT_SIZE, side)
            if not resp or (not resp.get('result') and not resp.get('success')):
                self.log(f"Order Failed: {resp}")
                return False

        # Internal State Management
        is_long_leg = leg_name.startswith('long_')

        # OPENING POSITIONS
        if (action == "sell" and not is_long_leg) or (action == "buy" and is_long_leg):
            # OPEN SHORT or OPEN LONG
            if leg_name in self.positions:
                self.log(f"Warning: Overwriting position {leg_name}")

            self.positions[leg_name] = {
                'symbol': symbol,
                'product_id': product_id,
                'entry_price': price,
                'last_reset_price': price,
                'size': CONTRACT_SIZE,
                'strike': ticker.get('parsed_strike', 0)
            }

            if not is_long_leg:  # Short Strangle leg
                self.cumulative_credit += (price * CONTRACT_SIZE)
            else:
                # Long leg (Iron Fly wings) costs money, does not add to credit.
                # Credit is "collected premiums ever sold". Wings are bought.
                pass

        # CLOSING POSITIONS
        elif (action == "buy" and not is_long_leg) or (action == "sell" and is_long_leg):
            # CLOSE SHORT or CLOSE LONG
            if leg_name in self.positions:
                pos = self.positions[leg_name]

                # Calculate PnL
                # For Short: (Entry - Exit) * Size
                # For Long: (Exit - Entry) * Size
                if not is_long_leg:
                    pnl = (pos['entry_price'] - price) * pos['size']
                else:
                    pnl = (price - pos['entry_price']) * pos['size']

                self.realized_pnl += pnl
                del self.positions[leg_name]
            else:
                self.log(f"Warning: Closing position {leg_name} not found in state.")

        return True

    def check_adjustments(self):
        triggered_leg = None
        for leg in ['call', 'put']:
            if leg not in self.positions: continue
            pos = self.positions[leg]
            curr_price = self.get_mid_price(pos['symbol'])

            if curr_price >= pos['last_reset_price'] * ADJUST_TRIGGER:
                self.send_alert(
                    f"Trigger! {leg.upper()} premium {curr_price:.4f} >= {pos['last_reset_price']:.4f} * {ADJUST_TRIGGER}")
                triggered_leg = leg
                break

        if triggered_leg:
            winning_leg = triggered_leg  # The one that hit 30% (Tested side)
            losing_leg = 'put' if winning_leg == 'call' else 'call'  # The Collapsed side
            self.perform_adjustment(winning_leg, losing_leg)

    def perform_adjustment(self, winning_leg, losing_leg):
        self.send_alert(f"Adjusting: Keeping {winning_leg}, Rolling {losing_leg}")

        # Strategy:
        # a. KEEP winning leg (tested side)
        # b. CLOSE losing leg (collapsed side)
        # c. SELL new option on SAME side as closed leg (losing_leg) at target premium

        winning_pos = self.positions[winning_leg]
        winning_price = self.get_mid_price(winning_pos['symbol'])

        if losing_leg in self.positions:
            pos = self.positions[losing_leg]
            ticker = self.tickers.get(pos['symbol'])
            # Close losing leg (Short) -> Buy to close
            self.execute_trade(ticker, "buy", losing_leg)

        new_ticker = self.find_ticker_by_premium(losing_leg, winning_price)
        if new_ticker:
            self.send_alert(f"Rolling into {new_ticker['symbol']} (Target Premium: {winning_price})")
            # Open new Short leg
            self.execute_trade(new_ticker, "sell", losing_leg)
            # Check for compression completion logic handled in separate loop
        else:
            self.send_alert("CRITICAL: Could not find new leg to roll into!")

    def find_ticker_by_premium(self, leg_type, target_premium):
        contract_type = 'call_options' if leg_type == 'call' else 'put_options'
        target_date = self.get_0dte_expiry_date()

        best_ticker = None
        min_diff = float('inf')

        with self.lock:
            candidates = list(self.tickers.values())

        for t in candidates:
            if t.get('contract_type') == contract_type and t.get('underlying_asset_symbol') == 'BTC':
                info = self.parse_symbol(t['symbol'])
                if info and info['date'] == target_date:
                    price = self.get_mid_price(t['symbol'])
                    diff = abs(price - target_premium)
                    if diff < min_diff:
                        min_diff = diff
                        best_ticker = t
                        best_ticker['parsed_strike'] = info['strike']

        return best_ticker

    def check_compression(self):
        if 'call' in self.positions and 'put' in self.positions:
            c_strike = self.positions['call']['strike']
            p_strike = self.positions['put']['strike']
            spread = abs(c_strike - p_strike)

            spot = self.client.get_index_price()
            if not spot: return

            c_dist = abs(c_strike - spot) / spot
            p_dist = abs(p_strike - spot) / spot

            if spread <= COMPRESSION_WIDTH and c_dist < 0.02 and p_dist < 0.02:
                self.send_alert(f"Compression Complete (Spread {spread}). Executing Final Hedge.")
                self.execute_iron_fly_hedge(spot)

    def execute_iron_fly_hedge(self, spot):
        c_prem = self.get_mid_price(self.positions['call']['symbol'])
        p_prem = self.get_mid_price(self.positions['put']['symbol'])
        width = c_prem + p_prem

        # Strategy: Buy 1 Call at ATM + width, Buy 1 Put at ATM - width
        c_wing_strike = spot + width
        p_wing_strike = spot - width

        c_wing = self.find_closest_strike('call', c_wing_strike)
        p_wing = self.find_closest_strike('put', p_wing_strike)

        if c_wing and p_wing:
            self.send_alert(f"Buying Wings: {c_wing['symbol']} & {p_wing['symbol']}")
            # Open Long Wings -> Buy to Open
            self.execute_trade(c_wing, "buy", "long_call")
            self.execute_trade(p_wing, "buy", "long_put")

            self.state = StrategyState.IRON_FLY
        else:
            self.send_alert("Could not find wings!")

    def find_closest_strike(self, leg_type, target_strike):
        contract_type = 'call_options' if leg_type == 'call' else 'put_options'
        target_date = self.get_0dte_expiry_date()

        best = None
        min_dist = float('inf')

        with self.lock:
            candidates = list(self.tickers.values())

        for t in candidates:
            if t.get('contract_type') == contract_type and t.get('underlying_asset_symbol') == 'BTC':
                info = self.parse_symbol(t['symbol'])
                if info and info['date'] == target_date:
                    dist = abs(info['strike'] - target_strike)
                    if dist < min_dist:
                        min_dist = dist
                        best = t
                        best['parsed_strike'] = info['strike']
        return best

    def check_global_pnl(self):
        unrealized = 0.0

        for leg, pos in list(self.positions.items()):
            curr = self.get_mid_price(pos['symbol'])
            is_long = leg.startswith('long_')

            if is_long:
                pnl = (curr - pos['entry_price']) * pos['size']
            else:
                pnl = (pos['entry_price'] - curr) * pos['size']

            unrealized += pnl

            # Emergency Leg Exit: MTM loss > 2.5 * entry credit (Only for Shorts)
            if not is_long:
                entry_credit = pos['entry_price'] * pos['size']
                if pnl < -LEG_MAX_LOSS * entry_credit:
                    self.send_alert(f"EMERGENCY EXIT: Leg {leg} loss exceeded limit!")
                    self.flatten_all()
                    return

        net_pnl = self.realized_pnl + unrealized

        if self.cumulative_credit > 0:
            roi = net_pnl / self.cumulative_credit

            if roi >= PROFIT_TARGET:
                self.send_alert(f"Take Profit Hit! (+{roi * 100:.1f}%)")
                self.flatten_all()
            elif roi <= STOP_LOSS:
                self.send_alert(f"Stop Loss Hit! ({roi * 100:.1f}%)")
                self.flatten_all()

    def flatten_all(self):
        self.send_alert("FLATTENING ALL POSITIONS")
        for leg in list(self.positions.keys()):
            pos = self.positions[leg]
            ticker = self.tickers.get(pos['symbol'])
            is_long = leg.startswith('long_')

            # Close Short -> Buy
            # Close Long -> Sell
            action = "sell" if is_long else "buy"
            self.execute_trade(ticker, action, leg)

        self.state = StrategyState.EXITED
        self.should_stop = True

    def run(self):
        self.load_products()
        self.start_ws()

        self.log("Bot Running. Press Ctrl+C to stop.")

        now_utc = datetime.now(timezone.utc).strftime("%H:%M")
        self.log(f"Current UTC Time: {now_utc}")
        self.log(f"Scheduled Entry: {ENTRY_TIME_UTC_START} - {ENTRY_TIME_UTC_END}")

        if self.force_entry:
            self.log("Force Entry Enabled: Will attempt entry immediately.")
        else:
            self.log("Waiting for entry time (or use --force-entry to bypass)...")

        self.last_message_time = time.time()

        try:
            while not self.should_stop:
                time.sleep(1)

                # Heartbeat check for WebSocket
                if time.time() - self.last_message_time > 10:
                    self.send_alert("WARNING: WebSocket Heartbeat Lost (> 10s). Flattening.")
                    self.flatten_all()

                if os.path.exists("TRIGGER_ENTRY"):
                    self.log("Manual Trigger Detected")
                    if not self.positions:
                        self.enter_initial_positions()
                    os.remove("TRIGGER_ENTRY")

                if os.path.exists("STOP_BOT"):
                    self.flatten_all()
                    os.remove("STOP_BOT")

        except KeyboardInterrupt:
            self.log("Stopping...")
        finally:
            if self.ws: self.ws.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", help="API Key")
    parser.add_argument("--secret", help="API Secret")
    parser.add_argument("--dry-run", action="store_true", help="Dry Run Mode")
    parser.add_argument("--force-entry", action="store_true", help="Force Entry Immediately (Bypass Time Check)")
    args = parser.parse_args()

    # 1. Try Args
    key = args.key
    secret = args.secret

    # 2. Try Env Vars
    if not key: key = os.environ.get("DELTA_API_KEY")
    if not secret: secret = os.environ.get("DELTA_API_SECRET")

    # 3. Fallback to Hardcoded Keys (User Request)
    # Keys removed for security. Please use environment variables or CLI arguments.
    if not key: key = ""
    if not secret: secret = ""

    if not key or not secret:
        print("No API Credentials found. Forcing Dry Run.")
        args.dry_run = True
        key = "test"
        secret = "test"
    else:
        # Masked Key Log
        masked_key = key[:4] + "*" * (len(key) - 8) + key[-4:] if len(key) > 8 else "****"
        print(f"Using API Key: {masked_key}")

    bot = GammaBot(key, secret, dry_run=args.dry_run, force_entry=args.force_entry)
    bot.run()