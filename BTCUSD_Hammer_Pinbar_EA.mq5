//+------------------------------------------------------------------+
//|                                     BTCUSD_Hammer_Pinbar_EA.mq5   |
//|                                                   Copyright 2026 |
//|                                             https://github.com/  |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026"
#property link      "https://github.com/"
#property version   "1.00"
#property description "Professional BTCUSD Hammer & Pinbar Reversal Expert Advisor"
#property description "Designed for XM Trading and compatible with any MT5 broker."
#property description "Trades BUY ONLY using Green Hammer and Green Pin Bar patterns."
#property description "Strict one-candle breakout entry confirmation rule."

#include <Trade\Trade.mqh>

//--- Custom Enums
enum ENUM_CUSTOM_TIMEFRAME
{
   TF_M1  = PERIOD_M1,  // M1
   TF_M3  = PERIOD_M3,  // M3
   TF_M5  = PERIOD_M5,  // M5
   TF_M15 = PERIOD_M15, // M15 (Default)
   TF_M30 = PERIOD_M30, // M30
   TF_H1  = PERIOD_H1,  // H1
   TF_H4  = PERIOD_H4,  // H4
   TF_D1  = PERIOD_D1   // D1
};

//--- User Inputs
input group "---- Pattern Settings ----"
input bool                 InpEnableHammer       = true;       // Enable Hammer Detection
input bool                 InpEnablePinBar       = true;       // Enable Pin Bar Detection
input double               InpHammerLowerWickPct = 50.0;       // Hammer Lower Wick % (Min)
input double               InpHammerBodyPct      = 20.0;       // Hammer Body % (Max)
input double               InpHammerUpperWickPct = 10.0;       // Hammer Upper Wick % (Max)
input double               InpPinBarLowerWickPct = 50.0;       // Pin Bar Lower Wick % (Min)
input double               InpPinBarBodyPct      = 25.0;       // Pin Bar Body % (Max)
input double               InpPinBarUpperWickPct = 15.0;       // Pin Bar Upper Wick % (Max)

input group "---- Candle Filter ----"
input int                  InpMinCandleRange     = 1000;       // Minimum Candle Range (Points)

input group "---- Trade Settings ----"
input ENUM_CUSTOM_TIMEFRAME InpTimeframe         = TF_M15;     // Signal Timeframe
input double               InpLotSize            = 0.1;        // Lot Size
input int                  InpEntryBuffer        = 0;          // Entry Buffer (Points)
input int                  InpStopLossBuffer     = 0;          // Stop Loss Buffer (Points)
input double               InpRiskReward         = 2.0;        // Risk : Reward Ratio (1:X)
input int                  InpSlippage           = 30;         // Slippage (Points)
input string               InpTradeComment       = "BTCUSD_Hammer_Pinbar"; // Trade Comment
input bool                 InpOnePositionAtTime  = true;       // One Position At A Time
input int                  InpSignalExpiryBars   = 1;          // Signal Expiry Bars (Strict Rule requires 1)
input int                  InpMagicNumber        = 123456;     // Magic Number

//--- Global Variables
CTrade         m_trade;
datetime       m_last_bar_time   = 0;
bool           m_signal_active   = false;
double         m_signal_high     = 0.0;
double         m_signal_low      = 0.0;
datetime       m_signal_time     = 0;
ENUM_TIMEFRAMES m_timeframe;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   //--- Map Custom Timeframe to Standard MT5 Timeframe
   m_timeframe = (ENUM_TIMEFRAMES)InpTimeframe;

   //--- Initialize CTrade Settings
   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_trade.SetDeviationInPoints(InpSlippage);

   //--- Ensure Symbol is active in Market Watch
   if(!SymbolInfoInteger(Symbol(), SYMBOL_SELECT))
   {
      SymbolSelect(Symbol(), true);
   }

   //--- Check active timeframe bar open time
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(Symbol(), m_timeframe, 0, 1, rates) > 0)
   {
      m_last_bar_time = rates[0].time;
   }
   else
   {
      Print("Warning: Unable to read historical bar data on initialization.");
   }

   //--- Initialize a 1-second timer for dashboard updates
   EventSetTimer(1);

   PrintFormat("EA Initialized successfully. Symbol: %s, Timeframe: %s, Magic: %d",
               Symbol(), EnumToString(InpTimeframe), InpMagicNumber);

   //--- Initial dashboard render
   UpdateDashboard();

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   Comment(""); // Clear chart dashboard comments
   PrintFormat("EA Deinitialized. Reason: %d", reason);
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   //--- Fetch historical and live rates (rates[0] is current, rates[1] is Signal candle, rates[2] is Previous candle)
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(Symbol(), m_timeframe, 0, 3, rates);
   if(copied < 3)
   {
      return; // Wait for enough data
   }

   //--- Detect New Bar Close / Open Event
   if(rates[0].time != m_last_bar_time)
   {
      m_last_bar_time = rates[0].time;

      //--- Strict One-Candle Expiry rule:
      // If a signal was active, and a new bar has just opened, it means the breakout candle (which was rates[0]
      // in the previous tick and is now rates[1]) has closed. Since it didn't trigger, the signal is expired.
      if(m_signal_active)
      {
         PrintFormat("Signal EXPIRED: Immediate next candle (%s) failed to break Signal High (%.5f). Discarding signal permanently.",
                     TimeToString(rates[1].time), m_signal_high);
         m_signal_active = false;
         m_signal_high   = 0.0;
         m_signal_low    = 0.0;
         m_signal_time   = 0;
      }

      //--- Scan for a new signal if position conditions allow
      if(!InpOnePositionAtTime || !IsPositionOpen())
      {
         CheckSignalCandle(rates);
      }
   }

   //--- Breakout Entry Monitoring
   if(m_signal_active)
   {
      //--- Skip entry if One Position At A Time is enabled and a position is active
      if(InpOnePositionAtTime && IsPositionOpen())
      {
         return;
      }

      double current_ask = SymbolInfoDouble(Symbol(), SYMBOL_ASK);
      double point_size  = SymbolInfoDouble(Symbol(), SYMBOL_POINT);
      double entry_trigger_price = m_signal_high + (InpEntryBuffer * point_size);

      if(current_ask > entry_trigger_price)
      {
         //--- Breakout Confirmed! Execute Market Buy Order
         ExecuteBuyOrder(entry_trigger_price, current_ask);

         //--- Invalidate Signal immediately to ensure only one trade can ever be taken from this signal candle
         m_signal_active = false;
         m_signal_high   = 0.0;
         m_signal_low    = 0.0;
         m_signal_time   = 0;
      }
   }

   //--- Update UI Dashboard on tick
   UpdateDashboard();
}

//+------------------------------------------------------------------+
//| Timer function for responsive UI                                 |
//+------------------------------------------------------------------+
void OnTimer()
{
   UpdateDashboard();
}

//+------------------------------------------------------------------+
//| Check if a position is currently open for this EA                |
//+------------------------------------------------------------------+
bool IsPositionOpen()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetString(POSITION_SYMBOL) == Symbol() &&
            PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         {
            return true;
         }
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Check newly closed candles for the required reversal signal     |
//+------------------------------------------------------------------+
void CheckSignalCandle(const MqlRates &rates[])
{
   // rates[1] = newly closed signal candle candidate
   // rates[2] = previous completed candle

   double high  = rates[1].high;
   double low   = rates[1].low;
   double open  = rates[1].open;
   double close = rates[1].close;

   double total_range = high - low;
   double point_size  = SymbolInfoDouble(Symbol(), SYMBOL_POINT);
   double total_range_points = (point_size > 0.0) ? (total_range / point_size) : 0.0;

   //--- 1. Previous Candle Must Be Bearish (Red)
   bool is_prev_bearish = (rates[2].close < rates[2].open);
   if(!is_prev_bearish)
   {
      return; // Quietly return, standard state
   }

   //--- 2. Signal Candle Must Be Bullish (Green)
   bool is_sig_bullish = (close > open);
   if(!is_sig_bullish)
   {
      return; // Quietly return
   }

   //--- 3. Ignore Tiny Candles
   if(total_range_points < (double)InpMinCandleRange)
   {
      PrintFormat("Signal Rejected: Total range of candle %.1f points is below minimum filter of %d points.",
                  total_range_points, InpMinCandleRange);
      return;
   }

   if(total_range <= 0.0) return;

   //--- Calculate Candle Geometry Percentages
   double body       = close - open;
   double upper_wick = high - close;
   double lower_wick = open - low;

   double lower_wick_pct = (lower_wick / total_range) * 100.0;
   double body_pct       = (body / total_range) * 100.0;
   double upper_wick_pct = (upper_wick / total_range) * 100.0;

   bool is_hammer  = false;
   bool is_pinbar  = false;

   //--- Validate Hammer Settings
   if(InpEnableHammer)
   {
      if(lower_wick_pct >= InpHammerLowerWickPct &&
         body_pct       <= InpHammerBodyPct &&
         upper_wick_pct <= InpHammerUpperWickPct)
      {
         is_hammer = true;
      }
   }

   //--- Validate Pin Bar Settings
   if(InpEnablePinBar)
   {
      if(lower_wick_pct >= InpPinBarLowerWickPct &&
         body_pct       <= InpPinBarBodyPct &&
         upper_wick_pct <= InpPinBarUpperWickPct)
      {
         is_pinbar = true;
      }
   }

   //--- Trigger Signal Setup if either enabled pattern is qualified
   if(is_hammer || is_pinbar)
   {
      m_signal_active = true;
      m_signal_high   = high;
      m_signal_low    = low;
      m_signal_time   = rates[1].time;

      string pattern_name = "";
      if(is_hammer && is_pinbar) pattern_name = "Hammer & Pin Bar";
      else if(is_hammer) pattern_name = "Hammer";
      else pattern_name = "Pin Bar";

      PrintFormat("VALID REVERSAL PATTERN DETECTED! Pattern: %s, Time: %s, High: %.5f, Low: %.5f. (Lower Wick: %.1f%%, Body: %.1f%%, Upper Wick: %.1f%%)",
                  pattern_name, TimeToString(m_signal_time), m_signal_high, m_signal_low, lower_wick_pct, body_pct, upper_wick_pct);
   }
   else
   {
      // Diagnostic logging to assist strategy optimization and troubleshooting
      PrintFormat("Candidate rejected. Geometry: Lower Wick %.1f%%, Body %.1f%%, Upper Wick %.1f%%. "
                  "(Hammer Requirements: LW >= %.1f%%, B <= %.1f%%, UW <= %.1f%%; "
                  "Pin Bar Requirements: LW >= %.1f%%, B <= %.1f%%, UW <= %.1f%%)",
                  lower_wick_pct, body_pct, upper_wick_pct,
                  InpHammerLowerWickPct, InpHammerBodyPct, InpHammerUpperWickPct,
                  InpPinBarLowerWickPct, InpPinBarBodyPct, InpPinBarUpperWickPct);
   }
}

//+------------------------------------------------------------------+
//| Execute BUY Trade with risk calculations and standard CTrade      |
//+------------------------------------------------------------------+
void ExecuteBuyOrder(double entry_trigger_price, double current_ask)
{
   double point_size = SymbolInfoDouble(Symbol(), SYMBOL_POINT);

   //--- Stop Loss = Signal Low - Stop Loss Buffer
   double sl_price = m_signal_low - (InpStopLossBuffer * point_size);

   //--- Risk = Entry - Stop Loss
   double risk = entry_trigger_price - sl_price;

   if(risk <= 0.0)
   {
      PrintFormat("Error: Risk calculation is invalid (%.5f). Stop Loss: %.5f, Entry Trigger: %.5f. Order Aborted.",
                  risk, sl_price, entry_trigger_price);
      return;
   }

   //--- Take Profit = Entry + (Risk * RiskReward)
   double tp_price = entry_trigger_price + (risk * InpRiskReward);

   //--- Normalize volume and price values
   double norm_ask = NormalizePrice(current_ask);
   double norm_sl  = NormalizePrice(sl_price);
   double norm_tp  = NormalizePrice(tp_price);
   double norm_lot = NormalizeVolume(InpLotSize);

   PrintFormat("Breakout Confirmed! Submitting Market Buy Order. Lot: %.2f, Price: %.5f, SL: %.5f, TP: %.5f, Risk: %.5f",
               norm_lot, norm_ask, norm_sl, norm_tp, risk);

   if(m_trade.Buy(norm_lot, Symbol(), norm_ask, norm_sl, norm_tp, InpTradeComment))
   {
      uint retcode = m_trade.ResultRetcode();
      if(retcode == TRADE_RETCODE_DONE || retcode == TRADE_RETCODE_PLACED)
      {
         PrintFormat("BUY trade executed successfully! Retcode: %u, Order: %I64u, Deal: %I64u",
                     retcode, m_trade.ResultOrder(), m_trade.ResultDeal());
      }
      else
      {
         PrintFormat("BUY trade placed with notice. Retcode: %u, Server Message: %s",
                     retcode, m_trade.ResultRetcodeDescription());
      }
   }
   else
   {
      PrintFormat("CRITICAL: BUY trade execution failed! Retcode: %u, Description: %s",
                  m_trade.ResultRetcode(), m_trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Price normalization to match symbol digits and tick size         |
//+------------------------------------------------------------------+
double NormalizePrice(double price)
{
   double tick_size = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_SIZE);
   int digits       = (int)SymbolInfoInteger(Symbol(), SYMBOL_DIGITS);

   if(tick_size <= 0.0)
   {
      return NormalizeDouble(price, digits);
   }

   return NormalizeDouble(MathRound(price / tick_size) * tick_size, digits);
}

//+------------------------------------------------------------------+
//| Volume normalization to comply with broker lot filters           |
//+------------------------------------------------------------------+
double NormalizeVolume(double volume)
{
   double min_lot  = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MIN);
   double max_lot  = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MAX);
   double step_lot = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_STEP);

   double normalized = MathRound(volume / step_lot) * step_lot;
   if(normalized < min_lot)
      normalized = min_lot;
   if(normalized > max_lot)
      normalized = max_lot;

   return NormalizeDouble(normalized, 2);
}

//+------------------------------------------------------------------+
//| Interactive UI Dashboard for real-time monitoring                 |
//+------------------------------------------------------------------+
void UpdateDashboard()
{
   string text = "========================================================\n";
   text += "     BTCUSD HAMMER & PINBAR REVERSAL EA (XM)       \n";
   text += "========================================================\n";
   text += StringFormat("  Symbol:             %s\n", Symbol());
   text += StringFormat("  Timeframe:          %s\n", EnumToString(InpTimeframe));
   text += StringFormat("  Lot Size:           %.2f\n", InpLotSize);
   text += StringFormat("  Risk:Reward Ratio:  1 : %.2f\n", InpRiskReward);
   text += StringFormat("  One Position Limit: %s\n", InpOnePositionAtTime ? "ENABLED" : "DISABLED");
   text += StringFormat("  Position Active:    %s\n", IsPositionOpen() ? "YES" : "NO");
   text += "--------------------------------------------------------\n";
   text += StringFormat("  Hammer Setup:       %s (LW >= %.0f%%, B <= %.0f%%, UW <= %.0f%%)\n",
                        InpEnableHammer ? "ENABLED" : "DISABLED", InpHammerLowerWickPct, InpHammerBodyPct, InpHammerUpperWickPct);
   text += StringFormat("  Pin Bar Setup:      %s (LW >= %.0f%%, B <= %.0f%%, UW <= %.0f%%)\n",
                        InpEnablePinBar ? "ENABLED" : "DISABLED", InpPinBarLowerWickPct, InpPinBarBodyPct, InpPinBarUpperWickPct);
   text += StringFormat("  Min Candle Range:   %d Points\n", InpMinCandleRange);
   text += "--------------------------------------------------------\n";

   if(m_signal_active)
   {
      double point_size = SymbolInfoDouble(Symbol(), SYMBOL_POINT);
      double trigger_price = m_signal_high + (InpEntryBuffer * point_size);
      double stop_loss_price = m_signal_low - (InpStopLossBuffer * point_size);

      text += "  >>> PENDING CONFIRMATION BREAKOUT SIGNAL <<<\n";
      text += StringFormat("  Signal Closed At:   %s\n", TimeToString(m_signal_time));
      text += StringFormat("  Signal High:        %.5f\n", m_signal_high);
      text += StringFormat("  Signal Low:         %.5f\n", m_signal_low);
      text += StringFormat("  Entry Breakout Target: %.5f\n", trigger_price);
      text += StringFormat("  Calculated Stop Loss:  %.5f\n", stop_loss_price);
      text += StringFormat("  Current Ask Price:  %.5f\n", SymbolInfoDouble(Symbol(), SYMBOL_ASK));
   }
   else
   {
      text += "  Status: Scanning for Bullish Hammer / Pin Bar Setup...\n";
   }
   text += "========================================================\n";

   Comment(text);
}
