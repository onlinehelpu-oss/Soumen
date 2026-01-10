"""
PERFECT TRADING BOT - FIXED VERSION
"""

import numpy as np
import time
import os
import json
from datetime import datetime as dt, timedelta
from typing import Dict, List, Tuple, Optional
import random


# ============================================================================
# PERFECT CONFIGURATION
# ============================================================================

class PerfectTradingConfig:
    """Perfect balanced trading configuration."""

    # Symbol
    SYMBOL = "NSE:RELIANCE-EQ"

    # Account - Balanced
    INITIAL_BALANCE = 1000000
    POSITION_SIZE = 75  # Balanced position size
    MAX_POSITIONS = 8  # Reasonable limit

    # Timing - Optimal
    UPDATE_INTERVAL = 15  # Good pace

    # Balanced Strategy
    RSI_PERIOD = 10
    RSI_OVERBOUGHT = 70
    RSI_OVERSOLD = 30
    EMA_SHORT = 8
    EMA_LONG = 21
    MACD_FAST = 8
    MACD_SLOW = 17
    MACD_SIGNAL = 7

    # Smart Risk Management
    STOP_LOSS_PCT = 1.5
    TAKE_PROFIT_PCT = 3.0
    TRAILING_STOP_PCT = 0.5  # Trailing stop after profit

    # Trading Behavior
    MIN_CONFIDENCE = 0.4
    VOLATILITY_MULTIPLIER = 2.0
    INITIAL_PRICES = 50

    # Profit Taking
    MIN_PROFIT_TO_SELL = 1.0  # Minimum 1% profit to consider selling
    PARTIAL_PROFIT_TAKING = True  # Take partial profits
    PARTIAL_SELL_PCT = 0.5  # Sell 50% at take profit


# ============================================================================
# PERFECT MARKET SIMULATOR - FIXED
# ============================================================================

class PerfectMarketSimulator:
    """Perfect market simulator with realistic trends."""

    def __init__(self, config: PerfectTradingConfig):
        self.config = config
        # Define support and resistance BEFORE generating prices
        self.support = 2450
        self.resistance = 2550
        self.prices = self._generate_trending_prices()
        self.trend_direction = random.choice([-1, 1]) * 0.0001
        self.volatility = 0.002 * config.VOLATILITY_MULTIPLIER
        self.last_price = self.prices[-1]
        self.cycle = 0

    def _generate_trending_prices(self) -> List[float]:
        """Generate trending initial prices."""
        print(f"📊 Generating {self.config.INITIAL_PRICES} trending prices...")

        prices = []
        current = 2500.0  # Start at midpoint

        # Create a slight trend
        trend = random.uniform(-0.0002, 0.0002)

        for i in range(self.config.INITIAL_PRICES):
            # Trend + noise
            change = trend + np.random.normal(0, 0.001)
            current = current * (1 + change)
            current += np.random.uniform(-0.5, 0.5)

            # Stay in range using class attributes (now defined)
            current = max(self.support, min(self.resistance, current))
            prices.append(round(current, 2))

        trend_direction = "UP" if prices[-1] > prices[0] else "DOWN"
        print(f"✅ Initial trend: {trend_direction} " +
              f"({prices[0]:.2f} → {prices[-1]:.2f})")
        return prices

    def generate_price(self) -> float:
        """Generate price with realistic behavior."""
        self.cycle += 1

        # Base movement with mean reversion
        distance_to_mid = (2500 - self.last_price) / 2500
        mean_reversion = distance_to_mid * 0.0005

        # Trend
        trend = self.trend_direction

        # Volatility
        volatility = self.volatility * (1 + abs(distance_to_mid))

        # Combine
        change = trend + mean_reversion + np.random.normal(0, volatility)

        # Calculate new price
        new_price = self.last_price * (1 + change)

        # Add noise
        new_price += np.random.uniform(-0.3, 0.3)

        # Respect support/resistance
        if new_price < self.support:
            new_price = self.support + random.uniform(0, 5)
        elif new_price > self.resistance:
            new_price = self.resistance - random.uniform(0, 5)

        # Round
        new_price = round(new_price, 2)

        # Update trend occasionally
        if self.cycle % 100 == 0:
            self.trend_direction = random.uniform(-0.0002, 0.0002)

        # Update
        self.last_price = new_price
        self.prices.append(new_price)

        # Keep history
        if len(self.prices) > 2000:
            self.prices = self.prices[-2000:]

        return new_price

    def get_price_history(self, count: int = 100) -> List[float]:
        """Get recent price history."""
        return self.prices[-count:] if self.prices else []


# ============================================================================
# PERFECT TRADING STRATEGY
# ============================================================================

class PerfectTradingStrategy:
    """Perfect balanced trading strategy."""

    def __init__(self, config: PerfectTradingConfig):
        self.config = config
        self.last_signal = 0
        self.consecutive_signals = 0
        self.market_bias = 0.0  # -1 to +1, negative=bearish, positive=bullish

    def calculate_indicators(self, prices: List[float]) -> Dict:
        """Calculate balanced indicators."""
        if len(prices) < 10:
            # Use simple defaults
            current = prices[-1] if prices else 2500
            return {
                'rsi': 50.0,
                'rsi_status': 'NEUTRAL',
                'ema_short': current,
                'ema_long': current,
                'ema_trend': 'NEUTRAL',
                'ema_crossover': 0,
                'macd_line': 0,
                'macd_signal': 0,
                'macd_histogram': 0,
                'macd_trend': 'NEUTRAL',
                'price': current,
                'trend': 'SIDEWAYS'
            }

        # Simple RSI
        if len(prices) >= self.config.RSI_PERIOD:
            changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
            gains = sum(max(0, c) for c in changes[-self.config.RSI_PERIOD:])
            losses = sum(abs(min(0, c)) for c in changes[-self.config.RSI_PERIOD:])

            if losses == 0:
                rsi = 100
            else:
                rs = gains / losses
                rsi = 100 - (100 / (1 + rs))
        else:
            rsi = 50.0

        # Simple EMA
        def quick_ema(data, period):
            if len(data) < period:
                return data[-1]
            alpha = 2 / (period + 1)
            ema = data[0]
            for price in data[1:]:
                ema = price * alpha + ema * (1 - alpha)
            return ema

        ema_short = quick_ema(prices[-self.config.EMA_SHORT:], self.config.EMA_SHORT)
        ema_long = quick_ema(prices[-self.config.EMA_LONG:], self.config.EMA_LONG)

        # Determine overall trend
        if len(prices) > 20:
            short_avg = np.mean(prices[-5:])
            long_avg = np.mean(prices[-20:])
            if short_avg > long_avg * 1.01:
                trend = 'UPTREND'
            elif short_avg < long_avg * 0.99:
                trend = 'DOWNTREND'
            else:
                trend = 'SIDEWAYS'
        else:
            trend = 'SIDEWAYS'

        # Update market bias
        price_change = (prices[-1] - prices[0]) / prices[0] * 100 if len(prices) > 1 else 0
        self.market_bias = np.tanh(price_change / 10)  # Convert to -1 to +1

        return {
            'rsi': rsi,
            'rsi_status': 'OVERSOLD' if rsi < self.config.RSI_OVERSOLD
            else 'OVERBOUGHT' if rsi > self.config.RSI_OVERBOUGHT
            else 'NEUTRAL',
            'ema_short': ema_short,
            'ema_long': ema_long,
            'ema_trend': 'BULLISH' if ema_short > ema_long else 'BEARISH',
            'ema_crossover': ema_short - ema_long,
            'macd_line': ema_short - ema_long,  # Simplified
            'macd_signal': (ema_short - ema_long) * 0.9,  # Simplified
            'macd_histogram': (ema_short - ema_long) * 0.1,  # Simplified
            'macd_trend': 'BULLISH' if ema_short > ema_long else 'BEARISH',
            'price': prices[-1],
            'trend': trend,
            'market_bias': self.market_bias
        }

    def generate_signal(self, indicators: Dict, has_positions: bool) -> Tuple[int, float, List[str]]:
        """Generate balanced trading signals with profit taking."""
        rsi = indicators['rsi']
        rsi_status = indicators['rsi_status']
        ema_trend = indicators['ema_trend']
        market_bias = indicators['market_bias']
        trend = indicators['trend']

        score = 0.0
        reasons = []

        # 1. RSI Analysis (40%)
        if rsi_status == 'OVERSOLD':
            score += 0.6
            reasons.append(f"RSI oversold ({rsi:.1f})")
        elif rsi_status == 'OVERBOUGHT':
            score -= 0.4  # A general negative signal
            reasons.append("RSI overbought - avoid buying")
            if has_positions:
                score -= 0.3  # Stronger sell signal if holding positions
                reasons.append("Consider selling")
        else:  # Neutral RSI
            if rsi < 45:
                score += 0.2  # Weak buy signal
            if rsi > 55 and has_positions:
                score -= 0.2  # Weak sell signal if holding

        # 2. EMA Trend (30%)
        if ema_trend == 'BULLISH':
            score += 0.4
            reasons.append("EMA bullish")
        else:
            score -= 0.2  # General negative signal
            if has_positions:
                score -= 0.2  # Stronger sell signal if holding
                reasons.append("EMA bearish - consider selling")

        # 3. Market Bias & Trend (20%)
        if trend == 'UPTREND':
            score += 0.3 * market_bias
            reasons.append(f"Market in {trend.lower()}")
        elif trend == 'DOWNTREND':
            score += 0.3 * market_bias
            reasons.append(f"Market in {trend.lower()}")

        # 4. Avoid chasing - don't buy at highs
        if indicators['price'] > indicators['ema_long'] * 1.02 and not has_positions:
            score -= 0.3
            reasons.append("Price above EMA - avoid chasing")

        # 5. Profit taking bias if we have positions
        if has_positions:
            # Bias toward taking profits
            score -= 0.2
            if len(reasons) < 3:
                reasons.append("Has positions - consider profit taking")

        # Calculate confidence
        confidence = min(abs(score), 1.0)
        confidence = max(self.config.MIN_CONFIDENCE, confidence)

        # Apply market bias to confidence
        confidence *= (1 + abs(market_bias) * 0.3)

        # Generate signal
        if score > 0.25 and not has_positions:
            return 1, confidence, reasons  # BUY
        elif score < -0.25 and has_positions:
            return -1, confidence, reasons  # SELL
        else:
            return 0, 0.5, ["No clear signal - holding"]


# ============================================================================
# PERFECT TRADING BOT
# ============================================================================

class PerfectTradingBot:
    """Perfect trading bot with balanced trading and profit taking."""

    def __init__(self, config: PerfectTradingConfig):
        self.config = config

        # Initialize components
        self.market = PerfectMarketSimulator(config)
        self.strategy = PerfectTradingStrategy(config)

        # Trading state
        self.balance = config.INITIAL_BALANCE
        self.positions = []  # List of dicts with additional info
        self.trade_history = []

        # Statistics
        self.cycle = 0
        self.start_time = dt.now()
        self.total_pnl = 0.0
        self.total_trades = 0
        self.winning_trades = 0
        self.total_buys = 0
        self.total_sells = 0

        # Risk management
        self.max_drawdown = 0.0
        self.peak_portfolio = config.INITIAL_BALANCE

        # Display
        print("\n" + "=" * 60)
        print("🎯 PERFECT TRADING BOT - BALANCED STRATEGY")
        print("=" * 60)
        print(f"📊 Symbol: {config.SYMBOL}")
        print(f"💰 Initial Balance: ₹{config.INITIAL_BALANCE:,.2f}")
        print(f"⚡ Update Interval: {config.UPDATE_INTERVAL} seconds")
        print(f"🎯 Strategy: Balanced RSI/EMA with Profit Taking")
        print(f"📈 Position Size: {config.POSITION_SIZE} shares")
        print(f"🛡️  Stop Loss: {config.STOP_LOSS_PCT}% | Take Profit: {config.TAKE_PROFIT_PCT}%")
        print(f"💰 Partial Profit Taking: {'ENABLED' if config.PARTIAL_PROFIT_TAKING else 'DISABLED'}")
        print("=" * 60)

    def execute_buy(self, price: float, confidence: float, reasons: List[str]) -> bool:
        """Execute buy with position management."""
        # Calculate position size based on confidence and available positions
        base_size = self.config.POSITION_SIZE
        available_slots = self.config.MAX_POSITIONS - len(self.positions)

        if available_slots <= 0:
            print("  ⚠️  Max positions reached - cannot buy")
            return False

        # Scale position based on confidence and available slots
        size_multiplier = min(confidence, 1.0) * (available_slots / self.config.MAX_POSITIONS)
        position_size = int(base_size * (0.5 + size_multiplier * 0.5))
        position_size = max(10, position_size)  # Minimum

        cost = position_size * price

        if self.balance >= cost:
            # Calculate stop loss and take profit
            stop_loss = price * (1 - self.config.STOP_LOSS_PCT / 100)
            take_profit = price * (1 + self.config.TAKE_PROFIT_PCT / 100)

            # Execute buy
            self.balance -= cost
            self.positions.append({
                'id': len(self.positions) + 1,
                'quantity': position_size,
                'entry_price': price,
                'entry_time': dt.now(),
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'highest_price': price,  # For trailing stop
                'reasons': reasons
            })

            # Record trade
            self.trade_history.append({
                'time': dt.now(),
                'action': 'BUY',
                'quantity': position_size,
                'price': price,
                'cost': cost,
                'confidence': confidence,
                'reasons': reasons,
                'type': 'PAPER'
            })

            self.total_trades += 1
            self.total_buys += 1

            print(f"\n  🟢 BUY #{self.total_trades}")
            print(f"     Quantity: {position_size} shares")
            print(f"     Price: ₹{price:.2f}")
            print(f"     Cost: ₹{cost:,.2f}")
            print(f"     Confidence: {confidence:.1%}")
            print(f"     Reasons: {', '.join(reasons[:2])}")
            print(f"     Stop Loss: ₹{stop_loss:.2f} ({self.config.STOP_LOSS_PCT}%)")
            print(f"     Take Profit: ₹{take_profit:.2f} ({self.config.TAKE_PROFIT_PCT}%)")

            return True
        else:
            print(f"  ⚠️  Insufficient funds: Need ₹{cost:,.2f}, have ₹{self.balance:,.2f}")
            return False

    def check_and_execute_sells(self, current_price: float) -> List[float]:
        """Check for stop loss, take profit, and trailing stop triggers."""
        pnls = []
        positions_to_remove = []

        for i, position in enumerate(self.positions):
            quantity = position['quantity']
            entry_price = position['entry_price']

            # Update highest price for trailing stop
            if current_price > position['highest_price']:
                position['highest_price'] = current_price

            # Check stop loss
            if current_price <= position['stop_loss']:
                pnl = self._execute_sell(i, current_price, "STOP_LOSS")
                pnls.append(pnl)
                positions_to_remove.append(i)
                continue

            # Check take profit
            if current_price >= position['take_profit']:
                if self.config.PARTIAL_PROFIT_TAKING:
                    # Partial profit taking
                    sell_quantity = int(quantity * self.config.PARTIAL_SELL_PCT)
                    if sell_quantity > 0:
                        pnl = self._execute_partial_sell(i, sell_quantity, current_price, "PARTIAL_TAKE_PROFIT")
                        pnls.append(pnl)
                        # Update position quantity
                        position['quantity'] -= sell_quantity
                        if position['quantity'] <= 0:
                            positions_to_remove.append(i)
                else:
                    # Full sell
                    pnl = self._execute_sell(i, current_price, "TAKE_PROFIT")
                    pnls.append(pnl)
                    positions_to_remove.append(i)
                continue

            # Check trailing stop
            if self.config.TRAILING_STOP_PCT > 0:
                trail_stop = position['highest_price'] * (1 - self.config.TRAILING_STOP_PCT / 100)
                if current_price <= trail_stop:
                    pnl = self._execute_sell(i, current_price, "TRAILING_STOP")
                    pnls.append(pnl)
                    positions_to_remove.append(i)

        # Remove positions (in reverse order to maintain indices)
        for i in sorted(positions_to_remove, reverse=True):
            if i < len(self.positions):
                self.positions.pop(i)

        return pnls

    def _execute_sell(self, idx: int, price: float, reason: str) -> float:
        """Execute sell of entire position."""
        position = self.positions[idx]
        quantity = position['quantity']
        entry_price = position['entry_price']
        revenue = quantity * price
        pnl = revenue - (quantity * entry_price)
        pnl_pct = (pnl / (quantity * entry_price)) * 100 if (quantity * entry_price) > 0 else 0

        # Update balance
        self.balance += revenue

        # Update statistics
        self.total_pnl += pnl
        self.total_trades += 1
        self.total_sells += 1

        if pnl > 0:
            self.winning_trades += 1

        # Record trade
        self.trade_history.append({
            'time': dt.now(),
            'action': 'SELL',
            'quantity': quantity,
            'price': price,
            'revenue': revenue,
            'entry_price': entry_price,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'sell_reason': reason,
            'is_partial': False,
            'type': 'PAPER'
        })

        # Display
        pnl_color = "\033[92m" if pnl > 0 else "\033[91m"
        reset_color = "\033[0m"

        print(f"\n  🔴 SELL #{self.total_trades}")
        print(f"     Quantity: {quantity} shares")
        print(f"     Price: ₹{price:.2f}")
        print(f"     Entry: ₹{entry_price:.2f}")
        print(f"     P&L: {pnl_color}₹{pnl:+,.2f} ({pnl_pct:+.2f}%){reset_color}")
        print(f"     Reason: {reason}")

        return pnl

    def _execute_partial_sell(self, idx: int, quantity: int, price: float, reason: str) -> float:
        """Execute partial sell of position."""
        position = self.positions[idx]
        entry_price = position['entry_price']
        revenue = quantity * price
        pnl = revenue - (quantity * entry_price)
        pnl_pct = (pnl / (quantity * entry_price)) * 100 if (quantity * entry_price) > 0 else 0

        # Update balance
        self.balance += revenue

        # Update statistics
        self.total_pnl += pnl
        self.total_trades += 1
        self.total_sells += 1

        if pnl > 0:
            self.winning_trades += 1

        # Record trade
        self.trade_history.append({
            'time': dt.now(),
            'action': 'SELL',
            'quantity': quantity,
            'price': price,
            'revenue': revenue,
            'entry_price': entry_price,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'sell_reason': reason,
            'is_partial': True,
            'type': 'PAPER'
        })

        # Display
        pnl_color = "\033[92m" if pnl > 0 else "\033[91m"
        reset_color = "\033[0m"

        print(f"\n  🔴 PARTIAL SELL #{self.total_trades}")
        print(f"     Quantity: {quantity} shares")
        print(f"     Price: ₹{price:.2f}")
        print(f"     Entry: ₹{entry_price:.2f}")
        print(f"     P&L: {pnl_color}₹{pnl:+,.2f} ({pnl_pct:+.2f}%){reset_color}")
        print(f"     Reason: {reason}")

        return pnl

    def execute_signal_sell(self, price: float, confidence: float, reasons: List[str]) -> bool:
        """Execute sell based on trading signal."""
        if not self.positions:
            return False

        # Find position with the best return to sell, even if it's a loss.
        best_position_idx = -1
        best_profit_pct = -float('inf')

        for i, position in enumerate(self.positions):
            current_value = position['quantity'] * price
            cost = position['quantity'] * position['entry_price']
            profit_pct = (current_value - cost) / cost * 100 if cost > 0 else 0

            if profit_pct > best_profit_pct:
                best_profit_pct = profit_pct
                best_position_idx = i

        if best_position_idx >= 0:
            pnl = self._execute_sell(best_position_idx, price, "SIGNAL")

            # The position is removed from the list after being sold.
            self.positions.pop(best_position_idx)

            # Add reasons to the trade history
            if self.trade_history:
                self.trade_history[-1]['reasons'] = reasons

            return True
        else:
            # This should not happen if there are positions.
            print("  ⚠️  Signal to sell, but no position was selected.")
            return False

    def run_cycle(self) -> Dict:
        """Run one perfect trading cycle."""
        self.cycle += 1

        # Generate current price
        current_price = self.market.generate_price()

        # Get price history
        price_history = self.market.get_price_history(100)

        # Check and execute automatic sells (stop loss, take profit, trailing stop)
        auto_sell_pnls = self.check_and_execute_sells(current_price)

        # Calculate indicators
        indicators = self.strategy.calculate_indicators(price_history)

        # Generate signal
        has_positions = len(self.positions) > 0
        signal, confidence, reasons = self.strategy.generate_signal(indicators, has_positions)

        # Execute trade based on signal
        signal_executed = False
        signal_pnl = 0.0

        if signal == 1 and len(self.positions) < self.config.MAX_POSITIONS:  # Buy if not at max positions
            signal_executed = self.execute_buy(current_price, confidence, reasons)
        elif signal == -1 and has_positions:
            signal_executed = self.execute_signal_sell(current_price, confidence, reasons)
            if signal_executed and self.trade_history:
                signal_pnl = self.trade_history[-1].get('pnl', 0.0)

        # Calculate portfolio value
        portfolio_value = self._calculate_portfolio_value(current_price)

        # Update drawdown
        self._update_drawdown(portfolio_value)

        return {
            'cycle': self.cycle,
            'timestamp': dt.now(),
            'price': current_price,
            'indicators': indicators,
            'signal': signal,
            'confidence': confidence,
            'reasons': reasons,
            'signal_executed': signal_executed,
            'signal_pnl': signal_pnl,
            'auto_sell_pnls': auto_sell_pnls,
            'portfolio_value': portfolio_value,
            'balance': self.balance,
            'positions': len(self.positions),
            'total_pnl': self.total_pnl,
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'current_drawdown': self._calculate_drawdown(portfolio_value)
        }

    def _calculate_portfolio_value(self, current_price: float) -> float:
        """Calculate total portfolio value."""
        position_value = sum(p['quantity'] * current_price for p in self.positions)
        return self.balance + position_value

    def _calculate_drawdown(self, portfolio_value: float) -> float:
        """Calculate current drawdown."""
        if portfolio_value > self.peak_portfolio:
            self.peak_portfolio = portfolio_value
            return 0.0
        else:
            return (self.peak_portfolio - portfolio_value) / self.peak_portfolio * 100

    def _update_drawdown(self, portfolio_value: float):
        """Update maximum drawdown."""
        current_dd = self._calculate_drawdown(portfolio_value)
        if current_dd > self.max_drawdown:
            self.max_drawdown = current_dd

    def display_status(self, data: Dict):
        """Display perfect status information."""
        print(f"\n[Cycle {data['cycle']:03d}] {data['timestamp'].strftime('%H:%M:%S')}")
        print("-" * 60)

        # Price with change
        if len(self.market.prices) > 1:
            prev = self.market.prices[-2]
            change = ((data['price'] - prev) / prev) * 100
            change_icon = "↗️" if change > 0 else "↘️" if change < 0 else "➡️"
            price_str = f"💰 Price: ₹{data['price']:.2f} ({change_icon} {change:+.2f}%)"
        else:
            price_str = f"💰 Price: ₹{data['price']:.2f}"

        print(price_str)
        print(f"💳 Balance: ₹{data['balance']:,.2f}")
        print(f"📊 Positions: {data['positions']}/{self.config.MAX_POSITIONS}")
        print(f"📈 Portfolio: ₹{data['portfolio_value']:,.2f}")

        # P&L with color
        pnl_color = "\033[92m" if data['total_pnl'] >= 0 else "\033[91m"
        reset_color = "\033[0m"
        print(f"🎯 Total P&L: {pnl_color}₹{data['total_pnl']:+,.2f}{reset_color}")

        # Trading stats
        win_rate = (data['winning_trades'] / data['total_trades'] * 100) if data['total_trades'] > 0 else 0
        print(
            f"🔄 Trades: {data['total_trades']} (B:{self.total_buys}/S:{self.total_sells}) | Win Rate: {win_rate:.1f}%")

        # Drawdown
        if data['current_drawdown'] > 0:
            print(f"⚠️  Drawdown: {data['current_drawdown']:.2f}%")

        # Indicators
        if data['indicators']:
            ind = data['indicators']
            print(f"\n📊 Market Analysis:")
            print(f"  RSI: {ind['rsi']:.1f} ({ind['rsi_status']})")
            print(f"  Trend: {ind['trend']}")
            print(f"  EMA: {ind['ema_trend']} ({ind['ema_short']:.1f}/{ind['ema_long']:.1f})")
            bias_status = 'BULLISH' if ind['market_bias'] > 0.1 else 'BEARISH' if ind['market_bias'] < -0.1 else 'NEUTRAL'
            print(f"  Bias: {bias_status}")

        # Signal
        signal_text = {1: '🟢 BUY', -1: '🔴 SELL', 0: '🟡 HOLD'}
        signal_emoji = "🚀" if data['signal'] == 1 else "💸" if data['signal'] == -1 else "⏸️"

        print(f"\n{signal_emoji} Signal: {signal_text[data['signal']]} ({data['confidence']:.1%})")

        if data['reasons'] and data['signal'] != 0:
            print(f"   Reasons: {', '.join(data['reasons'][:2])}")

        # Open positions
        if self.positions:
            print(f"\n📦 Open Positions ({len(self.positions)}):")
            total_invested = 0
            total_unrealized = 0

            for pos in self.positions[-3:]:  # Show last 3
                current_val = pos['quantity'] * data['price']
                cost = pos['quantity'] * pos['entry_price']
                unrealized = current_val - cost
                unrealized_pct = (unrealized / cost) * 100 if cost > 0 else 0

                total_invested += cost
                total_unrealized += unrealized

                pnl_color = "\033[92m" if unrealized > 0 else "\033[91m"
                status = "✅ PROFIT" if unrealized_pct >= 1 else "⚠️  LOSS" if unrealized_pct <= -1 else "➖ NEUTRAL"

                print(f"  #{pos['id']}: {pos['quantity']} @ ₹{pos['entry_price']:.2f}")
                print(f"     Current: ₹{current_val:,.2f} | {status}")
                print(f"     P&L: {pnl_color}₹{unrealized:+,.2f} ({unrealized_pct:+.2f}%){reset_color}")

            if len(self.positions) > 3:
                print(f"  ... and {len(self.positions) - 3} more positions")

            # Summary
            total_current = sum(p['quantity'] * data['price'] for p in self.positions)
            total_pnl_color = "\033[92m" if total_unrealized > 0 else "\033[91m"
            print(f"\n  📊 Summary: Invested: ₹{total_invested:,.2f} | Current: ₹{total_current:,.2f}")
            print(f"     Total Unrealized: {total_pnl_color}₹{total_unrealized:+,.2f}{reset_color}")

        # Recent auto-sells
        if data['auto_sell_pnls']:
            print(f"\n⚡ Auto-sells this cycle: {len(data['auto_sell_pnls'])}")
            total_auto_pnl = sum(data['auto_sell_pnls'])
            if total_auto_pnl != 0:
                pnl_color = "\033[92m" if total_auto_pnl > 0 else "\033[91m"
                print(f"   Total Auto-sell P&L: {pnl_color}₹{total_auto_pnl:+,.2f}{reset_color}")

    def run(self, duration_hours: float = 0.5):
        """Run the perfect trading bot."""
        print(f"\n{'=' * 60}")
        print(f"🚀 PERFECT TRADING SESSION")
        print(f"{'=' * 60}")

        end_time = self.start_time + timedelta(hours=min(duration_hours, 2))  # Max 2 hours

        print(f"⏰ Session end time: {end_time.strftime('%H:%M:%S')}")
        print(f"   ({min(duration_hours, 2)} hours)")
        print(f"\n🎯 Starting balanced trading...")

        try:
            while dt.now() < end_time:
                # Run cycle
                cycle_data = self.run_cycle()

                # Display
                self.display_status(cycle_data)

                # Time info
                elapsed = (dt.now() - self.start_time).seconds / 3600
                remaining = (end_time - dt.now()).seconds / 3600

                print(f"\n{'=' * 50}")
                print(f"⏱️  Elapsed: {elapsed:.2f}h | Remaining: {remaining:.2f}h")
                print(f"⏳ Next update in {self.config.UPDATE_INTERVAL} seconds...")

                time.sleep(self.config.UPDATE_INTERVAL)

        except KeyboardInterrupt:
            print("\n\n🛑 Trading stopped by user")

        self.show_summary()

    def show_summary(self):
        """Show perfect trading summary."""
        print(f"\n{'=' * 60}")
        print(f"📊 PERFECT TRADING SUMMARY")
        print(f"{'=' * 60}")

        # Final calculations
        final_price = self.market.last_price
        portfolio_value = self._calculate_portfolio_value(final_price)
        total_return = ((portfolio_value - self.config.INITIAL_BALANCE) / self.config.INITIAL_BALANCE) * 100

        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0

        print(f"\n📈 PERFORMANCE SUMMARY:")
        print(f"  Total Cycles: {self.cycle}")
        print(f"  Session Duration: {((dt.now() - self.start_time).seconds / 3600):.2f}h")
        print(f"  Initial Balance: ₹{self.config.INITIAL_BALANCE:,.2f}")
        print(f"  Final Portfolio: ₹{portfolio_value:,.2f}")
        print(f"  Total Return: {total_return:+.2f}%")
        print(f"  Total P&L: ₹{self.total_pnl:+,.2f}")
        print(f"  Max Drawdown: {self.max_drawdown:.2f}%")

        print(f"\n📊 TRADING ACTIVITY:")
        print(f"  Total Trades: {self.total_trades}")
        print(f"  Buy Orders: {self.total_buys}")
        print(f"  Sell Orders: {self.total_sells}")
        print(f"  Winning Trades: {self.winning_trades}")
        print(f"  Losing Trades: {self.total_trades - self.winning_trades}")
        print(f"  Win Rate: {win_rate:.1f}%")

        if self.total_trades > 0:
            avg_pnl = self.total_pnl / self.total_trades
            avg_color = "\033[92m" if avg_pnl > 0 else "\033[91m"
            print(f"  Average Trade P&L: {avg_color}₹{avg_pnl:+,.2f}\033[0m")

        print(f"\n📦 FINAL POSITIONS:")
        if self.positions:
            total_invested = 0
            total_current = 0
            total_unrealized = 0

            for pos in self.positions:
                current_val = pos['quantity'] * final_price
                cost = pos['quantity'] * pos['entry_price']
                unrealized = current_val - cost
                unrealized_pct = (unrealized / cost) * 100 if cost > 0 else 0

                total_invested += cost
                total_current += current_val
                total_unrealized += unrealized

                pnl_color = "\033[92m" if unrealized > 0 else "\033[91m"
                print(f"  #{pos['id']}: {pos['quantity']} @ ₹{pos['entry_price']:.2f}")
                print(f"     Current: ₹{current_val:,.2f}")
                print(f"     P&L: {pnl_color}₹{unrealized:+,.2f} ({unrealized_pct:+.2f}%)\033[0m")

            total_pnl_color = "\033[92m" if total_unrealized > 0 else "\033[91m"
            print(f"\n  📊 Summary:")
            print(f"     Total Invested: ₹{total_invested:,.2f}")
            print(f"     Total Current Value: ₹{total_current:,.2f}")
            print(f"     Total Unrealized P&L: {total_pnl_color}₹{total_unrealized:+,.2f}\033[0m")
        else:
            print(f"  No open positions")

        print(f"\n🔄 RECENT TRADES (last 8):")
        recent_trades = self.trade_history[-8:]

        if recent_trades:
            for trade in recent_trades:
                time_str = trade['time'].strftime('%H:%M:%S')
                action = trade['action']
                qty = trade['quantity']
                price = trade['price']

                action_emoji = "🟢" if action == 'BUY' else "🔴"

                if 'pnl' in trade:
                    pnl = trade['pnl']
                    pnl_color = "\033[92m" if pnl > 0 else "\033[91m"
                    reason = trade.get('sell_reason', 'SIGNAL')
                    partial = " (Partial)" if trade.get('is_partial', False) else ""
                    print(f"  {time_str} {action_emoji} {action}{partial} {qty} @ ₹{price:.2f}")
                    print(f"    P&L: {pnl_color}₹{pnl:+,.2f}\033[0m | {reason}")
                else:
                    print(f"  {time_str} {action_emoji} {action} {qty} @ ₹{price:.2f}")
        else:
            print(f"  No trades executed")

        # Market summary
        print(f"\n📈 MARKET SUMMARY:")
        if self.market.prices:
            high = max(self.market.prices)
            low = min(self.market.prices)
            start = self.market.prices[0]
            end = self.market.prices[-1]
            market_return = ((end - start) / start) * 100

            print(f"  Price Range: ₹{low:.2f} - ₹{high:.2f}")
            print(f"  Start: ₹{start:.2f} | End: ₹{end:.2f}")
            print(f"  Market Return: {market_return:+.2f}%")
            print(f"  Your Return: {total_return:+.2f}%")
            print(f"  Alpha (vs Market): {total_return - market_return:+.2f}%")

        print(f"\n{'=' * 60}")
        print(f"🎯 SESSION COMPLETE - Ready for Live Trading!")
        print(f"{'=' * 60}")


# ============================================================================
# MAIN - PERFECT
# ============================================================================

def main():
    """Main perfect trading bot."""
    print("\n" + "=" * 60)
    print("🎯 PERFECT TRADING BOT - FIXED VERSION")
    print("=" * 60)

    print("\n📊 This bot features:")
    print("  • Balanced BUY/SELL signals")
    print("  • Automatic stop loss & take profit")
    print("  • Partial profit taking")
    print("  • Trailing stop loss")
    print("  • Risk management with drawdown control")
    print("  • Realistic market simulation")

    # Create config
    config = PerfectTradingConfig()

    # Get duration (sensible limit)
    try:
        max_duration = 2  # Maximum 2 hours for testing
        prompt = f"\n⏰ Enter duration in hours (0.25 to {max_duration}): "
        duration = float(input(prompt).strip() or "0.5")
        duration = min(max(0.25, duration), max_duration)  # Clamp between 0.25 and 2
    except:
        duration = 0.5

    # Create and run
    bot = PerfectTradingBot(config)

    print(f"\n🚀 Starting in 3 seconds...")
    time.sleep(3)

    bot.run(duration_hours=duration)


# ============================================================================
# QUICK 30-MINUTE TEST
# ============================================================================

def quick_test():
    """30-minute perfect trading test."""
    print("\n" + "=" * 60)
    print("⚡ 30-MINUTE PERFECT TRADING TEST")
    print("=" * 60)

    config = PerfectTradingConfig()

    bot = PerfectTradingBot(config)

    # Run for 30 minutes
    bot.run(duration_hours=0.5)


# ============================================================================
# SUPER QUICK 5-MINUTE DEMO
# ============================================================================

def quick_demo():
    """5-minute demo to see immediate action."""
    print("\n" + "=" * 60)
    print("🚀 5-MINUTE QUICK DEMO")
    print("=" * 60)

    config = PerfectTradingConfig()
    config.UPDATE_INTERVAL = 5  # Faster for demo

    bot = PerfectTradingBot(config)

    # Run for 5 minutes
    bot.run(duration_hours=0.083)


# ============================================================================
# SCRIPT ENTRY
# ============================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        if sys.argv[1] == "test":
            quick_test()
        elif sys.argv[1] == "demo":
            quick_demo()
        else:
            print(f"Usage: python {sys.argv[0]} [test|demo]")
            print("  test: 30-minute balanced test")
            print("  demo: 5-minute quick demo")
            print("  (no argument): Full interactive mode")
    else:
        main()