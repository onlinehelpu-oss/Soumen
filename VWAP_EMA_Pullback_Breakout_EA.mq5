//+------------------------------------------------------------------+
//|                                VWAP_EMA_Pullback_Breakout_EA.mq5 |
//|                                                            Jules |
//|                                             https://github.com/  |
//+------------------------------------------------------------------+
#property copyright "Jules"
#property link      "https://github.com/"
#property version   "1.00"
#property description "VWAP + EMA Trend Pullback Breakout EA"

// Standard Library Includes
#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\PositionInfo.mqh>

//--- Enums
enum ENUM_RR_PRESETS {
   RR_1_1,       // 1:1
   RR_1_1_5,     // 1:1.5
   RR_1_2,       // 1:2 (Default)
   RR_1_3,       // 1:3
   RR_CUSTOM     // Custom
};

enum ENUM_MM_TYPE {
   MM_FIXED_LOT, // Fixed Lot
   MM_RISK_PCT   // Risk %
};

enum ENUM_VWAP_RESET {
   VWAP_RESET_DAILY,   // Reset Daily
   VWAP_RESET_SESSION  // Reset Session
};

//--- Inputs
input group "=== GENERAL SETTINGS ==="
input ulong             InpMagicNumber     = 123456;      // Magic Number
input ENUM_TIMEFRAMES   InpSignalTimeframe = PERIOD_M15;  // Signal Timeframe

input group "=== EMA SETTINGS ==="
input int               InpEMAPeriod       = 34;          // EMA Period
input ENUM_APPLIED_PRICE InpEMAPrice       = PRICE_CLOSE; // EMA Price Source

input group "=== VWAP SETTINGS ==="
input ENUM_VWAP_RESET   InpVWAPResetMode   = VWAP_RESET_DAILY; // VWAP Reset Mode
input string            InpSessionStart    = "09:15";          // VWAP Session Reset Time (HH:MM)
input ENUM_APPLIED_PRICE InpVWAPSource     = PRICE_CLOSE;      // VWAP Price Source

input group "=== ENTRY SETTINGS ==="
input int               InpEntryBuffer     = 0;           // Entry Buffer (Points)
input int               InpSLBuffer        = 0;           // Stop Loss Buffer (Points)
input ENUM_RR_PRESETS   InpRiskReward      = RR_1_2;      // Risk Reward Ratio Preset
input double            InpRiskRewardRatio = 2.0;         // Custom Risk Reward Ratio (if Custom selected)
input int               InpMinCandlePoints = 50;          // Min Candle Range (Points) to Avoid Tiny Candles

input group "=== MONEY MANAGEMENT ==="
input ENUM_MM_TYPE      InpMMType          = MM_RISK_PCT; // Money Management Mode
input double            InpFixedLot        = 0.1;         // Fixed Lot Size
input double            InpRiskPercent     = 1.0;         // Risk % per Trade

input group "=== TRADING FILTERS ==="
input bool              InpOnePositionOnly = true;        // One Open Position Only
input bool              InpUseSpreadFilter = true;        // Enable Spread Filter
input int               InpMaxSpread       = 50;          // Maximum Spread (Points)
input int               InpSlippage        = 3;           // Slippage (Points)
input bool              InpUseSessionFilter= false;       // Enable Trading Session Filter
input string            InpSessionStartTrading = "09:15"; // Trading Session Start (HH:MM)
input string            InpSessionEndTrading   = "15:20"; // Trading Session End (HH:MM)

input group "=== BREAK EVEN & TRAILING ==="
input bool              InpEnableBreakEven = false;       // Enable Break Even
input int               InpBreakEvenTriggerPoints = 200;  // Break Even Trigger (Points)
input int               InpBreakEvenLockPoints    = 50;   // Break Even Lock (Points)
input bool              InpEnableTrailing  = false;       // Enable Trailing Stop
input int               InpTrailingStopPoints = 150;      // Trailing Stop Distance (Points)
input int               InpTrailingStepPoints = 50;       // Trailing Stop Step (Points)

input group "=== NEWS FILTER ==="
input bool              InpEnableNewsFilter= false;       // Enable News Filter (Optional/Placeholder)
input int               InpMinsBeforeNews  = 15;          // Minutes Before News
input int               InpMinsAfterNews   = 15;          // Minutes After News

//--- Global Variables
CTrade         m_trade;
CSymbolInfo    m_symbol;
int            m_ema_handle = INVALID_HANDLE;
datetime       m_last_bar_time = 0;

// Signal Tracking
bool           m_signal_active = false;
datetime       m_signal_candle_time = 0;
double         m_signal_high = 0;
double         m_signal_low = 0;
string         m_last_rejection = "None - Scanning";

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // Check validation of Session settings
   string start_parts[];
   if(StringSplit(InpSessionStart, ':', start_parts) != 2)
   {
      Alert("Invalid VWAP Session Reset Time format! Use HH:MM");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(InpUseSessionFilter)
   {
      string t_start_parts[], t_end_parts[];
      if(StringSplit(InpSessionStartTrading, ':', t_start_parts) != 2 ||
         StringSplit(InpSessionEndTrading, ':', t_end_parts) != 2)
      {
         Alert("Invalid Trading Session format! Use HH:MM");
         return INIT_PARAMETERS_INCORRECT;
      }
   }

   // Initialize Symbol Info
   if(!m_symbol.Name(_Symbol))
   {
      Print("Failed to initialize symbol: ", _Symbol);
      return INIT_FAILED;
   }
   m_symbol.RefreshRates();

   // Setup Trade Settings
   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_trade.SetDeviationInPoints(InpSlippage);
   m_trade.SetTypeFillingBySymbol(_Symbol);

   // Initialize EMA Indicator
   m_ema_handle = iMA(_Symbol, InpSignalTimeframe, InpEMAPeriod, 0, MODE_EMA, InpEMAPrice);
   if(m_ema_handle == INVALID_HANDLE)
   {
      Print("Failed to create EMA handle!");
      return INIT_FAILED;
   }

   // Create GUI Dashboard
   CreateDashboard();

   // Set Timer for dashboard refresh (1-second intervals)
   EventSetTimer(1);

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   // Release Indicator Handle
   if(m_ema_handle != INVALID_HANDLE)
   {
      IndicatorRelease(m_ema_handle);
   }

   // Remove Timer
   EventKillTimer();

   // Cleanup Dashboard Objects
   DeleteDashboard();
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   m_symbol.RefreshRates();

   // Ensure history is loaded
   if(iBars(_Symbol, InpSignalTimeframe) < InpEMAPeriod + 50)
   {
      m_last_rejection = "Waiting for historical bars load...";
      UpdateDashboard();
      return;
   }

   double ask = m_symbol.Ask();
   double bid = m_symbol.Bid();
   double point = m_symbol.Point();

   // Check if new bar has opened on the target timeframe
   datetime current_bar_time = iTime(_Symbol, InpSignalTimeframe, 0);
   if(current_bar_time != m_last_bar_time)
   {
      m_last_bar_time = current_bar_time;

      // Fetch Bar 1 data
      double open1 = iOpen(_Symbol, InpSignalTimeframe, 1);
      double close1 = iClose(_Symbol, InpSignalTimeframe, 1);
      double high1 = iHigh(_Symbol, InpSignalTimeframe, 1);
      double low1 = iLow(_Symbol, InpSignalTimeframe, 1);

      double ema1 = GetEMAValue(1);
      double ema2 = GetEMAValue(2);
      double vwap1 = GetVWAP(1);

      // Evaluate Trend Filter: Close > VWAP AND VWAP > EMA AND EMA Rising (EMA1 > EMA2)
      bool trend_ok = (close1 > vwap1) && (vwap1 > ema1) && (ema1 > ema2);

      if(trend_ok)
      {
         // Signal Candle Condition (Cond A: cross VWAP or Cond B: touch/cross EMA)
         bool condA = (open1 < vwap1 && close1 > vwap1);
         bool condB = (low1 < ema1 && close1 > ema1);

         if(condA || condB)
         {
            // Avoid Tiny Candle Check
            double range = high1 - low1;
            double min_range = InpMinCandlePoints * point;

            if(range >= min_range)
            {
               m_signal_active = true;
               m_signal_candle_time = iTime(_Symbol, InpSignalTimeframe, 1);
               m_signal_high = high1;
               m_signal_low = low1;
               m_last_rejection = "Setup active - waiting for breakout";
               Print(StringFormat("VWAP_EMA: New Signal Candle detected at %s. High: %.5f, Low: %.5f. Waiting for breakout above %.5f",
                     TimeToString(m_signal_candle_time), high1, low1, high1 + InpEntryBuffer * point));
            }
            else
            {
               if(!m_signal_active)
               {
                  m_last_rejection = StringFormat("Rejected - Tiny candle (Range: %d pts, Min: %d pts)", (int)(range / point), InpMinCandlePoints);
               }
            }
         }
         else
         {
            if(!m_signal_active)
            {
               m_last_rejection = "Bar 1 did not meet Signal Candle criteria (Cond A or B)";
            }
         }
      }
      else
      {
         if(!m_signal_active)
         {
            m_last_rejection = "Trend Filter not satisfied (Bullish alignment required)";
         }
      }
   }

   // Process active breakout signal
   if(m_signal_active)
   {
      // Check if breakout window has expired (i.e. the "very next candle" has closed)
      int shift = iBarShift(_Symbol, InpSignalTimeframe, m_signal_candle_time, true);
      if(shift > 1)
      {
         m_signal_active = false;
         m_last_rejection = "Previous breakout expired without entry";
         Print("VWAP_EMA: Breakout window expired without breakout entry.");
         UpdateDashboard();
         return;
      }

      // Perform Trading Filter Checks before entering
      if(InpOnePositionOnly && HasOpenPosition())
      {
         m_last_rejection = "Filter: Position already open";
         UpdateDashboard();
         return;
      }

      if(InpUseSpreadFilter)
      {
         int current_spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
         if(current_spread > InpMaxSpread)
         {
            m_last_rejection = StringFormat("Filter: High Spread (%d pts, Max: %d)", current_spread, InpMaxSpread);
            UpdateDashboard();
            return;
         }
      }

      if(InpUseSessionFilter && !IsInSession())
      {
         m_last_rejection = "Filter: Outside Trading Session";
         UpdateDashboard();
         return;
      }

      // Check if current price breaks the Signal Candle's High + Entry Buffer
      double trigger_price = m_signal_high + InpEntryBuffer * point;
      if(ask > trigger_price)
      {
         // Calculate Stop Loss
         double sl_price = m_signal_low - InpSLBuffer * point;

         // Respect Broker's Stop Level
         double min_stop = ask - m_symbol.StopsLevel() * point;
         if(sl_price > min_stop)
         {
            sl_price = min_stop;
         }

         // Calculate Risk-Reward Multiplier
         double rr_multiplier = 2.0;
         switch(InpRiskReward)
         {
            case RR_1_1:   rr_multiplier = 1.0; break;
            case RR_1_1_5: rr_multiplier = 1.5; break;
            case RR_1_2:   rr_multiplier = 2.0; break;
            case RR_1_3:   rr_multiplier = 3.0; break;
            case RR_CUSTOM: rr_multiplier = InpRiskRewardRatio; break;
         }

         double sl_distance_price = ask - sl_price;
         double tp_price = ask + sl_distance_price * rr_multiplier;

         // Determine Position Lot Size
         double lot_size = InpFixedLot;
         if(InpMMType == MM_RISK_PCT)
         {
            double balance = AccountInfoDouble(ACCOUNT_BALANCE);
            double risk_amount = balance * (InpRiskPercent / 100.0);
            double sl_distance_points = sl_distance_price / point;
            lot_size = CalculateRiskLotSize(risk_amount, sl_distance_points);
         }

         lot_size = NormalizeVolume(lot_size);

         // Execute Trade
         if(lot_size > 0)
         {
            m_last_rejection = "Sending BUY Order...";
            UpdateDashboard();

            if(m_trade.Buy(lot_size, _Symbol, ask, sl_price, tp_price, "VWAP_EMA_EA"))
            {
               m_signal_active = false;
               m_last_rejection = "Trade Executed successfully";
               Print(StringFormat("VWAP_EMA: BUY Order Executed! Vol: %.2f, Entry: %.5f, SL: %.5f, TP: %.5f", lot_size, ask, sl_price, tp_price));
            }
            else
            {
               m_last_rejection = StringFormat("Trade Execution Failed: Code %d", m_trade.ResultRetcode());
               Print("VWAP_EMA: BUY execution failed: ", m_trade.ResultComment());
            }
         }
         else
         {
            m_last_rejection = "Error: Invalid Lot Size";
         }
      }
   }

   // Manage Active Stops (Break Even & Trailing Stop)
   ManagePositions();

   // Update GUI Dashboard
   UpdateDashboard();
}

//+------------------------------------------------------------------+
//| Timer function                                                   |
//+------------------------------------------------------------------+
void OnTimer()
{
   UpdateDashboard();
}

//+------------------------------------------------------------------+
//| Get EMA Value                                                    |
//+------------------------------------------------------------------+
double GetEMAValue(int index)
{
   double buffer[1];
   if(CopyBuffer(m_ema_handle, 0, index, 1, buffer) == 1)
   {
      return buffer[0];
   }
   return 0;
}

//+------------------------------------------------------------------+
//| Custom VWAP Calculation with Daily & Session Reset Support       |
//+------------------------------------------------------------------+
double GetVWAP(int idx)
{
   datetime barTime = iTime(_Symbol, InpSignalTimeframe, idx);
   if(barTime == 0) return 0;

   MqlDateTime dtBar;
   TimeToStruct(barTime, dtBar);

   int max_bars = iBars(_Symbol, InpSignalTimeframe);
   if(max_bars > 2000) max_bars = 2000; // Limit search to prevent performance lag

   int start_idx = idx;

   for(int i = idx; i < max_bars; i++)
   {
      datetime t = iTime(_Symbol, InpSignalTimeframe, i);
      if(t == 0) break;

      MqlDateTime dtCurrent;
      TimeToStruct(t, dtCurrent);

      bool isReset = false;
      if(InpVWAPResetMode == VWAP_RESET_DAILY)
      {
         if(dtCurrent.day != dtBar.day || dtCurrent.mon != dtBar.mon || dtCurrent.year != dtBar.year)
         {
            isReset = true;
         }
      }
      else if(InpVWAPResetMode == VWAP_RESET_SESSION)
      {
         datetime sessionStart = datetime(StringToTime(IntegerToString(dtBar.year) + "." +
                                                      IntegerToString(dtBar.mon) + "." +
                                                      IntegerToString(dtBar.day) + " " +
                                                      InpSessionStart));
         if(t < sessionStart)
         {
            isReset = true;
         }
      }

      if(isReset)
      {
         start_idx = i - 1;
         break;
      }
      start_idx = i;
   }

   if(start_idx < idx) start_idx = idx;

   double sumPriceVol = 0;
   double sumVol = 0;
   double last_price = 0;

   for(int i = start_idx; i >= idx; i--)
   {
      double openPrice = iOpen(_Symbol, InpSignalTimeframe, i);
      double highPrice = iHigh(_Symbol, InpSignalTimeframe, i);
      double lowPrice = iLow(_Symbol, InpSignalTimeframe, i);
      double closePrice = iClose(_Symbol, InpSignalTimeframe, i);
      long tickVol = iTickVolume(_Symbol, InpSignalTimeframe, i);

      double price = closePrice;
      if(InpVWAPSource == PRICE_OPEN) price = openPrice;
      else if(InpVWAPSource == PRICE_HIGH) price = highPrice;
      else if(InpVWAPSource == PRICE_LOW) price = lowPrice;
      else if(InpVWAPSource == PRICE_MEDIAN) price = (highPrice + lowPrice) / 2.0;
      else if(InpVWAPSource == PRICE_TYPICAL) price = (highPrice + lowPrice + closePrice) / 3.0;
      else if(InpVWAPSource == PRICE_WEIGHTED) price = (highPrice + lowPrice + 2.0 * closePrice) / 4.0;

      last_price = price;
      double vol = (double)tickVol;
      if(vol <= 0) vol = 1.0;

      sumPriceVol += price * vol;
      sumVol += vol;
   }

   if(sumVol > 0)
      return sumPriceVol / sumVol;

   return last_price;
}

//+------------------------------------------------------------------+
//| Calculate Lot Size based on risk amount and SL distance         |
//+------------------------------------------------------------------+
double CalculateRiskLotSize(double risk_amount, double sl_distance_points)
{
   if(sl_distance_points <= 0) return SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);

   double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);

   if(tick_size <= 0) return SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);

   double price_sl_dist = sl_distance_points * point;
   double lot_step_value = price_sl_dist * (tick_value / tick_size);

   if(lot_step_value <= 0) return SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);

   double calculated_lot = risk_amount / lot_step_value;
   return calculated_lot;
}

//+------------------------------------------------------------------+
//| Normalize Volume to match broker constraints                    |
//+------------------------------------------------------------------+
double NormalizeVolume(double volume)
{
   double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   double normalized = MathRound(volume / step_lot) * step_lot;
   if(normalized < min_lot) normalized = min_lot;
   if(normalized > max_lot) normalized = max_lot;

   return normalized;
}

//+------------------------------------------------------------------+
//| Verify if there is already an open position matching magic/symbol|
//+------------------------------------------------------------------+
bool HasOpenPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;

      if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
      {
         return true;
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Check if Current Time lies within permitted trading session      |
//+------------------------------------------------------------------+
bool IsInSession()
{
   if(!InpUseSessionFilter) return true;

   datetime nowTime = TimeCurrent();
   MqlDateTime dt;
   TimeToStruct(nowTime, dt);

   string start_parts[];
   StringSplit(InpSessionStartTrading, ':', start_parts);
   if(ArraySize(start_parts) != 2) return true;

   int start_hour = (int)StringToInteger(start_parts[0]);
   int start_min = (int)StringToInteger(start_parts[1]);

   string end_parts[];
   StringSplit(InpSessionEndTrading, ':', end_parts);
   if(ArraySize(end_parts) != 2) return true;

   int end_hour = (int)StringToInteger(end_parts[0]);
   int end_min = (int)StringToInteger(end_parts[1]);

   int current_time_mins = dt.hour * 60 + dt.min;
   int start_time_mins = start_hour * 60 + start_min;
   int end_time_mins = end_hour * 60 + end_min;

   if(start_time_mins <= end_time_mins)
   {
      return (current_time_mins >= start_time_mins && current_time_mins <= end_time_mins);
   }
   else
   {
      return (current_time_mins >= start_time_mins || current_time_mins <= end_time_mins);
   }
}

//+------------------------------------------------------------------+
//| Manage Active Positions (Break Even & Trailing Stop)            |
//+------------------------------------------------------------------+
void ManagePositions()
{
   double bid = m_symbol.Bid();
   double point = m_symbol.Point();

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;

      if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
      {
         long posType = PositionGetInteger(POSITION_TYPE);
         if(posType == POSITION_TYPE_BUY)
         {
            double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
            double currentSL = PositionGetDouble(POSITION_PRICE_SL);
            double currentTP = PositionGetDouble(POSITION_PRICE_TP);

            bool modified = false;
            double newSL = currentSL;

            // 1. Break Even Protection
            if(InpEnableBreakEven)
            {
               double triggerPrice = openPrice + InpBreakEvenTriggerPoints * point;
               double lockPrice = openPrice + InpBreakEvenLockPoints * point;

               if(bid >= triggerPrice && (currentSL < lockPrice || currentSL == 0))
               {
                  newSL = lockPrice;
                  modified = true;
               }
            }

            // 2. Trailing Stop
            if(InpEnableTrailing)
            {
               double trailSL = bid - InpTrailingStopPoints * point;
               if(trailSL > openPrice)
               {
                  if(currentSL == 0 || (trailSL >= currentSL + InpTrailingStepPoints * point))
                  {
                     if(trailSL > newSL)
                     {
                        newSL = trailSL;
                        modified = true;
                     }
                  }
               }
            }

            if(modified && newSL != currentSL)
            {
               newSL = NormalizeDouble(newSL, _Digits);
               if(m_trade.PositionModify(ticket, newSL, currentTP))
               {
                  Print(StringFormat("VWAP_EMA: Modified Position Stops for Ticket %I64u. New SL: %.5f", ticket, newSL));
               }
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| GUI Dashboard Label Creation                                      |
//+------------------------------------------------------------------+
void CreateDashboard()
{
   int x = 20;
   int y = 20;
   int y_spacing = 18;

   string labels[] = {
      "Title", "Timeframe", "EMA_Value", "VWAP_Value", "Trend_Filter",
      "Signal_Status", "Account_Info", "Spread_Info", "Last_Rejection"
   };

   for(int i = 0; i < ArraySize(labels); i++)
   {
      string name = "VWAP_EMA_" + labels[i];
      if(ObjectFind(0, name) < 0)
      {
         ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
         ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
         ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y + i * y_spacing);
         ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
         ObjectSetString(0, name, OBJPROP_FONT, "Lucida Console");
         ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 10);
         ObjectSetInteger(0, name, OBJPROP_COLOR, clrWhite);
         ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
      }
   }
}

//+------------------------------------------------------------------+
//| GUI Dashboard Values Update                                       |
//+------------------------------------------------------------------+
void UpdateDashboard()
{
   // Title
   SetLabelText("Title", "=== VWAP + EMA Trend Pullback Breakout EA ===", clrYellow);

   // Timeframe
   string tf_str = EnumToString(InpSignalTimeframe);
   SetLabelText("Timeframe", "Active Timeframe: " + tf_str, clrCyan);

   // EMA & VWAP Values
   double ema1 = GetEMAValue(1);
   double vwap1 = GetVWAP(1);
   string ema_str = StringFormat("EMA(%d): %.5f", InpEMAPeriod, ema1);
   string vwap_str = StringFormat("VWAP: %.5f", vwap1);
   SetLabelText("EMA_Value", ema_str, clrWhite);
   SetLabelText("VWAP_Value", vwap_str, clrWhite);

   // Trend Filter Status
   bool trend_ok = false;
   double close1 = iClose(_Symbol, InpSignalTimeframe, 1);
   double ema2 = GetEMAValue(2);
   if(close1 > vwap1 && vwap1 > ema1 && ema1 > ema2)
   {
      trend_ok = true;
   }
   string trend_str = StringFormat("Trend Filter: %s (Close > VWAP > EMA & EMA Rising)", trend_ok ? "BULLISH" : "NEUTRAL");
   SetLabelText("Trend_Filter", trend_str, trend_ok ? clrGreen : clrRed);

   // Signal Status
   string sig_str;
   color sig_color = clrWhite;
   if(m_signal_active)
   {
      sig_str = StringFormat("Signal: ACTIVE (Breakout High: %.5f)", m_signal_high + InpEntryBuffer * SymbolInfoDouble(_Symbol, SYMBOL_POINT));
      sig_color = clrOrange;
   }
   else
   {
      sig_str = "Signal: Scanning for Setup...";
      sig_color = clrGray;
   }
   SetLabelText("Signal_Status", sig_str, sig_color);

   // Account Info
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   string acc_str = StringFormat("Balance: %.2f | Equity: %.2f", balance, equity);
   SetLabelText("Account_Info", acc_str, clrWhite);

   // Spread
   int spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   string spread_str = StringFormat("Current Spread: %d points (Max Allowed: %d)", spread, InpMaxSpread);
   color spread_color = (InpUseSpreadFilter && spread > InpMaxSpread) ? clrRed : clrGreen;
   SetLabelText("Spread_Info", spread_str, spread_color);

   // Last Rejection
   SetLabelText("Last_Rejection", "Status/Reason: " + m_last_rejection, clrLightBlue);

   ChartRedraw();
}

//+------------------------------------------------------------------+
//| Set Text on Dashboard Object                                     |
//+------------------------------------------------------------------+
void SetLabelText(string subName, string text, color clr)
{
   string name = "VWAP_EMA_" + subName;
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
}

//+------------------------------------------------------------------+
//| GUI Dashboard Deletion                                            |
//+------------------------------------------------------------------+
void DeleteDashboard()
{
   string labels[] = {
      "Title", "Timeframe", "EMA_Value", "VWAP_Value", "Trend_Filter",
      "Signal_Status", "Account_Info", "Spread_Info", "Last_Rejection"
   };

   for(int i = 0; i < ArraySize(labels); i++)
   {
      string name = "VWAP_EMA_" + labels[i];
      ObjectDelete(0, name);
   }
   ChartRedraw();
}
