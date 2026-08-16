//+------------------------------------------------------------------+
//|                                             HeikenAshi_Ind.mq5   |
//|                                 Copyright 2025, Expert Advisor   |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2025"
#property link      "https://www.mql5.com"
#property version   "1.00"
#property description "Heiken Ashi Indicator for MT5 Chart and Strategy Tester Visual Mode."

#property indicator_chart_window
#property indicator_buffers 5
#property indicator_plots   1

//--- Plot Heiken Ashi Candles
#property indicator_label1  "Heiken Ashi"
#property indicator_type1   DRAW_COLOR_CANDLES
#property indicator_color1  clrLime, clrRed
#property indicator_style1  STYLE_SOLID
#property indicator_width1  1

//--- Indicator Buffers
double BufferOpen[];
double BufferHigh[];
double BufferLow[];
double BufferClose[];
double BufferColor[];

//+------------------------------------------------------------------+
//| Custom indicator initialization function                         |
//+------------------------------------------------------------------+
int OnInit()
  {
   // Map Indicator Buffers
   SetIndexBuffer(0, BufferOpen,  INDICATOR_DATA);
   SetIndexBuffer(1, BufferHigh,  INDICATOR_DATA);
   SetIndexBuffer(2, BufferLow,   INDICATOR_DATA);
   SetIndexBuffer(3, BufferClose, INDICATOR_DATA);
   SetIndexBuffer(4, BufferColor, INDICATOR_COLOR_INDEX);

   // Set short name
   IndicatorSetString(INDICATOR_SHORTNAME, "Heiken Ashi Visual");

   return INIT_SUCCEEDED;
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
      return 0;

   int start = prev_calculated;

   if(start == 0)
     {
      // First bar calculation
      BufferOpen[0]  = (open[0] + close[0]) / 2.0;
      BufferClose[0] = (open[0] + high[0] + low[0] + close[0]) / 4.0;
      BufferHigh[0]  = MathMax(high[0], MathMax(BufferOpen[0], BufferClose[0]));
      BufferLow[0]   = MathMin(low[0], MathMin(BufferOpen[0], BufferClose[0]));
      BufferColor[0] = (BufferClose[0] >= BufferOpen[0]) ? 0.0 : 1.0;

      start = 1;
     }

   for(int i = start; i < rates_total; i++)
     {
      BufferClose[i] = (open[i] + high[i] + low[i] + close[i]) / 4.0;
      BufferOpen[i]  = (BufferOpen[i - 1] + BufferClose[i - 1]) / 2.0;
      BufferHigh[i]  = MathMax(high[i], MathMax(BufferOpen[i], BufferClose[i]));
      BufferLow[i]   = MathMin(low[i], MathMin(BufferOpen[i], BufferClose[i]));
      BufferColor[i] = (BufferClose[i] >= BufferOpen[i]) ? 0.0 : 1.0; // 0 = Green (Lime), 1 = Red
     }

   return rates_total;
  }
//+------------------------------------------------------------------+
