//+------------------------------------------------------------------+
//|                                     VWAP_LiquiditySwings_EA.mq5  |
//|                                                            Jules |
//|                                             https://www.mql5.com |
//|                                                                  |
//| A completely self-contained Expert Advisor implementing VWAP and |
//| Liquidity Swings strategy on MT5. No external custom indicator    |
//| files are required, ensuring instant out-of-the-box execution     |
//| and plotting in any Strategy Tester or live environment.          |
//+------------------------------------------------------------------+
#property copyright "Jules"
#property link      "https://www.mql5.com"
#property version   "1.05"

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
input bool InpPlotOnChart = true;                            // Plot VWAP, EMA & Swings on Chart
input bool InpUseBreakEven = true;                           // Move SL to Cost-to-Cost at 1:1
input double InpBreakEvenRatio = 1.0;                        // Breakeven Risk:Reward Ratio

//--- global state
CTrade m_trade;
CSymbolInfo m_symbol;

// EMA Handle
int m_ema_handle = INVALID_HANDLE;
datetime m_last_bar_time = 0;

// Setup tracking
bool m_setup_active = false;
datetime m_setup_time = 0; // open time of the immediate next candle
double m_signal_low = 0.0;
double m_signal_high = 0.0;
double m_target_price = 0.0;

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

   Print("[+] Self-Contained Expert Advisor initialized successfully.");
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(m_ema_handle != INVALID_HANDLE) IndicatorRelease(m_ema_handle);

   // Clean up chart drawings
   if(InpPlotOnChart)
   {
      ObjectsDeleteAll(0, "EA_VWAP_");
      ObjectsDeleteAll(0, "EA_EMA_");
      ObjectsDeleteAll(0, "EA_Swing_");
      ObjectsDeleteAll(0, "EA_ActiveSetup_");
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
//| Self-Contained VWAP Calculation                                  |
//+------------------------------------------------------------------+
double CalculateVWAP(datetime target_time)
{
   datetime start_time = GetAnchorStartTime(target_time, InpAnchorPeriod);
   MqlRates vwap_rates[];
   int copied = CopyRates(_Symbol, InpVWAPTimeframe, start_time, target_time, vwap_rates);
   if(copied <= 0) return 0.0;

   double sum_pv = 0;
   double sum_v = 0;
   for(int i = 0; i < copied; i++)
   {
      double price = (vwap_rates[i].high + vwap_rates[i].low + vwap_rates[i].close) / 3.0;
      double vol = (vwap_rates[i].real_volume > 0) ? (double)vwap_rates[i].real_volume : (double)vwap_rates[i].tick_volume;
      if(vol <= 0) vol = 1.0;
      sum_pv += price * vol;
      sum_v += vol;
   }
   return (sum_v > 0) ? (sum_pv / sum_v) : 0.0;
}

//+------------------------------------------------------------------+
//| Find previous confirmed swing low natively                       |
//+------------------------------------------------------------------+
double FindPreviousSwingLow(int lookback)
{
   MqlRates swing_rates[];
   int copied = CopyRates(_Symbol, InpStrategyTimeframe, 0, 300, swing_rates);
   if(copied < 2 * lookback + 1) return 0.0;

   // Chronological ordering: swing_rates[0] is oldest, swing_rates[copied-1] is current bar.
   // We search backwards from the latest confirmed pivot candle (copied - 1 - lookback)
   for(int i = copied - 1 - lookback; i >= lookback; i--)
   {
      double current_low = swing_rates[i].low;
      bool is_pivot = true;

      for(int j = 1; j <= lookback; j++)
      {
         if(swing_rates[i - j].low < current_low || swing_rates[i + j].low < current_low)
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
//| Manage Breakeven (trail stop loss to cost-to-cost at 1:1 profit) |
//+------------------------------------------------------------------+
void ManageBreakEven()
{
   if(!InpUseBreakEven) return;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         {
            double entry = PositionGetDouble(POSITION_PRICE_OPEN);
            double sl = PositionGetDouble(POSITION_SL);
            double tp = PositionGetDouble(POSITION_TP);

            // This is a Short position
            if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_SELL)
            {
               if(sl > entry)
               {
                  double risk_distance = sl - entry;
                  double target_trigger = entry - risk_distance * InpBreakEvenRatio;
                  double current_ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

                  if(current_ask <= target_trigger)
                  {
                     Print("[*] 1:1 profit achieved! Trailing Stop Loss to Cost-to-Cost: ", entry);
                     if(!m_trade.PositionModify(ticket, entry, tp))
                     {
                        Print("[-] Failed to trail Stop Loss to cost-to-cost: Error ", m_trade.ResultRetcode());
                     }
                  }
               }
            }
         }
      }
   }
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
//| Self-Contained High-Performance On-Chart Plotting                 |
//+------------------------------------------------------------------+
void PlotIndicatorsOnChart()
{
   if(!InpPlotOnChart || (MQLInfoInteger(MQL_TESTER) && !MQLInfoInteger(MQL_VISUAL_MODE))) return;

   MqlRates rates[];
   int copied = CopyRates(_Symbol, InpStrategyTimeframe, 0, 150, rates);
   if(copied < 2) return;

   // 1. Plot Regime EMA
   double ema_vals[];
   int ema_copied = CopyBuffer(m_ema_handle, 0, 0, 150, ema_vals);
   if(ema_copied >= copied)
   {
      ObjectsDeleteAll(0, "EA_EMA_");
      for(int i = 1; i < copied; i++)
      {
         datetime t1 = rates[i-1].time;
         datetime t2 = rates[i].time;
         double y1 = ema_vals[i-1];
         double y2 = ema_vals[i];

         if(y1 > 0 && y2 > 0)
         {
            string name = "EA_EMA_" + (string)i;
            if(ObjectCreate(0, name, OBJ_TREND, 0, t1, y1, t2, y2))
            {
               ObjectSetInteger(0, name, OBJPROP_COLOR, clrOrange);
               ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
               ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
               ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
               ObjectSetInteger(0, name, OBJPROP_BACK, true);
            }
         }
      }
   }

   // 2. Plot self-contained VWAP
   ObjectsDeleteAll(0, "EA_VWAP_");
   for(int i = 1; i < copied; i++)
   {
      datetime t1 = rates[i-1].time;
      datetime t2 = rates[i].time;
      double y1 = CalculateVWAP(t1);
      double y2 = CalculateVWAP(t2);

      if(y1 > 0 && y2 > 0)
      {
         string name = "EA_VWAP_" + (string)i;
         if(ObjectCreate(0, name, OBJ_TREND, 0, t1, y1, t2, y2))
         {
            ObjectSetInteger(0, name, OBJPROP_COLOR, clrBlue);
            ObjectSetInteger(0, name, OBJPROP_WIDTH, 2);
            ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
            ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
            ObjectSetInteger(0, name, OBJPROP_BACK, true);
         }
      }
   }

   // 3. Plot Liquidity Swings High and Low markers
   ObjectsDeleteAll(0, "EA_Swing_");
   for(int i = InpPivotLookback; i < copied - InpPivotLookback; i++)
   {
      double current_high = rates[i].high;
      double current_low  = rates[i].low;
      bool is_pivot_high  = true;
      bool is_pivot_low   = true;

      for(int j = 1; j <= InpPivotLookback; j++)
      {
         if(rates[i - j].high > current_high || rates[i + j].high > current_high)
            is_pivot_high = false;
         if(rates[i - j].low < current_low || rates[i + j].low < current_low)
            is_pivot_low = false;
      }

      if(is_pivot_high)
      {
         string name = "EA_Swing_High_" + (string)rates[i].time;
         if(ObjectCreate(0, name, OBJ_ARROW_DOWN, 0, rates[i].time, current_high))
         {
            ObjectSetInteger(0, name, OBJPROP_COLOR, clrRed);
            ObjectSetInteger(0, name, OBJPROP_ARROWCODE, 159);
            ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
         }
      }
      if(is_pivot_low)
      {
         string name = "EA_Swing_Low_" + (string)rates[i].time;
         if(ObjectCreate(0, name, OBJ_ARROW_UP, 0, rates[i].time, current_low))
         {
            ObjectSetInteger(0, name, OBJPROP_COLOR, clrTeal);
            ObjectSetInteger(0, name, OBJPROP_ARROWCODE, 159);
            ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
         }
      }
   }

   // 4. Plot Active Setup low and high lines
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

   // Check and trail active positions to Cost-to-Cost
   ManageBreakEven();

   // New bar processing
   if(current_bar_time != m_last_bar_time)
   {
      m_last_bar_time = current_bar_time;

      // Manage breakout setup expiration
      if(m_setup_active)
      {
         if(current_bar_time > m_setup_time)
         {
            Print("[*] Setup expired: Next candle did not break the signal candle low. Discarding setup.");
            m_setup_active = false;
         }
      }

      // Check for a new signal candle (runs once per bar)
      // CopyRates with count 3:
      // rates[2] is current incomplete bar (index 0)
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
               // Valid signal candle!
               m_setup_active = true;
               m_setup_time = current_bar_time; // Bar 0 is the immediate next candle
               m_signal_low = low_1;
               m_signal_high = high_1;

               // Find previous swing low
               m_target_price = FindPreviousSwingLow(InpPivotLookback);

               Print("[+] Signal Candle detected at time: ", TimeToString(rates[1].time),
                     " | High: ", high_1, " Low: ", low_1, " EMA: ", ema_1,
                     " | Target previous swing low: ", m_target_price);
            }
         }
      }

      // Update chart drawings on new bar open
      PlotIndicatorsOnChart();
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
         // Calculate self-contained VWAP
         double vwap_val = CalculateVWAP(TimeCurrent());
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
                  PlotIndicatorsOnChart();
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
            // VWAP has not loaded yet, wait for data
         }
      }
   }
}
//+------------------------------------------------------------------+
