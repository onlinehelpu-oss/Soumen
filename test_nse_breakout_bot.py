# -*- coding: utf-8 -*-
"""
Unit and Integration Test Suite for NSE Sector Breakout Trading Bot (`nse_sector_breakout_bot.py`)
"""

import unittest
from datetime import datetime, time as dtime, timedelta
from typing import Dict, Any

from nse_sector_breakout_bot import (
    StrategyConfig,
    Candle,
    StockData,
    Position,
    MarketDataProvider,
    StockSelectionPipeline,
    StrategyEngine,
    SYMBOL_TO_SECTOR
)


class MockMarketDataProvider(MarketDataProvider):
    def __init__(self):
        self.quotes: Dict[str, Dict[str, Any]] = {}
        self.pdh_data: Dict[str, Dict[str, float]] = {}
        self.placed_orders: List[Dict[str, Any]] = []

    def set_quote(self, symbol: str, ltp: float, open_p: float, close_p: float, volume: float):
        self.quotes[symbol] = {
            "symbol": symbol,
            "ltp": ltp,
            "open": open_p,
            "close": close_p,
            "volume": volume
        }

    def set_pdh(self, symbol: str, high: float, close: float):
        self.pdh_data[symbol] = {"high": high, "low": high - 20.0, "close": close}

    def get_live_quote(self, symbol: str) -> Dict[str, Any]:
        return self.quotes.get(symbol, {})

    def get_previous_day_ohlc(self, symbol: str) -> Dict[str, float]:
        return self.pdh_data.get(symbol, {"high": 100.0, "low": 90.0, "close": 95.0})

    def place_market_order(self, symbol: str, qty: int, side: int) -> Dict[str, Any]:
        order_info = {"symbol": symbol, "qty": qty, "side": side, "id": f"MOCK_ORD_{len(self.placed_orders)+1}"}
        self.placed_orders.append(order_info)
        return {"s": "ok", "id": order_info["id"]}


class TestNSEBreakoutBot(unittest.TestCase):

    def setUp(self):
        self.config = StrategyConfig()
        self.config.SIMULATION_MODE = True
        self.provider = MockMarketDataProvider()

    def test_stock_selection_sector_and_filters(self):
        """Tests sector performance calculation, 2.5% movement filter, and PDH filter."""
        pipeline = StockSelectionPipeline(self.config, self.provider)

        # Setup 2 stocks in NIFTY BANK (top sector) and 1 in NIFTY IT
        s1 = "NSE:SBIN-EQ"       # NIFTY BANK
        s2 = "NSE:HDFCBANK-EQ"   # NIFTY BANK
        s3 = "NSE:TCS-EQ"        # NIFTY IT

        pipeline.register_stock(s1)
        pipeline.register_stock(s2)
        pipeline.register_stock(s3)

        # s1: Ref=100, LTP=102 (+2.0%, <= 2.5% max move, > PDH 101) -> SHOULD QUALIFY
        self.provider.set_quote(s1, ltp=102.0, open_p=100.0, close_p=100.0, volume=50000)
        self.provider.set_pdh(s1, high=101.0, close=100.0)

        # s2: Ref=100, LTP=104 (+4.0%, > 2.5% max move) -> SHOULD BE EXCLUDED (2.5% Filter)
        self.provider.set_quote(s2, ltp=104.0, open_p=100.0, close_p=100.0, volume=40000)
        self.provider.set_pdh(s2, high=101.0, close=100.0)

        # s3: Ref=100, LTP=101 (+1.0%) -> In NIFTY IT (Lower performing sector)
        self.provider.set_quote(s3, ltp=101.0, open_p=100.0, close_p=100.0, volume=30000)
        self.provider.set_pdh(s3, high=100.5, close=100.0)

        pipeline.update_live_market_data()

        self.assertEqual(pipeline.top_sector, "NIFTY BANK")

        qualified = pipeline.run_selection_and_filters()

        self.assertIn(s1, qualified)
        self.assertNotIn(s2, qualified)  # Failed 2.5% filter
        self.assertNotIn(s3, qualified)  # Not in top sector

    def test_previous_day_high_filter(self):
        """Tests that stocks below PDH are excluded when PDH filter is enabled."""
        pipeline = StockSelectionPipeline(self.config, self.provider)
        s1 = "NSE:SBIN-EQ"
        pipeline.register_stock(s1)

        # LTP = 100.0, PDH = 101.0 (LTP <= PDH)
        self.provider.set_quote(s1, ltp=100.0, open_p=99.0, close_p=99.0, volume=50000)
        self.provider.set_pdh(s1, high=101.0, close=99.0)

        pipeline.update_live_market_data()
        qualified = pipeline.run_selection_and_filters()

        self.assertNotIn(s1, qualified)

    def test_initial_candles_ignored(self):
        """Verifies that the first N completed candles (default 3) are ignored."""
        engine = StrategyEngine(self.config, self.provider)
        stock = StockData("NSE:SBIN-EQ")
        engine.add_stock_to_track(stock)

        base_time = datetime(2026, 9, 7, 9, 15)

        # Complete 3 candles (all RED with low volume)
        for i in range(3):
            t_start = base_time + timedelta(minutes=5 * i)
            # Send ticks to build candle
            engine.process_tick("NSE:SBIN-EQ", 500.0, 1000, t_start)
            engine.process_tick("NSE:SBIN-EQ", 495.0, 1000, t_start + timedelta(minutes=4, seconds=59))

        self.assertEqual(len(stock.completed_candles), 2)  # 2 finalized, 3rd in progress
        # Finalize 3rd candle with tick in 4th candle time
        engine.process_tick("NSE:SBIN-EQ", 495.0, 1000, base_time + timedelta(minutes=15))

        self.assertEqual(len(stock.completed_candles), 3)
        self.assertIsNone(stock.signal_candle)  # First 3 candles ignored!

    def test_signal_candle_detection_and_immediate_breakout(self):
        """
        Tests:
        1. Identification of Red candle with lowest volume of the day after initial 3 candles.
        2. Immediate next candle breaking High triggers BUY entry.
        3. SL set to Signal Candle Low, Target set with 1:2 R:R.
        """
        engine = StrategyEngine(self.config, self.provider)
        stock = StockData("NSE:SBIN-EQ")
        engine.add_stock_to_track(stock)

        base_time = datetime(2026, 9, 7, 9, 15)

        # Complete 3 initial candles (ignored)
        for i in range(3):
            t = base_time + timedelta(minutes=5 * i)
            engine.process_tick("NSE:SBIN-EQ", 500.0, 10000, t)

        # Candle 4 (index 3): Red candle with lowest volume (volume = 1000)
        t4 = base_time + timedelta(minutes=15)
        engine.process_tick("NSE:SBIN-EQ", 500.0, 500, t4)           # Open 500
        engine.process_tick("NSE:SBIN-EQ", 502.0, 100, t4 + timedelta(seconds=30))  # High 502
        engine.process_tick("NSE:SBIN-EQ", 490.0, 200, t4 + timedelta(minutes=2))   # Low 490
        engine.process_tick("NSE:SBIN-EQ", 492.0, 200, t4 + timedelta(minutes=4, seconds=59)) # Close 492 (RED, Vol 1000)

        # Close Candle 4 by pushing tick for Candle 5
        t5 = base_time + timedelta(minutes=20)
        engine.process_tick("NSE:SBIN-EQ", 492.0, 100, t5)

        # Verify Candle 4 became Signal Candle!
        self.assertIsNotNone(stock.signal_candle)
        self.assertEqual(stock.signal_candle.high, 502.0)
        self.assertEqual(stock.signal_candle.low, 490.0)

        # Candle 5 (Immediate next candle): Break High of Signal Candle (502.0) -> Push LTP = 503.0
        engine.process_tick("NSE:SBIN-EQ", 503.0, 500, t5 + timedelta(seconds=10))

        # Check Position Executed!
        self.assertIsNotNone(stock.active_position)
        pos = stock.active_position
        self.assertEqual(pos.entry_price, 503.0)
        self.assertEqual(pos.stop_loss, 490.0)  # Signal Candle Low
        # Risk = 503 - 490 = 13. Target = 503 + (13 * 2) = 529.0
        self.assertEqual(pos.target, 529.0)

    def test_immediate_next_candle_invalidation(self):
        """Verifies that if immediate next candle closes without breaking High, Signal Candle is invalidated."""
        engine = StrategyEngine(self.config, self.provider)
        stock = StockData("NSE:SBIN-EQ")
        engine.add_stock_to_track(stock)

        base_time = datetime(2026, 9, 7, 9, 15)

        # 3 initial candles
        for i in range(3):
            t = base_time + timedelta(minutes=5 * i)
            engine.process_tick("NSE:SBIN-EQ", 500.0, 10000, t)

        # Candle 4: Red lowest volume (Vol=500, H=502, L=490)
        t4 = base_time + timedelta(minutes=15)
        engine.process_tick("NSE:SBIN-EQ", 500.0, 250, t4)
        engine.process_tick("NSE:SBIN-EQ", 502.0, 100, t4 + timedelta(seconds=30))
        engine.process_tick("NSE:SBIN-EQ", 490.0, 150, t4 + timedelta(minutes=4, seconds=59))

        # Push tick for Candle 5
        t5 = base_time + timedelta(minutes=20)
        engine.process_tick("NSE:SBIN-EQ", 491.0, 100, t5)

        self.assertIsNotNone(stock.signal_candle)

        # Candle 5 stays BELOW 502.0 (e.g. max high 500.0) and completes
        engine.process_tick("NSE:SBIN-EQ", 500.0, 500, t5 + timedelta(minutes=2))
        engine.process_tick("NSE:SBIN-EQ", 495.0, 500, t5 + timedelta(minutes=4, seconds=59))

        # Complete Candle 5 by sending tick for Candle 6
        t6 = base_time + timedelta(minutes=25)
        engine.process_tick("NSE:SBIN-EQ", 495.0, 100, t6)

        # Signal candle should be INVALIDATED because immediate next candle failed breakout
        self.assertIsNone(stock.signal_candle)
        self.assertTrue(stock.signal_candle_invalidated)
        self.assertIsNone(stock.active_position)

    def test_order_execution_and_position_exit(self):
        """Verifies market BUY order placement, Stop Loss exit, and Target exit execution."""
        engine = StrategyEngine(self.config, self.provider)
        stock = StockData("NSE:SBIN-EQ")
        engine.add_stock_to_track(stock)

        base_time = datetime(2026, 9, 7, 9, 15)

        # Build initial 3 candles
        for i in range(3):
            stock.completed_candles.append(Candle(base_time, 500, 505, 495, 500, 10000))

        # Signal Candle (Red, lowest vol = 500)
        signal_candle = Candle(base_time + timedelta(minutes=15), 500.0, 502.0, 490.0, 492.0, 500)
        stock.completed_candles.append(signal_candle)
        engine.on_candle_completed(stock, signal_candle, 3, base_time + timedelta(minutes=20))

        # Breakout Tick (503.0 > 502.0 High)
        t_entry = base_time + timedelta(minutes=20, seconds=5)
        engine.process_tick("NSE:SBIN-EQ", 503.0, 100, t_entry)

        # Verify BUY order was dispatched to provider
        self.assertEqual(len(self.provider.placed_orders), 1)
        self.assertEqual(self.provider.placed_orders[0]["side"], 1)  # 1 = BUY

        # Test Stop Loss Trigger (Price drops to 489.0 <= SL 490.0)
        engine.process_tick("NSE:SBIN-EQ", 489.0, 100, t_entry + timedelta(minutes=1))

        # Verify SELL exit order was dispatched
        self.assertEqual(len(self.provider.placed_orders), 2)
        self.assertEqual(self.provider.placed_orders[1]["side"], -1)  # -1 = SELL
        self.assertIsNone(stock.active_position)  # Position closed

    def test_signal_cutoff_time(self):
        """Verifies no new Signal Candles are accepted past 11:00 AM IST."""
        engine = StrategyEngine(self.config, self.provider)
        stock = StockData("NSE:SBIN-EQ")
        engine.add_stock_to_track(stock)

        cutoff_time = datetime(2026, 9, 7, 11, 5)  # 11:05 AM IST (Past 11:00 AM)

        # Create 3 initial candles
        for i in range(3):
            stock.completed_candles.append(Candle(cutoff_time, 500, 505, 495, 500, 10000))

        # Completed Red candle with lowest volume past cutoff time
        late_candle = Candle(cutoff_time, 500, 502, 490, 492, 100)
        stock.completed_candles.append(late_candle)

        engine.on_candle_completed(stock, late_candle, 3, cutoff_time)

        self.assertIsNone(stock.signal_candle)  # Rejected due to 11:00 AM cutoff!


if __name__ == "__main__":
    unittest.main()
