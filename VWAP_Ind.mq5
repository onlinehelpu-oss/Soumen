//+------------------------------------------------------------------+
//|                                                     VWAP_Ind.mq5 |
//|                                                            Jules |
//|                                             https://www.mql5.com |
//|                                                                  |
//| Volume Weighted Average Price (VWAP) Indicator for MT5           |
//+------------------------------------------------------------------+
#property copyright "Jules"
#property link      "https://www.mql5.com"
#property version   "1.00"
#property indicator_chart_window
#property indicator_buffers 3
#property indicator_plots   1

//--- plot VWAP
#property indicator_label1  "VWAP"
#property indicator_type1   DRAW_LINE
#property indicator_color1  clrBlue
#property indicator_style1  STYLE_SOLID
#property indicator_width1  2

//--- enums
enum ENUM_ANCHOR_PERIOD
{
   ANCHOR_SESSION, // Session (Daily)
   ANCHOR_WEEK,    // Week
   ANCHOR_MONTH,   // Month
   ANCHOR_YEAR     // Year
};

//--- input parameters
input ENUM_ANCHOR_PERIOD InpAnchorPeriod = ANCHOR_SESSION; // Anchor Period
input ENUM_APPLIED_PRICE InpAppliedPrice = PRICE_TYPICAL;   // Applied Price (hlc3)

//--- indicator buffers
double VWAPBuffer[];
double CumPVBuffer[];
double CumVBuffer[];

//+------------------------------------------------------------------+
//| Custom indicator initialization function                         |
//+------------------------------------------------------------------+
int OnInit()
{
   //--- indicator buffers mapping
   SetIndexBuffer(0, VWAPBuffer, INDICATOR_DATA);
   SetIndexBuffer(1, CumPVBuffer, INDICATOR_CALCULATIONS);
   SetIndexBuffer(2, CumVBuffer, INDICATOR_CALCULATIONS);

   //--- name for DataWindow and chart tip
   string short_name = "VWAP(";
   switch(InpAnchorPeriod)
   {
      case ANCHOR_SESSION: short_name += "Session"; break;
      case ANCHOR_WEEK:    short_name += "Week"; break;
      case ANCHOR_MONTH:   short_name += "Month"; break;
      case ANCHOR_YEAR:    short_name += "Year"; break;
   }
   short_name += ")";
   IndicatorSetString(INDICATOR_SHORTNAME, short_name);

   //--- accuracy of display
   IndicatorSetInteger(INDICATOR_DIGITS, _Digits);

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Helper to detect new anchor period                               |
//+------------------------------------------------------------------+
bool IsNewPeriod(datetime time1, datetime time2, ENUM_ANCHOR_PERIOD anchor)
{
   MqlDateTime dt1, dt2;
   TimeToStruct(time1, dt1);
   TimeToStruct(time2, dt2);

   if(anchor == ANCHOR_SESSION)
   {
      return (dt1.day != dt2.day || dt1.mon != dt2.mon || dt1.year != dt2.year);
   }
   else if(anchor == ANCHOR_WEEK)
   {
      // Calculate start of week (Sunday is 0, Monday is 1, etc. in MqlDateTime)
      // We align to Monday as start of week.
      int day_offset1 = (dt1.day_of_week == 0) ? 6 : (dt1.day_of_week - 1);
      int day_offset2 = (dt2.day_of_week == 0) ? 6 : (dt2.day_of_week - 1);
      datetime week_start1 = time1 - day_offset1 * 86400 - (time1 % 86400);
      datetime week_start2 = time2 - day_offset2 * 86400 - (time2 % 86400);
      return (week_start1 != week_start2);
   }
   else if(anchor == ANCHOR_MONTH)
   {
      return (dt1.mon != dt2.mon || dt1.year != dt2.year);
   }
   else if(anchor == ANCHOR_YEAR)
   {
      return (dt1.year != dt2.year);
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
   if(rates_total < 1) return 0;

   int start = prev_calculated;
   if(start > 0) start--; // overlap the last calculated bar

   for(int i = start; i < rates_total; i++)
   {
      double price = 0;
      switch(InpAppliedPrice)
      {
         case PRICE_CLOSE:     price = close[i]; break;
         case PRICE_OPEN:      price = open[i]; break;
         case PRICE_HIGH:      price = high[i]; break;
         case PRICE_LOW:       price = low[i]; break;
         case PRICE_MEDIAN:    price = (high[i] + low[i]) / 2.0; break;
         case PRICE_TYPICAL:   price = (high[i] + low[i] + close[i]) / 3.0; break;
         case PRICE_WEIGHTED:  price = (high[i] + low[i] + 2.0 * close[i]) / 4.0; break;
         default:              price = close[i]; break;
      }

      double vol = (real_volume[i] > 0) ? (double)real_volume[i] : (double)tick_volume[i];
      if(vol <= 0) vol = 1.0;

      bool is_new = false;
      if(i == 0)
      {
         is_new = true;
      }
      else
      {
         is_new = IsNewPeriod(time[i], time[i-1], InpAnchorPeriod);
      }

      if(is_new)
      {
         CumPVBuffer[i] = price * vol;
         CumVBuffer[i] = vol;
      }
      else
      {
         CumPVBuffer[i] = CumPVBuffer[i-1] + price * vol;
         CumVBuffer[i] = CumVBuffer[i-1] + vol;
      }

      if(CumVBuffer[i] > 0)
      {
         VWAPBuffer[i] = CumPVBuffer[i] / CumVBuffer[i];
      }
      else
      {
         VWAPBuffer[i] = price;
      }
   }

   return rates_total;
}
//+------------------------------------------------------------------+
