//+------------------------------------------------------------------+
//|                                     Green_Breakout_Red_Low_EA.mq5|
//|                                  Copyright 2026, Jules Developer |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, Jules Developer"
#property link      "https://www.mql5.com"
#property version   "1.00"
#property strict

// Include the standard trade library
#include <Trade\Trade.mqh>

//--- Enums
enum ENUM_LOT_TYPE
{
   LOT_FIXED,     // Fixed Lot Size
   LOT_RISK_PCT   // Risk Percent per Trade
};

enum ENUM_SL_MODE
{
   SL_MODE_SIGNAL_LOW,  // Signal Candle Low
   SL_MODE_FIXED_POINTS // Fixed Points
};

//--- Inputs
input group "=== TRADE SETTINGS ==="
input double               InpFixedLots            = 0.1;               // Fixed Lot Size (if Lot Type = Fixed)
input double               InpRiskPercent          = 1.0;               // Risk Percent per Trade
input ENUM_LOT_TYPE        InpLotType              = LOT_FIXED;         // Lot Sizing Type
input double               InpMinLotSizeOverride   = 0.01;              // Min Lot Size Override (0 to disable)
input double               InpMaxMarginUtilizationPct = 70.0;           // Max Margin Utilization % (OrderCalcMargin limit)
input ulong                InpMagicNumber          = 123456;            // Magic Number
input int                  InpSlippage             = 30;                // Slippage (Points)

input group "=== STRATEGY SETTINGS ==="
input double               InpRiskRewardRatio      = 2.0;               // Risk to Reward Ratio (e.g. 1:2)
input int                  InpTakeProfitPoints     = 0;                 // Fixed TP Points (0 = Use Risk-Reward Ratio)
input ENUM_SL_MODE         InpStopLossMode         = SL_MODE_SIGNAL_LOW;// Stop Loss Mode
input int                  InpFixedSLPoints        = 200;               // Fixed SL Points (if SL Mode = Fixed)
input int                  InpEntryBufferPoints    = 10;                // Entry Buy Buffer (Points above Red candle High)
input int                  InpSLBufferPoints       = 10;                // Stop Loss Safety Buffer (Points below Red candle Low)
input double               InpMinCandlePoints      = 100.0;             // Minimum Candle Range in Points (Ignore tiny candles)
input int                  InpMaxSetupLifeBars     = 1;                 // Max Setup Lifetime in Bars (Default 1 bar)
input bool                 InpInvalidateOnLowBreak = true;              // Invalidate setup if price drops below Red candle Low

input group "=== SIGNAL FILTERS (OR RELATION) ==="
input bool                 InpUseSwingLowFilter    = true;              // Require Red candle to be at a Swing Low
input int                  InpSwingLookback        = 5;                 // Swing Low Lookback (Bars to the left)
input bool                 InpUseSupportFilter     = true;              // Require Red candle to be at Support
input bool                 InpUseDayLowFilter      = true;              // Require Red candle to be at Day Low
input int                  InpDayLowBufferPoints   = 50;                // Day Low Buffer in Points

input group "=== SUPPORT FILTER PARAMETERS ==="
input bool                 InpUseEMASupport        = true;              // Use EMA as Support
input int                  InpEMAPeriod            = 50;                // EMA Period
input int                  InpEMABufferPoints      = 50;                // EMA Distance Buffer (Points)
input bool                 InpUseHorizontalSupport = true;              // Use Horizontal Support (Historical Low)
input int                  InpHorizontalSupportLookback = 100;          // Horizontal Support Lookback (Bars)
input int                  InpSupportBufferPoints  = 50;                // Support Buffer (Points)

//--- Global Variables
CTrade         m_trade;                      // Trade execution object
datetime       m_last_bar_time         = 0;  // Tracks the opening time of the current bar
datetime       m_last_entered_bar_time = 0;  // Tracks the time of the bar we entered our last trade on
bool           m_setup_active          = false; // Whether we have an active breakout setup pending
datetime       m_setup_bar_time        = 0;  // The time of the Red candle bar
double         m_setup_high            = 0.0;// High of the Red candle
double         m_setup_low             = 0.0; // Low of the Red candle
int            m_setup_bars_passed     = 0;  // Counter for setup lifetime
int            m_ema_handle            = INVALID_HANDLE; // Handle for EMA indicator

//--- Helper Functions to Safe Copy Bar Data
double GetOpen(int index) { double val[]; if(CopyOpen(_Symbol, _Period, index, 1, val) > 0) return val[0]; return 0.0; }
double GetClose(int index) { double val[]; if(CopyClose(_Symbol, _Period, index, 1, val) > 0) return val[0]; return 0.0; }
double GetHigh(int index) { double val[]; if(CopyHigh(_Symbol, _Period, index, 1, val) > 0) return val[0]; return 0.0; }
double GetLow(int index) { double val[]; if(CopyLow(_Symbol, _Period, index, 1, val) > 0) return val[0]; return 0.0; }
datetime GetTime(int index) { datetime val[]; if(CopyTime(_Symbol, _Period, index, 1, val) > 0) return val[0]; return 0; }

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // Set magic number for CTrade
   m_trade.SetExpertMagicNumber(InpMagicNumber);

   // Set slippage in points
   m_trade.SetDeviationInPoints(InpSlippage);

   // Check symbol details
   PrintFormat("EA Initialized for symbol %s on period %s", _Symbol, EnumToString(_Period));
   PrintFormat("Point size: %.5f, Digits: %d", _Point, _Digits);

   // Warn if not running on GOLD/XAUUSD as per request, but still allow execution
   if(StringFind(_Symbol, "XAU") < 0 && StringFind(_Symbol, "GOLD") < 0)
   {
      Print("Warning: This Expert Advisor is optimized and requested for XAUUSD/GOLD trading!");
   }

   // Initialize EMA handle
   if(InpUseEMASupport)
   {
      m_ema_handle = iMA(_Symbol, _Period, InpEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
      if(m_ema_handle == INVALID_HANDLE)
      {
         Print("Error: Failed to create EMA indicator handle!");
      }
   }

   // Get initial bar time
   m_last_bar_time = GetTime(0);

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   // Release indicator handle
   if(m_ema_handle != INVALID_HANDLE)
   {
      IndicatorRelease(m_ema_handle);
      m_ema_handle = INVALID_HANDLE;
   }

   // Clear comments on chart
   Comment("");
   Print("EA Deinitialized. Reason code: ", reason);
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // 1. Detect New Bar and update setup lifetime
   datetime current_bar_time = GetTime(0);
   if(current_bar_time != m_last_bar_time)
   {
      m_last_bar_time = current_bar_time;

      // If we have an active setup, increment the bars passed
      if(m_setup_active)
      {
         m_setup_bars_passed++;
         if(m_setup_bars_passed >= InpMaxSetupLifeBars)
         {
            m_setup_active = false;
            PrintFormat("Setup Expired: Breakout did not occur within %d bars of the Red Candle at %s",
                        InpMaxSetupLifeBars, TimeToString(m_setup_bar_time));
         }
      }

      // Check for a new setup on the completed bar (Bar 1)
      CheckForNewSetup();
   }

   // 2. Monitor Live Tick breakout signals
   if(m_setup_active)
   {
      MonitorBreakoutAndExecute();
   }

   // 3. Update Visual Dashboard
   UpdateDashboard();
}

//+------------------------------------------------------------------+
//| Check the newly completed bar (Bar 1) for a valid setup         |
//+------------------------------------------------------------------+
void CheckForNewSetup()
{
   // Previous candle (Bar 1) must be RED (Close < Open)
   double open1 = GetOpen(1);
   double close1 = GetClose(1);
   double high1 = GetHigh(1);
   double low1 = GetLow(1);

   if(close1 >= open1)
   {
      // Not a red candle
      return;
   }

   PrintFormat("Evaluating completed Red Candle at %s: Open: %.2f, High: %.2f, Low: %.2f, Close: %.2f",
               TimeToString(GetTime(1)), open1, high1, low1, close1);

   // Ignore tiny candles
   if(IsTinyCandle(high1, low1))
   {
      return;
   }

   // Check Location Filters (Swing Low, Support, Day Low)
   bool swing_low_ok = false;
   bool support_ok = false;
   bool day_low_ok = false;

   if(InpUseSwingLowFilter)
   {
      swing_low_ok = CheckIsSwingLow(low1);
   }

   if(InpUseSupportFilter)
   {
      support_ok = CheckIsAtSupport(low1);
   }

   if(InpUseDayLowFilter)
   {
      day_low_ok = CheckIsAtDayLow(low1);
   }

   // Determine if we meet the "OR" relation requested (at swing low OR at support OR at day low)
   // If no filters are enabled, we treat it as valid.
   bool filter_enabled = InpUseSwingLowFilter || InpUseSupportFilter || InpUseDayLowFilter;
   bool filter_passed = !filter_enabled || swing_low_ok || support_ok || day_low_ok;

   if(filter_passed)
   {
      m_setup_active = true;
      m_setup_bar_time = GetTime(1);
      m_setup_high = high1;
      m_setup_low = low1;
      m_setup_bars_passed = 0;

      PrintFormat("=== VALID SETUP DETECTED ===");
      PrintFormat("Red Candle at %s meets criteria! Setup High: %.2f, Setup Low: %.2f",
                  TimeToString(m_setup_bar_time), m_setup_high, m_setup_low);
      PrintFormat("Checks status: Swing Low: %s, At Support: %s, Day Low: %s",
                  swing_low_ok ? "YES" : "NO", support_ok ? "YES" : "NO", day_low_ok ? "YES" : "NO");
      PrintFormat("Trigger Buy Price: %.2f", m_setup_high + InpEntryBufferPoints * _Point);
   }
}

//+------------------------------------------------------------------+
//| Monitor live price for breakout and execute trade                |
//+------------------------------------------------------------------+
void MonitorBreakoutAndExecute()
{
   // Ensure we only have one trade open at a time for this magic number/symbol
   if(GetActivePositionsCount() > 0)
   {
      return;
   }

   // Ensure we don't trade multiple times on the same bar
   if(GetTime(0) == m_last_entered_bar_time)
   {
      return;
   }

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double bar0_open = GetOpen(0);

   // Invalidate setup if price breaks below the red candle's low (structure violated)
   if(InpInvalidateOnLowBreak && bid < m_setup_low)
   {
      m_setup_active = false;
      PrintFormat("Setup Invalidated: Bid price (%.2f) broke below Red candle low (%.2f)", bid, m_setup_low);
      return;
   }

   // The current bar (Bar 0) must be Green (Ask > Bar 0 Open)
   bool is_green_candle = (ask > bar0_open);

   // Price must break above Red Candle High + Entry Buffer
   double trigger_price = m_setup_high + InpEntryBufferPoints * _Point;

   if(is_green_candle && ask >= trigger_price)
   {
      PrintFormat("Breakout Triggered! Current bar is green. Ask: %.2f >= Trigger Price: %.2f", ask, trigger_price);
      ExecuteBuyOrder(ask);
   }
}

//+------------------------------------------------------------------+
//| Execute BUY Order with correct SL, TP, and lot sizes             |
//+------------------------------------------------------------------+
void ExecuteBuyOrder(double entry_price)
{
   // Calculate Stop Loss
   double sl_price = 0.0;
   if(InpStopLossMode == SL_MODE_SIGNAL_LOW)
   {
      sl_price = m_setup_low - InpSLBufferPoints * _Point;
   }
   else if(InpStopLossMode == SL_MODE_FIXED_POINTS)
   {
      sl_price = entry_price - InpFixedSLPoints * _Point;
   }

   // Enforce broker Stops Level limitations
   double min_stop_level = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   if(entry_price - sl_price < min_stop_level)
   {
      sl_price = entry_price - min_stop_level - (10 * _Point);
      PrintFormat("SL adjusted to comply with Stops Level. New SL: %.2f", sl_price);
   }

   // Calculate Take Profit
   double tp_price = 0.0;
   if(InpTakeProfitPoints > 0)
   {
      tp_price = entry_price + InpTakeProfitPoints * _Point;
   }
   else
   {
      double sl_distance = entry_price - sl_price;
      tp_price = entry_price + (sl_distance * InpRiskRewardRatio);
   }

   if(tp_price - entry_price < min_stop_level)
   {
      tp_price = entry_price + min_stop_level + (10 * _Point);
      PrintFormat("TP adjusted to comply with Stops Level. New TP: %.2f", tp_price);
   }

   // Determine Lot Size
   double raw_lots = InpFixedLots;
   if(InpLotType == LOT_RISK_PCT)
   {
      double account_balance = AccountInfoDouble(ACCOUNT_BALANCE);
      double risk_amount = account_balance * (InpRiskPercent / 100.0);
      double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
      double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      double sl_distance_points = (entry_price - sl_price) / _Point;

      if(tick_value > 0 && tick_size > 0 && sl_distance_points > 0)
      {
         raw_lots = risk_amount / (sl_distance_points * (tick_value / tick_size) * _Point);
      }
      else
      {
         raw_lots = InpFixedLots;
         PrintFormat("Warning: Could not calculate risk-based lots, using fallback fixed lots (%.2f)", InpFixedLots);
      }
   }

   // Normalize Lot Size
   double final_lots = NormalizeLotSize(raw_lots);

   // Adjust for margin to prevent Code 10019
   final_lots = AdjustLotSizeForMargin(final_lots);

   // Verify filling mode
   m_trade.SetTypeFilling(GetFillingMode());

   PrintFormat("Sending BUY order: Symbol: %s, Volume: %.2f, Price: %.2f, SL: %.2f, TP: %.2f",
               _Symbol, final_lots, entry_price, sl_price, tp_price);

   if(m_trade.Buy(final_lots, _Symbol, entry_price, sl_price, tp_price, "Green Breakout Red Low"))
   {
      ulong retcode = m_trade.ResultRetcode();
      if(retcode == 10009 || retcode == 10008)
      {
         PrintFormat("BUY trade successfully executed! Ticket: %I64u", m_trade.ResultOrder());
         m_setup_active = false; // Deactivate setup
         m_last_entered_bar_time = GetTime(0); // Track entry bar
      }
      else
      {
         PrintFormat("Trade execution failed with Retcode: %I64u, Description: %s", retcode, m_trade.ResultRetcodeDescription());
      }
   }
   else
   {
      PrintFormat("OrderSend failed! Error details: %s", m_trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Get the count of active positions for this EA                    |
//+------------------------------------------------------------------+
int GetActivePositionsCount()
{
   int count = 0;
   int total = PositionsTotal();
   for(int i = total - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         {
            count++;
         }
      }
   }
   return count;
}

//+------------------------------------------------------------------+
//| Normalize Lot Size to comply with broker steps                   |
//+------------------------------------------------------------------+
double NormalizeLotSize(double lots)
{
   double lot_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   if(InpMinLotSizeOverride > 0.0)
   {
      min_lot = InpMinLotSizeOverride;
   }

   if(lot_step <= 0) lot_step = 0.01;

   double normalized = MathRound(lots / lot_step) * lot_step;

   if(normalized < min_lot) normalized = min_lot;
   if(normalized > max_lot) normalized = max_lot;

   return normalized;
}

//+------------------------------------------------------------------+
//| Evaluate required margin and scale lots if free margin is tight |
//+------------------------------------------------------------------+
double AdjustLotSizeForMargin(double requested_lots)
{
   double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double required_margin = 0.0;

   if(OrderCalcMargin(ORDER_TYPE_BUY, _Symbol, requested_lots, SymbolInfoDouble(_Symbol, SYMBOL_ASK), required_margin))
   {
      double max_margin_allowed = free_margin * (InpMaxMarginUtilizationPct / 100.0);
      if(required_margin > max_margin_allowed)
      {
         double scale_factor = max_margin_allowed / required_margin;
         double adjusted_lots = requested_lots * scale_factor;
         adjusted_lots = NormalizeLotSize(adjusted_lots);
         PrintFormat("Margin utilization warning: %.2f lots requires %.2f margin. Free margin: %.2f. Limit (%.1f%%): %.2f. Scaling down to %.2f lots.",
                     requested_lots, required_margin, free_margin, InpMaxMarginUtilizationPct, max_margin_allowed, adjusted_lots);
         return adjusted_lots;
      }
   }
   return requested_lots;
}

//+------------------------------------------------------------------+
//| Get compatible filling mode dynamically                           |
//+------------------------------------------------------------------+
uint GetFillingMode()
{
   uint filling = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0) return ORDER_FILLING_FOK;
   if((filling & SYMBOL_FILLING_IOC) != 0) return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
}

//+------------------------------------------------------------------+
//| Filter Check: Is Swing Low                                       |
//+------------------------------------------------------------------+
bool CheckIsSwingLow(double red_low)
{
   // Check if low is the lowest of the last N completed bars
   for(int i = 2; i <= InpSwingLookback + 1; i++)
   {
      double historical_low = GetLow(i);
      if(historical_low > 0 && historical_low < red_low)
      {
         return false;
      }
   }
   return true;
}

//+------------------------------------------------------------------+
//| Filter Check: Is At Support (Pivot Support, EMA, Horizontal)      |
//+------------------------------------------------------------------+
bool CheckIsAtSupport(double red_low)
{
   // 1. Pivot Point Support levels (S1, S2, S3)
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(_Symbol, PERIOD_D1, 1, 1, rates);
   if(copied > 0)
   {
      double high_d1 = rates[0].high;
      double low_d1 = rates[0].low;
      double close_d1 = rates[0].close;

      double pivot = (high_d1 + low_d1 + close_d1) / 3.0;
      double s1 = (2.0 * pivot) - high_d1;
      double s2 = pivot - (high_d1 - low_d1);
      double s3 = low_d1 - 2.0 * (high_d1 - pivot);

      double buffer = InpSupportBufferPoints * _Point;
      if(MathAbs(red_low - s1) <= buffer || MathAbs(red_low - s2) <= buffer ||
         MathAbs(red_low - s3) <= buffer || MathAbs(red_low - pivot) <= buffer)
      {
         PrintFormat("Support Match (Pivot): Red low is near Pivot Support level. S1: %.2f, S2: %.2f, S3: %.2f, Pivot: %.2f", s1, s2, s3, pivot);
         return true;
      }
   }

   // 2. Exponential Moving Average Support
   if(InpUseEMASupport && m_ema_handle != INVALID_HANDLE)
   {
      double ema_values[];
      ArraySetAsSeries(ema_values, true);
      if(CopyBuffer(m_ema_handle, 0, 1, 1, ema_values) > 0)
      {
         double ema_val = ema_values[0];
         double ema_buffer = InpEMABufferPoints * _Point;
         // Support check: low is touching, below, or close to the EMA line
         if(red_low <= ema_val + ema_buffer)
         {
            PrintFormat("Support Match (EMA): Red Low (%.2f) is close to EMA-%d (%.2f)", red_low, InpEMAPeriod, ema_val);
            return true;
         }
      }
   }

   // 3. Horizontal Support (Historical low of last N bars)
   if(InpUseHorizontalSupport)
   {
      double lowest_low = GetLowestLow(_Symbol, _Period, InpHorizontalSupportLookback, 2); // start from index 2 to exclude Bar 1
      if(lowest_low > 0)
      {
         double support_buffer = InpSupportBufferPoints * _Point;
         if(red_low <= lowest_low + support_buffer)
         {
            PrintFormat("Support Match (Horizontal): Red Low (%.2f) is near horizontal low (%.2f) of last %d bars",
                        red_low, lowest_low, InpHorizontalSupportLookback);
            return true;
         }
      }
   }

   return false;
}

//+------------------------------------------------------------------+
//| Filter Check: Is At Day Low                                      |
//+------------------------------------------------------------------+
bool CheckIsAtDayLow(double red_low)
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(_Symbol, PERIOD_D1, 0, 1, rates);
   if(copied > 0)
   {
      double day_low = rates[0].low;
      double buffer = InpDayLowBufferPoints * _Point;
      if(red_low <= day_low + buffer)
      {
         PrintFormat("Day Low Match: Red Low (%.2f) is near current day low (%.2f)", red_low, day_low);
         return true;
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Get lowest low dynamically using pure standard MQL5             |
//+------------------------------------------------------------------+
double GetLowestLow(string symbol, ENUM_TIMEFRAMES timeframe, int count, int start_index)
{
   double lowest_low = DBL_MAX;
   double low_array[];
   if(CopyLow(symbol, timeframe, start_index, count, low_array) > 0)
   {
      for(int i = 0; i < count; i++)
      {
         if(low_array[i] < lowest_low)
         {
            lowest_low = low_array[i];
         }
      }
   }
   return lowest_low;
}

//+------------------------------------------------------------------+
//| Filter out tiny candles                                          |
//+------------------------------------------------------------------+
bool IsTinyCandle(double high, double low)
{
   double range = high - low;
   double min_range = InpMinCandlePoints * _Point;
   if(range < min_range)
   {
      PrintFormat("Tiny Candle: Range (%.2f points / %.2f USD) is less than minimum (%.2f points)",
                  range / _Point, range, InpMinCandlePoints);
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Update on-chart comment dashboard                               |
//+------------------------------------------------------------------+
void UpdateDashboard()
{
   // Skip drawing in visual Strategy Tester if requested for speed
   if(MQLInfoInteger(MQL_TESTER) && !MQLInfoInteger(MQL_VISUAL_MODE))
      return;

   string text = StringFormat(
      "=== GREEN BREAKOUT RED LOW EA ===\n"
      "Symbol: %s | Timeframe: %s\n"
      "Positions: %d\n"
      "Setup Active: %s\n"
      "Last Signal Bar Time: %s\n"
      "Signal High: %.2f | Low: %.2f\n"
      "Trigger Price: %.2f\n"
      "Risk-Reward: 1:%.1f\n"
      "Min Candle Size: %.1f points\n"
      "================================",
      _Symbol, EnumToString(_Period), GetActivePositionsCount(),
      m_setup_active ? "YES" : "NO",
      m_setup_active ? TimeToString(m_setup_bar_time) : "N/A",
      m_setup_active ? m_setup_high : 0.0,
      m_setup_active ? m_setup_low : 0.0,
      m_setup_active ? (m_setup_high + InpEntryBufferPoints * _Point) : 0.0,
      InpRiskRewardRatio, InpMinCandlePoints
   );

   Comment(text);
}
//+------------------------------------------------------------------+
