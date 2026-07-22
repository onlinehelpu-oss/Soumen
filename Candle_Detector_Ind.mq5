//+------------------------------------------------------------------+
//|                                         Candle_Detector_Ind.mq5  |
//|                                                            Jules  |
//|                                             https://github.com   |
//+------------------------------------------------------------------+
#property copyright "Jules"
#property link      "https://github.com"
#property version   "1.00"
#property description "Professional Custom Candlestick Detector for MT5."
#property description "Identifies 6 precise bearish candlestick rejection patterns (C2 to C7)."

#property indicator_chart_window
#property indicator_buffers 0
#property indicator_plots   0

//--- General Settings
input group "---- General Settings ----"
input int    InpMaxHistoryBars    = 1000;       // Max history bars to scan
input bool   InpShowDashboard     = true;       // Show real-time statistics dashboard
input bool   InpEnableAlerts      = true;       // Enable terminal pop-up alerts on candle close
input bool   InpSendNotifications = false;      // Send push notifications to mobile
input bool   InpSendMail          = false;      // Send email alerts
input int    InpFontSize          = 10;         // Label font size
input color  InpLabelColor        = clrOrange;  // Text label color

//--- Pattern 2 (C2) Parameters
// Classic shooting star with short body and minimal lower wick (c2.png)
input group "---- Pattern 2 (C2) Parameters ----"
input double InpC2_MinUpperWickPct = 55.0;      // Min Upper Wick % (of range)
input double InpC2_MaxBodyPct      = 35.0;      // Max Body % (of range)
input double InpC2_MaxLowerWickPct = 5.0;       // Max Lower Wick % (of range)

//--- Pattern 3 (C3) Parameters
// Bearish trend bar with short upper/lower wicks and a very large body (c3.png)
input group "---- Pattern 3 (C3) Parameters ----"
input double InpC3_MinUpperWickPct = 10.0;      // Min Upper Wick % (of range)
input double InpC3_MaxUpperWickPct = 35.0;      // Max Upper Wick % (of range)
input double InpC3_MinBodyPct      = 55.0;      // Min Body % (of range)
input double InpC3_MaxLowerWickPct = 15.0;      // Max Lower Wick % (of range)

//--- Pattern 4 (C4) Parameters
// Bearish pinbar with medium upper wick, medium body, and a clearly visible lower tail (c4.png)
input group "---- Pattern 4 (C4) Parameters ----"
input double InpC4_MinUpperWickPct = 35.0;      // Min Upper Wick % (of range)
input double InpC4_MaxUpperWickPct = 55.0;      // Max Upper Wick % (of range)
input double InpC4_MinBodyPct      = 30.0;      // Min Body % (of range)
input double InpC4_MaxBodyPct      = 50.0;      // Max Body % (of range)
input double InpC4_MinLowerWickPct = 10.0;      // Min Lower Wick % (of range)
input double InpC4_MaxLowerWickPct = 25.0;      // Max Lower Wick % (of range)

//--- Pattern 5 (C5) Parameters
// Bearish candle with long upper wick, long body, and short lower tail (c5.png)
input group "---- Pattern 5 (C5) Parameters ----"
input double InpC5_MinUpperWickPct = 35.0;      // Min Upper Wick % (of range)
input double InpC5_MaxUpperWickPct = 50.0;      // Max Upper Wick % (of range)
input double InpC5_MinBodyPct      = 45.0;      // Min Body % (of range)
input double InpC5_MaxBodyPct      = 60.0;      // Max Body % (of range)
input double InpC5_MaxLowerWickPct = 15.0;      // Max Lower Wick % (of range)

//--- Pattern 6 (C6) Parameters
// Rejection candle with long upper wick, medium-long body, and almost no lower wick (c6.png)
input group "---- Pattern 6 (C6) Parameters ----"
input double InpC6_MinUpperWickPct = 40.0;      // Min Upper Wick % (of range)
input double InpC6_MaxUpperWickPct = 60.0;      // Max Upper Wick % (of range)
input double InpC6_MinBodyPct      = 35.0;      // Min Body % (of range)
input double InpC6_MaxBodyPct      = 55.0;      // Max Body % (of range)
input double InpC6_MaxLowerWickPct = 5.0;       // Max Lower Wick % (of range)

//--- Pattern 7 (C7) Parameters
// Extreme pinbar with very small body (gravestone-like) and short lower wick (c7.png)
input group "---- Pattern 7 (C7) Parameters ----"
input double InpC7_MinUpperWickPct = 60.0;      // Min Upper Wick % (of range)
input double InpC7_MaxBodyPct      = 25.0;      // Max Body % (of range)
input double InpC7_MinLowerWickPct = 5.0;       // Min Lower Wick % (of range)
input double InpC7_MaxLowerWickPct = 20.0;      // Max Lower Wick % (of range)

//--- Global Variables
datetime m_last_alert_time = 0;
bool     m_is_first_run    = true;

//+------------------------------------------------------------------+
//| Custom Indicator Initialization Function                         |
//+------------------------------------------------------------------+
int OnInit()
{
   IndicatorSetString(INDICATOR_SHORTNAME, "Candle_Detector_Ind");
   m_last_alert_time = 0;
   m_is_first_run = true;
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Custom Indicator Deinitialization Function                       |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   // Delete all graphical elements created by this indicator
   ObjectsDeleteAll(0, "CandleDet_");
}

//+------------------------------------------------------------------+
//| Custom Indicator Iteration Function                              |
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
   // Handle insufficient bars
   if(rates_total < 50) return(0);

   // To prevent startup alert spam for pre-existing candles,
   // initialize m_last_alert_time to the last closed bar time on the first run.
   if(m_is_first_run)
   {
      m_last_alert_time = time[rates_total - 2];
      m_is_first_run = false;
   }

   // Determine the starting index for scanning
   int start_idx = 0;
   if(prev_calculated > 0)
   {
      start_idx = prev_calculated - 1;
   }
   else
   {
      // Limit search to InpMaxHistoryBars to prevent terminal lag
      start_idx = rates_total - InpMaxHistoryBars;
      if(start_idx < 0) start_idx = 0;

      // Clear old labels on first calculation
      ObjectsDeleteAll(0, "CandleDet_");
   }

   // Count of matches for the dashboard
   int c2_count = 0;
   int c3_count = 0;
   int c4_count = 0;
   int c5_count = 0;
   int c6_count = 0;
   int c7_count = 0;

   // First, count all historical matches for accurate stats
   for(int i = 0; i < rates_total - 1; i++)
   {
      if(i < rates_total - InpMaxHistoryBars) continue;

      string pat = IdentifyPattern(open[i], high[i], low[i], close[i]);
      if(pat == "C2") c2_count++;
      else if(pat == "C3") c3_count++;
      else if(pat == "C4") c4_count++;
      else if(pat == "C5") c5_count++;
      else if(pat == "C6") c6_count++;
      else if(pat == "C7") c7_count++;
   }

   // Draw/update objects for the calculated range
   for(int i = start_idx; i < rates_total - 1; i++)
   {
      string pattern = IdentifyPattern(open[i], high[i], low[i], close[i]);
      string obj_name = "CandleDet_Lbl_" + IntegerToString(time[i]);

      if(pattern != "")
      {
         // Calculate a dynamically scaling offset based on average range of surrounding bars
         double total_sum = 0;
         int count = 0;
         for(int k = i; k >= MathMax(0, i - 19); k--)
         {
            total_sum += (high[k] - low[k]);
            count++;
         }
         double avg_range = (count > 0) ? (total_sum / count) : 0.0;
         double offset = avg_range * 0.15; // 15% of average range for perfect visual spacing
         if(offset <= 0) offset = 10 * SymbolInfoDouble(_Symbol, SYMBOL_POINT);

         double label_price = high[i] + offset;

         // Draw the label
         if(ObjectFind(0, obj_name) < 0)
         {
            ObjectCreate(0, obj_name, OBJ_TEXT, 0, time[i], label_price);
            ObjectSetString(0, obj_name, OBJPROP_TEXT, pattern);
            ObjectSetInteger(0, obj_name, OBJPROP_COLOR, InpLabelColor);
            ObjectSetInteger(0, obj_name, OBJPROP_FONTSIZE, InpFontSize);
            ObjectSetString(0, obj_name, OBJPROP_FONT, "Trebuchet MS");
            ObjectSetInteger(0, obj_name, OBJPROP_ANCHOR, ANCHOR_BOTTOM);
            ObjectSetInteger(0, obj_name, OBJPROP_SELECTABLE, false);
            ObjectSetInteger(0, obj_name, OBJPROP_SELECTED, false);
            ObjectSetInteger(0, obj_name, OBJPROP_HIDDEN, true);
         }
         else
         {
            // Update coordinates in case chart was resized
            ObjectMove(0, obj_name, 0, time[i], label_price);
            ObjectSetString(0, obj_name, OBJPROP_TEXT, pattern);
         }
      }
      else
      {
         // If no pattern, make sure any old label is cleared
         ObjectDelete(0, obj_name);
      }
   }

   // --- Alert on newly closed bar ---
   int last_closed_bar = rates_total - 2;
   if(time[last_closed_bar] > m_last_alert_time)
   {
      string last_pattern = IdentifyPattern(open[last_closed_bar], high[last_closed_bar], low[last_closed_bar], close[last_closed_bar]);
      m_last_alert_time = time[last_closed_bar]; // Advance threshold immediately

      if(last_pattern != "")
      {
         TriggerAlert(last_pattern, time[last_closed_bar]);
      }
   }

   // Update real-time statistics dashboard
   UpdateDashboard(c2_count, c3_count, c4_count, c5_count, c6_count, c7_count);

   return(rates_total);
}

//+------------------------------------------------------------------+
//| Core Pattern Identifier Logic                                    |
//+------------------------------------------------------------------+
string IdentifyPattern(double o, double h, double l, double c)
{
   double range = h - l;
   if(range <= 0.0) return("");

   // All 6 targeted patterns are bearish (red) candles
   bool is_bearish = (c < o);
   if(!is_bearish) return("");

   double body = o - c;
   double upper_wick = h - o;
   double lower_wick = c - l;

   double body_pct = (body / range) * 100.0;
   double upper_wick_pct = (upper_wick / range) * 100.0;
   double lower_wick_pct = (lower_wick / range) * 100.0;

   //--- C2 Check: Classic Shooting Star with short body & almost no lower wick
   if(upper_wick_pct >= InpC2_MinUpperWickPct &&
      body_pct <= InpC2_MaxBodyPct &&
      lower_wick_pct <= InpC2_MaxLowerWickPct)
   {
      return("C2");
   }

   //--- C3 Check: Bearish Trend Bar (strong momentum, short wicks)
   if(upper_wick_pct >= InpC3_MinUpperWickPct && upper_wick_pct <= InpC3_MaxUpperWickPct &&
      body_pct >= InpC3_MinBodyPct &&
      lower_wick_pct <= InpC3_MaxLowerWickPct)
   {
      return("C3");
   }

   //--- C4 Check: Bearish Pinbar with visible tail
   if(upper_wick_pct >= InpC4_MinUpperWickPct && upper_wick_pct <= InpC4_MaxUpperWickPct &&
      body_pct >= InpC4_MinBodyPct && body_pct <= InpC4_MaxBodyPct &&
      lower_wick_pct >= InpC4_MinLowerWickPct && lower_wick_pct <= InpC4_MaxLowerWickPct)
   {
      return("C4");
   }

   //--- C5 Check: Bearish Strong Rejection
   if(upper_wick_pct >= InpC5_MinUpperWickPct && upper_wick_pct <= InpC5_MaxUpperWickPct &&
      body_pct >= InpC5_MinBodyPct && body_pct <= InpC5_MaxBodyPct &&
      lower_wick_pct <= InpC5_MaxLowerWickPct)
   {
      return("C5");
   }

   //--- C6 Check: Rejection with small/minimal lower wick
   if(upper_wick_pct >= InpC6_MinUpperWickPct && upper_wick_pct <= InpC6_MaxUpperWickPct &&
      body_pct >= InpC6_MinBodyPct && body_pct <= InpC6_MaxBodyPct &&
      lower_wick_pct <= InpC6_MaxLowerWickPct)
   {
      return("C6");
   }

   //--- C7 Check: Extreme Gravestone/Pinbar with very small body and small lower wick
   if(upper_wick_pct >= InpC7_MinUpperWickPct &&
      body_pct <= InpC7_MaxBodyPct &&
      lower_wick_pct >= InpC7_MinLowerWickPct && lower_wick_pct <= InpC7_MaxLowerWickPct)
   {
      return("C7");
   }

   return("");
}

//+------------------------------------------------------------------+
//| Optimized Dashboard Row Helper to Prevent Flickering             |
//+------------------------------------------------------------------+
void UpdateDashRow(string name, string text, int x, int y, color col)
{
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_SELECTED, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   }

   // Update properties dynamically without deleting/recreating to avoid screen flickering
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, col);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 10);
   ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
}

//+------------------------------------------------------------------+
//| Real-time Dashboard Renderer                                     |
//+------------------------------------------------------------------+
void UpdateDashboard(int c2, int c3, int c4, int c5, int c6, int c7)
{
   if(!InpShowDashboard)
   {
      ObjectsDeleteAll(0, "CandleDet_Dash_");
      return;
   }

   int x = 20;
   int y_start = 30;
   int y_step = 18;

   UpdateDashRow("CandleDet_Dash_Header", "=== BEARISH REJECTION CANDLE SCANNER ===", x, y_start, clrYellow);
   UpdateDashRow("CandleDet_Dash_C2",     "C2 (Shooting Star - Classic):     " + IntegerToString(c2), x, y_start + y_step, clrOrange);
   UpdateDashRow("CandleDet_Dash_C3",     "C3 (Strong Bearish Trend Bar):   " + IntegerToString(c3), x, y_start + 2*y_step, clrOrange);
   UpdateDashRow("CandleDet_Dash_C4",     "C4 (Bearish Pinbar w/ Tail):     " + IntegerToString(c4), x, y_start + 3*y_step, clrOrange);
   UpdateDashRow("CandleDet_Dash_C5",     "C5 (Strong Rejection Bearish):   " + IntegerToString(c5), x, y_start + 4*y_step, clrOrange);
   UpdateDashRow("CandleDet_Dash_C6",     "C6 (Rejection w/ Tiny Tail):     " + IntegerToString(c6), x, y_start + 5*y_step, clrOrange);
   UpdateDashRow("CandleDet_Dash_C7",     "C7 (Extreme Pinbar/Gravestone):  " + IntegerToString(c7), x, y_start + 6*y_step, clrOrange);
}

//+------------------------------------------------------------------+
//| Notification Handler                                             |
//+------------------------------------------------------------------+
void TriggerAlert(string pattern, datetime bar_time)
{
   string msg = "Candle Detector: Pattern " + pattern + " detected on " + _Symbol + " (" + EnumToString((ENUM_TIMEFRAME)_Period) + ") at " + TimeToString(bar_time, TIME_DATE|TIME_MINUTES);

   if(InpEnableAlerts)
   {
      Alert(msg);
   }
   if(InpSendNotifications)
   {
      SendNotification(msg);
   }
   if(InpSendMail)
   {
      SendMail("Candle Detector Notification", msg);
   }
   Print("[ALERT] " + msg);
}
//+------------------------------------------------------------------+
