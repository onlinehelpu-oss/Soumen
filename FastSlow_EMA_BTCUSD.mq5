//+------------------------------------------------------------------+
//|                                        FastSlow_EMA_BTCUSD.mq5   |
//|                                                      Jules       |
//|                                             https://github.com   |
//+------------------------------------------------------------------+
#property copyright "Jules"
#property link      "https://github.com"
#property version   "1.00"
#property description "Fast-Slow EMA Strategy - BTCUSD"
#property description "Strict Next Candle Entry and Multi-Condition EMA Breakout Exit"

//--- Include Trade Standard Library
#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\SymbolInfo.mqh>

//--- Inputs
input group "=== Strategy Parameters ==="
input ENUM_TIMEFRAMES InpTimeframe       = PERIOD_M5;      // Timeframe
input int            InpFastEMAPeriod   = 9;              // Fast EMA Period
input int            InpSlowEMAPeriod   = 15;             // Slow EMA Period
input int            InpExitEMAPeriod   = 50;             // Exit EMA Period
input double         InpEMABuffer       = 0.0;            // EMA Buffer in points (e.g. 0.0)
input bool           InpRequireGreenLong= true;           // Require Green Signal Candle for Long
input bool           InpRequireRedShort = true;           // Require Red Signal Candle for Short
input double         InpMinCandlePct    = 0.0;            // Min Candle Range % (0.0 to disable)
input double         InpMinCandlePoints = 0.0;            // Min Candle Range in Points (0.0 to disable)

enum ENUM_SL_MODE {
   SL_MODE_SIGNAL, // Signal Candle Extreme (Low/High)
   SL_MODE_SWING   // Swing Extreme (Low/High)
};

input group "=== Risk Management ==="
input ENUM_SL_MODE   InpSLMode          = SL_MODE_SIGNAL; // Stop Loss Mode
input int            InpSwingLookback   = 5;              // Swing Lookback candles
input int            InpTPLookback      = 50;             // TP Lookback candles (for Target)
input double         InpSLBufferPoints  = 2.0;            // Stop Loss Buffer in points (e.g. 2.0)
input double         InpLotSize         = 0.1;            // Fixed Lot Size
input ulong          InpMagicNumber     = 123456;         // Magic Number

//--- Global Objects
CTrade m_trade;

//--- Indicator Handles
int m_handle_fast_ema = INVALID_HANDLE;
int m_handle_slow_ema = INVALID_HANDLE;
int m_handle_exit_ema = INVALID_HANDLE;

//--- State Variables
datetime m_last_bar_time           = 0;

bool     m_long_signal_active      = false;
bool     m_short_signal_active     = false;
datetime m_signal_candle_time      = 0;
double   m_signal_candle_high      = 0.0;
double   m_signal_candle_low       = 0.0;

bool     m_long_exit_pending       = false;
bool     m_short_exit_pending      = false;
datetime m_exit_signal_candle_time = 0;
double   m_exit_signal_candle_low  = 0.0;
double   m_exit_signal_candle_high = 0.0;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   //--- Set Magic Number for trade requests
   m_trade.SetExpertMagicNumber(InpMagicNumber);

   //--- Initialize indicator handles
   m_handle_fast_ema = iMA(_Symbol, InpTimeframe, InpFastEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
   m_handle_slow_ema = iMA(_Symbol, InpTimeframe, InpSlowEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
   m_handle_exit_ema = iMA(_Symbol, InpTimeframe, InpExitEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);

   if(m_handle_fast_ema == INVALID_HANDLE || m_handle_slow_ema == INVALID_HANDLE || m_handle_exit_ema == INVALID_HANDLE) {
      Print("Error: Failed to create indicator handles.");
      return INIT_FAILED;
   }

   //--- Pre-load bar time to prevent trigger on first tick
   m_last_bar_time = GetBarTime(0);

   Print("FastSlow_EMA_BTCUSD EA initialized successfully.");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   //--- Release Indicator Handles
   IndicatorRelease(m_handle_fast_ema);
   IndicatorRelease(m_handle_slow_ema);
   IndicatorRelease(m_handle_exit_ema);

   //--- Clear chart comment
   Comment("");
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick)) {
      return;
   }

   //--- Check if a new candle is completed
   if(CheckNewBar()) {
      EvaluateNewCandleSignals();
   }

   //--- Manage execution triggers & active positions
   ManageState(tick);

   //--- Update dashboard
   UpdateDashboard();
}

//+------------------------------------------------------------------+
//| Check for a Completed Candle (New Bar)                          |
//+------------------------------------------------------------------+
bool CheckNewBar()
{
   datetime current_bar_time = GetBarTime(0);
   if(current_bar_time == 0) return false;

   if(m_last_bar_time == 0) {
      m_last_bar_time = current_bar_time;
      return false;
   }

   if(current_bar_time != m_last_bar_time) {
      m_last_bar_time = current_bar_time;
      return true;
   }

   return false;
}

//+------------------------------------------------------------------+
//| Evaluate finished candle signals                                 |
//+------------------------------------------------------------------+
void EvaluateNewCandleSignals()
{
   bool is_pos_open = IsPositionOpen();

   //--- Fetch historical values for Completed Bar (Bar 1)
   double fast_ema = GetEMAValue(m_handle_fast_ema, 1);
   double slow_ema = GetEMAValue(m_handle_slow_ema, 1);
   double exit_ema = GetEMAValue(m_handle_exit_ema, 1);

   double open_val  = GetOpen(1);
   double high_val  = GetHigh(1);
   double low_val   = GetLow(1);
   double close_val = GetClose(1);
   datetime time_val = GetBarTime(1);

   if(fast_ema <= 0 || slow_ema <= 0 || exit_ema <= 0 || open_val <= 0 || high_val <= 0 || low_val <= 0 || close_val <= 0) {
      Print("Warning: Historical data or indicators not fully loaded.");
      return;
   }

   double range = high_val - low_val;
   double min_range_pct = close_val * InpMinCandlePct / 100.0;
   bool range_ok = (range >= min_range_pct) && (range >= InpMinCandlePoints);

   //--- 1. NO Position Open: Scan for ENTRY signal
   if(!is_pos_open) {
      // Expiry Check for prior pending signal
      if(m_long_signal_active && m_signal_candle_time != time_val) {
         m_long_signal_active = false;
         Print("Long breakout signal expired. Next candle failed to trigger.");
      }
      if(m_short_signal_active && m_signal_candle_time != time_val) {
         m_short_signal_active = false;
         Print("Short breakout signal expired. Next candle failed to trigger.");
      }

      // BULLISH Strict Body Cross
      bool bull_ema_ok = fast_ema > slow_ema;
      bool bull_body_opens_below = open_val < MathMin(fast_ema, slow_ema);
      bool bull_body_closes_above = close_val > MathMax(fast_ema, slow_ema) + InpEMABuffer;
      bool bull_green_ok = !InpRequireGreenLong || (close_val > open_val);

      if(bull_ema_ok && bull_body_opens_below && bull_body_closes_above && bull_green_ok && range_ok) {
         m_long_signal_active = true;
         m_short_signal_active = false;
         m_signal_candle_time = time_val;
         m_signal_candle_high = high_val;
         m_signal_candle_low = low_val;

         Print("=== BULLISH SIGNAL DETECTED ===");
         Print("  Time: ", TimeToString(time_val));
         Print("  Fast EMA: ", fast_ema, " | Slow EMA: ", slow_ema);
         Print("  OHLC: O=", open_val, " H=", high_val, " L=", low_val, " C=", close_val);
         Print("  Trigger High Threshold: ", m_signal_candle_high);
      }

      // BEARISH Strict Body Cross
      bool bear_ema_ok = fast_ema < slow_ema;
      bool bear_body_opens_above = open_val > MathMax(fast_ema, slow_ema);
      bool bear_body_closes_below = close_val < MathMin(fast_ema, slow_ema) - InpEMABuffer;
      bool bear_green_ok = !InpRequireRedShort || (close_val < open_val);

      if(bear_ema_ok && bear_body_opens_above && bear_body_closes_below && bear_green_ok && range_ok) {
         m_short_signal_active = true;
         m_long_signal_active = false;
         m_signal_candle_time = time_val;
         m_signal_candle_high = high_val;
         m_signal_candle_low = low_val;

         Print("=== BEARISH SIGNAL DETECTED ===");
         Print("  Time: ", TimeToString(time_val));
         Print("  Fast EMA: ", fast_ema, " | Slow EMA: ", slow_ema);
         Print("  OHLC: O=", open_val, " H=", high_val, " L=", low_val, " C=", close_val);
         Print("  Trigger Low Threshold: ", m_signal_candle_low);
      }
   }
   //--- 2. Position Open: Scan for EMA EXIT signal
   else {
      int pos_type = GetPositionType();

      if(pos_type == POSITION_TYPE_BUY) {
         // Long position exit conditions based on Exit EMA
         bool is_red = close_val < open_val;
         bool intrabar_up = (open_val < exit_ema) && (high_val > exit_ema);
         bool closed_below = close_val < exit_ema - InpEMABuffer;

         if(is_red && intrabar_up && closed_below) {
            m_long_exit_pending = true;
            m_exit_signal_candle_time = time_val;
            m_exit_signal_candle_low = low_val;
            Print("=== LONG POSITION EXIT EMA SIGNAL ===");
            Print("  Time: ", TimeToString(time_val));
            Print("  Exit EMA: ", exit_ema);
            Print("  Exit Low Threshold: ", m_exit_signal_candle_low);
         }
      }
      else if(pos_type == POSITION_TYPE_SELL) {
         // Short position exit conditions based on Exit EMA
         bool is_green = close_val > open_val;
         bool intrabar_down = (open_val > exit_ema) && (low_val < exit_ema);
         bool closed_above = close_val > exit_ema + InpEMABuffer;

         if(is_green && intrabar_down && closed_above) {
            m_short_exit_pending = true;
            m_exit_signal_candle_time = time_val;
            m_exit_signal_candle_high = high_val;
            Print("=== SHORT POSITION EXIT EMA SIGNAL ===");
            Print("  Time: ", TimeToString(time_val));
            Print("  Exit EMA: ", exit_ema);
            Print("  Exit High Threshold: ", m_exit_signal_candle_high);
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Manage execution triggers & state on tick                        |
//+------------------------------------------------------------------+
void ManageState(const MqlTick &tick)
{
   bool is_pos_open = IsPositionOpen();

   if(!is_pos_open) {
      // Clear exit flags since no position is open
      m_long_exit_pending = false;
      m_short_exit_pending = false;

      // Handle Long entry breakout
      if(m_long_signal_active) {
         datetime current_bar_time = GetBarTime(0);
         datetime next_bar_expected = m_signal_candle_time + PeriodSeconds(InpTimeframe);

         if(current_bar_time == next_bar_expected) {
            if(tick.ask > m_signal_candle_high) {
               Print("Long breakout triggered! Price: ", tick.ask, " > ", m_signal_candle_high);

               //--- Calculate Stop Loss & Take Profit
               double sl = 0.0;
               double tp = 0.0;

               if(InpSLMode == SL_MODE_SIGNAL) {
                  sl = m_signal_candle_low - InpSLBufferPoints;
               } else if(InpSLMode == SL_MODE_SWING) {
                  double lows[];
                  ArraySetAsSeries(lows, true);
                  if(CopyLow(_Symbol, InpTimeframe, 1, InpSwingLookback, lows) == InpSwingLookback) {
                     double swing_low = lows[ArrayMinimum(lows)];
                     sl = swing_low - InpSLBufferPoints;
                  } else {
                     sl = m_signal_candle_low - InpSLBufferPoints;
                  }
               }

               double highs[];
               ArraySetAsSeries(highs, true);
               if(CopyHigh(_Symbol, InpTimeframe, 1, InpTPLookback, highs) == InpTPLookback) {
                  tp = highs[ArrayMaximum(highs)];
               } else {
                  tp = m_signal_candle_high + (m_signal_candle_high - m_signal_candle_low) * 2.0; // Fallback 1:2
               }

               // Anti-Race Lock
               m_long_signal_active = false;

               // Place Buy order
               double lots = NormalizeVolume(InpLotSize);
               if(m_trade.Buy(lots, _Symbol, tick.ask, NormalizePrice(sl), NormalizePrice(tp), "Fast-Slow EMA Long")) {
                  Print("Long order submitted successfully.");
               } else {
                  Print("Failed to submit Long order. Error: ", m_trade.ResultRetcodeDescription());
                  m_long_signal_active = true; // Restore on error
               }
            }
         }
      }

      // Handle Short entry breakout
      if(m_short_signal_active) {
         datetime current_bar_time = GetBarTime(0);
         datetime next_bar_expected = m_signal_candle_time + PeriodSeconds(InpTimeframe);

         if(current_bar_time == next_bar_expected) {
            if(tick.bid < m_signal_candle_low) {
               Print("Short breakout triggered! Price: ", tick.bid, " < ", m_signal_candle_low);

               //--- Calculate Stop Loss & Take Profit
               double sl = 0.0;
               double tp = 0.0;

               if(InpSLMode == SL_MODE_SIGNAL) {
                  sl = m_signal_candle_high + InpSLBufferPoints;
               } else if(InpSLMode == SL_MODE_SWING) {
                  double highs[];
                  ArraySetAsSeries(highs, true);
                  if(CopyHigh(_Symbol, InpTimeframe, 1, InpSwingLookback, highs) == InpSwingLookback) {
                     double swing_high = highs[ArrayMaximum(highs)];
                     sl = swing_high + InpSLBufferPoints;
                  } else {
                     sl = m_signal_candle_high + InpSLBufferPoints;
                  }
               }

               double lows[];
               ArraySetAsSeries(lows, true);
               if(CopyLow(_Symbol, InpTimeframe, 1, InpTPLookback, lows) == InpTPLookback) {
                  tp = lows[ArrayMinimum(lows)];
               } else {
                  tp = m_signal_candle_low - (m_signal_candle_high - m_signal_candle_low) * 2.0; // Fallback 1:2
               }

               // Anti-Race Lock
               m_short_signal_active = false;

               // Place Sell order
               double lots = NormalizeVolume(InpLotSize);
               if(m_trade.Sell(lots, _Symbol, tick.bid, NormalizePrice(sl), NormalizePrice(tp), "Fast-Slow EMA Short")) {
                  Print("Short order submitted successfully.");
               } else {
                  Print("Failed to submit Short order. Error: ", m_trade.ResultRetcodeDescription());
                  m_short_signal_active = true; // Restore on error
               }
            }
         }
      }
   }
   else {
      // Clear pending triggers as a position is already active
      m_long_signal_active = false;
      m_short_signal_active = false;

      int pos_type = GetPositionType();

      // Manage Long Exit EMA breakout trigger
      if(pos_type == POSITION_TYPE_BUY && m_long_exit_pending) {
         datetime current_bar_time = GetBarTime(0);
         datetime next_bar_expected = m_exit_signal_candle_time + PeriodSeconds(InpTimeframe);

         if(current_bar_time >= next_bar_expected) {
            if(tick.bid < m_exit_signal_candle_low) {
               Print("Long Exit EMA breakout triggered! Price: ", tick.bid, " < ", m_exit_signal_candle_low);
               m_long_exit_pending = false;
               CloseAllPositions();
            }
         }
      }
      // Manage Short Exit EMA breakout trigger
      else if(pos_type == POSITION_TYPE_SELL && m_short_exit_pending) {
         datetime current_bar_time = GetBarTime(0);
         datetime next_bar_expected = m_exit_signal_candle_time + PeriodSeconds(InpTimeframe);

         if(current_bar_time >= next_bar_expected) {
            if(tick.ask > m_exit_signal_candle_high) {
               Print("Short Exit EMA breakout triggered! Price: ", tick.ask, " > ", m_exit_signal_candle_high);
               m_short_exit_pending = false;
               CloseAllPositions();
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Get Indicator Value Safely                                       |
//+------------------------------------------------------------------+
double GetEMAValue(int handle, int index)
{
   double val[];
   ArraySetAsSeries(val, true);
   if(CopyBuffer(handle, 0, index, 1, val) < 1) {
      return 0.0;
   }
   return val[0];
}

//+------------------------------------------------------------------+
//| Safe OHLC Data Wrappers                                          |
//+------------------------------------------------------------------+
datetime GetBarTime(int index)
{
   datetime t[];
   ArraySetAsSeries(t, true);
   if(CopyTime(_Symbol, InpTimeframe, index, 1, t) < 1) return 0;
   return t[0];
}

double GetOpen(int index)
{
   double val[];
   ArraySetAsSeries(val, true);
   if(CopyOpen(_Symbol, InpTimeframe, index, 1, val) < 1) return 0.0;
   return val[0];
}

double GetHigh(int index)
{
   double val[];
   ArraySetAsSeries(val, true);
   if(CopyHigh(_Symbol, InpTimeframe, index, 1, val) < 1) return 0.0;
   return val[0];
}

double GetLow(int index)
{
   double val[];
   ArraySetAsSeries(val, true);
   if(CopyLow(_Symbol, InpTimeframe, index, 1, val) < 1) return 0.0;
   return val[0];
}

double GetClose(int index)
{
   double val[];
   ArraySetAsSeries(val, true);
   if(CopyClose(_Symbol, InpTimeframe, index, 1, val) < 1) return 0.0;
   return val[0];
}

//+------------------------------------------------------------------+
//| Check if position for magic and symbol is open                   |
//+------------------------------------------------------------------+
bool IsPositionOpen()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0) {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber) {
            return true;
         }
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Get open position type                                           |
//+------------------------------------------------------------------+
int GetPositionType()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0) {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber) {
            return (int)PositionGetInteger(POSITION_TYPE);
         }
      }
   }
   return -1;
}

//+------------------------------------------------------------------+
//| Close all matching positions                                     |
//+------------------------------------------------------------------+
void CloseAllPositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0) {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber) {
            m_trade.PositionClose(ticket);
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Normalize volume lot sizes                                       |
//+------------------------------------------------------------------+
double NormalizeVolume(double volume)
{
   double min_vol = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_vol = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step_vol = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   if(volume < min_vol) volume = min_vol;
   if(volume > max_vol) volume = max_vol;

   double normalized = MathRound(volume / step_vol) * step_vol;
   return normalized;
}

//+------------------------------------------------------------------+
//| Normalize price to symbol digits                                 |
//+------------------------------------------------------------------+
double NormalizePrice(double price)
{
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   return NormalizeDouble(price, digits);
}

//+------------------------------------------------------------------+
//| Update real-time chart HUD dashboard                             |
//+------------------------------------------------------------------+
void UpdateDashboard()
{
   double fast_ema = GetEMAValue(m_handle_fast_ema, 0);
   double slow_ema = GetEMAValue(m_handle_slow_ema, 0);
   double exit_ema = GetEMAValue(m_handle_exit_ema, 0);

   string status_str = "Watching";
   if(IsPositionOpen()) {
      int pos_type = GetPositionType();
      if(pos_type == POSITION_TYPE_BUY) {
         status_str = "Long Position Active" + (m_long_exit_pending ? " (Exit Pending)" : "");
      } else if(pos_type == POSITION_TYPE_SELL) {
         status_str = "Short Position Active" + (m_short_exit_pending ? " (Exit Pending)" : "");
      }
   } else {
      if(m_long_signal_active) status_str = "Long Signal Pending Breakout (> " + DoubleToString(m_signal_candle_high, 2) + ")";
      else if(m_short_signal_active) status_str = "Short Signal Pending Breakout (< " + DoubleToString(m_signal_candle_low, 2) + ")";
   }

   string text = "==================================================\n" +
                 "          FAST-SLOW EMA STRATEGY (BTCUSD)         \n" +
                 "==================================================\n" +
                 " Symbol: " + _Symbol + "\n" +
                 " Timeframe: " + EnumToString(InpTimeframe) + "\n" +
                 " Fast EMA (" + IntegerToString(InpFastEMAPeriod) + "): " + DoubleToString(fast_ema, 2) + "\n" +
                 " Slow EMA (" + IntegerToString(InpSlowEMAPeriod) + "): " + DoubleToString(slow_ema, 2) + "\n" +
                 " Exit EMA (" + IntegerToString(InpExitEMAPeriod) + "): " + DoubleToString(exit_ema, 2) + "\n" +
                 "--------------------------------------------------\n" +
                 " Status: " + status_str + "\n";

   if(m_long_signal_active || m_short_signal_active) {
      text += " Signal Candle High: " + DoubleToString(m_signal_candle_high, 2) + "\n" +
              " Signal Candle Low:  " + DoubleToString(m_signal_candle_low, 2) + "\n" +
              " Signal Time:        " + TimeToString(m_signal_candle_time, TIME_DATE|TIME_MINUTES) + "\n";
   }

   if(IsPositionOpen()) {
      for(int i = PositionsTotal() - 1; i >= 0; i--) {
         ulong ticket = PositionGetTicket(i);
         if(ticket > 0) {
            if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber) {
               text += " Position Ticket:    " + IntegerToString(ticket) + "\n" +
                       " Entry Price:        " + DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN), 2) + "\n" +
                       " Stop Loss:          " + DoubleToString(PositionGetDouble(POSITION_SL), 2) + "\n" +
                       " Take Profit:        " + DoubleToString(PositionGetDouble(POSITION_TP), 2) + "\n" +
                       " Volume:             " + DoubleToString(PositionGetDouble(POSITION_VOLUME), 2) + "\n" +
                       " Current Profit:     $" + DoubleToString(PositionGetDouble(POSITION_PROFIT), 2) + "\n";
               break;
            }
         }
      }
   }

   text += "==================================================";
   Comment(text);
}
