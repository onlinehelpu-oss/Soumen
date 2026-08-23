//+------------------------------------------------------------------+
//|                                                    VWAP only.mq5 |
//|                                  Copyright 2025, EA Developer    |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "EA Developer"
#property link      "https://www.mql5.com"
#property version   "1.00"
#property description "MT5 Expert Advisor - VWAP Breakdown Strategy with Pivot Point Standard Filter"
#property description "1. Red Signal Candle: High > VWAP and Close < VWAP on Strategy Timeframe"
#property description "2. Pivot Filter: Price strictly below Pivot level (P) if Pivot Filter is enabled"
#property description "3. Entry: Next immediate candle breaks below Signal Candle Low"
#property description "4. Stop Loss: Signal Candle High (+ optional buffer)"
#property description "5. Take Profit: Configurable Risk-to-Reward Ratio (1:1, 1:2, custom)"

#property tester_indicator "VWAP.ex5"
#property tester_indicator "Pivot_Points_Standard.ex5"

#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\PositionInfo.mqh>

enum ENUM_PIVOT_TYPE_EA
  {
   EA_PIVOT_TRADITIONAL = 0, // Traditional
   EA_PIVOT_FIBONACCI   = 1, // Fibonacci
   EA_PIVOT_WOODIE      = 2, // Woodie
   EA_PIVOT_CLASSIC     = 3, // Classic
   EA_PIVOT_DEMARK      = 4, // DM (DeMark)
   EA_PIVOT_CAMARILLA   = 5  // Camarilla
  };

enum ENUM_PIVOT_TIMEFRAME_EA
  {
   EA_PIVOT_TF_AUTO    = 0, // Auto
   EA_PIVOT_TF_DAILY   = 1, // Daily
   EA_PIVOT_TF_WEEKLY  = 2, // Weekly
   EA_PIVOT_TF_MONTHLY = 3, // Monthly
   EA_PIVOT_TF_YEARLY  = 4  // Yearly
  };

//--- Input Parameters
input group "=== Strategy Settings ===";
input ENUM_TIMEFRAMES InpStrategyTF        = PERIOD_M15;    // Strategy Timeframe (M1, M3, M5, M15, M30, H1, D1)
input ENUM_TIMEFRAMES InpVWAPResetPeriod   = PERIOD_D1;     // VWAP Reset Period (PERIOD_D1, PERIOD_W1, PERIOD_MN1)

input group "=== Pivot Point Filter & Settings ===";
input bool                  InpUsePivotFilter    = true;                 // Use Pivot Filter (Sell only below Pivot P)
input bool                  InpShowPivotsOnChart = true;                 // Plot Pivot Points Standard on Chart (ON/OFF)
input ENUM_PIVOT_TYPE_EA    InpPivotType         = EA_PIVOT_TRADITIONAL; // Pivot Type
input ENUM_PIVOT_TIMEFRAME_EA InpPivotTimeframe  = EA_PIVOT_TF_AUTO;      // Pivot Timeframe

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

int            m_vwap_handle        = INVALID_HANDLE;
int            m_pivot_handle       = INVALID_HANDLE;
datetime       m_last_bar_time      = 0;
bool           m_pending_breakout   = false;
double         m_signal_high        = 0.0;
double         m_signal_low         = 0.0;
datetime       m_signal_time        = 0;
datetime       m_target_bar_time    = 0;
string         m_dashboard_obj_prefix = "VWAP_EA_Dash_";

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
  {
   // Initialize Symbol Info
   if(!m_symbol.Name(_Symbol))
     {
      Print("[ERROR] Failed to initialize symbol info for ", _Symbol);
      return(INIT_FAILED);
     }
   m_symbol.Refresh();

   // Set Trade Magic
   m_trade.SetExpertMagicNumber(InpMagicNumber);

   // Configure Filling Mode
   uint filling = GetFillingMode();
   m_trade.SetTypeFilling((ENUM_ORDER_TYPE_FILLING)filling);

   // Load VWAP Indicator Handle
   m_vwap_handle = iCustom(_Symbol, InpStrategyTF, "VWAP", InpVWAPResetPeriod);
   if(m_vwap_handle == INVALID_HANDLE)
     {
      Print("[ERROR] Failed to create VWAP indicator handle. Ensure VWAP.ex5 is compiled in Indicators folder.");
      return(INIT_FAILED);
     }

   // Load Pivot Points Standard Indicator Handle
   m_pivot_handle = iCustom(_Symbol, InpStrategyTF, "Pivot_Points_Standard", InpPivotType, InpPivotTimeframe, InpShowPivotsOnChart);
   if(m_pivot_handle == INVALID_HANDLE)
     {
      Print("[WARNING] Failed to load Pivot_Points_Standard indicator handle.");
     }

   // Attach Indicators to Chart in Visual Mode / Live Chart
   if(!MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_VISUAL_MODE))
     {
      ChartIndicatorAdd(0, 0, m_vwap_handle);
      if(InpShowPivotsOnChart && m_pivot_handle != INVALID_HANDLE)
        {
         ChartIndicatorAdd(0, 0, m_pivot_handle);
        }
     }

   m_last_bar_time    = 0;
   m_pending_breakout = false;

   Print("[INFO] VWAP Breakdown EA Initialized. Strategy TF: ", EnumToString(InpStrategyTF),
         " | Pivot Filter: ", (InpUsePivotFilter ? "ON" : "OFF"),
         " | Pivot Plot: ", (InpShowPivotsOnChart ? "ON" : "OFF"),
         " | RR: 1:", DoubleToString(InpRiskRewardRatio, 2));

   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   if(m_vwap_handle != INVALID_HANDLE)
     {
      IndicatorRelease(m_vwap_handle);
      m_vwap_handle = INVALID_HANDLE;
     }

   if(m_pivot_handle != INVALID_HANDLE)
     {
      IndicatorRelease(m_pivot_handle);
      m_pivot_handle = INVALID_HANDLE;
     }

   ObjectsDeleteAllPrefix(m_dashboard_obj_prefix);
   Comment("");
  }

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
  {
   if(!m_symbol.RefreshRates())
      return;

   // Check spread filter
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
     {
      Print("[WARNING] CopyRates failed for Strategy Timeframe.");
      return;
     }

   double open_p  = rates[0].open;
   double high_p  = rates[0].high;
   double low_p   = rates[0].low;
   double close_p = rates[0].close;

   // Read VWAP value for completed bar 1
   double vwap_arr[];
   ArraySetAsSeries(vwap_arr, true);
   if(CopyBuffer(m_vwap_handle, 0, 1, 1, vwap_arr) < 1)
     {
      Print("[WARNING] CopyBuffer failed for VWAP handle.");
      return;
     }

   double vwap_val = vwap_arr[0];

   // Read Pivot P level if filter active
   double pivot_p_val = 0.0;
   bool pivot_condition_ok = true;

   if(InpUsePivotFilter && m_pivot_handle != INVALID_HANDLE)
     {
      double p_arr[];
      ArraySetAsSeries(p_arr, true);
      if(CopyBuffer(m_pivot_handle, 0, 1, 1, p_arr) >= 1)
        {
         pivot_p_val = p_arr[0];
         // Price must be strictly below Pivot P
         if(close_p >= pivot_p_val)
           {
            pivot_condition_ok = false;
           }
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
//| Remove Visual Dashboard Objects                                  |
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

   string text = "=== VWAP BREAKDOWN EA ===" +
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
