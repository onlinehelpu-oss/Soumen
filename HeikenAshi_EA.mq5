//+------------------------------------------------------------------+
//|                                              HeikenAshi_EA.mq5   |
//|                                 Copyright 2025, Expert Advisor   |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2025"
#property link      "https://www.mql5.com"
#property version   "1.00"
#property description "Professional MetaTrader 5 Expert Advisor based on Heiken Ashi Strategy."
#property description "Executes Sell breakout entries when a Red HA candle with no upper wick is preceded by a Green HA candle."

// Optional indicator hints for MT5 Strategy Tester asset inclusion
#property tester_indicator "Heiken_Ashi Series.ex5"
#property tester_indicator "Heiken_Ashi.ex5"
#property tester_indicator "Examples\\Heiken_Ashi.ex5"

#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>

//--- Enums
enum ENUM_SIGNAL_TIMEFRAME
  {
   TF_M1  = PERIOD_M1,  // M1 / 1 Minute
   TF_M3  = PERIOD_M3,  // M3 / 3 Minutes
   TF_M5  = PERIOD_M5,  // M5 / 5 Minutes
   TF_M15 = PERIOD_M15, // M15 / 15 Minutes
   TF_M30 = PERIOD_M30, // M30 / 30 Minutes
   TF_H1  = PERIOD_H1,  // H1 / 60 Minutes
   TF_H4  = PERIOD_H4,  // H4 / 4 Hours
   TF_D1  = PERIOD_D1   // D1 / 1 Day
  };

enum ENUM_BREAKOUT_PRICE_TYPE
  {
   BREAKOUT_HA_LOW   = 0, // Heiken Ashi Signal Candle Low
   BREAKOUT_REAL_LOW = 1  // Real (Raw) Signal Candle Low
  };

enum ENUM_SL_PRICE_TYPE
  {
   SL_GREEN_HA_HIGH   = 0, // Green HA Candle High
   SL_GREEN_REAL_HIGH = 1  // Green Real (Raw) Candle High
  };

enum ENUM_LOT_TYPE
  {
   LOT_FIXED    = 0, // Fixed Lot Size
   LOT_RISK_PCT = 1  // Risk Percentage of Balance
  };

//--- Heiken Ashi Candle Structure
struct HA_Candle
  {
   double            open;
   double            high;
   double            low;
   double            close;
   datetime          time;
   bool              is_red;
   bool              is_green;
   bool              no_upper_wick;
  };

//--- Input Parameters
sinput group "=== Timeframe Configuration ==="
input ENUM_SIGNAL_TIMEFRAME InpSignalTimeframe         = TF_M5;                 // Signal Timeframe

sinput group "=== Strategy Parameters ==="
input ENUM_BREAKOUT_PRICE_TYPE InpBreakoutLevel        = BREAKOUT_HA_LOW;       // Breakout Level Source
input ENUM_SL_PRICE_TYPE       InpSLPriceLevel         = SL_GREEN_HA_HIGH;      // Stop Loss Level Source
input double                   InpUpperWickTolerance   = 0.0;                   // Upper Wick Max Tolerance (Points)
input double                   InpEntryBufferPoints    = 0.0;                   // Entry Buffer Below Low (Points)
input double                   InpSLBufferPoints       = 0.0;                   // Stop Loss Buffer Above High (Points)

sinput group "=== MAIN EMA Filter Configuration ==="
input bool                     InpUseEMAFilter         = true;                  // Enable MAIN EMA Filter
input int                      InpEMAPeriod            = 34;                    // MAIN EMA Period (e.g. 9, 15, 21, 34, 50, 100, 200)
input ENUM_MA_METHOD           InpEMAMethod            = MODE_EMA;              // EMA Smoothing Method
input ENUM_APPLIED_PRICE       InpEMAAppliedPrice      = PRICE_CLOSE;           // EMA Applied Price

sinput group "=== Risk Management & Targets ==="
input double                   InpRiskRewardRatio      = 1.5;                   // Risk-to-Reward Ratio (1:1, 1:1.5, 1:2, etc.)
input ENUM_LOT_TYPE            InpLotType              = LOT_RISK_PCT;          // Lot Sizing Method
input double                   InpRiskPercent          = 1.0;                   // Risk Percentage (% of Free Margin)
input double                   InpFixedLotSize         = 0.01;                  // Fixed Lot Size
input double                   InpMinLotOverride       = 0.0;                   // Min Lot Override (0.0 = Use Broker Default)

sinput group "=== Chart Display & Visuals ==="
input bool                     InpAttachHAIndicator    = true;                  // Attach Heiken Ashi Indicator To Chart
input bool                     InpShowDashboard        = true;                  // Show On-Chart Visual Dashboard

sinput group "=== EA System Settings ==="
input ulong                    InpMagicNumber          = 883401;                // Magic Number
input string                   InpTradeComment         = "HA Breakout Sell";    // Trade Comment

//--- Global Variables & Objects
CTrade                         m_trade;
CSymbolInfo                    m_symbol;
int                            m_ema_handle            = INVALID_HANDLE;
int                            m_ha_visual_handle      = INVALID_HANDLE;
ENUM_TIMEFRAMES                m_tf                    = PERIOD_M5;

//--- Setup Tracking Variables
bool                           m_setup_active          = false;
datetime                       m_setup_bar_time        = 0;
datetime                       m_signal_candle_time    = 0;
double                         m_breakout_target_price = 0.0;
double                         m_stop_loss_price       = 0.0;
double                         m_take_profit_price     = 0.0;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
  {
   m_tf = (ENUM_TIMEFRAMES)InpSignalTimeframe;

   if(!m_symbol.Name(_Symbol))
     {
      Print("[ERROR] Failed to initialize symbol info for ", _Symbol);
      return INIT_FAILED;
     }

   m_symbol.Refresh();

   // Initialize CTrade
   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_trade.SetMarginMode();

   // Configure broker filling type dynamically
   ConfigureTradeFilling();

   // Initialize EMA Indicator Handle if filter enabled
   if(InpUseEMAFilter)
     {
      m_ema_handle = iMA(_Symbol, m_tf, InpEMAPeriod, 0, InpEMAMethod, InpEMAAppliedPrice);
      if(m_ema_handle == INVALID_HANDLE)
        {
         Print("[ERROR] Failed to create EMA handle. Error: ", GetLastError());
         return INIT_FAILED;
        }

      // Automatically attach EMA line to chart window in live & Strategy Tester visual mode
      if(!MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_VISUAL_MODE))
        {
         ChartIndicatorAdd(0, 0, m_ema_handle);
        }
     }

   // Attach clean Heiken Ashi custom indicator to chart if enabled (safely handled for Strategy Tester)
   if(InpAttachHAIndicator && !MQLInfoInteger(MQL_TESTER))
     {
      SetupHeikenAshiIndicator();
     }

   m_setup_active = false;
   m_setup_bar_time = 0;
   m_signal_candle_time = 0;

   Print("[INIT] Expert Advisor successfully initialized. Timeframe: ", EnumToString(m_tf),
         " | EMA Filter: ", InpUseEMAFilter ? IntegerToString(InpEMAPeriod) : "DISABLED",
         " | R:R Ratio: ", DoubleToString(InpRiskRewardRatio, 2));

   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   if(m_ema_handle != INVALID_HANDLE)
     {
      IndicatorRelease(m_ema_handle);
      m_ema_handle = INVALID_HANDLE;
     }

   if(m_ha_visual_handle != INVALID_HANDLE)
     {
      IndicatorRelease(m_ha_visual_handle);
      m_ha_visual_handle = INVALID_HANDLE;
     }

   // Clear objects and dashboard
   ObjectsDeleteAll(0, "HA_Obj_");
   ObjectsDeleteAll(0, "HA_EA_");
   ChartRedraw(0);
   Print("[DEINIT] EA removed. Reason code: ", reason);
  }

//+------------------------------------------------------------------+
//| Attach Heiken Ashi Indicator to Chart                            |
//+------------------------------------------------------------------+
void SetupHeikenAshiIndicator()
  {
   // Reset handle
   m_ha_visual_handle = INVALID_HANDLE;

   // Try loading indicators in priority order
   m_ha_visual_handle = iCustom(_Symbol, m_tf, "Heiken_Ashi Series");
   if(m_ha_visual_handle == INVALID_HANDLE)
      m_ha_visual_handle = iCustom(_Symbol, m_tf, "Heiken_Ashi");
   if(m_ha_visual_handle == INVALID_HANDLE)
      m_ha_visual_handle = iCustom(_Symbol, m_tf, "Examples\\Heiken_Ashi");

   if(m_ha_visual_handle != INVALID_HANDLE)
     {
      ChartIndicatorAdd(0, 0, m_ha_visual_handle);
     }
   else
     {
      Print("[INFO] Heiken Ashi indicator handle not loaded. Strategy execution continues using internal calculations.");
     }
  }

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
  {
   // Always refresh symbol tick data
   if(!m_symbol.RefreshRates())
      return;

   // 1. Check for Active Position on Current Symbol & Magic Number
   if(HasOpenPosition())
     {
      // Already in position, reset setup and update dashboard
      m_setup_active = false;
      if(InpShowDashboard)
         UpdateDashboard("POSITION OPEN", clrLime);
      return;
     }

   // 2. Fetch Rates and Calculate Heiken Ashi Candles
   int req_bars = 60;
   MqlRates rates[];
   ArraySetAsSeries(rates, true);

   if(CopyRates(_Symbol, m_tf, 0, req_bars, rates) < req_bars)
     {
      if(InpShowDashboard)
         UpdateDashboard("SYNCING DATA...", clrYellow);
      return;
     }

   HA_Candle ha[];
   if(!CalculateHeikenAshi(rates, req_bars, ha))
     {
      if(InpShowDashboard)
         UpdateDashboard("CALCULATING HA...", clrYellow);
      return;
     }

   // 3. New Signal Scan on Bar Open
   datetime current_bar_time = rates[0].time;

   // Check if the signal setup bar has passed without breakout occurring
   if(m_setup_active && current_bar_time > m_setup_bar_time)
     {
      Print("[EXPIRED] Next immediate candle closed without breaking Signal Low (",
            DoubleToString(m_breakout_target_price, _Digits), "). Setup invalidated.");
      m_setup_active = false;
     }

   // Evaluate new setup if not currently tracking an active setup
   if(!m_setup_active && rates[1].time != m_signal_candle_time)
     {
      CheckForNewSetup(rates, ha, current_bar_time);
     }

   // 4. Tick-by-Tick Breakout Execution
   if(m_setup_active && current_bar_time == m_setup_bar_time)
     {
      double current_bid = m_symbol.Bid();

      // Check for Sell breakout: Bid must break below target level
      if(current_bid <= m_breakout_target_price)
        {
         Print("[BREAKOUT DETECTED] Bid (", DoubleToString(current_bid, _Digits),
               ") broke below target level (", DoubleToString(m_breakout_target_price, _Digits),
               "). Executing Sell order...");

         ExecuteSellOrder();
        }
     }

   // 5. Update Visual Dashboard
   if(InpShowDashboard)
     {
      if(m_setup_active)
        {
         string status = "WAITING BREAKOUT (" + DoubleToString(m_breakout_target_price, _Digits) + ")";
         UpdateDashboard(status, clrOrange);
        }
      else
        {
         UpdateDashboard("SCANNING SIGNALS", clrLightBlue);
        }
     }
  }

//+------------------------------------------------------------------+
//| Check for New Signal Setup                                       |
//+------------------------------------------------------------------+
void CheckForNewSetup(const MqlRates &rates[], const HA_Candle &ha[], datetime current_bar_time)
  {
   // Signal Candle is Index 1 (Last completed bar)
   // Previous Candle is Index 2 (Bar before signal candle)
   HA_Candle signal_ha = ha[1];
   HA_Candle prev_ha   = ha[2];

   // Rule 1: Signal Candle must be Red
   if(!signal_ha.is_red)
      return;

   // Rule 2: Signal Candle must have No Upper Wick (within tolerance)
   double upper_wick_pts = (signal_ha.high - signal_ha.open) / _Point;
   if(upper_wick_pts > InpUpperWickTolerance)
      return;

   // Rule 3: Previous Candle must be Green Heiken Ashi
   if(!prev_ha.is_green)
      return;

   // Rule 4: MAIN EMA Filter (if enabled)
   // Signal Candle HA Close must be strictly below the EMA value at Index 1
   if(InpUseEMAFilter)
     {
      if(m_ema_handle == INVALID_HANDLE)
         return;

      double ema_val[];
      ArraySetAsSeries(ema_val, true);
      if(CopyBuffer(m_ema_handle, 0, 1, 1, ema_val) < 1)
         return;

      if(signal_ha.close >= ema_val[0])
        {
         // Signal candle closed above or at EMA, condition not met
         return;
        }
     }

   // All Signal Rules Met!
   // Set Breakout Target Level
   if(InpBreakoutLevel == BREAKOUT_HA_LOW)
      m_breakout_target_price = signal_ha.low - (InpEntryBufferPoints * _Point);
   else
      m_breakout_target_price = rates[1].low - (InpEntryBufferPoints * _Point);

   m_breakout_target_price = NormalizedPrice(m_breakout_target_price);

   // Set Stop Loss Level based on Green Candle High
   if(InpSLPriceLevel == SL_GREEN_HA_HIGH)
      m_stop_loss_price = prev_ha.high + (InpSLBufferPoints * _Point);
   else
      m_stop_loss_price = rates[2].high + (InpSLBufferPoints * _Point);

   m_stop_loss_price = NormalizedPrice(m_stop_loss_price);

   // Activate Setup for the immediate next candle (Index 0)
   m_setup_active = true;
   m_setup_bar_time = current_bar_time;
   m_signal_candle_time = rates[1].time;

   Print("[NEW SETUP] Red HA Signal Candle at ", TimeToString(m_signal_candle_time, TIME_DATE|TIME_MINUTES),
         " | Target Low: ", DoubleToString(m_breakout_target_price, _Digits),
         " | Green Candle SL: ", DoubleToString(m_stop_loss_price, _Digits),
         " | EMA Filter: ", InpUseEMAFilter ? "PASSED" : "N/A");
  }

//+------------------------------------------------------------------+
//| Execute Short Order                                              |
//+------------------------------------------------------------------+
void ExecuteSellOrder()
  {
   double entry_price = m_symbol.Bid();
   entry_price = NormalizedPrice(entry_price);

   // Ensure SL is strictly above entry price
   if(m_stop_loss_price <= entry_price)
     {
      Print("[ERROR] Invalid Stop Loss price (", DoubleToString(m_stop_loss_price, _Digits),
            ") relative to Entry (", DoubleToString(entry_price, _Digits), "). Cancelling setup.");
      m_setup_active = false;
      return;
     }

   // Calculate Risk Distance
   double sl_distance = m_stop_loss_price - entry_price;

   // Calculate Take Profit based on Risk-to-Reward Ratio
   m_take_profit_price = entry_price - (sl_distance * InpRiskRewardRatio);
   m_take_profit_price = NormalizedPrice(m_take_profit_price);

   // Calculate Lot Size
   double lot_size = CalculateLotSize(sl_distance);
   if(lot_size <= 0.0)
     {
      Print("[ERROR] Calculated lot size is invalid: ", lot_size);
      m_setup_active = false;
      return;
     }

   // Deactivate setup before sending order to prevent double entry
   m_setup_active = false;

   // Submit Trade Request
   if(m_trade.Sell(lot_size, _Symbol, entry_price, m_stop_loss_price, m_take_profit_price, InpTradeComment))
     {
      Print("[TRADE SUCCESS] Sell Order Executed! Ticket: ", m_trade.ResultOrder(),
            " | Price: ", DoubleToString(m_trade.ResultPrice(), _Digits),
            " | Lots: ", DoubleToString(lot_size, 2),
            " | SL: ", DoubleToString(m_stop_loss_price, _Digits),
            " | TP: ", DoubleToString(m_take_profit_price, _Digits));
     }
   else
     {
      Print("[TRADE FAILED] Sell Order Failed! Return Code: ", m_trade.ResultRetcode(),
            " | Description: ", m_trade.ResultRetcodeDescription());
     }
  }

//+------------------------------------------------------------------+
//| Calculate Heiken Ashi Series recursively                         |
//+------------------------------------------------------------------+
bool CalculateHeikenAshi(const MqlRates &rates[], int count, HA_Candle &ha[])
  {
   if(count <= 0)
      return false;

   ArrayResize(ha, count);
   ArraySetAsSeries(ha, true);

   // Note: rates array is indexed in series format (0 = current, count-1 = oldest)
   // We must compute HA values chronologically from oldest (count-1) to newest (0)

   int oldest_idx = count - 1;

   // Initial Bar
   ha[oldest_idx].open  = (rates[oldest_idx].open + rates[oldest_idx].close) / 2.0;
   ha[oldest_idx].close = (rates[oldest_idx].open + rates[oldest_idx].high + rates[oldest_idx].low + rates[oldest_idx].close) / 4.0;
   ha[oldest_idx].high  = MathMax(rates[oldest_idx].high, MathMax(ha[oldest_idx].open, ha[oldest_idx].close));
   ha[oldest_idx].low   = MathMin(rates[oldest_idx].low, MathMin(ha[oldest_idx].open, ha[oldest_idx].close));
   ha[oldest_idx].time  = rates[oldest_idx].time;
   ha[oldest_idx].is_red   = (ha[oldest_idx].close < ha[oldest_idx].open);
   ha[oldest_idx].is_green = (ha[oldest_idx].close > ha[oldest_idx].open);
   ha[oldest_idx].no_upper_wick = ((ha[oldest_idx].high - ha[oldest_idx].open) <= (InpUpperWickTolerance * _Point));

   // Chronological recursion for subsequent bars
   for(int i = oldest_idx - 1; i >= 0; i--)
     {
      ha[i].close = (rates[i].open + rates[i].high + rates[i].low + rates[i].close) / 4.0;
      ha[i].open  = (ha[i + 1].open + ha[i + 1].close) / 2.0;
      ha[i].high  = MathMax(rates[i].high, MathMax(ha[i].open, ha[i].close));
      ha[i].low   = MathMin(rates[i].low, MathMin(ha[i].open, ha[i].close));
      ha[i].time  = rates[i].time;
      ha[i].is_red   = (ha[i].close < ha[i].open);
      ha[i].is_green = (ha[i].close > ha[i].open);
      ha[i].no_upper_wick = ((ha[i].high - ha[i].open) <= (InpUpperWickTolerance * _Point));
     }

   return true;
  }

//+------------------------------------------------------------------+
//| Calculate Lot Size based on Risk or Fixed Setting                |
//+------------------------------------------------------------------+
double CalculateLotSize(double sl_distance_price)
  {
   if(InpLotType == LOT_FIXED)
     {
      return NormalizeLotSize(InpFixedLotSize);
     }

   // Risk Percentage Sizing
   double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   if(free_margin <= 0)
     {
      Print("[ERROR] Account free margin is 0 or negative.");
      return 0.0;
     }

   double risk_amount = free_margin * (InpRiskPercent / 100.0);
   double tick_size   = m_symbol.TickSize();
   double tick_value  = m_symbol.TickValue();

   if(tick_size <= 0.0 || tick_value <= 0.0 || sl_distance_price <= 0.0)
     {
      Print("[ERROR] Invalid price or tick parameters for lot sizing.");
      return 0.0;
     }

   double risk_per_lot = (sl_distance_price / tick_size) * tick_value;
   if(risk_per_lot <= 0.0)
      return 0.0;

   double raw_lots = risk_amount / risk_per_lot;
   double lot_step = m_symbol.LotsStep();
   double min_lot  = (InpMinLotOverride > 0.0) ? InpMinLotOverride : m_symbol.LotsMin();
   double max_lot  = m_symbol.LotsMax();

   double steps    = MathFloor((raw_lots - min_lot) / lot_step);
   double lots     = min_lot + (steps * lot_step);

   lots = MathMin(max_lot, MathMax(min_lot, lots));
   lots = NormalizeLotSize(lots);

   // Dynamic Margin Utilization Protection to prevent Code 10019
   double margin_required = 0.0;
   if(OrderCalcMargin(ORDER_TYPE_SELL, _Symbol, lots, m_symbol.Bid(), margin_required))
     {
      if(margin_required > free_margin * 0.90) // Cap margin at 90% of free margin
        {
         double scaled_lots = lots * ((free_margin * 0.90) / margin_required);
         steps = MathFloor((scaled_lots - min_lot) / lot_step);
         lots  = min_lot + (steps * lot_step);
         lots  = MathMin(max_lot, MathMax(min_lot, lots));
         lots  = NormalizeLotSize(lots);
        }
     }

   return lots;
  }

//+------------------------------------------------------------------+
//| Normalize Lot Size to broker steps                               |
//+------------------------------------------------------------------+
double NormalizeLotSize(double lots)
  {
   double lot_step = m_symbol.LotsStep();
   double min_lot  = (InpMinLotOverride > 0.0) ? InpMinLotOverride : m_symbol.LotsMin();
   double max_lot  = m_symbol.LotsMax();

   if(lot_step <= 0)
      return min_lot;

   lots = MathRound(lots / lot_step) * lot_step;
   if(lots < min_lot)
      lots = min_lot;
   if(lots > max_lot)
      lots = max_lot;

   return NormalizeDouble(lots, 2);
  }

//+------------------------------------------------------------------+
//| Normalize Price to symbol tick precision                         |
//+------------------------------------------------------------------+
double NormalizedPrice(double price)
  {
   double tick_size = m_symbol.TickSize();
   if(tick_size <= 0)
      return NormalizeDouble(price, _Digits);

   return NormalizeDouble(MathRound(price / tick_size) * tick_size, _Digits);
  }

//+------------------------------------------------------------------+
//| Check if open position exists for symbol and magic number        |
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
//| Dynamic Broker Trade Filling Mode Setup                          |
//+------------------------------------------------------------------+
void ConfigureTradeFilling()
  {
   uint filling = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);

   if((filling & SYMBOL_FILLING_FOK) != 0)
      m_trade.SetTypeFilling(ORDER_FILLING_FOK);
   else if((filling & SYMBOL_FILLING_IOC) != 0)
      m_trade.SetTypeFilling(ORDER_FILLING_IOC);
   else
      m_trade.SetTypeFilling(ORDER_FILLING_RETURN);
  }

//+------------------------------------------------------------------+
//| Render / Update Visual Dashboard on Chart                        |
//+------------------------------------------------------------------+
void UpdateDashboard(string status_text, color status_color)
  {
   if(!InpShowDashboard || (MQLInfoInteger(MQL_TESTER) && !MQLInfoInteger(MQL_VISUAL_MODE)))
      return;

   int x = 20;
   int y = 30;
   int line_height = 20;

   CreateDashboardLabel("HA_EA_Header", "=== HEIKEN ASHI BREAKOUT EA ===", x, y, clrGold, 10, true);
   y += line_height + 5;

   CreateDashboardLabel("HA_EA_TF", "Signal Timeframe: " + EnumToString(m_tf), x, y, clrWhite, 9, false);
   y += line_height;

   CreateDashboardLabel("HA_EA_EMA", "EMA Filter: " + (InpUseEMAFilter ? (IntegerToString(InpEMAPeriod) + " Period") : "OFF"), x, y, clrWhite, 9, false);
   y += line_height;

   CreateDashboardLabel("HA_EA_RR", "Target R:R Ratio: 1 : " + DoubleToString(InpRiskRewardRatio, 2), x, y, clrWhite, 9, false);
   y += line_height;

   CreateDashboardLabel("HA_EA_Status", "Status: " + status_text, x, y, status_color, 10, true);

   ChartRedraw(0);
  }

//+------------------------------------------------------------------+
//| Helper to create/update chart label                              |
//+------------------------------------------------------------------+
void CreateDashboardLabel(string name, string text, int x, int y, color text_color, int font_size, bool is_bold)
  {
   if(ObjectFind(0, name) < 0)
     {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, (long)CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
      ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
     }

   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetString(0, name, OBJPROP_FONT, is_bold ? "Arial Bold" : "Arial");
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, font_size);
   ObjectSetInteger(0, name, OBJPROP_COLOR, (long)text_color);
  }
//+------------------------------------------------------------------+
