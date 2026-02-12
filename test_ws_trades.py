
import websocket
import json
import threading
import time

WS_URL = "wss://socket.india.delta.exchange"
SYMBOLS = ["BTCUSD"]

def on_message(ws, message):
    try:
        data = json.loads(message)
        print(f"Received: {json.dumps(data, indent=2)}")
        if data.get("type") == "all_trades":
            # Just print the first one and stop for debugging
            pass
    except Exception as e:
        print(f"Error parsing message: {e}")

def on_error(ws, error):
    print(f"Error: {error}")

def on_close(ws, close_status_code, close_msg):
    print("### closed ###")

def on_open(ws):
    print("Opened connection")
    payload = {
        "type": "subscribe",
        "payload": {
            "channels": [
                {
                    "name": "all_trades",
                    "symbols": SYMBOLS
                }
            ]
        }
    }
    ws.send(json.dumps(payload))

if __name__ == "__main__":
    websocket.enableTrace(True)
    ws = websocket.WebSocketApp(WS_URL,
                                on_open=on_open,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)

    t = threading.Thread(target=ws.run_forever)
    t.start()

    time.sleep(10)
    ws.close()
