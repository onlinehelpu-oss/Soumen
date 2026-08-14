//+------------------------------------------------------------------+
//|                                         WilliamsFractals_Ind.mq5 |
//|                                                            Jules |
//|                                                                  |
//| Converted Pine Script v6 Williams Fractals MT5 Custom Indicator  |
//| Highly optimized with correct boundaries and crash protection    |
//+------------------------------------------------------------------+
#property copyright "Jules"
#property link      ""
#property version   "1.20"
#property indicator_chart_window
#property indicator_buffers 2
#property indicator_plots   2

//--- Plot UpFractal
#property indicator_label1  "Up Fractal"
#property indicator_type1   DRAW_ARROW
#property indicator_color1  clrTeal
#property indicator_width1  2

//--- Plot DownFractal
#property indicator_label2  "Down Fractal"
#property indicator_type2   DRAW_ARROW
#property indicator_color2  clrRed
#property indicator_width2  2

//--- Input Parameters
input int InpPeriods = 2; // Periods (minimum 2)

//--- Global Variables
int m_periods = 2;

//--- Indicator Buffers
double m_up_buffer[];
double m_down_buffer[];

//+------------------------------------------------------------------+
//| Custom indicator initialization function                         |
//+------------------------------------------------------------------+
int OnInit()
{
   m_periods = (InpPeriods < 2) ? 2 : InpPeriods;

   // Map indicator buffers
   SetIndexBuffer(0, m_up_buffer, INDICATOR_DATA);
   SetIndexBuffer(1, m_down_buffer, INDICATOR_DATA);

   // Set plot arrow characters (Wingdings)
   PlotIndexSetInteger(0, PLOT_ARROW, 225); // Up Arrow
   PlotIndexSetInteger(1, PLOT_ARROW, 226); // Down Arrow

   // Set empty values to avoid drawing zeros
   PlotIndexSetDouble(0, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   PlotIndexSetDouble(1, PLOT_EMPTY_VALUE, EMPTY_VALUE);

   IndicatorSetString(INDICATOR_SHORTNAME, "Williams Fractals (" + IntegerToString(m_periods) + ")");

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
   // Check minimum bars (at least 2 * m_periods + 5 is required to safely prevent array out-of-bounds)
   int required_bars = 2 * m_periods + 5;
   if(rates_total < required_bars) return 0;

   // Set series indexing direction so index 0 is always the newest bar
   ArraySetAsSeries(high, true);
   ArraySetAsSeries(low, true);
   ArraySetAsSeries(m_up_buffer, true);
   ArraySetAsSeries(m_down_buffer, true);

   // Initialize empty values for any new candles on first calculation or incremental calculation
   if(prev_calculated == 0)
   {
      ArrayInitialize(m_up_buffer, EMPTY_VALUE);
      ArrayInitialize(m_down_buffer, EMPTY_VALUE);
   }

   // Determine search limit
   int limit = rates_total - prev_calculated;
   if(prev_calculated > 0)
   {
      // We must recalculate several newest bars because a fractal requires future confirmation bars
      limit += 2 * m_periods + 5;
   }

   // Cap limit to valid boundary to prevent 'Array out of range' crash.
   // The maximum index accessed in condition checks is (center + 2 * m_periods + 4) when j = m_periods.
   // Thus, we must guarantee: start_idx + 2 * m_periods + 4 < rates_total.
   int max_safe_start = rates_total - 1 - 2 * m_periods - 4;

   int start_idx = limit;
   if(start_idx > max_safe_start) start_idx = max_safe_start;

   int end_idx = 0; // Plot up to the latest possible candle (index 0)

   // Loop through selected historical rates using optimized series-indexing (0 = newest)
   for(int i = start_idx; i >= end_idx; i--)
   {
      int center = i;
      int n = m_periods;

      // Default plot values for this center's target index to empty first
      m_up_buffer[center + n] = EMPTY_VALUE;
      m_down_buffer[center + n] = EMPTY_VALUE;

      //--- UpFractal Calculation (Pine Script rules)
      bool upflagDownFrontier = true;
      bool upflagUpFrontier0 = true;
      bool upflagUpFrontier1 = true;
      bool upflagUpFrontier2 = true;
      bool upflagUpFrontier3 = true;
      bool upflagUpFrontier4 = true;

      // In series array, a bar's relative index in past (Pine: high[n-i]) is high[center + n - i]
      // Pine: high[n+i] is high[center + n + i] (further in the past)
      // Pine: high[n+1] is high[center + n + 1]
      for(int j = 1; j <= n; j++)
      {
         upflagDownFrontier = upflagDownFrontier && (high[center + n - j] < high[center + n]);
         upflagUpFrontier0 = upflagUpFrontier0 && (high[center + n + j] < high[center + n]);
         upflagUpFrontier1 = upflagUpFrontier1 && (high[center + n + 1] <= high[center + n] && high[center + n + j + 1] < high[center + n]);
         upflagUpFrontier2 = upflagUpFrontier2 && (high[center + n + 1] <= high[center + n] && high[center + n + 2] <= high[center + n] && high[center + n + j + 2] < high[center + n]);
         upflagUpFrontier3 = upflagUpFrontier3 && (high[center + n + 1] <= high[center + n] && high[center + n + 2] <= high[center + n] && high[center + n + 3] <= high[center + n] && high[center + n + j + 3] < high[center + n]);
         upflagUpFrontier4 = upflagUpFrontier4 && (high[center + n + 1] <= high[center + n] && high[center + n + 2] <= high[center + n] && high[center + n + 3] <= high[center + n] && high[center + n + 4] <= high[center + n] && high[center + n + j + 4] < high[center + n]);
      }

      bool flagUpFrontier = upflagUpFrontier0 || upflagUpFrontier1 || upflagUpFrontier2 || upflagUpFrontier3 || upflagUpFrontier4;
      if(upflagDownFrontier && flagUpFrontier)
      {
         m_up_buffer[center + n] = high[center + n];
      }

      //--- DownFractal Calculation (Pine Script rules)
      bool downflagDownFrontier = true;
      bool downflagUpFrontier0 = true;
      bool downflagUpFrontier1 = true;
      bool downflagUpFrontier2 = true;
      bool downflagUpFrontier3 = true;
      bool downflagUpFrontier4 = true;

      for(int j = 1; j <= n; j++)
      {
         downflagDownFrontier = downflagDownFrontier && (low[center + n - j] > low[center + n]);
         downflagUpFrontier0 = downflagUpFrontier0 && (low[center + n + j] > low[center + n]);
         downflagUpFrontier1 = downflagUpFrontier1 && (low[center + n + 1] >= low[center + n] && low[center + n + j + 1] > low[center + n]);
         downflagUpFrontier2 = downflagUpFrontier2 && (low[center + n + 1] >= low[center + n] && low[center + n + 2] >= low[center + n] && low[center + n + j + 2] > low[center + n]);
         downflagUpFrontier3 = downflagUpFrontier3 && (low[center + n + 1] >= low[center + n] && low[center + n + 2] >= low[center + n] && low[center + n + 3] >= low[center + n] && low[center + n + j + 3] > low[center + n]);
         downflagUpFrontier4 = downflagUpFrontier4 && (low[center + n + 1] >= low[center + n] && low[center + n + 2] >= low[center + n] && low[center + n + 3] >= low[center + n] && low[center + n + 4] >= low[center + n] && low[center + n + j + 4] > low[center + n]);
      }

      bool flagDownFrontier = downflagUpFrontier0 || downflagUpFrontier1 || downflagUpFrontier2 || downflagUpFrontier3 || downflagUpFrontier4;
      if(downflagDownFrontier && flagDownFrontier)
      {
         m_down_buffer[center + n] = low[center + n];
      }
   }

   return rates_total;
}
//+------------------------------------------------------------------+
