//+------------------------------------------------------------------+
//|                                    AllCandleDetectorIndicator.mq5|
//|                                  Copyright 2024, Trading Robot |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, Trading Robot"
#property link      "https://www.mql5.com"
#property version   "3.00"
#property description "MT5 Candle Detector Indicator for Patterns C2-C7 and General Rejections"
#property indicator_chart_window
#property indicator_buffers 0
#property indicator_plots   0
#property strict

//--- INPUT PARAMETERS
input group "=== Detection Settings ==="
input double          InpMinCandlePoints   = 1500.0;          // Ignore Tiny Candle: Min Candle Range in Points (1500 points = $15.00 for BTCUSD)
input bool            InpRequirePrevGreen  = true;            // Previous Candle must be GREEN
input bool            InpUseEMAFilter      = false;           // Use EMA Filter? (High above EMA, Close below EMA)
input int             InpEMAPeriod         = 21;              // EMA Period Close-basis
input ENUM_MA_METHOD  InpEMAMethod         = MODE_EMA;        // EMA MA Method

input group "=== Pattern Detection Toggles ==="
input bool            InpDetectC2          = true;            // Detect Pattern C2 (UW ~75%, Body ~24%, LW ~0.6%)
input bool            InpDetectC3          = true;            // Detect Pattern C3 (UW ~28%, Body ~67%, LW ~4%)
input bool            InpDetectC4          = true;            // Detect Pattern C4 (UW ~41%, Body ~45%, LW ~13%)
input bool            InpDetectC5          = true;            // Detect Pattern C5 (UW ~34%, Body ~60%, LW ~5%)
input bool            InpDetectC6          = true;            // Detect Pattern C6 (UW ~49%, Body ~46%, LW ~4.5%)
input bool            InpDetectC7          = true;            // Detect Pattern C7 (UW ~77%, Body ~14%, LW ~8%)
input bool            InpDetectGeneral     = true;            // Fallback: Detect General Rejection Shapes

input group "=== General Rejection Bounds ==="
input double          InpUpperWickMin      = 50.0;            // Rejection MUST be long upper wick: Min Upper Wick % (default >=50%)
input double          InpUpperWickMax      = 95.0;            // Upper Wick Max % of total range
input double          InpBodyMin           = 1.0;             // Body Min % of total range
input double          InpBodyMax           = 40.0;            // Body Max % of total range (default <=40% to keep body small)
input double          InpLowerWickMax      = 25.0;            // Lower Wick Max % of total range
input bool            InpUpperWickMustBeLongest = true;       // Upper wick must be strictly longer than body and lower wick

input group "=== Chart Visual Settings ==="
input bool            InpDrawArrows        = true;            // Draw visual arrow above signal candle
input bool            InpDrawLabels        = true;            // Draw text label with pattern name above arrow
input color           InpArrowColor        = clrRed;          // Arrow Color
input color           InpTextColor         = clrLightSalmon;  // Text Label Color

//--- GLOBALS
int      m_ema_handle = INVALID_HANDLE;
datetime m_last_bar_time = 0;

//+------------------------------------------------------------------+
//| Custom indicator initialization function                         |
//+------------------------------------------------------------------+
int OnInit()
{
   // Initialize EMA handle if filter is active
   if(InpUseEMAFilter)
   {
      m_ema_handle = iMA(_Symbol, PERIOD_CURRENT, InpEMAPeriod, 0, InpEMAMethod, PRICE_CLOSE);
      if(m_ema_handle == INVALID_HANDLE)
      {
         Print("[Init] Failed to create EMA handle!");
         return(INIT_FAILED);
      }
   }

   m_last_bar_time = 0;
   PrintFormat("[Init] All Candle Detector Indicator successfully loaded on %s.", _Symbol);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Custom indicator deinitialization function                       |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(m_ema_handle != INVALID_HANDLE)
   {
      IndicatorRelease(m_ema_handle);
   }
   Print("[Deinit] Indicator unloaded.");
}

//+------------------------------------------------------------------+
//| Get Specific Candle Pattern Name                                 |
//+------------------------------------------------------------------+
string GetCandlePatternName(double O, double H, double L, double C, double &uw_pct, double &body_pct, double &lw_pct)
{
   double range = H - L;
   if(range <= 0) return "None";

   double body       = MathAbs(O - C);
   double upperWick  = H - MathMax(O, C);
   double lowerWick  = MathMin(O, C) - L;

   uw_pct   = (upperWick / range) * 100.0;
   body_pct = (body      / range) * 100.0;
   lw_pct   = (lowerWick / range) * 100.0;

   // Rejection candle must be red (bearish close < open)
   if(C >= O) return "None";

   // Pattern C2
   if(InpDetectC2 &&
      uw_pct >= 70.0 && uw_pct <= 80.0 &&
      body_pct >= 18.0 && body_pct <= 30.0 &&
      lw_pct >= 0.0 && lw_pct <= 3.0)
   {
      return "C2";
   }

   // Pattern C3
   if(InpDetectC3 &&
      uw_pct >= 23.0 && uw_pct <= 33.0 &&
      body_pct >= 62.0 && body_pct <= 72.0 &&
      lw_pct >= 0.0 && lw_pct <= 7.0)
   {
      return "C3";
   }

   // Pattern C4 (Relaxed minimum lower wick to 0.0 to support small/zero lower wick)
   if(InpDetectC4 &&
      uw_pct >= 36.0 && uw_pct <= 46.0 &&
      body_pct >= 40.0 && body_pct <= 51.0 &&
      lw_pct >= 0.0 && lw_pct <= 18.0)
   {
      return "C4";
   }

   // Pattern C5 (Relaxed minimum lower wick to 0.0 to support small/zero lower wick)
   if(InpDetectC5 &&
      uw_pct >= 30.0 && uw_pct <= 39.0 &&
      body_pct >= 55.0 && body_pct <= 65.0 &&
      lw_pct >= 0.0 && lw_pct <= 9.0)
   {
      return "C5";
   }

   // Pattern C6 (Relaxed minimum lower wick to 0.0 to support small/zero lower wick)
   if(InpDetectC6 &&
      uw_pct >= 44.0 && uw_pct <= 54.0 &&
      body_pct >= 41.0 && body_pct <= 52.0 &&
      lw_pct >= 0.0 && lw_pct <= 8.0)
   {
      return "C6";
   }

   // Pattern C7 (Relaxed minimum lower wick to 0.0 to support small/zero lower wick)
   if(InpDetectC7 &&
      uw_pct >= 73.0 && uw_pct <= 83.0 &&
      body_pct >= 10.0 && body_pct <= 18.0 &&
      lw_pct >= 0.0 && lw_pct <= 12.0)
   {
      return "C7";
   }

   return "None";
}

//+------------------------------------------------------------------+
//| Custom indicator iteration function                              |
//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
{
   if(rates_total < 3) return 0;

   // Determine start index for recalculation
   int limit = rates_total - prev_calculated;
   if(prev_calculated > 0) limit++;

   // We process from oldest to newest bars
   for(int i = MathMin(limit, rates_total - 3); i >= 1; i--)
   {
      // Convert arrays to series-like indices (rates_total-1-i)
      int idx = rates_total - 1 - i;
      int idx_prev = idx - 1; // Previous candle (shift=2 in series notation)

      double o = open[idx];
      double h = high[idx];
      double l = low[idx];
      double c = close[idx];
      datetime t = time[idx];

      double range = h - l;
      if(range <= 0) continue;

      // Filter tiny candles by points
      if(InpMinCandlePoints > 0 && (range / _Point) < InpMinCandlePoints) continue;

      // Previous green candle rule
      if(InpRequirePrevGreen)
      {
         double prev_o = open[idx_prev];
         double prev_c = close[idx_prev];
         if(prev_c <= prev_o) continue;
       }

      // Calculate percentages and find pattern
      double uw_pct = 0, body_pct = 0, lw_pct = 0;
      string pattern = GetCandlePatternName(o, h, l, c, uw_pct, body_pct, lw_pct);

      bool isMatch = false;
      if(pattern != "None")
      {
         isMatch = true;
      }
      else if(InpDetectGeneral)
      {
         if(c < o &&
            uw_pct >= InpUpperWickMin && uw_pct <= InpUpperWickMax &&
            body_pct >= InpBodyMin && body_pct <= InpBodyMax &&
            lw_pct >= 0.0 && lw_pct <= InpLowerWickMax)
         {
            if(!InpUpperWickMustBeLongest || (uw_pct > body_pct && uw_pct > lw_pct))
            {
               pattern = "LongWickRejection";
               isMatch = true;
            }
         }
      }

      if(!isMatch) continue;

      // EMA Filter
      if(InpUseEMAFilter && m_ema_handle != INVALID_HANDLE)
      {
         double emaVal[1];
         // Need to copy using bar index (rates_total - 1 - idx) which is 'i' in our series notation
         if(CopyBuffer(m_ema_handle, 0, i, 1, emaVal) > 0)
         {
            if(!(h > emaVal[0] && c < emaVal[0])) continue;
         }
         else continue;
      }

      // Draw Chart Objects
      if(InpDrawArrows)
      {
         string arrowName = "IndCandleArrow_" + TimeToString(t);
         ObjectDelete(0, arrowName);
         if(ObjectCreate(0, arrowName, OBJ_ARROW_DOWN, 0, t, h))
         {
            ObjectSetInteger(0, arrowName, OBJPROP_COLOR, InpArrowColor);
            ObjectSetInteger(0, arrowName, OBJPROP_WIDTH, 2);
            ObjectSetInteger(0, arrowName, OBJPROP_ANCHOR, ANCHOR_BOTTOM);
         }
      }

      if(InpDrawLabels)
      {
         string labelName = "IndCandleLabel_" + TimeToString(t);
         ObjectDelete(0, labelName);
         double offset = range * 0.15;
         if(offset <= 0) offset = 10 * _Point;

         if(ObjectCreate(0, labelName, OBJ_TEXT, 0, t, h + offset))
         {
            ObjectSetString(0, labelName, OBJPROP_TEXT, pattern);
            ObjectSetInteger(0, labelName, OBJPROP_COLOR, InpTextColor);
            ObjectSetInteger(0, labelName, OBJPROP_FONTSIZE, 10);
            ObjectSetInteger(0, labelName, OBJPROP_ANCHOR, ANCHOR_BOTTOM);
         }
      }
   }

   return(rates_total);
}
