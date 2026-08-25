//+------------------------------------------------------------------+
//|                                     VWAP_EMA_BuySell_EA.mq5      |
//|                                  Copyright 2025, EA Developer    |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "EA Developer"
#property link      "https://www.mql5.com"
#property version   "2.00"
#property description "MT5 Expert Advisor - 3-EMA Alignment & Main EMA Breakout Strategy with Optional VWAP Filter"

#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\PositionInfo.mqh>
#include <Canvas\Canvas.mqh>

//--- Pending setup direction
enum ENUM_PENDING_DIR
  {
   PENDING_NONE = 0,
   PENDING_BUY  = 1,
   PENDING_SELL = 2
  };

//--- Trade Direction
enum ENUM_TRADE_DIRECTION
  {
   DIRECTION_BOTH       = 0,   // Both Buy and Sell
   DIRECTION_BUY_ONLY   = 1,   // Buy Only
   DIRECTION_SELL_ONLY  = 2    // Sell Only
  };

//--- Spread filter mode
enum ENUM_SPREAD_FILTER_MODE
  {
   SPREAD_MODE_PERCENT = 0,    // % of price (recommended, works on any symbol)
   SPREAD_MODE_POINTS  = 1     // Raw points
  };

//--- Signal candle size filter mode
enum ENUM_CANDLE_FILTER_MODE
  {
   CANDLE_FILTER_PERCENT = 0,  // % of price (recommended, works on any symbol)
   CANDLE_FILTER_POINTS  = 1   // Raw points
  };

//--- Input Parameters
input group "=== Strategy Settings ===";
input string               InpTradeSymbol       = "";             // Trade Symbol (blank = current)
input ENUM_TIMEFRAMES      InpStrategyTF        = PERIOD_M15;     // Strategy Timeframe
input ENUM_TRADE_DIRECTION InpTradeDirection    = DIRECTION_BOTH; // Trade Direction

input group "=== VWAP Filter & Plot Settings ===";
input bool                 InpUseVWAPFilter     = true;           // Use VWAP Filter (ON/OFF)
input ENUM_TIMEFRAMES      InpVWAPResetPeriod   = PERIOD_D1;      // VWAP Reset Period (D1, W1, MN1)
input bool                 InpShowVWAPPlot      = true;           // Plot VWAP Line on Chart
input bool                 InpShowVWAPLabel     = true;           // Show VWAP Price Value Tag
input color                InpVWAPColor         = clrDodgerBlue;  // VWAP Line & Label Color
input int                  InpVWAPLineWidth     = 2;              // VWAP Line Width
input int                  InpVWAPHistoryBars   = 500;            // VWAP Plot History Length (bars)

input group "=== Fast EMA Settings ===";
input int                  InpFastEMAPeriod     = 13;             // Fast EMA Period
input ENUM_TIMEFRAMES      InpFastEMATimeframe  = PERIOD_CURRENT; // Fast EMA Timeframe (CURRENT = Strategy TF)
input ENUM_MA_METHOD       InpFastEMAMethod     = MODE_EMA;       // Fast MA Method
input ENUM_APPLIED_PRICE   InpFastEMAPrice      = PRICE_CLOSE;    // Fast EMA Applied Price
input bool                 InpShowFastEMAPlot   = true;           // Plot Fast EMA Line on Chart
input color                InpFastEMAColor      = clrYellow;      // Fast EMA Line Color
input int                  InpFastEMALineWidth  = 1;              // Fast EMA Line Width

input group "=== Slow EMA Settings ===";
input int                  InpSlowEMAPeriod     = 21;             // Slow EMA Period
input ENUM_TIMEFRAMES      InpSlowEMATimeframe  = PERIOD_CURRENT; // Slow EMA Timeframe (CURRENT = Strategy TF)
input ENUM_MA_METHOD       InpSlowEMAMethod     = MODE_EMA;       // Slow MA Method
input ENUM_APPLIED_PRICE   InpSlowEMAPrice      = PRICE_CLOSE;    // Slow EMA Applied Price
input bool                 InpShowSlowEMAPlot   = true;           // Plot Slow EMA Line on Chart
input color                InpSlowEMAColor      = clrOrange;      // Slow EMA Line Color
input int                  InpSlowEMALineWidth  = 1;              // Slow EMA Line Width

input group "=== Main EMA Settings (Signal Candle EMA) ===";
input int                  InpMainEMAPeriod     = 34;             // Main EMA Period
input ENUM_TIMEFRAMES      InpMainEMATimeframe  = PERIOD_CURRENT; // Main EMA Timeframe (CURRENT = Strategy TF)
input ENUM_MA_METHOD       InpMainEMAMethod     = MODE_EMA;       // Main MA Method
input ENUM_APPLIED_PRICE   InpMainEMAPrice      = PRICE_CLOSE;    // Main EMA Applied Price
input bool                 InpShowMainEMAPlot   = true;           // Plot Main EMA Line on Chart
input color                InpMainEMAColor      = clrLime;        // Main EMA Line Color
input int                  InpMainEMALineWidth  = 2;              // Main EMA Line Width
input int                  InpEMAHistoryBars    = 500;            // EMA Plot History Length (bars)

input group "=== Signal Candle Filters ===";
input bool                    InpUseCandleSizeFilter   = true;              // Ignore Too-Big Signal Candles (ON/OFF)
input ENUM_CANDLE_FILTER_MODE InpCandleFilterMode      = CANDLE_FILTER_PERCENT; // Filter Mode
input double                  InpMaxCandlePercent      = 0.5;               // Max Signal Candle Range (% of price) - used if mode=PERCENT
input double                  InpMaxSignalCandlePoints = 500.0;             // Max Signal Candle Range (points) - used if mode=POINTS

input group "=== Risk & Target Management ===";
input double               InpRiskRewardRatio   = 2.0;            // Risk-to-Reward Ratio
input bool                 InpUseMinimumLot     = true;            // Always Start From Symbol's Minimum Lot (fixed-lot mode)
input double               InpLotSize           = 0.01;           // Fixed Lot Size (used only if InpUseMinimumLot = false)
input bool                 InpUseRiskPercent    = false;          // Use % Risk Sizing (overrides both of the above)
input double               InpRiskPercent       = 1.0;            // Margin Risk % per trade
input double               InpSLBufferPoints    = 0.0;            // Stop Loss Buffer (points)

input group "=== Spread Filter ===";
input ENUM_SPREAD_FILTER_MODE InpSpreadFilterMode = SPREAD_MODE_PERCENT; // Spread Filter Mode
input double               InpMaxSpreadPercent  = 0.05;           // Max Spread (% of price) - used if mode=PERCENT
input double               InpMaxSpreadPoints   = 50.0;           // Max Spread (points, 0=disabled) - used if mode=POINTS

input group "=== Execution Settings ===";
input ulong                InpMagicNumber       = 20250822;       // EA Magic Number
input bool                 InpOnePositionAtOnce = true;           // Limit to 1 Open Position
input bool                 InpShowDashboard     = true;           // Show Dashboard

//--- Global Objects & Variables
CTrade         m_trade;
CSymbolInfo    m_symbol;
CPositionInfo  m_position;

string         m_sym               = "";
bool           m_sym_matches_chart = true;
int            m_digits            = 5;

datetime          m_last_bar_time     = 0;
ENUM_PENDING_DIR  m_pending_direction = PENDING_NONE;
double            m_signal_high       = 0.0;
double            m_signal_low        = 0.0;
datetime          m_signal_time       = 0;
datetime          m_target_bar_time   = 0;
string            m_dashboard_obj_prefix = "VWAP_EMA_Dash_";

//--- VWAP Globals
double         m_last_vwap_value        = 0.0;
string         m_vwap_tag_name          = "VWAP_EA_VWAPTag";

//--- EMA Handles & Globals
int            m_fast_ema_handle        = INVALID_HANDLE;
int            m_slow_ema_handle        = INVALID_HANDLE;
int            m_main_ema_handle        = INVALID_HANDLE;

double         m_last_fast_ema_value    = 0.0;
double         m_last_slow_ema_value    = 0.0;
double         m_last_main_ema_value    = 0.0;

ENUM_TIMEFRAMES m_fast_ema_tf           = PERIOD_CURRENT;
ENUM_TIMEFRAMES m_slow_ema_tf           = PERIOD_CURRENT;
ENUM_TIMEFRAMES m_main_ema_tf           = PERIOD_CURRENT;

string         m_fast_ema_tag_name      = "VWAP_EA_FastEMATag";
string         m_slow_ema_tag_name      = "VWAP_EA_SlowEMATag";
string         m_main_ema_tag_name      = "VWAP_EA_MainEMATag";

//--- Chart-drawing Globals (CCanvas)
CCanvas        m_canvas;
bool           m_canvas_ready           = false;
string         m_canvas_obj_name        = "VWAP_EA_Canvas";
ulong          m_last_canvas_redraw_ms  = 0;

//+------------------------------------------------------------------+
//| Calculate built-in VWAP value for a given bar shift              |
//+------------------------------------------------------------------+
double CalculateBuiltinVWAP(const int shift)
  {
   datetime bar_time = iTime(m_sym, InpStrategyTF, shift);
   if(bar_time == 0) return 0.0;

   datetime session_start = iTime(m_sym, InpVWAPResetPeriod, iBarShift(m_sym, InpVWAPResetPeriod, bar_time, false));
   if(session_start == 0) return 0.0;

   int start_shift = iBarShift(m_sym, InpStrategyTF, session_start, false);
   int end_shift   = shift;
   if(start_shift < end_shift) return 0.0;

   int count = start_shift - end_shift + 1;
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   if(CopyRates(m_sym, InpStrategyTF, end_shift, count, rates) < count)
      return 0.0;

   double cumulative_pv = 0.0;
   double cumulative_vol = 0.0;

   for(int i = 0; i < count; i++)
     {
      double typical_price = (rates[i].high + rates[i].low + rates[i].close) / 3.0;
      double volume = (rates[i].tick_volume > 0) ? (double)rates[i].tick_volume : (double)rates[i].real_volume;
      if(volume <= 0) volume = 1.0;

      cumulative_pv  += typical_price * volume;
      cumulative_vol += volume;
     }

   return (cumulative_vol > 0.0) ? (cumulative_pv / cumulative_vol) : 0.0;
  }

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
  {
   m_sym = (InpTradeSymbol == "") ? _Symbol : InpTradeSymbol;

   if(!SymbolSelect(m_sym, true))
     {
      Print("[ERROR] Symbol '", m_sym, "' not found in Market Watch.");
      return(INIT_FAILED);
     }

   m_sym_matches_chart = (m_sym == _Symbol);

   if(!m_symbol.Name(m_sym))
     {
      Print("[ERROR] Failed to initialize symbol info for ", m_sym);
      return(INIT_FAILED);
     }
   m_symbol.Refresh();
   m_digits = (int)m_symbol.Digits();

   m_trade.SetExpertMagicNumber(InpMagicNumber);
   uint filling = GetFillingMode();
   m_trade.SetTypeFilling((ENUM_ORDER_TYPE_FILLING)filling);

   m_last_bar_time     = 0;
   m_pending_direction = PENDING_NONE;

   //--- Initialize Fast EMA
   m_fast_ema_tf = (InpFastEMATimeframe == PERIOD_CURRENT) ? InpStrategyTF : InpFastEMATimeframe;
   m_fast_ema_handle = iMA(m_sym, m_fast_ema_tf, InpFastEMAPeriod, 0, InpFastEMAMethod, InpFastEMAPrice);
   if(m_fast_ema_handle == INVALID_HANDLE)
     {
      Print("[ERROR] Failed to create Fast EMA indicator handle for ", m_sym, " period=", InpFastEMAPeriod);
      return(INIT_FAILED);
     }

   //--- Initialize Slow EMA
   m_slow_ema_tf = (InpSlowEMATimeframe == PERIOD_CURRENT) ? InpStrategyTF : InpSlowEMATimeframe;
   m_slow_ema_handle = iMA(m_sym, m_slow_ema_tf, InpSlowEMAPeriod, 0, InpSlowEMAMethod, InpSlowEMAPrice);
   if(m_slow_ema_handle == INVALID_HANDLE)
     {
      Print("[ERROR] Failed to create Slow EMA indicator handle for ", m_sym, " period=", InpSlowEMAPeriod);
      return(INIT_FAILED);
     }

   //--- Initialize Main EMA
   m_main_ema_tf = (InpMainEMATimeframe == PERIOD_CURRENT) ? InpStrategyTF : InpMainEMATimeframe;
   m_main_ema_handle = iMA(m_sym, m_main_ema_tf, InpMainEMAPeriod, 0, InpMainEMAMethod, InpMainEMAPrice);
   if(m_main_ema_handle == INVALID_HANDLE)
     {
      Print("[ERROR] Failed to create Main EMA indicator handle for ", m_sym, " period=", InpMainEMAPeriod);
      return(INIT_FAILED);
     }

   //--- Attach the chart-drawing canvas
   if(InpShowVWAPPlot || InpShowFastEMAPlot || InpShowSlowEMAPlot || InpShowMainEMAPlot)
     {
      int width  = (int)ChartGetInteger(0, CHART_WIDTH_IN_PIXELS, 0);
      int height = (int)ChartGetInteger(0, CHART_HEIGHT_IN_PIXELS, 0);
      if(width < 100)  width  = 800;
      if(height < 100) height = 600;

      if(m_canvas.CreateBitmapLabel(m_canvas_obj_name, 0, 0, width, height, COLOR_FORMAT_ARGB_NORMALIZE))
        {
         ObjectSetInteger(0, m_canvas_obj_name, OBJPROP_BACK, false);
         ObjectSetInteger(0, m_canvas_obj_name, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, m_canvas_obj_name, OBJPROP_HIDDEN, true);
         ObjectSetInteger(0, m_canvas_obj_name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
         ObjectSetInteger(0, m_canvas_obj_name, OBJPROP_XDISTANCE, 0);
         ObjectSetInteger(0, m_canvas_obj_name, OBJPROP_YDISTANCE, 0);
         m_canvas_ready = true;
        }
      else
         Print("[WARN] Failed to create chart drawing canvas. Visual lines will not be plotted.");
     }

   //--- Initial values update
   m_last_vwap_value = CalculateBuiltinVWAP(0);
   GetEMAValue(m_fast_ema_handle, 0, m_last_fast_ema_value);
   GetEMAValue(m_slow_ema_handle, 0, m_last_slow_ema_value);
   GetEMAValue(m_main_ema_handle, 0, m_last_main_ema_value);
   RedrawChartLines();

   Print("[INFO] EA initialized successfully. Strategy: 3-EMA Alignment + Main EMA Signal + VWAP Filter.");
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   ObjectsDeleteAllPrefix(m_dashboard_obj_prefix);
   ObjectDelete(0, m_vwap_tag_name);
   ObjectDelete(0, m_fast_ema_tag_name);
   ObjectDelete(0, m_slow_ema_tag_name);
   ObjectDelete(0, m_main_ema_tag_name);

   if(m_canvas_ready)
     {
      m_canvas.Destroy();
      m_canvas_ready = false;
     }

   if(m_fast_ema_handle != INVALID_HANDLE)
     {
      IndicatorRelease(m_fast_ema_handle);
      m_fast_ema_handle = INVALID_HANDLE;
     }
   if(m_slow_ema_handle != INVALID_HANDLE)
     {
      IndicatorRelease(m_slow_ema_handle);
      m_slow_ema_handle = INVALID_HANDLE;
     }
   if(m_main_ema_handle != INVALID_HANDLE)
     {
      IndicatorRelease(m_main_ema_handle);
      m_main_ema_handle = INVALID_HANDLE;
     }
   Comment("");
  }

//+------------------------------------------------------------------+
//| Chart event handler                                              |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
  {
   if(id == CHARTEVENT_CHART_CHANGE)
     {
      RedrawChartLines();
      return;
     }
  }

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
  {
   if(!m_symbol.RefreshRates())
      return;

   // --- Symbol-independent spread filter -----------------------------
   bool spread_too_high  = false;
   double spread_price   = m_symbol.Ask() - m_symbol.Bid();
   double spread_pts     = (m_symbol.Point() > 0) ? spread_price / m_symbol.Point() : 0.0;
   double spread_percent = (m_symbol.Bid() > 0) ? (spread_price / m_symbol.Bid()) * 100.0 : 0.0;

   if(InpSpreadFilterMode == SPREAD_MODE_PERCENT)
     {
      if(InpMaxSpreadPercent > 0 && spread_percent > InpMaxSpreadPercent)
         spread_too_high = true;
     }
   else
     {
      if(InpMaxSpreadPoints > 0 && spread_pts > InpMaxSpreadPoints)
         spread_too_high = true;
     }

   if(spread_too_high)
     {
      string spread_msg = (InpSpreadFilterMode == SPREAD_MODE_PERCENT)
                           ? ("Spread too high: " + DoubleToString(spread_percent, 3) + " %")
                           : ("Spread too high: " + DoubleToString(spread_pts, 1) + " pts");
      UpdateDashboard(spread_msg);
      return;
     }
   // --------------------------------------------------------------------

   datetime current_bar_time = iTime(m_sym, InpStrategyTF, 0);
   if(current_bar_time != m_last_bar_time)
     {
      m_last_bar_time = current_bar_time;
      m_last_vwap_value = CalculateBuiltinVWAP(0);
      CheckForNewSignal();
     }

   // Keep last known EMA values fresh for dashboard
   GetEMAValue(m_fast_ema_handle, 0, m_last_fast_ema_value);
   GetEMAValue(m_slow_ema_handle, 0, m_last_slow_ema_value);
   GetEMAValue(m_main_ema_handle, 0, m_last_main_ema_value);

   // Throttled redraw of on-chart lines (~5x/sec max)
   ulong now_ms = GetTickCount();
   if(now_ms - m_last_canvas_redraw_ms >= 200)
     {
      m_last_canvas_redraw_ms = now_ms;
      RedrawChartLines();
     }

   if(m_pending_direction != PENDING_NONE)
     {
      datetime candle_0_time = iTime(m_sym, InpStrategyTF, 0);
      if(candle_0_time != m_target_bar_time)
        {
         Print("[INFO] ", PendingDirToString(m_pending_direction), " setup expired: Next candle closed without breakout.");
         m_pending_direction = PENDING_NONE;
        }
      else if(m_pending_direction == PENDING_SELL)
        {
         double trigger_price = m_signal_low - (InpSLBufferPoints * m_symbol.Point());
         double current_bid   = m_symbol.Bid();
         if(current_bid <= trigger_price)
            TryTriggerEntry(PENDING_SELL, current_bid);
        }
      else if(m_pending_direction == PENDING_BUY)
        {
         double trigger_price = m_signal_high + (InpSLBufferPoints * m_symbol.Point());
         double current_ask   = m_symbol.Ask();
         if(current_ask >= trigger_price)
            TryTriggerEntry(PENDING_BUY, current_ask);
        }
     }

   if(InpShowDashboard)
      UpdateDashboard("Active");
  }

//+------------------------------------------------------------------+
//| Validate conditions at trigger moment and execute trade         |
//+------------------------------------------------------------------+
void TryTriggerEntry(const ENUM_PENDING_DIR dir, const double current_price)
  {
   if(InpOnePositionAtOnce && HasOpenPosition())
     {
      m_pending_direction = PENDING_NONE;
      return;
     }

   // --- EMA Alignment Verification at entry moment ---
   double fast_ema = 0.0, slow_ema = 0.0, main_ema = 0.0;
   if(!GetEMAValue(m_fast_ema_handle, 0, fast_ema) ||
      !GetEMAValue(m_slow_ema_handle, 0, slow_ema) ||
      !GetEMAValue(m_main_ema_handle, 0, main_ema))
     {
      Print("[INFO] EMA values unavailable at trigger, ignoring signal.");
      m_pending_direction = PENDING_NONE;
      return;
     }

   if(dir == PENDING_SELL)
     {
      // Sell requires: Main EMA > Slow EMA > Fast EMA
      if(!(main_ema > slow_ema && slow_ema > fast_ema))
        {
         Print("[INFO] SELL entry ignored: EMA alignment invalid (Main:", DoubleToString(main_ema, m_digits),
               " Slow:", DoubleToString(slow_ema, m_digits), " Fast:", DoubleToString(fast_ema, m_digits), ").");
         m_pending_direction = PENDING_NONE;
         return;
        }
     }
   else if(dir == PENDING_BUY)
     {
      // Buy requires: Fast EMA > Slow EMA > Main EMA
      if(!(fast_ema > slow_ema && slow_ema > main_ema))
        {
         Print("[INFO] BUY entry ignored: EMA alignment invalid (Fast:", DoubleToString(fast_ema, m_digits),
               " Slow:", DoubleToString(slow_ema, m_digits), " Main:", DoubleToString(main_ema, m_digits), ").");
         m_pending_direction = PENDING_NONE;
         return;
        }
     }

   // --- VWAP Filter Verification at entry moment ---
   if(InpUseVWAPFilter)
     {
      double vwap_val = CalculateBuiltinVWAP(0);
      if(vwap_val > 0.0)
        {
         if(dir == PENDING_SELL && current_price >= vwap_val)
           {
            Print("[INFO] SELL entry ignored: Price (", DoubleToString(current_price, m_digits),
                  ") not below VWAP (", DoubleToString(vwap_val, m_digits), ").");
            m_pending_direction = PENDING_NONE;
            return;
           }
         if(dir == PENDING_BUY && current_price <= vwap_val)
           {
            Print("[INFO] BUY entry ignored: Price (", DoubleToString(current_price, m_digits),
                  ") not above VWAP (", DoubleToString(vwap_val, m_digits), ").");
            m_pending_direction = PENDING_NONE;
            return;
           }
        }
     }

   if(dir == PENDING_SELL)
      ExecuteShortEntry();
   else if(dir == PENDING_BUY)
      ExecuteLongEntry();
  }

//+------------------------------------------------------------------+
//| Check closed candle (shift 1) for a new trade setup              |
//+------------------------------------------------------------------+
void CheckForNewSignal()
  {
   if(InpOnePositionAtOnce && HasOpenPosition())
      return;

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(m_sym, InpStrategyTF, 1, 1, rates) < 1)
      return;

   double open_p  = rates[0].open;
   double high_p  = rates[0].high;
   double low_p   = rates[0].low;
   double close_p = rates[0].close;

   // Get EMA values at shift 1
   double fast_ema = 0.0, slow_ema = 0.0, main_ema = 0.0;
   if(!GetEMAValue(m_fast_ema_handle, 1, fast_ema) ||
      !GetEMAValue(m_slow_ema_handle, 1, slow_ema) ||
      !GetEMAValue(m_main_ema_handle, 1, main_ema))
      return;

   // Get VWAP value at shift 1
   double vwap_val = CalculateBuiltinVWAP(1);

   // --- Candle Size Filter ---
   if(InpUseCandleSizeFilter)
     {
      double candle_range = high_p - low_p;
      bool   too_big = false;

      if(InpCandleFilterMode == CANDLE_FILTER_PERCENT)
        {
         double candle_range_percent = (close_p > 0.0) ? (candle_range / close_p) * 100.0 : 0.0;
         if(InpMaxCandlePercent > 0 && candle_range_percent > InpMaxCandlePercent)
            too_big = true;
        }
      else // CANDLE_FILTER_POINTS
        {
         double candle_range_points = (m_symbol.Point() > 0) ? candle_range / m_symbol.Point() : 0.0;
         if(InpMaxSignalCandlePoints > 0 && candle_range_points > InpMaxSignalCandlePoints)
            too_big = true;
        }

      if(too_big) return;
     }

   // --- SELL Signal setup ---
   // 1. Red candle (close < open)
   // 2. Touch and close below Main EMA (high > Main EMA & close < Main EMA)
   // 3. EMA alignment: Main EMA > Slow EMA > Fast EMA
   // 4. VWAP filter (if ON): price/close below VWAP
   bool sell_candle_pattern = (close_p < open_p) && (high_p > main_ema) && (close_p < main_ema);
   bool sell_ema_alignment  = (main_ema > slow_ema) && (slow_ema > fast_ema);
   bool sell_vwap_filter    = (!InpUseVWAPFilter) || (vwap_val > 0.0 && close_p < vwap_val);

   bool sell_signal = sell_candle_pattern && sell_ema_alignment && sell_vwap_filter;

   // --- BUY Signal setup ---
   // 1. Green candle (close > open)
   // 2. Touch and close above Main EMA (low < Main EMA & close > Main EMA)
   // 3. EMA alignment: Fast EMA > Slow EMA > Main EMA
   // 4. VWAP filter (if ON): price/close above VWAP
   bool buy_candle_pattern = (close_p > open_p) && (low_p < main_ema) && (close_p > main_ema);
   bool buy_ema_alignment  = (fast_ema > slow_ema) && (slow_ema > main_ema);
   bool buy_vwap_filter    = (!InpUseVWAPFilter) || (vwap_val > 0.0 && close_p > vwap_val);

   bool buy_signal = buy_candle_pattern && buy_ema_alignment && buy_vwap_filter;

   bool allow_sell = (InpTradeDirection == DIRECTION_BOTH || InpTradeDirection == DIRECTION_SELL_ONLY);
   bool allow_buy  = (InpTradeDirection == DIRECTION_BOTH || InpTradeDirection == DIRECTION_BUY_ONLY);

   // --- Set pending signal for immediate next candle breakout ---
   if(sell_signal && allow_sell)
     {
      m_pending_direction = PENDING_SELL;
      m_signal_high       = high_p;   // Stop Loss = Red signal candle High
      m_signal_low        = low_p;    // Trigger price = Red signal candle Low
      m_signal_time       = rates[0].time;
      m_target_bar_time   = iTime(m_sym, InpStrategyTF, 0);
      Print("[SIGNAL] Valid SELL setup detected on candle ", TimeToString(m_signal_time),
            " | Low: ", DoubleToString(m_signal_low, m_digits), " High: ", DoubleToString(m_signal_high, m_digits));
     }
   else if(buy_signal && allow_buy)
     {
      m_pending_direction = PENDING_BUY;
      m_signal_high       = high_p;   // Trigger price = Green signal candle High
      m_signal_low        = low_p;    // Stop Loss = Green signal candle Low
      m_signal_time       = rates[0].time;
      m_target_bar_time   = iTime(m_sym, InpStrategyTF, 0);
      Print("[SIGNAL] Valid BUY setup detected on candle ", TimeToString(m_signal_time),
            " | High: ", DoubleToString(m_signal_high, m_digits), " Low: ", DoubleToString(m_signal_low, m_digits));
     }
  }

//+------------------------------------------------------------------+
//| Execute Short Entry                                              |
//+------------------------------------------------------------------+
void ExecuteShortEntry()
  {
   if(InpOnePositionAtOnce && HasOpenPosition()) { m_pending_direction = PENDING_NONE; return; }
   m_pending_direction = PENDING_NONE;

   double entry_price = m_symbol.Bid();
   double sl_price    = NormalizeDouble(m_signal_high + (InpSLBufferPoints * m_symbol.Point()), m_digits);
   double min_stop    = m_symbol.StopsLevel() * m_symbol.Point();

   if(sl_price <= entry_price + min_stop)
      sl_price = NormalizeDouble(entry_price + min_stop + (10 * m_symbol.Point()), m_digits);

   double risk_distance = sl_price - entry_price;
   if(risk_distance <= 0) return;

   double tp_price = NormalizeDouble(entry_price - (risk_distance * InpRiskRewardRatio), m_digits);
   double trade_lots = CalculateLotSize(risk_distance);
   if(trade_lots <= 0) return;

   m_trade.Sell(trade_lots, m_sym, entry_price, sl_price, tp_price, "3-EMA+VWAP EA");
  }

//+------------------------------------------------------------------+
//| Execute Long Entry                                               |
//+------------------------------------------------------------------+
void ExecuteLongEntry()
  {
   if(InpOnePositionAtOnce && HasOpenPosition()) { m_pending_direction = PENDING_NONE; return; }
   m_pending_direction = PENDING_NONE;

   double entry_price = m_symbol.Ask();
   double sl_price    = NormalizeDouble(m_signal_low - (InpSLBufferPoints * m_symbol.Point()), m_digits);
   double min_stop    = m_symbol.StopsLevel() * m_symbol.Point();

   if(sl_price >= entry_price - min_stop)
      sl_price = NormalizeDouble(entry_price - min_stop - (10 * m_symbol.Point()), m_digits);

   double risk_distance = entry_price - sl_price;
   if(risk_distance <= 0) return;

   double tp_price = NormalizeDouble(entry_price + (risk_distance * InpRiskRewardRatio), m_digits);
   double trade_lots = CalculateLotSize(risk_distance);
   if(trade_lots <= 0) return;

   m_trade.Buy(trade_lots, m_sym, entry_price, sl_price, tp_price, "3-EMA+VWAP EA");
  }

//+------------------------------------------------------------------+
//| Calculate trade lot size                                         |
//+------------------------------------------------------------------+
double CalculateLotSize(double risk_distance_price)
  {
   if(!InpUseRiskPercent)
     {
      double fixed_lots = InpUseMinimumLot ? m_symbol.LotsMin() : InpLotSize;
      return NormalizeLotSize(fixed_lots);
     }

   double free_margin  = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double risk_amount  = free_margin * (InpRiskPercent / 100.0);
   double tick_value   = SymbolInfoDouble(m_sym, SYMBOL_TRADE_TICK_VALUE);
   double tick_size    = SymbolInfoDouble(m_sym, SYMBOL_TRADE_TICK_SIZE);

   double fallback_lots = InpUseMinimumLot ? m_symbol.LotsMin() : InpLotSize;

   if(tick_value <= 0 || tick_size <= 0 || risk_distance_price <= 0)
      return NormalizeLotSize(fallback_lots);

   double loss_per_lot = (risk_distance_price / tick_size) * tick_value;
   if(loss_per_lot <= 0) return NormalizeLotSize(fallback_lots);

   double calc_lots = risk_amount / loss_per_lot;
   if(calc_lots < m_symbol.LotsMin())
      calc_lots = m_symbol.LotsMin();

   return NormalizeLotSize(calc_lots);
  }

//+------------------------------------------------------------------+
//| Normalize lot size to broker constraints                         |
//+------------------------------------------------------------------+
double NormalizeLotSize(double lots)
  {
   double min_lot  = m_symbol.LotsMin();
   double max_lot  = m_symbol.LotsMax();
   double step_lot = m_symbol.LotsStep();

   if(step_lot > 0)
      lots = MathFloor(lots / step_lot) * step_lot;

   if(lots < min_lot) lots = min_lot;
   if(lots > max_lot) lots = max_lot;

   return NormalizeDouble(lots, 2);
  }

//+------------------------------------------------------------------+
//| Check for open positions                                         |
//+------------------------------------------------------------------+
bool HasOpenPosition()
  {
   for(int i = 0; i < PositionsTotal(); i++)
     {
      if(PositionGetTicket(i) > 0)
        {
         if(PositionGetString(POSITION_SYMBOL) == m_sym &&
            PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
            return true;
        }
     }
   return false;
  }

//+------------------------------------------------------------------+
//| Query symbol filling mode                                        |
//+------------------------------------------------------------------+
uint GetFillingMode()
  {
   uint mode = (uint)SymbolInfoInteger(m_sym, SYMBOL_FILLING_MODE);
   if((mode & SYMBOL_FILLING_FOK) != 0) return ORDER_FILLING_FOK;
   if((mode & SYMBOL_FILLING_IOC) != 0) return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
  }

//+------------------------------------------------------------------+
//| Delete visual objects with prefix                                |
//+------------------------------------------------------------------+
void ObjectsDeleteAllPrefix(string prefix)
  {
   for(int i = ObjectsTotal(0, -1, -1) - 1; i >= 0; i--)
     {
      string name = ObjectName(0, i, -1, -1);
      if(StringFind(name, prefix) == 0)
         ObjectDelete(0, name);
     }
  }

//+------------------------------------------------------------------+
//| String helper functions                                          |
//+------------------------------------------------------------------+
string PendingDirToString(const ENUM_PENDING_DIR dir)
  {
   switch(dir)
     {
      case PENDING_BUY:  return "BUY";
      case PENDING_SELL: return "SELL";
      default:           return "NONE";
     }
  }

string TradeDirectionToString(const ENUM_TRADE_DIRECTION dir)
  {
   switch(dir)
     {
      case DIRECTION_BUY_ONLY:  return "Buy Only";
      case DIRECTION_SELL_ONLY: return "Sell Only";
      default:                  return "Both";
     }
  }

//+------------------------------------------------------------------+
//| Update visual dashboard                                          |
//+------------------------------------------------------------------+
void UpdateDashboard(string status)
  {
   if(!InpShowDashboard) return;

   string pending_text = "NO";
   if(m_pending_direction == PENDING_SELL)
      pending_text = "SELL (Low: " + DoubleToString(m_signal_low, m_digits) + ")";
   else if(m_pending_direction == PENDING_BUY)
      pending_text = "BUY (High: " + DoubleToString(m_signal_high, m_digits) + ")";

   string text = "=== 3-EMA + VWAP Strategy EA ===" +
                 "\nSymbol: " + m_sym +
                 "\nDirection: " + TradeDirectionToString(InpTradeDirection) +
                 "\nStrategy TF: " + EnumToString(InpStrategyTF) +
                 "\nVWAP Filter: " + (InpUseVWAPFilter ? "ON" : "OFF") +
                 "\nVWAP: " + ((m_last_vwap_value > 0.0) ? DoubleToString(m_last_vwap_value, m_digits) : "N/A") +
                 "\nFast EMA(" + IntegerToString(InpFastEMAPeriod) + "): " + ((m_last_fast_ema_value > 0.0) ? DoubleToString(m_last_fast_ema_value, m_digits) : "N/A") +
                 "\nSlow EMA(" + IntegerToString(InpSlowEMAPeriod) + "): " + ((m_last_slow_ema_value > 0.0) ? DoubleToString(m_last_slow_ema_value, m_digits) : "N/A") +
                 "\nMain EMA(" + IntegerToString(InpMainEMAPeriod) + "): " + ((m_last_main_ema_value > 0.0) ? DoubleToString(m_last_main_ema_value, m_digits) : "N/A") +
                 "\nTarget RR: 1:" + DoubleToString(InpRiskRewardRatio, 2) +
                 "\nCandle Size Filter: " + (InpUseCandleSizeFilter ?
                    ((InpCandleFilterMode == CANDLE_FILTER_PERCENT) ?
                       ("ON (Max " + DoubleToString(InpMaxCandlePercent, 2) + "% of price)") :
                       ("ON (Max " + DoubleToString(InpMaxSignalCandlePoints, 0) + " pts)")) : "OFF") +
                 "\nOpen Position: " + (HasOpenPosition() ? "YES" : "NO") +
                 "\nPending Setup: " + pending_text +
                 "\nStatus: " + status;

   Comment(text);
  }

double OnTester() { return TesterStatistics(STAT_PROFIT); }

//+------------------------------------------------------------------+
//| Fetch EMA value for a specific handle and shift                  |
//+------------------------------------------------------------------+
bool GetEMAValue(const int handle, const int shift, double &ema_value)
  {
   ema_value = 0.0;
   if(handle == INVALID_HANDLE) return false;

   double buf[];
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(handle, 0, shift, 1, buf) < 1)
      return false;

   ema_value = buf[0];
   return (ema_value > 0.0);
  }

//+------------------------------------------------------------------+
//| Draw anti-aliased thick line                                     |
//+------------------------------------------------------------------+
void DrawThickLineAA(const int x1, const int y1, const int x2, const int y2,
                     const uint argb, const int width)
  {
   int half = MathMax(0, (width - 1) / 2);
   for(int w = -half; w <= half; w++)
      m_canvas.LineAA(x1, y1 + w, x2, y2 + w, argb);
  }

//+------------------------------------------------------------------+
//| Update line text tag on chart                                    |
//+------------------------------------------------------------------+
void UpdateLineTag(const string tag_name, const string text, const color clr,
                   const datetime tag_time, const double price)
  {
   if(ObjectFind(0, tag_name) < 0)
     {
      ObjectCreate(0, tag_name, OBJ_TEXT, 0, tag_time, price);
      ObjectSetInteger(0, tag_name, OBJPROP_ANCHOR, ANCHOR_LEFT);
      ObjectSetInteger(0, tag_name, OBJPROP_FONTSIZE, 8);
      ObjectSetString(0, tag_name, OBJPROP_FONT, "Arial Bold");
      ObjectSetInteger(0, tag_name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, tag_name, OBJPROP_HIDDEN, true);
     }
   else
     {
      ObjectSetInteger(0, tag_name, OBJPROP_TIME, tag_time);
      ObjectSetDouble(0, tag_name, OBJPROP_PRICE, price);
     }

   ObjectSetInteger(0, tag_name, OBJPROP_COLOR, clr);
   ObjectSetString(0, tag_name, OBJPROP_TEXT, text);
  }

//+------------------------------------------------------------------+
//| Get number of bars to plot on visible chart                      |
//+------------------------------------------------------------------+
int GetPlotBarCount(const int max_bars_input)
  {
   int visible_bars = (int)ChartGetInteger(0, CHART_VISIBLE_BARS, 0);
   if(visible_bars <= 0)
      return MathMax(2, max_bars_input);

   int wanted = visible_bars + 20;
   return MathMax(2, MathMin(max_bars_input, wanted));
  }

//+------------------------------------------------------------------+
//| Draw VWAP line using exact per-bar calculation                   |
//+------------------------------------------------------------------+
void DrawVWAPLine()
  {
   int bars_needed = GetPlotBarCount(InpVWAPHistoryBars);

   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   int copied = CopyRates(m_sym, InpStrategyTF, 0, bars_needed, rates);
   if(copied < 2) return;

   uint argb = ColorToARGB(InpVWAPColor, 255);
   int prev_x = -1, prev_y = -1;
   double last_val = 0.0;
   datetime last_time = 0;
   int width = (int)m_canvas.Width();
   int height = (int)m_canvas.Height();

   for(int i = 0; i < copied; i++)
     {
      int shift = copied - 1 - i;
      double val = CalculateBuiltinVWAP(shift);
      if(val <= 0.0) { prev_x = -1; continue; }

      int x, y;
      if(ChartTimePriceToXY(0, 0, rates[i].time, val, x, y))
        {
         if(x >= 0 && x < width && y >= 0 && y < height)
           {
            if(prev_x >= 0)
               DrawThickLineAA(prev_x, prev_y, x, y, argb, InpVWAPLineWidth);
            prev_x = x;
            prev_y = y;
           }
         else
           {
            prev_x = -1;
           }
        }
      else
        {
         prev_x = -1;
        }

      last_val  = val;
      last_time = rates[i].time;
     }

   m_last_vwap_value = last_val;

   if(InpShowVWAPLabel && last_val > 0.0)
      UpdateLineTag(m_vwap_tag_name, " VWAP " + DoubleToString(last_val, m_digits),
                    InpVWAPColor, last_time + (datetime)(PeriodSeconds(InpStrategyTF) * 2), last_val);
   else
      ObjectDelete(0, m_vwap_tag_name);
  }

//+------------------------------------------------------------------+
//| Draw a specific EMA line                                         |
//+------------------------------------------------------------------+
void DrawEMALine(const int handle, const ENUM_TIMEFRAMES tf, const color clr,
                 const int width, const string label_prefix, const string tag_name,
                 double &last_val_out)
  {
   if(handle == INVALID_HANDLE) return;

   int bars_needed = GetPlotBarCount(InpEMAHistoryBars);

   double buf[];
   ArraySetAsSeries(buf, true);
   int copied = CopyBuffer(handle, 0, 0, bars_needed, buf);
   if(copied < 2) return;

   uint argb = ColorToARGB(clr, 255);
   int prev_x = -1, prev_y = -1;
   double last_val = 0.0;
   datetime last_time = 0;
   int canvas_w = (int)m_canvas.Width();
   int canvas_h = (int)m_canvas.Height();

   for(int shift = copied - 1; shift >= 0; shift--)
     {
      double val = buf[shift];
      if(val <= 0.0) { prev_x = -1; continue; }

      datetime t = iTime(m_sym, tf, shift);
      if(t == 0) { prev_x = -1; continue; }

      int x, y;
      if(ChartTimePriceToXY(0, 0, t, val, x, y))
        {
         if(x >= 0 && x < canvas_w && y >= 0 && y < canvas_h)
           {
            if(prev_x >= 0)
               DrawThickLineAA(prev_x, prev_y, x, y, argb, width);
            prev_x = x;
            prev_y = y;
           }
         else
           {
            prev_x = -1;
           }
        }
      else
        {
         prev_x = -1;
        }

      last_val  = val;
      last_time = t;
     }

   if(last_val > 0.0)
      last_val_out = last_val;

   if(last_val > 0.0)
      UpdateLineTag(tag_name, " " + label_prefix + " " + DoubleToString(last_val, m_digits),
                    clr, last_time + (datetime)(PeriodSeconds(tf) * 2), last_val);
   else
      ObjectDelete(0, tag_name);
  }

//+------------------------------------------------------------------+
//| Master redraw for canvas lines                                   |
//+------------------------------------------------------------------+
void RedrawChartLines()
  {
   if(!m_canvas_ready) return;
   if(!InpShowVWAPPlot && !InpShowFastEMAPlot && !InpShowSlowEMAPlot && !InpShowMainEMAPlot)
     {
      m_canvas.Erase(0x00000000);
      m_canvas.Update();
      return;
     }

   int width  = (int)ChartGetInteger(0, CHART_WIDTH_IN_PIXELS, 0);
   int height = (int)ChartGetInteger(0, CHART_HEIGHT_IN_PIXELS, 0);
   if(width > 0 && height > 0 && (width != (int)m_canvas.Width() || height != (int)m_canvas.Height()))
      m_canvas.Resize(width, height);

   m_canvas.Erase(0x00000000); // transparent clear

   if(InpShowVWAPPlot)
      DrawVWAPLine();
   if(InpShowFastEMAPlot)
      DrawEMALine(m_fast_ema_handle, m_fast_ema_tf, InpFastEMAColor, InpFastEMALineWidth, "Fast EMA(" + IntegerToString(InpFastEMAPeriod) + ")", m_fast_ema_tag_name, m_last_fast_ema_value);
   else
      ObjectDelete(0, m_fast_ema_tag_name);

   if(InpShowSlowEMAPlot)
      DrawEMALine(m_slow_ema_handle, m_slow_ema_tf, InpSlowEMAColor, InpSlowEMALineWidth, "Slow EMA(" + IntegerToString(InpSlowEMAPeriod) + ")", m_slow_ema_tag_name, m_last_slow_ema_value);
   else
      ObjectDelete(0, m_slow_ema_tag_name);

   if(InpShowMainEMAPlot)
      DrawEMALine(m_main_ema_handle, m_main_ema_tf, InpMainEMAColor, InpMainEMALineWidth, "Main EMA(" + IntegerToString(InpMainEMAPeriod) + ")", m_main_ema_tag_name, m_last_main_ema_value);
   else
      ObjectDelete(0, m_main_ema_tag_name);

   m_canvas.Update();
  }
//+------------------------------------------------------------------+
