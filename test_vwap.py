
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def compute_indicators(df):
    if df is None or df.empty:
        return df
    df = df.copy()

    # VWAP Calculation (Anchored Daily)
    if "volume" in df.columns:
        print("Volume column found.")
        # Avoid SettingWithCopyWarning if we are modifying slice (already copied above)
        pv = df["close"] * df["volume"]
        # Group by date to anchor VWAP
        cum_pv = pv.groupby(df.index.date).cumsum()
        cum_vol = df["volume"].groupby(df.index.date).cumsum()

        # Calculate VWAP, fill with close if volume is 0
        df["vwap"] = cum_pv / cum_vol
        df["vwap"] = df["vwap"].fillna(df["close"])
    else:
        print("Volume column NOT found.")
        df["vwap"] = df["close"]

    return df

# Mock Data
data = []
base = datetime.now()
for i in range(100):
    ts = base - timedelta(minutes=5 * (100 - i))
    data.append({
        "ts": ts,
        "open": 100.0 + i,
        "high": 105.0 + i,
        "low": 95.0 + i,
        "close": 102.0 + i,
        "volume": 1000.0  # Constant volume
    })

df = pd.DataFrame(data).set_index("ts").sort_index()
df.index.name = "datetime"

# Run
df_result = compute_indicators(df)
last = df_result.iloc[-1]
print(f"Close: {last['close']}")
print(f"VWAP: {last['vwap']}")
print(f"Volume: {last['volume']}")
print(f"Is Equal? {last['close'] == last['vwap']}")
