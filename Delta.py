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
TIMEFRAME_RES = "1m"  # Changed to 1m as per user log
TIMEFRAME_MINUTES = 1
LOOKBACK_CANDLES = 671

# Strategy Params
EMA_FAST = 9
EMA_SLOW = 21

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
                    self.products[sym] = pid
                    self.id_to_symbol[pid] = sym
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
                # Fast/Slow status
                trend = "🟢 Bull" if last['ema_fast'] > last['ema_slow'] else "🔴 Bear"
                print(f"{sym:<12} | LTP: $ {ltp:,.2f} | Trend: {trend} | Vol: {int(vol):,}")
        print("="*70)

        now = dt.now()
        next_min = (now.minute // TIMEFRAME_MINUTES + 1) * TIMEFRAME_MINUTES
        next_time = (now + timedelta(minutes=TIMEFRAME_MINUTES)).replace(second=0, microsecond=0) # Approx

        print(f"⏰ TIMEFRAME: {TIMEFRAME_MINUTES} minute candles")
        print(f"   - Each candle represents {TIMEFRAME_MINUTES} minutes of price action")
        print(f"   - Strategy: Fast({EMA_FAST}) / Slow({EMA_SLOW}) EMA Crossover")
        print("="*70 + "\n")

    def update(self):
        """Called periodically to fetch fresh data and check signals"""
        # In a real efficient bot, we'd update last candle with live ticks.
        # Here we fetch last few candles via REST to update the DataFrame.
        for sym in SYMBOLS_TO_MONITOR:
            # Fetch only last 5 candles to update tip
            df_new = self.client.fetch_history(sym, TIMEFRAME_RES, 5)
            if not df_new.empty:
                # Merge logic (simplified: just replace tail)
                # In prod, we'd append unique timestamps
                df_old = self.data.get(sym, pd.DataFrame())
                if df_old.empty:
                    self.data[sym] = df_new
                else:
                    # Concatenate and drop duplicates
                    df_combined = pd.concat([df_old, df_new]).drop_duplicates(subset='time', keep='last').sort_values('time').reset_index(drop=True)
                    # Recalculate indicators on the tail (or whole if short)
                    # For correctness with EMA, we need history.
                    # Re-calc over whole series is safest for this demo scale.
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
        # Bullish Cross: Fast crosses above Slow
        if prev['ema_fast'] <= prev['ema_slow'] and curr['ema_fast'] > curr['ema_slow']:
            if self.positions[symbol] <= 0:
                log("signal", f"🔵 BUY SIGNAL for {symbol} @ {curr['close']:.2f} (Fast {curr['ema_fast']:.2f} > Slow {curr['ema_slow']:.2f})")
                self.positions[symbol] = 1
                # Place Paper Order Here

        # Bearish Cross: Fast crosses below Slow
        elif prev['ema_fast'] >= prev['ema_slow'] and curr['ema_fast'] < curr['ema_slow']:
            if self.positions[symbol] >= 0:
                log("signal", f"🟠 SELL SIGNAL for {symbol} @ {curr['close']:.2f} (Fast {curr['ema_fast']:.2f} < Slow {curr['ema_slow']:.2f})")
                self.positions[symbol] = -1
                # Place Paper Order Here

# ==============================================================================
# MAIN LOGIC
# ==============================================================================
def main():
    client = DeltaClient()
    client.fetch_products()

    # Map required symbols
    for sym in SYMBOLS_TO_MONITOR:
        pid = client.get_product_id(sym)
        if pid:
            log("delta", f"Mapped {sym} -> product_id {pid}")

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

    wsa = websocket.WebSocketApp(WS_URL, on_open=on_ws_open)
    ws_thread = threading.Thread(target=wsa.run_forever)
    ws_thread.daemon = True
    ws_thread.start()

    # Main Loop
    try:
        while True:
            time.sleep(60) # Check every minute
            log("heartbeat", f"Bot active. Checking signals...")

            # Update Strategy
            strategy.update()

            # Log Status
            for sym in SYMBOLS_TO_MONITOR:
                df = strategy.data.get(sym)
                if df is not None:
                    last = df.iloc[-1]
                    trend = "🟢" if last['ema_fast'] > last['ema_slow'] else "🔴"
                    log("heartbeat", f"  {trend} {sym:<10} | LTP: {last['close']:<10.2f} | Fast: {last['ema_fast']:.2f} | Slow: {last['ema_slow']:.2f}")

    except KeyboardInterrupt:
        print("\nExiting...")

if __name__ == "__main__":
    main()
