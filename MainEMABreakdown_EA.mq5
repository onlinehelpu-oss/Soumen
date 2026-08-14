//+------------------------------------------------------------------+
//|                                         MainEMABreakdown_EA.mq5  |
//|                                                            Jules |
//|                                                                  |
//| An MT5 Expert Advisor executing a Main EMA Breakdown Sell strategy |
//| strictly following user specifications and Williams Fractals.    |
//+------------------------------------------------------------------+
#property copyright "Jules"
#property link      ""
#property version   "1.10"

#include <Trade\Trade.mqh>

//--- Define Custom Timeframe Selection Enum
enum ENUM_CUSTOM_TIMEFRAME
{
   TF_1_MIN  = 1,     // 1 Minute
   TF_3_MIN  = 3,     // 3 Minutes
   TF_5_MIN  = 5,     // 5 Minutes
   TF_15_MIN = 15,    // 15 Minutes
   TF_30_MIN = 30,    // 30 Minutes
   TF_1_HOUR = 60,    // 1 Hour
   TF_1_DAY  = 1440   // 1 Day
};

//--- Input Parameters
input group "---- Timeframe & EMA Settings ----"
input ENUM_CUSTOM_TIMEFRAME InpTimeframe = TF_1_HOUR;        // Candle Timeframe
input int                  InpEMAPeriod = 34;                // Main EMA Period (e.g. 15, 21, 34, 50, 200)

input group "---- Execution Settings ----"
input double               InpLotSize = 0.01;                // Trade Lot Size
input int                  InpCoolDownSeconds = 300;         // Cooldown Period (Seconds) after trade close

input group "---- Take Profit: Risk-to-Reward ----"
input bool                 InpUseRRTarget = true;            // Risk-to-Reward basis: ON/OFF
input double               InpRiskRewardRatio = 2.0;         // Risk-to-Reward Ratio (e.g. 1.0, 2.0, etc.)

input group "---- Take Profit: Dollar Basis ----"
input bool                 InpUseDollarTarget = false;       // Profit in Dollar basis: ON/OFF
input double               InpDollarTarget = 2.0;            // Dollar basis profit target (e.g. 2.0, 5.0, etc.)

input group "---- Take Profit: Williams Fractal ----"
input bool                 InpUseFractalTarget = false;      // Williams Fractal basis: ON/OFF
input int                  InpFractalPeriod = 2;             // Fractal Period (must be >= 2)

//--- Global Variables
#define EA_MAGIC 823471
CTrade trade;

int      m_ema_handle = INVALID_HANDLE;
datetime m_last_candle_time = 0;

//--- Setup Tracking Variables
bool     m_setup_active = false;
double   m_signal_high = 0.0;
double   m_signal_low = 0.0;
datetime m_signal_candle_time = 0;

//+------------------------------------------------------------------+
//| Get Timeframe Enum Value                                         |
//+------------------------------------------------------------------+
ENUM_TIMEFRAMES GetTimeframe(ENUM_CUSTOM_TIMEFRAME tf)
{
   switch(tf)
   {
      case TF_1_MIN:   return PERIOD_M1;
      case TF_3_MIN:   return PERIOD_M3;
      case TF_5_MIN:   return PERIOD_M5;
      case TF_15_MIN:  return PERIOD_M15;
      case TF_30_MIN:  return PERIOD_M30;
      case TF_1_HOUR:  return PERIOD_H1;
      case TF_1_DAY:   return PERIOD_D1;
      default:         return PERIOD_CURRENT;
   }
}

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // Set Expert Magic Number
   trade.SetExpertMagicNumber(EA_MAGIC);

   // Initialize EMA Indicator
   m_ema_handle = iMA(_Symbol, GetTimeframe(InpTimeframe), InpEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
   if(m_ema_handle == INVALID_HANDLE)
   {
      Print("Error: Failed to create EMA indicator handle.");
      return INIT_FAILED;
   }

   // Enforce minimum Fractal Period of 2
   if(InpFractalPeriod < 2)
   {
      Print("Warning: Fractal Period must be at least 2. Overriding to 2.");
      // Note: input variables are read-only constants in MQL5, so we cannot modify InpFractalPeriod directly.
   }

   m_last_candle_time = 0;
   m_setup_active = false;

   PrintFormat("Main EMA Breakdown EA Initialized on Timeframe %d Min, EMA Period %d.", InpTimeframe, InpEMAPeriod);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(m_ema_handle != INVALID_HANDLE)
   {
      IndicatorRelease(m_ema_handle);
      m_ema_handle = INVALID_HANDLE;
   }

   // Clean up visual objects on chart
   ObjectDelete(0, "Fractal_Swing_Low");
   ObjectDelete(0, "Final_TP_Line");
   ChartRedraw();
}

//+------------------------------------------------------------------+
//| Normalize Price                                                  |
//+------------------------------------------------------------------+
double NormalizePrice(double price)
{
   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tick_size > 0)
   {
      return MathRound(price / tick_size) * tick_size;
   }
   return NormalizeDouble(price, _Digits);
}

//+------------------------------------------------------------------+
//| Normalize Lot Size                                               |
//+------------------------------------------------------------------+
double NormalizeLotSize(double lot)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double min_vol = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_vol = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   if(step > 0)
   {
      lot = MathRound(lot / step) * step;
   }
   if(lot < min_vol) lot = min_vol;
   if(lot > max_vol) lot = max_vol;
   return NormalizeDouble(lot, 2);
}

//+------------------------------------------------------------------+
//| Check if position is open for this Magic Number and Symbol      |
//+------------------------------------------------------------------+
bool IsPositionOpen()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == EA_MAGIC)
         {
            return true;
         }
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Get last close time from history for cooldown enforcement        |
//+------------------------------------------------------------------+
datetime GetLastCloseTime()
{
   datetime last_close = 0;
   if(HistorySelect(0, TimeCurrent()))
   {
      int total = HistoryDealsTotal();
      for(int i = total - 1; i >= 0; i--)
      {
         ulong ticket = HistoryDealGetTicket(i);
         if(ticket > 0)
         {
            long magic = HistoryDealGetInteger(ticket, DEAL_MAGIC);
            if(magic == EA_MAGIC)
            {
               long entry_type = HistoryDealGetInteger(ticket, DEAL_ENTRY);
               if(entry_type == DEAL_ENTRY_OUT || entry_type == DEAL_ENTRY_OUT_BY)
               {
                  datetime deal_time = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
                  if(deal_time > last_close)
                  {
                     last_close = deal_time;
                  }
               }
            }
         }
      }
   }
   return last_close;
}

//+------------------------------------------------------------------+
//| Resolve broker order filling mode                                |
//+------------------------------------------------------------------+
uint GetFillingMode()
{
   long filling = 0;
   if(SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE, filling))
   {
      if((filling & SYMBOL_FILLING_FOK) != 0) return ORDER_FILLING_FOK;
      if((filling & SYMBOL_FILLING_IOC) != 0) return ORDER_FILLING_IOC;
   }
   return ORDER_FILLING_RETURN;
}

//+------------------------------------------------------------------+
//| Check if a candle at center is a completed down/bearish fractal  |
//+------------------------------------------------------------------+
bool IsDownFractal(int center, int n, const double &low[])
{
   bool downflagDownFrontier = true;
   bool downflagUpFrontier0 = true;
   bool downflagUpFrontier1 = true;
   bool downflagUpFrontier2 = true;
   bool downflagUpFrontier3 = true;
   bool downflagUpFrontier4 = true;

   for(int i = 1; i <= n; i++)
   {
      downflagDownFrontier = downflagDownFrontier && (low[center - i] > low[center]);
      downflagUpFrontier0 = downflagUpFrontier0 && (low[center + i] > low[center]);
      downflagUpFrontier1 = downflagUpFrontier1 && (low[center + 1] >= low[center] && low[center + i + 1] > low[center]);
      downflagUpFrontier2 = downflagUpFrontier2 && (low[center + 1] >= low[center] && low[center + 2] >= low[center] && low[center + i + 2] > low[center]);
      downflagUpFrontier3 = downflagUpFrontier3 && (low[center + 1] >= low[center] && low[center + 2] >= low[center] && low[center + 3] >= low[center] && low[center + i + 3] > low[center]);
      downflagUpFrontier4 = downflagUpFrontier4 && (low[center + 1] >= low[center] && low[center + 2] >= low[center] && low[center + 3] >= low[center] && low[center + 4] >= low[center] && low[center + i + 4] > low[center]);
   }

   bool flagDownFrontier = downflagUpFrontier0 || downflagUpFrontier1 || downflagUpFrontier2 || downflagUpFrontier3 || downflagUpFrontier4;

   return (downflagDownFrontier && flagDownFrontier);
}

//+------------------------------------------------------------------+
//| Get most recent confirmed down Williams Fractal below entry      |
//+------------------------------------------------------------------+
double GetMostRecentDownFractalLow(int n, double entry_price)
{
   if(n < 2) n = 2;

   double low_values[];
   ArraySetAsSeries(low_values, true);

   // Copy plenty of bars to search back
   int request_bars = 1000;
   int copied = CopyLow(_Symbol, GetTimeframe(InpTimeframe), 0, request_bars, low_values);
   if(copied < n + 5)
   {
      Print("Warning: Not enough bars to compute Williams Fractals.");
      return 0.0;
   }

   // The fractal must have been confirmed before the signal candle (index 1).
   // A fractal is confirmed when its newer frontier bars are closed.
   // The newest frontier bar for center index is (center - n).
   // For it to be confirmed before the signal candle (index 1), the newest required bar
   // must be older than the signal candle (i.e. >= 2).
   // Therefore, center - n >= 2 => center >= n + 2.
   int start_idx = n + 2;
   int max_search_idx = copied - (n + 5);

   for(int center = start_idx; center <= max_search_idx; center++)
   {
      if(IsDownFractal(center, n, low_values))
      {
         double fractal_low = low_values[center];
         if(fractal_low < entry_price)
         {
            return fractal_low;
         }
      }
   }

   return 0.0;
}

//+------------------------------------------------------------------+
//| Calculate final broker-side Take Profit price                    |
//+------------------------------------------------------------------+
double CalculateBrokerTP(double entry_price, double sl)
{
   double rr_tp = 0.0;
   double fractal_tp = 0.0;

   if(InpUseRRTarget)
   {
      double risk_distance = sl - entry_price;
      if(risk_distance <= 0) risk_distance = _Point;
      rr_tp = entry_price - risk_distance * InpRiskRewardRatio;
   }

   if(InpUseFractalTarget)
   {
      fractal_tp = GetMostRecentDownFractalLow(InpFractalPeriod, entry_price);
      if(fractal_tp <= 0.0)
      {
         Print("Warning: No confirmed bearish Williams Fractal found below entry price.");
      }
      else
      {
         PrintFormat("Williams Fractal Target found: %.5f", fractal_tp);
         // Visual Plotting: Create a horizontal line at the Williams Fractal Swing Low level on-chart
         ObjectDelete(0, "Fractal_Swing_Low");
         if(ObjectCreate(0, "Fractal_Swing_Low", OBJ_HLINE, 0, 0, fractal_tp))
         {
            ObjectSetInteger(0, "Fractal_Swing_Low", OBJPROP_COLOR, clrTeal);
            ObjectSetInteger(0, "Fractal_Swing_Low", OBJPROP_STYLE, STYLE_DASH);
            ObjectSetInteger(0, "Fractal_Swing_Low", OBJPROP_WIDTH, 2);
            ObjectSetString(0, "Fractal_Swing_Low", OBJPROP_TEXT, "Williams Fractal TP Level");
            ChartRedraw();
            Print("Visual Plotting: Created 'Fractal_Swing_Low' line on-chart.");
         }
      }
   }

   double final_tp = 0.0;

   if(InpUseRRTarget && InpUseFractalTarget)
   {
      if(rr_tp > 0 && fractal_tp > 0)
      {
         // For Sell, the higher TP price is closer to the entry, so it is reached earlier
         final_tp = MathMax(rr_tp, fractal_tp);
         PrintFormat("Both TP options ON. R:R TP=%.5f, Fractal TP=%.5f. Selecting earlier target (MathMax) = %.5f", rr_tp, fractal_tp, final_tp);
      }
      else if(rr_tp > 0)
      {
         final_tp = rr_tp;
      }
      else if(fractal_tp > 0)
      {
         final_tp = fractal_tp;
      }
   }
   else if(InpUseRRTarget)
   {
      final_tp = rr_tp;
   }
   else if(InpUseFractalTarget)
   {
      final_tp = fractal_tp;
   }

   if(final_tp > 0)
   {
      double norm_tp = NormalizePrice(final_tp);
      // Visual Plotting: Create horizontal line for the Final TP on-chart
      ObjectDelete(0, "Final_TP_Line");
      if(ObjectCreate(0, "Final_TP_Line", OBJ_HLINE, 0, 0, norm_tp))
      {
         ObjectSetInteger(0, "Final_TP_Line", OBJPROP_COLOR, clrRed);
         ObjectSetInteger(0, "Final_TP_Line", OBJPROP_STYLE, STYLE_SOLID);
         ObjectSetInteger(0, "Final_TP_Line", OBJPROP_WIDTH, 1);
         ObjectSetString(0, "Final_TP_Line", OBJPROP_TEXT, "Take Profit Target");
         ChartRedraw();
      }
      return norm_tp;
   }

   return 0.0;
}

//+------------------------------------------------------------------+
//| Check for completed candle close & evaluate signal                |
//+------------------------------------------------------------------+
void OnNewCandle()
{
   MqlRates rates[];
   if(CopyRates(_Symbol, GetTimeframe(InpTimeframe), 1, 1, rates) < 1)
   {
      Print("Warning: Failed to copy completed candle rates.");
      return;
   }

   double ema_values[];
   if(CopyBuffer(m_ema_handle, 0, 1, 1, ema_values) < 1)
   {
      Print("Warning: Failed to copy EMA values.");
      return;
   }

   double open  = rates[0].open;
   double high  = rates[0].high;
   double low   = rates[0].low;
   double close = rates[0].close;
   double ema   = ema_values[0];

   // Signal Logic Check:
   // - Must be a Red candle (close < open)
   // - Candle crossed below and closed below EMA, OR touched the EMA and closed below EMA
   //   This is equivalent to: high >= ema && close < ema
   if(close < open && high >= ema && close < ema)
   {
      m_signal_high = high;
      m_signal_low = low;
      m_signal_candle_time = rates[0].time;
      m_setup_active = true;

      PrintFormat("Valid Signal Candle detected @ %s. High: %.5f, Low: %.5f, EMA: %.5f. Setup activated.",
                  TimeToString(rates[0].time, TIME_DATE|TIME_MINUTES), m_signal_high, m_signal_low, ema);
   }
   else
   {
      m_setup_active = false;
   }
}

//+------------------------------------------------------------------+
//| Execute Sell Entry                                               |
//+------------------------------------------------------------------+
void ExecuteSellEntry(double bid)
{
   // Turn off the setup immediately to prevent concurrent ticks triggering multiple trades
   m_setup_active = false;

   double sl = m_signal_high;
   double tp = CalculateBrokerTP(bid, sl);

   sl = NormalizePrice(sl);
   double lot = NormalizeLotSize(InpLotSize);

   trade.SetTypeFilling((ENUM_ORDER_TYPE_FILLING)GetFillingMode());

   PrintFormat("Executing Sell Breakout: Lot=%.2f, Bid=%.5f, SL=%.5f, TP=%.5f", lot, bid, sl, tp);

   if(trade.Sell(lot, _Symbol, bid, sl, tp, "EMA Breakdown Sell"))
   {
      Print("Sell order submitted successfully.");
   }
   else
   {
      Print("Error placing Sell order: ", trade.ResultRetcode(), " - ", trade.ResultComment());
      // Re-enable setup if trade failed and there is no open position
      if(!IsPositionOpen())
      {
         m_setup_active = true;
         Print("Setup re-enabled due to execution failure with no open position.");
      }
   }
}

//+------------------------------------------------------------------+
//| Monitor active position profit for Dollar target                 |
//+------------------------------------------------------------------+
void CheckDollarTarget()
{
   if(!InpUseDollarTarget) return;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == EA_MAGIC)
         {
            double profit = PositionGetDouble(POSITION_PROFIT);
            if(profit >= InpDollarTarget)
            {
               PrintFormat("Dollar profit target reached: Floating Profit=%.2f >= Target=%.2f. Closing position.", profit, InpDollarTarget);
               trade.PositionClose(ticket);
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| OnTick function                                                  |
//+------------------------------------------------------------------+
void OnTick()
{
   // Check for new candle completion
   datetime times[];
   if(CopyTime(_Symbol, GetTimeframe(InpTimeframe), 0, 1, times) >= 1)
   {
      if(times[0] != m_last_candle_time)
      {
         m_last_candle_time = times[0];
         OnNewCandle();
      }
   }

   // Monitor and enforce Dollar target if applicable
   CheckDollarTarget();

   // Cleanup visual objects if no positions are active
   if(!IsPositionOpen())
   {
      if(ObjectFind(0, "Fractal_Swing_Low") >= 0 || ObjectFind(0, "Final_TP_Line") >= 0)
      {
         ObjectDelete(0, "Fractal_Swing_Low");
         ObjectDelete(0, "Final_TP_Line");
         ChartRedraw();
      }
   }

   // Check for next immediate candle breakout entry
   if(m_setup_active && !IsPositionOpen())
   {
      datetime current_time[];
      if(CopyTime(_Symbol, GetTimeframe(InpTimeframe), 0, 1, current_time) >= 1)
      {
         datetime expected_current_time = m_signal_candle_time + PeriodSeconds(GetTimeframe(InpTimeframe));

         if(current_time[0] > expected_current_time)
         {
            // Setup expired: next immediate candle completed without breaking the low
            Print("Setup expired: The immediate next candle completed without breaking the signal candle's low.");
            m_setup_active = false;
            return;
         }
         else if(current_time[0] == expected_current_time)
         {
            // We are strictly on the next immediate candle
            double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
            if(bid < m_signal_low)
            {
               // Enforce Cooldown period
               if(TimeCurrent() - GetLastCloseTime() < InpCoolDownSeconds)
               {
                  static datetime last_cooldown_print = 0;
                  if(TimeCurrent() - last_cooldown_print > 10)
                  {
                     Print("Breakout detected, but entry blocked by Cooldown Period.");
                     last_cooldown_print = TimeCurrent();
                  }
                  return;
               }

               // All criteria met -> execute trade!
               ExecuteSellEntry(bid);
            }
         }
      }
   }
}
//+------------------------------------------------------------------+
