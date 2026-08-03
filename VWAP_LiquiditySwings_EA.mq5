//+------------------------------------------------------------------+
//|                                     VWAP_LiquiditySwings_EA.mq5  |
//|                                                            Jules |
//|                                             https://www.mql5.com |
//|                                                                  |
//| An Expert Advisor implementing VWAP and Liquidity Swings         |
//| strategy on MT5, with beautiful high-performance chart plotting. |
//+------------------------------------------------------------------+
#property copyright "Jules"
#property link      "https://www.mql5.com"
#property version   "1.01"

#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>

//--- enums
enum ENUM_ANCHOR_PERIOD
{
   ANCHOR_SESSION, // Session (Daily)
   ANCHOR_WEEK,    // Week
   ANCHOR_MONTH,   // Month
   ANCHOR_YEAR     // Year
};

//--- inputs
input group "=== Strategy Parameters ==="
input ENUM_TIMEFRAMES InpStrategyTimeframe = PERIOD_M15;    // Strategy Timeframe
input int InpRegimeEMAPeriod = 34;                           // Regime EMA Period
input ENUM_MA_METHOD InpRegimeEMAMethod = MODE_EMA;          // Regime EMA Method
input ENUM_APPLIED_PRICE InpRegimeEMAAppliedPrice = PRICE_CLOSE; // Regime EMA Applied Price
input double InpEntryBufferPoints = 0.0;                     // Breakout Entry Buffer (Points)

input group "=== VWAP Settings ==="
input ENUM_TIMEFRAMES InpVWAPTimeframe = PERIOD_H1;          // VWAP Timeframe
input ENUM_ANCHOR_PERIOD InpAnchorPeriod = ANCHOR_SESSION;   // VWAP Anchor Period

input group "=== Liquidity Swings (Target) ==="
input int InpPivotLookback = 14;                             // Swing Pivot Lookback (length)
input double InpFallbackRR = 2.0;                            // Fallback Risk:Reward (if no swing low)

input group "=== Risk & Trade Management ==="
input double InpLotSize = 0.1;                               // Trade Lot Size
input ulong InpMagicNumber = 887766;                         // Magic Number
input bool InpPlotOnChart = true;                            // Plot VWAP & Swings on Chart

//--- global state
CTrade m_trade;
CSymbolInfo m_symbol;
int m_ema_handle = INVALID_HANDLE;
datetime m_last_bar_time = 0;

// Setup tracking
bool m_setup_active = false;
datetime m_setup_time = 0; // open time of the immediate next candle
double m_signal_low = 0.0;
double m_signal_high = 0.0;
double m_target_price = 0.0;

// Cache for optimized VWAP calculations
datetime m_last_vwap_calc_bar = 0;
double m_cached_vwap = 0.0;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // Initialize Symbol Info
   if(!m_symbol.Name(_Symbol))
   {
      Print("[-] Failed to initialize Symbol Info for ", _Symbol);
      return(INIT_FAILED);
   }

   m_trade.SetExpertMagicNumber(InpMagicNumber);

   // Configure filling policy dynamically
   uint filling = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0)
      m_trade.SetTypeFilling(ORDER_FILLING_FOK);
   else if((filling & SYMBOL_FILLING_IOC) != 0)
      m_trade.SetTypeFilling(ORDER_FILLING_IOC);
   else
      m_trade.SetTypeFilling(ORDER_FILLING_RETURN);

   // Initialize EMA handle
   m_ema_handle = iMA(_Symbol, InpStrategyTimeframe, InpRegimeEMAPeriod, 0, InpRegimeEMAMethod, InpRegimeEMAAppliedPrice);
   if(m_ema_handle == INVALID_HANDLE)
   {
      Print("[-] Failed to initialize EMA indicator handle.");
      return(INIT_FAILED);
   }

   Print("[+] Expert Advisor initialized successfully.");
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   // Clean up any drawn graphical objects
   if(InpPlotOnChart)
   {
      ObjectsDeleteAll(0, "EA_VWAP_");
      ObjectsDeleteAll(0, "EA_Swing_");
      ObjectsDeleteAll(0, "EA_ActiveSetup_");
   }
   if(m_ema_handle != INVALID_HANDLE)
   {
      IndicatorRelease(m_ema_handle);
   }
}

//+------------------------------------------------------------------+
//| Helper to detect start of anchor period                          |
//+------------------------------------------------------------------+
datetime GetAnchorStartTime(datetime current_time, ENUM_ANCHOR_PERIOD anchor)
{
   MqlDateTime dt;
   TimeToStruct(current_time, dt);

   if(anchor == ANCHOR_SESSION)
   {
      dt.hour = 0; dt.min = 0; dt.sec = 0;
      return StructToTime(dt);
   }
   else if(anchor == ANCHOR_WEEK)
   {
      dt.hour = 0; dt.min = 0; dt.sec = 0;
      datetime day_start = StructToTime(dt);
      int day_offset = (dt.day_of_week == 0) ? 6 : (dt.day_of_week - 1);
      return day_start - day_offset * 86400;
   }
   else if(anchor == ANCHOR_MONTH)
   {
      dt.day = 1; dt.hour = 0; dt.min = 0; dt.sec = 0;
      return StructToTime(dt);
   }
   else if(anchor == ANCHOR_YEAR)
   {
      dt.mon = 1; dt.day = 1; dt.hour = 0; dt.min = 0; dt.sec = 0;
      return StructToTime(dt);
   }
   return 0;
}

//+------------------------------------------------------------------+
//| Calculate VWAP at a specific time internally with caching        |
//+------------------------------------------------------------------+
double CalculateVWAPAtTime(datetime target_time)
{
   datetime current_vwap_bar = iTime(_Symbol, InpVWAPTimeframe, 0);
   if(current_vwap_bar == m_last_vwap_calc_bar && m_cached_vwap > 0)
   {
      return m_cached_vwap;
   }

   datetime start_time = GetAnchorStartTime(target_time, InpAnchorPeriod);
   MqlRates rates[];
   int copied = CopyRates(_Symbol, InpVWAPTimeframe, start_time, target_time, rates);
   if(copied <= 0) return 0.0;

   double sum_pv = 0;
   double sum_v = 0;
   for(int i = 0; i < copied; i++)
   {
      double price = (rates[i].high + rates[i].low + rates[i].close) / 3.0;
      double vol = (rates[i].real_volume > 0) ? (double)rates[i].real_volume : (double)rates[i].tick_volume;
      if(vol <= 0) vol = 1.0;
      sum_pv += price * vol;
      sum_v += vol;
   }

   m_cached_vwap = (sum_v > 0) ? (sum_pv / sum_v) : 0.0;
   m_last_vwap_calc_bar = current_vwap_bar;
   return m_cached_vwap;
}

//+------------------------------------------------------------------+
//| Find the previous confirmed swing low                            |
//+------------------------------------------------------------------+
double FindPreviousSwingLow(int lookback)
{
   MqlRates rates[];
   int copied = CopyRates(_Symbol, InpStrategyTimeframe, 0, 500, rates);
   if(copied < 2 * lookback + 1) return 0.0;

   // Chronological: rates[copied-1] is current bar, rates[0] is oldest.
   // MT5 index i maps to rates[copied - 1 - i].
   for(int i = lookback; i < copied - lookback; i++)
   {
      int rates_idx = copied - 1 - i;
      double current_low = rates[rates_idx].low;
      bool is_pivot = true;

      for(int j = 1; j <= lookback; j++)
      {
         if(rates[rates_idx - j].low < current_low || rates[rates_idx + j].low < current_low)
         {
            is_pivot = false;
            break;
         }
      }

      if(is_pivot)
      {
         return current_low;
      }
   }
   return 0.0;
}

//+------------------------------------------------------------------+
//| Check if we already have an open position matching symbol/magic   |
//+------------------------------------------------------------------+
bool HasOpenPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         {
            return true;
         }
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Normalize lots using broker step rules                            |
//+------------------------------------------------------------------+
double NormalizeLotSize(double lots)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0) step = 0.01;
   double min_vol = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_vol = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   double normalized = MathRound(lots / step) * step;
   if(normalized < min_vol) normalized = min_vol;
   if(normalized > max_vol) normalized = max_vol;
   return normalized;
}

//+------------------------------------------------------------------+
//| Adjust SL and TP levels to comply with broker stops levels       |
//+------------------------------------------------------------------+
void AdjustSLTP(double entry, double &sl, double &tp)
{
   double limit = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   if(limit <= 0) limit = 10 * _Point;

   if(sl < entry + limit) sl = entry + limit;
   if(tp > entry - limit) tp = entry - limit;
}

//+------------------------------------------------------------------+
//| Plot indicator lines and swing markers on chart (High Perf)      |
//+------------------------------------------------------------------+
void PlotIndicators()
{
   if(!InpPlotOnChart || (MQLInfoInteger(MQL_TESTER) && !MQLInfoInteger(MQL_VISUAL_MODE))) return;

   // Optimized single-pass VWAP calculation for plotting
   MqlRates chart_rates[];
   int copied = CopyRates(_Symbol, InpStrategyTimeframe, 0, 100, chart_rates);
   if(copied >= 2)
   {
      datetime oldest_time = chart_rates[0].time;
      datetime start_time = GetAnchorStartTime(oldest_time, InpAnchorPeriod);

      MqlRates vwap_rates[];
      int vwap_copied = CopyRates(_Symbol, InpVWAPTimeframe, start_time, TimeCurrent(), vwap_rates);
      if(vwap_copied > 0)
      {
         double vwap_values[];
         ArrayResize(vwap_values, vwap_copied);

         double sum_pv = 0;
         double sum_v = 0;
         datetime current_anchor_start = start_time;

         for(int i = 0; i < vwap_copied; i++)
         {
            datetime t = vwap_rates[i].time;
            datetime bar_anchor_start = GetAnchorStartTime(t, InpAnchorPeriod);
            if(bar_anchor_start != current_anchor_start)
            {
               sum_pv = 0;
               sum_v = 0;
               current_anchor_start = bar_anchor_start;
            }

            double price = (vwap_rates[i].high + vwap_rates[i].low + vwap_rates[i].close) / 3.0;
            double vol = (vwap_rates[i].real_volume > 0) ? (double)vwap_rates[i].real_volume : (double)vwap_rates[i].tick_volume;
            if(vol <= 0) vol = 1.0;

            sum_pv += price * vol;
            sum_v += vol;
            vwap_values[i] = (sum_v > 0) ? (sum_pv / sum_v) : price;
         }

         double chart_vwap[];
         ArrayResize(chart_vwap, copied);

         int vwap_idx = 0;
         for(int i = 0; i < copied; i++)
         {
            datetime t = chart_rates[i].time;
            while(vwap_idx < vwap_copied - 1 && vwap_rates[vwap_idx + 1].time <= t)
            {
               vwap_idx++;
            }
            chart_vwap[i] = vwap_values[vwap_idx];
         }

         ObjectsDeleteAll(0, "EA_VWAP_");
         for(int i = 1; i < copied; i++)
         {
            datetime t1 = chart_rates[i-1].time;
            datetime t2 = chart_rates[i].time;
            double vwap1 = chart_vwap[i-1];
            double vwap2 = chart_vwap[i];

            string name = "EA_VWAP_" + (string)i;
            if(ObjectCreate(0, name, OBJ_TREND, 0, t1, vwap1, t2, vwap2))
            {
               ObjectSetInteger(0, name, OBJPROP_COLOR, clrBlue);
               ObjectSetInteger(0, name, OBJPROP_WIDTH, 2);
               ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
               ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
               ObjectSetInteger(0, name, OBJPROP_BACK, true);
            }
         }
      }
   }

   // Plot Swing Highs/Lows markers
   MqlRates swing_rates[];
   int swing_copied = CopyRates(_Symbol, InpStrategyTimeframe, 0, 300, swing_rates);
   if(swing_copied >= 2 * InpPivotLookback + 1)
   {
      ObjectsDeleteAll(0, "EA_Swing_");
      int count_highs = 0;
      int count_lows = 0;

      for(int i = InpPivotLookback; i < swing_copied - InpPivotLookback; i++)
      {
         double current_high = swing_rates[i].high;
         double current_low  = swing_rates[i].low;
         bool is_pivot_high  = true;
         bool is_pivot_low   = true;

         for(int j = 1; j <= InpPivotLookback; j++)
         {
            if(swing_rates[i - j].high > current_high || swing_rates[i + j].high > current_high)
               is_pivot_high = false;
            if(swing_rates[i - j].low < current_low || swing_rates[i + j].low < current_low)
               is_pivot_low = false;
         }

         if(is_pivot_high && count_highs < 15)
         {
            string name = "EA_Swing_High_" + (string)swing_rates[i].time;
            if(ObjectCreate(0, name, OBJ_ARROW_DOWN, 0, swing_rates[i].time, current_high))
            {
               ObjectSetInteger(0, name, OBJPROP_COLOR, clrRed);
               ObjectSetInteger(0, name, OBJPROP_ARROWCODE, 159);
               ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
            }
            count_highs++;
         }
         if(is_pivot_low && count_lows < 15)
         {
            string name = "EA_Swing_Low_" + (string)swing_rates[i].time;
            if(ObjectCreate(0, name, OBJ_ARROW_UP, 0, swing_rates[i].time, current_low))
            {
               ObjectSetInteger(0, name, OBJPROP_COLOR, clrTeal);
               ObjectSetInteger(0, name, OBJPROP_ARROWCODE, 159);
               ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
            }
            count_lows++;
         }
      }
   }

   // Plot active setup line if any
   ObjectsDeleteAll(0, "EA_ActiveSetup_");
   if(m_setup_active)
   {
      ObjectCreate(0, "EA_ActiveSetup_Low", OBJ_HLINE, 0, 0, m_signal_low);
      ObjectSetInteger(0, "EA_ActiveSetup_Low", OBJPROP_COLOR, clrOrange);
      ObjectSetInteger(0, "EA_ActiveSetup_Low", OBJPROP_STYLE, STYLE_DASH);
      ObjectSetInteger(0, "EA_ActiveSetup_Low", OBJPROP_SELECTABLE, false);

      ObjectCreate(0, "EA_ActiveSetup_High", OBJ_HLINE, 0, 0, m_signal_high);
      ObjectSetInteger(0, "EA_ActiveSetup_High", OBJPROP_COLOR, clrCrimson);
      ObjectSetInteger(0, "EA_ActiveSetup_High", OBJPROP_STYLE, STYLE_DASH);
      ObjectSetInteger(0, "EA_ActiveSetup_High", OBJPROP_SELECTABLE, false);
   }

   ChartRedraw(0);
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // Check current bar time on strategy timeframe
   datetime current_bar_time = iTime(_Symbol, InpStrategyTimeframe, 0);
   if(current_bar_time == 0) return;

   // Refresh dynamic plots and check setups on new bar
   if(current_bar_time != m_last_bar_time)
   {
      m_last_bar_time = current_bar_time;
      PlotIndicators();

      // Manage breakout setup expiration
      if(m_setup_active)
      {
         // If a new bar opens that is beyond the immediate next candle of our signal candle, we must discard it
         if(current_bar_time > m_setup_time)
         {
            Print("[*] Setup expired: Next candle did not break the signal candle low. Discarding signal from setup time ", TimeToString(m_setup_time));
            m_setup_active = false;
         }
      }

      // Check for a new signal candle (this runs once per bar when a new bar has just opened)
      // For CopyRates of count 3:
      // rates[2] is the current active bar (index 0)
      // rates[1] is the last completed bar (index 1 / signal candle)
      // rates[0] is the bar before that (index 2 / previous candle)
      MqlRates rates[];
      if(CopyRates(_Symbol, InpStrategyTimeframe, 0, 3, rates) == 3)
      {
         double open_1 = rates[1].open;
         double close_1 = rates[1].close;
         double high_1 = rates[1].high;
         double low_1 = rates[1].low;

         double open_2 = rates[0].open;
         double close_2 = rates[0].close;

         // 1. Prev candle must be Green
         bool prev_is_green = (close_2 > open_2);
         // 2. Signal candle must be Red
         bool sig_is_red = (close_1 < open_1);

         // 3. Retrieve EMA at index 1
         double ema_val[];
         if(CopyBuffer(m_ema_handle, 0, 1, 1, ema_val) > 0)
         {
            double ema_1 = ema_val[0];

            // 4. Signal candle crossed above or touched EMA, and closed below EMA
            bool touched_or_crossed = (high_1 >= ema_1);
            bool closed_below = (close_1 < ema_1);

            if(prev_is_green && sig_is_red && touched_or_crossed && closed_below)
            {
               // We have a valid signal candle!
               m_setup_active = true;
               m_setup_time = current_bar_time; // current bar (index 0) is the immediate next candle
               m_signal_low = low_1;
               m_signal_high = high_1;

               // Find previous swing low
               m_target_price = FindPreviousSwingLow(InpPivotLookback);

               Print("[+] Signal Candle detected at time: ", TimeToString(rates[1].time),
                     " | High: ", high_1, " Low: ", low_1, " EMA: ", ema_1,
                     " | Target previous swing low: ", m_target_price);

               PlotIndicators();
            }
         }
      }
   }

   // Monitor Breakout Entry tick-by-tick
   if(m_setup_active && !HasOpenPosition())
   {
      double current_bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      if(current_bid <= 0) return;

      // Check if price breaks below signal candle's low (minus entry buffer)
      double entry_level = m_signal_low - InpEntryBufferPoints * _Point;
      if(current_bid < entry_level)
      {
         // Double check VWAP filter on configured VWAP Timeframe
         double vwap_val = CalculateVWAPAtTime(TimeCurrent());
         if(vwap_val > 0)
         {
            if(current_bid < vwap_val)
            {
               // Confirm entry!
               double entry_price = current_bid;
               double sl = m_signal_high;
               double tp = m_target_price;

               // Adjust and normalize SL / TP
               if(tp <= 0 || tp >= entry_price)
               {
                  double sl_dist = sl - entry_price;
                  tp = entry_price - sl_dist * InpFallbackRR;
                  Print("[!] Invalid or missing swing low. Using Fallback Risk:Reward of ", InpFallbackRR, "x to set Target: ", tp);
               }

               AdjustSLTP(entry_price, sl, tp);

               double normalized_lots = NormalizeLotSize(InpLotSize);

               Print("[>] Sending Market SELL order: Lot=", normalized_lots,
                     " | Entry=", entry_price, " SL=", sl, " TP=", tp, " | VWAP=", vwap_val);

               if(m_trade.Sell(normalized_lots, _Symbol, entry_price, sl, tp, "VWAP Liquidity Swings EA"))
               {
                  Print("[+] Trade executed successfully.");
                  m_setup_active = false; // consume setup
                  PlotIndicators();
               }
               else
               {
                  Print("[-] Order execution failed: Error ", m_trade.ResultRetcode(), " - ", m_trade.ResultComment());
               }
            }
            else
            {
               static datetime last_vwap_warn = 0;
               if(TimeCurrent() - last_vwap_warn > 60)
               {
                  Print("[!] Breakout detected but price (", current_bid, ") is above VWAP (", vwap_val, "). Trade skipped.");
                  last_vwap_warn = TimeCurrent();
               }
            }
         }
         else
         {
            Print("[-] VWAP calculation returned 0. Waiting for data...");
         }
      }
   }
}
//+------------------------------------------------------------------+
