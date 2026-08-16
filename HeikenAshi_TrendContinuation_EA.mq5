//+------------------------------------------------------------------+
//|                               HeikenAshi_TrendContinuation_EA.mq5|
//|                                  Copyright 2025                  |
//|                                                                  |
//| Multi-Timeframe Heiken Ashi Trend Continuation Strategy for MT5  |
//+------------------------------------------------------------------+
#property copyright "Copyright 2025"
#property link      ""
#property version   "1.00"
#property description "Fully automated MT5 Expert Advisor executing a non-repainting Heiken Ashi Trend Continuation strategy on a user-configurable timeframe with real market price execution and swing-based risk management."

#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\AccountInfo.mqh>

//+------------------------------------------------------------------+
//| Enumerations                                                     |
//+------------------------------------------------------------------+
enum ENUM_ENTRY_MODE
{
   ENTRY_MARKET_CLOSE      = 0, // Market Entry on Confirmation Candle Close
   ENTRY_BREAKOUT_HIGH_LOW = 1  // Breakout Entry Above/Below Confirmation Candle Real High/Low
};

enum ENUM_SIZING_MODE
{
   SIZING_FIXED_LOT    = 0, // Fixed Lot Size
   SIZING_RISK_PERCENT = 1  // Percentage Account Equity Risk Sizing
};

enum ENUM_SIGNAL_TYPE
{
   SIGNAL_NONE = 0,
   SIGNAL_BUY  = 1,
   SIGNAL_SELL = 2
};

//+------------------------------------------------------------------+
//| Structures                                                       |
//+------------------------------------------------------------------+
struct HeikenAshiCandle
{
   double open;
   double high;
   double low;
   double close;
   datetime time;
   bool is_bullish;
   bool is_bearish;
   double body_size;
   double total_range;
   double body_pct;
   double upper_wick_pct;
   double lower_wick_pct;
};

//+------------------------------------------------------------------+
//| Input Parameters                                                 |
//+------------------------------------------------------------------+
input group "=== General & Timeframe Settings ==="
input ENUM_TIMEFRAMES InpSignalTimeframe        = PERIOD_M15;    // Signal Timeframe (M1, M3, M5, M15, M30, H1, H4, D1)
input ulong           InpMagicNumber            = 888123;        // Magic Number
input string          InpTradeComment           = "HA_TrendCont"; // Trade Comment

input group "=== Step 1: Preceding Trend Settings ==="
input int             InpMinTrendCandles        = 3;             // Minimum Preceding Trend Candles (e.g. 3)
input double          InpMinTrendBodyPercent    = 30.0;          // Min Preceding HA Body % (0 = filter off)

input group "=== Step 2: Indecision Candle Settings ==="
input double          InpMaxIndecisionBodyPercent = 30.0;        // Max Indecision HA Body % (e.g. <= 30%)
input double          InpMinIndecisionUpperWickPct = 15.0;       // Min Indecision Upper Wick % (e.g. >= 15%)
input double          InpMinIndecisionLowerWickPct = 15.0;       // Min Indecision Lower Wick % (e.g. >= 15%)

input group "=== Step 3: Confirmation Candle Settings ==="
input double          InpMinConfirmationBodyPercent = 50.0;      // Min Confirmation HA Body % (e.g. >= 50%)
input double          InpMaxBullishLowerWickPercent = 10.0;      // Max Bullish Lower Wick % (e.g. <= 10%)
input double          InpMaxBearishUpperWickPercent = 10.0;      // Max Bearish Upper Wick % (e.g. <= 10%)
input double          InpMinHAConfirmationRangePoints = 0.0;     // Min Confirmation Candle Range in Points

input group "=== Step 4: Entry & Exit Management ==="
input ENUM_ENTRY_MODE InpEntryMode              = ENTRY_MARKET_CLOSE; // Entry Mode
input double          InpRiskReward             = 2.0;           // Risk-to-Reward Ratio (e.g. 1.5, 2.0, 3.0)
input int             InpSwingLookback          = 20;            // Swing High/Low Lookback Bars
input int             InpSwingStrength          = 2;             // Swing Pivot Strength (Bars Left & Right)
input int             InpSLBufferPoints         = 20;            // Stop Loss Buffer in Points
input int             InpMinSLPoints            = 50;            // Minimum Stop Loss Distance in Points

input group "=== Risk & Position Sizing ==="
input ENUM_SIZING_MODE InpSizingMode            = SIZING_RISK_PERCENT; // Position Sizing Mode
input double          InpFixedLot               = 0.01;          // Fixed Lot Size
input double          InpRiskPercent            = 1.0;           // Account Risk % per Trade
input bool            InpOnePositionPerSymbol   = true;          // Limit to 1 Position Per Symbol
input bool            InpOnePositionAtATime     = false;         // Limit to 1 Position Account-Wide

input group "=== Optional Strategy Filters ==="
input bool            InpUseEMAFilter           = false;         // Enable EMA Trend Filter
input int             InpEMAPeriod              = 50;            // EMA Period
input ENUM_APPLIED_PRICE InpEMAAppliedPrice     = PRICE_CLOSE;   // EMA Applied Price
input bool            InpUseATRFilter           = false;         // Enable ATR Volatility Filter
input int             InpATRPeriod              = 14;            // ATR Period
input double          InpMinATRPoints           = 0.0;           // Minimum ATR Range in Points
input double          InpMaxATRPoints           = 10000.0;       // Maximum ATR Range in Points
input bool            InpUseSpreadFilter        = true;          // Enable Max Spread Filter
input int             InpMaxSpreadPoints        = 50;            // Maximum Spread in Points
input bool            InpUseSessionFilter       = false;         // Enable Trading Session Filter
input string          InpSessionStart           = "08:00";       // Session Start Time (HH:MM)
input string          InpSessionEnd             = "20:00";       // Session End Time (HH:MM)

input group "=== Visuals & Dashboard ==="
input bool            InpShowSignals            = true;          // Show Entry Arrows & Signals on Chart
input bool            InpShowSLTP               = true;          // Show SL / TP Target Lines
input bool            InpShowDashboard          = true;          // Show On-Chart Information Panel

input group "=== Notification & Alerts ==="
input bool            InpEnableAlerts           = true;          // Enable Terminal Popup Alerts
input bool            InpEnablePushNotification = false;         // Enable Mobile Push Notifications
input bool            InpEnableSoundAlert       = false;         // Enable Sound Alerts
input bool            InpEnableEmailAlert       = false;         // Enable Email Notifications

//+------------------------------------------------------------------+
//| Global Variables & Objects                                       |
//+------------------------------------------------------------------+
CTrade         m_trade;
CSymbolInfo    m_symbol;
CAccountInfo   m_account;

datetime       m_last_bar_time           = 0;
bool           m_pending_breakout        = false;
ENUM_SIGNAL_TYPE m_breakout_type         = SIGNAL_NONE;
double         m_breakout_trigger_price  = 0.0;
datetime       m_breakout_expiry_time    = 0;
double         m_breakout_swing_sl_price = 0.0;

int            m_ema_handle              = INVALID_HANDLE;
int            m_atr_handle              = INVALID_HANDLE;

ENUM_SIGNAL_TYPE m_last_signal_type      = SIGNAL_NONE;
datetime       m_last_signal_time        = 0;
double         m_last_entry_price        = 0.0;
double         m_last_sl_price           = 0.0;
double         m_last_tp_price           = 0.0;
string         m_status_reason           = "Initialized";

//+------------------------------------------------------------------+
//| Expert Initialization Function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   if (!m_symbol.Name(_Symbol))
   {
      Print("[EA] Error initializing CSymbolInfo for symbol: ", _Symbol);
      return INIT_FAILED;
   }
   m_symbol.Refresh();

   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_trade.SetMarginMode();

   // SetFillingType safely
   ENUM_ORDER_TYPE_FILLING filling = GetExecutionFillingMode();
   m_trade.SetTypeFilling(filling);

   // Initialize Indicator Handles if enabled
   if (InpUseEMAFilter)
   {
      m_ema_handle = iMA(_Symbol, InpSignalTimeframe, InpEMAPeriod, 0, MODE_EMA, InpEMAAppliedPrice);
      if (m_ema_handle == INVALID_HANDLE)
      {
         Print("[EA] Error creating EMA indicator handle.");
         return INIT_FAILED;
      }
   }

   if (InpUseATRFilter)
   {
      m_atr_handle = iATR(_Symbol, InpSignalTimeframe, InpATRPeriod);
      if (m_atr_handle == INVALID_HANDLE)
      {
         Print("[EA] Error creating ATR indicator handle.");
         return INIT_FAILED;
      }
   }

   m_last_bar_time = 0;
   m_pending_breakout = false;
   m_status_reason = "Ready";

   Print("[EA] Initialized successfully. Signal Timeframe: ", EnumToString(InpSignalTimeframe), " Magic: ", InpMagicNumber);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert Deinitialization Function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if (m_ema_handle != INVALID_HANDLE) IndicatorRelease(m_ema_handle);
   if (m_atr_handle != INVALID_HANDLE) IndicatorRelease(m_atr_handle);

   if (InpShowDashboard) DeleteDashboard();
   if (InpShowSignals) ObjectsDeleteAll(0, "HA_EA_Sig_");
   if (InpShowSLTP) ObjectsDeleteAll(0, "HA_EA_Line_");
}

//+------------------------------------------------------------------+
//| Expert Tick Function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   if (!m_symbol.RefreshRates()) return;

   // Handle pending breakout monitoring tick-by-tick
   if (m_pending_breakout)
   {
      ProcessPendingBreakout();
   }

   // Check for new closed candle on Signal Timeframe
   datetime current_bar_time = iTime(_Symbol, InpSignalTimeframe, 0);
   if (current_bar_time == 0) return;

   if (current_bar_time != m_last_bar_time)
   {
      m_last_bar_time = current_bar_time;
      OnNewBar();
   }

   // Update Dashboard Visuals
   if (InpShowDashboard && (!MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_VISUAL_MODE)))
   {
      UpdateDashboard();
   }
}

//+------------------------------------------------------------------+
//| Logic Executed Once Per New Closed Signal Timeframe Bar          |
//+------------------------------------------------------------------+
void OnNewBar()
{
   // Reset pending breakout if a new bar started and breakout wasn't hit
   m_pending_breakout = false;

   // Validate general trade permission and session filters
   if (!CheckTradePermissions()) return;

   // Read Heiken Ashi Series for signal evaluation
   int total_ha_bars = MathMax(100, InpMinTrendCandles + InpSwingLookback + 10);
   HeikenAshiCandle ha[];
   if (!GetHeikenAshiSeries(InpSignalTimeframe, total_ha_bars, ha))
   {
      m_status_reason = "HA Data Fetch Failed";
      return;
   }

   // Check filters
   if (!PassesATRFilter())
   {
      m_status_reason = "ATR Filter Blocked";
      return;
   }

   if (!PassesSpreadFilter())
   {
      m_status_reason = "High Spread Blocked";
      return;
   }

   if (!PassesSessionFilter())
   {
      m_status_reason = "Outside Trading Session";
      return;
   }

   // Evaluate Long and Short Setups
   bool buy_setup  = EvaluateLongSetup(ha);
   bool sell_setup = EvaluateShortSetup(ha);

   if (buy_setup && PassesEMAFilter(SIGNAL_BUY))
   {
      m_status_reason = "BUY Signal Detected";
      HandleSignal(SIGNAL_BUY);
   }
   else if (sell_setup && PassesEMAFilter(SIGNAL_SELL))
   {
      m_status_reason = "SELL Signal Detected";
      HandleSignal(SIGNAL_SELL);
   }
   else
   {
      m_status_reason = "Searching for Setups";
   }
}

//+------------------------------------------------------------------+
//| Evaluate LONG Setup Conditions                                   |
//+------------------------------------------------------------------+
bool EvaluateLongSetup(const HeikenAshiCandle &ha[])
{
   if (ArraySize(ha) < 3 + InpMinTrendCandles) return false;

   // Index 1 = Confirmation Candle (most recently completed candle)
   // Index 2 = Indecision Candle
   // Index 3 .. (2 + InpMinTrendCandles) = Preceding Trend Candles

   // Step 3: Bullish Confirmation Candle
   if (!ha[1].is_bullish) return false;
   if (ha[1].lower_wick_pct > InpMaxBullishLowerWickPercent) return false;
   if (ha[1].body_pct < InpMinConfirmationBodyPercent) return false;
   if ((ha[1].total_range / _Point) < InpMinHAConfirmationRangePoints) return false;

   // Step 2: Indecision Candle
   if (ha[2].body_pct > InpMaxIndecisionBodyPercent) return false;
   if (ha[2].upper_wick_pct < InpMinIndecisionUpperWickPct) return false;
   if (ha[2].lower_wick_pct < InpMinIndecisionLowerWickPct) return false;

   // Step 1: Preceding Bullish Trend
   for (int i = 3; i < 3 + InpMinTrendCandles; i++)
   {
      if (!ha[i].is_bullish) return false;
      if (InpMinTrendBodyPercent > 0.0 && ha[i].body_pct < InpMinTrendBodyPercent) return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| Evaluate SHORT Setup Conditions                                  |
//+------------------------------------------------------------------+
bool EvaluateShortSetup(const HeikenAshiCandle &ha[])
{
   if (ArraySize(ha) < 3 + InpMinTrendCandles) return false;

   // Index 1 = Confirmation Candle (most recently completed candle)
   // Index 2 = Indecision Candle
   // Index 3 .. (2 + InpMinTrendCandles) = Preceding Trend Candles

   // Step 3: Bearish Confirmation Candle
   if (!ha[1].is_bearish) return false;
   if (ha[1].upper_wick_pct > InpMaxBearishUpperWickPercent) return false;
   if (ha[1].body_pct < InpMinConfirmationBodyPercent) return false;
   if ((ha[1].total_range / _Point) < InpMinHAConfirmationRangePoints) return false;

   // Step 2: Indecision Candle
   if (ha[2].body_pct > InpMaxIndecisionBodyPercent) return false;
   if (ha[2].upper_wick_pct < InpMinIndecisionUpperWickPct) return false;
   if (ha[2].lower_wick_pct < InpMinIndecisionLowerWickPct) return false;

   // Step 1: Preceding Bearish Trend
   for (int i = 3; i < 3 + InpMinTrendCandles; i++)
   {
      if (!ha[i].is_bearish) return false;
      if (InpMinTrendBodyPercent > 0.0 && ha[i].body_pct < InpMinTrendBodyPercent) return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| Signal Execution Router                                          |
//+------------------------------------------------------------------+
void HandleSignal(ENUM_SIGNAL_TYPE signal)
{
   // Check position rules
   if (InpOnePositionPerSymbol && CountPositions(true) > 0)
   {
      m_status_reason = "Blocked: Symbol Position Exists";
      return;
   }
   if (InpOnePositionAtATime && CountPositions(false) > 0)
   {
      m_status_reason = "Blocked: Account Position Exists";
      return;
   }

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if (CopyRates(_Symbol, InpSignalTimeframe, 0, 5, rates) < 5) return;

   m_last_signal_type = signal;
   m_last_signal_time = TimeCurrent();

   if (InpShowSignals) DrawSignalArrow(signal, rates[1].time, (signal == SIGNAL_BUY) ? rates[1].low : rates[1].high);

   if (InpEntryMode == ENTRY_MARKET_CLOSE)
   {
      ExecuteMarketEntry(signal);
   }
   else if (InpEntryMode == ENTRY_BREAKOUT_HIGH_LOW)
   {
      m_pending_breakout = true;
      m_breakout_type = signal;
      m_breakout_expiry_time = rates[0].time + PeriodSeconds(InpSignalTimeframe);

      if (signal == SIGNAL_BUY)
      {
         m_breakout_trigger_price  = rates[1].high; // Real Market High of Confirmation Bar
         m_breakout_swing_sl_price = GetSwingLow(InpSignalTimeframe, InpSwingLookback, InpSwingStrength);
      }
      else
      {
         m_breakout_trigger_price  = rates[1].low;  // Real Market Low of Confirmation Bar
         m_breakout_swing_sl_price = GetSwingHigh(InpSignalTimeframe, InpSwingLookback, InpSwingStrength);
      }
      m_status_reason = "Breakout Setup Pending";
      SendEAAlert("HA Trend Continuation: Pending Breakout Signal Detected for " + _Symbol);
   }
}

//+------------------------------------------------------------------+
//| Execute Immediate Market Entry                                   |
//+------------------------------------------------------------------+
void ExecuteMarketEntry(ENUM_SIGNAL_TYPE signal)
{
   double ask = m_symbol.Ask();
   double bid = m_symbol.Bid();

   double entry_price = (signal == SIGNAL_BUY) ? ask : bid;
   double sl_price    = 0.0;
   double tp_price    = 0.0;

   if (signal == SIGNAL_BUY)
   {
      double swing_low = GetSwingLow(InpSignalTimeframe, InpSwingLookback, InpSwingStrength);
      sl_price = NormalizeDouble(swing_low - InpSLBufferPoints * _Point, _Digits);

      // Enforce minimum SL distance
      double min_sl_dist = InpMinSLPoints * _Point;
      if (entry_price - sl_price < min_sl_dist) sl_price = NormalizeDouble(entry_price - min_sl_dist, _Digits);

      double risk = entry_price - sl_price;
      tp_price = NormalizeDouble(entry_price + (risk * InpRiskReward), _Digits);
   }
   else // SIGNAL_SELL
   {
      double swing_high = GetSwingHigh(InpSignalTimeframe, InpSwingLookback, InpSwingStrength);
      sl_price = NormalizeDouble(swing_high + InpSLBufferPoints * _Point, _Digits);

      // Enforce minimum SL distance
      double min_sl_dist = InpMinSLPoints * _Point;
      if (sl_price - entry_price < min_sl_dist) sl_price = NormalizeDouble(entry_price + min_sl_dist, _Digits);

      double risk = sl_price - entry_price;
      tp_price = NormalizeDouble(entry_price - (risk * InpRiskReward), _Digits);
   }

   // Ensure broker Stop Level distance
   long stop_level_pts = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double stop_level_dist = stop_level_pts * _Point;

   if (signal == SIGNAL_BUY)
   {
      if (entry_price - sl_price < stop_level_dist) sl_price = NormalizeDouble(entry_price - stop_level_dist, _Digits);
      if (tp_price - entry_price < stop_level_dist) tp_price = NormalizeDouble(entry_price + stop_level_dist, _Digits);
   }
   else
   {
      if (sl_price - entry_price < stop_level_dist) sl_price = NormalizeDouble(entry_price + stop_level_dist, _Digits);
      if (entry_price - tp_price < stop_level_dist) tp_price = NormalizeDouble(entry_price - stop_level_dist, _Digits);
   }

   double lot = CalculateLotSize(entry_price, sl_price);
   if (lot <= 0)
   {
      m_status_reason = "Invalid Calculated Lot Size";
      return;
   }

   bool success = false;
   if (signal == SIGNAL_BUY)
      success = m_trade.Buy(lot, _Symbol, entry_price, sl_price, tp_price, InpTradeComment);
   else
      success = m_trade.Sell(lot, _Symbol, entry_price, sl_price, tp_price, InpTradeComment);

   if (success)
   {
      m_last_entry_price = entry_price;
      m_last_sl_price    = sl_price;
      m_last_tp_price    = tp_price;
      m_status_reason    = "Trade Executed";

      if (InpShowSLTP) DrawSLTPLines(entry_price, sl_price, tp_price);
      SendEAAlert(StringFormat("HA EA: %s Entry Executed @ %.5f, SL: %.5f, TP: %.5f", (signal == SIGNAL_BUY ? "BUY" : "SELL"), entry_price, sl_price, tp_price));
   }
   else
   {
      m_status_reason = StringFormat("OrderSend Error: %d", m_trade.ResultRetcode());
      Print("[EA] Trade execution failed. RetCode: ", m_trade.ResultRetcode(), " Comment: ", m_trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Monitor Tick-By-Tick Pending Breakout Entries                   |
//+------------------------------------------------------------------+
void ProcessPendingBreakout()
{
   if (TimeCurrent() >= m_breakout_expiry_time)
   {
      m_pending_breakout = false;
      m_status_reason    = "Breakout Expired";
      return;
   }

   double ask = m_symbol.Ask();
   double bid = m_symbol.Bid();

   if (m_breakout_type == SIGNAL_BUY && ask >= m_breakout_trigger_price)
   {
      m_pending_breakout = false;
      ExecuteBreakoutMarketOrder(SIGNAL_BUY, ask, m_breakout_swing_sl_price);
   }
   else if (m_breakout_type == SIGNAL_SELL && bid <= m_breakout_trigger_price)
   {
      m_pending_breakout = false;
      ExecuteBreakoutMarketOrder(SIGNAL_SELL, bid, m_breakout_swing_sl_price);
   }
}

//+------------------------------------------------------------------+
//| Execute Market Order for Breakout Confirmation                   |
//+------------------------------------------------------------------+
void ExecuteBreakoutMarketOrder(ENUM_SIGNAL_TYPE signal, double execution_price, double swing_sl)
{
   double sl_price = 0.0;
   double tp_price = 0.0;

   if (signal == SIGNAL_BUY)
   {
      sl_price = NormalizeDouble(swing_sl - InpSLBufferPoints * _Point, _Digits);
      double min_sl_dist = InpMinSLPoints * _Point;
      if (execution_price - sl_price < min_sl_dist) sl_price = NormalizeDouble(execution_price - min_sl_dist, _Digits);

      double risk = execution_price - sl_price;
      tp_price = NormalizeDouble(execution_price + (risk * InpRiskReward), _Digits);
   }
   else
   {
      sl_price = NormalizeDouble(swing_sl + InpSLBufferPoints * _Point, _Digits);
      double min_sl_dist = InpMinSLPoints * _Point;
      if (sl_price - execution_price < min_sl_dist) sl_price = NormalizeDouble(execution_price + min_sl_dist, _Digits);

      double risk = sl_price - execution_price;
      tp_price = NormalizeDouble(execution_price - (risk * InpRiskReward), _Digits);
   }

   // Ensure broker Stop Level distance
   long stop_level_pts = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double stop_level_dist = stop_level_pts * _Point;

   if (signal == SIGNAL_BUY)
   {
      if (execution_price - sl_price < stop_level_dist) sl_price = NormalizeDouble(execution_price - stop_level_dist, _Digits);
      if (tp_price - execution_price < stop_level_dist) tp_price = NormalizeDouble(execution_price + stop_level_dist, _Digits);
   }
   else
   {
      if (sl_price - execution_price < stop_level_dist) sl_price = NormalizeDouble(execution_price + stop_level_dist, _Digits);
      if (execution_price - tp_price < stop_level_dist) tp_price = NormalizeDouble(execution_price - stop_level_dist, _Digits);
   }

   double lot = CalculateLotSize(execution_price, sl_price);
   if (lot <= 0)
   {
      m_status_reason = "Invalid Lot Size for Breakout";
      return;
   }

   bool success = false;
   if (signal == SIGNAL_BUY)
      success = m_trade.Buy(lot, _Symbol, execution_price, sl_price, tp_price, InpTradeComment);
   else
      success = m_trade.Sell(lot, _Symbol, execution_price, sl_price, tp_price, InpTradeComment);

   if (success)
   {
      m_last_entry_price = execution_price;
      m_last_sl_price    = sl_price;
      m_last_tp_price    = tp_price;
      m_status_reason    = "Breakout Order Executed";

      if (InpShowSLTP) DrawSLTPLines(execution_price, sl_price, tp_price);
      SendEAAlert(StringFormat("HA EA: Breakout %s Executed @ %.5f, SL: %.5f, TP: %.5f", (signal == SIGNAL_BUY ? "BUY" : "SELL"), execution_price, sl_price, tp_price));
   }
   else
   {
      m_status_reason = StringFormat("Breakout Order Error: %d", m_trade.ResultRetcode());
   }
}

//+------------------------------------------------------------------+
//| Calculate Non-Repainting Heiken Ashi Series                      |
//+------------------------------------------------------------------+
bool GetHeikenAshiSeries(ENUM_TIMEFRAMES tf, int count, HeikenAshiCandle &ha[])
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);

   int copied = CopyRates(_Symbol, tf, 0, count, rates);
   if (copied < count) return false;

   ArrayResize(ha, count);
   ArraySetAsSeries(ha, true);

   // Calculate sequentially from oldest bar (index count - 1) to newest (index 0)
   for (int i = count - 1; i >= 0; i--)
   {
      double ha_close = (rates[i].open + rates[i].high + rates[i].low + rates[i].close) / 4.0;
      double ha_open  = 0.0;

      if (i == count - 1)
      {
         ha_open = (rates[i].open + rates[i].close) / 2.0;
      }
      else
      {
         ha_open = (ha[i + 1].open + ha[i + 1].close) / 2.0;
      }

      double ha_high = MathMax(rates[i].high, MathMax(ha_open, ha_close));
      double ha_low  = MathMin(rates[i].low, MathMin(ha_open, ha_close));

      ha[i].open  = ha_open;
      ha[i].high  = ha_high;
      ha[i].low   = ha_low;
      ha[i].close = ha_close;
      ha[i].time  = rates[i].time;

      ha[i].is_bullish = (ha_close > ha_open);
      ha[i].is_bearish = (ha_close < ha_open);

      ha[i].total_range = ha_high - ha_low;
      ha[i].body_size   = MathAbs(ha_close - ha_open);

      if (ha[i].total_range > 0.0)
      {
         ha[i].body_pct       = (ha[i].body_size / ha[i].total_range) * 100.0;
         ha[i].upper_wick_pct = ((ha_high - MathMax(ha_open, ha_close)) / ha[i].total_range) * 100.0;
         ha[i].lower_wick_pct = ((MathMin(ha_open, ha_close) - ha_low) / ha[i].total_range) * 100.0;
      }
      else
      {
         ha[i].body_pct       = 0.0;
         ha[i].upper_wick_pct = 0.0;
         ha[i].lower_wick_pct = 0.0;
      }
   }

   return true;
}

//+------------------------------------------------------------------+
//| Find Confirmed Non-Repainting Swing Low                          |
//+------------------------------------------------------------------+
double GetSwingLow(ENUM_TIMEFRAMES tf, int lookback, int strength)
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);

   int total_needed = lookback + strength + 5;
   if (CopyRates(_Symbol, tf, 0, total_needed, rates) < total_needed)
      return m_symbol.Bid();

   for (int i = strength + 1; i <= lookback + strength; i++)
   {
      bool is_swing = true;
      double candidate = rates[i].low;

      for (int j = 1; j <= strength; j++)
      {
         if (rates[i + j].low < candidate) { is_swing = false; break; }
      }
      if (!is_swing) continue;

      for (int j = 1; j <= strength; j++)
      {
         if (rates[i - j].low < candidate) { is_swing = false; break; }
      }

      if (is_swing) return candidate;
   }

   // Fallback: lowest low of recent lookback bars
   double lowest = rates[1].low;
   for (int i = 2; i <= lookback; i++)
   {
      if (rates[i].low < lowest) lowest = rates[i].low;
   }
   return lowest;
}

//+------------------------------------------------------------------+
//| Find Confirmed Non-Repainting Swing High                         |
//+------------------------------------------------------------------+
double GetSwingHigh(ENUM_TIMEFRAMES tf, int lookback, int strength)
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);

   int total_needed = lookback + strength + 5;
   if (CopyRates(_Symbol, tf, 0, total_needed, rates) < total_needed)
      return m_symbol.Ask();

   for (int i = strength + 1; i <= lookback + strength; i++)
   {
      bool is_swing = true;
      double candidate = rates[i].high;

      for (int j = 1; j <= strength; j++)
      {
         if (rates[i + j].high > candidate) { is_swing = false; break; }
      }
      if (!is_swing) continue;

      for (int j = 1; j <= strength; j++)
      {
         if (rates[i - j].high > candidate) { is_swing = false; break; }
      }

      if (is_swing) return candidate;
   }

   // Fallback: highest high of recent lookback bars
   double highest = rates[1].high;
   for (int i = 2; i <= lookback; i++)
   {
      if (rates[i].high > highest) highest = rates[i].high;
   }
   return highest;
}

//+------------------------------------------------------------------+
//| Dynamic Position Lot Size Calculation                            |
//+------------------------------------------------------------------+
double CalculateLotSize(double entry_price, double sl_price)
{
   if (InpSizingMode == SIZING_FIXED_LOT)
      return NormalizeLotSize(InpFixedLot);

   double sl_dist_pts = MathAbs(entry_price - sl_price) / _Point;
   if (sl_dist_pts <= 0) return NormalizeLotSize(InpFixedLot);

   double equity = m_account.Equity();
   double risk_amount = equity * (InpRiskPercent / 100.0);

   double tick_value = m_symbol.TickValue();
   double tick_size  = m_symbol.TickSize();

   if (tick_value <= 0 || tick_size <= 0) return NormalizeLotSize(InpFixedLot);

   double loss_per_lot = (sl_dist_pts * _Point / tick_size) * tick_value;
   if (loss_per_lot <= 0) return NormalizeLotSize(InpFixedLot);

   double calculated_lots = risk_amount / loss_per_lot;

   // Margin Utilization Safety Check
   double margin_req = 0.0;
   if (OrderCalcMargin(ORDER_TYPE_BUY, _Symbol, calculated_lots, entry_price, margin_req) && margin_req > 0)
   {
      double free_margin = m_account.FreeMargin();
      if (margin_req > free_margin * 0.90)
      {
         calculated_lots = calculated_lots * (free_margin * 0.90 / margin_req);
      }
   }

   return NormalizeLotSize(calculated_lots);
}

//+------------------------------------------------------------------+
//| Normalize Volume to Broker Lot Limits                            |
//+------------------------------------------------------------------+
double NormalizeLotSize(double lot)
{
   double min_lot  = m_symbol.LotsMin();
   double max_lot  = m_symbol.LotsMax();
   double lot_step = m_symbol.LotsStep();

   if (lot_step <= 0) lot_step = 0.01;

   double normalized = MathFloor(lot / lot_step) * lot_step;
   if (normalized < min_lot) normalized = min_lot;
   if (normalized > max_lot) normalized = max_lot;

   return NormalizeDouble(normalized, 2);
}

//+------------------------------------------------------------------+
//| Count Active Positions                                           |
//+------------------------------------------------------------------+
int CountPositions(bool current_symbol_only)
{
   int count = 0;
   for (int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if (ticket <= 0) continue;

      if (PositionGetInteger(POSITION_MAGIC) != (long)InpMagicNumber) continue;
      if (current_symbol_only && PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      count++;
   }
   return count;
}

//+------------------------------------------------------------------+
//| Filter Validations                                               |
//+------------------------------------------------------------------+
bool PassesEMAFilter(ENUM_SIGNAL_TYPE signal)
{
   if (!InpUseEMAFilter || m_ema_handle == INVALID_HANDLE) return true;

   double ema[];
   ArraySetAsSeries(ema, true);
   if (CopyBuffer(m_ema_handle, 0, 1, 1, ema) < 1) return false;

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if (CopyRates(_Symbol, InpSignalTimeframe, 1, 1, rates) < 1) return false;

   double close = rates[0].close;

   if (signal == SIGNAL_BUY)
      return (close > ema[0]);
   else if (signal == SIGNAL_SELL)
      return (close < ema[0]);

   return true;
}

bool PassesATRFilter()
{
   if (!InpUseATRFilter || m_atr_handle == INVALID_HANDLE) return true;

   double atr[];
   ArraySetAsSeries(atr, true);
   if (CopyBuffer(m_atr_handle, 0, 1, 1, atr) < 1) return false;

   double atr_pts = atr[0] / _Point;
   return (atr_pts >= InpMinATRPoints && atr_pts <= InpMaxATRPoints);
}

bool PassesSpreadFilter()
{
   if (!InpUseSpreadFilter) return true;
   long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   return (spread <= InpMaxSpreadPoints);
}

bool PassesSessionFilter()
{
   if (!InpUseSessionFilter) return true;

   datetime now = TimeCurrent();
   MqlDateTime dt;
   TimeToStruct(now, dt);

   string current_time_str = StringFormat("%02d:%02d", dt.hour, dt.min);
   return (current_time_str >= InpSessionStart && current_time_str <= InpSessionEnd);
}

bool CheckTradePermissions()
{
   if (MQLInfoInteger(MQL_TESTER)) return true;

   if (!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) return false;
   if (!MQLInfoInteger(MQL_TRADE_ALLOWED)) return false;

   ENUM_SYMBOL_TRADE_MODE trade_mode = (ENUM_SYMBOL_TRADE_MODE)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE);
   if (trade_mode != SYMBOL_TRADE_MODE_FULL) return false;

   return true;
}

ENUM_ORDER_TYPE_FILLING GetExecutionFillingMode()
{
   uint filling = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if ((filling & SYMBOL_FILLING_FOK) != 0) return ORDER_FILLING_FOK;
   if ((filling & SYMBOL_FILLING_IOC) != 0) return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
}

//+------------------------------------------------------------------+
//| Chart Visuals & Annotations                                      |
//+------------------------------------------------------------------+
void DrawSignalArrow(ENUM_SIGNAL_TYPE signal, datetime bar_time, double price)
{
   string name = "HA_EA_Sig_" + IntegerToString(bar_time);
   ObjectDelete(0, name);

   if (signal == SIGNAL_BUY)
   {
      ObjectCreate(0, name, OBJ_ARROW_BUY, 0, bar_time, price - 10 * _Point);
      ObjectSetInteger(0, name, OBJPROP_COLOR, clrDodgerBlue);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, 2);
   }
   else if (signal == SIGNAL_SELL)
   {
      ObjectCreate(0, name, OBJ_ARROW_SELL, 0, bar_time, price + 10 * _Point);
      ObjectSetInteger(0, name, OBJPROP_COLOR, clrRed);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, 2);
   }
}

void DrawSLTPLines(double entry, double sl, double tp)
{
   string prefix = "HA_EA_Line_";
   ObjectDelete(0, prefix + "Entry");
   ObjectDelete(0, prefix + "SL");
   ObjectDelete(0, prefix + "TP");

   ObjectCreate(0, prefix + "Entry", OBJ_HLINE, 0, 0, entry);
   ObjectSetInteger(0, prefix + "Entry", OBJPROP_COLOR, clrYellow);
   ObjectSetInteger(0, prefix + "Entry", OBJPROP_STYLE, STYLE_SOLID);

   ObjectCreate(0, prefix + "SL", OBJ_HLINE, 0, 0, sl);
   ObjectSetInteger(0, prefix + "SL", OBJPROP_COLOR, clrRed);
   ObjectSetInteger(0, prefix + "SL", OBJPROP_STYLE, STYLE_DASH);

   ObjectCreate(0, prefix + "TP", OBJ_HLINE, 0, 0, tp);
   ObjectSetInteger(0, prefix + "TP", OBJPROP_COLOR, clrLime);
   ObjectSetInteger(0, prefix + "TP", OBJPROP_STYLE, STYLE_DASH);
}

//+------------------------------------------------------------------+
//| Dashboard Information Panel                                      |
//+------------------------------------------------------------------+
void UpdateDashboard()
{
   string panel_bg = "HA_EA_Dash_BG";
   if (ObjectFind(0, panel_bg) < 0)
   {
      ObjectCreate(0, panel_bg, OBJ_RECTANGLE_LABEL, 0, 0, 0);
      ObjectSetInteger(0, panel_bg, OBJPROP_XDISTANCE, 15);
      ObjectSetInteger(0, panel_bg, OBJPROP_YDISTANCE, 25);
      ObjectSetInteger(0, panel_bg, OBJPROP_XSIZE, 320);
      ObjectSetInteger(0, panel_bg, OBJPROP_YSIZE, 240);
      ObjectSetInteger(0, panel_bg, OBJPROP_BGCOLOR, clrDarkSlateGray);
      ObjectSetInteger(0, panel_bg, OBJPROP_BORDER_TYPE, BORDER_FLAT);
      ObjectSetInteger(0, panel_bg, OBJPROP_COLOR, clrLightGray);
      ObjectSetInteger(0, panel_bg, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   }

   // Fetch current HA bar state for display
   HeikenAshiCandle ha[];
   GetHeikenAshiSeries(InpSignalTimeframe, 5, ha);
   string ha_trend_str = "NEUTRAL";
   if (ArraySize(ha) >= 2)
   {
      ha_trend_str = ha[1].is_bullish ? "BULLISH 🟢" : (ha[1].is_bearish ? "BEARISH 🔴" : "NEUTRAL ⚪");
   }

   long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   double atr_val = 0.0;
   if (m_atr_handle != INVALID_HANDLE)
   {
      double atr_buf[];
      if (CopyBuffer(m_atr_handle, 0, 0, 1, atr_buf) > 0) atr_val = atr_buf[0] / _Point;
   }

   string lines[10];
   lines[0] = "=== HA TREND CONTINUATION EA ===";
   lines[1] = "Signal TF: " + EnumToString(InpSignalTimeframe);
   lines[2] = "Current HA Trend: " + ha_trend_str;
   lines[3] = "EA Status: " + m_status_reason;
   lines[4] = "Risk:Reward Target: 1:" + DoubleToString(InpRiskReward, 1);
   lines[5] = "Current Spread: " + IntegerToString(spread) + " pts";
   lines[6] = "Current ATR: " + DoubleToString(atr_val, 1) + " pts";
   lines[7] = "Active Positions: " + IntegerToString(CountPositions(true));
   lines[8] = "Last Entry: " + (m_last_entry_price > 0 ? DoubleToString(m_last_entry_price, _Digits) : "None");
   lines[9] = "Last SL: " + (m_last_sl_price > 0 ? DoubleToString(m_last_sl_price, _Digits) : "None");

   for (int i = 0; i < 10; i++)
   {
      string lbl_name = "HA_EA_Dash_Lbl_" + IntegerToString(i);
      if (ObjectFind(0, lbl_name) < 0)
      {
         ObjectCreate(0, lbl_name, OBJ_LABEL, 0, 0, 0);
         ObjectSetInteger(0, lbl_name, OBJPROP_XDISTANCE, 25);
         ObjectSetInteger(0, lbl_name, OBJPROP_YDISTANCE, 35 + (i * 20));
         ObjectSetInteger(0, lbl_name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
         ObjectSetInteger(0, lbl_name, OBJPROP_FONTSIZE, 9);
         ObjectSetInteger(0, lbl_name, OBJPROP_FONT, "Trebuchet MS");
      }
      ObjectSetString(0, lbl_name, OBJPROP_TEXT, lines[i]);
      ObjectSetInteger(0, lbl_name, OBJPROP_COLOR, (i == 0 ? clrGold : clrWhite));
   }
}

void DeleteDashboard()
{
   ObjectDelete(0, "HA_EA_Dash_BG");
   for (int i = 0; i < 10; i++)
   {
      ObjectDelete(0, "HA_EA_Dash_Lbl_" + IntegerToString(i));
   }
}

//+------------------------------------------------------------------+
//| Notifications Router                                             |
//+------------------------------------------------------------------+
void SendEAAlert(string msg)
{
   if (InpEnableAlerts) Alert(msg);
   if (InpEnableSoundAlert) PlaySound("alert.wav");
   if (InpEnablePushNotification) SendNotification(msg);
   if (InpEnableEmailAlert) SendMail("HA EA Alert Notification", msg);
}
//+------------------------------------------------------------------+
