//+------------------------------------------------------------------+
//|                                         Liquidity_Swings_Ind.mq5 |
//|                                                            Jules |
//|                                             https://www.mql5.com |
//|                                                                  |
//| Liquidity Swings Custom Indicator for MT5                         |
//+------------------------------------------------------------------+
#property copyright "Jules"
#property link      "https://www.mql5.com"
#property version   "1.00"
#property indicator_chart_window
#property indicator_buffers 2
#property indicator_plots   2

//--- plot Swing High
#property indicator_label1  "Swing High"
#property indicator_type1   DRAW_ARROW
#property indicator_color1  clrRed
#property indicator_width1  2

//--- plot Swing Low
#property indicator_label2  "Swing Low"
#property indicator_type2   DRAW_ARROW
#property indicator_color2  clrTeal
#property indicator_width2  2

//--- input parameters
input int InpPivotLookback = 14; // Pivot Lookback (length)

//--- indicator buffers
double SwingHighBuffer[];
double SwingLowBuffer[];

//+------------------------------------------------------------------+
//| Custom indicator initialization function                         |
//+------------------------------------------------------------------+
int OnInit()
{
   //--- indicator buffers mapping
   SetIndexBuffer(0, SwingHighBuffer, INDICATOR_DATA);
   SetIndexBuffer(1, SwingLowBuffer, INDICATOR_DATA);

   //--- setting arrow codes
   PlotIndexSetInteger(0, PLOT_ARROW, 159); // Dot or down arrow
   PlotIndexSetInteger(1, PLOT_ARROW, 159); // Dot or up arrow

   //--- empty values
   PlotIndexSetDouble(0, PLOT_EMPTY_VALUE, 0.0);
   PlotIndexSetDouble(1, PLOT_EMPTY_VALUE, 0.0);

   IndicatorSetString(INDICATOR_SHORTNAME, "Liquidity Swings (" + (string)InpPivotLookback + ")");
   IndicatorSetInteger(INDICATOR_DIGITS, _Digits);

   return(INIT_SUCCEEDED);
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
   if(rates_total < 2 * InpPivotLookback + 1) return 0;

   int start = prev_calculated;
   if(start > 0) start--; // overlap the last calculated bar
   if(start < InpPivotLookback) start = InpPivotLookback;

   int limit = rates_total - InpPivotLookback - 1;

   for(int i = start; i <= limit; i++)
   {
      double current_high = high[i];
      double current_low  = low[i];
      bool is_pivot_high  = true;
      bool is_pivot_low   = true;

      // Check left and right wings
      for(int j = 1; j <= InpPivotLookback; j++)
      {
         if(high[i - j] > current_high || high[i + j] > current_high)
         {
            is_pivot_high = false;
         }
         if(low[i - j] < current_low || low[i + j] < current_low)
         {
            is_pivot_low = false;
         }
      }

      if(is_pivot_high)
      {
         SwingHighBuffer[i] = current_high;
      }
      else
      {
         SwingHighBuffer[i] = 0.0;
      }

      if(is_pivot_low)
      {
         SwingLowBuffer[i] = current_low;
      }
      else
      {
         SwingLowBuffer[i] = 0.0;
      }
   }

   // Keep the last InpPivotLookback bars empty since their future is not yet confirmed
   for(int i = limit + 1; i < rates_total; i++)
   {
      SwingHighBuffer[i] = 0.0;
      SwingLowBuffer[i]  = 0.0;
   }

   return rates_total;
}
//+------------------------------------------------------------------+
