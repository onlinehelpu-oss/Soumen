import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytz

from vwap_reclaim_breakout_bot import (
    IST,
    ArmedSignal,
    PositionStore,
    VwapReclaimBreakoutStrategy,
    compute_quantity,
    parse_option_symbol,
)


class DummyBroker:
    def __init__(self, df_1m: pd.DataFrame, ltp: float):
        self.df_1m = df_1m
        self.ltp = ltp
        self.placed_orders = []

    def get_historical_1m(self, symbol: str, days_back: int) -> pd.DataFrame:
        return self.df_1m

    def get_ltp(self, symbol: str) -> float:
        return self.ltp

    def place_order(self, symbol: str, qty: int, side: str, product_type: str,
                    order_type: str = "MARKET", limit_price: float = 0,
                    stop_price: float = 0) -> str:
        order_id = f"ORD_{len(self.placed_orders) + 1}"
        self.placed_orders.append({
            "symbol": symbol, "qty": qty, "side": side,
            "product_type": product_type, "order_type": order_type,
            "order_id": order_id
        })
        return order_id


class TestVwapReclaimBot(unittest.TestCase):

    def test_compute_vwap_1m(self):
        start = datetime.now(IST).replace(hour=9, minute=15, second=0, microsecond=0)
        timestamps = [start + timedelta(minutes=i) for i in range(5)]
        df_1m = pd.DataFrame({
            "timestamp": timestamps,
            "open": [100.0, 102.0, 101.0, 103.0, 104.0],
            "high": [103.0, 104.0, 103.0, 106.0, 105.0],
            "low": [99.0, 101.0, 100.0, 102.0, 103.0],
            "close": [102.0, 103.0, 102.0, 105.0, 104.0],
            "volume": [0, 100, 200, 150, 50],
        })
        res = VwapReclaimBreakoutStrategy.compute_vwap_1m(df_1m)
        self.assertIn("vwap", res.columns)
        self.assertTrue(pd.isna(res.iloc[0]["vwap"]))
        self.assertFalse(isinstance(res["vwap"].dtype, pd.CategoricalDtype))
        self.assertEqual(res["vwap"].dtype, np.float64)

    def test_build_candles(self):
        strat = VwapReclaimBreakoutStrategy(timeframe="M15", risk_reward=2.0)
        start = datetime.now(IST).replace(hour=9, minute=16, second=0, microsecond=0)
        timestamps = [start + timedelta(minutes=i) for i in range(30)] # 09:16 to 09:45
        df_1m = pd.DataFrame({
            "timestamp": timestamps,
            "open": [100.0] * 30,
            "high": [105.0] * 30,
            "low": [95.0] * 30,
            "close": [102.0] * 30,
            "volume": [10] * 30,
        })
        candles = strat.build_candles(df_1m)
        self.assertFalse(candles.empty)
        self.assertIn("vwap", candles.columns)
        self.assertEqual(len(candles), 2)  # 09:30 and 09:45

    def test_is_signal_candle(self):
        # Green candle whose low < vwap and close > vwap
        valid_row = {"open": 100.0, "close": 105.0, "low": 95.0, "high": 106.0, "vwap": 98.0}
        self.assertTrue(VwapReclaimBreakoutStrategy._is_signal_candle(valid_row))

        # Red candle -> Invalid
        red_row = {"open": 105.0, "close": 100.0, "low": 95.0, "high": 106.0, "vwap": 98.0}
        self.assertFalse(VwapReclaimBreakoutStrategy._is_signal_candle(red_row))

        # Low above VWAP (no dip below VWAP) -> Invalid
        no_dip_row = {"open": 100.0, "close": 105.0, "low": 99.0, "high": 106.0, "vwap": 98.0}
        self.assertFalse(VwapReclaimBreakoutStrategy._is_signal_candle(no_dip_row))

    def test_armed_signal_lifecycle(self):
        strat = VwapReclaimBreakoutStrategy(timeframe="M15", risk_reward=2.0)
        ts = pd.Timestamp("2026-09-04 09:30:00+0530", tz="Asia/Kolkata")
        candle = pd.Series({
            "timestamp": ts,
            "open": 100.0,
            "high": 110.0,
            "low": 95.0,
            "close": 108.0,
            "vwap": 98.0,
        })
        strat.on_new_closed_candle(candle)
        self.assertIsNotNone(strat.armed)
        self.assertEqual(strat.armed.high, 110.0)
        self.assertEqual(strat.armed.low, 95.0)

        # Poll LTP below high -> no breakout
        now_ts = pd.Timestamp("2026-09-04 09:35:00+0530", tz="Asia/Kolkata")
        res = strat.check_breakout(109.0, now_ts)
        self.assertIsNone(res)
        self.assertIsNotNone(strat.armed)

        # Poll LTP > high -> Breakout!
        res = strat.check_breakout(111.0, now_ts)
        self.assertIsNotNone(res)
        self.assertEqual(res["entry"], 111.0)
        self.assertEqual(res["sl"], 95.0)
        self.assertEqual(res["target"], 111.0 + 2.0 * (111.0 - 95.0))
        self.assertIsNone(strat.armed)  # Disarmed after trigger

    def test_armed_signal_expiration(self):
        strat = VwapReclaimBreakoutStrategy(timeframe="M15", risk_reward=2.0)
        ts = pd.Timestamp("2026-09-04 09:30:00+0530", tz="Asia/Kolkata")
        candle = pd.Series({
            "timestamp": ts,
            "open": 100.0,
            "high": 110.0,
            "low": 95.0,
            "close": 108.0,
            "vwap": 98.0,
        })
        strat.on_new_closed_candle(candle)

        # Poll at or after expires_at (09:45:00)
        expired_ts = pd.Timestamp("2026-09-04 09:45:00+0530", tz="Asia/Kolkata")
        res = strat.check_breakout(112.0, expired_ts)
        self.assertIsNone(res)
        self.assertIsNone(strat.armed)

    def test_compute_quantity(self):
        # Quantity mode exact
        cfg_qty = {"mode": "quantity", "quantity": 75}
        self.assertEqual(compute_quantity(cfg_qty, lot_size=75, entry_price=100.0), 75)

        # Quantity mode non-multiple auto-adjusts
        cfg_adj = {"mode": "quantity", "quantity": 70}
        self.assertEqual(compute_quantity(cfg_adj, lot_size=65, entry_price=100.0), 65)

        # Amount mode
        cfg_amt = {"mode": "amount", "amount": 50000}
        # 50000 / 100 = 500 units -> 500 / 65 = 7.69 -> 7 lots -> 7 * 65 = 455
        self.assertEqual(compute_quantity(cfg_amt, lot_size=65, entry_price=100.0), 455)

    def test_position_store(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            path = tf.name

        try:
            store = PositionStore(path)
            self.assertFalse(store.has_open_position())

            pos_data = {"symbol": "NSE:TEST", "qty": 75, "entry": 100.0, "sl": 90.0, "target": 120.0, "product_type": "INTRADAY"}
            store.open_position(pos_data)
            self.assertTrue(store.has_open_position())
            self.assertEqual(store.get_open_position()["symbol"], "NSE:TEST")

            # Duplicate open should fail
            with self.assertRaises(RuntimeError):
                store.open_position(pos_data)

            store.close_position()
            self.assertFalse(store.has_open_position())

            # Test corrupted file recoverability
            with open(path, "w") as f:
                f.write("CORRUPTED_JSON")
            self.assertFalse(store.has_open_position())
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_parse_option_symbol(self):
        parsed = parse_option_symbol("NSE:NIFTY25JAN23500CE")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["underlying"], "NIFTY")
        self.assertEqual(parsed["strike"], "23500")
        self.assertEqual(parsed["option_type"], "CALL")

        self.assertIsNone(parse_option_symbol("INVALID_SYMBOL"))


if __name__ == "__main__":
    unittest.main()
