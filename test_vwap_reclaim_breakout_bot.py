import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytz

from vwap_reclaim_breakout_bot import (
    DAILY_MAX_LOSS,
    INDEX_CONFIGS,
    IST,
    LOT_MULTIPLIER,
    SL_MODE,
    ArmedSignal,
    FyersBroker,
    PositionStore,
    VwapReclaimBreakoutStrategy,
    auto_resolve_atm_option,
    check_and_shift_strike,
    compute_quantity,
    get_expiry_candidates,
    handle_signal_and_entry,
    parse_option_symbol,
)


class DummyMultiIndexOptionChainFyers:
    def optionchain(self, data: dict):
        sym = data.get("symbol", "")
        if not data.get("timestamp"):
            return {
                "s": "ok",
                "data": {
                    "optionChain": [{"option_type": "-", "ltp": 80000.0 if "SENSEX" in sym else 23897.7}],
                    "expiryData": [
                        {"expiry": "1788862200", "date": "08-09-2026"},
                        {"expiry": "1789467000", "date": "15-09-2026"},
                    ]
                }
            }
        else:
            if "SENSEX" in sym:
                return {
                    "s": "ok",
                    "data": {
                        "optionChain": [
                            {"strike_price": 79500, "option_type": "CE", "symbol": "BSE:SENSEX26SEP79500CE", "ltp": 520.0},
                            {"strike_price": 80000, "option_type": "CE", "symbol": "BSE:SENSEX26SEP80000CE", "ltp": 425.0},
                            {"strike_price": 80500, "option_type": "CE", "symbol": "BSE:SENSEX26SEP80500CE", "ltp": 320.0},
                        ]
                    }
                }
            else:
                return {
                    "s": "ok",
                    "data": {
                        "optionChain": [
                            {"strike_price": 23850, "option_type": "CE", "symbol": "NSE:NIFTY26SEP23850CE", "ltp": 250.0},
                            {"strike_price": 23900, "option_type": "CE", "symbol": "NSE:NIFTY26SEP23900CE", "ltp": 195.0},
                            {"strike_price": 23950, "option_type": "CE", "symbol": "NSE:NIFTY26SEP23950CE", "ltp": 150.0},
                        ]
                    }
                }


class DummyBroker:
    def __init__(self, df_1m: pd.DataFrame, ltp: float):
        self.df_1m = df_1m
        self.ltp = ltp
        self.fyers = DummyMultiIndexOptionChainFyers()
        self.placed_orders = []
        self.live_ltp = {}
        self.subscribed_symbols = []

    def get_historical_1m(self, symbol: str, days_back: int) -> pd.DataFrame:
        return self.df_1m

    def get_ltp(self, symbol: str) -> float:
        if symbol in self.live_ltp and self.live_ltp[symbol] > 0:
            return self.live_ltp[symbol]
        return self.ltp

    def subscribe_symbol(self, symbol: str):
        if symbol not in self.subscribed_symbols:
            self.subscribed_symbols.append(symbol)

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
        strat = VwapReclaimBreakoutStrategy(timeframe="M15", risk_reward=2.0, min_premium=180.0, max_premium=220.0)
        start = datetime.now(IST).replace(hour=9, minute=16, second=0, microsecond=0)
        timestamps = [start + timedelta(minutes=i) for i in range(30)]
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
        self.assertEqual(len(candles), 2)

    def test_is_signal_candle_sensex_range(self):
        strat = VwapReclaimBreakoutStrategy(timeframe="M15", risk_reward=2.0, min_premium=400.0, max_premium=450.0)

        valid_row = {"open": 405.0, "close": 425.0, "low": 390.0, "high": 430.0, "vwap": 410.0}
        self.assertTrue(strat._is_signal_candle(valid_row))

        too_low_row = {"open": 370.0, "close": 380.0, "low": 350.0, "high": 385.0, "vwap": 375.0}
        self.assertFalse(strat._is_signal_candle(too_low_row))

    def test_compute_quantity_with_multiplier(self):
        cfg_qty = {"mode": "quantity"}
        self.assertEqual(compute_quantity(cfg_qty, lot_size=20, entry_price=425.0, lot_multiplier=2), 40)

    def test_auto_resolve_sensex_option(self):
        broker = DummyBroker(pd.DataFrame(), 80000.0)
        cfg = {"sizing": {"mode": "quantity"}}
        res = auto_resolve_atm_option(broker, cfg, spot_symbol="BSE:SENSEX-INDEX", option_type="CE")
        self.assertIsNotNone(res)
        self.assertEqual(res["trade_symbol"], "BSE:SENSEX26SEP80000CE")
        self.assertEqual(res["strike"], 80000.0)

    def test_position_store_daily_pnl(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            path = tf.name

        try:
            store = PositionStore(path)
            self.assertEqual(store.get_daily_pnl(), 0.0)

            pos_data = {"symbol": "BSE:SENSEX_CE", "qty": 20, "entry": 425.0, "sl": 400.0, "target": 475.0, "product_type": "INTRADAY"}
            store.open_position(pos_data)

            store.close_position(pnl=-1000.0)
            self.assertEqual(store.get_daily_pnl(), -1000.0)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_handle_signal_entry_blocked_when_position_open(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            path = tf.name

        try:
            store = PositionStore(path)
            store.open_position({"symbol": "EXISTING", "qty": 65, "entry": 200.0, "sl": 180.0, "target": 240.0})
            broker = DummyBroker(pd.DataFrame(), 200.0)
            strat = VwapReclaimBreakoutStrategy(timeframe="M15", risk_reward=2.0)
            cfg = {"order": {"product_type": "INTRADAY"}}

            INDEX_CONFIGS["NSE:NIFTY50-INDEX"]["trade_symbol"] = "NSE:TEST"
            handle_signal_and_entry(broker, store, strat, cfg, spot_symbol="NSE:NIFTY50-INDEX")
            self.assertEqual(len(broker.placed_orders), 0)
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
