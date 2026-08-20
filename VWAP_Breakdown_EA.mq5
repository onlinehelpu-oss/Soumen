//+------------------------------------------------------------------+
//|                                           VWAP_Breakdown_EA.mq5 |
//|                                  Copyright 2025, EA Developer    |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "EA Developer"
#property link      "https://www.mql5.com"
#property version   "1.00"
#property description "MT5 Expert Advisor - VWAP Breakdown Strategy"
#property description "1. Red Signal Candle: High > VWAP and Close < VWAP on Strategy Timeframe"
#property description "2. Entry: Next immediate candle breaks below Signal Candle Low"
#property description "3. Stop Loss: Signal Candle High (+ optional buffer)"
#property description "4. Take Profit: Configurable Risk-to-Reward Ratio (1:1, 1:2, custom)"

#property tester_indicator "VWAP.ex5"

#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\PositionInfo.mqh>

//--- Input Parameters
input group "=== Strategy Settings ===";
input ENUM_TIMEFRAMES InpStrategyTF        = PERIOD_M15;    // Strategy Timeframe (M1, M3, M5, M15, M30, H1, D1)
input ENUM_TIMEFRAMES InpVWAPResetPeriod   = PERIOD_D1;     // VWAP Reset Period (PERIOD_D1, PERIOD_W1, PERIOD_MN1)

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

   // Attach VWAP to Chart in Visual Mode / Live Chart
   if(!MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_VISUAL_MODE))
     {
      ChartIndicatorAdd(0, 0, m_vwap_handle);
     }

   m_last_bar_time    = 0;
   m_pending_breakout = false;

   Print("[INFO] VWAP Breakdown EA Initialized Successfully. Strategy TF: ", EnumToString(InpStrategyTF),
         " | VWAP Reset: ", EnumToString(InpVWAPResetPeriod), " | RR: 1:", DoubleToString(InpRiskRewardRatio, 2));

   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   // Release indicator handle
   if(m_vwap_handle != INVALID_HANDLE)
     {
      IndicatorRelease(m_vwap_handle);
      m_vwap_handle = INVALID_HANDLE;
     }

   // Clean up visual objects
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
      // Verify if pending setup is still valid (must be inside the immediate next candle)
      datetime candle_0_time = iTime(_Symbol, InpStrategyTF, 0);
      if(candle_0_time != m_target_bar_time)
        {
         // Next immediate candle has closed without breakout -> Expire setup
         m_pending_breakout = false;
         Print("[INFO] Signal setup expired: Next candle closed without breaking Signal Low.");
        }
      else
        {
         // Check for price breakdown below signal low
         double trigger_price = m_signal_low - (InpSLBufferPoints * m_symbol.Point());
         double current_bid   = m_symbol.Bid();

         if(current_bid <= trigger_price)
           {
            // Trigger SELL Trade
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
   // If limit 1 position active and we already have a position, skip signal search
   if(InpOnePositionAtOnce && HasOpenPosition())
      return;

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   // Copy historical bar at index 1 (completed candle)
   if(CopyRates(_Symbol, InpStrategyTF, 1, 1, rates) < 1)
     {
      Print("[WARNING] CopyRates failed for Strategy Timeframe.");
      return;
     }

   // Index 0 of copied rates array corresponds to historical bar 1
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

   // Strategy Conditions:
   // 1. Red Candle: close < open
   // 2. Touched or crossed above VWAP: high > vwap_val
   // 3. Closed below VWAP: close < vwap_val
   bool is_red          = (close_p < open_p);
   bool high_above_vwap = (high_p > vwap_val);
   bool close_below_vwap= (close_p < vwap_val);

   if(is_red && high_above_vwap && close_below_vwap)
     {
      m_pending_breakout = true;
      m_signal_high      = high_p;
      m_signal_low       = low_p;
      m_signal_time      = rates[0].time;
      m_target_bar_time  = iTime(_Symbol, InpStrategyTF, 0); // Immediate next bar (index 0)

      Print("[SIGNAL DETECTED] Valid Red VWAP Breakdown Signal Candle at ", TimeToString(m_signal_time),
            " | High: ", DoubleToString(m_signal_high, _Digits),
            " | Low: ", DoubleToString(m_signal_low, _Digits),
            " | VWAP: ", DoubleToString(vwap_val, _Digits),
            " -> Waiting for next-candle breakdown trigger below: ", DoubleToString(m_signal_low - (InpSLBufferPoints * m_symbol.Point()), _Digits));
     }
  }

//+------------------------------------------------------------------+
//| Execute Short Entry Market Order                                 |
//+------------------------------------------------------------------+
void ExecuteShortEntry()
  {
   // Double check open position constraint
   if(InpOnePositionAtOnce && HasOpenPosition())
     {
      m_pending_breakout = false;
      return;
     }

   m_pending_breakout = false; // Clear flag to prevent multi-triggering

   double entry_price = m_symbol.Bid();
   double sl_price    = m_signal_high + (InpSLBufferPoints * m_symbol.Point());

   // Normalize SL
   sl_price = NormalizeDouble(sl_price, _Digits);

   // Ensure SL is strictly above entry price
   double min_stop_level = m_symbol.StopsLevel() * m_symbol.Point();
   if(sl_price <= entry_price + min_stop_level)
     {
      sl_price = NormalizeDouble(entry_price + min_stop_level + (10 * m_symbol.Point()), _Digits);
     }

   double risk_distance = sl_price - entry_price;
   if(risk_distance <= 0)
     {
      Print("[ERROR] Invalid Risk Distance: ", risk_distance);
      return;
     }

   // Target Take Profit based on user-selected Risk-to-Reward ratio
   double tp_distance = risk_distance * InpRiskRewardRatio;
   double tp_price    = NormalizeDouble(entry_price - tp_distance, _Digits);

   // Calculate position volume
   double trade_lots = CalculateLotSize(risk_distance);
   if(trade_lots <= 0)
     {
      Print("[ERROR] Volume calculation returned invalid lot size.");
      return;
     }

   // Validate Margin Requirements
   double req_margin = 0.0;
   if(OrderCalcMargin(ORDER_TYPE_SELL, _Symbol, trade_lots, entry_price, req_margin))
     {
      double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
      if(req_margin > free_margin)
        {
         Print("[WARNING] Insufficient margin. Required: ", req_margin, ", Free: ", free_margin, ". Rescaling lot size.");
         trade_lots = NormalizeLotSize(trade_lots * (free_margin * 0.9 / req_margin));
         if(trade_lots < m_symbol.LotsMin())
           {
            Print("[ERROR] Rescaled lot size below broker minimum limit.");
            return;
           }
        }
     }

   // Submit Market Sell Order
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
                 "\nTarget RR: 1:" + DoubleToString(InpRiskRewardRatio, 2) +
                 "\nPending Setup: " + (m_pending_breakout ? "YES (Signal Low: " + DoubleToString(m_signal_low, _Digits) + ")" : "NO") +
                 "\nStatus: " + status;

   Comment(text);
  }
//+------------------------------------------------------------------+
