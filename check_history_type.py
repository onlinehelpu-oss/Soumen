
import requests
import json
import time

BASE_URL = "https://api.india.delta.exchange"
SYMBOL = "NEARUSD"

def get_ticker():
    url = f"{BASE_URL}/v2/tickers"
    try:
        resp = requests.get(url)
        data = resp.json()
        if "result" in data:
            for t in data["result"]:
                if t.get("symbol") == SYMBOL:
                    return t
    except Exception as e:
        print(f"Error getting ticker: {e}")
    return None

def get_history(resolution, start, end):
    url = f"{BASE_URL}/v2/chart/history"
    params = {
        "symbol": SYMBOL,
        "resolution": resolution,
        "from": start,
        "to": end
    }
    try:
        resp = requests.get(url, params=params)
        return resp.json()
    except Exception as e:
        print(f"Error getting history: {e}")
        return None

if __name__ == "__main__":
    print(f"Checking data for {SYMBOL}...")

    ticker = get_ticker()
    if ticker:
        mark = float(ticker.get("mark_price", 0))
        close = float(ticker.get("close", 0))
        print(f"Ticker Mark Price: {mark}")
        print(f"Ticker Last Price (Close): {close}")
    else:
        print("Failed to get ticker.")

    now = int(time.time())
    start = now - (60 * 60) # 1 hour

    # Check 1m history
    history = get_history("1", start, now)

    if history and "result" in history:
        res = history["result"]
        if "c" in res and len(res["c"]) > 0:
            last_candle_close = float(res["c"][-1])
            print(f"Last 1m Candle Close (History): {last_candle_close}")

            # Compare
            diff_mark = abs(last_candle_close - mark)
            diff_close = abs(last_candle_close - close)

            print(f"Diff vs Mark: {diff_mark:.4f}")
            print(f"Diff vs Last: {diff_close:.4f}")

            if diff_mark < diff_close:
                print(">> History seems to track MARK PRICE (mostly).")
            else:
                print(">> History seems to track LAST PRICE (mostly).")
        else:
            print("No history data found.")
    else:
        print("Failed to get history.")
