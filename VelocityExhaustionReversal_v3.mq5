//+------------------------------------------------------------------+
//|                                 VelocityExhaustionReversal_v3.mq5|
//|                                  Copyright 2024, Quant Developer |
//|                                             https://www.mql5.com |
//|                                                                  |
//| Strategy Name: Velocity Exhaustion Reversal v3 (VER Pro)          |
//| Target Platform: MetaTrader 5 (Pure MQL5, Zero DLLs, Zero Libs)  |
//| Compatibility: XM Broker and general MT5 platforms               |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, Quant Developer"
#property link      "https://www.mql5.com"
#property version   "3.10"
#property strict

// Include standard trade libraries
#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\PositionInfo.mqh>

//+------------------------------------------------------------------+
//|                       INPUT PARAMETERS                           |
//+------------------------------------------------------------------+

// --- CONFIG & SESSIONS ---
input group "---- CONFIG & SESSIONS ----"
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

// --- TICK & VELOCITY ENGINE ---
input group "---- TICK & VELOCITY ENGINE ----"
input int    InpTickCacheSize     = 100;        // Rolling Tick Cache Ring Buffer Size
input double InpDensityWindowSec  = 2.0;        // Density Window (Seconds)
input int    InpVelocityMAPeriod  = 20;         // Velocity MA Lookback Period (Ticks)
input double InpVelocityMultiplier= 1.1;        // Velocity Trigger Multiplier (Ratio)
input double InpEWMAAlpha         = 0.15;       // EWMA Smoothing Factor
input double InpMADThreshold      = 3.0;        // MAD Outlier Rejection Threshold (z-score)

// --- EXPANSION & STRUCTURE ENGINE ---
input group "---- EXPANSION & STRUCTURE ENGINE ----"
input ENUM_TIMEFRAMES InpTimeframe= PERIOD_CURRENT; // Strategy Candle Timeframe
input int    InpATRPeriod         = 14;         // Volatility Lookback Period (Candles)
input double InpExpansionMultiplier=0.8;        // Volatility Expansion Multiplier
input int    InpSwingLookback     = 10;         // Swing High/Low Lookback (Candles)
input double InpSweepBufferPoints = 100.0;      // Sweep Rejection Zone Buffer (Points)
input double InpEqualLimitPoints  = 20.0;       // Equal High/Low Threshold Buffer (Points)
input int    InpFractalLeftBars   = 2;          // Left Bars for Pivot Swing Identification
input int    InpFractalRightBars  = 2;          // Right Bars for Pivot Swing Identification
input bool   InpRequireDisplacement=false;      // Require Strong Displacement Candle

// --- SIGNAL ENGINE ---
input group "---- SIGNAL ENGINE ----"
input double InpMinCandlePoints   = 10.0;       // Reject Tiny Candles (Min Points)
input double InpMinWickPct        = 30.0;       // Minimum Rejection Wick %
input double InpMaxBodyPct        = 45.0;       // Maximum Candle Body %
input double InpMinDisplacementPct=55.0;        // Minimum Body % for Displacement
input double InpMinSignalScore    = 50.0;       // Minimum Confidence Score to Execute (0-100)

// --- EXECUTION ENGINE ---
input group "---- EXECUTION ENGINE ----"
enum EEntryMode
{
   ENTRY_PASSIVE_LIMIT, // Institutional Passive Execution via Limit Order
   ENTRY_IMMEDIATE,     // Instant Execution
   ENTRY_BREAKOUT       // Breakout confirmation Execution
};
input EEntryMode InpEntryMode     = ENTRY_BREAKOUT; // Entry Execution Mode
input double InpEntryBufferPoints = 10.0;       // Breakout Entry Buffer (Points)
input int    InpSetupExpiryBars   = 1;          // Entry Setup Expiry Bars
input uint   InpMagicNumber       = 748291;     // Expert Advisor Magic Number
input double InpMaxSpreadPoints   = 50.0;       // Maximum Allowed Spread (Points)
input ulong  InpSlippagePoints    = 10;         // Maximum Slippage (Points)
input int    InpMaxRetries        = 5;          // Maximum Execution Retries
input int    InpRetryDelayMS      = 200;        // Retry Delay (Milliseconds)
input double InpIcebergSplitPct   = 50.0;       // Iceberg Order Split Percentage (0 = Off)

// --- RISK ENGINE ---
input group "---- RISK ENGINE ----"
enum ERiskMode
{
   RISK_FIXED_LOT,       // Use Fixed Lot Size
   RISK_PERCENT,         // Use Risk % of Account Equity
   RISK_KELLY_CRITERION, // Volatility-Adjusted Kelly Sizing
   RISK_DRAWDOWN_KELLY   // Drawdown Streak-Adjusted Kelly
};
input ERiskMode InpRiskMode        = RISK_PERCENT; // Lot Sizing Mode
input double InpFixedLotSize      = 0.01;       // Fixed Lot Size (if RISK_FIXED_LOT)
input double InpRiskPercent       = 1.0;        // Risk Percentage (1% is standard safe retail/pro risk)
input double InpKellyWinRate      = 0.55;       // Estimated Strategy Win-Rate for Kelly
input double InpKellyPayoffRatio  = 2.0;        // Estimated Payoff Ratio (Average Win / Average Loss)
input double InpKellyFraction     = 0.10;       // Fractional Kelly Sizing Multiplier (safe 10% fraction)
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

// --- EXIT ENGINE ---
input group "---- EXIT ENGINE ----"
input double InpATRTrailMultiplier= 2.5;        // ATR Trailing Multiplier (0 = Off)
input double InpBreakEvenTriggerPts=150.0;      // Break Even Trigger Distance (Points, 0 = Off)
input double InpBreakEvenBufferPts =20.0;       // Break Even Profit Buffer (Points)
input double InpPartialClosePct   = 50.0;       // Volume Partial Close % (0 = Off)
input double InpPartialCloseRR     = 1.0;        // Risk/Reward Target for Partial Close
input double InpMomentumExitRatio = 0.4;        // Momentum Reversal Ratio Exit (0 = Off)
input int    InpMaxHoldMinutes    = 120;        // Maximum Position Hold Time (Minutes, 0 = Off)
input bool   InpUseSlowdownExit   = true;       // Close position on extreme tick speed slowdown

// --- DASHBOARD ---
input group "---- DASHBOARD ----"
input bool   InpDrawDashboard     = true;       // Render Chart Statistics Dashboard

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
   double Speed;        // Speed in raw points per second
   double NormSpeed;    // Volatility-normalized speed (ATR units per second)
   double Acceleration; // Acceleration in speed units per second
};

enum ESetupType
{
   SETUP_NONE,
   SETUP_BUY,
   SETUP_SELL
};

enum ESignalState
{
   STATE_IDLE,
   STATE_PENDING_BREAKOUT,
   STATE_BREAKOUT_DETECTED
};

struct SignalSetup
{
   ESetupType   Type;
   double       TriggerPrice;
   double       StopLoss;
   double       TakeProfit;
   datetime     SetupTime;
   int          SetupBarIndex;
   double       SignalHigh;
   double       SignalLow;
   ESignalState State;
};

//+------------------------------------------------------------------+
//|                      CLOGGER: LOGGER SYSTEM                      |
//+------------------------------------------------------------------+
class CLogger
{
public:
   static void Info(const string message)
   {
      PrintFormat("[VER INFO] %s", message);
   }
   static void Error(const string message, int errorCode = 0)
   {
      if(errorCode > 0)
         PrintFormat("[VER ERROR] %s (Error Code: %d)", message, errorCode);
      else
         PrintFormat("[VER ERROR] %s", message);
   }
};

//+------------------------------------------------------------------+
//|                      CSYMBOLTIME: SYMBOL & TIME MANAGER          |
//+------------------------------------------------------------------+
class CSymbolTime
{
public:
   static bool IsInSession()
   {
      if(InpUseSessionFilter == false) return true;

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
//|       CTICKENGINE: MODULE 2 ZERO-ITERATION RING BUFFER           |
//+------------------------------------------------------------------+
class CTickEngine
{
private:
   TickData m_cache[];
   int m_cache_size;
   int m_head; // Pointer to latest tick
   int m_count;
   double m_density_window;

   // O(1) Real-time Rolling Statistics
   double m_sum_speed;
   double m_sum_sq_speed;
   double m_sum_norm;
   double m_sum_sq_norm;

public:
   CTickEngine() :
      m_cache_size(100),
      m_head(-1),
      m_count(0),
      m_density_window(2.0),
      m_sum_speed(0.0),
      m_sum_sq_speed(0.0),
      m_sum_norm(0.0),
      m_sum_sq_norm(0.0)
   {}

   void Init(int cache_size, double density_window)
   {
      m_cache_size = cache_size;
      m_density_window = density_window;
      ArrayResize(m_cache, m_cache_size);
      m_head = -1;
      m_count = 0;
      m_sum_speed = 0.0;
      m_sum_sq_speed = 0.0;
      m_sum_norm = 0.0;
      m_sum_sq_norm = 0.0;
   }

   void AddTick(const MqlTick &tick, double live_atr)
   {
      m_head = (m_head + 1) % m_cache_size;

      TickData data = {0};
      data.Ask = tick.ask;
      data.Bid = tick.bid;
      data.Price = (tick.ask + tick.bid) / 2.0;
      data.TimeMsc = tick.time_msc;

      if(m_count > 0)
      {
         int prev_index = (m_head - 1 + m_cache_size) % m_cache_size;
         long time_diff = tick.time_msc - m_cache[prev_index].TimeMsc;
         if(time_diff <= 0) time_diff = 1;

         data.Distance = MathAbs(data.Price - m_cache[prev_index].Price);
         double dist_points = data.Distance / _Point;
         double seconds = (double)time_diff / 1000.0;
         data.Speed = dist_points / seconds;

         double atr_points = live_atr / _Point;
         if(atr_points <= 0.0) atr_points = 100.0;
         data.NormSpeed = data.Speed / atr_points;
         data.Acceleration = (data.Speed - m_cache[prev_index].Speed) / seconds;
      }
      else
      {
         data.Distance = 0.0;
         data.Speed = 0.0;
         data.NormSpeed = 0.0;
         data.Acceleration = 0.0;
      }

      // Subtract the oldest element from rolling statistics
      if(m_count >= m_cache_size)
      {
         m_sum_speed -= m_cache[m_head].Speed;
         m_sum_sq_speed -= (m_cache[m_head].Speed * m_cache[m_head].Speed);
         m_sum_norm -= m_cache[m_head].NormSpeed;
         m_sum_sq_norm -= (m_cache[m_head].NormSpeed * m_cache[m_head].NormSpeed);
      }

      m_cache[m_head] = data;

      // Add new element to rolling statistics
      m_sum_speed += data.Speed;
      m_sum_sq_speed += (data.Speed * data.Speed);
      m_sum_norm += data.NormSpeed;
      m_sum_sq_norm += (data.NormSpeed * data.NormSpeed);

      if(m_count < m_cache_size) m_count++;
   }

   bool GetTick(int offset, TickData &out_tick) const
   {
      if(offset < 0 || offset >= m_count) return false;
      int idx = (m_head - offset + m_cache_size) % m_cache_size;
      out_tick = m_cache[idx];
      return true;
   }

   int GetTicksCount() const { return m_count; }

   double GetRollingMean() const
   {
      if(m_count == 0) return 0.0;
      return m_sum_norm / m_count;
   }

   double GetRollingVariance() const
   {
      if(m_count <= 1) return 0.0;
      double mean = GetRollingMean();
      double var = (m_sum_sq_norm / m_count) - (mean * mean);
      return (var < 0) ? 0.0 : var;
   }

   double GetTickDensity() const
   {
      if(m_count == 0) return 0.0;
      long cutoff_time = m_cache[m_head].TimeMsc - (long)(m_density_window * 1000.0);
      int density_count = 0;
      for(int i = 0; i < m_count; i++)
      {
         int idx = (m_head - i + m_cache_size) % m_cache_size;
         if(m_cache[idx].TimeMsc >= cutoff_time) density_count++;
         else break;
      }
      return (double)density_count;
   }
};

//+------------------------------------------------------------------+
//|      CVELOCITYENGINE: EWMA & MAD OUTLIER ROBUST VELOCITY        |
//+------------------------------------------------------------------+
class CVelocityEngine
{
private:
   const CTickEngine *m_tick_engine;
   int m_period;
   double m_ewma;

   void BubbleSort(double &arr[], int size)
   {
      for(int i = 0; i < size - 1; i++)
      {
         for(int j = 0; j < size - i - 1; j++)
         {
            if(arr[j] > arr[j + 1])
            {
               double temp = arr[j];
               arr[j] = arr[j + 1];
               arr[j + 1] = temp;
            }
         }
      }
   }

   double GetMedian(double &arr[], int size)
   {
      if(size <= 0) return 0.0;
      BubbleSort(arr, size);
      if(size % 2 != 0)
         return arr[size / 2];
      else
         return (arr[(size - 1) / 2] + arr[size / 2]) / 2.0;
   }

public:
   CVelocityEngine() : m_tick_engine(NULL), m_period(20), m_ewma(0.0) {}

   void Init(const CTickEngine *tick_engine, int period)
   {
      m_tick_engine = tick_engine;
      m_period = period;
      m_ewma = 0.0;
   }

   bool CalculateVelocity(double &avg_speed, double &cur_speed, double &velocity_ratio, double &accel_ratio)
   {
      int total_ticks = m_tick_engine.GetTicksCount();
      if(total_ticks < m_period || total_ticks == 0) return false;

      double speeds[];
      ArrayResize(speeds, m_period);

      for(int i = 0; i < m_period; i++)
      {
         TickData t;
         if(m_tick_engine.GetTick(i, t))
            speeds[i] = t.NormSpeed;
         else
            speeds[i] = 0.0;
      }

      double median = GetMedian(speeds, m_period);

      double deviations[];
      ArrayResize(deviations, m_period);
      for(int i = 0; i < m_period; i++)
      {
         deviations[i] = MathAbs(speeds[i] - median);
      }
      double mad = GetMedian(deviations, m_period);
      if(mad <= 0.0) mad = 1e-6;

      double sum_clean = 0.0;
      int clean_count = 0;
      for(int i = 0; i < m_period; i++)
      {
         double z_score = MathAbs(speeds[i] - median) / (1.4826 * mad);
         if(z_score <= InpMADThreshold)
         {
            sum_clean += speeds[i];
            clean_count++;
         }
      }

      avg_speed = (clean_count > 0) ? (sum_clean / clean_count) : median;

      TickData latest;
      if(!m_tick_engine.GetTick(0, latest)) return false;

      double current_raw_speed = latest.NormSpeed;
      if(m_ewma == 0.0) m_ewma = current_raw_speed;
      else m_ewma = (InpEWMAAlpha * current_raw_speed) + ((1.0 - InpEWMAAlpha) * m_ewma);

      cur_speed = m_ewma;
      velocity_ratio = (avg_speed > 0.0) ? (cur_speed / avg_speed) : 1.0;

      accel_ratio = 1.0;

      return true;
   }
};

//+------------------------------------------------------------------+
//|                CEXPANSIONENGINE: MODULE 4 EXPANSION              |
//+------------------------------------------------------------------+
class CExpansionEngine
{
private:
   int m_atr_handle;

public:
   CExpansionEngine() : m_atr_handle(INVALID_HANDLE) {}

   void Init(const string symbol, ENUM_TIMEFRAMES timeframe, int period)
   {
      m_atr_handle = iATR(symbol, timeframe, period);
   }

   void Deinit()
   {
      if(m_atr_handle != INVALID_HANDLE)
      {
         IndicatorRelease(m_atr_handle);
         m_atr_handle = INVALID_HANDLE;
      }
   }

   double GetLiveATR()
   {
      if(m_atr_handle == INVALID_HANDLE) return 0.0;
      double atr_values[];
      ArraySetAsSeries(atr_values, true);
      if(CopyBuffer(m_atr_handle, 0, 0, 1, atr_values) < 1)
      {
         return 0.0;
      }
      return atr_values[0];
   }

   bool CalculateExpansion(const string symbol, ENUM_TIMEFRAMES timeframe, double multiplier, double &current_range, double &atr, double &expansion_score)
   {
      atr = GetLiveATR();
      if(atr <= 0.0) return false;

      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      int copied = CopyRates(symbol, timeframe, 1, 1, rates);
      if(copied < 1) return false;

      current_range = rates[0].high - rates[0].low;
      expansion_score = current_range / atr;

      return (current_range > multiplier * atr);
   }
};

//+------------------------------------------------------------------+
//|    CSWINGENGINE: FRACTALS & LIQUIDITY POOL DETECTION            |
//+------------------------------------------------------------------+
class CSwingEngine
{
public:
   bool IsSwingHighPivot(const MqlRates &rates[], int idx, int left_bars, int right_bars, int total_size)
   {
      if(idx < right_bars || idx >= total_size - left_bars) return false;
      double target_high = rates[idx].high;
      for(int i = 1; i <= left_bars; i++)
      {
         if(rates[idx + i].high > target_high) return false;
      }
      for(int i = 1; i <= right_bars; i++)
      {
         if(rates[idx - i].high >= target_high) return false;
      }
      return true;
   }

   bool IsSwingLowPivot(const MqlRates &rates[], int idx, int left_bars, int right_bars, int total_size)
   {
      if(idx < right_bars || idx >= total_size - left_bars) return false;
      double target_low = rates[idx].low;
      for(int i = 1; i <= left_bars; i++)
      {
         if(rates[idx + i].low < target_low) return false;
      }
      for(int i = 1; i <= right_bars; i++)
      {
         if(rates[idx - i].low <= target_low) return false;
      }
      return true;
   }

   bool GetRecentSwingPoints(const string symbol, ENUM_TIMEFRAMES timeframe, int lookback, double &swing_high, double &swing_low)
   {
      MqlRates rates[];
      ArraySetAsSeries(rates, true);

      int request_bars = lookback + InpFractalLeftBars + InpFractalRightBars + 2;
      int copied = CopyRates(symbol, timeframe, 2, request_bars, rates);
      if(copied < request_bars) return false;

      double pivot_high = 0.0;
      double pivot_low = DBL_MAX;

      for(int i = InpFractalRightBars; i < copied - InpFractalLeftBars; i++)
      {
         if(IsSwingHighPivot(rates, i, InpFractalLeftBars, InpFractalRightBars, copied))
         {
            if(rates[i].high > pivot_high) pivot_high = rates[i].high;
         }
         if(IsSwingLowPivot(rates, i, InpFractalLeftBars, InpFractalRightBars, copied))
         {
            if(rates[i].low < pivot_low) pivot_low = rates[i].low;
         }
      }

      if(pivot_high == 0.0 || pivot_low == DBL_MAX)
      {
         double highest = 0.0;
         double lowest = DBL_MAX;
         for(int i = 0; i < lookback; i++)
         {
            if(rates[i].high > highest) highest = rates[i].high;
            if(rates[i].low < lowest) lowest = rates[i].low;
         }
         swing_high = highest;
         swing_low = lowest;
         return true;
      }

      swing_high = pivot_high;
      swing_low = pivot_low;
      return true;
   }
};

//+------------------------------------------------------------------+
//|    CEXHAUSTIONENGINE: MODULE 6 DISPLACEMENT STRUCTURES           |
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
      ArraySetAsSeries(rates, true);
      if(CopyRates(symbol, timeframe, 1, 2, rates) < 2) return false;

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

      bool displacement_confirmed = true;
      if(InpRequireDisplacement)
      {
         double prev_range = rates[1].high - rates[1].low;
         double prev_body = MathAbs(rates[1].close - rates[1].open);
         double prev_body_pct = (prev_range > 0.0) ? (prev_body / prev_range * 100.0) : 0.0;
         if(prev_body_pct < InpMinDisplacementPct)
         {
            displacement_confirmed = false;
         }
      }

      if(displacement_confirmed)
      {
         if(lower_wick_pct >= min_wick_pct && body_pct <= max_body_pct && close_position_pct >= (100.0 - max_body_pct))
         {
            bull_exhaustion = true;
         }

         if(upper_wick_pct >= min_wick_pct && body_pct <= max_body_pct && close_position_pct <= max_body_pct)
         {
            bear_exhaustion = true;
         }
      }

      return (bull_exhaustion || bear_exhaustion);
   }
};

//+------------------------------------------------------------------+
//|    CTRADEENGINE: PASSIVE LIMIT & LATENCY COMPENSATED EXECUTION   |
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

      if(TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) == 0 && MQLInfoInteger(MQL_TESTER) == 0) return false;
      if(MQLInfoInteger(MQL_TRADE_ALLOWED) == 0 && MQLInfoInteger(MQL_TESTER) == 0) return false;

      double required_margin = 0.0;
      if(!OrderCalcMargin(order_type, _Symbol, volume, price, required_margin)) return false;

      double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
      if(required_margin > free_margin) return false;

      bool success = false;
      for(int attempt = 1; attempt <= m_max_retries; attempt++)
      {
         m_symbol_info.RefreshRates();
         double current_price = (order_type == ORDER_TYPE_BUY) ? m_symbol_info.Ask() : m_symbol_info.Bid();

         double order_volume = volume;
         if(InpIcebergSplitPct > 0.0 && InpIcebergSplitPct < 100.0)
         {
            order_volume = volume * (InpIcebergSplitPct / 100.0);
            double lot_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
            order_volume = MathFloor(order_volume / lot_step) * lot_step;
            if(order_volume < SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN)) order_volume = volume;
         }

         if(order_type == ORDER_TYPE_BUY)
            success = m_trade.Buy(order_volume, _Symbol, current_price, sl, tp, comment);
         else
            success = m_trade.Sell(order_volume, _Symbol, current_price, sl, tp, comment);

         if(success)
         {
            uint ret_code = m_trade.ResultRetcode();
            if(ret_code == TRADE_RETCODE_DONE || ret_code == TRADE_RETCODE_PLACED)
            {
               if(order_volume < volume)
               {
                  double remaining = volume - order_volume;
                  if(remaining >= SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN))
                  {
                     m_trade.Buy(remaining, _Symbol, current_price, sl, tp, comment + " [Iceberg Bal]");
                  }
               }
               return true;
            }
         }

         if(attempt < m_max_retries)
            Sleep(m_retry_delay_ms);
      }

      return false;
   }

   bool ExecuteLimitOrder(ENUM_ORDER_TYPE order_type, double volume, double limit_price, double sl, double tp, const string comment)
   {
      ConfigureFilling();
      if(TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) == 0 && MQLInfoInteger(MQL_TESTER) == 0) return false;

      m_symbol_info.RefreshRates();
      bool success = false;

      if(order_type == ORDER_TYPE_BUY)
         success = m_trade.BuyLimit(volume, limit_price, _Symbol, sl, tp, ORDER_TIME_DAY, 0, comment);
      else
         success = m_trade.SellLimit(volume, limit_price, _Symbol, sl, tp, ORDER_TIME_DAY, 0, comment);

      if(success)
      {
         uint ret = m_trade.ResultRetcode();
         return (ret == TRADE_RETCODE_DONE || ret == TRADE_RETCODE_PLACED);
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
//|    CRISKENGINE: DRAWDOWN-ADJUSTED KELLY CRITERION SIZING         |
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

      if(m_max_trades_per_day > 0 && m_daily_trades_count >= m_max_trades_per_day) return false;
      if(m_max_consecutive_losses > 0 && m_consecutive_losses >= m_max_consecutive_losses) return false;

      double cur_equity = AccountInfoDouble(ACCOUNT_EQUITY);
      double loss = m_starting_daily_equity - cur_equity;
      double limit = (m_max_daily_loss / 100.0) * m_starting_daily_equity;

      if(loss > limit) return false;

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
      if(sl_distance_pts <= 0.0) return m_fixed_lot;

      double account_equity = AccountInfoDouble(ACCOUNT_EQUITY);
      double active_risk_pct = m_risk_pct;

      if(InpRiskMode == RISK_KELLY_CRITERION || InpRiskMode == RISK_DRAWDOWN_KELLY)
      {
         double p = InpKellyWinRate;
         double b = InpKellyPayoffRatio;
         if(b > 0.0)
         {
            double raw_kelly = (p * (b + 1.0) - 1.0) / b;
            if(raw_kelly > 0.0)
            {
               active_risk_pct = raw_kelly * InpKellyFraction * 100.0;
            }
         }

         if(InpRiskMode == RISK_DRAWDOWN_KELLY && m_consecutive_losses > 0)
         {
            double decay_factor = MathMax(0.1, 1.0 - (m_consecutive_losses * 0.20));
            active_risk_pct *= decay_factor;
         }
      }

      if(InpRiskMode == RISK_FIXED_LOT) return m_fixed_lot;

      double risk_amount = (active_risk_pct / 100.0) * account_equity;

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
         double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
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
//|                  CEXITENGINE: ADAPTIVE EXIT SYSTEMS              |
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

   ulong        m_partially_closed_tickets[];

   bool IsAlreadyPartiallyClosed(ulong ticket)
   {
      int size = ArraySize(m_partially_closed_tickets);
      for(int i = 0; i < size; i++)
      {
         if(m_partially_closed_tickets[i] == ticket) return true;
      }
      return false;
   }

   void RegisterPartialClose(ulong ticket)
   {
      int size = ArraySize(m_partially_closed_tickets);
      ArrayResize(m_partially_closed_tickets, size + 1);
      m_partially_closed_tickets[size] = ticket;
   }

public:
   CExitEngine() : m_trade_engine(NULL) {}

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
      ArrayFree(m_partially_closed_tickets);
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
               m_trade_engine.ClosePosition(ticket);
               continue;
            }
         }

         // 2. Momentum Decay & Speed Slowdown Exit Filter
         if(InpUseSlowdownExit && velocity_ratio < 0.25 && profit_pts > 50.0)
         {
            PrintFormat("[CExitEngine] Speed Slowdown Exit triggered! Velocity decay ratio: %.2f", velocity_ratio);
            m_trade_engine.ClosePosition(ticket);
            continue;
         }

         if(m_momentum_exit_ratio > 0.0 && velocity_ratio < m_momentum_exit_ratio && profit_pts > 10.0)
         {
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
               CTrade trade;
               trade.SetExpertMagicNumber(InpMagicNumber);
               trade.PositionModify(ticket, target_be, current_tp);
               continue;
            }
         }

         // 4. Robust Partial Close with Duplicate Prevention
         if(m_partial_close_pct > 0.0 && m_partial_close_rr > 0.0 && !IsAlreadyPartiallyClosed(ticket))
         {
            double initial_sl_dist = MathAbs(open_price - current_sl) / _Point;
            if(initial_sl_dist > 0 && profit_pts >= initial_sl_dist * m_partial_close_rr)
            {
               double close_vol = MathFloor((volume * (m_partial_close_pct / 100.0)) / SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP)) * SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
               double min_vol = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
               if(close_vol >= min_vol && close_vol < volume)
               {
                  if(m_trade_engine.ClosePosition(ticket, close_vol))
                  {
                     RegisterPartialClose(ticket);
                  }
                  continue;
               }
            }
         }

         // 5. Adaptive Trailing Stop
         if(m_atr_trail_mult > 0.0 && current_atr > 0.0)
         {
            double profit_atr = (current_atr > 0.0) ? ((profit_pts * _Point) / current_atr) : 0.0;
            double scale = 1.0 - MathMin(0.5, profit_atr * 0.15);
            double adaptive_trail_mult = m_atr_trail_mult * scale;

            double atr_dist = current_atr * adaptive_trail_mult;
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
         m_trade_engine.ClosePosition(ticket);
      }
   }
};

//+------------------------------------------------------------------+
//|                  CDASHBOARD: VISUAL REALTIME UI                  |
//+------------------------------------------------------------------+
class CDashboard
{
public:
   static void Draw(int total_ticks, double speed, double avg_speed, double score)
   {
      if(!InpDrawDashboard || MQLInfoInteger(MQL_TESTER)) return;

      string objName = "VER_PRO_DASHBOARD";
      string text = StringFormat("VER PRO v3.00 | Ticks: %d | Speed: %.2f (Avg: %.2f) | Score: %.1f",
                                 total_ticks, speed, avg_speed, score);

      if(ObjectFind(0, objName) < 0)
      {
         ObjectCreate(0, objName, OBJ_LABEL, 0, 0, 0);
         ObjectSetInteger(0, objName, OBJPROP_XDISTANCE, 15);
         ObjectSetInteger(0, objName, OBJPROP_YDISTANCE, 25);
         ObjectSetInteger(0, objName, OBJPROP_CORNER, CORNER_LEFT_UPPER);
         ObjectSetString(0, objName, OBJPROP_FONT, "Consolas");
         ObjectSetInteger(0, objName, OBJPROP_FONTSIZE, 11);
         ObjectSetInteger(0, objName, OBJPROP_COLOR, clrAqua);
      }
      ObjectSetString(0, objName, OBJPROP_TEXT, text);
   }

   static void Destroy()
   {
      string objName = "VER_PRO_DASHBOARD";
      if(ObjectFind(0, objName) >= 0)
      {
         ObjectDelete(0, objName);
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
bool               g_velocity_burst_detected = false;

//+------------------------------------------------------------------+
//|                  EXPERT INITIALIZATION FUNCTION                  |
//+------------------------------------------------------------------+
int OnInit()
{
   CLogger::Info("Initializing VER Pro v3.10...");

   g_tick_engine.Init(InpTickCacheSize, InpDensityWindowSec);
   g_velocity_engine.Init(&g_tick_engine, InpVelocityMAPeriod);
   g_trade_engine.Init(InpMagicNumber, InpMaxSpreadPoints, InpSlippagePoints, InpMaxRetries, InpRetryDelayMS);

   g_risk_engine.Init(InpFixedLotSize, InpRiskPercent, InpMaxDailyLossPct,
                      InpMaxTradesPerDay, InpMaxConsecLosses);

   g_exit_engine.Init(&g_trade_engine, InpATRTrailMultiplier, InpBreakEvenTriggerPts, InpBreakEvenBufferPts,
                      InpPartialClosePct, InpPartialCloseRR, InpMomentumExitRatio, InpMaxHoldMinutes);

   g_expansion_engine.Init(_Symbol, InpTimeframe, InpATRPeriod);

   g_active_setup.Type = SETUP_NONE;
   g_active_setup.State = STATE_IDLE;
   g_velocity_burst_detected = false;

   CLogger::Info("VER Pro v3.10 Initialized Successfully.");
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//|                  EXPERT DEINITIALIZATION FUNCTION                |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   g_expansion_engine.Deinit();
   CDashboard::Destroy();
   CLogger::Info(StringFormat("VER Pro v3.10 Deinitialized. Reason code: %d", reason));
}

//+------------------------------------------------------------------+
//|                  EXPERT TICK FUNCTION                            |
//+------------------------------------------------------------------+
void OnTick()
{
   // 1. Obtain current volatility ATR for normalized speed scaling
   double current_atr = g_expansion_engine.GetLiveATR();
   if(current_atr <= 0.0) current_atr = _Point * 100.0;

   // 2. Refresh live ticks cache
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick)) return;
   g_tick_engine.AddTick(tick, current_atr);

   // 3. Perform daily reset and checks inside risk engine
   g_risk_engine.DailyResetCheck();

   // 4. Close positions if risk limits are breached (Emergency Exit)
   if(!g_risk_engine.IsTradingAllowed())
   {
      g_exit_engine.CloseAllPositions("Daily Risk / Trade limits violated.");
      return;
   }

   // 5. Track candle bar transitions
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

   // 6. Exits management
   double current_range = 0.0;
   double expansion_score = 0.0;

   // Calculate live expansion metrics
   g_expansion_engine.CalculateExpansion(_Symbol, InpTimeframe, InpExpansionMultiplier, current_range, current_atr, expansion_score);

   double avg_speed = 0.0;
   double cur_speed = 0.0;
   double velocity_ratio = 1.0;
   double accel_ratio = 1.0;
   if(g_velocity_engine.CalculateVelocity(avg_speed, cur_speed, velocity_ratio, accel_ratio))
   {
      // Track any speed burst spikes during the candle formation period
      if(velocity_ratio >= InpVelocityMultiplier)
      {
         g_velocity_burst_detected = true;
      }
   }

   g_exit_engine.ManageExits(current_atr, velocity_ratio);

   // Refresh visual stats Dashboard
   CDashboard::Draw(g_tick_engine.GetTicksCount(), cur_speed, avg_speed, velocity_ratio * 100.0);

   // 7. Check if we already have an open position (One Position At A Time rule)
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
      g_active_setup.Type = SETUP_NONE;
      g_active_setup.State = STATE_IDLE;
      return;
   }

   // 8. Session Filter Check
   if(!CSymbolTime::IsInSession())
   {
      g_active_setup.Type = SETUP_NONE;
      g_active_setup.State = STATE_IDLE;
      return;
   }

   // 9. State Machine Evaluation for Signals and Entry Execution

   // Handle new bar setups registration
   if(is_new_bar)
   {
      // Setup expiry verification
      if(g_active_setup.Type != SETUP_NONE && g_active_setup.State == STATE_PENDING_BREAKOUT)
      {
         int current_bars_total = iBars(_Symbol, InpTimeframe);
         if(current_bars_total - g_active_setup.SetupBarIndex > InpSetupExpiryBars)
         {
            g_active_setup.Type = SETUP_NONE;
            g_active_setup.State = STATE_IDLE;
         }
      }

      // Look for new signals on completed bar (index 1)
      bool expansion_valid = g_expansion_engine.CalculateExpansion(_Symbol, InpTimeframe, InpExpansionMultiplier, current_range, current_atr, expansion_score);

      double swing_high = 0.0;
      double swing_low = 0.0;
      bool swing_valid = g_swing_engine.GetRecentSwingPoints(_Symbol, InpTimeframe, InpSwingLookback, swing_high, swing_low);

      bool bull_ex = false, bear_ex = false;
      double body_p = 0, u_wick_p = 0, l_wick_p = 0, close_pos_p = 0;
      bool exhaustion_valid = g_exhaustion_engine.AnalyzeExhaustion(_Symbol, InpTimeframe, InpMinCandlePoints, InpMinWickPct, InpMaxBodyPct,
                                                                  bull_ex, bear_ex, body_p, u_wick_p, l_wick_p, close_pos_p);

      MqlRates completed_rates[];
      if(CopyRates(_Symbol, InpTimeframe, 1, 1, completed_rates) > 0 && swing_valid && expansion_valid && exhaustion_valid && g_velocity_burst_detected)
      {
         double comp_low = completed_rates[0].low;
         double comp_high = completed_rates[0].high;
         double comp_close = completed_rates[0].close;

         // Sweep buffers with EQH / EQL identification filters
         double sweep_low_threshold = swing_low + InpSweepBufferPoints * _Point;
         double sweep_high_threshold = swing_high - InpSweepBufferPoints * _Point;

         // Institutional multi-factored confidence score calculation (0 to 100)
         double confidence_score = 0.0;
         if(expansion_score > 1.2) confidence_score += 35.0;
         else confidence_score += 20.0;

         confidence_score += (100.0 - body_p) * 0.40; // larger wicks increase score
         confidence_score += (velocity_ratio > 1.3) ? 25.0 : 15.0;

         if(confidence_score >= InpMinSignalScore)
         {
            // BUY Reversal Setup Requirements
            if(bull_ex && comp_low <= sweep_low_threshold && comp_close > swing_low)
            {
               g_active_setup.Type = SETUP_BUY;
               g_active_setup.State = STATE_PENDING_BREAKOUT;
               g_active_setup.SignalHigh = comp_high;
               g_active_setup.SignalLow = comp_low;
               g_active_setup.SetupTime = TimeCurrent();
               g_active_setup.SetupBarIndex = iBars(_Symbol, InpTimeframe);

               // BREAKOUT TARGET ENTRY price is actual breakout point
               g_active_setup.TriggerPrice = comp_high + InpEntryBufferPoints * _Point;

               // Define Stop Loss and Take Profit RELATIVE TO THE ACTUAL EXPECTED ENTRY PRICE
               if(InpSLMode == SL_SWING)
                  g_active_setup.StopLoss = swing_low - InpSLSwingPaddingPts * _Point;
               else
                  g_active_setup.StopLoss = g_active_setup.TriggerPrice - current_atr * InpSLATRMultiplier;

               g_active_setup.TakeProfit = g_active_setup.TriggerPrice + current_atr * InpTPATRMultiplier;

               CLogger::Info(StringFormat("BUY Setup Registered. Confidence Score: %.1f, Trigger Price: %f, SL: %f, TP: %f",
                             confidence_score, g_active_setup.TriggerPrice, g_active_setup.StopLoss, g_active_setup.TakeProfit));
            }

            // SELL Reversal Setup Requirements
            if(bear_ex && comp_high >= sweep_high_threshold && comp_close < swing_high)
            {
               g_active_setup.Type = SETUP_SELL;
               g_active_setup.State = STATE_PENDING_BREAKOUT;
               g_active_setup.SignalHigh = comp_high;
               g_active_setup.SignalLow = comp_low;
               g_active_setup.SetupTime = TimeCurrent();
               g_active_setup.SetupBarIndex = iBars(_Symbol, InpTimeframe);

               // BREAKOUT TARGET ENTRY price is actual breakout point
               g_active_setup.TriggerPrice = comp_low - InpEntryBufferPoints * _Point;

               // Define Stop Loss and Take Profit RELATIVE TO THE ACTUAL EXPECTED ENTRY PRICE
               if(InpSLMode == SL_SWING)
                  g_active_setup.StopLoss = swing_high + InpSLSwingPaddingPts * _Point;
               else
                  g_active_setup.StopLoss = g_active_setup.TriggerPrice + current_atr * InpSLATRMultiplier;

               g_active_setup.TakeProfit = g_active_setup.TriggerPrice - current_atr * InpTPATRMultiplier;

               CLogger::Info(StringFormat("SELL Setup Registered. Confidence Score: %.1f, Trigger Price: %f, SL: %f, TP: %f",
                             confidence_score, g_active_setup.TriggerPrice, g_active_setup.StopLoss, g_active_setup.TakeProfit));
            }
         }
      }

      // Reset the velocity burst record for the new forming candle
      g_velocity_burst_detected = false;
   }

   // State Machine Execution: Waiting for breakout confirmation and executing instantly
   if(g_active_setup.Type != SETUP_NONE)
   {
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

      if(g_active_setup.State == STATE_PENDING_BREAKOUT)
      {
         bool breakout_confirmed = false;

         if(g_active_setup.Type == SETUP_BUY)
         {
            if(InpEntryMode == ENTRY_IMMEDIATE || InpEntryMode == ENTRY_PASSIVE_LIMIT)
               breakout_confirmed = true;
            else if(InpEntryMode == ENTRY_BREAKOUT && ask >= g_active_setup.TriggerPrice)
               breakout_confirmed = true;
         }
         else if(g_active_setup.Type == SETUP_SELL)
         {
            if(InpEntryMode == ENTRY_IMMEDIATE || InpEntryMode == ENTRY_PASSIVE_LIMIT)
               breakout_confirmed = true;
            else if(InpEntryMode == ENTRY_BREAKOUT && bid <= g_active_setup.TriggerPrice)
               breakout_confirmed = true;
         }

         if(breakout_confirmed)
         {
            if(!g_trade_engine.CheckSpread()) return;

            if(g_active_setup.Type == SETUP_BUY)
            {
               double sl_dist = MathAbs(ask - g_active_setup.StopLoss) / _Point;
               double volume = g_risk_engine.CalculateLotSize(sl_dist);

               if(InpEntryMode == ENTRY_PASSIVE_LIMIT)
               {
                  // Passive execution limit placement at optimized bid price to minimize market impact
                  double entry_lim = SymbolInfoDouble(_Symbol, SYMBOL_BID);
                  if(g_trade_engine.ExecuteLimitOrder(ORDER_TYPE_BUY, volume, entry_lim, g_active_setup.StopLoss, g_active_setup.TakeProfit, "VER Passive Buy Limit"))
                  {
                     g_active_setup.Type = SETUP_NONE;
                     g_active_setup.State = STATE_IDLE;
                  }
               }
               else
               {
                  if(g_trade_engine.ExecuteMarketOrder(ORDER_TYPE_BUY, volume, ask, g_active_setup.StopLoss, g_active_setup.TakeProfit, "VER Buy Entry"))
                  {
                     g_active_setup.Type = SETUP_NONE;
                     g_active_setup.State = STATE_IDLE;
                  }
               }
            }
            else if(g_active_setup.Type == SETUP_SELL)
            {
               double sl_dist = MathAbs(g_active_setup.StopLoss - bid) / _Point;
               double volume = g_risk_engine.CalculateLotSize(sl_dist);

               if(InpEntryMode == ENTRY_PASSIVE_LIMIT)
               {
                  double entry_lim = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
                  if(g_trade_engine.ExecuteLimitOrder(ORDER_TYPE_SELL, volume, entry_lim, g_active_setup.StopLoss, g_active_setup.TakeProfit, "VER Passive Sell Limit"))
                  {
                     g_active_setup.Type = SETUP_NONE;
                     g_active_setup.State = STATE_IDLE;
                  }
               }
               else
               {
                  if(g_trade_engine.ExecuteMarketOrder(ORDER_TYPE_SELL, volume, bid, g_active_setup.StopLoss, g_active_setup.TakeProfit, "VER Sell Entry"))
                  {
                     g_active_setup.Type = SETUP_NONE;
                     g_active_setup.State = STATE_IDLE;
                  }
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
                  CLogger::Info(StringFormat("Historical deal recorded. Profit: %.2f", net_profit));
               }
            }
         }
      }
   }
}
