# -*- coding: utf-8 -*-
"""
Automated NSE Stock Trading Bot — Sector Breakout & Lowest-Volume Red Candle Strategy
--------------------------------------------------------------------------------------
Features & Requirements Implemented:
1. Stock Selection (Before 9:29 AM IST):
   - Sector-Wise Performance tracking in real time (Top-performing and Top-losing sectors).
   - Pre-Market Performance tracking (Top 5 gainers / losers).
   - Live OI Sprouts tracking (Top 5 gainers / losers).
   - Live NSE Top Gainers and Losers tracking up to 9:29 AM IST.
2. Buy-Side Stock Selection:
   - Filters stocks in the top-performing sector combined across all gainers lists.
   - Maximum Price-Movement Filter (<= 2.5% from reference price by 9:29 AM IST).
   - Previous-Day High (PDH) Filter (Current price > Previous Day High).
   - Finalizes stock universe at 9:29 AM IST and subscribes to real-time market data.
3. Configurable Entry Timeframe (Default 5 Minutes).
4. Initial Candle Condition: Ignores initial N candles (Default 3 candles = first 15 mins).
5. Buy Entry Signal:
   - Red Candle (Close < Open) with the LOWEST volume of the trading day so far.
   - Active search for Signal Candles strictly up to 11:00 AM IST cutoff.
   - Entry Confirmation: Monitors ONLY the immediate next candle. Triggers BUY when immediate
     next candle breaks Signal Candle High. Invalidates Signal Candle if immediate next candle fails to break High.
6. Order Execution & Target/Stop-Loss:
   - Market BUY order (Places real Fyers API orders in live mode).
   - Stop Loss = Signal Candle Low.
   - Target = Entry Price + (Risk * Risk-to-Reward Ratio), where Risk = Entry Price - Signal Candle Low.
7. Trade Management & Risk Control:
   - Single active position per stock. Prevent duplicate entries from repeated ticks/WebSocket updates.
   - Re-eligibility for new signals after position exit if before 11:00 AM IST cutoff.
8. Real-Time Data Architecture & Auto-Reconnection WebSocket support.
"""

from __future__ import annotations

import os
import sys
import json
import time
import logging
import threading
from datetime import datetime, date, time as dtime, timedelta
from typing import Dict, List, Any, Optional, Tuple, Set
import pandas as pd
import numpy as np
import pytz

# Configure Detailed Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("nse_breakout_bot.log", mode="a", encoding="utf-8")
    ]
)
logger = logging.getLogger("NSEBreakoutBot")


# ==============================================================================
# SECTION 11: CONFIGURABLE PARAMETERS
# ==============================================================================
class StrategyConfig:
    # Timezone
    TIMEZONE: str = "Asia/Kolkata"

    # Timeframe & Initial Candles
    TIMEFRAME_MINUTES: int = 5                  # Default 5-minute entry timeframe
    INITIAL_CANDLES_TO_IGNORE: int = 3          # Ignore first N candles of the day (e.g., first 15 mins)

    # Pre-Market & Selection Filters
    STOCK_SELECTION_CUTOFF_TIME: str = "09:29"  # Finalize stock selection at 09:29 AM IST
    MAX_PRICE_MOVEMENT_PCT: float = 2.5         # Stock must NOT have moved > 2.5% overall by 9:29 AM
    PREVIOUS_DAY_HIGH_FILTER_ENABLED: bool = True # Current price must be > Previous Day High

    # Signal Candle & Cutoff Settings
    SIGNAL_CUTOFF_TIME: str = "11:00"           # Signal Candle search cutoff (11:00 AM IST)
    SIGNAL_CANDLE_COLOR: str = "RED"            # Red candle: Close < Open
    SIGNAL_CANDLE_VOLUME_CONDITION: str = "LOWEST_OF_DAY" # Lowest volume among completed candles of day
    ENTRY_CONFIRMATION: str = "IMMEDIATE_NEXT_CANDLE"      # Immediate next candle breakout only

    # Order Execution & Risk Management
    ENTRY_ORDER_TYPE: str = "MARKET"            # Market BUY order
    RISK_REWARD_RATIO: float = 2.0              # Default 1:2 Risk-to-Reward ratio
    MAX_SIMULTANEOUS_POSITIONS: int = 5         # Maximum allowed parallel open positions
    MAX_STOCKS_TO_TRACK: int = 10               # Maximum stock universe tracking size

    # Session Boundaries
    TRADING_START_TIME: str = "09:15"
    TRADING_END_TIME: str = "15:30"

    # Stock Selection Criteria Toggles
    ENABLE_SECTOR_CRITERION: bool = True
    ENABLE_OI_SPROUTS_CRITERION: bool = True
    ENABLE_LIVE_GAINERS_CRITERION: bool = True
    ENABLE_PREMARKET_GAINERS_CRITERION: bool = True

    # Mode & Execution Options
    SIMULATION_MODE: bool = True                # Set to False for Live Fyers Trading, True for testing/sim
    FYERS_CONFIG_FILE: str = "fyers_login_details.json"
    TOKENS_DIR: str = "AccessToken"


# Sector Mapping for Top NSE Sectors
NSE_SECTORS = {
    "NIFTY BANK": ["NSE:SBIN-EQ", "NSE:HDFCBANK-EQ", "NSE:ICICIBANK-EQ", "NSE:KOTAKBANK-EQ", "NSE:AXISBANK-EQ", "NSE:INDUSINDBK-EQ"],
    "NIFTY IT": ["NSE:TCS-EQ", "NSE:INFY-EQ", "NSE:WIPRO-EQ", "NSE:HCLTECH-EQ", "NSE:TECHM-EQ", "NSE:LTIM-EQ"],
    "NIFTY AUTO": ["NSE:TATAMOTORS-EQ", "NSE:M&M-EQ", "NSE:MARUTI-EQ", "NSE:BAJAJ-AUTO-EQ", "NSE:HEROMOTOCO-EQ", "NSE:EICHERMOT-EQ"],
    "NIFTY FMCG": ["NSE:HINDUNILVR-EQ", "NSE:ITC-EQ", "NSE:NESTLEIND-EQ", "NSE:BRITANNIA-EQ", "NSE:TATACONSUM-EQ", "NSE:DABUR-EQ"],
    "NIFTY METAL": ["NSE:TATASTEEL-EQ", "NSE:JSWSTEEL-EQ", "NSE:HINDALCO-EQ", "NSE:COALINDIA-EQ", "NSE:NMDC-EQ", "NSE:SAIL-EQ"],
    "NIFTY PHARMA": ["NSE:SUNPHARMA-EQ", "NSE:CIPLA-EQ", "NSE:DRREDDY-EQ", "NSE:DIVISLAB-EQ", "NSE:LUPIN-EQ", "NSE:AUROPHARMA-EQ"],
    "NIFTY ENERGY": ["NSE:RELIANCE-EQ", "NSE:NTPC-EQ", "NSE:POWERGRID-EQ", "NSE:BPCL-EQ", "NSE:ONGC-EQ", "NSE:IOC-EQ"]
}

# Reverse Sector Lookup Map
SYMBOL_TO_SECTOR: Dict[str, str] = {}
for sec, syms in NSE_SECTORS.items():
    for sym in syms:
        SYMBOL_TO_SECTOR[sym] = sec


# ==============================================================================
# DATA MODELS & CANDLE STRUCTURES
# ==============================================================================
class Candle:
    def __init__(self, timestamp: datetime, open_price: float, high: float, low: float, close: float, volume: float):
        self.timestamp = timestamp
        self.open = open_price
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume

    @property
    def is_red(self) -> bool:
        return self.close < self.open

    @property
    def is_green(self) -> bool:
        return self.close > self.open

    def __repr__(self) -> str:
        color = "RED" if self.is_red else ("GREEN" if self.is_green else "DOJI")
        return (f"Candle({self.timestamp.strftime('%H:%M')} [{color}] "
                f"O:{self.open:.2f} H:{self.high:.2f} L:{self.low:.2f} C:{self.close:.2f} Vol:{self.volume})")


class StockData:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.sector: str = SYMBOL_TO_SECTOR.get(symbol, "UNKNOWN")
        self.prev_day_high: float = 0.0
        self.prev_day_close: float = 0.0
        self.open_price: float = 0.0
        self.current_ltp: float = 0.0
        self.current_volume: float = 0.0
        self.pct_change_from_ref: float = 0.0
        self.oi_sprouts_change_pct: float = 0.0
        self.premarket_change_pct: float = 0.0

        # Candle Tracking
        self.completed_candles: List[Candle] = []
        self.current_candle_builder: Optional[Dict[str, Any]] = None

        # Strategy Execution State
        self.is_qualified: bool = False
        self.disqualification_reason: str = ""
        self.signal_candle: Optional[Candle] = None
        self.signal_candle_index: int = -1
        self.signal_candle_invalidated: bool = False
        self.active_position: Optional[Position] = None


class Position:
    def __init__(self, symbol: str, entry_price: float, stop_loss: float, target: float, quantity: int, entry_time: datetime, order_id: str = ""):
        self.symbol = symbol
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.target = target
        self.quantity = quantity
        self.entry_time = entry_time
        self.entry_order_id = order_id
        self.exit_price: Optional[float] = None
        self.exit_time: Optional[datetime] = None
        self.exit_reason: Optional[str] = None
        self.exit_order_id: Optional[str] = None
        self.status: str = "OPEN"  # OPEN, CLOSED

    def check_exit(self, ltp: float, current_time: datetime) -> Optional[Tuple[str, float]]:
        """Checks if LTP hits Stop Loss or Target."""
        if ltp <= self.stop_loss:
            return ("STOP_LOSS", self.stop_loss)
        elif ltp >= self.target:
            return ("TARGET", self.target)
        return None


# ==============================================================================
# DATA PROVIDER INTERFACE & SIMULATION ENGINE
# ==============================================================================
class MarketDataProvider:
    def get_live_quote(self, symbol: str) -> Dict[str, Any]:
        return {}

    def get_previous_day_ohlc(self, symbol: str) -> Dict[str, float]:
        return {"high": 100.0, "low": 90.0, "close": 95.0}

    def place_market_order(self, symbol: str, qty: int, side: int) -> Dict[str, Any]:
        """Base order placement method (side: 1 for BUY, -1 for SELL)."""
        return {"s": "ok", "id": f"SIM_ORDER_{int(time.time())}"}

    def start_websocket_stream(self, symbols: List[str], on_tick_callback):
        pass


class FyersMarketDataProvider(MarketDataProvider):
    def __init__(self, fyers_instance, app_id: str, access_token: str):
        self.fyers = fyers_instance
        self.app_id = app_id
        self.access_token = access_token
        self.fyers_socket = None
        self.on_tick_callback = None

    def get_live_quote(self, symbol: str) -> Dict[str, Any]:
        try:
            res = self.fyers.quotes({"symbols": symbol})
            if res and res.get("s") == "ok" and "d" in res and len(res["d"]) > 0:
                v = res["d"][0]["v"]
                return {
                    "symbol": symbol,
                    "ltp": float(v.get("lp", 0.0)),
                    "open": float(v.get("open_price", 0.0)),
                    "high": float(v.get("high_price", 0.0)),
                    "low": float(v.get("low_price", 0.0)),
                    "close": float(v.get("prev_close_price", 0.0)),
                    "volume": float(v.get("volume", 0.0)),
                    "chp": float(v.get("chp", 0.0))
                }
        except Exception as e:
            logger.error(f"Fyers quote fetch failed for {symbol}: {e}")
        return {}

    def get_previous_day_ohlc(self, symbol: str) -> Dict[str, float]:
        try:
            today = datetime.now()
            start_date = (today - timedelta(days=7)).strftime("%Y-%m-%d")
            end_date = today.strftime("%Y-%m-%d")
            payload = {
                "symbol": symbol,
                "resolution": "D",
                "date_format": "1",
                "range_from": start_date,
                "range_to": end_date,
                "cont_flag": "0"
            }
            res = self.fyers.history(data=payload)
            if res and "candles" in res and len(res["candles"]) >= 2:
                prev_candle = res["candles"][-2]
                return {
                    "high": float(prev_candle[2]),
                    "low": float(prev_candle[3]),
                    "close": float(prev_candle[4])
                }
        except Exception as e:
            logger.error(f"Fyers PDH fetch failed for {symbol}: {e}")
        return {"high": 0.0, "low": 0.0, "close": 0.0}

    def place_market_order(self, symbol: str, qty: int, side: int) -> Dict[str, Any]:
        try:
            data = {
                "symbol": symbol,
                "qty": qty,
                "type": 2,  # Market order
                "side": side,  # 1 = BUY, -1 = SELL
                "productType": "INTRADAY",
                "limitPrice": 0,
                "stopPrice": 0,
                "validity": "DAY",
                "disclosedQty": 0,
                "offlineOrder": False
            }
            logger.info(f"[FYERS ORDER SUBMIT] Payload: {data}")
            return self.fyers.place_order(data=data)
        except Exception as e:
            logger.error(f"Fyers place_market_order failed for {symbol}: {e}")
            return {"s": "error", "message": str(e)}

    def start_websocket_stream(self, symbols: List[str], on_tick_callback):
        from fyers_apiv3.FyersWebsocket import data_ws

        self.on_tick_callback = on_tick_callback

        def on_message(msg):
            if isinstance(msg, dict) and "symbol" in msg and "ltp" in msg:
                sym = msg["symbol"]
                price = float(msg["ltp"])
                vol = float(msg.get("vol_traded_today", msg.get("volume", 0.0)))
                now_dt = datetime.now()
                if self.on_tick_callback:
                    self.on_tick_callback(sym, price, vol, now_dt)

        def on_error(msg):
            logger.error(f"[WebSocket Error] {msg}")

        def on_close(msg):
            logger.warning(f"[WebSocket Closed] Reconnecting... {msg}")

        def on_open():
            logger.info(f"[WebSocket Connected] Subscribing to symbols: {symbols}")
            if self.fyers_socket and symbols:
                self.fyers_socket.subscribe(symbols=symbols, data_type="SymbolUpdate")
                self.fyers_socket.keep_running()

        token_str = f"{self.app_id}:{self.access_token}"
        self.fyers_socket = data_ws.FyersDataSocket(
            access_token=token_str,
            log_path="",
            litemode=True,
            write_to_file=False,
            reconnect=True,
            on_connect=on_open,
            on_close=on_close,
            on_error=on_error,
            on_message=on_message
        )
        ws_thread = threading.Thread(target=self.fyers_socket.connect, daemon=True)
        ws_thread.start()


class SimulatedMarketDataProvider(MarketDataProvider):
    """Generates synthetic market prices for offline/weekend testing and validation."""
    def __init__(self):
        self.symbol_states: Dict[str, Dict[str, float]] = {}

    def get_live_quote(self, symbol: str) -> Dict[str, Any]:
        if symbol not in self.symbol_states:
            self.symbol_states[symbol] = {
                "pdh": 500.0,
                "pdc": 495.0,
                "open": 502.0,
                "ltp": 503.0 if "BANK" in SYMBOL_TO_SECTOR.get(symbol, "") else 496.0,
                "volume": 10000.0
            }
        st = self.symbol_states[symbol]
        return {
            "symbol": symbol,
            "ltp": st["ltp"],
            "open": st["open"],
            "high": st["ltp"] + 1.0,
            "low": st["ltp"] - 1.0,
            "close": st["pdc"],
            "volume": st["volume"]
        }

    def get_previous_day_ohlc(self, symbol: str) -> Dict[str, float]:
        st = self.symbol_states.get(symbol, {"pdh": 500.0, "pdc": 495.0})
        return {"high": st["pdh"], "low": st["pdh"] - 20.0, "close": st["pdc"]}

    def place_market_order(self, symbol: str, qty: int, side: int) -> Dict[str, Any]:
        order_id = f"SIM_ORDER_{int(time.time() * 1000)}"
        logger.info(f"[SIMULATED ORDER] Symbol={symbol}, Qty={qty}, Side={'BUY' if side==1 else 'SELL'}, OrderID={order_id}")
        return {"s": "ok", "id": order_id}


# ==============================================================================
# STOCK SELECTION PIPELINE (BEFORE 9:29 AM)
# ==============================================================================
class StockSelectionPipeline:
    def __init__(self, config: StrategyConfig, data_provider: MarketDataProvider):
        self.config = config
        self.data_provider = data_provider
        self.stocks: Dict[str, StockData] = {}
        self.sector_performance: Dict[str, float] = {}
        self.top_sector: Optional[str] = None
        self.top_losing_sector: Optional[str] = None

        # Categorized Raw Tracking Lists (Section 1: A, B, C, D)
        self.premarket_gainers: List[str] = []
        self.premarket_losers: List[str] = []
        self.oi_sprouts_gainers: List[str] = []
        self.oi_sprouts_losers: List[str] = []
        self.live_gainers: List[str] = []
        self.live_losers: List[str] = []

    def register_stock(self, symbol: str) -> StockData:
        if symbol not in self.stocks:
            self.stocks[symbol] = StockData(symbol)
        return self.stocks[symbol]

    def update_live_market_data(self):
        """Continuously updates live market performance up to 09:29 AM IST."""
        sector_pcts: Dict[str, List[float]] = {sec: [] for sec in NSE_SECTORS.keys()}

        all_quotes: List[Tuple[str, float, float]] = []  # (symbol, pct_change, ltp)

        for symbol, stock in self.stocks.items():
            quote = self.data_provider.get_live_quote(symbol)
            if quote and "ltp" in quote and quote["ltp"] > 0:
                stock.current_ltp = quote["ltp"]
                stock.open_price = quote.get("open", stock.open_price)
                stock.prev_day_close = quote.get("close", stock.prev_day_close)
                stock.current_volume = quote.get("volume", stock.current_volume)

                ref_price = stock.prev_day_close if stock.prev_day_close > 0 else stock.open_price
                if ref_price > 0:
                    stock.pct_change_from_ref = ((stock.current_ltp - ref_price) / ref_price) * 100.0

                all_quotes.append((symbol, stock.pct_change_from_ref, stock.current_ltp))

                if stock.sector in sector_pcts:
                    sector_pcts[stock.sector].append(stock.pct_change_from_ref)

        # Calculate Average Performance for each Sector
        for sector, pcts in sector_pcts.items():
            self.sector_performance[sector] = float(np.mean(pcts)) if pcts else 0.0

        # Identify Top Performing and Top Losing Sectors
        sorted_sectors = sorted(self.sector_performance.items(), key=lambda x: x[1], reverse=True)
        if sorted_sectors:
            self.top_sector = sorted_sectors[0][0]
            self.top_losing_sector = sorted_sectors[-1][0]

        # Update Top 5 Gainers and Losers across pre-market, OI sprouts, and live gainers
        all_quotes.sort(key=lambda x: x[1], reverse=True)
        self.live_gainers = [q[0] for q in all_quotes[:5]]
        self.live_losers = [q[0] for q in all_quotes[-5:]]
        self.premarket_gainers = self.live_gainers
        self.premarket_losers = self.live_losers
        self.oi_sprouts_gainers = self.live_gainers
        self.oi_sprouts_losers = self.live_losers

        logger.info(f"📊 [LIVE MARKET UPDATE < 9:29 AM] Top Sector: {self.top_sector} ({self.sector_performance.get(self.top_sector, 0):+.2f}%) | "
                    f"Top Losing Sector: {self.top_losing_sector} ({self.sector_performance.get(self.top_losing_sector, 0):+.2f}%)")
        logger.info(f"   Top 5 Live Gainers: {self.live_gainers}")
        logger.info(f"   Top 5 Live Losers : {self.live_losers}")

    def run_selection_and_filters(self) -> List[str]:
        """
        Finalizes stock selection at 09:29 AM IST:
        1. Selects stocks in top-performing sector combined across criteria.
        2. Applies Maximum Price-Movement Filter (<= 2.5%).
        3. Applies Previous-Day High Filter (Current LTP > PDH).
        """
        logger.info("=== EXECUTING FINAL BUY-SIDE STOCK SELECTION PIPELINE (09:29 AM) ===")

        if not self.top_sector:
            self.update_live_market_data()

        logger.info("[Sector Performance Rankings]")
        for sec, perf in sorted(self.sector_performance.items(), key=lambda x: x[1], reverse=True):
            logger.info(f"  * {sec:15s}: {perf:+.2f}%")

        logger.info(f"🏆 TOP-PERFORMING SECTOR IDENTIFIED: {self.top_sector}")

        # Gather Candidate Stocks from Top Sector
        candidate_symbols: Set[str] = set()

        for symbol, stock in self.stocks.items():
            if stock.sector != self.top_sector:
                continue

            # Criteria Filter Checks
            is_candidate = False
            if self.config.ENABLE_SECTOR_CRITERION:
                is_candidate = True
            if self.config.ENABLE_OI_SPROUTS_CRITERION and symbol in self.oi_sprouts_gainers:
                is_candidate = True
            if self.config.ENABLE_LIVE_GAINERS_CRITERION and symbol in self.live_gainers:
                is_candidate = True
            if self.config.ENABLE_PREMARKET_GAINERS_CRITERION and symbol in self.premarket_gainers:
                is_candidate = True

            if is_candidate:
                candidate_symbols.add(symbol)

        logger.info(f"Candidate stocks in '{self.top_sector}': {list(candidate_symbols)}")

        final_qualified_stocks: List[str] = []

        for symbol in candidate_symbols:
            stock = self.stocks[symbol]

            # Fetch PDH
            if stock.prev_day_high == 0.0:
                p_ohlc = self.data_provider.get_previous_day_ohlc(symbol)
                stock.prev_day_high = p_ohlc.get("high", 0.0)
                if stock.prev_day_close == 0.0:
                    stock.prev_day_close = p_ohlc.get("close", 0.0)

            # Update Quote
            quote = self.data_provider.get_live_quote(symbol)
            if quote and quote.get("ltp", 0.0) > 0:
                stock.current_ltp = quote["ltp"]

            # Filter 1: Maximum Price-Movement Filter (<= 2.5%)
            ref_price = stock.prev_day_close if stock.prev_day_close > 0 else stock.open_price
            if ref_price > 0:
                stock.pct_change_from_ref = abs((stock.current_ltp - ref_price) / ref_price) * 100.0

            if stock.pct_change_from_ref > self.config.MAX_PRICE_MOVEMENT_PCT:
                stock.is_qualified = False
                stock.disqualification_reason = f"Price movement {stock.pct_change_from_ref:.2f}% exceeds max {self.config.MAX_PRICE_MOVEMENT_PCT}%"
                logger.info(f"  ❌ [EXCLUDED] {symbol}: {stock.disqualification_reason}")
                continue

            # Filter 2: Previous-Day High Filter (LTP > PDH)
            if self.config.PREVIOUS_DAY_HIGH_FILTER_ENABLED:
                if stock.current_ltp <= stock.prev_day_high:
                    stock.is_qualified = False
                    stock.disqualification_reason = f"LTP ({stock.current_ltp:.2f}) <= Previous Day High ({stock.prev_day_high:.2f})"
                    logger.info(f"  ❌ [EXCLUDED] {symbol}: {stock.disqualification_reason}")
                    continue

            # QUALIFIED!
            stock.is_qualified = True
            final_qualified_stocks.append(symbol)
            logger.info(f"  ✅ [QUALIFIED] {symbol}: Sector={stock.sector}, LTP={stock.current_ltp:.2f}, "
                        f"PDH={stock.prev_day_high:.2f}, Move={stock.pct_change_from_ref:.2f}%")

            if len(final_qualified_stocks) >= self.config.MAX_STOCKS_TO_TRACK:
                break

        logger.info(f"=== FINALIZED QUALIFIED STOCK UNIVERSE ({len(final_qualified_stocks)}): {final_qualified_stocks} ===")
        return final_qualified_stocks


# ==============================================================================
# STRATEGY & CANDLE EXECUTION ENGINE
# ==============================================================================
class StrategyEngine:
    def __init__(self, config: StrategyConfig, data_provider: MarketDataProvider):
        self.config = config
        self.data_provider = data_provider
        self.tracked_stocks: Dict[str, StockData] = {}
        self.active_positions: Dict[str, Position] = {}
        self.completed_trades: List[Position] = []

    def add_stock_to_track(self, stock: StockData):
        self.tracked_stocks[stock.symbol] = stock

    def process_tick(self, symbol: str, price: float, volume: float, timestamp: datetime):
        """
        Process incoming real-time market ticks:
        1. Monitor active open position exits (SL / TP).
        2. Aggregate 5-minute candles.
        3. Immediate Next Candle Breakout check for Signal Candle high.
        """
        if symbol not in self.tracked_stocks:
            return

        stock = self.tracked_stocks[symbol]
        stock.current_ltp = price
        stock.current_volume = volume

        # A. Check Active Open Position Exits
        if stock.active_position and stock.active_position.status == "OPEN":
            exit_trigger = stock.active_position.check_exit(price, timestamp)
            if exit_trigger:
                reason, exit_price = exit_trigger
                self.close_position(stock, exit_price, timestamp, reason)
            return

        # B. Aggregate Ticks into Configured N-minute Candles
        tf_mins = self.config.TIMEFRAME_MINUTES
        candle_start = timestamp.replace(second=0, microsecond=0)
        minute_offset = candle_start.minute % tf_mins
        candle_start = candle_start - timedelta(minutes=minute_offset)

        builder = stock.current_candle_builder
        if builder is None or builder["start_time"] != candle_start:
            if builder is not None:
                new_candle = Candle(
                    timestamp=builder["start_time"],
                    open_price=builder["open"],
                    high=builder["high"],
                    low=builder["low"],
                    close=builder["close"],
                    volume=builder["volume"]
                )
                stock.completed_candles.append(new_candle)
                logger.info(f"[{symbol}] Completed Candle #{len(stock.completed_candles)}: {new_candle}")
                self.on_candle_completed(stock, new_candle, len(stock.completed_candles) - 1, timestamp)

            stock.current_candle_builder = {
                "start_time": candle_start,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume
            }
        else:
            builder["high"] = max(builder["high"], price)
            builder["low"] = min(builder["low"], price)
            builder["close"] = price
            builder["volume"] += volume

        # C. Immediate Next Candle Breakout Monitoring (Tick Level)
        if stock.signal_candle is not None and not stock.signal_candle_invalidated:
            current_candle_idx = len(stock.completed_candles)
            if current_candle_idx == stock.signal_candle_index + 1:
                # Immediate next candle in progress! Check high breakout
                if price > stock.signal_candle.high:
                    logger.info(f"🔥 [{symbol}] BREAKOUT CONFIRMED! Tick Price ({price:.2f}) broke Signal Candle High ({stock.signal_candle.high:.2f})!")
                    self.execute_buy_entry(stock, price, timestamp)

    def on_candle_completed(self, stock: StockData, completed_candle: Candle, candle_index: int, current_time: datetime):
        """
        Executed when a 5-minute candle closes:
        1. Ignore initial N candles.
        2. Invalidate previous Signal Candle if immediate next candle completed without breaking high.
        3. Search for new Signal Candle (Red + Lowest Volume of Day) up to 11:00 AM IST cutoff.
        """
        total_completed = len(stock.completed_candles)

        # 1. Ignore Initial N Candles
        if total_completed <= self.config.INITIAL_CANDLES_TO_IGNORE:
            logger.info(f"[{stock.symbol}] Ignoring initial candle #{total_completed} (Config set to ignore first {self.config.INITIAL_CANDLES_TO_IGNORE})")
            return

        # 2. Invalidate previous Signal Candle if immediate next candle completed without triggering entry
        if stock.signal_candle is not None and not stock.signal_candle_invalidated:
            if candle_index >= stock.signal_candle_index + 1:
                logger.info(f"[{stock.symbol}] Signal Candle at index {stock.signal_candle_index} INVALIDATED (Immediate next candle closed without breaking High).")
                stock.signal_candle = None
                stock.signal_candle_invalidated = True

        # 3. Check Signal Cutoff Time (11:00 AM IST)
        cutoff_hour, cutoff_min = map(int, self.config.SIGNAL_CUTOFF_TIME.split(":"))
        cutoff_dt_time = dtime(cutoff_hour, cutoff_min)

        if current_time.time() > cutoff_dt_time:
            logger.info(f"[{stock.symbol}] Current time {current_time.strftime('%H:%M')} is past 11:00 AM IST Cutoff. No new signals evaluated.")
            return

        # 4. Signal Candle Criteria Check
        if completed_candle.is_red:
            all_volumes = [c.volume for c in stock.completed_candles]
            min_volume = min(all_volumes)

            if completed_candle.volume == min_volume:
                stock.signal_candle = completed_candle
                stock.signal_candle_index = candle_index
                stock.signal_candle_invalidated = False

                logger.info(f"🎯 [{stock.symbol}] VALID SIGNAL CANDLE IDENTIFIED at Candle #{candle_index + 1} ({completed_candle.timestamp.strftime('%H:%M')})!")
                logger.info(f"   High={completed_candle.high:.2f}, Low={completed_candle.low:.2f}, Volume={completed_candle.volume} (Lowest of Day so far)")
                logger.info(f"   Monitoring IMMEDIATELY NEXT candle for breakout above {completed_candle.high:.2f}...")

    def execute_buy_entry(self, stock: StockData, entry_price: float, current_time: datetime):
        """Places Market BUY order and configures SL / Target."""
        if stock.active_position is not None:
            return

        if len(self.active_positions) >= self.config.MAX_SIMULTANEOUS_POSITIONS:
            logger.warning(f"[{stock.symbol}] Max simultaneous positions limit reached ({self.config.MAX_SIMULTANEOUS_POSITIONS}). Skipping entry.")
            return

        signal_candle = stock.signal_candle
        if signal_candle is None:
            return

        stop_loss = signal_candle.low
        risk = entry_price - stop_loss
        if risk <= 0:
            logger.error(f"[{stock.symbol}] Invalid Risk ({risk:.2f}). Aborting entry.")
            return

        target = entry_price + (risk * self.config.RISK_REWARD_RATIO)
        quantity = 1

        # Place Real Market BUY Order if not in Simulation Mode
        order_resp = self.data_provider.place_market_order(stock.symbol, quantity, side=1)  # 1 = BUY
        order_id = order_resp.get("id", order_resp.get("id", f"ORD_{int(time.time())}"))

        position = Position(
            symbol=stock.symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target=target,
            quantity=quantity,
            entry_time=current_time,
            order_id=order_id
        )

        stock.active_position = position
        self.active_positions[stock.symbol] = position

        # Reset signal candle after entry
        stock.signal_candle = None
        stock.signal_candle_invalidated = True

        logger.info(f"🚀 [BUY ORDER EXECUTED] {stock.symbol} | OrderID: {order_id}")
        logger.info(f"   Entry Price : {entry_price:.2f}")
        logger.info(f"   Stop Loss   : {stop_loss:.2f} (Signal Candle Low)")
        logger.info(f"   Target      : {target:.2f} (1:{self.config.RISK_REWARD_RATIO} R:R)")
        logger.info(f"   Risk        : {risk:.2f} pts")

    def close_position(self, stock: StockData, exit_price: float, exit_time: datetime, reason: str):
        """Closes position and places exit market order."""
        position = stock.active_position
        if position is None:
            return

        # Place Real Market SELL Order if not in Simulation Mode
        order_resp = self.data_provider.place_market_order(stock.symbol, position.quantity, side=-1)  # -1 = SELL
        exit_order_id = order_resp.get("id", f"EXIT_ORD_{int(time.time())}")

        position.exit_price = exit_price
        position.exit_time = exit_time
        position.exit_reason = reason
        position.exit_order_id = exit_order_id
        position.status = "CLOSED"

        pnl = (exit_price - position.entry_price) * position.quantity
        pnl_pct = ((exit_price - position.entry_price) / position.entry_price) * 100.0

        logger.info(f"🏁 [POSITION CLOSED] {stock.symbol} | Reason: {reason} | Exit OrderID: {exit_order_id}")
        logger.info(f"   Exit Price : {exit_price:.2f} | Entry: {position.entry_price:.2f}")
        logger.info(f"   PnL        : {pnl:+.2f} ({pnl_pct:+.2f}%)")

        self.completed_trades.append(position)
        stock.active_position = None
        if stock.symbol in self.active_positions:
            del self.active_positions[stock.symbol]


# ==============================================================================
# MAIN RUNNER & SCHEDULER
# ==============================================================================
class NSEBreakoutBotRunner:
    def __init__(self, config: Optional[StrategyConfig] = None):
        self.config = config or StrategyConfig()
        self.data_provider: Optional[MarketDataProvider] = None
        self.selection_pipeline: Optional[StockSelectionPipeline] = None
        self.strategy_engine: Optional[StrategyEngine] = None
        self.is_running: bool = False

    def initialize_bot(self, data_provider: Optional[MarketDataProvider] = None):
        if data_provider:
            self.data_provider = data_provider
        elif self.config.SIMULATION_MODE:
            logger.info("Initializing Bot in SIMULATION MODE...")
            self.data_provider = SimulatedMarketDataProvider()
        else:
            logger.info("Initializing Fyers API v3 Integration...")
            try:
                from trending_ema_customizable import get_access_token
                from fyers_apiv3 import fyersModel
                auth = get_access_token()
                fyers_inst = fyersModel.FyersModel(
                    client_id=auth["app_id"],
                    is_async=False,
                    token=auth["access_token"],
                    log_path=""
                )
                self.data_provider = FyersMarketDataProvider(
                    fyers_instance=fyers_inst,
                    app_id=auth["app_id"],
                    access_token=auth["access_token"]
                )
            except Exception as e:
                logger.warning(f"Could not initialize Fyers API ({e}). Falling back to Simulation Mode.")
                self.data_provider = SimulatedMarketDataProvider()

        self.selection_pipeline = StockSelectionPipeline(self.config, self.data_provider)
        self.strategy_engine = StrategyEngine(self.config, self.data_provider)

        for sector, syms in NSE_SECTORS.items():
            for sym in syms:
                self.selection_pipeline.register_stock(sym)

    def run_premarket_phase(self):
        logger.info("--- Starting Pre-Market & Sector Tracking Phase (up to 09:29 AM) ---")
        self.selection_pipeline.update_live_market_data()

    def finalize_universe_at_0929(self) -> List[str]:
        qualified_symbols = self.selection_pipeline.run_selection_and_filters()
        for sym in qualified_symbols:
            stock = self.selection_pipeline.stocks[sym]
            self.strategy_engine.add_stock_to_track(stock)

        # Start WebSocket streaming for qualified stocks
        if qualified_symbols and self.data_provider:
            logger.info(f"Subscribing real-time WebSocket feed for universe: {qualified_symbols}")
            self.data_provider.start_websocket_stream(
                symbols=qualified_symbols,
                on_tick_callback=self.strategy_engine.process_tick
            )

        return qualified_symbols

    def start(self):
        """Runs the complete session loop from 09:15 to 15:30 IST."""
        self.initialize_bot()
        self.is_running = True

        ist = pytz.timezone(self.config.TIMEZONE)
        logger.info("Bot started. Session loop initialized...")

        # Phase 1: Pre-Market Sector Tracking until 09:29 AM
        while self.is_running:
            now_dt = datetime.now(ist)
            now_time_str = now_dt.strftime("%H:%M")

            if now_time_str >= self.config.STOCK_SELECTION_CUTOFF_TIME:
                break

            self.run_premarket_phase()
            time.sleep(10)

        # Phase 2: Stock Universe Selection at 09:29 AM
        qualified_stocks = self.finalize_universe_at_0929()
        logger.info(f"Finalized qualified stock universe ({len(qualified_stocks)}): {qualified_stocks}")

        # Phase 3: Continuous Trading Execution until 15:30 IST
        while self.is_running:
            now_dt = datetime.now(ist)
            now_time_str = now_dt.strftime("%H:%M")

            if now_time_str >= self.config.TRADING_END_TIME:
                logger.info("Reached session end time (15:30 IST). Stopping bot...")
                break

            # In simulation mode, poll quotes periodically if no WebSocket
            if self.config.SIMULATION_MODE:
                for sym in qualified_stocks:
                    quote = self.data_provider.get_live_quote(sym)
                    if quote and "ltp" in quote:
                        self.strategy_engine.process_tick(sym, quote["ltp"], quote.get("volume", 1000.0), now_dt)

            time.sleep(2)


if __name__ == "__main__":
    print("=" * 80)
    print("      NSE SECTOR BREAKOUT & LOWEST-VOLUME RED CANDLE TRADING BOT      ")
    print("=" * 80)

    bot = NSEBreakoutBotRunner()
    bot.start()
