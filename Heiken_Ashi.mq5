//+------------------------------------------------------------------+
//|                                                  Heiken_Ashi.mq5 |
//|                             Copyright 2000-2026, MetaQuotes Ltd. |
//|                                                     www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2000-2026, MetaQuotes Ltd."
#property link      "https://www.mql5.com"
#property version   "1.00"
#property description "Heiken Ashi Candles Indicator converted from Pine Script v6."

//--- indicator settings
#property indicator_chart_window
#property indicator_buffers 5
#property indicator_plots   1
#property indicator_type1   DRAW_COLOR_CANDLES
#property indicator_color1  clrMediumSeaGreen, clrRed // Green and Red candles matching Pine Script plotcandle
#property indicator_label1  "Heiken Ashi Open;Heiken Ashi High;Heiken Ashi Low;Heiken Ashi Close"

//--- inputs
input bool InpIsHA = true; // Use HA Candles (true = Heiken Ashi, false = Standard)

//--- indicator buffers
double ExtOBuffer[];
double ExtHBuffer[];
double ExtLBuffer[];
double ExtCBuffer[];
double ExtColorBuffer[];

//+------------------------------------------------------------------+
//| Custom indicator initialization function                         |
//+------------------------------------------------------------------+
int OnInit()
  {
//--- indicator buffers mapping
   SetIndexBuffer(0, ExtOBuffer,     INDICATOR_DATA);
   SetIndexBuffer(1, ExtHBuffer,     INDICATOR_DATA);
   SetIndexBuffer(2, ExtLBuffer,     INDICATOR_DATA);
   SetIndexBuffer(3, ExtCBuffer,     INDICATOR_DATA);
   SetIndexBuffer(4, ExtColorBuffer, INDICATOR_COLOR_INDEX);

   ArraySetAsSeries(ExtOBuffer,     true);
   ArraySetAsSeries(ExtHBuffer,     true);
   ArraySetAsSeries(ExtLBuffer,     true);
   ArraySetAsSeries(ExtCBuffer,     true);
   ArraySetAsSeries(ExtColorBuffer, true);

//--- settings
   IndicatorSetInteger(INDICATOR_DIGITS, _Digits);
   IndicatorSetString(INDICATOR_SHORTNAME, "Heiken Ashi");
   PlotIndexSetDouble(0, PLOT_EMPTY_VALUE, 0.0);

//--- Hide standard chart line/candles so plotcandle draws cleanly
   ChartSetInteger(0, CHART_MODE, CHART_LINE);
   ChartSetInteger(0, CHART_COLOR_CHART_LINE, (long)clrNONE);

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
                const long &volume[],
                const int &spread[])
  {
   if(rates_total < 2)
      return(0);

   build_hashi(rates_total, prev_calculated, open, high, low, close);

   return(rates_total);
  }

//+------------------------------------------------------------------+
//| Build Heiken Ashi Buffers                                        |
//+------------------------------------------------------------------+
bool build_hashi(const int rates_total,
                 const int prev_calculated,
                 const double &open[],
                 const double &high[],
                 const double &low[],
                 const double &close[])
  {
   ArraySetAsSeries(open,  true);
   ArraySetAsSeries(high,  true);
   ArraySetAsSeries(low,   true);
   ArraySetAsSeries(close, true);

   int limit;
   if(prev_calculated == 0)
     {
      // First calculation
      if(!InpIsHA)
        {
         ExtOBuffer[rates_total - 1] = open[rates_total - 1];
         ExtHBuffer[rates_total - 1] = high[rates_total - 1];
         ExtLBuffer[rates_total - 1] = low[rates_total - 1];
         ExtCBuffer[rates_total - 1] = close[rates_total - 1];
        }
      else
        {
         ExtOBuffer[rates_total - 1] = (open[rates_total - 1] + close[rates_total - 1]) / 2.0;
         ExtCBuffer[rates_total - 1] = (open[rates_total - 1] + high[rates_total - 1] + low[rates_total - 1] + close[rates_total - 1]) / 4.0;
         ExtHBuffer[rates_total - 1] = MathMax(high[rates_total - 1], MathMax(ExtOBuffer[rates_total - 1], ExtCBuffer[rates_total - 1]));
         ExtLBuffer[rates_total - 1] = MathMin(low[rates_total - 1],  MathMin(ExtOBuffer[rates_total - 1], ExtCBuffer[rates_total - 1]));
        }
      ExtColorBuffer[rates_total - 1] = (ExtCBuffer[rates_total - 1] > ExtOBuffer[rates_total - 1]) ? 0.0 : 1.0;
      limit = rates_total - 1;
     }
   else
     {
      limit = (rates_total - prev_calculated) + 1;
     }

   // Main calculation loop
   for(int i = limit - 1; i >= 0 && !IsStopped(); i--)
     {
      if(!InpIsHA)
        {
         ExtOBuffer[i] = open[i];
         ExtHBuffer[i] = high[i];
         ExtLBuffer[i] = low[i];
         ExtCBuffer[i] = close[i];
        }
      else
        {
         double ha_open  = (ExtOBuffer[i + 1] + ExtCBuffer[i + 1]) / 2.0;
         double ha_close = (open[i] + high[i] + low[i] + close[i]) / 4.0;
         double ha_high  = MathMax(high[i], MathMax(ha_open, ha_close));
         double ha_low   = MathMin(low[i],  MathMin(ha_open, ha_close));

         ExtOBuffer[i] = ha_open;
         ExtHBuffer[i] = ha_high;
         ExtLBuffer[i] = ha_low;
         ExtCBuffer[i] = ha_close;
        }

      // Set candle color: 0.0 = Green (c > o), 1.0 = Red (c <= o)
      if(ExtCBuffer[i] > ExtOBuffer[i])
         ExtColorBuffer[i] = 0.0;
      else
         ExtColorBuffer[i] = 1.0;
     }

   return true;
  }

//+------------------------------------------------------------------+
//| Indicator deinitialization function                              |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   ChartSetInteger(0, CHART_COLOR_CHART_LINE, (long)clrGray);
   ChartSetInteger(0, CHART_MODE, CHART_CANDLES);
  }
//+------------------------------------------------------------------+
