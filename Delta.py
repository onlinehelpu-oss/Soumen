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
BASE_URL = "https://api.india.delta.exchange"
WS_URL = "wss://socket.india.delta.exchange"
SYMBOLS_TO_MONITOR = ["BTCUSD", "ETHUSD", "SOLUSD"]
TIMEFRAME_RES = "15m"  # 15 minute candles
TIMEFRAME_MINUTES = 15
LOOKBACK_CANDLES = 671

# Strategy Params (Fast Slow Paper)
EMA_FAST = 9
EMA_SLOW = 15  # Updated to match image

# ==============================================================================
# LOGGING HELPER
# ==============================================================================
def log(tag, message):
    timestamp = dt.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{tag}] {message}")

# ==============================================================================
# DATA & INDICATORS
# ==============================================================================
def compute_ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

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
                    if sym in SYMBOLS_TO_MONITOR:
                        self.products[sym] = pid
                        self.id_to_symbol[pid] = sym
                        log("delta", f"Mapped {sym} -> product_id {pid}")
            else:
                log("error", "Failed to fetch products: " + str(data))
        except Exception as e:
            log("error", f"Error fetching products: {e}")

    def get_product_id(self, symbol):
        return self.products.get(symbol)

    def fetch_history(self, symbol, resolution, num_candles):
        now_ts = int(time.time())
        res_map = {
            "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200, "1d": 86400
        }
        res_sec = res_map.get(resolution, 60)

        # Buffer: add extra 20 candles for EMA warmup
        start_ts = now_ts - ((num_candles + 20) * res_sec)

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
                df = pd.DataFrame(candles)
                if not df.empty:
                    # Rename columns to standard names if needed, usually: t, o, h, l, c, v
                    # Delta returns: close, high, low, open, time, volume
                    df = df.sort_values(by="time").reset_index(drop=True)
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
# STRATEGY ENGINE
# ==============================================================================
class StrategyEngine:
    def __init__(self, client):
        self.client = client
        self.data = {} # symbol -> DataFrame
        self.positions = {} # symbol -> side (1=Long, -1=Short, 0=None)
        self.live_prices = {} # symbol -> float

    def warmup(self):
        log("warmup", "Fetching historical data...")
        for sym in SYMBOLS_TO_MONITOR:
            df = self.client.fetch_history(sym, TIMEFRAME_RES, LOOKBACK_CANDLES)
            if not df.empty:
                # Calculate Indicators
                df['ema_fast'] = compute_ema(df['close'], EMA_FAST)
                df['ema_slow'] = compute_ema(df['close'], EMA_SLOW)
                self.data[sym] = df
                self.positions[sym] = 0
                self.live_prices[sym] = df.iloc[-1]['close']
                log("warmup", f"Loaded {len(df)} candles for {sym}")
            else:
                log("warmup", f"No candles for {sym}")

        log("warmup", "Historical data loaded")
        self.print_market_summary()

    def print_market_summary(self):
        print("\n" + "="*70)
        print("📊 CURRENT MARKET PRICES (LTP)")
        print("="*70)
        for sym in SYMBOLS_TO_MONITOR:
            df = self.data.get(sym)
            if df is not None and not df.empty:
                last = df.iloc[-1]
                ltp = last['close']
                vol = last['volume']

                # Check 24h Change logic if available (simplified here)
                change = 0.00
                trend_icon = "📈" if change >= 0 else "📉"

                print(f"{sym:<12} | LTP: $ {ltp:,.2f} | 24h Change: {trend_icon}   +{change:.2f}% | Vol: $ {int(vol):,}")
        print("="*70)

        now = dt.now()
        next_min = (now.minute // TIMEFRAME_MINUTES + 1) * TIMEFRAME_MINUTES
        # Simple calc for time remaining
        minutes_remaining = TIMEFRAME_MINUTES - (now.minute % TIMEFRAME_MINUTES)
        seconds_remaining = 60 - now.second

        print(f"⏰ TIMEFRAME: {TIMEFRAME_MINUTES} minute candles")
        print(f"   - Each candle represents {TIMEFRAME_MINUTES} minutes of price action")
        print(f"   - New candle completes every {TIMEFRAME_MINUTES} minutes")
        print(f"   - Strategy: Fast({EMA_FAST}) / Slow({EMA_SLOW}) EMA Crossover")
        # print(f"   - Time remaining: {minutes_remaining}m {seconds_remaining}s")
        print("="*70 + "\n")

    def update(self):
        """Called periodically to fetch fresh data and check signals"""
        for sym in SYMBOLS_TO_MONITOR:
            # Fetch strictly new data to append
            df_new = self.client.fetch_history(sym, TIMEFRAME_RES, 5)
            if not df_new.empty:
                df_old = self.data.get(sym, pd.DataFrame())
                if df_old.empty:
                    self.data[sym] = df_new
                else:
                    # Concatenate and drop duplicates based on time
                    df_combined = pd.concat([df_old, df_new]).drop_duplicates(subset='time', keep='last').sort_values('time').reset_index(drop=True)

                    # Update live price from latest candle close if available
                    self.live_prices[sym] = df_combined.iloc[-1]['close']

                    # Recalculate indicators
                    df_combined['ema_fast'] = compute_ema(df_combined['close'], EMA_FAST)
                    df_combined['ema_slow'] = compute_ema(df_combined['close'], EMA_SLOW)
                    self.data[sym] = df_combined

                self.check_signal(sym)

    def check_signal(self, symbol):
        df = self.data[symbol]
        if len(df) < 2: return

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        # Crossover Logic
        if prev['ema_fast'] <= prev['ema_slow'] and curr['ema_fast'] > curr['ema_slow']:
            if self.positions[symbol] <= 0:
                log("signal", f"🔵 BUY SIGNAL for {symbol} @ {curr['close']:.2f} (Fast {curr['ema_fast']:.2f} > Slow {curr['ema_slow']:.2f})")
                self.positions[symbol] = 1

        elif prev['ema_fast'] >= prev['ema_slow'] and curr['ema_fast'] < curr['ema_slow']:
            if self.positions[symbol] >= 0:
                log("signal", f"🟠 SELL SIGNAL for {symbol} @ {curr['close']:.2f} (Fast {curr['ema_fast']:.2f} < Slow {curr['ema_slow']:.2f})")
                self.positions[symbol] = -1

# ==============================================================================
# MAIN LOGIC
# ==============================================================================
def main():
    client = DeltaClient()
    client.fetch_products()

    strategy = StrategyEngine(client)
    strategy.warmup()

    # WebSocket (Keep Alive & Ticker Monitor)
    log("main", "Starting WebSocket connection...")
    log("main", "Bot running. Press Ctrl+C to exit.")

    def on_ws_open(ws):
        log("ws", "Connected to Delta Exchange")
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
        # Optional: Parse message to update live prices in real-time
        # For now, we rely on the REST update loop for strategy signals
        # But logging connection health is good
        pass

    def on_ws_error(ws, error):
        log("ws_error", f"WebSocket Error: {error}")

    def on_ws_close(ws, close_status_code, close_msg):
        log("ws_close", "WebSocket Closed. Reconnecting...")

    def run_ws():
        while True:
            try:
                # Use run_forever with ping/pong to keep connection alive
                wsa = websocket.WebSocketApp(
                    WS_URL,
                    on_open=on_ws_open,
                    on_message=on_ws_message,
                    on_error=on_ws_error,
                    on_close=on_ws_close
                )
                wsa.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                log("ws_exception", f"Exception in WS loop: {e}")

            log("ws", "Reconnecting in 5 seconds...")
            time.sleep(5)

    ws_thread = threading.Thread(target=run_ws)
    ws_thread.daemon = True
    ws_thread.start()

    # Main Loop
    try:
        while True:
            time.sleep(60) # Check every minute

            # Update Strategy
            strategy.update()

            # Log Status
            log("heartbeat", f"Bot active. Monitoring {len(SYMBOLS_TO_MONITOR)} symbols...")
            log("heartbeat", f"📊 Current Market Prices:")
            for sym in SYMBOLS_TO_MONITOR:
                df = strategy.data.get(sym)
                if df is not None:
                    last = df.iloc[-1]
                    trend = "🟢" if last['ema_fast'] > last['ema_slow'] else "🔴"
                    status = "watch" # Default

                    # Format log to match user preference
                    # [heartbeat]   🟢 BTCUSD       | LTP: $ 84,113.00 | Status: watch
                    print(f"[heartbeat]   {trend} {sym:<12} | LTP: $ {last['close']:,.2f} | Status: {status}")

    except KeyboardInterrupt:
        print("\nExiting...")

if __name__ == "__main__":
    main()
