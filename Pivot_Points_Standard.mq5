//+------------------------------------------------------------------+
//|                                        Pivot_Points_Standard.mq5 |
//|                                  Copyright 2025, EA Developer    |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Pivot Points Standard"
#property version   "1.00"
#property indicator_chart_window
#property indicator_buffers 11
#property indicator_plots   11

#property indicator_label1  "P"
#property indicator_type1   DRAW_LINE
#property indicator_color1  clrDarkOrange
#property indicator_style1  STYLE_SOLID
#property indicator_width1  1

#property indicator_label2  "R1"
#property indicator_type2   DRAW_LINE
#property indicator_color2  clrRed
#property indicator_style2  STYLE_SOLID
#property indicator_width2  1

#property indicator_label3  "S1"
#property indicator_type3   DRAW_LINE
#property indicator_color3  clrGreen
#property indicator_style3  STYLE_SOLID
#property indicator_width3  1

#property indicator_label4  "R2"
#property indicator_type4   DRAW_LINE
#property indicator_color4  clrRed
#property indicator_style4  STYLE_SOLID
#property indicator_width4  1

#property indicator_label5  "S2"
#property indicator_type5   DRAW_LINE
#property indicator_color5  clrGreen
#property indicator_style5  STYLE_SOLID
#property indicator_width5  1

#property indicator_label6  "R3"
#property indicator_type6   DRAW_LINE
#property indicator_color6  clrRed
#property indicator_style6  STYLE_SOLID
#property indicator_width6  1

#property indicator_label7  "S3"
#property indicator_type7   DRAW_LINE
#property indicator_color7  clrGreen
#property indicator_style7  STYLE_SOLID
#property indicator_width7  1

#property indicator_label8  "R4"
#property indicator_type8   DRAW_LINE
#property indicator_color8  clrRed
#property indicator_style8  STYLE_SOLID
#property indicator_width8  1

#property indicator_label9  "S4"
#property indicator_type9   DRAW_LINE
#property indicator_color9  clrGreen
#property indicator_style9  STYLE_SOLID
#property indicator_width9  1

#property indicator_label10 "R5"
#property indicator_type10  DRAW_LINE
#property indicator_color10 clrRed
#property indicator_style10 STYLE_SOLID
#property indicator_width10 1

#property indicator_label11 "S5"
#property indicator_type11  DRAW_LINE
#property indicator_color11 clrGreen
#property indicator_style11 STYLE_SOLID
#property indicator_width11 1

enum ENUM_PIVOT_TYPE
  {
   PIVOT_TRADITIONAL = 0, // Traditional
   PIVOT_FIBONACCI   = 1, // Fibonacci
   PIVOT_WOODIE      = 2, // Woodie
   PIVOT_CLASSIC     = 3, // Classic
   PIVOT_DEMARK      = 4, // DM (DeMark)
   PIVOT_CAMARILLA   = 5  // Camarilla
  };

enum ENUM_PIVOT_TIMEFRAME
  {
   PIVOT_TF_AUTO    = 0, // Auto
   PIVOT_TF_DAILY   = 1, // Daily
   PIVOT_TF_WEEKLY  = 2, // Weekly
   PIVOT_TF_MONTHLY = 3, // Monthly
   PIVOT_TF_YEARLY  = 4  // Yearly
  };

//--- Input Parameters
input group "=== Pivot Settings ===";
input ENUM_PIVOT_TYPE      InpPivotType      = PIVOT_TRADITIONAL; // Pivot Type
input ENUM_PIVOT_TIMEFRAME InpPivotTimeframe = PIVOT_TF_AUTO;       // Pivot Timeframe
input bool                 InpShowPlot       = true;              // Show Pivot Lines on Chart (ON/OFF)

//--- Buffers
double BufferP[];
double BufferR1[];
double BufferS1[];
double BufferR2[];
double BufferS2[];
double BufferR3[];
double BufferS3[];
double BufferR4[];
double BufferS4[];
double BufferR5[];
double BufferS5[];

string m_obj_prefix = "Pivot_Tag_";

//+------------------------------------------------------------------+
//| Helper to determine anchor timeframe                             |
//+------------------------------------------------------------------+
ENUM_TIMEFRAMES GetAnchorTimeframe(ENUM_TIMEFRAMES chart_tf)
  {
   if(InpPivotTimeframe == PIVOT_TF_DAILY)   return PERIOD_D1;
   if(InpPivotTimeframe == PIVOT_TF_WEEKLY)  return PERIOD_W1;
   if(InpPivotTimeframe == PIVOT_TF_MONTHLY) return PERIOD_MN1;
   if(InpPivotTimeframe == PIVOT_TF_YEARLY)  return PERIOD_MN1; // Approximation or Yearly

   // Auto detection
   if(chart_tf <= PERIOD_M15)  return PERIOD_D1;
   if(chart_tf <= PERIOD_H4)   return PERIOD_W1;
   if(chart_tf <= PERIOD_D1)   return PERIOD_MN1;
   return PERIOD_MN1;
  }

//+------------------------------------------------------------------+
//| Custom indicator initialization function                         |
//+------------------------------------------------------------------+
int OnInit()
  {
   SetIndexBuffer(0,  BufferP,  INDICATOR_DATA);
   SetIndexBuffer(1,  BufferR1, INDICATOR_DATA);
   SetIndexBuffer(2,  BufferS1, INDICATOR_DATA);
   SetIndexBuffer(3,  BufferR2, INDICATOR_DATA);
   SetIndexBuffer(4,  BufferS2, INDICATOR_DATA);
   SetIndexBuffer(5,  BufferR3, INDICATOR_DATA);
   SetIndexBuffer(6,  BufferS3, INDICATOR_DATA);
   SetIndexBuffer(7,  BufferR4, INDICATOR_DATA);
   SetIndexBuffer(8,  BufferS4, INDICATOR_DATA);
   SetIndexBuffer(9,  BufferR5, INDICATOR_DATA);
   SetIndexBuffer(10, BufferS5, INDICATOR_DATA);

   ArraySetAsSeries(BufferP,  false);
   ArraySetAsSeries(BufferR1, false);
   ArraySetAsSeries(BufferS1, false);
   ArraySetAsSeries(BufferR2, false);
   ArraySetAsSeries(BufferS2, false);
   ArraySetAsSeries(BufferR3, false);
   ArraySetAsSeries(BufferS3, false);
   ArraySetAsSeries(BufferR4, false);
   ArraySetAsSeries(BufferS4, false);
   ArraySetAsSeries(BufferR5, false);
   ArraySetAsSeries(BufferS5, false);

   // Configure plot visibility
   for(int p = 0; p < 11; p++)
     {
      PlotIndexSetInteger(p, PLOT_DRAW_TYPE, InpShowPlot ? DRAW_LINE : DRAW_NONE);
     }

   IndicatorSetString(INDICATOR_SHORTNAME, "Pivot Points Standard (" + EnumToString(InpPivotType) + ")");
   IndicatorSetInteger(INDICATOR_DIGITS, _Digits);

   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Deinitialization function                                        |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   ObjectsDeleteAllPrefix(m_obj_prefix);
  }

//+------------------------------------------------------------------+
//| Remove text tags                                                 |
//+------------------------------------------------------------------+
void ObjectsDeleteAllPrefix(string prefix)
  {
   int total = ObjectsTotal(0, -1, -1);
   for(int i = total - 1; i >= 0; i--)
     {
      string name = ObjectName(0, i, -1, -1);
      if(StringFind(name, prefix) == 0)
         ObjectDelete(0, name);
     }
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
   if(rates_total < 2)
      return(0);

   ENUM_TIMEFRAMES anchor_tf = GetAnchorTimeframe(_Period);
   int start = (prev_calculated > 1) ? prev_calculated - 1 : 0;

   for(int i = start; i < rates_total; i++)
     {
      datetime bar_time = time[i];

      // Query preceding anchor period HTF OHLC (exact = false to match intraday timestamps to HTF bar)
      int htf_bar = iBarShift(_Symbol, anchor_tf, bar_time, false);
      int target_htf = (htf_bar >= 0) ? htf_bar + 1 : 1;

      double htf_high  = iHigh(_Symbol, anchor_tf, target_htf);
      double htf_low   = iLow(_Symbol, anchor_tf, target_htf);
      double htf_close = iClose(_Symbol, anchor_tf, target_htf);
      double htf_open  = iOpen(_Symbol, anchor_tf, target_htf);

      if(htf_high <= 0 || htf_low <= 0 || htf_close <= 0)
        {
         htf_high  = high[i];
         htf_low   = low[i];
         htf_close = close[i];
         htf_open  = open[i];
        }

      double P = 0, R1 = 0, S1 = 0, R2 = 0, S2 = 0, R3 = 0, S3 = 0, R4 = 0, S4 = 0, R5 = 0, S5 = 0;
      double range = htf_high - htf_low;

      switch(InpPivotType)
        {
         case PIVOT_FIBONACCI:
           {
            P  = (htf_high + htf_low + htf_close) / 3.0;
            R1 = P + 0.382 * range;
            S1 = P - 0.382 * range;
            R2 = P + 0.618 * range;
            S2 = P - 0.618 * range;
            R3 = P + 1.000 * range;
            S3 = P - 1.000 * range;
            R4 = R3; S4 = S3; R5 = R3; S5 = S3;
            break;
           }

         case PIVOT_WOODIE:
           {
            P  = (htf_high + htf_low + 2.0 * htf_open) / 4.0;
            R1 = 2.0 * P - htf_low;
            S1 = 2.0 * P - htf_high;
            R2 = P + range;
            S2 = P - range;
            R3 = htf_high + 2.0 * (P - htf_low);
            S3 = htf_low - 2.0 * (htf_high - P);
            R4 = R3 + range;
            S4 = S3 - range;
            R5 = R4; S5 = S4;
            break;
           }

         case PIVOT_CLASSIC:
           {
            P  = (htf_high + htf_low + htf_close) / 3.0;
            R1 = 2.0 * P - htf_low;
            S1 = 2.0 * P - htf_high;
            R2 = P + range;
            S2 = P - range;
            R3 = P + 2.0 * range;
            S3 = P - 2.0 * range;
            R4 = P + 3.0 * range;
            S4 = P - 3.0 * range;
            R5 = R4; S5 = S4;
            break;
           }

         case PIVOT_DEMARK:
           {
            double x = 0;
            if(htf_close < htf_open)      x = htf_high + 2.0 * htf_low + htf_close;
            else if(htf_close > htf_open) x = 2.0 * htf_high + htf_low + htf_close;
            else                          x = htf_high + htf_low + 2.0 * htf_close;

            P  = x / 4.0;
            R1 = x / 2.0 - htf_low;
            S1 = x / 2.0 - htf_high;
            R2 = P; S2 = P; R3 = P; S3 = P; R4 = P; S4 = P; R5 = P; S5 = P;
            break;
           }

         case PIVOT_CAMARILLA:
           {
            P  = (htf_high + htf_low + htf_close) / 3.0;
            R1 = htf_close + range * 1.1 / 12.0;
            S1 = htf_close - range * 1.1 / 12.0;
            R2 = htf_close + range * 1.1 / 6.0;
            S2 = htf_close - range * 1.1 / 6.0;
            R3 = htf_close + range * 1.1 / 4.0;
            S3 = htf_close - range * 1.1 / 4.0;
            R4 = htf_close + range * 1.1 / 2.0;
            S4 = htf_close - range * 1.1 / 2.0;
            R5 = (htf_low > 0) ? (htf_high / htf_low) * htf_close : htf_close;
            S5 = htf_close - (R5 - htf_close);
            break;
           }

         default: // TRADITIONAL
           {
            P  = (htf_high + htf_low + htf_close) / 3.0;
            R1 = 2.0 * P - htf_low;
            S1 = 2.0 * P - htf_high;
            R2 = P + range;
            S2 = P - range;
            R3 = P + 2.0 * range;
            S3 = P - 2.0 * range;
            R4 = P + 3.0 * range;
            S4 = P - 3.0 * range;
            R5 = P + 4.0 * range;
            S5 = P - 4.0 * range;
            break;
           }
        }

      BufferP[i]  = P;
      BufferR1[i] = R1;
      BufferS1[i] = S1;
      BufferR2[i] = R2;
      BufferS2[i] = S2;
      BufferR3[i] = R3;
      BufferS3[i] = S3;
      BufferR4[i] = R4;
      BufferS4[i] = S4;
      BufferR5[i] = R5;
      BufferS5[i] = S5;
     }

   return(rates_total);
  }
//+------------------------------------------------------------------+
