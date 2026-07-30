//+------------------------------------------------------------------+
//|                                                     Vwap 1.mq5   |
//|                                                     Jules Coder |
//|                                  https://github.com/julescoder   |
//+------------------------------------------------------------------+
#property copyright "Jules Coder"
#property link      "https://github.com/julescoder"
#property version   "1.00"
#property description "VWAP & EMA Pullback Breakout Strategy for XAUUSD on XM Broker"
#property strict

// Include standard MQL5 trade library
#include <Trade\Trade.mqh>

//--- Input Parameters
input group "=== Indicator Settings ==="
input ENUM_TIMEFRAMES InpTimeframe        = PERIOD_M5;        // Candle Period / Timeframe
input int             InpEMAPeriod       = 34;               // EMA Period (default 34)
input ENUM_APPLIED_PRICE InpEMAPrice     = PRICE_CLOSE;      // EMA Applied Price

input group "=== Strategy Conditions ==="
input double          InpMinCandlePoints = 100.0;            // Min Candle Range in Points (100 pts = $1.00 on XAUUSD)
input double          InpMaxCandlePoints = 1000.0;           // Max Candle Range in Points (1000 pts = $10.00 on XAUUSD, 0 to disable)
input double          InpEntryBufferPoints = 10.0;           // Entry Breakout Buffer in Points (10 pts = $0.10)
input double          InpRiskRewardRatio = 2.0;              // Risk-to-Reward Ratio (e.g. 1:2)
input bool            InpOnePositionAtATime = true;          // Limit to One Open Position at a time

input group "=== Money Management ==="
input double          InpLotSize         = 0.1;              // Fixed Lot Size (if Risk Lot is disabled)
input bool            InpUseRiskLot      = false;            // Enable Risk-Based Lot Sizing
input double          InpRiskPct         = 1.0;              // Risk Percentage per Trade (from Account Balance)

input group "=== Execution Settings ==="
input ulong           InpMagicNumber     = 987654;           // EA Magic Number
input ulong           InpSlippage        = 30;               // Maximum Allowed Slippage (Points)
input bool            InpUseRealVolume   = false;            // Use Real Volume for VWAP (if false, Tick Volume is used)

//--- Global Variables
CTrade   trade;
int      ema_handle = INVALID_HANDLE;
datetime m_last_bar_time = 0;

//--- Signal State Variables
datetime m_signal_candle_time = 0;
double   m_signal_high = 0;
double   m_signal_low = 0;
bool     m_signal_active = false;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // Print startup info
   PrintFormat("VWAP/EMA Breakout EA Starting... Symbol: %s, Timeframe: %s", Symbol(), EnumToString(InpTimeframe));

   // Initialize EMA Indicator Handle
   ema_handle = iMA(Symbol(), InpTimeframe, InpEMAPeriod, 0, MODE_EMA, InpEMAPrice);
   if(ema_handle == INVALID_HANDLE)
   {
      Print("Error: Failed to create iMA handle. Initialization aborted.");
      return INIT_FAILED;
   }

   // Pre-set Trade Filling mode based on XM Broker settings
   SetTradeFillingMode();

   // Set magic number
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippage);

   // Initialize last bar time
   m_last_bar_time = iTime(Symbol(), InpTimeframe, 0);

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   // Release Indicator Handle
   if(ema_handle != INVALID_HANDLE)
   {
      IndicatorRelease(ema_handle);
   }

   // Clear visual comments and objects
   Comment("");
   ObjectDelete(0, "VWAP_Line");
   ObjectDelete(0, "Breakout_Trigger_Line");

   Print("VWAP/EMA Breakout EA Deinitialized.");
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // 1. Check for a New Candle
   if(IsNewBar())
   {
      // Manage signal candle expiration on new bar
      if(m_signal_active)
      {
         datetime current_bar_time = iTime(Symbol(), InpTimeframe, 0);
         // If the new bar's open time is past the immediate next bar's expected time
         if(current_bar_time > m_signal_candle_time + PeriodSeconds(InpTimeframe))
         {
            m_signal_active = false;
            PrintFormat("VWAP/EMA EA: Signal candle at %s expired without breakout.", TimeToString(m_signal_candle_time));
         }
      }

      // Look for a new signal setup on the closed bar
      CheckNewBarSignal();
   }

   // 2. Check for Tick-by-Tick Breakout Trigger if Signal is Active
   if(m_signal_active)
   {
      // Double check that we are still in the immediate next candle
      datetime current_bar_time = iTime(Symbol(), InpTimeframe, 0);
      if(current_bar_time > m_signal_candle_time + PeriodSeconds(InpTimeframe))
      {
         m_signal_active = false;
         PrintFormat("VWAP/EMA EA: Signal candle at %s expired without breakout.", TimeToString(m_signal_candle_time));
         return;
      }

      double ask = SymbolInfoDouble(Symbol(), SYMBOL_ASK);
      double trigger_price = m_signal_high + InpEntryBufferPoints * SymbolInfoDouble(Symbol(), SYMBOL_POINT);

      if(ask >= trigger_price)
      {
         ExecuteBuyEntry();
      }
   }

   // 3. Update Visual Dashboard and Chart Indicators
   double current_vwap = CalculateVWAP(iTime(Symbol(), InpTimeframe, 0));
   double current_ema = GetEMAValue(0);
   UpdateDashboard(current_vwap, current_ema);
   DrawVWAPLine(current_vwap);
   DrawTriggerLine(m_signal_high + InpEntryBufferPoints * SymbolInfoDouble(Symbol(), SYMBOL_POINT));
}

//+------------------------------------------------------------------+
//| Check if a new candle has opened                                 |
//+------------------------------------------------------------------+
bool IsNewBar()
{
   datetime current_time = iTime(Symbol(), InpTimeframe, 0);
   if(current_time != m_last_bar_time)
   {
      m_last_bar_time = current_time;
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Check the last completed bar (index 1) for signal criteria       |
//+------------------------------------------------------------------+
void CheckNewBarSignal()
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(Symbol(), InpTimeframe, 0, 3, rates) < 3)
   {
      return;
   }

   // Get indicator values for the completed candle (index 1)
   double ema_val = GetEMAValue(1);
   double vwap_val = CalculateVWAP(rates[1].time);

   if(ema_val == 0 || vwap_val == 0)
   {
      return; // Indicators not ready yet
   }

   // 1. Green Candle: Close must be strictly greater than Open
   bool is_green = (rates[1].close > rates[1].open);

   // 2. Closed above both VWAP and EMA
   bool closed_above = (rates[1].close > ema_val) && (rates[1].close > vwap_val);

   // 3. Low crossed below or touched both and closed above
   bool low_touched_or_below = (rates[1].low <= ema_val) && (rates[1].low <= vwap_val);

   // 4. Ignore tiny candle filter
   double range = rates[1].high - rates[1].low;
   double min_range_points = InpMinCandlePoints * SymbolInfoDouble(Symbol(), SYMBOL_POINT);
   bool is_not_tiny = (range >= min_range_points);

   // 5. Avoid big signal candle filter
   double max_range_points = InpMaxCandlePoints * SymbolInfoDouble(Symbol(), SYMBOL_POINT);
   bool is_not_too_big = (InpMaxCandlePoints <= 0.0 || range <= max_range_points);

   // Verify all conditions
   if(is_green && closed_above && low_touched_or_below && is_not_tiny && is_not_too_big)
   {
      m_signal_candle_time = rates[1].time;
      m_signal_high = rates[1].high;
      m_signal_low = rates[1].low;
      m_signal_active = true;

      PrintFormat("VWAP/EMA EA: SIGNAL CANDLE DETECTED! Time: %s, High: %.2f, Low: %.2f, Range: %.2f, VWAP: %.2f, EMA: %.2f",
                  TimeToString(m_signal_candle_time), m_signal_high, m_signal_low, range, vwap_val, ema_val);
   }
}

//+------------------------------------------------------------------+
//| Execute BUY Breakout Order                                       |
//+------------------------------------------------------------------+
void ExecuteBuyEntry()
{
   // Check One-Position-at-a-Time constraint
   if(InpOnePositionAtATime && HasOpenPosition())
   {
      m_signal_active = false;
      return;
   }

   double ask = SymbolInfoDouble(Symbol(), SYMBOL_ASK);
   double entry_price = ask;
   double sl = m_signal_low;

   // Verify SL is below Entry
   double risk = entry_price - sl;
   if(risk <= 0)
   {
      Print("VWAP/EMA EA: Entry price is below or equal to signal low. Cannot enter trade.");
      m_signal_active = false;
      return;
   }

   // Calculate Take Profit based on customizable Risk-to-Reward Ratio
   double tp = entry_price + (risk * InpRiskRewardRatio);

   // Ensure SL & TP comply with the Broker's minimum stop levels
   ApplyStopsLevelLimits(entry_price, sl, tp);

   // Calculate Lot size
   double lots = InpLotSize;
   if(InpUseRiskLot)
   {
      lots = CalculateRiskLot(entry_price, sl);
   }

   // Ensure Lot complies with broker limits and volume steps
   double lot_step = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_STEP);
   lots = MathFloor(lots / lot_step) * lot_step;
   double min_lot = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MIN);
   double max_lot = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MAX);
   if(lots < min_lot) lots = min_lot;
   if(lots > max_lot) lots = max_lot;

   // Evaluate required margin and dynamically scale down volume if needed
   lots = CheckAndScaleLotSize(lots, entry_price);

   // Set proper filling mode dynamically
   SetTradeFillingMode();

   PrintFormat("VWAP/EMA EA: Placing BUY breakout order. Lots: %.2f, Entry: %.2f, SL: %.2f, TP: %.2f",
               lots, entry_price, sl, tp);

   // Anti-Race Lock Pattern (set false before sending order to avoid rapid-tick double execution)
   m_signal_active = false;

   if(!trade.Buy(lots, Symbol(), entry_price, sl, tp, "VWAP_EMA Breakout"))
   {
      PrintFormat("VWAP/EMA EA: Trade failed! Error code: %d", trade.ResultRetcode());
   }
   else
   {
      PrintFormat("VWAP/EMA EA: Trade executed successfully! Ticket: %I64u", trade.ResultDeal());
   }
}

//+------------------------------------------------------------------+
//| Calculate dynamic Risk-Based Lot Size                            |
//+------------------------------------------------------------------+
double CalculateRiskLot(double entry, double sl)
{
   double loss_points = entry - sl;
   if(loss_points <= 0) return InpLotSize;

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double risk_amount = balance * (InpRiskPct / 100.0);

   double tick_value = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_VALUE);
   double tick_size = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_SIZE);

   if(tick_size == 0 || tick_value == 0) return InpLotSize;

   double loss_per_lot = (loss_points / tick_size) * tick_value;
   if(loss_per_lot <= 0) return InpLotSize;

   double calculated_lots = risk_amount / loss_per_lot;
   return calculated_lots;
}

//+------------------------------------------------------------------+
//| Verify and dynamically scale down lots to prevent Code 10019     |
//+------------------------------------------------------------------+
double CheckAndScaleLotSize(double lots, double entry_price)
{
   double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double required_margin = 0;

   if(!OrderCalcMargin(ORDER_TYPE_BUY, Symbol(), lots, entry_price, required_margin))
   {
      return lots;
   }

   // Safety threshold (e.g. max 80% free margin usage)
   double max_allowed_margin = free_margin * 0.8;

   if(required_margin > max_allowed_margin)
   {
      double scale = max_allowed_margin / required_margin;
      double scaled_lots = lots * scale;

      double lot_step = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_STEP);
      double min_lot = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MIN);

      scaled_lots = MathFloor(scaled_lots / lot_step) * lot_step;
      if(scaled_lots < min_lot)
      {
         scaled_lots = min_lot;
      }

      PrintFormat("VWAP/EMA EA: Margin limits reached. Scaled lot size from %.2f to %.2f (Free Margin: %.2f)",
                  lots, scaled_lots, free_margin);
      return scaled_lots;
   }

   return lots;
}

//+------------------------------------------------------------------+
//| Get VWAP value dynamically without external indicators           |
//+------------------------------------------------------------------+
double CalculateVWAP(datetime bar_time)
{
   datetime day_start = GetVWAPAnchorTime(bar_time);
   MqlRates vwap_rates[];

   // Copy historical rates from the start of the day up to the target bar
   int copied = CopyRates(Symbol(), InpTimeframe, day_start, bar_time, vwap_rates);
   if(copied <= 0)
   {
      return 0;
   }

   double sum_pv = 0;
   double sum_v = 0;
   for(int k = 0; k < copied; k++)
   {
      double high = vwap_rates[k].high;
      double low = vwap_rates[k].low;
      double close = vwap_rates[k].close;
      double typical = (high + low + close) / 3.0;
      double volume = (double)(InpUseRealVolume && vwap_rates[k].real_volume > 0 ? vwap_rates[k].real_volume : vwap_rates[k].tick_volume);

      sum_pv += typical * volume;
      sum_v += volume;
   }

   return (sum_v > 0) ? (sum_pv / sum_v) : vwap_rates[copied - 1].close;
}

//+------------------------------------------------------------------+
//| Get the starting time for the VWAP anchor                        |
//+------------------------------------------------------------------+
datetime GetVWAPAnchorTime(datetime bar_time)
{
   MqlDateTime dt;
   TimeToStruct(bar_time, dt);

   if(InpTimeframe >= PERIOD_D1)
   {
      // Higher timeframe: Reset anchor weekly/monthly
      dt.day = 1;
      dt.hour = 0;
      dt.min = 0;
      dt.sec = 0;
      return StructToTime(dt);
   }
   else
   {
      // Standard daily VWAP
      dt.hour = 0;
      dt.min = 0;
      dt.sec = 0;
      return StructToTime(dt);
   }
}

//+------------------------------------------------------------------+
//| Fetch the EMA value at a specific index                          |
//+------------------------------------------------------------------+
double GetEMAValue(int index)
{
   if(ema_handle == INVALID_HANDLE) return 0;

   double values[1];
   if(CopyBuffer(ema_handle, 0, index, 1, values) < 1)
   {
      return 0;
   }
   return values[0];
}

//+------------------------------------------------------------------+
//| Check if we have an active open position for our Magic Number     |
//+------------------------------------------------------------------+
bool HasOpenPosition()
{
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      string symbol = PositionGetSymbol(i);
      if(symbol == Symbol())
      {
         if(PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         {
            return true;
         }
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Helper to adjust SL and TP to comply with Stops Level Limits     |
//+------------------------------------------------------------------+
void ApplyStopsLevelLimits(double entry, double &sl, double &tp)
{
   double stops_level = SymbolInfoInteger(Symbol(), SYMBOL_TRADE_STOPS_LEVEL) * SymbolInfoDouble(Symbol(), SYMBOL_POINT);
   if(stops_level <= 0) return;

   double min_distance = stops_level;

   // For Buy order, SL must be below entry by at least StopsLevel
   if(entry - sl < min_distance)
   {
      sl = entry - min_distance;
   }

   // TP must be above entry by at least StopsLevel
   if(tp - entry < min_distance)
   {
      tp = entry + min_distance;
   }
}

//+------------------------------------------------------------------+
//| Query and set dynamic execution filling modes                    |
//+------------------------------------------------------------------+
void SetTradeFillingMode()
{
   uint filling = (uint)SymbolInfoInteger(Symbol(), SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0)
   {
      trade.SetTypeFilling(ORDER_FILLING_FOK);
   }
   else if((filling & SYMBOL_FILLING_IOC) != 0)
   {
      trade.SetTypeFilling(ORDER_FILLING_IOC);
   }
   else
   {
      trade.SetTypeFilling(ORDER_FILLING_RETURN);
   }
}

//+------------------------------------------------------------------+
//| Render Flicker-free Comment Dashboard                            |
//+------------------------------------------------------------------+
void UpdateDashboard(double vwap, double ema)
{
   // Skip in non-visual tester mode to optimize performance
   if(MQLInfoInteger(MQL_TESTER) && !MQLInfoInteger(MQL_VISUAL_MODE))
      return;

   string signal_status = m_signal_active ? "ACTIVE (Waiting for next-candle breakout)" : "INACTIVE";

   string text = "==================================================\n";
   text += "         VWAP & EMA PULLBACK BREAKOUT EA          \n";
   text += "==================================================\n";
   text += StringFormat(" Timeframe        : %s\n", EnumToString(InpTimeframe));
   text += StringFormat(" EMA Period       : %d\n", InpEMAPeriod);
   text += StringFormat(" Risk Reward Ratio: 1 : %.1f\n", InpRiskRewardRatio);
   text += StringFormat(" Min Candle Points: %.1f\n", InpMinCandlePoints);
   text += "--------------------------------------------------\n";
   text += StringFormat(" Current VWAP     : %.2f\n", vwap);
   text += StringFormat(" Current EMA      : %.2f\n", ema);
   text += "--------------------------------------------------\n";
   text += StringFormat(" Signal Status    : %s\n", signal_status);
   if(m_signal_active)
   {
      double trigger_price = m_signal_high + InpEntryBufferPoints * SymbolInfoDouble(Symbol(), SYMBOL_POINT);
      text += StringFormat(" Signal Time      : %s\n", TimeToString(m_signal_candle_time));
      text += StringFormat(" Signal High      : %.2f (Trigger: %.2f)\n", m_signal_high, trigger_price);
      text += StringFormat(" Signal Low (SL)  : %.2f\n", m_signal_low);
   }
   text += "==================================================\n";

   Comment(text);
}

//+------------------------------------------------------------------+
//| Draw dynamic HLINE at current VWAP price                         |
//+------------------------------------------------------------------+
void DrawVWAPLine(double vwap_val)
{
   if(MQLInfoInteger(MQL_TESTER) && !MQLInfoInteger(MQL_VISUAL_MODE))
      return;

   string name = "VWAP_Line";
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_HLINE, 0, 0, vwap_val);
      ObjectSetInteger(0, name, OBJPROP_COLOR, clrBlue);
      ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_DASH);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
      ObjectSetString(0, name, OBJPROP_TOOLTIP, "Current Daily VWAP");
   }
   else
   {
      ObjectMove(0, name, 0, 0, vwap_val);
   }
}

//+------------------------------------------------------------------+
//| Draw dynamic HLINE at active signal trigger price                |
//+------------------------------------------------------------------+
void DrawTriggerLine(double trigger_price)
{
   if(MQLInfoInteger(MQL_TESTER) && !MQLInfoInteger(MQL_VISUAL_MODE))
      return;

   string name = "Breakout_Trigger_Line";
   if(!m_signal_active)
   {
      ObjectDelete(0, name);
      return;
   }

   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_HLINE, 0, 0, trigger_price);
      ObjectSetInteger(0, name, OBJPROP_COLOR, clrGreen);
      ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_DOT);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
      ObjectSetString(0, name, OBJPROP_TOOLTIP, "Breakout Trigger Price");
   }
   else
   {
      ObjectMove(0, name, 0, 0, trigger_price);
   }
}
