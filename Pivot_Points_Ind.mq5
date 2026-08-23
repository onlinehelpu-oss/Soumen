//+------------------------------------------------------------------+
//|                                             Pivot_Points_Ind.mq5 |
//|                                                            Jules |
//|                                             https://www.mql5.com |
//|                                                                  |
//| Pivot Points Standard Indicator for MT5                           |
//+------------------------------------------------------------------+
#property copyright "Jules"
#property link      "https://www.mql5.com"
#property version   "1.00"
#property indicator_chart_window
#property indicator_buffers 11
#property indicator_plots   11

//--- plot definitions
#property indicator_label1  "P"
#property indicator_type1   DRAW_LINE
#property indicator_color1  clrOrange
#property indicator_style1  STYLE_SOLID

#property indicator_label2  "R1"
#property indicator_type2   DRAW_LINE
#property indicator_color2  clrRed
#property indicator_style2  STYLE_DOT

#property indicator_label3  "S1"
#property indicator_type3   DRAW_LINE
#property indicator_color3  clrGreen
#property indicator_style3  STYLE_DOT

#property indicator_label4  "R2"
#property indicator_type4   DRAW_LINE
#property indicator_color4  clrRed
#property indicator_style4  STYLE_DOT

#property indicator_label5  "S2"
#property indicator_type5   DRAW_LINE
#property indicator_color5  clrGreen
#property indicator_style5  STYLE_DOT

#property indicator_label6  "R3"
#property indicator_type6   DRAW_LINE
#property indicator_color6  clrRed
#property indicator_style6  STYLE_DOT

#property indicator_label7  "S3"
#property indicator_type7   DRAW_LINE
#property indicator_color7  clrGreen
#property indicator_style7  STYLE_DOT

#property indicator_label8  "R4"
#property indicator_type8   DRAW_LINE
#property indicator_color8  clrRed
#property indicator_style8  STYLE_DOT

#property indicator_label9  "S4"
#property indicator_type9   DRAW_LINE
#property indicator_color9  clrGreen
#property indicator_style9  STYLE_DOT

#property indicator_label10 "R5"
#property indicator_type10  DRAW_LINE
#property indicator_color10 clrRed
#property indicator_style10 STYLE_DOT

#property indicator_label11 "S5"
#property indicator_type11  DRAW_LINE
#property indicator_color11 clrGreen
#property indicator_style11 STYLE_DOT

//--- enums
enum ENUM_PIVOT_TYPE
{
   PIVOT_TRADITIONAL, // Traditional
   PIVOT_FIBONACCI,   // Fibonacci
   PIVOT_WOODIE,      // Woodie
   PIVOT_CLASSIC,     // Classic
   PIVOT_DEMARK,      // DeMark
   PIVOT_CAMARILLA    // Camarilla
};

enum ENUM_PIVOT_TF
{
   PIVOT_TF_DAILY,    // Daily
   PIVOT_TF_WEEKLY,   // Weekly
   PIVOT_TF_MONTHLY   // Monthly
};

//--- inputs
input ENUM_PIVOT_TYPE InpPivotType = PIVOT_TRADITIONAL; // Pivot Type
input ENUM_PIVOT_TF   InpPivotTF   = PIVOT_TF_DAILY;    // Pivot Timeframe

//--- buffers
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

//+------------------------------------------------------------------+
//| Custom indicator initialization function                         |
//+------------------------------------------------------------------+
int OnInit()
{
   SetIndexBuffer(0, BufferP, INDICATOR_DATA);
   SetIndexBuffer(1, BufferR1, INDICATOR_DATA);
   SetIndexBuffer(2, BufferS1, INDICATOR_DATA);
   SetIndexBuffer(3, BufferR2, INDICATOR_DATA);
   SetIndexBuffer(4, BufferS2, INDICATOR_DATA);
   SetIndexBuffer(5, BufferR3, INDICATOR_DATA);
   SetIndexBuffer(6, BufferS3, INDICATOR_DATA);
   SetIndexBuffer(7, BufferR4, INDICATOR_DATA);
   SetIndexBuffer(8, BufferS4, INDICATOR_DATA);
   SetIndexBuffer(9, BufferR5, INDICATOR_DATA);
   SetIndexBuffer(10, BufferS5, INDICATOR_DATA);

   IndicatorSetString(INDICATOR_SHORTNAME, "Pivot Points Standard");
   IndicatorSetInteger(INDICATOR_DIGITS, _Digits);

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Helper to detect new anchor period                               |
//+------------------------------------------------------------------+
bool IsNewPeriod(datetime time1, datetime time2, ENUM_PIVOT_TF ptf)
{
   MqlDateTime dt1, dt2;
   TimeToStruct(time1, dt1);
   TimeToStruct(time2, dt2);

   if(ptf == PIVOT_TF_DAILY)
   {
      return (dt1.day != dt2.day || dt1.mon != dt2.mon || dt1.year != dt2.year);
   }
   else if(ptf == PIVOT_TF_WEEKLY)
   {
      int offset1 = (dt1.day_of_week == 0) ? 6 : (dt1.day_of_week - 1);
      int offset2 = (dt2.day_of_week == 0) ? 6 : (dt2.day_of_week - 1);
      datetime w1 = time1 - offset1 * 86400 - (time1 % 86400);
      datetime w2 = time2 - offset2 * 86400 - (time2 % 86400);
      return (w1 != w2);
   }
   else if(ptf == PIVOT_TF_MONTHLY)
   {
      return (dt1.mon != dt2.mon || dt1.year != dt2.year);
   }
   return false;
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
                const long &real_volume[],
                const int &spread[])
{
   if(rates_total < 2) return 0;

   int start = prev_calculated;
   if(start > 0) start--;

   ENUM_TIMEFRAMES htf = PERIOD_D1;
   if(InpPivotTF == PIVOT_TF_WEEKLY) htf = PERIOD_W1;
   else if(InpPivotTF == PIVOT_TF_MONTHLY) htf = PERIOD_MN1;

   for(int i = start; i < rates_total; i++)
   {
      datetime bar_time = time[i];

      // Request previous higher timeframe bar data
      MqlRates htf_rates[];
      int copied = CopyRates(_Symbol, htf, bar_time, 2, htf_rates);
      if(copied >= 2)
      {
         // htf_rates[0] is the previous completed HTF bar
         double prev_high = htf_rates[0].high;
         double prev_low  = htf_rates[0].low;
         double prev_close= htf_rates[0].close;
         double prev_open = htf_rates[0].open;
         double range     = prev_high - prev_low;

         double P = 0, R1 = 0, S1 = 0, R2 = 0, S2 = 0, R3 = 0, S3 = 0, R4 = 0, S4 = 0, R5 = 0, S5 = 0;

         switch(InpPivotType)
         {
            case PIVOT_TRADITIONAL:
               P  = (prev_high + prev_low + prev_close) / 3.0;
               R1 = 2.0 * P - prev_low;
               S1 = 2.0 * P - prev_high;
               R2 = P + range;
               S2 = P - range;
               R3 = P + 2.0 * range;
               S3 = P - 2.0 * range;
               R4 = P + 3.0 * range;
               S4 = P - 3.0 * range;
               R5 = P + 4.0 * range;
               S5 = P - 4.0 * range;
               break;

            case PIVOT_FIBONACCI:
               P  = (prev_high + prev_low + prev_close) / 3.0;
               R1 = P + 0.382 * range;
               S1 = P - 0.382 * range;
               R2 = P + 0.618 * range;
               S2 = P - 0.618 * range;
               R3 = P + 1.000 * range;
               S3 = P - 1.000 * range;
               break;

            case PIVOT_WOODIE:
               // Woodie uses current open or prev close
               P  = (prev_high + prev_low + 2.0 * prev_close) / 4.0;
               R1 = 2.0 * P - prev_low;
               S1 = 2.0 * P - prev_high;
               R2 = P + range;
               S2 = P - range;
               R3 = prev_high + 2.0 * (P - prev_low);
               S3 = prev_low - 2.0 * (prev_high - P);
               break;

            case PIVOT_CLASSIC:
               P  = (prev_high + prev_low + prev_close) / 3.0;
               R1 = 2.0 * P - prev_low;
               S1 = 2.0 * P - prev_high;
               R2 = P + (prev_high - prev_low);
               S2 = P - (prev_high - prev_low);
               R3 = P + 2.0 * (prev_high - prev_low);
               S3 = P - 2.0 * (prev_high - prev_low);
               R4 = P + 3.0 * (prev_high - prev_low);
               S4 = P - 3.0 * (prev_high - prev_low);
               break;

            case PIVOT_DEMARK:
               double x = 0;
               if(prev_close < prev_open) x = prev_high + 2.0 * prev_low + prev_close;
               else if(prev_close > prev_open) x = 2.0 * prev_high + prev_low + prev_close;
               else x = prev_high + prev_low + 2.0 * prev_close;
               P  = x / 4.0;
               R1 = x / 2.0 - prev_low;
               S1 = x / 2.0 - prev_high;
               break;

            case PIVOT_CAMARILLA:
               P  = (prev_high + prev_low + prev_close) / 3.0;
               R1 = prev_close + range * 1.1 / 12.0;
               S1 = prev_close - range * 1.1 / 12.0;
               R2 = prev_close + range * 1.1 / 6.0;
               S2 = prev_close - range * 1.1 / 6.0;
               R3 = prev_close + range * 1.1 / 4.0;
               S3 = prev_close - range * 1.1 / 4.0;
               R4 = prev_close + range * 1.1 / 2.0;
               S4 = prev_close - range * 1.1 / 2.0;
               R5 = (prev_high / prev_low) * prev_close;
               S5 = prev_close - (R5 - prev_close);
               break;
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
      else
      {
         BufferP[i]  = 0;
         BufferR1[i] = 0;
         BufferS1[i] = 0;
         BufferR2[i] = 0;
         BufferS2[i] = 0;
         BufferR3[i] = 0;
         BufferS3[i] = 0;
         BufferR4[i] = 0;
         BufferS4[i] = 0;
         BufferR5[i] = 0;
         BufferS5[i] = 0;
      }
   }

   return rates_total;
}
//+------------------------------------------------------------------+
