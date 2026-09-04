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
    FyersBroker,
    PositionStore,
    VwapReclaimBreakoutStrategy,
    auto_resolve_atm_option,
    compute_quantity,
    get_expiry_candidates,
    parse_option_symbol,
)


class DummyOptionChainFyers:
    def optionchain(self, data: dict):
        if not data.get("timestamp"):
            return {
                "s": "ok",
                "data": {
                    "optionChain": [
                        {"option_type": "-", "ltp": 23897.7}
                    ],
                    "expiryData": [
                        {"expiry": "1788862200", "date": "08-09-2026"},
                        {"expiry": "1789467000", "date": "15-09-2026"},
                    ]
                }
            }
        else:
            return {
                "s": "ok",
                "data": {
                    "optionChain": [
                        {"strike_price": 23850, "option_type": "CE", "symbol": "NSE:NIFTY26SEP23850CE", "ltp": 210.0},
                        {"strike_price": 23900, "option_type": "CE", "symbol": "NSE:NIFTY26SEP23900CE", "ltp": 185.0},
                        {"strike_price": 23950, "option_type": "CE", "symbol": "NSE:NIFTY26SEP23950CE", "ltp": 150.0},
                    ]
                }
            }


class DummyBroker:
    def __init__(self, df_1m: pd.DataFrame, ltp: float):
        self.df_1m = df_1m
        self.ltp = ltp
        self.fyers = DummyOptionChainFyers()
        self.placed_orders = []
        self.live_ltp = {}

    def get_historical_1m(self, symbol: str, days_back: int) -> pd.DataFrame:
        return self.df_1m

    def get_ltp(self, symbol: str) -> float:
        if symbol in self.live_ltp and self.live_ltp[symbol] > 0:
            return self.live_ltp[symbol]
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
            "open": [190.0, 192.0, 191.0, 193.0, 194.0],
            "high": [193.0, 194.0, 193.0, 196.0, 195.0],
            "low": [189.0, 191.0, 190.0, 192.0, 193.0],
            "close": [192.0, 193.0, 192.0, 195.0, 194.0],
            "volume": [0, 100, 200, 150, 50],
        })
        res = VwapReclaimBreakoutStrategy.compute_vwap_1m(df_1m)
        self.assertIn("vwap", res.columns)
        self.assertTrue(pd.isna(res.iloc[0]["vwap"]))
        self.assertEqual(res["vwap"].dtype, np.float64)

    def test_build_candles(self):
        strat = VwapReclaimBreakoutStrategy(timeframe="M15", risk_reward=2.0, min_premium=180.0)
        start = datetime.now(IST).replace(hour=9, minute=16, second=0, microsecond=0)
        timestamps = [start + timedelta(minutes=i) for i in range(30)] # 09:16 to 09:45
        df_1m = pd.DataFrame({
            "timestamp": timestamps,
            "open": [190.0] * 30,
            "high": [205.0] * 30,
            "low": [185.0] * 30,
            "close": [195.0] * 30,
            "volume": [10] * 30,
        })
        candles = strat.build_candles(df_1m)
        self.assertFalse(candles.empty)
        self.assertIn("vwap", candles.columns)
        self.assertEqual(len(candles), 2)  # 09:30 and 09:45

    def test_is_signal_candle_with_min_premium(self):
        strat = VwapReclaimBreakoutStrategy(timeframe="M15", risk_reward=2.0, min_premium=180.0)

        # Valid option candle (close 190 >= 180, low 175 < vwap 180, close 190 > vwap 180)
        valid_row = {"open": 178.0, "close": 190.0, "low": 175.0, "high": 192.0, "vwap": 180.0}
        self.assertTrue(strat._is_signal_candle(valid_row))

        # Close below min_premium (close 170 < 180) -> Invalid
        low_premium_row = {"open": 160.0, "close": 170.0, "low": 155.0, "high": 172.0, "vwap": 165.0}
        self.assertFalse(strat._is_signal_candle(low_premium_row))

        # Red option candle -> Invalid
        red_row = {"open": 195.0, "close": 185.0, "low": 175.0, "high": 196.0, "vwap": 180.0}
        self.assertFalse(strat._is_signal_candle(red_row))

    def test_armed_signal_lifecycle(self):
        strat = VwapReclaimBreakoutStrategy(timeframe="M15", risk_reward=2.0, min_premium=180.0)
        ts = pd.Timestamp("2026-09-04 09:30:00+0530", tz="Asia/Kolkata")
        candle = pd.Series({
            "timestamp": ts,
            "open": 182.0,
            "high": 200.0,
            "low": 178.0,
            "close": 195.0,
            "vwap": 180.0,
        })
        strat.on_new_closed_candle(candle)
        self.assertIsNotNone(strat.armed)
        self.assertEqual(strat.armed.high, 200.0)
        self.assertEqual(strat.armed.low, 178.0)

        # Poll LTP below high (198.0) -> no breakout
        now_ts = pd.Timestamp("2026-09-04 09:35:00+0530", tz="Asia/Kolkata")
        res = strat.check_breakout(198.0, now_ts)
        self.assertIsNone(res)

        # Poll LTP > high (201.0) -> Breakout on option price!
        res = strat.check_breakout(201.0, now_ts)
        self.assertIsNotNone(res)
        self.assertEqual(res["entry"], 201.0)
        self.assertEqual(res["sl"], 178.0)
        self.assertEqual(res["target"], 201.0 + 2.0 * (201.0 - 178.0))
        self.assertIsNone(strat.armed)

    def test_armed_signal_expiration(self):
        strat = VwapReclaimBreakoutStrategy(timeframe="M15", risk_reward=2.0, min_premium=180.0)
        ts = pd.Timestamp("2026-09-04 09:30:00+0530", tz="Asia/Kolkata")
        candle = pd.Series({
            "timestamp": ts,
            "open": 182.0,
            "high": 200.0,
            "low": 178.0,
            "close": 195.0,
            "vwap": 180.0,
        })
        strat.on_new_closed_candle(candle)

        expired_ts = pd.Timestamp("2026-09-04 09:45:00+0530", tz="Asia/Kolkata")
        res = strat.check_breakout(202.0, expired_ts)
        self.assertIsNone(res)
        self.assertIsNone(strat.armed)

    def test_compute_quantity(self):
        cfg_qty = {"mode": "quantity", "quantity": 65}
        self.assertEqual(compute_quantity(cfg_qty, lot_size=65, entry_price=190.0), 65)

        cfg_adj = {"mode": "quantity", "quantity": 70}
        self.assertEqual(compute_quantity(cfg_adj, lot_size=65, entry_price=190.0), 65)

        cfg_amt = {"mode": "amount", "amount": 50000}
        self.assertEqual(compute_quantity(cfg_amt, lot_size=65, entry_price=190.0), 260)

    def test_get_expiry_candidates(self):
        chosen_exp = {"expiry": "1788862200", "date": "08-09-2026"}
        cands = get_expiry_candidates(chosen_exp)
        self.assertIn("1788862200", cands)
        self.assertIn(1788862200, cands)
        self.assertIn("2026-09-08", cands)

    def test_auto_resolve_atm_option(self):
        broker = DummyBroker(pd.DataFrame(), 23897.7)
        cfg = {
            "symbol": {"spot_symbol": "NSE:NIFTY50-INDEX", "lot_size": 65},
            "strategy": {"min_premium": 180.0},
            "sizing": {"mode": "quantity", "quantity": 65}
        }
        res = auto_resolve_atm_option(broker, cfg, option_type="CE")
        self.assertIsNotNone(res)
        self.assertEqual(res["trade_symbol"], "NSE:NIFTY26SEP23900CE")
        self.assertEqual(res["strike"], 23900.0)

    def test_position_store(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            path = tf.name

        try:
            store = PositionStore(path)
            self.assertFalse(store.has_open_position())

            pos_data = {"symbol": "NSE:TEST_CE", "qty": 65, "entry": 195.0, "sl": 178.0, "target": 229.0, "product_type": "INTRADAY"}
            store.open_position(pos_data)
            self.assertTrue(store.has_open_position())
            self.assertEqual(store.get_open_position()["symbol"], "NSE:TEST_CE")

            with self.assertRaises(RuntimeError):
                store.open_position(pos_data)

            store.close_position()
            self.assertFalse(store.has_open_position())

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
