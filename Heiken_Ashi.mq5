//+------------------------------------------------------------------+
//|                                                  Heiken_Ashi.mq5 |
//|                             Copyright 2000-2026, MetaQuotes Ltd. |
//|                                                     www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2000-2026, MetaQuotes Ltd."
#property link      "https://www.mql5.com"
//--- indicator settings
#property indicator_chart_window
#property indicator_buffers 5
#property indicator_plots   1
#property indicator_type1   DRAW_COLOR_CANDLES
#property indicator_color1  clrDodgerBlue,clrRed
#property indicator_label1  "Heiken Ashi Open;Heiken Ashi High;Heiken Ashi Low;Heiken Ashi Close"

//--- indicator buffers
double ExtOBuffer[];
double ExtHBuffer[];
double ExtLBuffer[];
double ExtCBuffer[];
double ExtColorBuffer[];

//+------------------------------------------------------------------+
//| Custom indicator initialization function                         |
//+------------------------------------------------------------------+
void OnInit()
  {
//--- indicator buffers mapping
   SetIndexBuffer(0,ExtOBuffer,INDICATOR_DATA);
   SetIndexBuffer(1,ExtHBuffer,INDICATOR_DATA);
   SetIndexBuffer(2,ExtLBuffer,INDICATOR_DATA);
   SetIndexBuffer(3,ExtCBuffer,INDICATOR_DATA);
   SetIndexBuffer(4,ExtColorBuffer,INDICATOR_COLOR_INDEX);

   ArraySetAsSeries(ExtOBuffer, true);
   ArraySetAsSeries(ExtHBuffer, true);
   ArraySetAsSeries(ExtLBuffer, true);
   ArraySetAsSeries(ExtCBuffer, true);
   ArraySetAsSeries(ExtColorBuffer, true);

//---
   IndicatorSetInteger(INDICATOR_DIGITS,_Digits);
//--- sets short name
   IndicatorSetString(INDICATOR_SHORTNAME,"Heiken Ashi");
//--- sets drawing line empty value
   PlotIndexSetDouble(0,PLOT_EMPTY_VALUE,0.0);
   ChartSetInteger(0, CHART_MODE, CHART_LINE);
   ChartSetInteger(0, CHART_COLOR_CHART_LINE, (long)clrNONE);
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
   ArraySetAsSeries(open, true);
   ArraySetAsSeries(high, true);
   ArraySetAsSeries(low, true);
   ArraySetAsSeries(close, true);

   int limit;
   //--- preliminary calculations
   if(prev_calculated == 0)
     {
      // fill buffers with the data of the oldest index
      ExtLBuffer[rates_total-1]=low[rates_total-1];
      ExtHBuffer[rates_total-1]=high[rates_total-1];
      ExtOBuffer[rates_total-1]=open[rates_total-1];
      ExtCBuffer[rates_total-1]=close[rates_total-1];
      limit=rates_total-1;
     }
   else
      limit = (rates_total - prev_calculated) + 1;

   //--- the main loop of calculations
   for(int i=limit-1; i>=0 && !IsStopped(); i--)
     {
      double ha_open =(ExtOBuffer[i+1]+ExtCBuffer[i+1])/2;
      double ha_close=(open[i]+high[i]+low[i]+close[i])/4;
      double ha_high =MathMax(high[i],MathMax(ha_open,ha_close));
      double ha_low  =MathMin(low[i],MathMin(ha_open,ha_close));

      ExtLBuffer[i]=ha_low;
      ExtHBuffer[i]=ha_high;
      ExtOBuffer[i]=ha_open;
      ExtCBuffer[i]=ha_close;

      //--- set candle color
      if(ha_open<ha_close)
         ExtColorBuffer[i]=0.0; // Bullish (clrDodgerBlue)
      else
         ExtColorBuffer[i]=1.0; // Bearish (clrRed)
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
