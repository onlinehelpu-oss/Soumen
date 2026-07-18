//+------------------------------------------------------------------+
//|                                FlexibleUpperWickRejectionEA.mq5 |
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
input double InpMinCandleRangePoints = 15.0;    // Minimum Candle Range (Points)
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
datetime       m_signal_time   = 0;

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

//====================================================================//
// Reusable function to analyze candle structure
// Returns true if copy operations are successful, false otherwise.
//====================================================================//
bool AnalyzeCandle(ENUM_TIMEFRAMES tf, int shift, double &body_pct, double &upper_wick_pct, double &lower_wick_pct, double &range_val)
{
   double O = GetOpen(tf, shift);
   double H = GetHigh(tf, shift);
   double L = GetLow(tf, shift);
   double C = GetClose(tf, shift);

   range_val = H - L;
   if(range_val <= 0.0)
   {
      body_pct = 0.0;
      upper_wick_pct = 0.0;
      lower_wick_pct = 0.0;
      return false;
   }

   double body       = MathAbs(O - C);
   double upper_wick  = H - MathMax(O, C);
   double lower_wick  = MathMin(O, C) - L;

   body_pct       = (body / range_val) * 100.0;
   upper_wick_pct = (upper_wick / range_val) * 100.0;
   lower_wick_pct = (lower_wick / range_val) * 100.0;

   return true;
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

//--- Scan and validate signal on shift 1
void CheckForSignal(ENUM_TIMEFRAMES tf)
{
   double body_pct = 0, upper_wick_pct = 0, lower_wick_pct = 0, range_val = 0;

   // Check if we can analyze the signal candle (shift 1)
   if(!AnalyzeCandle(tf, 1, body_pct, upper_wick_pct, lower_wick_pct, range_val))
   {
      m_signal_active = false;
      return;
   }

   // Ignore tiny candles
   double min_range = InpMinCandleRangePoints * _Point;
   if(range_val < min_range)
   {
      m_signal_active = false;
      return;
   }

   // Ignore Doji
   if(InpIgnoreDoji)
   {
      double o = GetOpen(tf, 1);
      double c = GetClose(tf, 1);
      if(MathAbs(o - c) <= 0.0 || body_pct <= 1.0)
      {
         m_signal_active = false;
         return;
      }
   }

   // Check if the signal candle matches our flexible upper wick rejection criteria
   bool is_rejection = (upper_wick_pct >= MinUpperWickPct &&
                        body_pct <= MaxBodyPct &&
                        lower_wick_pct <= MaxLowerWickPct);

   if(!is_rejection)
   {
      m_signal_active = false;
      return;
   }

   // Previous candle (shift 2) must be green
   double o2 = GetOpen(tf, 2);
   double c2 = GetClose(tf, 2);
   if(c2 <= o2)
   {
      m_signal_active = false;
      return;
   }

   // All conditions met! Activate signal for the current bar
   m_signal_active = true;
   m_signal_low = GetLow(tf, 1);
   m_signal_high = GetHigh(tf, 1);
   m_signal_time = GetTime(tf, 1);

   PrintFormat("[Signal Detected] Time: %s, Low: %.5f, High: %.5f, UpperWick: %.1f%%, Body: %.1f%%, LowerWick: %.1f%%",
               TimeToString(m_signal_time), m_signal_low, m_signal_high, upper_wick_pct, body_pct, lower_wick_pct);
}

//--- Detect if a new bar has opened
bool CheckNewBar(ENUM_TIMEFRAMES tf)
{
   datetime current_bar_time = GetTime(tf, 0);
   if(current_bar_time == 0)
      return false;
   if(current_bar_time != g_last_bar_time)
   {
      g_last_bar_time = current_bar_time;
      return true;
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

//--- OnInit
int OnInit()
{
   // Map Custom Timeframe input to standard ENUM_TIMEFRAMES
   m_tf = (ENUM_TIMEFRAMES)InpTimeframe;

   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_trade.SetDeviationInPoints(InpSlippage);

   // Initialize last bar time
   g_last_bar_time = GetTime(m_tf, 0);

   // Check for signal immediately on start to see if shift 1 was a signal and we are in active breakout monitoring
   CheckForSignal(m_tf);

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
   // Check if new bar has opened to scan for a new signal candle
   if(CheckNewBar(m_tf))
   {
      CheckForSignal(m_tf);
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
