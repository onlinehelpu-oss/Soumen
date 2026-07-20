//+------------------------------------------------------------------+
//|                                           RedShootingStar_EA.mq5|
//|                                                            Jules |
//|                     Red-ShootingStar Strategy for BTCUSD (MT5)   |
//+------------------------------------------------------------------+
#property copyright "Jules"
#property link      ""
#property version   "1.00"
#property description "Red Shooting Star / Red Pinbar Reversal Strategy for BTCUSD"
#property description "Optimized for next-candle breakout entry with strict cross validation."

// Include standard trade library
#include <Trade\Trade.mqh>

//--- Input Parameters
input group "=== Strategy Parameters ==="
input ENUM_TIMEFRAMES InpTimeframe       = PERIOD_M15;       // Timeframe
input double          InpRiskRewardRatio = 1.0;              // Risk:Reward Ratio (e.g., 1.0 for 1:1)
input double          InpFixedLotSize    = 0.1;              // Fixed Lot Size for BTCUSD
input bool            InpOnePositionAtTime= true;            // One Position At A Time

input group "=== EMA Filters ==="
input int             InpRegimeEMAPeriod = 26;               // Regime EMA Period (Close < EMA)
input bool            InpUseFilterEMA    = true;             // Use Filter EMA (Period 15)
input int             InpFilterEMAPeriod = 15;               // Filter EMA Period (High > EMA, Close < EMA)

input group "=== Small Candle Guard ==="
input double          InpMinRangePct     = 0.0015;           // Min Range Pct (0.15% = 0.0015)

input group "=== Classic Candle Geometry ==="
input double          InpUpperWickMin    = 50.0;             // Min Upper Wick %
input double          InpUpperWickMax    = 80.0;             // Max Upper Wick %
input double          InpBodyMin         = 5.0;              // Min Body %
input double          InpBodyMax         = 30.0;             // Max Body %
input double          InpLowerWickMax    = 25.0;             // Max Lower Wick %

input group "=== Flexible Upper Wick Rejection ==="
input double          InpFluentWickMin   = 50.0;             // Fluent Min Upper Wick %
input double          InpFluentBodyMax   = 30.0;             // Fluent Max Body %
input double          InpFluentLowerWickMax = 25.0;          // Fluent Max Lower Wick %

input group "=== Order Buffers (Points) ==="
input double          InpEntryBufferPoints = 5.0;            // Entry Buffer below Low (in Points)
input double          InpSLBufferPoints    = 2.0;            // SL Buffer above High (in Points)

input group "=== Session Management ==="
input bool            InpUseSessionControl = false;          // Enable Session Control
input int             InpEntryCutoffHour   = 22;             // Entry Cutoff Hour
input int             InpEntryCutoffMinute = 0;              // Entry Cutoff Minute
input int             InpForceExitHour     = 22;             // Force Exit Hour
input int             InpForceExitMinute   = 50;             // Force Exit Minute

input group "=== System Properties ==="
input ulong           InpMagicNumber     = 883211;           // Magic Number

//--- Global variables
CTrade         m_trade;             // Trade execution object
int            m_handle_regime_ema; // Handle for Regime EMA
int            m_handle_filter_ema; // Handle for Filter EMA

// Breakout execution state tracking
bool           m_trigger_active;     // True if breakout monitoring is active
double         m_trigger_low;        // Low of the signal candle
double         m_trigger_high;       // High of the signal candle
double         m_trigger_threshold;  // Breakout price threshold (Low - Entry Buffer)
datetime       m_trigger_time;       // Start time of the signal candle
datetime       m_trigger_start_time; // Start time of the breakout monitoring window
datetime       m_trigger_expiration; // Expiration time of the breakout window
double         m_last_bid;           // Store the last bid price for strict cross detection

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // Set Magic Number for trade object
   m_trade.SetExpertMagicNumber(InpMagicNumber);

   // Reset trigger tracking
   m_trigger_active = false;
   m_trigger_low = 0.0;
   m_trigger_high = 0.0;
   m_trigger_threshold = 0.0;
   m_trigger_time = 0;
   m_trigger_start_time = 0;
   m_trigger_expiration = 0;
   m_last_bid = 0.0;

   // Initialize Indicators
   m_handle_regime_ema = iMA(_Symbol, InpTimeframe, InpRegimeEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
   m_handle_filter_ema = iMA(_Symbol, InpTimeframe, InpFilterEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);

   if(m_handle_regime_ema == INVALID_HANDLE || m_handle_filter_ema == INVALID_HANDLE)
   {
      Print("❌ Failed to create EMA Indicator Handles!");
      return(INIT_FAILED);
   }

   Print("🚀 Red Shooting Star EA initialized successfully for BTCUSD!");
   PrintFormat("Settings: TF=%s | Regime EMA=%d | Filter EMA=%d | R:R=%.2f",
               EnumToString(InpTimeframe), InpRegimeEMAPeriod, InpFilterEMAPeriod, InpRiskRewardRatio);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   // Release indicator handles
   if(m_handle_regime_ema != INVALID_HANDLE) IndicatorRelease(m_handle_regime_ema);
   if(m_handle_filter_ema != INVALID_HANDLE) IndicatorRelease(m_handle_filter_ema);
   Print("🔌 Red Shooting Star EA stopped.");
}

//+------------------------------------------------------------------+
//| Check if an open position exists                                |
//+------------------------------------------------------------------+
bool HasOpenPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(PositionGetSymbol(i) == _Symbol)
      {
         ulong magic = PositionGetInteger(POSITION_MAGIC);
         if(magic == InpMagicNumber)
         {
            return(true);
         }
      }
   }
   return(false);
}

//+------------------------------------------------------------------+
//| Close all EA positions                                           |
//+------------------------------------------------------------------+
void CloseAllPositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(PositionGetSymbol(i) == _Symbol)
      {
         ulong magic = PositionGetInteger(POSITION_MAGIC);
         if(magic == InpMagicNumber)
         {
            m_trade.PositionClose(PositionGetTicket(i));
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Candle geometry and context validation                           |
//+------------------------------------------------------------------+
bool CheckCandleGeometry(const MqlRates &rates[], double regime_ema, double filter_ema, bool &is_valid_context)
{
   // rates[0] is shift 1 (signal candidate), rates[1] is shift 2 (previous)
   double o = rates[0].open;
   double h = rates[0].high;
   double l = rates[0].low;
   double c = rates[0].close;

   double prev_o = rates[1].open;
   double prev_c = rates[1].close;

   double total_range = h - l;
   if(total_range <= 0 || c == 0) return(false);

   // Check min range percentage constraint
   double range_pct = total_range / c;
   if(range_pct < InpMinRangePct) return(false);

   // Calculate candle geometry ratios
   double upper_wick = h - MathMax(o, c);
   double body       = MathAbs(o - c);
   double lower_wick = MathMin(o, c) - l;

   double upper_wick_pct = (upper_wick / total_range) * 100.0;
   double body_pct       = (body / total_range) * 100.0;
   double lower_wick_pct = (lower_wick / total_range) * 100.0;

   // 1. Classic Shooting Star: Must be Red, preceded by Green
   bool is_classic = false;
   if(c < o && prev_c > prev_o)
   {
      if(upper_wick_pct >= InpUpperWickMin && upper_wick_pct <= InpUpperWickMax &&
         body_pct >= InpBodyMin && body_pct <= InpBodyMax &&
         lower_wick_pct >= 0 && lower_wick_pct <= InpLowerWickMax)
      {
         is_classic = true;
      }
   }

   // 2. Flexible Upper Wick Rejection: Any color signal candle, previous Green
   bool is_fluent = false;
   if(prev_c > prev_o)
   {
      if(upper_wick_pct >= InpFluentWickMin &&
         body_pct <= InpFluentBodyMax &&
         lower_wick_pct <= InpFluentLowerWickMax)
      {
         is_fluent = true;
      }
   }

   // Check pattern match
   bool pattern_match = is_classic || is_fluent;
   if(!pattern_match) return(false);

   // Apply configurable EMA Filter (default 15 EMA)
   if(InpUseFilterEMA)
   {
      // High must be above EMA, Close below it. Red signal candle, Green previous.
      if(h <= filter_ema || c >= filter_ema) return(false);
      if(c >= o || prev_c <= prev_o) return(false);
   }

   // Context conditions: (Below Regime EMA) OR (At Day High)
   bool is_below_regime_ema = (c < regime_ema);

   double day_high = h;
   MqlRates daily_rates[];
   int copied = CopyRates(_Symbol, PERIOD_D1, 0, 1, daily_rates);
   if(copied > 0)
   {
      day_high = daily_rates[0].high;
   }
   bool is_at_day_high = (h >= day_high - 0.01);

   is_valid_context = is_below_regime_ema || is_at_day_high;

   return(is_valid_context);
}

//+------------------------------------------------------------------+
//| Signal evaluation on candle close                                |
//+------------------------------------------------------------------+
void EvaluateSignals()
{
   if(InpOnePositionAtTime && HasOpenPosition())
   {
      Print("🚫 Skipping signal evaluation - Position already open.");
      return;
   }

   // Get candles: shift 1 (signal candle) and shift 2 (previous candle)
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(_Symbol, InpTimeframe, 1, 2, rates);
   if(copied < 2)
   {
      Print("⚠️ Failed to copy rates for signal evaluation.");
      return;
   }

   // Copy EMA values
   double regime_ema_values[1];
   double filter_ema_values[1];
   if(CopyBuffer(m_handle_regime_ema, 0, 1, 1, regime_ema_values) < 1 ||
      CopyBuffer(m_handle_filter_ema, 0, 1, 1, filter_ema_values) < 1)
   {
      Print("⚠️ Failed to copy EMA values.");
      return;
   }

   bool is_valid_context = false;
   if(CheckCandleGeometry(rates, regime_ema_values[0], filter_ema_values[0], is_valid_context))
   {
      // Initialize breakout trigger window
      m_trigger_active = true;
      m_trigger_low = rates[0].low;
      m_trigger_high = rates[0].high;
      m_trigger_time = rates[0].time;
      m_trigger_start_time = rates[0].time + PeriodSeconds(InpTimeframe);
      m_trigger_expiration = rates[0].time + 2 * PeriodSeconds(InpTimeframe);

      double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
      m_trigger_threshold = m_trigger_low - (InpEntryBufferPoints * point);

      // Initialize last bid to prevent immediate breakout on old ticks
      m_last_bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

      PrintFormat("🎯 SIGNAL VALID: Watch NEXT LOW Breakthrough < %.2f | SL Price Target: %.2f",
                  m_trigger_threshold, m_trigger_high);

      // Log geometry percentages
      double total_range = m_trigger_high - m_trigger_low;
      if(total_range > 0)
      {
         double o = rates[0].open;
         double h = rates[0].high;
         double l = rates[0].low;
         double c = rates[0].close;
         double upper_wick = h - MathMax(o, c);
         double body       = MathAbs(o - c);
         double lower_wick = MathMin(o, c) - l;

         double upper_pct = (upper_wick / total_range) * 100.0;
         double body_pct       = (body / total_range) * 100.0;
         double lower_pct = (lower_wick / total_range) * 100.0;
         PrintFormat("📊 Candle Geometry: U=%.1f%%, B=%.1f%%, L=%.1f%%", upper_pct, body_pct, lower_pct);
      }
   }
}

//+------------------------------------------------------------------+
//| Execute Short Entry (Market Order with SL/TP Bracket)            |
//+------------------------------------------------------------------+
void ExecuteShortEntry()
{
   if(InpOnePositionAtTime && HasOpenPosition())
   {
      Print("🚫 Skipping trade entry - position already open.");
      return;
   }

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double entry_price = bid;

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double sl_price = m_trigger_high + (InpSLBufferPoints * point);
   double risk = sl_price - entry_price;

   if(risk <= 0)
   {
      Print("✋ Invalid Risk calculation <= 0. Skipping trade.");
      return;
   }

   double tp_price = entry_price - (InpRiskRewardRatio * risk);

   // Volume steps and lot sizing constraints
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double min_vol = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_vol = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lot = MathRound(InpFixedLotSize / step) * step;

   if(lot < min_vol) lot = min_vol;
   if(lot > max_vol) lot = max_vol;

   // Execute short entry trade
   if(m_trade.Sell(lot, _Symbol, bid, sl_price, tp_price, "RedShootingStar"))
   {
      PrintFormat("✅ SHORT ORDER SENT: Price=%.2f, SL=%.2f, TGT=%.2f, Lot=%.2f", entry_price, sl_price, tp_price, lot);
   }
   else
   {
      PrintFormat("❌ Failed to place SHORT order: %s", m_trade.ResultComment());
   }
}

//+------------------------------------------------------------------+
//| OnTick tick-by-tick event handler                               |
//+------------------------------------------------------------------+
void OnTick()
{
   datetime now_time = TimeCurrent();

   // Handle optional session control force exits
   if(InpUseSessionControl)
   {
      MqlDateTime current_dt;
      TimeToStruct(now_time, current_dt);

      // Force exit check
      if(current_dt.hour > InpForceExitHour ||
         (current_dt.hour == InpForceExitHour && current_dt.min >= InpForceExitMinute))
      {
         if(HasOpenPosition())
         {
            Print("⏰ Force exit time reached. Closing open positions...");
            CloseAllPositions();
         }
         m_trigger_active = false;
         return;
      }

      // Stop evaluating new trades if past cutoff
      if(current_dt.hour > InpEntryCutoffHour ||
         (current_dt.hour == InpEntryCutoffHour && current_dt.min >= InpEntryCutoffMinute))
      {
         m_trigger_active = false;
      }
   }

   // Monitor bar close state transition
   datetime current_bar_time = iTime(_Symbol, InpTimeframe, 0);
   static datetime last_processed_bar_time = 0;

   if(current_bar_time != last_processed_bar_time)
   {
      if(last_processed_bar_time != 0)
      {
         Print("Candle Closed... Evaluating Signals");
         EvaluateSignals();
      }
      last_processed_bar_time = current_bar_time;
   }

   // Monitor next-candle breakout trigger window
   if(m_trigger_active)
   {
      if(now_time >= m_trigger_expiration)
      {
         m_trigger_active = false;
         Print("⌛ Breakout window expired without breakout. Trigger deactivated.");
      }
      else if(now_time >= m_trigger_start_time)
      {
         double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

         // Strict cross check (Last Bid >= Threshold and Current Bid < Threshold)
         if(m_last_bid >= m_trigger_threshold && bid < m_trigger_threshold)
         {
            PrintFormat("🔥 BREAKOUT TRIGGERED: Bid %.2f < Threshold %.2f. Placing trade...", bid, m_trigger_threshold);
            ExecuteShortEntry();
            m_trigger_active = false;
         }
         m_last_bid = bid;
      }
   }
}
