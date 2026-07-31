//+------------------------------------------------------------------+
//|                                VelocityExhaustionReversal_EA.mq5 |
//|                                  Copyright 2024, Quant Developer |
//|                                             https://www.mql5.com |
//|                                                                  |
//| Strategy Name: Velocity Exhaustion Reversal (VER)                 |
//| Target Platform: MetaTrader 5 (Pure MQL5, Zero DLLs, Zero Libs)  |
//| Compatibility: XM Broker and general MT5 platforms               |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, Quant Developer"
#property link      "https://www.mql5.com"
#property version   "1.00"
#property strict

// Include standard trade libraries
#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\PositionInfo.mqh>

//+------------------------------------------------------------------+
//|                       INPUT PARAMETERS                           |
//+------------------------------------------------------------------+

// --- MODULE 1: TRADING SESSIONS ---
input group "---- MODULE 1: TRADING SESSIONS ----"
input bool InpUseSessionFilter    = false;      // Enable Session Filter
input int  InpLondonStartHour     = 8;          // London Start Hour (Broker Time)
input int  InpLondonEndHour       = 16;         // London End Hour (Broker Time)
input bool InpLondonActive        = true;       // Trade London Session
input int  InpNYStartHour         = 13;         // New York Start Hour (Broker Time)
input int  InpNYEndHour           = 21;         // New York End Hour (Broker Time)
input bool InpNYActive            = true;       // Trade NY Session
input int  InpAsianStartHour      = 0;          // Asian Start Hour (Broker Time)
input int  InpAsianEndHour        = 8;          // Asian End Hour (Broker Time)
input bool InpAsianActive         = false;      // Trade Asian Session

// --- MODULE 2 & 3: TICK & VELOCITY ENGINE ---
input group "---- MODULE 2 & 3: TICK & VELOCITY ----"
input int    InpTickCacheSize     = 100;        // Rolling Tick Cache Size
input double InpDensityWindowSec  = 2.0;        // Density Window (Seconds)
input int    InpVelocityMAPeriod  = 20;         // Velocity MA Lookback Period (Ticks)
input double InpVelocityMultiplier= 1.5;        // Velocity Trigger Multiplier (Ratio)

// --- MODULE 4: EXPANSION ENGINE ---
input group "---- MODULE 4: EXPANSION ENGINE ----"
input ENUM_TIMEFRAMES InpTimeframe= PERIOD_CURRENT; // Strategy Candle Timeframe
input int    InpATRPeriod         = 14;         // Volatility Lookback Period (Candles)
input double InpExpansionMultiplier=1.1;        // Volatility Expansion Multiplier

// --- MODULE 5: LIQUIDITY SWEEP ---
input group "---- MODULE 5: LIQUIDITY SWEEP ----"
input int    InpSwingLookback     = 10;         // Swing High/Low Lookback (Candles)

// --- MODULE 6: EXHAUSTION ENGINE ---
input group "---- MODULE 6: EXHAUSTION ENGINE ----"
input double InpMinCandlePoints   = 10.0;       // Reject Tiny Candles (Min Points)
input double InpMinWickPct        = 35.0;       // Minimum Rejection Wick %
input double InpMaxBodyPct        = 40.0;       // Maximum Candle Body %

// --- MODULE 7 & 8: SIGNAL & EXECUTION ---
input group "---- MODULE 7 & 8: SIGNAL & EXECUTION ----"
enum EEntryMode
{
   ENTRY_IMMEDIATE, // Enter instantly on tick velocity trigger
   ENTRY_BREAKOUT   // Enter on next candle breakout of signal high/low
};
input EEntryMode InpEntryMode     = ENTRY_BREAKOUT; // Entry Execution Mode
input double InpEntryBufferPoints = 10.0;       // Breakout Entry Buffer (Points)
input int    InpSetupExpiryBars   = 1;          // Entry Setup Expiry Bars
input uint   InpMagicNumber       = 748291;     // Expert Advisor Magic Number
input double InpMaxSpreadPoints   = 50.0;       // Maximum Allowed Spread (Points)
input ulong  InpSlippagePoints    = 10;         // Maximum Slippage (Points)
input int    InpMaxRetries        = 3;          // Maximum Execution Retries
input int    InpRetryDelayMS      = 200;        // Retry Delay (Milliseconds)

// --- MODULE 9: RISK MANAGEMENT ---
input group "---- MODULE 9: RISK MANAGEMENT ----"
enum ERiskMode
{
   RISK_FIXED_LOT, // Use Fixed Lot Size
   RISK_PERCENT    // Use Risk % of Margin
};
input ERiskMode InpRiskMode        = RISK_PERCENT; // Lot Sizing Mode
input double InpFixedLotSize      = 0.1;        // Fixed Lot Size (if RISK_FIXED_LOT)
input double InpRiskPercent       = 1.0;        // Risk Percentage (if RISK_PERCENT)
enum EStopLossMode
{
   SL_ATR,   // Stop Loss based on ATR
   SL_SWING  // Stop Loss based on Swing Extremes
};
input EStopLossMode InpSLMode     = SL_SWING;   // Stop Loss Mode
input double InpSLATRMultiplier   = 2.0;        // ATR Stop Loss Multiplier
input double InpSLSwingPaddingPts = 20.0;       // Swing Stop Loss Padding (Points)
input double InpTPATRMultiplier   = 3.0;        // Take Profit ATR Multiplier
input double InpMaxDailyLossPct   = 5.0;        // Maximum Daily Loss Limit (%)
input int    InpMaxTradesPerDay   = 10;         // Maximum Trades Per Day
input int    InpMaxConsecLosses   = 5;          // Maximum Consecutive Losses Allowed

// --- MODULE 10: EXIT ENGINE ---
input group "---- MODULE 10: EXIT ENGINE ----"
input double InpATRTrailMultiplier= 2.5;        // ATR Trailing Multiplier (0 = Off)
input double InpBreakEvenTriggerPts=150.0;      // Break Even Trigger Distance (Points, 0 = Off)
input double InpBreakEvenBufferPts =20.0;       // Break Even Profit Buffer (Points)
input double InpPartialClosePct   = 50.0;       // Volume Partial Close % (0 = Off)
input double InpPartialCloseRR     = 1.0;        // Risk/Reward Target for Partial Close
input double InpMomentumExitRatio = 0.4;        // Momentum Reversal Ratio Exit (0 = Off)
input int    InpMaxHoldMinutes    = 120;        // Maximum Position Hold Time (Minutes, 0 = Off)

//+------------------------------------------------------------------+
//|                      STRUCTS & ENUMS                             |
//+------------------------------------------------------------------+

struct TickData
{
   double Ask;
   double Bid;
   double Price;
   long   TimeMsc;
   double Distance;
   double Speed;
   double Acceleration;
};

enum ESetupType
{
   SETUP_NONE,
   SETUP_BUY,
   SETUP_SELL
};

struct SignalSetup
{
   ESetupType Type;
   double     TriggerPrice;
   double     StopLoss;
   double     TakeProfit;
   datetime   SetupTime;
   int        SetupBarIndex;
   double     SignalHigh;
   double     SignalLow;
};

//+------------------------------------------------------------------+
//|                      CUTILS: MODULE 1 UTILITIES                  |
//+------------------------------------------------------------------+
class CUtils
{
public:
   static bool IsInSession()
   {
      if(!InpUseSessionFilter) return true;

      MqlDateTime dt;
      TimeToStruct(TimeCurrent(), dt);
      int hour = dt.hour;

      // Check London Session
      if(InpLondonActive)
      {
         if(InpLondonStartHour <= InpLondonEndHour)
         {
            if(hour >= InpLondonStartHour && hour < InpLondonEndHour) return true;
         }
         else
         {
            if(hour >= InpLondonStartHour || hour < InpLondonEndHour) return true;
         }
      }

      // Check New York Session
      if(InpNYActive)
      {
         if(InpNYStartHour <= InpNYEndHour)
         {
            if(hour >= InpNYStartHour && hour < InpNYEndHour) return true;
         }
         else
         {
            if(hour >= InpNYStartHour || hour < InpNYEndHour) return true;
         }
      }

      // Check Asian Session
      if(InpAsianActive)
      {
         if(InpAsianStartHour <= InpAsianEndHour)
         {
            if(hour >= InpAsianStartHour && hour < InpAsianEndHour) return true;
         }
         else
         {
            if(hour >= InpAsianStartHour || hour < InpAsianEndHour) return true;
         }
      }

      return false;
   }
};

//+------------------------------------------------------------------+
//|                  CTICKENGINE: MODULE 2 TICK CACHE                |
//+------------------------------------------------------------------+
class CTickEngine
{
private:
   TickData m_cache[];
   int m_cache_size;
   int m_current_index;
   int m_ticks_count;
   double m_density_window;

public:
   CTickEngine() : m_cache_size(100), m_current_index(0), m_ticks_count(0), m_density_window(2.0) {}

   void Init(int cache_size, double density_window)
   {
      m_cache_size = cache_size;
      m_density_window = density_window;
      ArrayResize(m_cache, m_cache_size);
      m_current_index = 0;
      m_ticks_count = 0;
   }

   void AddTick(const MqlTick &tick)
   {
      TickData data = {0};
      data.Ask = tick.ask;
      data.Bid = tick.bid;
      data.Price = (tick.ask + tick.bid) / 2.0;
      data.TimeMsc = tick.time_msc;

      if(m_ticks_count > 0)
      {
         int prev_index = (m_current_index - 1 + m_cache_size) % m_cache_size;
         long time_diff = tick.time_msc - m_cache[prev_index].TimeMsc;
         if(time_diff <= 0) time_diff = 1;

         data.Distance = MathAbs(data.Price - m_cache[prev_index].Price);

         double dist_points = data.Distance / _Point;
         double seconds = (double)time_diff / 1000.0;
         data.Speed = dist_points / seconds;

         data.Acceleration = (data.Speed - m_cache[prev_index].Speed) / seconds;
      }
      else
      {
         data.Distance = 0.0;
         data.Speed = 0.0;
         data.Acceleration = 0.0;
      }

      m_cache[m_current_index] = data;
      m_current_index = (m_current_index + 1) % m_cache_size;
      if(m_ticks_count < m_cache_size) m_ticks_count++;
   }

   bool GetTick(int offset, TickData &out_tick) const
   {
      if(offset < 0 || offset >= m_ticks_count) return false;
      int idx = (m_current_index - 1 - offset + m_cache_size) % m_cache_size;
      out_tick = m_cache[idx];
      return true;
   }

   int GetTicksCount() const { return m_ticks_count; }

   double GetTickDensity() const
   {
      if(m_ticks_count == 0) return 0.0;
      TickData latest;
      if(!GetTick(0, latest)) return 0.0;

      long cutoff_time = latest.TimeMsc - (long)(m_density_window * 1000.0);
      int count = 0;
      for(int i = 0; i < m_ticks_count; i++)
      {
         TickData t;
         if(GetTick(i, t))
         {
            if(t.TimeMsc >= cutoff_time) count++;
            else break;
         }
      }
      return (double)count;
   }
};

//+------------------------------------------------------------------+
//|                CVELOCITYENGINE: MODULE 3 VELOCITY                |
//+------------------------------------------------------------------+
class CVelocityEngine
{
private:
   const CTickEngine *m_tick_engine;
   int m_period;

public:
   void Init(const CTickEngine *tick_engine, int period)
   {
      m_tick_engine = tick_engine;
      m_period = period;
   }

   bool CalculateVelocity(double &avg_speed, double &cur_speed, double &velocity_ratio, double &accel_ratio)
   {
      int total_ticks = m_tick_engine.GetTicksCount();
      if(total_ticks < m_period || total_ticks == 0) return false;

      double speed_sum = 0.0;
      double accel_sum = 0.0;

      for(int i = 0; i < m_period; i++)
      {
         TickData t;
         if(m_tick_engine.GetTick(i, t))
         {
            speed_sum += t.Speed;
            accel_sum += MathAbs(t.Acceleration);
         }
      }

      avg_speed = speed_sum / m_period;

      TickData latest;
      if(!m_tick_engine.GetTick(0, latest)) return false;

      cur_speed = latest.Speed;
      velocity_ratio = (avg_speed > 0) ? (cur_speed / avg_speed) : 1.0;

      double avg_accel = accel_sum / m_period;
      accel_ratio = (avg_accel > 0) ? (MathAbs(latest.Acceleration) / avg_accel) : 1.0;

      return true;
   }
};

//+------------------------------------------------------------------+
//|                CEXPANSIONENGINE: MODULE 4 EXPANSION              |
//+------------------------------------------------------------------+
class CExpansionEngine
{
public:
   bool CalculateExpansion(const string symbol, ENUM_TIMEFRAMES timeframe, int period, double multiplier, double &current_range, double &atr, double &expansion_score)
   {
      MqlRates rates[];
      int copied = CopyRates(symbol, timeframe, 1, period + 1, rates);
      if(copied < period + 1) return false;

      double tr_sum = 0.0;
      for(int i = 1; i <= period; i++)
      {
         double high = rates[i].high;
         double low = rates[i].low;
         double prev_close = rates[i-1].close;

         double tr = MathMax(high - low, MathMax(MathAbs(high - prev_close), MathAbs(low - prev_close)));
         tr_sum += tr;
      }

      atr = tr_sum / period;

      double high_1 = rates[copied-1].high;
      double low_1 = rates[copied-1].low;
      current_range = high_1 - low_1;

      expansion_score = (atr > 0) ? (current_range / atr) : 1.0;

      return (current_range > multiplier * atr);
   }
};

//+------------------------------------------------------------------+
//|               CSWINGENGINE: MODULE 5 LIQUIDITY SWEEP             |
//+------------------------------------------------------------------+
class CSwingEngine
{
public:
   bool GetRecentSwingPoints(const string symbol, ENUM_TIMEFRAMES timeframe, int lookback, double &swing_high, double &swing_low)
   {
      MqlRates rates[];
      int copied = CopyRates(symbol, timeframe, 2, lookback, rates);
      if(copied < lookback) return false;

      double max_high = 0.0;
      double min_low = DBL_MAX;

      for(int i = 0; i < lookback; i++)
      {
         if(rates[i].high > max_high) max_high = rates[i].high;
         if(rates[i].low < min_low) min_low = rates[i].low;
      }

      swing_high = max_high;
      swing_low = min_low;
      return true;
   }
};

//+------------------------------------------------------------------+
//|               CEXHAUSTIONENGINE: MODULE 6 EXHAUSTION             |
//+------------------------------------------------------------------+
class CExhaustionEngine
{
public:
   bool AnalyzeExhaustion(const string symbol, ENUM_TIMEFRAMES timeframe, double min_candle_points,
                          double min_wick_pct, double max_body_pct,
                          bool &bull_exhaustion, bool &bear_exhaustion,
                          double &body_pct, double &upper_wick_pct, double &lower_wick_pct, double &close_position_pct)
   {
      MqlRates rates[];
      if(CopyRates(symbol, timeframe, 1, 1, rates) < 1) return false;

      double open = rates[0].open;
      double high = rates[0].high;
      double low = rates[0].low;
      double close = rates[0].close;

      double total_range = high - low;
      double point_size = SymbolInfoDouble(symbol, SYMBOL_POINT);

      if(total_range < min_candle_points * point_size) return false;
      if(total_range <= 0.0) return false;

      double body = MathAbs(close - open);
      double upper_wick = high - MathMax(open, close);
      double lower_wick = MathMin(open, close) - low;

      body_pct = (body / total_range) * 100.0;
      upper_wick_pct = (upper_wick / total_range) * 100.0;
      lower_wick_pct = (lower_wick / total_range) * 100.0;
      close_position_pct = (close - low) / total_range * 100.0;

      bull_exhaustion = false;
      bear_exhaustion = false;

      if(lower_wick_pct >= min_wick_pct && body_pct <= max_body_pct && close_position_pct >= (100.0 - max_body_pct))
      {
         bull_exhaustion = true;
      }

      if(upper_wick_pct >= min_wick_pct && body_pct <= max_body_pct && close_position_pct <= max_body_pct)
      {
         bear_exhaustion = true;
      }

      return (bull_exhaustion || bear_exhaustion);
   }
};

//+------------------------------------------------------------------+
//|                CTRADEENGINE: MODULE 8 EXECUTION                  |
//+------------------------------------------------------------------+
class CTradeEngine
{
private:
   CTrade         m_trade;
   CSymbolInfo    m_symbol_info;
   uint           m_magic;
   double         m_max_spread_pts;
   ulong          m_slippage;
   int            m_max_retries;
   int            m_retry_delay_ms;

public:
   void Init(uint magic, double max_spread_pts, ulong slippage, int max_retries, int retry_delay_ms)
   {
      m_magic = magic;
      m_trade.SetExpertMagicNumber(magic);
      m_max_spread_pts = max_spread_pts;
      m_slippage = slippage;
      m_max_retries = max_retries;
      m_retry_delay_ms = retry_delay_ms;
      m_symbol_info.Name(_Symbol);
   }

   bool CheckSpread()
   {
      m_symbol_info.RefreshRates();
      double spread = (m_symbol_info.Ask() - m_symbol_info.Bid()) / _Point;
      if(m_max_spread_pts > 0 && spread > m_max_spread_pts)
      {
         return false;
      }
      return true;
   }

   void ConfigureFilling()
   {
      uint filling = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
      if((filling & SYMBOL_FILLING_FOK) != 0)
         m_trade.SetTypeFilling(ORDER_FILLING_FOK);
      else if((filling & SYMBOL_FILLING_IOC) != 0)
         m_trade.SetTypeFilling(ORDER_FILLING_IOC);
      else
         m_trade.SetTypeFilling(ORDER_FILLING_RETURN);
   }

   bool ExecuteMarketOrder(ENUM_ORDER_TYPE order_type, double volume, double price, double sl, double tp, const string comment)
   {
      ConfigureFilling();

      if(TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) == 0 && MQLInfoInteger(MQL_TESTER) == 0)
      {
         Print("[CTradeEngine] Terminal trade is not allowed!");
         return false;
      }

      if(MQLInfoInteger(MQL_TRADE_ALLOWED) == 0 && MQLInfoInteger(MQL_TESTER) == 0)
      {
         Print("[CTradeEngine] MQL trade is not allowed!");
         return false;
      }

      double required_margin = 0.0;
      if(!OrderCalcMargin(order_type, _Symbol, volume, price, required_margin))
      {
         PrintFormat("[CTradeEngine] Failed to calculate margin for volume %.2f", volume);
         return false;
      }

      double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
      if(required_margin > free_margin)
      {
         PrintFormat("[CTradeEngine] Insufficient margin! Required: %.2f, Free: %.2f", required_margin, free_margin);
         return false;
      }

      bool success = false;
      for(int attempt = 1; attempt <= m_max_retries; attempt++)
      {
         m_symbol_info.RefreshRates();
         double current_price = (order_type == ORDER_TYPE_BUY) ? m_symbol_info.Ask() : m_symbol_info.Bid();

         if(order_type == ORDER_TYPE_BUY)
            success = m_trade.Buy(volume, _Symbol, current_price, sl, tp, comment);
         else
            success = m_trade.Sell(volume, _Symbol, current_price, sl, tp, comment);

         if(success)
         {
            uint ret_code = m_trade.ResultRetcode();
            if(ret_code == TRADE_RETCODE_DONE || ret_code == TRADE_RETCODE_PLACED)
            {
               PrintFormat("[CTradeEngine] Order completed successfully on attempt %d! Ticket: %I64u", attempt, m_trade.ResultOrder());
               return true;
            }
         }

         PrintFormat("[CTradeEngine] Attempt %d failed. Retcode: %u, Error: %s. Retrying...",
                     attempt, m_trade.ResultRetcode(), m_trade.ResultComment());

         if(attempt < m_max_retries)
            Sleep(m_retry_delay_ms);
      }

      return false;
   }

   bool ClosePosition(ulong ticket, double volume = 0.0)
   {
      ConfigureFilling();
      if(volume <= 0.0)
         return m_trade.PositionClose(ticket);
      else
         return m_trade.PositionClosePartial(ticket, volume);
   }
};

//+------------------------------------------------------------------+
//|                CRISKENGINE: MODULE 9 RISK MANAGEMENT             |
//+------------------------------------------------------------------+
class CRiskEngine
{
private:
   double m_fixed_lot;
   double m_risk_pct;
   double m_max_daily_loss;
   int    m_max_trades_per_day;
   int    m_max_consecutive_losses;

   datetime m_last_reset_date;
   double   m_starting_daily_equity;
   int      m_daily_trades_count;
   int      m_consecutive_losses;
   double   m_daily_loss_accumulated;

public:
   CRiskEngine() :
      m_fixed_lot(0.1),
      m_risk_pct(1.0),
      m_max_daily_loss(5.0),
      m_max_trades_per_day(10),
      m_max_consecutive_losses(5),
      m_last_reset_date(0),
      m_starting_daily_equity(0.0),
      m_daily_trades_count(0),
      m_consecutive_losses(0),
      m_daily_loss_accumulated(0.0)
   {}

   void Init(double fixed_lot, double risk_pct, double max_daily_loss,
            int max_trades_per_day, int max_consecutive_losses)
   {
      m_fixed_lot = fixed_lot;
      m_risk_pct = risk_pct;
      m_max_daily_loss = max_daily_loss;
      m_max_trades_per_day = max_trades_per_day;
      m_max_consecutive_losses = max_consecutive_losses;
   }

   void DailyResetCheck()
   {
      datetime cur_time = TimeCurrent();
      datetime today_start = cur_time - (cur_time % 86400);
      if(today_start != m_last_reset_date)
      {
         m_last_reset_date = today_start;
         m_starting_daily_equity = AccountInfoDouble(ACCOUNT_EQUITY);
         m_daily_trades_count = 0;
         m_daily_loss_accumulated = 0.0;
         PrintFormat("[CRiskEngine] Daily reset completed. Starting Equity: %.2f", m_starting_daily_equity);
      }
   }

   bool IsTradingAllowed()
   {
      DailyResetCheck();

      if(m_max_trades_per_day > 0 && m_daily_trades_count >= m_max_trades_per_day)
      {
         PrintFormat("[CRiskEngine] Trading blocked: Daily trades limit (%d) reached.", m_max_trades_per_day);
         return false;
      }

      if(m_max_consecutive_losses > 0 && m_consecutive_losses >= m_max_consecutive_losses)
      {
         PrintFormat("[CRiskEngine] Trading blocked: Max consecutive losses (%d) reached.", m_max_consecutive_losses);
         return false;
      }

      double cur_equity = AccountInfoDouble(ACCOUNT_EQUITY);
      double loss = m_starting_daily_equity - cur_equity;
      double limit = (m_max_daily_loss / 100.0) * m_starting_daily_equity;

      if(loss > limit)
      {
         PrintFormat("[CRiskEngine] Trading blocked: Daily loss limit reached. Current loss: %.2f, Limit: %.2f", loss, limit);
         return false;
      }

      return true;
   }

   void RecordTradeResult(double profit)
   {
      m_daily_trades_count++;
      if(profit < 0.0)
      {
         m_consecutive_losses++;
         m_daily_loss_accumulated += MathAbs(profit);
      }
      else
      {
         m_consecutive_losses = 0;
      }
   }

   double CalculateLotSize(double sl_distance_pts)
   {
      if(InpRiskMode == RISK_FIXED_LOT || sl_distance_pts <= 0.0)
         return m_fixed_lot;

      double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
      double risk_amount = (m_risk_pct / 100.0) * free_margin;

      double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
      double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      if(tick_size <= 0) tick_size = _Point;

      double risk_per_lot = (sl_distance_pts * _Point / tick_size) * tick_value;
      if(risk_per_lot <= 0.0) return m_fixed_lot;

      double calculated_lots = risk_amount / risk_per_lot;

      double lot_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
      double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
      double max_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

      double lots = MathFloor(calculated_lots / lot_step) * lot_step;
      if(lots < min_lot) lots = min_lot;
      if(lots > max_lot) lots = max_lot;

      double required_margin = 0.0;
      if(OrderCalcMargin(ORDER_TYPE_BUY, _Symbol, lots, SymbolInfoDouble(_Symbol, SYMBOL_ASK), required_margin))
      {
         double max_usable_margin = free_margin * 0.70;
         if(required_margin > max_usable_margin)
         {
            lots = MathFloor((max_usable_margin / required_margin) * lots / lot_step) * lot_step;
            if(lots < min_lot) lots = min_lot;
         }
      }

      return lots;
   }
};

//+------------------------------------------------------------------+
//|                  CEXITENGINE: MODULE 10 EXITS                    |
//+------------------------------------------------------------------+
class CExitEngine
{
private:
   CTradeEngine *m_trade_engine;
   double       m_atr_trail_mult;
   double       m_be_trigger_pts;
   double       m_be_buffer_pts;
   double       m_partial_close_pct;
   double       m_partial_close_rr;
   double       m_momentum_exit_ratio;
   int          m_max_hold_minutes;

public:
   void Init(CTradeEngine *trade_engine, double atr_trail_mult, double be_trigger_pts, double be_buffer_pts,
             double partial_close_pct, double partial_close_rr, double momentum_exit_ratio, int max_hold_minutes)
   {
      m_trade_engine = trade_engine;
      m_atr_trail_mult = atr_trail_mult;
      m_be_trigger_pts = be_trigger_pts;
      m_be_buffer_pts = be_buffer_pts;
      m_partial_close_pct = partial_close_pct;
      m_partial_close_rr = partial_close_rr;
      m_momentum_exit_ratio = momentum_exit_ratio;
      m_max_hold_minutes = max_hold_minutes;
   }

   void ManageExits(double current_atr, double velocity_ratio)
   {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(PositionGetSymbol(i) == "") continue;
         if(PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != InpMagicNumber)
            continue;

         ulong ticket = PositionGetInteger(POSITION_TICKET);
         ENUM_POSITION_TYPE pos_type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
         double open_price = PositionGetDouble(POSITION_PRICE_OPEN);
         double current_sl = PositionGetDouble(POSITION_SL);
         double current_tp = PositionGetDouble(POSITION_TP);
         double volume = PositionGetDouble(POSITION_VOLUME);
         datetime open_time = (datetime)PositionGetInteger(POSITION_TIME);

         double cur_price = (pos_type == POSITION_TYPE_BUY) ? SymbolInfoDouble(_Symbol, SYMBOL_BID) : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         double profit_pts = (pos_type == POSITION_TYPE_BUY) ? (cur_price - open_price) / _Point : (open_price - cur_price) / _Point;

         // 1. Time Exit
         if(m_max_hold_minutes > 0)
         {
            long elapsed_seconds = TimeCurrent() - open_time;
            if(elapsed_seconds >= m_max_hold_minutes * 60)
            {
               PrintFormat("[CExitEngine] Time Exit triggered! Hold time elapsed: %d min.", elapsed_seconds / 60);
               m_trade_engine.ClosePosition(ticket);
               continue;
            }
         }

         // 2. Momentum Exit
         if(m_momentum_exit_ratio > 0.0 && velocity_ratio < m_momentum_exit_ratio && profit_pts > 10.0)
         {
            PrintFormat("[CExitEngine] Momentum Exhaustion Exit triggered! Velocity ratio %.2f < %.2f", velocity_ratio, m_momentum_exit_ratio);
            m_trade_engine.ClosePosition(ticket);
            continue;
         }

         // 3. Break Even
         if(m_be_trigger_pts > 0.0 && profit_pts >= m_be_trigger_pts)
         {
            double target_be = open_price + ((pos_type == POSITION_TYPE_BUY) ? 1.0 : -1.0) * m_be_buffer_pts * _Point;
            bool should_modify = false;

            if(pos_type == POSITION_TYPE_BUY && (current_sl < target_be - 0.00001 || current_sl == 0))
               should_modify = true;
            else if(pos_type == POSITION_TYPE_SELL && (current_sl > target_be + 0.00001 || current_sl == 0))
               should_modify = true;

            if(should_modify)
            {
               PrintFormat("[CExitEngine] Modifying SL to Break Even. Entry: %f, Target BE SL: %f", open_price, target_be);
               CTrade trade;
               trade.SetExpertMagicNumber(InpMagicNumber);
               trade.PositionModify(ticket, target_be, current_tp);
               continue;
            }
         }

         // 4. Partial Close
         string comment = PositionGetString(POSITION_COMMENT);
         if(m_partial_close_pct > 0.0 && m_partial_close_rr > 0.0 && StringFind(comment, "PC") < 0)
         {
            double initial_sl_dist = MathAbs(open_price - current_sl) / _Point;
            if(initial_sl_dist > 0 && profit_pts >= initial_sl_dist * m_partial_close_rr)
            {
               double close_vol = MathFloor((volume * (m_partial_close_pct / 100.0)) / SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP)) * SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
               double min_vol = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
               if(close_vol >= min_vol && close_vol < volume)
               {
                  PrintFormat("[CExitEngine] Partial Close Triggered! RR hit. Closing %.2f of %.2f lots", close_vol, volume);
                  m_trade_engine.ClosePosition(ticket, close_vol);
                  continue;
               }
            }
         }

         // 5. ATR Trailing Stop
         if(m_atr_trail_mult > 0.0 && current_atr > 0.0)
         {
            double atr_dist = current_atr * m_atr_trail_mult;
            double new_sl = (pos_type == POSITION_TYPE_BUY) ? (cur_price - atr_dist) : (cur_price + atr_dist);
            bool should_trail = false;

            if(pos_type == POSITION_TYPE_BUY)
            {
               if(new_sl > current_sl + 0.00001 && new_sl < cur_price - 10 * _Point)
                  should_trail = true;
            }
            else
            {
               if((new_sl < current_sl - 0.00001 || current_sl == 0) && new_sl > cur_price + 10 * _Point)
                  should_trail = true;
            }

            if(should_trail)
            {
               CTrade trade;
               trade.SetExpertMagicNumber(InpMagicNumber);
               trade.PositionModify(ticket, new_sl, current_tp);
            }
         }
      }
   }

   void CloseAllPositions(string reason)
   {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(PositionGetSymbol(i) == "") continue;
         if(PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != InpMagicNumber)
            continue;

         ulong ticket = PositionGetInteger(POSITION_TICKET);
         PrintFormat("[CExitEngine] Emergency closing position %I64u due to: %s", ticket, reason);
         m_trade_engine.ClosePosition(ticket);
      }
   }
};

//+------------------------------------------------------------------+
//|                  GLOBAL OBJECTS & VARIABLES                      |
//+------------------------------------------------------------------+

CTickEngine        g_tick_engine;
CVelocityEngine    g_velocity_engine;
CExpansionEngine   g_expansion_engine;
CSwingEngine       g_swing_engine;
CExhaustionEngine  g_exhaustion_engine;
CTradeEngine       g_trade_engine;
CRiskEngine        g_risk_engine;
CExitEngine        g_exit_engine;

SignalSetup        g_active_setup = {0};
datetime           g_last_bar_time = 0;

//+------------------------------------------------------------------+
//|                  EXPERT INITIALIZATION FUNCTION                  |
//+------------------------------------------------------------------+
int OnInit()
{
   // Initialize Engines
   g_tick_engine.Init(InpTickCacheSize, InpDensityWindowSec);
   g_velocity_engine.Init(&g_tick_engine, InpVelocityMAPeriod);
   g_trade_engine.Init(InpMagicNumber, InpMaxSpreadPoints, InpSlippagePoints, InpMaxRetries, InpRetryDelayMS);

   g_risk_engine.Init(InpFixedLotSize, InpRiskPercent, InpMaxDailyLossPct,
                      InpMaxTradesPerDay, InpMaxConsecLosses);

   g_exit_engine.Init(&g_trade_engine, InpATRTrailMultiplier, InpBreakEvenTriggerPts, InpBreakEvenBufferPts,
                      InpPartialClosePct, InpPartialCloseRR, InpMomentumExitRatio, InpMaxHoldMinutes);

   g_active_setup.Type = SETUP_NONE;

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//|                  EXPERT DEINITIALIZATION FUNCTION                |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Print("[VER EA] Deinitialized. Reason code: ", reason);
}

//+------------------------------------------------------------------+
//|                  EXPERT TICK FUNCTION                            |
//+------------------------------------------------------------------+
void OnTick()
{
   // 1. Refresh live ticks cache
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick)) return;
   g_tick_engine.AddTick(tick);

   // 2. Perform daily reset and checks inside risk engine
   g_risk_engine.DailyResetCheck();

   // 3. Close positions if risk limits are breached (Emergency Exit)
   if(!g_risk_engine.IsTradingAllowed())
   {
      g_exit_engine.CloseAllPositions("Daily Risk / Trade limits violated.");
      return;
   }

   // 4. Track candle bar transitions
   datetime current_bar_time = 0;
   MqlRates rates[];
   if(CopyRates(_Symbol, InpTimeframe, 0, 1, rates) > 0)
   {
      current_bar_time = rates[0].time;
   }

   bool is_new_bar = false;
   if(current_bar_time != g_last_bar_time)
   {
      is_new_bar = true;
      g_last_bar_time = current_bar_time;
   }

   // 5. Exits management
   double current_atr = 0.0;
   double current_range = 0.0;
   double expansion_score = 0.0;

   // Pre-calculate ATR for exiting trailing mechanics
   g_expansion_engine.CalculateExpansion(_Symbol, InpTimeframe, InpATRPeriod, InpExpansionMultiplier, current_range, current_atr, expansion_score);

   double avg_speed = 0.0;
   double cur_speed = 0.0;
   double velocity_ratio = 1.0;
   double accel_ratio = 1.0;
   g_velocity_engine.CalculateVelocity(avg_speed, cur_speed, velocity_ratio, accel_ratio);

   g_exit_engine.ManageExits(current_atr, velocity_ratio);

   // 6. Check if we already have an open position (One Position At A Time rule)
   int open_positions = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(PositionGetSymbol(i) == "") continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
      {
         open_positions++;
      }
   }
   if(open_positions > 0)
   {
      // Already holding, reset active setup to prevent stale executions
      g_active_setup.Type = SETUP_NONE;
      return;
   }

   // 7. Session Filter Check
   if(!CUtils::IsInSession())
   {
      g_active_setup.Type = SETUP_NONE;
      return;
   }

   // 8. Strategy Signal & Entry Execution

   // If a new bar opens, check for Setup Expiry or Evaluate New Reversals
   if(is_new_bar)
   {
      // If we had an active breakout setup from the previous bar, check if it expired
      if(g_active_setup.Type != SETUP_NONE)
      {
         int current_bars_total = iBars(_Symbol, InpTimeframe);
         if(current_bars_total - g_active_setup.SetupBarIndex > InpSetupExpiryBars)
         {
            Print("[VER EA] Active setup expired without breakout confirmation.");
            g_active_setup.Type = SETUP_NONE;
         }
      }

      // Look for a new reversal setup on the completed bar (index 1)
      bool expansion_valid = g_expansion_engine.CalculateExpansion(_Symbol, InpTimeframe, InpATRPeriod, InpExpansionMultiplier, current_range, current_atr, expansion_score);

      double swing_high = 0.0;
      double swing_low = 0.0;
      bool swing_valid = g_swing_engine.GetRecentSwingPoints(_Symbol, InpTimeframe, InpSwingLookback, swing_high, swing_low);

      bool bull_ex = false, bear_ex = false;
      double body_p = 0, u_wick_p = 0, l_wick_p = 0, close_pos_p = 0;
      bool exhaustion_valid = g_exhaustion_engine.AnalyzeExhaustion(_Symbol, InpTimeframe, InpMinCandlePoints, InpMinWickPct, InpMaxBodyPct,
                                                                  bull_ex, bear_ex, body_p, u_wick_p, l_wick_p, close_pos_p);

      // Check liquidity sweep conditions on the completed bar (index 1)
      MqlRates completed_rates[];
      if(CopyRates(_Symbol, InpTimeframe, 1, 1, completed_rates) > 0 && swing_valid && expansion_valid && exhaustion_valid)
      {
         double comp_low = completed_rates[0].low;
         double comp_high = completed_rates[0].high;
         double comp_close = completed_rates[0].close;

         // BUY Reversal Setup Requirements
         if(bull_ex && comp_low < swing_low && comp_close > swing_low)
         {
            g_active_setup.Type = SETUP_BUY;
            g_active_setup.SignalHigh = comp_high;
            g_active_setup.SignalLow = comp_low;
            g_active_setup.SetupTime = TimeCurrent();
            g_active_setup.SetupBarIndex = iBars(_Symbol, InpTimeframe);

            // Define Stop Loss and Take Profit
            double sl_dist_pts = 0.0;
            if(InpSLMode == SL_SWING)
               g_active_setup.StopLoss = swing_low - InpSLSwingPaddingPts * _Point;
            else
               g_active_setup.StopLoss = comp_close - current_atr * InpSLATRMultiplier;

            sl_dist_pts = MathAbs(comp_close - g_active_setup.StopLoss) / _Point;
            g_active_setup.TakeProfit = comp_close + current_atr * InpTPATRMultiplier;
            g_active_setup.TriggerPrice = comp_high + InpEntryBufferPoints * _Point;

            PrintFormat("[VER EA] BUY Setup Registered. Trigger Price: %f, SL: %f, TP: %f", g_active_setup.TriggerPrice, g_active_setup.StopLoss, g_active_setup.TakeProfit);
         }

         // SELL Reversal Setup Requirements
         if(bear_ex && comp_high > swing_high && comp_close < swing_high)
         {
            g_active_setup.Type = SETUP_SELL;
            g_active_setup.SignalHigh = comp_high;
            g_active_setup.SignalLow = comp_low;
            g_active_setup.SetupTime = TimeCurrent();
            g_active_setup.SetupBarIndex = iBars(_Symbol, InpTimeframe);

            double sl_dist_pts = 0.0;
            if(InpSLMode == SL_SWING)
               g_active_setup.StopLoss = swing_high + InpSLSwingPaddingPts * _Point;
            else
               g_active_setup.StopLoss = comp_close + current_atr * InpSLATRMultiplier;

            sl_dist_pts = MathAbs(g_active_setup.StopLoss - comp_close) / _Point;
            g_active_setup.TakeProfit = comp_close - current_atr * InpTPATRMultiplier;
            g_active_setup.TriggerPrice = comp_low - InpEntryBufferPoints * _Point;

            PrintFormat("[VER EA] SELL Setup Registered. Trigger Price: %f, SL: %f, TP: %f", g_active_setup.TriggerPrice, g_active_setup.StopLoss, g_active_setup.TakeProfit);
         }
      }
   }

   // 9. Process Execution and Velocity Check
   if(g_active_setup.Type != SETUP_NONE)
   {
      // Check velocity engine trigger
      bool velocity_trigger = (velocity_ratio >= InpVelocityMultiplier);

      if(velocity_trigger)
      {
         // Validate spread filter
         if(!g_trade_engine.CheckSpread()) return;

         double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

         // ENTRY EXECUTION
         if(g_active_setup.Type == SETUP_BUY)
         {
            bool trigger_condition = false;
            if(InpEntryMode == ENTRY_IMMEDIATE)
               trigger_condition = true;
            else if(InpEntryMode == ENTRY_BREAKOUT && ask >= g_active_setup.TriggerPrice)
               trigger_condition = true;

            if(trigger_condition)
            {
               double sl_dist = MathAbs(ask - g_active_setup.StopLoss) / _Point;
               double volume = g_risk_engine.CalculateLotSize(sl_dist);

               if(g_trade_engine.ExecuteMarketOrder(ORDER_TYPE_BUY, volume, ask, g_active_setup.StopLoss, g_active_setup.TakeProfit, "VER Buy Entry"))
               {
                  g_active_setup.Type = SETUP_NONE;
               }
            }
         }
         else if(g_active_setup.Type == SETUP_SELL)
         {
            bool trigger_condition = false;
            if(InpEntryMode == ENTRY_IMMEDIATE)
               trigger_condition = true;
            else if(InpEntryMode == ENTRY_BREAKOUT && bid <= g_active_setup.TriggerPrice)
               trigger_condition = true;

            if(trigger_condition)
            {
               double sl_dist = MathAbs(g_active_setup.StopLoss - bid) / _Point;
               double volume = g_risk_engine.CalculateLotSize(sl_dist);

               if(g_trade_engine.ExecuteMarketOrder(ORDER_TYPE_SELL, volume, bid, g_active_setup.StopLoss, g_active_setup.TakeProfit, "VER Sell Entry"))
               {
                  g_active_setup.Type = SETUP_NONE;
               }
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//|                  EXPERT TRADE TRANSACTION FUNCTION               |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   // Track trade execution results and update risk counters
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD)
   {
      ulong deal_ticket = trans.deal;
      if(deal_ticket > 0)
      {
         if(HistoryDealSelect(deal_ticket))
         {
            long deal_magic = HistoryDealGetInteger(deal_ticket, DEAL_MAGIC);
            if(deal_magic == InpMagicNumber)
            {
               double profit = HistoryDealGetDouble(deal_ticket, DEAL_PROFIT);
               double commission = HistoryDealGetDouble(deal_ticket, DEAL_COMMISSION);
               double swap = HistoryDealGetDouble(deal_ticket, DEAL_SWAP);
               double net_profit = profit + commission + swap;

               long entry_type = HistoryDealGetInteger(deal_ticket, DEAL_ENTRY);
               if(entry_type == DEAL_ENTRY_OUT)
               {
                  g_risk_engine.RecordTradeResult(net_profit);
                  PrintFormat("[VER EA] Historical deal recorded. Profit: %.2f", net_profit);
               }
            }
         }
      }
   }
}
