//+------------------------------------------------------------------+
//|                                                Vwap EMA sell.mq5 |
//|                                  Copyright 2025, EA Developer    |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "EA Developer"
#property link      "https://www.mql5.com"
#property version   "2.00"
#property description "All-In-One Single File VWAP Breakdown EA with Pivot Points Standard"
#property description "1. Red Signal Candle: High > VWAP and Close < VWAP on Strategy Timeframe"
#property description "2. Pivot Filter (Optional): Close < Pivot Level P"
#property description "3. Entry: Next immediate candle breaks below Signal Candle Low"
#property description "4. Stop Loss: Signal Candle High (+ optional buffer)"
#property description "5. Take Profit: Configurable Risk-to-Reward Ratio (1:1, 1:2, custom)"

#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\PositionInfo.mqh>

enum ENUM_PIVOT_TYPE_ALL
  {
   PIVOT_TRADITIONAL = 0, // Traditional
   PIVOT_FIBONACCI   = 1, // Fibonacci
   PIVOT_WOODIE      = 2, // Woodie
   PIVOT_CLASSIC     = 3, // Classic
   PIVOT_DEMARK      = 4, // DM (DeMark)
   PIVOT_CAMARILLA   = 5  // Camarilla
  };

enum ENUM_PIVOT_TIMEFRAME_ALL
  {
   PIVOT_TF_AUTO    = 0, // Auto
   PIVOT_TF_DAILY   = 1, // Daily
   PIVOT_TF_WEEKLY  = 2, // Weekly
   PIVOT_TF_MONTHLY = 3, // Monthly
   PIVOT_TF_YEARLY  = 4  // Yearly
  };

//--- Input Parameters
input group "=== Strategy Settings ===";
input ENUM_TIMEFRAMES InpStrategyTF        = PERIOD_M15;    // Strategy Timeframe (M1, M3, M5, M15, M30, H1, D1)
input ENUM_TIMEFRAMES InpVWAPResetPeriod   = PERIOD_D1;     // VWAP Reset Period (PERIOD_D1, PERIOD_W1, PERIOD_MN1)
input bool            InpShowVWAPOnChart   = true;          // Plot Live VWAP Line & Price Tag on Chart (ON/OFF)

input group "=== Pivot Point Filter & Settings ===";
input bool                    InpUsePivotFilter    = true;              // Use Pivot Filter (Sell only below Pivot P)
input bool                    InpShowPivotsOnChart = true;              // Plot Pivot Lines on Chart (ON/OFF)
input ENUM_PIVOT_TYPE_ALL     InpPivotType         = PIVOT_TRADITIONAL; // Pivot Type
input ENUM_PIVOT_TIMEFRAME_ALL InpPivotTimeframe  = PIVOT_TF_AUTO;     // Pivot Timeframe

input group "=== Risk & Target Management ===";
input double          InpRiskRewardRatio   = 2.0;           // Risk-to-Reward Ratio (1.0 = 1:1, 2.0 = 1:2, etc.)
input double          InpLotSize           = 0.01;          // Fixed Trade Volume (Lots)
input bool            InpUseRiskPercent    = false;         // Use % Risk Position Sizing
input double          InpRiskPercent       = 1.0;           // Account Free Margin Risk % per trade
input double          InpSLBufferPoints    = 0.0;           // Stop Loss Buffer (in Points)
input double          InpMaxSpreadPoints   = 50.0;          // Max Allowed Spread (in Points, 0 = Disabled)

input group "=== Execution Settings ===";
input ulong           InpMagicNumber       = 20250820;      // EA Magic Number
input bool            InpOnePositionAtOnce = true;          // Limit to 1 Open Position at a time
input bool            InpShowDashboard     = true;          // Show On-Chart Visual Dashboard

//--- Global Variables & Classes
CTrade         m_trade;
CSymbolInfo    m_symbol;
CPositionInfo  m_position;

datetime       m_last_bar_time      = 0;
bool           m_pending_breakout   = false;
double         m_signal_high        = 0.0;
double         m_signal_low         = 0.0;
datetime       m_signal_time        = 0;
datetime       m_target_bar_time    = 0;

string         m_obj_prefix         = "VWAP_AllInOne_";

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
  {
   if(!m_symbol.Name(_Symbol))
     {
      Print("[ERROR] Failed to initialize symbol info for ", _Symbol);
      return(INIT_FAILED);
     }
   m_symbol.Refresh();

   m_trade.SetExpertMagicNumber(InpMagicNumber);
   uint filling = GetFillingMode();
   m_trade.SetTypeFilling((ENUM_ORDER_TYPE_FILLING)filling);

   m_last_bar_time    = 0;
   m_pending_breakout = false;

   Print("[INFO] VWAP Breakdown All-In-One EA Initialized. Strategy TF: ", EnumToString(InpStrategyTF),
         " | Pivot Filter: ", (InpUsePivotFilter ? "ON" : "OFF"),
         " | Target RR: 1:", DoubleToString(InpRiskRewardRatio, 2));

   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   ObjectsDeleteAllPrefix(m_obj_prefix);
   Comment("");
  }

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
  {
   if(!m_symbol.RefreshRates())
      return;

   double spread_pts = (m_symbol.Ask() - m_symbol.Bid()) / m_symbol.Point();
   if(InpMaxSpreadPoints > 0 && spread_pts > InpMaxSpreadPoints)
     {
      UpdateDashboard("Spread too high: " + DoubleToString(spread_pts, 1) + " pts");
      return;
     }

   // 1. Check for Bar Completion on Strategy Timeframe
   datetime current_bar_time = iTime(_Symbol, InpStrategyTF, 0);
   if(current_bar_time != m_last_bar_time)
     {
      m_last_bar_time = current_bar_time;
      CheckForNewSignal();
     }

   // 2. Monitor Pending Breakout Setup (Next Candle Execution)
   if(m_pending_breakout)
     {
      datetime candle_0_time = iTime(_Symbol, InpStrategyTF, 0);
      if(candle_0_time != m_target_bar_time)
        {
         m_pending_breakout = false;
         Print("[INFO] Signal setup expired: Next candle closed without breaking Signal Low.");
        }
      else
        {
         double trigger_price = m_signal_low - (InpSLBufferPoints * m_symbol.Point());
         double current_bid   = m_symbol.Bid();

         if(current_bid <= trigger_price)
           {
            ExecuteShortEntry();
           }
        }
     }

   // 3. Render / Update Live Chart Visuals (VWAP line & Pivot levels)
   RenderChartVisuals();

   if(InpShowDashboard)
      UpdateDashboard("Active");
  }

//+------------------------------------------------------------------+
//| Evaluate completed candle for Red VWAP Breakdown Signal          |
//+------------------------------------------------------------------+
void CheckForNewSignal()
  {
   if(InpOnePositionAtOnce && HasOpenPosition())
      return;

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(_Symbol, InpStrategyTF, 1, 1, rates) < 1)
      return;

   double open_p  = rates[0].open;
   double high_p  = rates[0].high;
   double low_p   = rates[0].low;
   double close_p = rates[0].close;

   // 1. Internal VWAP Calculation for Bar 1
   double vwap_val = CalculateVWAP(InpStrategyTF, 1);
   if(vwap_val <= 0)
      return;

   // 2. Internal Pivot Level P Calculation
   double pivot_p_val = CalculatePivotP(InpStrategyTF, 1);
   bool pivot_condition_ok = true;

   if(InpUsePivotFilter && pivot_p_val > 0)
     {
      if(close_p >= pivot_p_val)
        {
         pivot_condition_ok = false;
        }
     }

   // Strategy Conditions:
   // 1. Red Candle: close < open
   // 2. Touched or crossed above VWAP: high > vwap_val
   // 3. Closed below VWAP: close < vwap_val
   // 4. Pivot Filter: close < Pivot P
   bool is_red           = (close_p < open_p);
   bool high_above_vwap  = (high_p > vwap_val);
   bool close_below_vwap = (close_p < vwap_val);

   if(is_red && high_above_vwap && close_below_vwap && pivot_condition_ok)
     {
      m_pending_breakout = true;
      m_signal_high      = high_p;
      m_signal_low       = low_p;
      m_signal_time      = rates[0].time;
      m_target_bar_time  = iTime(_Symbol, InpStrategyTF, 0);

      Print("[SIGNAL DETECTED] Red VWAP Breakdown Signal at ", TimeToString(m_signal_time),
            " | High: ", DoubleToString(m_signal_high, _Digits),
            " | Low: ", DoubleToString(m_signal_low, _Digits),
            " | VWAP: ", DoubleToString(vwap_val, _Digits),
            " | Pivot P: ", DoubleToString(pivot_p_val, _Digits));
     }
  }

//+------------------------------------------------------------------+
//| Execute Short Entry Market Order                                 |
//+------------------------------------------------------------------+
void ExecuteShortEntry()
  {
   if(InpOnePositionAtOnce && HasOpenPosition())
     {
      m_pending_breakout = false;
      return;
     }

   m_pending_breakout = false;

   double entry_price = m_symbol.Bid();
   double sl_price    = m_signal_high + (InpSLBufferPoints * m_symbol.Point());
   sl_price = NormalizeDouble(sl_price, _Digits);

   double min_stop_level = m_symbol.StopsLevel() * m_symbol.Point();
   if(sl_price <= entry_price + min_stop_level)
     {
      sl_price = NormalizeDouble(entry_price + min_stop_level + (10 * m_symbol.Point()), _Digits);
     }

   double risk_distance = sl_price - entry_price;
   if(risk_distance <= 0)
      return;

   double tp_distance = risk_distance * InpRiskRewardRatio;
   double tp_price    = NormalizeDouble(entry_price - tp_distance, _Digits);

   double trade_lots = CalculateLotSize(risk_distance);
   if(trade_lots <= 0)
      return;

   double req_margin = 0.0;
   if(OrderCalcMargin(ORDER_TYPE_SELL, _Symbol, trade_lots, entry_price, req_margin))
     {
      double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
      if(req_margin > free_margin)
        {
         trade_lots = NormalizeLotSize(trade_lots * (free_margin * 0.9 / req_margin));
         if(trade_lots < m_symbol.LotsMin())
            return;
        }
     }

   Print("[EXECUTION] Submitting Market SELL | Entry: ", entry_price,
         " | SL: ", sl_price, " | TP: ", tp_price, " | Volume: ", trade_lots);

   if(m_trade.Sell(trade_lots, _Symbol, entry_price, sl_price, tp_price, "VWAP Breakdown EA"))
     {
      Print("[SUCCESS] Short Position Opened Ticket #", m_trade.ResultOrder());
     }
   else
     {
      Print("[ERROR] Sell Order Failed. Code: ", m_trade.ResultRetcode(), " | Description: ", m_trade.ResultRetcodeDescription());
     }
  }

//+------------------------------------------------------------------+
//| Calculate Intraday/Period VWAP Internally                        |
//+------------------------------------------------------------------+
double CalculateVWAP(ENUM_TIMEFRAMES tf, int target_bar)
  {
   datetime bar_time = iTime(_Symbol, tf, target_bar);
   if(bar_time <= 0) return 0.0;

   datetime period_start = 0;
   MqlDateTime dt;
   TimeToStruct(bar_time, dt);

   if(InpVWAPResetPeriod == PERIOD_W1)
     {
      int day_offset = (dt.day_of_week == 0) ? 6 : dt.day_of_week - 1;
      period_start = bar_time - (day_offset * 86400) - (dt.hour * 3600 + dt.min * 60 + dt.sec);
     }
   else if(InpVWAPResetPeriod == PERIOD_MN1)
     {
      dt.day = 1; dt.hour = 0; dt.min = 0; dt.sec = 0;
      period_start = StructToTime(dt);
     }
   else // PERIOD_D1
     {
      dt.hour = 0; dt.min = 0; dt.sec = 0;
      period_start = StructToTime(dt);
     }

   int start_bar = iBarShift(_Symbol, tf, period_start, false);
   if(start_bar < target_bar) start_bar = target_bar + 100;

   int count = start_bar - target_bar + 1;
   if(count <= 0) count = 1;

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(_Symbol, tf, target_bar, count, rates) < count)
      return 0.0;

   double cum_pv  = 0.0;
   double cum_vol = 0.0;

   for(int k = count - 1; k >= 0; k--)
     {
      double typical_price = (rates[k].high + rates[k].low + rates[k].close) / 3.0;
      double vol           = (double)(rates[k].real_volume > 0 ? rates[k].real_volume : rates[k].tick_volume);
      if(vol <= 0) vol = 1.0;

      cum_pv  += typical_price * vol;
      cum_vol += vol;
     }

   return (cum_vol > 0) ? (cum_pv / cum_vol) : rates[0].close;
  }

//+------------------------------------------------------------------+
//| Calculate Pivot Points Standard Level P Internally               |
//+------------------------------------------------------------------+
double CalculatePivotP(ENUM_TIMEFRAMES tf, int target_bar)
  {
   datetime bar_time = iTime(_Symbol, tf, target_bar);
   ENUM_TIMEFRAMES anchor_tf = PERIOD_D1;

   if(InpPivotTimeframe == PIVOT_TF_WEEKLY)  anchor_tf = PERIOD_W1;
   else if(InpPivotTimeframe == PIVOT_TF_MONTHLY || InpPivotTimeframe == PIVOT_TF_YEARLY) anchor_tf = PERIOD_MN1;
   else if(InpPivotTimeframe == PIVOT_TF_AUTO)
     {
      if(tf <= PERIOD_M15)      anchor_tf = PERIOD_D1;
      else if(tf <= PERIOD_H4)  anchor_tf = PERIOD_W1;
      else                      anchor_tf = PERIOD_MN1;
     }

   int htf_bar = iBarShift(_Symbol, anchor_tf, bar_time, false);
   int target_htf = (htf_bar >= 0) ? htf_bar + 1 : 1;

   double htf_high  = iHigh(_Symbol, anchor_tf, target_htf);
   double htf_low   = iLow(_Symbol, anchor_tf, target_htf);
   double htf_close = iClose(_Symbol, anchor_tf, target_htf);
   double htf_open  = iOpen(_Symbol, anchor_tf, target_htf);

   if(htf_high <= 0 || htf_low <= 0 || htf_close <= 0)
      return 0.0;

   if(InpPivotType == PIVOT_WOODIE)
      return (htf_high + htf_low + 2.0 * htf_open) / 4.0;
   else if(InpPivotType == PIVOT_DEMARK)
     {
      double x = 0;
      if(htf_close < htf_open)      x = htf_high + 2.0 * htf_low + htf_close;
      else if(htf_close > htf_open) x = 2.0 * htf_high + htf_low + htf_close;
      else                          x = htf_high + htf_low + 2.0 * htf_close;
      return x / 4.0;
     }

   return (htf_high + htf_low + htf_close) / 3.0;
  }

//+------------------------------------------------------------------+
//| Render Live Chart Visuals (VWAP & Pivot Lines/Labels)            |
//+------------------------------------------------------------------+
void RenderChartVisuals()
  {
   if(MQLInfoInteger(MQL_TESTER) && !MQLInfoInteger(MQL_VISUAL_MODE))
      return;

   // 1. Live VWAP Line & Value Tag
   if(InpShowVWAPOnChart)
     {
      double current_vwap = CalculateVWAP(InpStrategyTF, 0);
      if(current_vwap > 0)
        {
         string line_name = m_obj_prefix + "VWAP_Line";
         string text_name = m_obj_prefix + "VWAP_Tag";

         if(ObjectFind(0, line_name) < 0)
           {
            ObjectCreate(0, line_name, OBJ_HLINE, 0, 0, current_vwap);
            ObjectSetInteger(0, line_name, OBJPROP_COLOR, (long)clrDodgerBlue);
            ObjectSetInteger(0, line_name, OBJPROP_STYLE, (long)STYLE_SOLID);
            ObjectSetInteger(0, line_name, OBJPROP_WIDTH, 2);
           }
         else
           {
            ObjectMove(0, line_name, 0, 0, current_vwap);
           }

         datetime last_time = iTime(_Symbol, InpStrategyTF, 0);
         datetime tag_time  = last_time + PeriodSeconds(InpStrategyTF) * 3;

         if(ObjectFind(0, text_name) < 0)
           {
            ObjectCreate(0, text_name, OBJ_TEXT, 0, tag_time, current_vwap);
            ObjectSetInteger(0, text_name, OBJPROP_ANCHOR, (long)ANCHOR_LEFT);
            ObjectSetInteger(0, text_name, OBJPROP_COLOR, (long)clrDodgerBlue);
            ObjectSetInteger(0, text_name, OBJPROP_FONTSIZE, 9);
            ObjectSetString(0, text_name, OBJPROP_FONT, "Arial Bold");
           }
         else
           {
            ObjectMove(0, text_name, 0, tag_time, current_vwap);
           }
         ObjectSetString(0, text_name, OBJPROP_TEXT, " VWAP " + DoubleToString(current_vwap, _Digits));
        }
     }
   else
     {
      ObjectDelete(0, m_obj_prefix + "VWAP_Line");
      ObjectDelete(0, m_obj_prefix + "VWAP_Tag");
     }

   // 2. Pivot Level P Line & Label
   if(InpShowPivotsOnChart)
     {
      double current_pivot_p = CalculatePivotP(InpStrategyTF, 0);
      if(current_pivot_p > 0)
        {
         string line_name = m_obj_prefix + "PivotP_Line";
         string text_name = m_obj_prefix + "PivotP_Tag";

         if(ObjectFind(0, line_name) < 0)
           {
            ObjectCreate(0, line_name, OBJ_HLINE, 0, 0, current_pivot_p);
            ObjectSetInteger(0, line_name, OBJPROP_COLOR, (long)clrDarkOrange);
            ObjectSetInteger(0, line_name, OBJPROP_STYLE, (long)STYLE_DASH);
            ObjectSetInteger(0, line_name, OBJPROP_WIDTH, 1);
           }
         else
           {
            ObjectMove(0, line_name, 0, 0, current_pivot_p);
           }

         datetime last_time = iTime(_Symbol, InpStrategyTF, 0);
         datetime tag_time  = last_time + PeriodSeconds(InpStrategyTF) * 3;

         if(ObjectFind(0, text_name) < 0)
           {
            ObjectCreate(0, text_name, OBJ_TEXT, 0, tag_time, current_pivot_p);
            ObjectSetInteger(0, text_name, OBJPROP_ANCHOR, (long)ANCHOR_LEFT);
            ObjectSetInteger(0, text_name, OBJPROP_COLOR, (long)clrDarkOrange);
            ObjectSetInteger(0, text_name, OBJPROP_FONTSIZE, 9);
            ObjectSetString(0, text_name, OBJPROP_FONT, "Arial Bold");
           }
         else
           {
            ObjectMove(0, text_name, 0, tag_time, current_pivot_p);
           }
         ObjectSetString(0, text_name, OBJPROP_TEXT, " P " + DoubleToString(current_pivot_p, _Digits));
        }
     }
   else
     {
      ObjectDelete(0, m_obj_prefix + "PivotP_Line");
      ObjectDelete(0, m_obj_prefix + "PivotP_Tag");
     }
  }

//+------------------------------------------------------------------+
//| Calculate Lot Size based on Risk Settings                        |
//+------------------------------------------------------------------+
double CalculateLotSize(double risk_distance_price)
  {
   if(!InpUseRiskPercent)
      return NormalizeLotSize(InpLotSize);

   double free_margin  = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double risk_amount  = free_margin * (InpRiskPercent / 100.0);
   double tick_value   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tick_size    = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);

   if(tick_value <= 0 || tick_size <= 0 || risk_distance_price <= 0)
      return NormalizeLotSize(InpLotSize);

   double loss_per_lot = (risk_distance_price / tick_size) * tick_value;
   if(loss_per_lot <= 0)
      return NormalizeLotSize(InpLotSize);

   double calculated_lots = risk_amount / loss_per_lot;
   return NormalizeLotSize(calculated_lots);
  }

//+------------------------------------------------------------------+
//| Normalize Lot Size according to Broker Step and Limits            |
//+------------------------------------------------------------------+
double NormalizeLotSize(double lots)
  {
   double min_lot  = m_symbol.LotsMin();
   double max_lot  = m_symbol.LotsMax();
   double step_lot = m_symbol.LotsStep();

   if(step_lot > 0)
      lots = MathFloor(lots / step_lot) * step_lot;

   if(lots < min_lot)
      lots = min_lot;
   if(lots > max_lot)
      lots = max_lot;

   return NormalizeDouble(lots, 2);
  }

//+------------------------------------------------------------------+
//| Check if open position exists for this Symbol & Magic Number     |
//+------------------------------------------------------------------+
bool HasOpenPosition()
  {
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
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
//| Determine Supported Order Filling Mode                           |
//+------------------------------------------------------------------+
uint GetFillingMode()
  {
   uint mode = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);

   if((mode & SYMBOL_FILLING_FOK) != 0)
      return ORDER_FILLING_FOK;
   if((mode & SYMBOL_FILLING_IOC) != 0)
      return ORDER_FILLING_IOC;

   return ORDER_FILLING_RETURN;
  }

//+------------------------------------------------------------------+
//| Remove Visual Objects                                            |
//+------------------------------------------------------------------+
void ObjectsDeleteAllPrefix(string prefix)
  {
   int total = ObjectsTotal(0, -1, -1);
   for(int i = total - 1; i >= 0; i--)
     {
      string name = ObjectName(0, i, -1, -1);
      if(StringFind(name, prefix) == 0)
         ObjectDelete(0, name);
     }
  }

//+------------------------------------------------------------------+
//| Update Visual Dashboard on Chart                                 |
//+------------------------------------------------------------------+
void UpdateDashboard(string status)
  {
   if(!InpShowDashboard)
      return;

   string text = "=== VWAP BREAKDOWN ALL-IN-ONE EA ===" +
                 "\nStrategy TF: " + EnumToString(InpStrategyTF) +
                 "\nVWAP Period: " + EnumToString(InpVWAPResetPeriod) +
                 "\nPivot Filter: " + (InpUsePivotFilter ? "ON (Below P)" : "OFF") +
                 "\nPivot Plot: " + (InpShowPivotsOnChart ? "ON" : "OFF") +
                 "\nTarget RR: 1:" + DoubleToString(InpRiskRewardRatio, 2) +
                 "\nPending Setup: " + (m_pending_breakout ? "YES (Signal Low: " + DoubleToString(m_signal_low, _Digits) + ")" : "NO") +
                 "\nStatus: " + status;

   Comment(text);
  }
//+------------------------------------------------------------------+
