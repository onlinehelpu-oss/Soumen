import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta
import logging
import threading
import sys
import json
import os

# ============================== LOAD CONFIGURATION ==========================
DEFAULT_CONFIG = {
    "MIN_CHANGE_PCT": 5.0,
    "TIMEFRAME_MIN": 30,
    "EMA_FAST_PERIOD": 9,
    "EMA_SLOW_PERIOD": 15,
    "EMA_EXIT_PERIOD": 50,
    "ATR_PERIOD": 14,
    "SWING_LOOKBACK": 50,
    "BUFFER_POINTS": 5,
    "MAGIC_NUMBER": 888888,
    "LOT_SIZE": 0.1,
    "MIN_RANGE_PCT": 0.0,
    "MAX_RANGE_PCT": 2.0
}

def load_config():
    if os.path.exists("config.json"):
        try:
            with open("config.json", "r") as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except Exception as e:
            print(f"Error loading config.json: {e}. Using defaults.")
    return DEFAULT_CONFIG

CONFIG = load_config()

# Map minutes to MT5 timeframe constants
TIMEFRAME_MAP = {
    1: mt5.TIMEFRAME_M1, 5: mt5.TIMEFRAME_M5, 15: mt5.TIMEFRAME_M15,
    30: mt5.TIMEFRAME_M30, 60: mt5.TIMEFRAME_H1, 240: mt5.TIMEFRAME_H4,
    1440: mt5.TIMEFRAME_D1
}
TIMEFRAME = TIMEFRAME_MAP.get(CONFIG["TIMEFRAME_MIN"], mt5.TIMEFRAME_M30)

# ============================== LOGGING =====================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler("mt5_bot.log"), logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("MT5_EMA_Bot")

# ============================== CORE BOT CLASS ==============================

class MultiSymbolEMABot:
    def __init__(self, config):
        self.config = config
        self.watchlist = []
        self.data_cache = {}        # symbol -> DataFrame
        self.last_cache_time = {}   # symbol -> last_update_time
        self.lock = threading.Lock()
        self.running = True
        self.pending_entries = {}   # symbol -> signal_data
        self.pending_exits = {}     # ticket -> exit_signal_data

    def initialize(self):
        if not mt5.initialize():
            logger.error(f"MT5 initialization failed: {mt5.last_error()}")
            return False

        acc = mt5.account_info()
        if acc:
            logger.info(f"Connected to Account: {acc.login} ({acc.company})")
        return True

    def shutdown(self):
        self.running = False
        mt5.shutdown()
        logger.info("MT5 Shutdown complete.")

    def get_filling_mode(self, symbol):
        """Detects correct filling mode for the broker/symbol."""
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info: return mt5.ORDER_FILLING_IOC

        filling_type = symbol_info.filling_mode
        if filling_type & mt5.SYMBOL_FILLING_FOK:
            return mt5.ORDER_FILLING_FOK
        elif filling_type & mt5.SYMBOL_FILLING_IOC:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def get_eligible_symbols(self):
        """Scans for symbols with high 24h volatility."""
        logger.info("Scanning market...")
        symbols = mt5.symbols_get()
        if not symbols: return []

        eligible = []
        for s in symbols:
            # Simple heuristic for crypto/futures: check if name contains 'PERP', '.P', or is a known crypto
            # In a real environment, you might filter by symbol path or name patterns.
            if not s.visible:
                if not mt5.symbol_select(s.name, True): continue

            rates = mt5.copy_rates_from_pos(s.name, mt5.TIMEFRAME_D1, 0, 2)
            if rates is not None and len(rates) >= 2:
                prev_close, curr_close = rates[0]['close'], rates[1]['close']
                change_pct = abs(((curr_close - prev_close) / prev_close) * 100)
                if change_pct >= self.config["MIN_CHANGE_PCT"]:
                    eligible.append(s.name)

        logger.info(f"Scan complete. Found {len(eligible)} symbols.")
        return eligible

    def update_data_cache(self, symbol):
        """Refreshes cached OHLCV data if a new bar has formed."""
        # Check if we need an update (only fetch once per minute or if cache is empty)
        now = time.time()
        if symbol in self.data_cache and now - self.last_cache_time.get(symbol, 0) < 30:
            return self.data_cache[symbol]

        rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, 1000)
        if rates is None or len(rates) == 0:
            return pd.DataFrame()

        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df.set_index('time', inplace=True)

        # Indicators
        df['ema_fast'] = df['close'].ewm(span=self.config["EMA_FAST_PERIOD"], adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=self.config["EMA_SLOW_PERIOD"], adjust=False).mean()
        df['ema_exit'] = df['close'].ewm(span=self.config["EMA_EXIT_PERIOD"], adjust=False).mean()

        tr1 = df['high'] - df['low']
        tr2 = (df['high'] - df['close'].shift()).abs()
        tr3 = (df['low'] - df['close'].shift()).abs()
        df['atr'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(self.config["ATR_PERIOD"]).mean()
        df['ema_slow_rising'] = df['ema_slow'] > df['ema_slow'].shift(1)

        self.data_cache[symbol] = df
        self.last_cache_time[symbol] = now
        return df

    def check_for_signal(self, symbol):
        df = self.update_data_cache(symbol)
        if df.empty or len(df) < 100: return None

        signal_candle = df.iloc[-2]
        prev_candle = df.iloc[-3]

        point = mt5.symbol_info(symbol).point
        buffer = self.config["BUFFER_POINTS"] * point

        # 1. Trend: Fast > Slow AND Slow Rising
        trend_ok = (signal_candle['ema_fast'] > signal_candle['ema_slow']) and signal_candle['ema_slow_rising']
        # 2. Dip: Low touches or goes below Slow EMA
        dip_ok = signal_candle['low'] <= signal_candle['ema_slow']
        # 3. Breakout: Body crosses both EMAs (+ buffer)
        breakout_ok = (signal_candle['open'] < signal_candle['ema_fast']) and                       (signal_candle['open'] < signal_candle['ema_slow']) and                       (signal_candle['close'] > (signal_candle['ema_fast'] + buffer)) and                       (signal_candle['close'] > (signal_candle['ema_slow'] + buffer))

        # 4. Confirmation: Green, Higher High, Range within limits
        candle_size_pct = ((signal_candle['high'] - signal_candle['low']) / signal_candle['open']) * 100
        confirmation_ok = (signal_candle['close'] > signal_candle['open']) and                           (signal_candle['high'] > prev_candle['high']) and                           (self.config["MIN_RANGE_PCT"] <= candle_size_pct <= self.config["MAX_RANGE_PCT"])

        if trend_ok and dip_ok and breakout_ok and confirmation_ok:
            recent_high = df['high'].iloc[-self.config["SWING_LOOKBACK"]-1:-1].max()
            return {
                'trigger_high': signal_candle['high'],
                'stop_loss': signal_candle['low'],
                'take_profit': recent_high,
                'time': df.index[-2]
            }
        return None

    def execute_entry(self, symbol, signal):
        tick = mt5.symbol_info_tick(symbol)
        if not tick: return False

        if tick.ask > signal['trigger_high']:
            logger.info(f"[{symbol}] Entry Triggered! Price {tick.ask} > {signal['trigger_high']}")

            if signal['take_profit'] <= tick.ask:
                logger.warning(f"[{symbol}] TP level invalid. Skipping.")
                return False

            request = {
                "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": self.config["LOT_SIZE"],
                "type": mt5.ORDER_TYPE_BUY, "price": tick.ask, "sl": signal['stop_loss'], "tp": signal['take_profit'],
                "magic": self.config["MAGIC_NUMBER"], "comment": "EMA Body Cross",
                "type_time": mt5.ORDER_TIME_GTC, "type_filling": self.get_filling_mode(symbol),
            }

            result = mt5.order_send(request)
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                logger.error(f"[{symbol}] Entry Order failed: {result.comment} ({result.retcode})")
                return False

            logger.info(f"[{symbol}] Position opened: Ticket {result.order}")
            return True
        return False

    def manage_risk_and_exits(self):
        positions = mt5.positions_get(magic=self.config["MAGIC_NUMBER"])
        if not positions: return

        for pos in positions:
            symbol = pos.symbol
            df = self.update_data_cache(symbol)
            if df.empty: continue

            # 1. Trailing Stop
            curr_atr = df.iloc[-1]['atr']
            profit_points = (pos.price_current - pos.price_open)
            if profit_points >= curr_atr and pos.sl < pos.price_open:
                logger.info(f"[{symbol}] Moving SL to Breakeven for ticket {pos.ticket}")
                req = {"action": mt5.TRADE_ACTION_SLTP, "position": pos.ticket, "sl": pos.price_open, "tp": pos.tp}
                mt5.order_send(req)

            # 2. EMA Trend Exit
            last_closed = df.iloc[-2]
            ema_exit = last_closed['ema_exit']
            is_red = last_closed['close'] < last_closed['open']
            touched_ema = (last_closed['open'] < ema_exit) and (last_closed['high'] >= ema_exit) and (last_closed['close'] < ema_exit)

            if is_red and touched_ema:
                if pos.ticket not in self.pending_exits:
                    logger.info(f"[{symbol}] EMA Trend Exit PENDING for {pos.ticket}")
                    self.pending_exits[pos.ticket] = {'trigger_low': last_closed['low'], 'signal_time': df.index[-2]}

            if pos.ticket in self.pending_exits:
                pending = self.pending_exits[pos.ticket]
                tick = mt5.symbol_info_tick(symbol)
                if tick and tick.bid < pending['trigger_low']:
                    logger.info(f"[{symbol}] EMA Trend Exit TRIGGERED for {pos.ticket}")
                    self.close_position(pos)
                    del self.pending_exits[pos.ticket]
                elif df.index[-1] > pending['signal_time'] + timedelta(minutes=self.config["TIMEFRAME_MIN"] * 2):
                    del self.pending_exits[pos.ticket]

    def close_position(self, pos):
        tick = mt5.symbol_info_tick(pos.symbol)
        request = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL, "position": pos.ticket, "price": tick.bid,
            "magic": self.config["MAGIC_NUMBER"], "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self.get_filling_mode(pos.symbol),
        }
        mt5.order_send(request)

    def scanner_loop(self):
        while self.running:
            try:
                new_watchlist = self.get_eligible_symbols()
                with self.lock:
                    self.watchlist = new_watchlist
                time.sleep(300)
            except Exception as e:
                logger.error(f"Scanner Loop Error: {e}")
                time.sleep(60)

    def main_loop(self):
        while self.running:
            try:
                with self.lock:
                    current_symbols = list(self.watchlist)

                self.manage_risk_and_exits()

                for symbol in current_symbols:
                    if mt5.positions_get(symbol=symbol, magic=self.config["MAGIC_NUMBER"]):
                        if symbol in self.pending_entries: del self.pending_entries[symbol]
                        continue

                    if symbol not in self.pending_entries:
                        signal = self.check_for_signal(symbol)
                        if signal:
                            logger.info(f"[{symbol}] Signal Confirmed! Waiting breakout above {signal['trigger_high']}")
                            self.pending_entries[symbol] = signal
                    else:
                        if self.execute_entry(symbol, self.pending_entries[symbol]):
                            del self.pending_entries[symbol]
                        else:
                            df = self.update_data_cache(symbol)
                            if not df.empty and df.index[-1] > self.pending_entries[symbol]['time'] + timedelta(minutes=self.config["TIMEFRAME_MIN"]):
                                logger.info(f"[{symbol}] Signal expired.")
                                del self.pending_entries[symbol]

                time.sleep(0.5)
            except Exception as e:
                logger.error(f"Main Loop Error: {e}")
                time.sleep(1)

if __name__ == "__main__":
    bot = MultiSymbolEMABot(CONFIG)
    if bot.initialize():
        threading.Thread(target=bot.scanner_loop, daemon=True).start()
        try:
            bot.main_loop()
        except KeyboardInterrupt:
            logger.info("Bot stopping...")
        finally:
            bot.shutdown()
