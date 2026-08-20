//+------------------------------------------------------------------+
//|                                                        VWAP.mq5  |
//|              Volume Weighted Average Price - resets each period  |
//+------------------------------------------------------------------+
#property copyright "VWAP"
#property version   "1.00"
#property indicator_chart_window
#property indicator_buffers 3
#property indicator_plots   1
#property indicator_label1  "VWAP"
#property indicator_type1   DRAW_LINE
#property indicator_color1  clrDodgerBlue
#property indicator_style1  STYLE_SOLID
#property indicator_width1  2

// Reset the VWAP calculation at the start of each new period.
// PERIOD_D1 = classic intraday VWAP (resets every day) - most common
// PERIOD_W1 = resets every week, PERIOD_MN1 = resets every month
input ENUM_TIMEFRAMES ResetPeriod = PERIOD_D1;

double VWAPBuffer[];
double CumPV[];
double CumVol[];

string ValueTagName = "VWAP_ValueTag";

//+------------------------------------------------------------------+
int OnInit()
  {
   SetIndexBuffer(0, VWAPBuffer, INDICATOR_DATA);
   SetIndexBuffer(1, CumPV, INDICATOR_CALCULATIONS);
   SetIndexBuffer(2, CumVol, INDICATOR_CALCULATIONS);

   ArraySetAsSeries(VWAPBuffer, false);
   ArraySetAsSeries(CumPV, false);
   ArraySetAsSeries(CumVol, false);

   PlotIndexSetString(0, PLOT_LABEL, "VWAP");
   IndicatorSetString(INDICATOR_SHORTNAME, "VWAP (" + EnumToString(ResetPeriod) + ")");
   IndicatorSetInteger(INDICATOR_DIGITS, _Digits);

   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   ObjectDelete(0, ValueTagName);
  }

//+------------------------------------------------------------------+
// Draw / move a small text tag that follows the current VWAP price,
// positioned a few bars to the right of the last candle - the closest
// MT5 equivalent to a price-scale label for a custom indicator line.
//+------------------------------------------------------------------+
void UpdateValueTag(datetime lastBarTime, double val)
  {
   datetime tagTime = lastBarTime + PeriodSeconds() * 3;

   if(ObjectFind(0, ValueTagName) < 0)
     {
      ObjectCreate(0, ValueTagName, OBJ_TEXT, 0, tagTime, val);
      ObjectSetInteger(0, ValueTagName, OBJPROP_ANCHOR, ANCHOR_LEFT);
      ObjectSetInteger(0, ValueTagName, OBJPROP_FONTSIZE, 8);
      ObjectSetString(0, ValueTagName, OBJPROP_FONT, "Arial Bold");
      ObjectSetInteger(0, ValueTagName, OBJPROP_COLOR, clrDodgerBlue);
      ObjectSetInteger(0, ValueTagName, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, ValueTagName, OBJPROP_HIDDEN, true);
     }
   else
     {
      ObjectMove(0, ValueTagName, 0, tagTime, val);
     }

   ObjectSetString(0, ValueTagName, OBJPROP_TEXT, " VWAP " + DoubleToString(val, _Digits));
  }

//+------------------------------------------------------------------+
// Determine whether bar time t1 belongs to a different reset-period
// than bar time t2, using each bar's own timestamp only (no need to
// load history from another timeframe - avoids silent reset failure).
//+------------------------------------------------------------------+
bool IsNewPeriod(datetime t1, datetime t2, ENUM_TIMEFRAMES period)
  {
   MqlDateTime dt1, dt2;
   TimeToStruct(t1, dt1);
   TimeToStruct(t2, dt2);

   if(period == PERIOD_MN1)
      return(dt1.year != dt2.year || dt1.mon != dt2.mon);

   if(period == PERIOD_W1)
     {
      int off1 = (dt1.day_of_week == 0) ? 6 : dt1.day_of_week - 1; // days since Monday
      int off2 = (dt2.day_of_week == 0) ? 6 : dt2.day_of_week - 1;
      datetime weekStart1 = t1 - off1 * 86400 - (dt1.hour * 3600 + dt1.min * 60 + dt1.sec);
      datetime weekStart2 = t2 - off2 * 86400 - (dt2.hour * 3600 + dt2.min * 60 + dt2.sec);
      return(weekStart1 != weekStart2);
     }

   // default: daily reset
   return(dt1.year != dt2.year || dt1.mon != dt2.mon || dt1.day != dt2.day);
  }

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
   int start = (prev_calculated > 1) ? prev_calculated - 1 : 0;

   for(int i = start; i < rates_total; i++)
     {
      bool isNewPeriod = (i == 0) ? true : IsNewPeriod(time[i], time[i-1], ResetPeriod);

      double typicalPrice = (high[i] + low[i] + close[i]) / 3.0;
      double vol = (double)(volume[i] > 0 ? volume[i] : tick_volume[i]);

      if(isNewPeriod)
        {
         CumPV[i]  = typicalPrice * vol;
         CumVol[i] = vol;
        }
      else
        {
         CumPV[i]  = CumPV[i-1] + typicalPrice * vol;
         CumVol[i] = CumVol[i-1] + vol;
        }

      VWAPBuffer[i] = (CumVol[i] > 0) ? (CumPV[i] / CumVol[i]) : typicalPrice;
     }

   if(rates_total > 0)
      UpdateValueTag(time[rates_total-1], VWAPBuffer[rates_total-1]);

   return(rates_total);
  }
//+------------------------------------------------------------------+
