//+------------------------------------------------------------------+
//|                                   MTF_EMA_VWAP_Breakout_EA.mq5   |
//|                    Multi-Timeframe EMA & VWAP Sell Breakout EA   |
//+------------------------------------------------------------------+
#property copyright "Copyright 2025"
#property link      "https://www.mql5.com"
#property version   "1.00"
#property description "MT5 Expert Advisor executing Next-Candle Short Breakouts based on Strategy TF Main EMA, Higher TF EMA, and MTF VWAP filters."

#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>

//--- Timeframe Selection Enum
enum ENUM_STRATEGY_TIMEFRAME
  {
   STF_M1  = PERIOD_M1,   // 1 Minute
   STF_M3  = PERIOD_M3,   // 3 Minutes
   STF_M5  = PERIOD_M5,   // 5 Minutes
   STF_M15 = PERIOD_M15,  // 15 Minutes
   STF_M30 = PERIOD_M30,  // 30 Minutes
   STF_H1  = PERIOD_H1,   // 1 Hour
   STF_D1  = PERIOD_D1    // 1 Day
  };

//--- Lot Sizing Mode
enum ENUM_LOT_TYPE
  {
   LOT_TYPE_FIXED = 0,   // Fixed Lot Size
   LOT_TYPE_RISK  = 1    // Risk Percentage (% of Free Margin)
  };

//+------------------------------------------------------------------+
//| INPUT PARAMETERS                                                 |
//+------------------------------------------------------------------+
input group "--- Timeframe Settings ---"
input ENUM_STRATEGY_TIMEFRAME InpStrategyTimeframe = STF_M1;    // Strategy Timeframe
input ENUM_STRATEGY_TIMEFRAME InpHigherTimeframe   = STF_M15;   // Higher Timeframe (HTF)

input group "--- EMA Settings ---"
input int                  InpMainEMAPeriod       = 34;       // Main EMA Period (Strategy TF)
input int                  InpHTFEMAPeriod        = 34;       // Higher Timeframe EMA Period
input ENUM_APPLIED_PRICE   InpEMAAppliedPrice     = PRICE_CLOSE; // EMA Applied Price

input group "--- VWAP Settings ---"
input ENUM_TIMEFRAMES      InpVWAPResetPeriod     = PERIOD_D1; // VWAP Reset Period (default: Daily)

input group "--- Risk & Trade Settings ---"
input ENUM_LOT_TYPE        InpLotType             = LOT_TYPE_FIXED; // Lot Sizing Method
input double               InpFixedLot            = 0.01;     // Fixed Lot Size
input double               InpRiskPercent         = 1.0;      // Risk Percentage (% per trade)
input double               InpRiskRewardRatio     = 1.0;      // Risk:Reward Target Ratio (1:1, 1:2, etc.)
input double               InpSLBufferPoints      = 10.0;     // Stop Loss Buffer (in Points)
input double               InpEntryBufferPoints   = 0.0;      // Entry Breakout Buffer (in Points)
input bool                 InpUseSpreadSLBuffer   = true;     // Add live Spread buffer to Stop Loss
input bool                 InpOnePositionAtATime  = true;     // Limit to strictly 1 active position
input int                  InpMaxSpreadPoints     = 50;       // Max Allowed Spread (in Points, 0=Disabled)

input group "--- System & Identification ---"
input ulong                InpMagicNumber         = 9876543;  // EA Magic Number
input string               InpTradeComment        = "MTF_EMA_VWAP_Sell"; // Order Comment

//+------------------------------------------------------------------+
//| GLOBAL VARIABLES & STRUCTURES                                    |
//+------------------------------------------------------------------+
CTrade      m_trade;
CSymbolInfo m_symbol;

// EMA Handles
int         m_handle_main_ema = INVALID_HANDLE;
int         m_handle_htf_ema  = INVALID_HANDLE;

// Active Signal Tracker Structure
struct SSignalSetup
  {
   bool     valid;
   datetime signal_bar_time;
   double   signal_high;
   double   signal_low;
   double   sl_price;
   double   tp_price;
  };

SSignalSetup g_setup = {false, 0, 0.0, 0.0, 0.0, 0.0};
datetime     g_last_processed_bar_time = 0;

// Dashboard coordinates
int          g_dash_x = 20;
int          g_dash_y = 30;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
  {
   // Initialize Symbol
   if(!m_symbol.Name(_Symbol))
     {
      Print("[ERROR] Failed to initialize symbol info for ", _Symbol);
      return(INIT_FAILED);
     }
   m_symbol.RefreshRates();

   // Set trade parameters
   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_trade.SetMarginMode();

   ENUM_TIMEFRAMES stf = (ENUM_TIMEFRAMES)InpStrategyTimeframe;
   ENUM_TIMEFRAMES htf = (ENUM_TIMEFRAMES)InpHigherTimeframe;

   // Validate Timeframes
   if(PeriodSeconds(htf) < PeriodSeconds(stf))
     {
      Print("[WARNING] Higher Timeframe (", EnumToString(htf), ") is smaller than Strategy Timeframe (", EnumToString(stf), "). Make sure higher timeframe is >= strategy timeframe.");
     }

   // Initialize Main EMA Indicator
   m_handle_main_ema = iMA(_Symbol, stf, InpMainEMAPeriod, 0, MODE_EMA, InpEMAAppliedPrice);
   if(m_handle_main_ema == INVALID_HANDLE)
     {
      Print("[ERROR] Failed to create Main EMA indicator handle.");
      return(INIT_FAILED);
     }

   // Initialize HTF EMA Indicator
   m_handle_htf_ema = iMA(_Symbol, htf, InpHTFEMAPeriod, 0, MODE_EMA, InpEMAAppliedPrice);
   if(m_handle_htf_ema == INVALID_HANDLE)
     {
      Print("[ERROR] Failed to create HTF EMA indicator handle.");
      return(INIT_FAILED);
     }

   // Create Dashboard
   CreateDashboard();

   Print("[INIT SUCCESS] EA initialized successfully on ", _Symbol, " | STF: ", EnumToString(stf), " | HTF: ", EnumToString(htf));
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   // Release Indicator Handles
   if(m_handle_main_ema != INVALID_HANDLE) IndicatorRelease(m_handle_main_ema);
   if(m_handle_htf_ema != INVALID_HANDLE)  IndicatorRelease(m_handle_htf_ema);

   // Remove Dashboard Objects
   RemoveDashboard();
  }

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
  {
   if(!m_symbol.RefreshRates()) return;

   ENUM_TIMEFRAMES stf = (ENUM_TIMEFRAMES)InpStrategyTimeframe;
   datetime current_bar_time = iTime(_Symbol, stf, 0);

   // Check for New Bar on Strategy Timeframe
   if(current_bar_time != g_last_processed_bar_time)
     {
      g_last_processed_bar_time = current_bar_time;

      // On new bar creation, check if previous setup was pending from index 1.
      // Next candle entry rule: Entry is strictly on the immediate next candle (index 0).
      // If a setup was active for the bar that just finished without breaking low, it expires now!
      if(g_setup.valid && g_setup.signal_bar_time < iTime(_Symbol, stf, 1))
        {
         Print("[SETUP EXPIRED] Immediate next candle completed without breaking Signal Low. Signal invalidated.");
         g_setup.valid = false;
        }

      // Evaluate new signal candle setup (index 1 = just completed bar)
      EvaluateSignalCandle();
     }

   // Execute Pending Breakout Entry Tick-by-Tick
   if(g_setup.valid)
     {
      CheckAndExecuteBreakout();
     }

   // Update Chart Dashboard
   UpdateDashboard();
  }

//+------------------------------------------------------------------+
//| Evaluate Signal Candle Conditions (Executed on Bar Close)       |
//+------------------------------------------------------------------+
void EvaluateSignalCandle()
  {
   ENUM_TIMEFRAMES stf = (ENUM_TIMEFRAMES)InpStrategyTimeframe;
   ENUM_TIMEFRAMES htf = (ENUM_TIMEFRAMES)InpHigherTimeframe;

   // 1. Fetch Signal Candle (Index 1) on Strategy Timeframe
   MqlRates stf_rates[];
   ArraySetAsSeries(stf_rates, true);
   if(CopyRates(_Symbol, stf, 1, 2, stf_rates) < 2)
     {
      Print("[WARN] Not enough Strategy Timeframe rates available.");
      return;
     }

   double sig_open  = stf_rates[0].open;   // Index 1 (index 0 in copy array)
   double sig_high  = stf_rates[0].high;
   double sig_low   = stf_rates[0].low;
   double sig_close = stf_rates[0].close;
   datetime sig_time= stf_rates[0].time;

   // Condition A: RED Candle on Strategy Timeframe
   bool is_red_candle = (sig_close < sig_open);
   if(!is_red_candle) return;

   // 2. Fetch Main EMA value for Signal Candle (Index 1)
   double main_ema_val[];
   ArraySetAsSeries(main_ema_val, true);
   if(CopyBuffer(m_handle_main_ema, 0, 1, 1, main_ema_val) < 1) return;

   double main_ema = main_ema_val[0];

   // Condition B: Signal candle touched Main EMA and closed below Main EMA
   // OR Signal candle High >= Main EMA and Close < Main EMA
   bool touch_and_close_below = (sig_high >= main_ema && sig_close < main_ema);
   if(!touch_and_close_below) return;

   // 3. Fetch HTF Price & HTF EMA
   // Get latest completed HTF candle or current HTF price
   double htf_ema_val[];
   ArraySetAsSeries(htf_ema_val, true);
   if(CopyBuffer(m_handle_htf_ema, 0, 0, 1, htf_ema_val) < 1) return;
   double htf_ema = htf_ema_val[0];

   // HTF Price check: Current Bid / Close must be below HTF EMA
   double current_price = m_symbol.Bid();
   bool htf_price_below_ema = (current_price < htf_ema);
   if(!htf_price_below_ema) return;

   // 4. Calculate VWAP on Strategy Timeframe & HTF
   double stf_vwap = CalculateVWAP(_Symbol, stf, 1, InpVWAPResetPeriod);
   double htf_vwap = CalculateVWAP(_Symbol, htf, 0, InpVWAPResetPeriod);

   // Condition C: Price below VWAP as per Strategy Timeframe AND Higher Timeframe
   bool stf_below_vwap = (sig_close < stf_vwap);
   bool htf_below_vwap = (current_price < htf_vwap);

   if(!stf_below_vwap || !htf_below_vwap)
     {
      Print("[SIGNAL REJECTED] Price above VWAP filter. STF Close: ", sig_close, " STF VWAP: ", stf_vwap, " | Current Price: ", current_price, " HTF VWAP: ", htf_vwap);
      return;
     }

   // ALL SIGNAL CONDITIONS MET!
   g_setup.valid            = true;
   g_setup.signal_bar_time  = sig_time;
   g_setup.signal_high      = sig_high;
   g_setup.signal_low       = sig_low;

   Print("[VALID SIGNAL DETECTED] Time: ", TimeToString(sig_time),
         " | High: ", sig_high, " Low: ", sig_low,
         " | Main EMA: ", main_ema, " HTF EMA: ", htf_ema,
         " | STF VWAP: ", stf_vwap, " HTF VWAP: ", htf_vwap);
  }

//+------------------------------------------------------------------+
//| Check and Execute Breakout Trade Tick-by-Tick                    |
//+------------------------------------------------------------------+
void CheckAndExecuteBreakout()
  {
   if(!g_setup.valid) return;

   // Single position check
   if(InpOnePositionAtATime && HasOpenPosition()) return;

   // Spread Check
   int current_spread = (int)m_symbol.Spread();
   if(InpMaxSpreadPoints > 0 && current_spread > InpMaxSpreadPoints)
     {
      return; // Skip execution during high spread
     }

   double bid = m_symbol.Bid();
   double ask = m_symbol.Ask();
   double point = m_symbol.Point();
   if(point <= 0) point = _Point;

   double breakout_threshold = g_setup.signal_low - (InpEntryBufferPoints * point);

   // Breakout Condition: Bid price drops below Signal Low minus buffer
   if(bid <= breakout_threshold)
     {
      // Calculate Stop Loss & Take Profit relative to execution price
      double sl_distance_pts = (g_setup.signal_high - bid) / point + InpSLBufferPoints;

      if(InpUseSpreadSLBuffer)
        {
         sl_distance_pts += current_spread;
        }

      double sl_price = bid + (sl_distance_pts * point);
      double risk_pts = sl_price - bid;

      if(risk_pts <= 0)
        {
         Print("[ERROR] Invalid Risk Points calculation: ", risk_pts);
         g_setup.valid = false;
         return;
        }

      double tp_price = bid - (risk_pts * InpRiskRewardRatio);

      // Calculate Lot Size
      double lot_size = CalculateLotSize(risk_pts);

      // Validate Margins & Broker Limits
      lot_size = StandardizeLotSize(lot_size);
      if(!ValidateMargin(ORDER_TYPE_SELL, lot_size))
        {
         Print("[ERROR] Insufficient margin for lot size: ", lot_size);
         return;
        }

      // Select Filling Mode
      m_trade.SetTypeFilling(GetFillingMode());

      // Submit Short Market Order
      Print("[EXECUTING SHORT TRADE] Bid: ", bid, " | SL: ", sl_price, " | TP: ", tp_price, " | Lots: ", lot_size);

      if(m_trade.Sell(lot_size, _Symbol, bid, sl_price, tp_price, InpTradeComment))
        {
         Print("[ORDER SUCCESS] Short Position Opened. Ticket: ", m_trade.ResultOrder());
         g_setup.valid = false; // Mark setup executed
        }
      else
        {
         Print("[ORDER FAILED] Error Code: ", m_trade.ResultRetcode(), " | Description: ", m_trade.ResultRetcodeDescription());
        }
     }
  }

//+------------------------------------------------------------------+
//| Calculate VWAP (Volume Weighted Average Price)                   |
//+------------------------------------------------------------------+
double CalculateVWAP(string symbol, ENUM_TIMEFRAMES tf, int bar_shift, ENUM_TIMEFRAMES reset_period)
  {
   datetime bar_time = iTime(symbol, tf, bar_shift);
   if(bar_time == 0) return 0.0;

   // Determine start time of the reset period
   MqlDateTime dt;
   TimeToStruct(bar_time, dt);

   datetime period_start = 0;
   if(reset_period == PERIOD_D1)
     {
      dt.hour = 0; dt.min = 0; dt.sec = 0;
      period_start = StructToTime(dt);
     }
   else if(reset_period == PERIOD_W1)
     {
      int days_to_sub = (dt.day_of_week == 0) ? 6 : dt.day_of_week - 1;
      dt.hour = 0; dt.min = 0; dt.sec = 0;
      period_start = StructToTime(dt) - (days_to_sub * 86400);
     }
   else if(reset_period == PERIOD_MN1)
     {
      dt.day = 1; dt.hour = 0; dt.min = 0; dt.sec = 0;
      period_start = StructToTime(dt);
     }
   else
     {
      dt.hour = 0; dt.min = 0; dt.sec = 0;
      period_start = StructToTime(dt);
     }

   int start_bar = iBarShift(symbol, tf, period_start, false);
   if(start_bar < bar_shift) start_bar = bar_shift;

   int count = start_bar - bar_shift + 1;
   if(count <= 0) return 0.0;

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(symbol, tf, bar_shift, count, rates) <= 0) return 0.0;

   double cum_pv  = 0.0;
   double cum_vol = 0.0;

   for(int i = 0; i < ArraySize(rates); i++)
     {
      double typical_price = (rates[i].high + rates[i].low + rates[i].close) / 3.0;
      double vol = (double)(rates[i].tick_volume > 0 ? rates[i].tick_volume : rates[i].real_volume);
      if(vol <= 0) vol = 1.0;

      cum_pv  += typical_price * vol;
      cum_vol += vol;
     }

   if(cum_vol > 0) return (cum_pv / cum_vol);
   return (rates[0].high + rates[0].low + rates[0].close) / 3.0;
  }

//+------------------------------------------------------------------+
//| Calculate Position Lot Size Based on Risk Settings              |
//+------------------------------------------------------------------+
double CalculateLotSize(double risk_pts)
  {
   if(InpLotType == LOT_TYPE_FIXED)
     {
      return InpFixedLot;
     }

   double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double risk_amount = free_margin * (InpRiskPercent / 100.0);

   double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tick_size  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double point      = SymbolInfoDouble(_Symbol, SYMBOL_POINT);

   if(tick_size <= 0 || point <= 0 || risk_pts <= 0) return InpFixedLot;

   double risk_per_lot = (risk_pts * point / tick_size) * tick_value;
   if(risk_per_lot <= 0) return InpFixedLot;

   double calculated_lot = risk_amount / risk_per_lot;
   return calculated_lot;
  }

//+------------------------------------------------------------------+
//| Standardize Lot Size according to Broker Limits                  |
//+------------------------------------------------------------------+
double StandardizeLotSize(double lot)
  {
   double min_lot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_lot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   if(step_lot > 0)
     {
      lot = MathFloor(lot / step_lot) * step_lot;
     }

   if(lot < min_lot) lot = min_lot;
   if(lot > max_lot) lot = max_lot;

   return NormalizeDouble(lot, 2);
  }

//+------------------------------------------------------------------+
//| Margin Validation                                                |
//+------------------------------------------------------------------+
bool ValidateMargin(ENUM_ORDER_TYPE order_type, double lot_size)
  {
   double margin_required = 0.0;
   if(!OrderCalcMargin(order_type, _Symbol, lot_size, m_symbol.Bid(), margin_required))
     {
      return true; // Bypass if calc fails
     }

   double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   return (margin_required <= free_margin * 0.90); // Require 10% safety buffer
  }

//+------------------------------------------------------------------+
//| Detect Available Broker Filling Mode                             |
//+------------------------------------------------------------------+
ENUM_ORDER_TYPE_FILLING GetFillingMode()
  {
   uint filling = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0) return ORDER_FILLING_FOK;
   if((filling & SYMBOL_FILLING_IOC) != 0) return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
  }

//+------------------------------------------------------------------+
//| Check if active position already exists for this EA              |
//+------------------------------------------------------------------+
bool HasOpenPosition()
  {
   int total = PositionsTotal();
   for(int i = total - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
        {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
            PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
           {
            return true;
           }
        }
     }
   return false;
  }

//+------------------------------------------------------------------+
//| Create Chart Dashboard                                           |
//+------------------------------------------------------------------+
void CreateDashboard()
  {
   string name = "EA_Dashboard_BG";
   ObjectCreate(0, name, OBJ_RECTANGLE_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, g_dash_x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, g_dash_y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, 280);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, 150);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, (long)clrDarkSlateGray);
   ObjectSetInteger(0, name, OBJPROP_BORDER_TYPE, (long)BORDER_FLAT);
   ObjectSetInteger(0, name, OBJPROP_CORNER, (long)CORNER_LEFT_UPPER);

   CreateDashboardLabel("EA_Dash_Title", "MTF EMA + VWAP BREAKOUT EA", g_dash_x + 10, g_dash_y + 10, clrGold, 9, true);
   CreateDashboardLabel("EA_Dash_TF", "STF: " + EnumToString((ENUM_TIMEFRAMES)InpStrategyTimeframe) + " | HTF: " + EnumToString((ENUM_TIMEFRAMES)InpHigherTimeframe), g_dash_x + 10, g_dash_y + 35, clrWhite, 8, false);
   CreateDashboardLabel("EA_Dash_EMA", "Main EMA: " + IntegerToString(InpMainEMAPeriod) + " | HTF EMA: " + IntegerToString(InpHTFEMAPeriod), g_dash_x + 10, g_dash_y + 55, clrWhite, 8, false);
   CreateDashboardLabel("EA_Dash_Setup", "Pending Setup: None", g_dash_x + 10, g_dash_y + 80, clrYellow, 8, false);
   CreateDashboardLabel("EA_Dash_Pos", "Active Trade: None", g_dash_x + 10, g_dash_y + 105, clrLightSkyBlue, 8, false);
  }

//+------------------------------------------------------------------+
//| Create Label Helper                                              |
//+------------------------------------------------------------------+
void CreateDashboardLabel(string name, string text, int x, int y, color col, int font_size, bool bold)
  {
   ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, (long)col);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, font_size);
   ObjectSetString(0, name, OBJPROP_FONT, bold ? "Arial Bold" : "Arial");
   ObjectSetInteger(0, name, OBJPROP_CORNER, (long)CORNER_LEFT_UPPER);
  }

//+------------------------------------------------------------------+
//| Update Chart Dashboard                                           |
//+------------------------------------------------------------------+
void UpdateDashboard()
  {
   string setup_str = g_setup.valid ? ("Active (Low: " + DoubleToString(g_setup.signal_low, _Digits) + ")") : "None";
   color setup_col  = g_setup.valid ? clrLime : clrYellow;

   bool has_pos = HasOpenPosition();
   string pos_str = has_pos ? "1 Open Position" : "No Positions";
   color pos_col  = has_pos ? clrOrange : clrLightSkyBlue;

   ObjectSetString(0, "EA_Dash_Setup", OBJPROP_TEXT, "Pending Setup: " + setup_str);
   ObjectSetInteger(0, "EA_Dash_Setup", OBJPROP_COLOR, (long)setup_col);

   ObjectSetString(0, "EA_Dash_Pos", OBJPROP_TEXT, "Active Trade: " + pos_str);
   ObjectSetInteger(0, "EA_Dash_Pos", OBJPROP_COLOR, (long)pos_col);

   ChartRedraw(0);
  }

//+------------------------------------------------------------------+
//| Remove Chart Dashboard                                           |
//+------------------------------------------------------------------+
void RemoveDashboard()
  {
   ObjectDelete(0, "EA_Dashboard_BG");
   ObjectDelete(0, "EA_Dash_Title");
   ObjectDelete(0, "EA_Dash_TF");
   ObjectDelete(0, "EA_Dash_EMA");
   ObjectDelete(0, "EA_Dash_Setup");
   ObjectDelete(0, "EA_Dash_Pos");
  }
//+------------------------------------------------------------------+
