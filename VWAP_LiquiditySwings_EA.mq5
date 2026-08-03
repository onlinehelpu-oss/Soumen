//+------------------------------------------------------------------+
//|                                     VWAP_LiquiditySwings_EA.mq5  |
//|                                                            Jules |
//|                                             https://www.mql5.com |
//|                                                                  |
//| An Expert Advisor implementing VWAP and Liquidity Swings         |
//| strategy on MT5, utilizing high-performance iCustom indicator     |
//| caching and robust next-candle breakout rules.                    |
//+------------------------------------------------------------------+
#property copyright "Jules"
#property link      "https://www.mql5.com"
#property version   "1.02"

//--- tell strategy tester to bundle and load custom indicators
#property tester_indicator "VWAP_Ind.ex5"
#property tester_indicator "Liquidity_Swings_Ind.ex5"

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

// Indicator handles
int m_ema_handle = INVALID_HANDLE;
int m_vwap_handle = INVALID_HANDLE;
int m_swings_handle = INVALID_HANDLE;

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

   // Load VWAP Indicator via iCustom
   m_vwap_handle = iCustom(_Symbol, InpVWAPTimeframe, "VWAP_Ind", InpAnchorPeriod, PRICE_TYPICAL);
   if(m_vwap_handle == INVALID_HANDLE)
   {
      Print("[-] Failed to load VWAP_Ind custom indicator.");
      return(INIT_FAILED);
   }

   // Load Liquidity Swings Indicator via iCustom
   m_swings_handle = iCustom(_Symbol, InpStrategyTimeframe, "Liquidity_Swings_Ind", InpPivotLookback);
   if(m_swings_handle == INVALID_HANDLE)
   {
      Print("[-] Failed to load Liquidity_Swings_Ind custom indicator.");
      return(INIT_FAILED);
   }

   // Attach indicators directly to the chart window for standard non-lagging plotting
   if(InpPlotOnChart && (!MQLInfoInteger(MQL_TESTER) || (MQLInfoInteger(MQL_TESTER) && MQLInfoInteger(MQL_VISUAL_MODE))))
   {
      ChartIndicatorAdd(0, 0, m_vwap_handle);
      ChartIndicatorAdd(0, 0, m_swings_handle);
   }

   Print("[+] Expert Advisor initialized successfully.");
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   // Clean up indicator handles
   if(m_ema_handle != INVALID_HANDLE) IndicatorRelease(m_ema_handle);
   if(m_vwap_handle != INVALID_HANDLE) IndicatorRelease(m_vwap_handle);
   if(m_swings_handle != INVALID_HANDLE) IndicatorRelease(m_swings_handle);

   // Remove drawn lines
   ObjectsDeleteAll(0, "EA_ActiveSetup_");
}

//+------------------------------------------------------------------+
//| Get the current VWAP value from indicator buffer                 |
//+------------------------------------------------------------------+
double GetCurrentVWAP()
{
   double val[];
   if(CopyBuffer(m_vwap_handle, 0, 0, 1, val) > 0)
   {
      return val[0];
   }
   return 0.0;
}

//+------------------------------------------------------------------+
//| Find the previous confirmed swing low from indicator buffer      |
//+------------------------------------------------------------------+
double FindPreviousSwingLowFromIndicator()
{
   double swing_lows[];
   int copied = CopyBuffer(m_swings_handle, 1, 0, 500, swing_lows);
   if(copied <= 0) return 0.0;

   // MT5's CopyBuffer copies chronological values where swing_lows[copied - 1] is current bar.
   // Search backwards from the newest confirmed bar.
   int start_idx = copied - 1 - InpPivotLookback;
   if(start_idx < 0) start_idx = copied - 1;

   for(int i = start_idx; i >= 0; i--)
   {
      if(swing_lows[i] > 0.0)
      {
         return swing_lows[i];
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
//| Plot active setup line if any                                    |
//+------------------------------------------------------------------+
void PlotActiveSetup()
{
   if(!InpPlotOnChart || (MQLInfoInteger(MQL_TESTER) && !MQLInfoInteger(MQL_VISUAL_MODE))) return;

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
            PlotActiveSetup();
         }
      }

      // Check for a new signal candle (this runs once per bar when a new bar has just opened)
      // rates[2] is current active bar (Bar 0)
      // rates[1] is the last completed bar (Bar 1 / Potential Signal Candle)
      // rates[0] is the bar before that (Bar 2 / Previous Candle)
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
               m_setup_time = current_bar_time; // current bar is the immediate next candle
               m_signal_low = low_1;
               m_signal_high = high_1;

               // Find previous swing low from Liquidity Swings indicator buffer
               m_target_price = FindPreviousSwingLowFromIndicator();

               Print("[+] Signal Candle detected at time: ", TimeToString(rates[1].time),
                     " | High: ", high_1, " Low: ", low_1, " EMA: ", ema_1,
                     " | Target previous swing low: ", m_target_price);

               PlotActiveSetup();
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
         double vwap_val = GetCurrentVWAP();
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
                  PlotActiveSetup();
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
