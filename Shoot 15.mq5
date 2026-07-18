//+------------------------------------------------------------------+
//|                                                  Shoot 15.mq5    |
//|                                                  Copyright 2025 |
//|                                                                  |
//+------------------------------------------------------------------+
#property copyright "Copyright 2025"
#property link      ""
#property version   "1.00"

#include <Trade\Trade.mqh>

//--- Custom Enums for Selectable Timeframe
enum ENUM_CUSTOM_TF {
   TF_M1  = PERIOD_M1,  // M1
   TF_M3  = PERIOD_M3,  // M3
   TF_M5  = PERIOD_M5,  // M5
   TF_M15 = PERIOD_M15, // M15
   TF_M30 = PERIOD_M30, // M30
   TF_H1  = PERIOD_H1,  // H1
   TF_H4  = PERIOD_H4,  // H4
   TF_D1  = PERIOD_D1   // D1
};

//--- EA Inputs
input group "--- SIGNAL SETTINGS ---"
input ENUM_CUSTOM_TF InpTimeframe    = TF_M15;  // Signal Timeframe
input double MinUpperWickPct         = 50.0;    // Minimum Upper Wick (% of total range)
input double MaxBodyPct              = 50.0;    // Maximum Body (% of total range)
input double MaxLowerWickPct         = 20.0;    // Maximum Lower Wick (% of total range)
input double InpMinCandleRangePoints = 0.0;     // Minimum Candle Range in Points (0.0 to disable)
input bool   InpIgnoreDoji           = true;    // Ignore Doji (Body == 0)

input group "--- RISK & TRADE MANAGEMENT ---"
input double InpRiskRewardRatio      = 1.5;     // Risk to Reward Ratio (e.g. 1.5 for 1:1.5)
input double InpEntryBufferPoints    = 0.0;     // Entry Buffer (Points below signal low)
input double InpSLBufferPoints       = 0.0;     // SL Buffer (Points above signal high)
input double InpLotSize              = 0.1;     // Lot Size
input ulong  InpMagicNumber          = 123456;  // Magic Number
input int    InpSlippage             = 10;      // Slippage (Points)

//--- Global Variables
CTrade         m_trade;
ENUM_TIMEFRAMES m_tf;
datetime       g_last_bar_time = 0;

//--- Signal State Variables
bool           m_signal_active = false;
double         m_signal_low    = 0.0;
double         m_signal_high   = 0.0;
datetime       m_signal_time   = 0;  // Start time of the signal candle

//====================================================================//
// Reusable function to return all candle measurements (body %, upper wick %, lower wick %)
//====================================================================//
bool GetCandleMeasurements(ENUM_TIMEFRAMES tf, int shift, double &body_pct, double &upper_wick_pct, double &lower_wick_pct, double &range_val)
{
   MqlRates rates[1];
   if(CopyRates(_Symbol, tf, shift, 1, rates) < 1)
   {
      body_pct = 0.0;
      upper_wick_pct = 0.0;
      lower_wick_pct = 0.0;
      range_val = 0.0;
      return false;
   }

   range_val = rates[0].high - rates[0].low;
   if(range_val <= 0.0)
   {
      body_pct = 0.0;
      upper_wick_pct = 0.0;
      lower_wick_pct = 0.0;
      return false;
   }

   double body       = MathAbs(rates[0].open - rates[0].close);
   double upper_wick  = rates[0].high - MathMax(rates[0].open, rates[0].close);
   double lower_wick  = MathMin(rates[0].open, rates[0].close) - rates[0].low;

   body_pct       = (body / range_val) * 100.0;
   upper_wick_pct = (upper_wick / range_val) * 100.0;
   lower_wick_pct = (lower_wick / range_val) * 100.0;

   return true;
}

//--- Helper Functions to fetch price data safely
double GetOpen(ENUM_TIMEFRAMES tf, int shift)
{
   double arr[1];
   if(CopyOpen(_Symbol, tf, shift, 1, arr) > 0)
      return arr[0];
   return 0.0;
}

double GetHigh(ENUM_TIMEFRAMES tf, int shift)
{
   double arr[1];
   if(CopyHigh(_Symbol, tf, shift, 1, arr) > 0)
      return arr[0];
   return 0.0;
}

double GetLow(ENUM_TIMEFRAMES tf, int shift)
{
   double arr[1];
   if(CopyLow(_Symbol, tf, shift, 1, arr) > 0)
      return arr[0];
   return 0.0;
}

double GetClose(ENUM_TIMEFRAMES tf, int shift)
{
   double arr[1];
   if(CopyClose(_Symbol, tf, shift, 1, arr) > 0)
      return arr[0];
   return 0.0;
}

datetime GetTime(ENUM_TIMEFRAMES tf, int shift)
{
   datetime arr[1];
   if(CopyTime(_Symbol, tf, shift, 1, arr) > 0)
      return arr[0];
   return 0;
}

//--- Check if position already exists for this symbol and magic number
bool HasActivePosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(PositionGetSymbol(i) == _Symbol)
      {
         ulong magic = PositionGetInteger(POSITION_MAGIC);
         if(magic == InpMagicNumber)
            return true;
      }
   }
   return false;
}

//--- Update text comment on chart
void UpdateDashboard()
{
   string text = "========================================================\n";
   text += "   FLEXIBLE UPPER WICK REJECTION EXPERT ADVISOR\n";
   text += "========================================================\n";
   text += StringFormat(" Timeframe: %s\n", EnumToString(InpTimeframe));
   text += StringFormat(" Rejection Criteria: Min Upper Wick: %.1f%%, Max Body: %.1f%%, Max Lower Wick: %.1f%%\n", MinUpperWickPct, MaxBodyPct, MaxLowerWickPct);
   text += StringFormat(" Tiny Candle Min Range: %.1f points | Ignore Doji: %s\n", InpMinCandleRangePoints, InpIgnoreDoji ? "Yes" : "No");
   text += StringFormat(" Entry Buffer: %.1f points | SL Buffer: %.1f points\n", InpEntryBufferPoints, InpSLBufferPoints);
   text += StringFormat(" Risk to Reward Ratio: 1 : %.2f\n", InpRiskRewardRatio);
   text += "--------------------------------------------------------\n";

   if(m_signal_active)
   {
      text += " [SIGNAL ACTIVE] - Waiting for Breakout!\n";
      text += StringFormat("   Signal Candle Time: %s\n", TimeToString(m_signal_time));
      text += StringFormat("   Breakout Trigger Low: %.5f\n", m_signal_low - InpEntryBufferPoints * _Point);
      text += StringFormat("   Stop Loss High: %.5f\n", m_signal_high + InpSLBufferPoints * _Point);
   }
   else
   {
      text += " [STATUS] No active signal candle detected for breakout.\n";
   }

   bool has_pos = HasActivePosition();
   text += StringFormat(" Position Status: %s\n", has_pos ? "ACTIVE POSITION" : "Flat");

   if(has_pos)
   {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(PositionGetSymbol(i) == _Symbol && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         {
            text += StringFormat("   Price: %.5f | SL: %.5f | TP: %.5f | PnL: %.2f\n",
                                 PositionGetDouble(POSITION_PRICE_OPEN),
                                 PositionGetDouble(POSITION_SL),
                                 PositionGetDouble(POSITION_TP),
                                 PositionGetDouble(POSITION_PROFIT));
         }
      }
   }
   text += "========================================================\n";

   Comment(text);
}

//--- Detect and set the broker's correct filling mode
void SetBrokerFillingMode()
{
   uint filling = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0)
   {
      m_trade.SetTypeFilling(ORDER_FILLING_FOK);
      Print("Filling mode set to ORDER_FILLING_FOK (FOK).");
   }
   else if((filling & SYMBOL_FILLING_IOC) != 0)
   {
      m_trade.SetTypeFilling(ORDER_FILLING_IOC);
      Print("Filling mode set to ORDER_FILLING_IOC (IOC).");
   }
   else
   {
      m_trade.SetTypeFilling(ORDER_FILLING_RETURN);
      Print("Filling mode set to ORDER_FILLING_RETURN.");
   }
}

//--- OnInit
int OnInit()
{
   // Map Custom Timeframe input to standard ENUM_TIMEFRAMES
   m_tf = (ENUM_TIMEFRAMES)InpTimeframe;

   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_trade.SetDeviationInPoints(InpSlippage);

   // Set correct filling mode for the broker to prevent order rejection
   SetBrokerFillingMode();

   // Initialize last bar time to 0 to ensure copy rates run on first tick
   g_last_bar_time = 0;
   m_signal_active = false;

   UpdateDashboard();

   Print("EA Initialized successfully.");
   return(INIT_SUCCEEDED);
}

//--- OnDeinit
void OnDeinit(const int reason)
{
   Comment("");
   Print("EA Deinitialized. Reason code: ", reason);
}

//--- OnTick
void OnTick()
{
   // Copy rates for current bar (shift 0), signal candidate (shift 1), and confirmation bar (shift 2)
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(_Symbol, m_tf, 0, 3, rates) < 3)
   {
      // Wait for history to load/sync, do not execute signal logic on empty history
      return;
   }

   datetime current_bar_time = rates[0].time;
   bool is_new_bar = false;

   // First tick or transition to a new bar
   if(g_last_bar_time == 0)
   {
      g_last_bar_time = current_bar_time;
      is_new_bar = true; // Force analysis on startup to check immediate signals!
   }
   else if(current_bar_time != g_last_bar_time)
   {
      g_last_bar_time = current_bar_time;
      is_new_bar = true;
   }

   if(is_new_bar)
   {
      // A new bar has opened.
      // Since it is immediate next-candle breakout ONLY, any signal from the previous candle is now expired
      m_signal_active = false;

      // Analyze newly completed bar (rates[1], corresponding to shift 1) using the reusable function
      double body_pct = 0.0, upper_wick_pct = 0.0, lower_wick_pct = 0.0, range_val = 0.0;
      if(GetCandleMeasurements(m_tf, 1, body_pct, upper_wick_pct, lower_wick_pct, range_val))
      {
         double min_range = InpMinCandleRangePoints * _Point;

         // Previous candle (shift 2, rates[2]) must be green
         double o2 = rates[2].open;
         double c2 = rates[2].close;
         bool prev_green = (c2 > o2);

         // Print robust diagnostics for the backtester / journal so user sees exactly why each candle is accepted or rejected
         PrintFormat("[Diagnostic] Completed Candle: %s. Range: %.1f points (Min required: %.1f), UpperWick: %.1f%% (Min: %.1f%%), Body: %.1f%% (Max: %.1f%%), LowerWick: %.1f%% (Max: %.1f%%), Prev Green: %s",
                     TimeToString(rates[1].time), range_val / _Point, InpMinCandleRangePoints, upper_wick_pct, MinUpperWickPct, body_pct, MaxBodyPct, lower_wick_pct, MaxLowerWickPct, prev_green ? "Yes" : "No");

         if(range_val >= min_range)
         {
            bool is_doji = false;
            if(InpIgnoreDoji)
            {
               double body = MathAbs(rates[1].open - rates[1].close);
               if(body <= 0.0 || (body / range_val) * 100.0 <= 1.0)
                  is_doji = true;
            }

            if(!is_doji)
            {
               bool is_rejection = (upper_wick_pct >= MinUpperWickPct &&
                                    body_pct <= MaxBodyPct &&
                                    lower_wick_pct <= MaxLowerWickPct);

               if(is_rejection)
               {
                  if(prev_green)
                  {
                     m_signal_active = true;
                     m_signal_low    = rates[1].low;
                     m_signal_high   = rates[1].high;
                     m_signal_time   = rates[1].time;

                     PrintFormat(">>> [SIGNAL ACTIVE] Pattern Validated! Time: %s, Low: %.5f, High: %.5f. Waiting for immediate breakout on the current bar.",
                                 TimeToString(m_signal_time), m_signal_low, m_signal_high);
                  }
                  else
                  {
                     Print("[Diagnostic] -> Rejected: Previous candle (Shift 2) is not Green.");
                  }
               }
               else
               {
                  Print("[Diagnostic] -> Rejected: Candle geometry does not satisfy rejection thresholds.");
               }
            }
            else
            {
               Print("[Diagnostic] -> Rejected: Candle is ignored as Doji.");
            }
         }
         else
         {
            PrintFormat("[Diagnostic] -> Rejected: Candle range %.1f points is below minimum range threshold.", range_val / _Point);
         }
      }
   }

   // Strict Next-Candle Breakout Check:
   // The breakout must happen on the candle immediately following the signal candle.
   // This means the current bar time must be exactly m_signal_time + PeriodSeconds(m_tf).
   if(m_signal_active)
   {
      datetime next_candle_time = m_signal_time + PeriodSeconds(m_tf);
      if(current_bar_time > next_candle_time)
      {
         // Breakout didn't happen during the immediate next candle. Signal is expired.
         PrintFormat("[Signal Expired] Current bar time %s is past immediate next candle time %s. Inactivating signal.",
                     TimeToString(current_bar_time), TimeToString(next_candle_time));
         m_signal_active = false;
      }
   }

   // If there is an active signal candle and no active position, check for breakout
   if(m_signal_active && !HasActivePosition())
   {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double breakout_level = m_signal_low - InpEntryBufferPoints * _Point;

      if(bid <= breakout_level && bid > 0.0)
      {
         // Breakout occurred! Execute entry
         double sl_price = m_signal_high + InpSLBufferPoints * _Point;

         // Normalize prices to symbol digits
         sl_price = NormalizeDouble(sl_price, _Digits);
         double entry_price = NormalizeDouble(bid, _Digits);

         // SL must be strictly above entry price
         if(sl_price <= entry_price)
         {
            sl_price = entry_price + 10.0 * _Point;
            sl_price = NormalizeDouble(sl_price, _Digits);
         }

         double risk_points = sl_price - entry_price;
         double tp_price = entry_price - (risk_points * InpRiskRewardRatio);
         tp_price = NormalizeDouble(tp_price, _Digits);

         PrintFormat("[Executing Breakout Entry] Bid: %.5f <= Trigger Low: %.5f. Placing Sell Order. SL: %.5f, TP: %.5f",
                     bid, breakout_level, sl_price, tp_price);

         if(m_trade.Sell(InpLotSize, _Symbol, entry_price, sl_price, tp_price, "Flexible Rejection Breakout"))
         {
            Print("Sell order execution submitted successfully.");
            m_signal_active = false; // Reset signal after trigger
         }
         else
         {
            PrintFormat("Sell order execution failed. Error Code: %d, Description: %s",
                        m_trade.ResultRetcode(), m_trade.ResultComment());
         }
      }
   }

   UpdateDashboard();
}
