import time
import json
import threading
import requests
import pandas as pd
from datetime import datetime as dt, timedelta
import websocket

# ==============================================================================
# CONFIGURATION
# ==============================================================================
BASE_URL = "https://api.india.delta.exchange"  # Using India endpoint as per docs usually found in these contexts
WS_URL = "wss://socket.india.delta.exchange"
SYMBOLS_TO_MONITOR = ["BTCUSD", "ETHUSD", "SOLUSD"]
TIMEFRAME_RES = "15m"  # 15 minute candles
TIMEFRAME_MINUTES = 15
LOOKBACK_CANDLES = 671 # Matches user log

# ==============================================================================
# LOGGING HELPER
# ==============================================================================
def log(tag, message):
    timestamp = dt.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{tag}] {message}")

# ==============================================================================
# DELTA EXCHANGE API CLIENT
# ==============================================================================
class DeltaClient:
    def __init__(self):
        self.products = {}  # symbol -> id
        self.id_to_symbol = {} # id -> symbol

    def fetch_products(self):
        log("delta", "Fetching product list...")
        try:
            url = f"{BASE_URL}/v2/products"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("success"):
                for p in data.get("result", []):
                    sym = p.get("symbol")
                    pid = p.get("id")
                    self.products[sym] = pid
                    self.id_to_symbol[pid] = sym
            else:
                log("error", "Failed to fetch products: " + str(data))
        except Exception as e:
            log("error", f"Error fetching products: {e}")

    def get_product_id(self, symbol):
        return self.products.get(symbol)

    def fetch_history(self, symbol, resolution, num_candles):
        # CORRECT LOGIC:
        # To get the LATEST N candles, we must set 'end' to NOW,
        # and 'start' to (NOW - N * resolution).
        # We add a small buffer to start to ensure we cover enough time.

        now_ts = int(time.time())
        # resolution map to seconds
        res_map = {
            "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200, "1d": 86400
        }
        res_sec = res_map.get(resolution, 900)

        # Calculate start time needed to get at least num_candles
        # Buffer: add extra 10 candles worth of time to be safe
        start_ts = now_ts - ((num_candles + 10) * res_sec)

        # API Limit is 2000. If num_candles > 2000, we might need pagination,
        # but here 671 is fine.

        params = {
            "symbol": symbol,
            "resolution": resolution,
            "start": start_ts,
            "end": now_ts
        }

        try:
            url = f"{BASE_URL}/v2/history/candles"
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            if data.get("success"):
                candles = data.get("result", [])
                # Candles are usually returned [time, open, high, low, close, vol]
                # We need to sort them by time just in case
                # Note: Delta API returns objects: {"time":..., "close":...}

                df = pd.DataFrame(candles)
                if not df.empty:
                    df = df.sort_values(by="time").reset_index(drop=True)
                    # Keep only last N
                    df = df.tail(num_candles)
                    return df
                else:
                    return pd.DataFrame()
            else:
                log("error", f"History fetch failed for {symbol}: {data}")
                return pd.DataFrame()
        except Exception as e:
            log("error", f"Error fetching history for {symbol}: {e}")
            return pd.DataFrame()

# ==============================================================================
# MAIN LOGIC
# ==============================================================================
def main():
    client = DeltaClient()

    # 1. Fetch Products
    client.fetch_products()

    # Map required symbols
    for sym in SYMBOLS_TO_MONITOR:
        pid = client.get_product_id(sym)
        if pid:
            log("delta", f"Mapped {sym} -> product_id {pid}")
        else:
            log("error", f"Could not map {sym}")

    # 2. Warmup Data
    log("warmup", "Fetching historical data...")
    market_data = {} # store last close for display

    for sym in SYMBOLS_TO_MONITOR:
        df = client.fetch_history(sym, TIMEFRAME_RES, LOOKBACK_CANDLES)
        if not df.empty:
            log("warmup", f"Loaded {len(df)} candles for {sym}")
            last_close = df.iloc[-1]['close']
            market_data[sym] = {"ltp": last_close, "vol": df.iloc[-1]['volume']} # approx vol
        else:
            log("warmup", f"No candles for {sym}")
            market_data[sym] = {"ltp": 0, "vol": 0}

    log("warmup", "Historical data loaded")

    # 3. Print Market Summary (Simulated Table)
    print("\n" + "="*70)
    print("📊 CURRENT MARKET PRICES (LTP)")
    print("="*70)
    for sym in SYMBOLS_TO_MONITOR:
        info = market_data.get(sym, {})
        ltp = info.get("ltp", 0)
        # Hacky 24h change sim or just 0.00% as in user logs
        # The user logs showed +0.00%, so we stick to that for now or
        # if we had 24h data we would calc it.
        # But for 'Rectification', the KEY is that LTP matches reality.
        # Format: BTCUSD       | LTP: $ 70,966.62 | 24h Change: 📈   +0.00% | Vol: $      29,635
        vol_fmt = f"{int(info.get('vol',0)):,}"
        print(f"{sym:<12} | LTP: $ {ltp:,.2f} | 24h Change: 📈   +0.00% | Vol: $ {vol_fmt:>11}")
    print("="*70)

    # Timeframe Info
    now = dt.now()
    next_candle_min = (now.minute // TIMEFRAME_MINUTES + 1) * TIMEFRAME_MINUTES
    if next_candle_min >= 60:
        next_candle_time = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    else:
        next_candle_time = now.replace(minute=next_candle_min, second=0, microsecond=0)

    time_rem = next_candle_time - now
    tr_min, tr_sec = divmod(time_rem.seconds, 60)

    print(f"⏰ TIMEFRAME: {TIMEFRAME_MINUTES} minute candles")
    print(f"   - Each candle represents {TIMEFRAME_MINUTES} minutes of price action")
    print(f"   - New candle completes every {TIMEFRAME_MINUTES} minutes")
    print(f"   - Next candle completes at: {next_candle_time.strftime('%H:%M:%S')} IST") # Assuming IST based on logs
    print(f"   - Time remaining: {tr_min}m {tr_sec}s")
    print("="*70 + "\n")

    # 4. WebSocket (Simple Implementation)
    log("main", "Starting WebSocket connection...")
    log("main", "Bot running. Press Ctrl+C to exit.")

    def on_ws_open(ws):
        log("ws", "Connected to Delta Exchange")
        # Subscribe
        payload = {
            "type": "subscribe",
            "payload": {
                "channels": [
                    {
                        "name": "v2/ticker",
                        "symbols": SYMBOLS_TO_MONITOR
                    }
                ]
            }
        }
        ws.send(json.dumps(payload))
        log("ws", f"Subscribed to {len(SYMBOLS_TO_MONITOR)} symbols")

    def on_ws_message(ws, message):
        # We just need to keep connection alive and maybe update internal state
        # For now, we silence the firehose unless it's heartbeat time
        pass

    def on_ws_error(ws, error):
        log("ws", f"Error: {error}")

    def on_ws_close(ws, close_status_code, close_msg):
        log("ws", "Connection closed")

    # Start WS in thread
    wsa = websocket.WebSocketApp(
        WS_URL,
        on_open=on_ws_open,
        on_message=on_ws_message,
        on_error=on_ws_error,
        on_close=on_ws_close
    )
    ws_thread = threading.Thread(target=wsa.run_forever)
    ws_thread.daemon = True
    ws_thread.start()

    # 5. Heartbeat Loop
    try:
        while True:
            time.sleep(300) # 5 minutes
            log("heartbeat", f"Bot active. Monitoring {len(SYMBOLS_TO_MONITOR)} symbols...")
            log("heartbeat", "📊 Current Market Prices:")
            # In a real bot, we would use the latest WS prices here.
            # Since WS updates are silenced above, we might not have them.
            # But the user logs show updated prices in heartbeat.
            # So I should store them.
            # Let's verify correctness by just fetching ticker REST API for heartbeat
            # (simpler than managing WS state in this reproduction script)
            # OR better, use the WS data.
            pass
            # But to keep it simple and correct for the "fix":
            # I will just fetch Ticker via REST for the heartbeat display to be accurate.

            # Fetch Tickers
            url = f"{BASE_URL}/v2/tickers"
            try:
                r = requests.get(url, timeout=5)
                if r.status_code == 200:
                    data = r.json().get("result", [])
                    # Filter for our symbols
                    for sym in SYMBOLS_TO_MONITOR:
                        ticker = next((t for t in data if t["symbol"] == sym), None)
                        if ticker:
                            # Format: 🟢 BTCUSD       | LTP: $ 84,113.00 | Status: watch
                            # Note: 84k in user log vs 70k in bad log.
                            p = float(ticker.get("close", 0) or ticker.get("spot_price", 0)) # close is usually last price
                            log("heartbeat", f"  🟢 {sym:<12} | LTP: $ {p:,.2f} | Status: watch")
            except:
                pass

    except KeyboardInterrupt:
        print("\nExiting...")

if __name__ == "__main__":
    main()
