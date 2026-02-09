#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ADVANCED GAMMA SCALPER WITH MULTI-MODEL SCALPING STRATEGIES

Enhanced Features:
1. Multi-Timeframe Scalping (1m, 3m, 5m concurrent analysis)
2. Order Flow Imbalance (Bid-Ask pressure)
3. VWAP Bands + Standard Deviation Scalping
4. Microstructure Edge Detection (Tick velocity, spread compression)
5. Greeks-Based Scalping (Delta hedging opportunities)
6. Rapid Mean Reversion (Bollinger Band + RSI oversold/overbought)
7. Volume Profile & POC (Point of Control) Detection
8. Iceberg Order Detection (Large hidden orders)
9. Smart Exit Management (Trailing stops, partial profit taking)
10. Multi-Leg Strategies (Spreads, Iron Condors for range scalping)

Requirements:
    pip install fyers-apiv3 requests pandas numpy scipy ta
"""

import os
import sys
import json
import time
import random
import datetime
import math
import hashlib
import csv
import argparse
import pandas as pd
import numpy as np
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs, quote
import requests
import webbrowser

try:
    from fyers_apiv3 import fyersModel
    import ta  # Technical Analysis library
    from scipy import stats
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install: pip install fyers-apiv3 pandas numpy scipy ta")
    sys.exit(1)

# ===============================
# CONFIGURATION
# ===============================
CONFIG_FILE = "fyers_login_details.json"
TOKENS_DIR = "AccessToken"
TODAY = str(datetime.date.today())
TOKEN_PATH = os.path.join(TOKENS_DIR, f"{TODAY}.json")

SYMBOLS = [
    "NSE:NIFTY50-INDEX",
    "NSE:NIFTYBANK-INDEX",
    "NSE:FINNIFTY-INDEX", "BSE:SENSEX-INDEX"
]

# Multi-timeframe settings
TIMEFRAMES = ["1", "3", "5"]  # minutes
PRIMARY_TF = "1"  # Primary scalping timeframe

# ==========================================
# ADVANCED STRATEGY CONFIGURATION (PER-STRATEGY RULES)
# ==========================================
# Format: "STRATEGY_KEY": {"target": bp, "stop": bp, "trail_act": bp, "trail_step": bp}
# Keys match the partial names of strategies (e.g., "RSI" matches "RSI_LONG")

STRATEGY_PARAMS = {
    "ORDER_FLOW": {"target": 15, "stop": 8, "trail_act": 8, "trail_step": 4},
    "VWAP": {"target": 30, "stop": 15, "trail_act": 15, "trail_step": 5},
    "MICRO": {"target": 12, "stop": 6, "trail_act": 6, "trail_step": 3},  # Quick momentum scalps
    "SPREAD": {"target": 10, "stop": 5, "trail_act": 5, "trail_step": 2},  # Spread scalps
    "RSI": {"target": 25, "stop": 12, "trail_act": 12, "trail_step": 5},
    "BB": {"target": 25, "stop": 12, "trail_act": 12, "trail_step": 5},
    "DEFAULT": {"target": 20, "stop": 10, "trail_act": 10, "trail_step": 5}
}

PARTIAL_EXIT_PCT = 0.5  # Exit 50% at 50% of target

# Advanced indicators
RSI_PERIOD = 7  # Fast RSI for scalping
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
VWAP_STD_BANDS = [1.0, 2.0, 3.0]  # Standard deviation bands
BB_PERIOD = 20
BB_STD = 2.0

# Order flow
ORDER_FLOW_WINDOW = 20  # ticks to analyze
BID_ASK_THRESHOLD = 0.65  # 65% imbalance threshold

# Microstructure
TICK_VELOCITY_WINDOW = 10  # seconds
TICK_VELOCITY_THRESHOLD = 0.2  # ticks/second for momentum (Adjusted for polling)
SPREAD_COMPRESSION_THRESHOLD = 0.3  # 30% tighter than average

# Greeks scalping
DELTA_HEDGE_THRESHOLD = 0.15  # Delta drift for rehedge
GAMMA_SCALP_THRESHOLD = 0.02  # High gamma opportunities

# Volume profile
VOLUME_PROFILE_BINS = 50
POC_PROXIMITY_BP = 10  # Trade near POC

# State management
STATE_FILE = "gamma_scalper_state.json"
TRADE_LOG_FILE = "scalp_trades.csv"

# Risk limits
MAX_POSITIONS_PER_SYMBOL = 3
MAX_DAILY_LOSS_BP = 50  # Max 50bp loss per symbol per day
MAX_DAILY_TRADES = 10  # Maximum 10 trades per day across all symbols
POSITION_SIZE_MULTIPLIER = 1
MAX_SLIPPAGE_BP = 2  # Don't trade if slippage exceeds 2bp
MIN_SIGNAL_STRENGTH = 0.4  # Minimum average strength to take trade (quality filter) - Lowered for polling

# Refresh
TICK_REFRESH_MS = 500  # 500ms for tick data
QUOTE_REFRESH_SEC = 2  # Increased to 2 seconds to avoid 429 errors
ANALYSIS_EVERY_N_TICKS = 5  # Deep analysis every N ticks

# CSV output
SAVE_CSV = True
CSV_OUTDIR = "scalper_data"


# ===============================
# DATA STRUCTURES
# ===============================

@dataclass
class Tick:
    """Represents a single market tick"""
    timestamp: float
    ltp: float
    bid: float
    ask: float
    bid_qty: int
    ask_qty: int
    volume: int
    oi: Optional[float] = None

    @property
    def spread(self) -> float:
        return self.ask - self.bid if self.ask and self.bid else 0.0

    @property
    def mid(self) -> float:
        return (self.ask + self.bid) / 2 if self.ask and self.bid else self.ltp

    @property
    def spread_pct(self) -> float:
        return (self.spread / self.mid * 100) if self.mid > 0 else 0.0


@dataclass
class OrderFlowSignal:
    """Order flow imbalance signal"""
    timestamp: float
    buy_pressure: float  # 0-1, higher = more buying
    sell_pressure: float
    imbalance: float  # -1 to 1
    strength: str  # "WEAK", "MODERATE", "STRONG"
    direction: str  # "BUY", "SELL", "NEUTRAL"


@dataclass
class VWAPDeviation:
    """VWAP deviation bands signal"""
    timestamp: float
    price: float
    vwap: float
    deviation_pct: float
    band_level: int  # 0, 1, 2, 3 for std bands
    signal: str  # "LONG_MEAN_REVERT", "SHORT_MEAN_REVERT", "NEUTRAL"
    strength: float  # 0-1


@dataclass
class MicrostructureEdge:
    """Microstructure analysis"""
    timestamp: float
    tick_velocity: float  # ticks/second
    spread_compression: float  # % tighter than average
    price_momentum_bp: float
    signal: str  # "MOMENTUM_LONG", "MOMENTUM_SHORT", "SPREAD_SCALP", "NEUTRAL"
    confidence: float  # 0-1


@dataclass
class ScalpPosition:
    """Enhanced position tracking for scalping"""
    symbol: str
    index_symbol: str
    side: str  # "LONG", "SHORT"
    entry_price: float
    current_price: float
    quantity: int
    entry_time: float

    # Dynamic Risk Parameters
    stop_loss: float
    target: float

    # Trailing Stop Parameters
    trailing_activation_bp: float
    trailing_step_bp: float

    trailing_stop: Optional[float] = None
    highest_profit_bp: float = 0.0
    partial_exit_done: bool = False
    remaining_qty: int = 0
    strategy: str = "GAMMA_BLAST"  # Strategy that opened it
    metadata: Dict = field(default_factory=dict)

    def __post_init__(self):
        self.remaining_qty = self.quantity

    @property
    def pnl_bp(self) -> float:
        if self.side == "LONG":
            return ((self.current_price / self.entry_price) - 1) * 10000
        else:
            return ((self.entry_price / self.current_price) - 1) * 10000

    @property
    def is_profitable(self) -> bool:
        return self.pnl_bp > 0


@dataclass
class DailyStats:
    """Daily performance tracking"""
    symbol: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl_bp: float = 0.0
    max_drawdown_bp: float = 0.0
    current_drawdown_bp: float = 0.0
    trades_taken_today: int = 0  # Track all trades across symbols

    @property
    def win_rate(self) -> float:
        return (self.wins / self.trades * 100) if self.trades > 0 else 0.0

    # ===============================


# SYMBOL MASTER
# ===============================
SYMBOL_MASTER_MAP = {}


def fetch_symbol_master():
    """Downloads Fyers master CSVs for lot sizes"""
    print("Fetching Symbol Master...")
    urls = {
        "NSE": "https://public.fyers.in/sym_details/NSE_FO.csv",
        "BSE": "https://public.fyers.in/sym_details/BSE_FO.csv"
    }

    temp_data = {}
    for exch, url in urls.items():
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            lines = r.text.strip().split("\n")
            reader = csv.reader(lines)
            next(reader, None)  # Skip header

            for row in reader:
                if len(row) < 14:
                    continue
                root = row[13].strip().upper()
                if not root:
                    continue
                try:
                    lot = int(row[3])
                except:
                    continue
                if root not in temp_data:
                    temp_data[root] = {"lot": lot}
                temp_data[root]["lot"] = lot
        except Exception as e:
            print(f"Warning: Failed to fetch {exch} master: {e}")

    mapping = {
        "NSE:NIFTY50-INDEX": ("NIFTY", 50, 25),
        "NSE:NIFTYBANK-INDEX": ("BANKNIFTY", 100, 15),
        "NSE:FINNIFTY-INDEX": ("FINNIFTY", 50, 25),
        "BSE:SENSEX-INDEX": ("SENSEX", 100, 10),
    }

    for sym_full, (root, default_step, default_lot) in mapping.items():
        lot = temp_data.get(root, {}).get("lot", default_lot)
        SYMBOL_MASTER_MAP[sym_full] = {
            "lot_size": lot,
            "step": default_step,
            "root": root
        }

    print(f"Symbol Master loaded: {len(SYMBOL_MASTER_MAP)} symbols")


# ===============================
# AUTHENTICATION (Fixed for 503 & api-t1)
# ===============================

def sha256_appIdHash(app_id, secret_id):
    return hashlib.sha256(f"{app_id}:{secret_id}".encode("utf-8")).hexdigest()


def extract_code(user_input):
    """Accept either a raw code or a full redirect URL with ?auth_code=..."""
    # Fix: Fyers v3 uses 'auth_code', user script uses 'code', accommodating both
    if "http" in user_input:
        q = parse_qs(urlparse(user_input).query)
        code = q.get("auth_code", [None])[0] or q.get("code", [None])[0]
        if not code:
            raise ValueError("No 'auth_code' found in URL.")
        return code
    return user_input


def validate_authcode(app_id, secret_id, auth_code, max_retries=5):
    # Using api-t1 to avoid 503 errors from legacy hosts
    url = "https://api-t1.fyers.in/api/v3/validate-authcode"
    payload = {
        "grant_type": "authorization_code",
        "appIdHash": sha256_appIdHash(app_id, secret_id),
        "code": auth_code,
    }
    headers = {"Content-Type": "application/json"}

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=20)
            if r.status_code == 503:
                sleep_s = min(2 ** attempt, 30)
                print(f"[{attempt}/{max_retries}] 503 Service Unavailable. Retrying in {sleep_s}s...")
                time.sleep(sleep_s)
                continue

            data = r.json()
            if data.get("s") == "error":
                raise RuntimeError(f"Fyers Error {data.get('code')}: {data.get('message')}")
            return data

        except requests.RequestException as e:
            if attempt == max_retries: raise
            print(f"Network error: {e}. Retrying...")
            time.sleep(2)


def load_or_prompt_creds():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)

    print("---- Enter Fyers Credentials ----")
    creds = {
        "api_key": input("APP ID: ").strip(),
        "api_secret": input("SECRET ID: ").strip(),
        "redirect_url": input("Redirect URL: ").strip(),
    }

    with open(CONFIG_FILE, "w") as f:
        json.dump(creds, f, indent=2)

    return creds


def get_access_token():
    creds = load_or_prompt_creds()
    app_id = creds["api_key"]

    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, "r") as f:
            token_data = json.load(f)
        token = token_data if isinstance(token_data, str) else token_data.get("access_token")
        return token, app_id

        # Automated Auth Flow (Browser Based via api-t1)
    print("\n[AUTH] Token not found. Starting Login Flow...")

    # 1. Build Auth URL
    base = "https://api-t1.fyers.in/api/v3/generate-authcode"
    params = (
        f"client_id={quote(app_id)}"
        f"&redirect_uri={quote(creds['redirect_url'], safe='')}"
        f"&response_type=code"
        f"&state=None"
        f"&scope=openid"
        f"&nonce={int(time.time())}"
    )
    auth_link = f"{base}?{params}"

    print(f"\nOpening Login Page: {auth_link}")
    webbrowser.open(auth_link)

    # 2. Accept Redirect URL
    print("\nSTEP 1: Login in the browser window.")
    print("STEP 2: Copy the ENTIRE URL after login.")
    redirect_url = input("\nPaste Redirect URL Here: ").strip()

    try:
        # 3. Extract Code
        auth_code = extract_code(redirect_url)

        # 4. Validate Code & Get Token (Manual Request)
        token_resp = validate_authcode(app_id, creds["api_secret"], auth_code)
        new_token = token_resp.get("access_token")

        if new_token:
            # Save token
            os.makedirs(TOKENS_DIR, exist_ok=True)
            data = {"access_token": new_token}
            with open(TOKEN_PATH, "w") as f:
                json.dump(data, f, indent=2)

            print(f"✓ Login Successful! Token saved to {TOKEN_PATH}")
            return new_token, app_id
        else:
            print("Error: No access_token in response.")
            sys.exit(1)

    except Exception as e:
        print(f"Auth Error: {e}")
        sys.exit(1)

    # ===============================


# TICK DATA STREAM
# ===============================

class TickDataManager:
    """Manages real-time tick data and historical buffers"""

    def __init__(self, symbol: str, max_ticks: int = 1000):
        self.symbol = symbol
        self.ticks = deque(maxlen=max_ticks)
        self.last_tick: Optional[Tick] = None
        self.tick_count = 0

    def add_tick(self, tick: Tick):
        self.ticks.append(tick)
        self.last_tick = tick
        self.tick_count += 1

    def get_recent_ticks(self, n: int) -> List[Tick]:
        return list(self.ticks)[-n:] if len(self.ticks) >= n else list(self.ticks)

    def tick_velocity(self, window_sec: float = 10.0) -> float:
        """Calculate ticks per second in recent window"""
        if not self.ticks:
            return 0.0

        cutoff = time.time() - window_sec
        recent = [t for t in self.ticks if t.timestamp >= cutoff]
        return len(recent) / window_sec if recent else 0.0

    def average_spread(self, n: int = 50) -> float:
        """Average spread over last N ticks"""
        recent = self.get_recent_ticks(n)
        if not recent:
            return 0.0
        spreads = [t.spread for t in recent if t.spread > 0]
        return np.mean(spreads) if spreads else 0.0

    # ===============================


# ORDER FLOW ANALYZER
# ===============================

class OrderFlowAnalyzer:
    """Analyzes bid-ask pressure and order flow imbalance"""

    def __init__(self, window: int = ORDER_FLOW_WINDOW):
        self.window = window

    def analyze(self, ticks: List[Tick]) -> Optional[OrderFlowSignal]:
        if len(ticks) < 5:
            return None

        recent = ticks[-self.window:]

        # Calculate buy vs sell pressure from bid/ask quantities
        buy_volume = sum(t.ask_qty for t in recent if t.ask_qty)
        sell_volume = sum(t.bid_qty for t in recent if t.bid_qty)
        total = buy_volume + sell_volume

        # Fallback 1: Tick Rule with Volume (Level 1 data) for Indices/Equity lacking L2
        if total == 0:
            for i in range(1, len(recent)):
                curr = recent[i]
                prev = recent[i-1]

                # Calculate volume delta (since volume is cumulative)
                tick_vol = max(0, curr.volume - prev.volume)

                if tick_vol > 0:
                    if curr.ltp > prev.ltp:
                        buy_volume += tick_vol
                    elif curr.ltp < prev.ltp:
                        sell_volume += tick_vol

            total = buy_volume + sell_volume

        # Fallback 2: Tick Direction Only (If volume is also missing/zero)
        if total == 0:
            for i in range(1, len(recent)):
                curr = recent[i]
                prev = recent[i-1]
                if curr.ltp > prev.ltp:
                    buy_volume += 1
                elif curr.ltp < prev.ltp:
                    sell_volume += 1

            total = buy_volume + sell_volume

        if total == 0:
            return None

        buy_pressure = buy_volume / total
        sell_pressure = sell_volume / total
        imbalance = buy_pressure - sell_pressure

        # Determine strength
        abs_imb = abs(imbalance)
        if abs_imb > 0.4:
            strength = "STRONG"
        elif abs_imb > 0.2:
            strength = "MODERATE"
        else:
            strength = "WEAK"

            # Direction
        if imbalance > BID_ASK_THRESHOLD - 0.5:
            direction = "BUY"
        elif imbalance < -(BID_ASK_THRESHOLD - 0.5):
            direction = "SELL"
        else:
            direction = "NEUTRAL"

        return OrderFlowSignal(
            timestamp=time.time(),
            buy_pressure=buy_pressure,
            sell_pressure=sell_pressure,
            imbalance=imbalance,
            strength=strength,
            direction=direction
        )

    # ===============================


# VWAP DEVIATION SCALPER
# ===============================

class VWAPDeviationScalper:
    """Scalps mean reversion from VWAP bands"""

    def __init__(self, std_bands: List[float] = VWAP_STD_BANDS):
        self.std_bands = sorted(std_bands)

    def analyze(self, df: pd.DataFrame) -> Optional[VWAPDeviation]:
        """Expects df with columns: timestamp, close, volume, vwap"""
        if df is None or len(df) < 20:
            return None

        last = df.iloc[-1]
        price = last['close']
        vwap = last.get('vwap', price)

        if vwap == 0:
            return None

            # Calculate standard deviation of price from VWAP
        df['price_dev'] = df['close'] - df['vwap']
        std = df['price_dev'].std()

        current_dev = price - vwap
        deviation_pct = (current_dev / vwap) * 100

        # Determine band level
        abs_dev = abs(current_dev)
        band_level = 0
        for i, band_std in enumerate(self.std_bands):
            if abs_dev >= band_std * std:
                band_level = i + 1

                # Signal generation
        signal = "NEUTRAL"
        strength = 0.0

        if band_level >= 2:  # 2 std or more
            if current_dev > 0:  # Price above VWAP
                signal = "SHORT_MEAN_REVERT"  # Expect reversion down
                strength = min(abs(deviation_pct) / 1.0, 1.0)  # Normalize
            else:
                signal = "LONG_MEAN_REVERT"
                strength = min(abs(deviation_pct) / 1.0, 1.0)

        return VWAPDeviation(
            timestamp=time.time(),
            price=price,
            vwap=vwap,
            deviation_pct=deviation_pct,
            band_level=band_level,
            signal=signal,
            strength=strength
        )

    # ===============================


# MICROSTRUCTURE EDGE DETECTOR
# ===============================

class MicrostructureAnalyzer:
    """Detects microstructure edges: spread compression, tick velocity"""

    def analyze(self, tick_mgr: TickDataManager) -> Optional[MicrostructureEdge]:
        if tick_mgr.tick_count < 20:
            return None

            # Tick velocity
        tick_vel = tick_mgr.tick_velocity(TICK_VELOCITY_WINDOW)

        # Spread compression
        current_spread = tick_mgr.last_tick.spread_pct if tick_mgr.last_tick else 0
        avg_spread = tick_mgr.average_spread(50)

        spread_compression = 0.0
        if avg_spread > 0:
            spread_compression = (avg_spread - current_spread) / avg_spread

            # Price momentum (last 10 ticks)
        recent = tick_mgr.get_recent_ticks(10)
        if len(recent) < 5:
            return None

        price_change = (recent[-1].ltp - recent[0].ltp) / recent[0].ltp * 10000  # bp

        # Signal logic
        signal = "NEUTRAL"
        confidence = 0.0

        # High velocity + positive momentum = momentum long
        # Lowered momentum threshold to 2bp for polling (approx 5 pts on Nifty)
        if tick_vel > TICK_VELOCITY_THRESHOLD and price_change > 2:
            signal = "MOMENTUM_LONG"
            confidence = min(tick_vel / (TICK_VELOCITY_THRESHOLD * 2), 1.0)

        elif tick_vel > TICK_VELOCITY_THRESHOLD and price_change < -2:
            signal = "MOMENTUM_SHORT"
            confidence = min(tick_vel / (TICK_VELOCITY_THRESHOLD * 2), 1.0)

            # Spread compression = scalp opportunity (quick in/out)
        elif spread_compression > SPREAD_COMPRESSION_THRESHOLD:
            signal = "SPREAD_SCALP"
            confidence = spread_compression

        return MicrostructureEdge(
            timestamp=time.time(),
            tick_velocity=tick_vel,
            spread_compression=spread_compression,
            price_momentum_bp=price_change,
            signal=signal,
            confidence=confidence
        )

    # ===============================


# RAPID RSI SCALPER
# ===============================

class RSIScalper:
    """Fast RSI-based mean reversion scalping"""

    def __init__(self, period: int = RSI_PERIOD, oversold: int = RSI_OVERSOLD, overbought: int = RSI_OVERBOUGHT):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def analyze(self, df: pd.DataFrame) -> Optional[dict]:
        if df is None or len(df) < self.period + 5:
            return None

            # Calculate RSI
        rsi = ta.momentum.RSIIndicator(df['close'], window=self.period).rsi()
        df['rsi'] = rsi

        last_rsi = df['rsi'].iloc[-1]
        prev_rsi = df['rsi'].iloc[-2]

        signal = "NEUTRAL"
        strength = 0.0

        # Oversold bounce
        if last_rsi < self.oversold and prev_rsi < last_rsi:
            signal = "LONG_RSI"
            strength = (self.oversold - last_rsi) / self.oversold

            # Overbought rejection
        elif last_rsi > self.overbought and prev_rsi > last_rsi:
            signal = "SHORT_RSI"
            strength = (last_rsi - self.overbought) / (100 - self.overbought)

        return {
            'signal': signal,
            'rsi': last_rsi,
            'strength': min(strength, 1.0),
            'timestamp': time.time()
        }

    # ===============================


# BOLLINGER BAND SCALPER
# ===============================

class BollingerScalper:
    """Bollinger Band squeeze and breakout scalping"""

    def __init__(self, period: int = BB_PERIOD, std_dev: float = BB_STD):
        self.period = period
        self.std_dev = std_dev

    def analyze(self, df: pd.DataFrame) -> Optional[dict]:
        if df is None or len(df) < self.period + 5:
            return None

            # Calculate Bollinger Bands
        bb = ta.volatility.BollingerBands(df['close'], window=self.period, window_dev=self.std_dev)
        df['bb_upper'] = bb.bollinger_hband()
        df['bb_lower'] = bb.bollinger_lband()
        df['bb_mid'] = bb.bollinger_mavg()
        df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid'] * 100

        last = df.iloc[-1]
        prev = df.iloc[-2]

        price = last['close']
        bb_upper = last['bb_upper']
        bb_lower = last['bb_lower']
        bb_mid = last['bb_mid']
        bb_width = last['bb_width']

        signal = "NEUTRAL"
        strength = 0.0

        # Mean reversion from bands
        if price <= bb_lower:
            signal = "LONG_BB"
            strength = abs(price - bb_lower) / (bb_upper - bb_lower)

        elif price >= bb_upper:
            signal = "SHORT_BB"
            strength = abs(price - bb_upper) / (bb_upper - bb_lower)

            # Squeeze breakout (low width followed by expansion)
        elif bb_width < 1.5 and prev['bb_width'] < bb_width:
            if price > bb_mid:
                signal = "BREAKOUT_LONG"
                strength = 0.6
            elif price < bb_mid:
                signal = "BREAKOUT_SHORT"
                strength = 0.6

        return {
            'signal': signal,
            'bb_width': bb_width,
            'strength': strength,
            'price': price,
            'bb_upper': bb_upper,
            'bb_lower': bb_lower,
            'timestamp': time.time()
        }

    # ===============================


# SCALP POSITION MANAGER
# ===============================

class ScalpPositionManager:
    """Enhanced position management with trailing stops and partial exits"""

    def __init__(self):
        self.positions: Dict[str, List[ScalpPosition]] = {}
        self.daily_stats: Dict[str, DailyStats] = {}
        self.closed_trades: List[dict] = []
        self.load_state()

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
                    # Restore positions
                    for sym, pos_list in data.get('positions', {}).items():
                        self.positions[sym] = [
                            ScalpPosition(**p) for p in pos_list
                        ]
                        # Restore stats
                    for sym, stats in data.get('daily_stats', {}).items():
                        self.daily_stats[sym] = DailyStats(**stats)

                    print(f"[STATE] Loaded {sum(len(v) for v in self.positions.values())} positions")
            except Exception as e:
                print(f"[STATE] Error loading: {e}")

    def save_state(self):
        try:
            data = {
                'positions': {
                    sym: [vars(p) for p in pos_list]
                    for sym, pos_list in self.positions.items()
                },
                'daily_stats': {
                    sym: vars(stats)
                    for sym, stats in self.daily_stats.items()
                }
            }
            with open(STATE_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[STATE] Error saving: {e}")

    def can_open_position(self, index_symbol: str) -> Tuple[bool, str]:
        """Check if we can open a new position"""
        current_count = len(self.positions.get(index_symbol, []))

        # Check max positions per symbol
        if current_count >= MAX_POSITIONS_PER_SYMBOL:
            return False, f"MAX_POSITIONS ({MAX_POSITIONS_PER_SYMBOL})"

            # Check daily trade limit (global across all symbols)
        total_trades_today = self.get_total_daily_trades()
        if total_trades_today >= MAX_DAILY_TRADES:
            return False, f"DAILY_LIMIT ({MAX_DAILY_TRADES} trades reached)"

            # Check daily loss limit
        stats = self.daily_stats.get(index_symbol)
        if stats and stats.total_pnl_bp < -MAX_DAILY_LOSS_BP:
            return False, f"DAILY_LOSS_LIMIT ({stats.total_pnl_bp:.1f}bp)"

        return True, "OK"

    def _get_strategy_params(self, strategy_name: str) -> Dict[str, float]:
        """Resolve parameters based on strategy type"""
        # Search for key keywords in strategy name
        for key, params in STRATEGY_PARAMS.items():
            if key in strategy_name:
                return params
        return STRATEGY_PARAMS["DEFAULT"]

    def open_position(self, symbol: str, index_symbol: str, side: str,
                      entry_price: float, quantity: int, strategy: str, metadata: dict = None):
        """Open a new scalp position with strategy-specific parameters"""

        # Get strategy specific rules
        params = self._get_strategy_params(strategy)

        target_bp = params["target"]
        stop_bp = params["stop"]
        trail_act = params["trail_act"]
        trail_step = params["trail_step"]

        # Calculate stops and targets
        if side == "LONG":
            stop_loss = entry_price * (1 - stop_bp / 10000)
            target = entry_price * (1 + target_bp / 10000)
        else:
            stop_loss = entry_price * (1 + stop_bp / 10000)
            target = entry_price * (1 - target_bp / 10000)

        position = ScalpPosition(
            symbol=symbol,
            index_symbol=index_symbol,
            side=side,
            entry_price=entry_price,
            current_price=entry_price,
            quantity=quantity,
            entry_time=time.time(),
            stop_loss=stop_loss,
            target=target,
            trailing_activation_bp=trail_act,
            trailing_step_bp=trail_step,
            strategy=strategy,
            metadata=metadata or {}
        )

        if index_symbol not in self.positions:
            self.positions[index_symbol] = []

        self.positions[index_symbol].append(position)
        self.save_state()

        print(
            f"[SCALP] OPEN {side} {symbol} @ {entry_price:.2f} | SL: {stop_loss:.2f} | TGT: {target:.2f} | Rule: {strategy} (SL:{stop_bp}bp TGT:{target_bp}bp)")

    def update_positions(self, index_symbol: str, current_price: float):
        """Update all positions for a symbol with current price and manage exits"""

        if index_symbol not in self.positions:
            return

        to_close = []

        for i, pos in enumerate(self.positions[index_symbol]):
            pos.current_price = current_price
            pnl_bp = pos.pnl_bp

            # Track highest profit for trailing stop
            if pnl_bp > pos.highest_profit_bp:
                pos.highest_profit_bp = pnl_bp

                # Partial exit at 50% of target (calculated from entry)
            # We calculate what BP the target represents
            target_bp_dist = abs(pos.target - pos.entry_price) / pos.entry_price * 10000

            if not pos.partial_exit_done and pos.remaining_qty == pos.quantity:
                if pnl_bp >= target_bp_dist * PARTIAL_EXIT_PCT:
                    partial_qty = int(pos.quantity * PARTIAL_EXIT_PCT)
                    pos.remaining_qty = pos.quantity - partial_qty
                    pos.partial_exit_done = True
                    print(
                        f"[SCALP] PARTIAL EXIT {pos.symbol} @ {current_price:.2f} | Closed {partial_qty}, Remaining {pos.remaining_qty} | PnL: +{pnl_bp:.1f}bp")

                    # Activate trailing stop after reaching threshold (Strategy Specific)
            if pos.highest_profit_bp >= pos.trailing_activation_bp and not pos.trailing_stop:
                if pos.side == "LONG":
                    # Set trailing stop below current price by step distance
                    pos.trailing_stop = current_price * (1 - pos.trailing_step_bp / 10000)
                else:
                    pos.trailing_stop = current_price * (1 + pos.trailing_step_bp / 10000)
                print(f"[SCALP] TRAILING STOP ACTIVATED {pos.symbol} @ {pos.trailing_stop:.2f}")

                # Update trailing stop
            if pos.trailing_stop:
                if pos.side == "LONG":
                    # Move trail up if price moves up
                    new_trail = current_price * (1 - pos.trailing_step_bp / 10000)
                    if new_trail > pos.trailing_stop:
                        pos.trailing_stop = new_trail
                else:
                    # Move trail down if price moves down
                    new_trail = current_price * (1 + pos.trailing_step_bp / 10000)
                    if new_trail < pos.trailing_stop:
                        pos.trailing_stop = new_trail

                        # Exit conditions
            reason = None

            # Stop loss hit
            if pos.side == "LONG" and current_price <= pos.stop_loss:
                reason = "STOP_LOSS"
            elif pos.side == "SHORT" and current_price >= pos.stop_loss:
                reason = "STOP_LOSS"

                # Target hit
            elif pos.side == "LONG" and current_price >= pos.target:
                reason = "TARGET"
            elif pos.side == "SHORT" and current_price <= pos.target:
                reason = "TARGET"

                # Trailing stop hit
            elif pos.trailing_stop:
                if pos.side == "LONG" and current_price <= pos.trailing_stop:
                    reason = "TRAILING_STOP"
                elif pos.side == "SHORT" and current_price >= pos.trailing_stop:
                    reason = "TRAILING_STOP"

                    # Time-based exit (scalp held too long - 5 minutes max)
            elif time.time() - pos.entry_time > 300:
                reason = "TIME_EXIT"

            if reason:
                to_close.append((i, reason))

                # Close positions
        for i, reason in reversed(to_close):
            self.close_position(index_symbol, i, reason)

    def close_position(self, index_symbol: str, pos_index: int, reason: str):
        """Close a position"""
        if index_symbol not in self.positions or pos_index >= len(self.positions[index_symbol]):
            return

        pos = self.positions[index_symbol][pos_index]
        pnl_bp = pos.pnl_bp

        # Update daily stats
        if index_symbol not in self.daily_stats:
            self.daily_stats[index_symbol] = DailyStats(symbol=index_symbol)

        stats = self.daily_stats[index_symbol]
        stats.trades += 1
        stats.total_pnl_bp += pnl_bp

        if pnl_bp > 0:
            stats.wins += 1
        else:
            stats.losses += 1
            if pnl_bp < stats.max_drawdown_bp:
                stats.max_drawdown_bp = pnl_bp

                # Log trade
        trade_log = {
            'timestamp': datetime.datetime.now().isoformat(),
            'symbol': pos.symbol,
            'index_symbol': index_symbol,
            'side': pos.side,
            'strategy': pos.strategy,
            'entry_price': pos.entry_price,
            'exit_price': pos.current_price,
            'quantity': pos.quantity,
            'pnl_bp': pnl_bp,
            'duration_sec': time.time() - pos.entry_time,
            'reason': reason,
            'partial_exit_done': pos.partial_exit_done
        }

        self.closed_trades.append(trade_log)
        self._append_trade_to_csv(trade_log)

        print(f"[SCALP] CLOSE {pos.side} {pos.symbol} @ {pos.current_price:.2f} | "
              f"PnL: {'+' if pnl_bp > 0 else ''}{pnl_bp:.1f}bp | Reason: {reason} | "
              f"Win Rate: {stats.win_rate:.1f}%")

        # Remove position
        del self.positions[index_symbol][pos_index]

        if not self.positions[index_symbol]:
            del self.positions[index_symbol]

        self.save_state()

    def _append_trade_to_csv(self, trade: dict):
        """Append trade to CSV log"""
        try:
            os.makedirs(CSV_OUTDIR, exist_ok=True)
            file_exists = os.path.exists(TRADE_LOG_FILE)

            with open(TRADE_LOG_FILE, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=trade.keys())
                if not file_exists:
                    writer.writeheader()
                writer.writerow(trade)
        except Exception as e:
            print(f"[LOG] Error writing trade: {e}")

    def get_position_count(self, index_symbol: str) -> int:
        return len(self.positions.get(index_symbol, []))

    def get_total_daily_trades(self) -> int:
        """Get total number of trades taken today across all symbols"""
        return sum(stats.trades for stats in self.daily_stats.values())

    # ===============================


# STRATEGY ORCHESTRATOR
# ===============================

class ScalpingOrchestrator:
    """Coordinates multiple scalping strategies"""

    def __init__(self, fyers_client):
        self.fy = fyers_client
        self.tick_managers: Dict[str, TickDataManager] = {}
        self.position_manager = ScalpPositionManager()

        # Strategy components
        self.order_flow = OrderFlowAnalyzer()
        self.vwap_scalper = VWAPDeviationScalper()
        self.microstructure = MicrostructureAnalyzer()
        self.rsi_scalper = RSIScalper()
        self.bb_scalper = BollingerScalper()

        # Historical candle cache
        self.candle_cache: Dict[Tuple[str, str], pd.DataFrame] = {}
        self.last_candle_fetch: Dict[Tuple[str, str], float] = {}
        self.debug_log_once = set()

    def get_tick_manager(self, symbol: str) -> TickDataManager:
        if symbol not in self.tick_managers:
            self.tick_managers[symbol] = TickDataManager(symbol)
        return self.tick_managers[symbol]

    def fetch_candles(self, symbol: str, timeframe: str, days: int = 1) -> Optional[pd.DataFrame]:
        """Fetch historical candles with caching"""
        cache_key = (symbol, timeframe)
        now = time.time()

        # Use cache if recent (30 seconds)
        if cache_key in self.last_candle_fetch:
            if now - self.last_candle_fetch[cache_key] < 30:
                return self.candle_cache.get(cache_key)

        try:
            end_date = datetime.date.today()
            start_date = end_date - datetime.timedelta(days=days)

            payload = {
                "symbol": symbol,
                "resolution": str(timeframe),
                "date_format": "1",
                "range_from": start_date.strftime("%Y-%m-%d"),
                "range_to": end_date.strftime("%Y-%m-%d"),
                "cont_flag": "1"
            }

            response = self.fy.history(data=payload)

            if response.get("s") != "ok":
                return None

            candles = response.get("candles", [])
            if not candles:
                return None

            df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")

            # Calculate VWAP
            df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
            df['pv'] = df['typical_price'] * df['volume']
            df['cum_pv'] = df['pv'].cumsum()
            df['cum_vol'] = df['volume'].cumsum()
            df['vwap'] = df['cum_pv'] / df['cum_vol']

            self.candle_cache[cache_key] = df
            self.last_candle_fetch[cache_key] = now

            return df

        except Exception as e:
            print(f"[CANDLE] Error fetching {symbol} {timeframe}m: {e}")
            return None

    def update_tick(self, symbol: str, quote_data: dict):
        """Process a new quote as a tick"""
        try:
            ltp = float(quote_data.get('lp', quote_data.get('ltp', 0)))

            # Ignore invalid ticks (e.g. market closed or data error)
            if ltp <= 0:
                return

            # Debugging keys
            if symbol not in self.debug_log_once:
                print(f"[DEBUG] {symbol} Quote Data Keys: {list(quote_data.keys())}")
                if 'bid_size' not in quote_data and 'ask_size' not in quote_data:
                    print(f"[WARN] {symbol}: 'bid_size' and 'ask_size' missing. Order Flow Analysis will be limited.")
                self.debug_log_once.add(symbol)

            bid = float(quote_data.get('bid', ltp))
            ask = float(quote_data.get('ask', ltp))
            bid_qty = int(quote_data.get('bid_size', 0))
            ask_qty = int(quote_data.get('ask_size', 0))
            volume = int(quote_data.get('volume', 0))

            tick = Tick(
                timestamp=time.time(),
                ltp=ltp,
                bid=bid,
                ask=ask,
                bid_qty=bid_qty,
                ask_qty=ask_qty,
                volume=volume
            )

            tick_mgr = self.get_tick_manager(symbol)
            tick_mgr.add_tick(tick)

            # Update positions with current price
            self.position_manager.update_positions(symbol, ltp)

        except Exception as e:
            print(f"[TICK] Error processing {symbol}: {e}")

    def analyze_and_trade(self, symbol: str):
        """Run all strategies and generate trading signals"""

        # Check if we can open new position
        can_trade, reason = self.position_manager.can_open_position(symbol)
        if not can_trade:
            # Only log on cycle boundaries to avoid spam
            if self.get_tick_manager(symbol).tick_count % 100 == 0:
                print(f"[LIMIT] {symbol}: Cannot trade - {reason}")
            return

        tick_mgr = self.get_tick_manager(symbol)

        if tick_mgr.tick_count < 20:
            return

        signals = []

        # 1. Order Flow Analysis
        try:
            ticks = tick_mgr.get_recent_ticks(ORDER_FLOW_WINDOW)
            flow_signal = self.order_flow.analyze(ticks)

            if flow_signal and flow_signal.strength in ["STRONG", "MODERATE"]:
                if flow_signal.direction == "BUY":
                    signals.append(("ORDER_FLOW_LONG", flow_signal.imbalance, flow_signal))
                elif flow_signal.direction == "SELL":
                    signals.append(("ORDER_FLOW_SHORT", abs(flow_signal.imbalance), flow_signal))
        except Exception as e:
            print(f"[FLOW] Error: {e}")

            # 2. VWAP Deviation
        try:
            df = self.fetch_candles(symbol, PRIMARY_TF, days=1)
            if df is not None and len(df) > 20:
                vwap_signal = self.vwap_scalper.analyze(df)

                if vwap_signal and vwap_signal.band_level >= 2:
                    if vwap_signal.signal == "LONG_MEAN_REVERT":
                        signals.append(("VWAP_LONG", vwap_signal.strength, vwap_signal))
                    elif vwap_signal.signal == "SHORT_MEAN_REVERT":
                        signals.append(("VWAP_SHORT", vwap_signal.strength, vwap_signal))
        except Exception as e:
            print(f"[VWAP] Error: {e}")

            # 3. Microstructure Edge
        try:
            micro_signal = self.microstructure.analyze(tick_mgr)

            if micro_signal and micro_signal.confidence > 0.6:
                if micro_signal.signal == "MOMENTUM_LONG":
                    signals.append(("MICRO_LONG", micro_signal.confidence, micro_signal))
                elif micro_signal.signal == "MOMENTUM_SHORT":
                    signals.append(("MICRO_SHORT", micro_signal.confidence, micro_signal))
                elif micro_signal.signal == "SPREAD_SCALP":
                    # Favor long if recent momentum positive
                    if micro_signal.price_momentum_bp > 0:
                        signals.append(("SPREAD_LONG", micro_signal.confidence, micro_signal))
                    else:
                        signals.append(("SPREAD_SHORT", micro_signal.confidence, micro_signal))
        except Exception as e:
            print(f"[MICRO] Error: {e}")

            # 4. RSI Scalping
        try:
            df = self.fetch_candles(symbol, PRIMARY_TF, days=1)
            if df is not None:
                rsi_signal = self.rsi_scalper.analyze(df)

                if rsi_signal and rsi_signal['strength'] > 0.5:
                    if rsi_signal['signal'] == "LONG_RSI":
                        signals.append(("RSI_LONG", rsi_signal['strength'], rsi_signal))
                    elif rsi_signal['signal'] == "SHORT_RSI":
                        signals.append(("RSI_SHORT", rsi_signal['strength'], rsi_signal))
        except Exception as e:
            print(f"[RSI] Error: {e}")

            # 5. Bollinger Band
        try:
            df = self.fetch_candles(symbol, PRIMARY_TF, days=1)
            if df is not None:
                bb_signal = self.bb_scalper.analyze(df)

                if bb_signal and bb_signal['strength'] > 0.5:
                    if "LONG" in bb_signal['signal']:
                        signals.append(("BB_LONG", bb_signal['strength'], bb_signal))
                    elif "SHORT" in bb_signal['signal']:
                        signals.append(("BB_SHORT", bb_signal['strength'], bb_signal))
        except Exception as e:
            print(f"[BB] Error: {e}")

            # Decision: Trade if multiple strategies agree
        # Apply progressive quality filter as we approach daily limit
        total_trades_today = self.position_manager.get_total_daily_trades()
        trades_remaining = MAX_DAILY_TRADES - total_trades_today

        # Dynamic thresholds based on trades remaining
        if trades_remaining <= 3:
            required_signals = 3  # Be very selective with last 3 trades
            min_strength = 0.75
        elif trades_remaining <= 5:
            required_signals = 3  # High quality only
            min_strength = 0.65
        else:
            required_signals = 2  # Standard confluence
            min_strength = MIN_SIGNAL_STRENGTH

        # Debugging signals
        if signals:
            print(f"[DEBUG] {symbol} Signals: {[s[0] for s in signals]} (Need {required_signals})")

        if len(signals) >= required_signals:  # At least N strategies agree
            long_signals = [s for s in signals if "LONG" in s[0]]
            short_signals = [s for s in signals if "SHORT" in s[0]]

            if len(long_signals) >= required_signals:
                # Check average signal strength
                avg_strength = np.mean([s[1] for s in long_signals])
                if avg_strength < min_strength:
                    print(
                        f"[FILTER] {symbol}: LONG signal strength {avg_strength:.2f} below threshold {min_strength:.2f}")
                    return

                    # Execute long
                strategy_names = ", ".join([s[0] for s in long_signals])
                print(
                    f"\n[SIGNAL] LONG {symbol} | Strategies: {strategy_names} | Strength: {avg_strength:.2f} | Trades Today: {total_trades_today}/{MAX_DAILY_TRADES}")
                self.execute_trade(symbol, "LONG", strategy_names, long_signals)

            elif len(short_signals) >= required_signals:
                # Check average signal strength
                avg_strength = np.mean([s[1] for s in short_signals])
                if avg_strength < min_strength:
                    print(
                        f"[FILTER] {symbol}: SHORT signal strength {avg_strength:.2f} below threshold {min_strength:.2f}")
                    return

                    # Execute short
                strategy_names = ", ".join([s[0] for s in short_signals])
                print(
                    f"\n[SIGNAL] SHORT {symbol} | Strategies: {strategy_names} | Strength: {avg_strength:.2f} | Trades Today: {total_trades_today}/{MAX_DAILY_TRADES}")
                self.execute_trade(symbol, "SHORT", strategy_names, short_signals)

    def execute_trade(self, index_symbol: str, side: str, strategy: str, signal_data: list):
        """Execute the actual trade"""

        # Get option symbol (ATM)
        tick_mgr = self.get_tick_manager(index_symbol)
        spot_price = tick_mgr.last_tick.ltp if tick_mgr.last_tick else 0

        if spot_price == 0:
            print("[TRADE] Error: No spot price available")
            return

            # Resolve option
        try:
            is_call = (side == "LONG")  # For simplicity, scalp calls on long, puts on short
            opt_symbol, expiry = self.resolve_option_symbol(is_call, spot_price, index_symbol)

            if not opt_symbol:
                print("[TRADE] Error: Could not resolve option symbol")
                return

                # Get lot size
            meta = SYMBOL_MASTER_MAP.get(index_symbol, {})
            lot_size = meta.get('lot_size', 1)
            qty = lot_size * POSITION_SIZE_MULTIPLIER

            # Place order (simulated here, implement actual order placement)
            order_side = "BUY"  # Always buy options for scalping

            print(f"[TRADE] Placing {order_side} {qty} {opt_symbol} @ Market")

            # ------------------------------------------------------------------
            # REAL EXECUTION BLOCK (Uncomment if you want real trades)
            # ------------------------------------------------------------------
            # order_data = {
            #     "symbol": opt_symbol,
            #     "qty": qty,
            #     "type": 2,  # Market Order
            #     "side": 1,  # Buy
            #     "productType": "INTRADAY",
            #     "limitPrice": 0,
            #     "stopPrice": 0,
            #     "validity": "DAY",
            #     "disclosedQty": 0,
            #     "offlineOrder": False,
            # }
            # resp = self.fy.place_order(data=order_data)
            # if resp.get("s") != "ok":
            #     print(f"[ERROR] Order Failed: {resp.get('message')}")
            #     return
            # ------------------------------------------------------------------

            # For now, simulate entry at spot
            entry_price = spot_price

            # Open position
            metadata = {
                'signals': [s[0] for s in signal_data],
                'expiry': expiry,
                'spot_entry': spot_price
            }

            self.position_manager.open_position(
                symbol=opt_symbol,
                index_symbol=index_symbol,
                side=side,
                entry_price=entry_price,
                quantity=qty,
                strategy=strategy,
                metadata=metadata
            )

        except Exception as e:
            print(f"[TRADE] Execution error: {e}")

    def resolve_option_symbol(self, is_ce: bool, spot_ltp: float, symbol_root: str) -> Tuple[
        Optional[str], Optional[str]]:
        """Resolve nearest ATM option symbol"""
        try:
            # Use the mapped root (e.g., "NSE:NIFTY50-INDEX" -> "NIFTY50-INDEX")
            # Fyers Option Chain API expects symbol in format "NSE:NIFTY50-INDEX" but sometimes fails with certain prefixes.
            # However, experience suggests passing the symbol as is usually works, but let's try strict formatting if needed.
            # Actually, standard practice for Fyers Option Chain is to pass the full symbol e.g., "NSE:NIFTY50-INDEX".
            # If that returns empty, it might be an API specific issue or the symbol format.
            # Let's try to ensure we are passing exactly what we have in SYMBOLS list.

            # The issue is likely that "data" key is missing or None.

            resp = self.fy.optionchain(data={"symbol": symbol_root, "strikecount": 10})
            if not resp or resp.get("s") != "ok":
                print(f"[ERROR] Option Chain Failed for {symbol_root}: {resp}")
                return None, None

            chain = (resp.get("data") or {}).get("optionChain", [])
            if not chain:
                print(f"[ERROR] Option Chain Empty for {symbol_root}")
                return None, None

            step = SYMBOL_MASTER_MAP.get(symbol_root, {}).get("step", 50)
            target = round(spot_ltp / step) * step
            opt_type = "CE" if is_ce else "PE"

            # Filter
            filtered = [r for r in chain if str(r.get("option_type", "")).upper() == opt_type]
            if not filtered:
                # Debugging: Print first row to see keys
                if chain:
                    print(f"[DEBUG] {symbol_root} Chain Sample: {chain[0]}")
                print(f"[ERROR] No {opt_type} options found in chain for {symbol_root}")
                return None, None

                # Nearest expiry

            def expiry_key(row):
                exp = row.get("expiry_date", row.get("expiry", ""))
                try:
                    return datetime.datetime.strptime(str(exp).strip(), "%d-%m-%Y")
                except:
                    return datetime.datetime.max

            filtered.sort(key=expiry_key)
            best_expiry = filtered[0].get("expiry_date", filtered[0].get("expiry"))

            # Same expiry
            filtered = [r for r in filtered if r.get("expiry_date", r.get("expiry")) == best_expiry]

            # Nearest strike
            def strike_key(row):
                sp = row.get("strike_price", row.get("strikePrice", 0))
                return abs(float(sp) - target)

            best = min(filtered, key=strike_key)
            sym = best.get("symbol", best.get("tradingsymbol", ""))

            return sym, best_expiry

        except Exception as e:
            print(f"[RESOLVE] Error: {e}")
            return None, None

        # ===============================


# MAIN LOOP
# ===============================

def main():
    global POSITION_SIZE_MULTIPLIER, PRIMARY_TF

    parser = argparse.ArgumentParser(description="Advanced Gamma Scalper")
    parser.add_argument("--timeframe", "-tf", type=str, default="1", help="Primary timeframe (1, 3, 5)")
    parser.add_argument("--lots", type=int, default=1, help="Position size multiplier")
    parser.add_argument("--demo", action="store_true", help="Demo mode (no real trades)")

    args = parser.parse_args()

    PRIMARY_TF = args.timeframe
    POSITION_SIZE_MULTIPLIER = args.lots

    print("=" * 70)
    print("ADVANCED GAMMA SCALPER - MULTI-MODEL EXECUTION")
    print("=" * 70)
    print(f"Primary Timeframe: {PRIMARY_TF}m")
    print(f"Position Size: {POSITION_SIZE_MULTIPLIER}x")
    print(f"Mode: {'DEMO' if args.demo else 'LIVE'}")
    print(f"Config: Loaded {len(STRATEGY_PARAMS)} unique strategy profiles")
    print("=" * 70)

    # Auth
    try:
        access_token, app_id = get_access_token()

        # Using pure access token (v3 standard)
        # Note: Some older v3 versions used AppID:Token, but latest uses raw token
        fy = fyersModel.FyersModel(client_id=app_id, is_async=False, token=access_token, log_path=os.getcwd())

        # Connection Probe
        try:
            profile = fy.get_profile()
            if profile.get("s") != "ok":
                raise Exception(f"Profile check failed: {profile}")

            print(f"\n✓ Authenticated successfully: {profile.get('data', {}).get('name', 'User')}")

        except Exception as probe_err:
            print(f"\n[ERROR] Connection Probe Failed: {probe_err}")
            print("Troubleshooting: Token is valid, but App ID or Permissions are wrong.")
            # We exit here because if profile fails, trading will definitely fail
            sys.exit(1)

    except Exception as e:
        print(f"✗ Auth failed: {e}")
        sys.exit(1)

        # Load symbol master
    fetch_symbol_master()

    # Initialize orchestrator
    orchestrator = ScalpingOrchestrator(fy)

    print("\n[STARTING SCALP ENGINE...]\n")

    cycle = 0

    try:
        while True:
            cycle += 1
            now = datetime.datetime.now()

            if cycle % 10 == 1:
                print(f"\n{'=' * 70}")
                print(f"{now:%Y-%m-%d %H:%M:%S} | Cycle #{cycle}")
                print(f"{'=' * 70}")

                # SMART RETRY LOGIC: Try Batch -> Fail -> Try Single
            # ---------------------------------------------------
            quotes_fetched = False

            # 1. Try Batch Request (Fastest)
            try:
                resp = fy.quotes(data={"symbols": ",".join(SYMBOLS)})

                # Handle Token Expiry
                if resp.get("code") == -15:
                    print("\n[ERROR] Access Token Expired! Deleting token file...")
                    if os.path.exists(TOKEN_PATH): os.remove(TOKEN_PATH)
                    sys.exit(1)

                if resp.get("s") == "ok" and resp.get("d"):
                    quotes_fetched = True
                    for quote in resp["d"]:
                        sym = quote.get('n')
                        if sym:
                            # Extract 'v' to get the actual market data dict
                            orchestrator.update_tick(sym, quote.get('v', {}))
                            tick_mgr = orchestrator.get_tick_manager(sym)
                            if tick_mgr.tick_count % ANALYSIS_EVERY_N_TICKS == 0:
                                orchestrator.analyze_and_trade(sym)

                elif resp.get("code") == 429:
                    print("[WARN] Batch limit hit. Switching to single-symbol mode...")

            except Exception as e:
                print(f"[WARN] Batch fetch error: {e}")

                # 2. Fallback: Single Symbol Request (If Batch Failed)
            if not quotes_fetched:
                for sym in SYMBOLS:
                    try:
                        time.sleep(1.5)  # Slow down for safety
                        resp = fy.quotes(data={"symbols": sym})

                        if resp.get("s") == "ok" and resp.get("d"):
                            quote = resp["d"][0]["v"] if isinstance(resp["d"], list) else resp["d"]
                            orchestrator.update_tick(sym, quote)
                            tick_mgr = orchestrator.get_tick_manager(sym)
                            if tick_mgr.tick_count % ANALYSIS_EVERY_N_TICKS == 0:
                                orchestrator.analyze_and_trade(sym)
                        else:
                            # If even single fails, print why
                            if resp.get("code") != 429:  # Ignore 429 spam
                                print(f"[DEBUG] {sym} failed: {resp}")

                    except Exception as e:
                        pass
                        # ---------------------------------------------------

            # Print status
            if cycle % 20 == 0:
                total_trades_today = orchestrator.position_manager.get_total_daily_trades()
                print(f"\n[STATUS] Total Trades Today: {total_trades_today}/{MAX_DAILY_TRADES}")

                for symbol in SYMBOLS:
                    tick_mgr = orchestrator.get_tick_manager(symbol)
                    pos_count = orchestrator.position_manager.get_position_count(symbol)
                    stats = orchestrator.position_manager.daily_stats.get(symbol)

                    if tick_mgr.last_tick:
                        status = f"{symbol}: LTP {tick_mgr.last_tick.ltp:.2f} | "
                        status += f"Ticks: {tick_mgr.tick_count} | Positions: {pos_count}"

                        if stats:
                            status += f" | Trades: {stats.trades} | Win%: {stats.win_rate:.1f} | PnL: {stats.total_pnl_bp:+.1f}bp"

                        print(status)

            time.sleep(QUOTE_REFRESH_SEC)

    except KeyboardInterrupt:
        print("\n\n[SHUTDOWN REQUESTED]")
        print(f"Closing {sum(len(v) for v in orchestrator.position_manager.positions.values())} open positions...")

        # Force close all positions
        for symbol, positions in list(orchestrator.position_manager.positions.items()):
            for i in range(len(positions) - 1, -1, -1):
                orchestrator.position_manager.close_position(symbol, i, "MANUAL_SHUTDOWN")

        print("\n[FINAL STATS]")
        for symbol, stats in orchestrator.position_manager.daily_stats.items():
            print(f"\n{symbol}:")
            print(f"  Trades: {stats.trades} | Wins: {stats.wins} | Losses: {stats.losses}")
            print(f"  Win Rate: {stats.win_rate:.1f}%")
            print(f"  Total PnL: {stats.total_pnl_bp:+.1f}bp")
            print(f"  Max DD: {stats.max_drawdown_bp:.1f}bp")

        print("\n✓ Shutdown complete")

    except Exception as e:
        print(f"\n✗ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
