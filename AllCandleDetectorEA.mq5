//+------------------------------------------------------------------+
//|                                         AllCandleDetectorEA.mq5 |
//|                                  Copyright 2024, Trading Robot |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, Trading Robot"
#property link      "https://www.mql5.com"
#property version   "1.00"
#property description "MT5 Candle Detector Expert Advisor for Patterns C2-C7 and General Rejections"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\PositionInfo.mqh>

//--- INPUT PARAMETERS
input group "=== Detection Settings ==="
input ENUM_TIMEFRAMES InpTimeframe         = PERIOD_CURRENT;  // Timeframe
input double          InpMinCandlePoints   = 0.0;             // Min Candle Range in Points (0 to disable)
input bool            InpRequirePrevGreen  = true;            // Previous Candle (shift=2) must be GREEN
input bool            InpUseEMAFilter      = false;           // Use EMA Filter? (High above EMA, Close below EMA)
input int             InpEMAPeriod         = 21;              // EMA Period Close-basis
input ENUM_MA_METHOD  InpEMAMethod         = MODE_EMA;        // EMA MA Method

input group "=== Pattern Detection Toggles ==="
input bool            InpDetectC2          = true;            // Detect Pattern C2 (UW ~75%, Body ~24%, LW ~0.6%)
input bool            InpDetectC3          = true;            // Detect Pattern C3 (UW ~28%, Body ~67%, LW ~4%)
input bool            InpDetectC4          = true;            // Detect Pattern C4 (UW ~41%, Body ~45%, LW ~13%)
input bool            InpDetectC5          = true;            // Detect Pattern C5 (UW ~34%, Body ~60%, LW ~5%)
input bool            InpDetectC6          = true;            // Detect Pattern C6 (UW ~49%, Body ~46%, LW ~4.5%)
input bool            InpDetectC7          = true;            // Detect Pattern C7 (UW ~77%, Body ~14%, LW ~8%)
input bool            InpDetectGeneral     = true;            // Fallback: Detect General Rejection Shapes

input group "=== General Rejection Bounds ==="
input double          InpUpperWickMin      = 25.0;            // Upper Wick Min % of total range
input double          InpUpperWickMax      = 90.0;            // Upper Wick Max % of total range
input double          InpBodyMin           = 1.0;             // Body Min % of total range
input double          InpBodyMax           = 75.0;            // Body Max % of total range
input double          InpLowerWickMax      = 20.0;            // Lower Wick Max % of total range

input group "=== Chart Visual Settings ==="
input bool            InpDrawArrows        = true;            // Draw visual arrow above signal candle
input bool            InpDrawLabels        = true;            // Draw text label with pattern name above arrow
input color           InpArrowColor        = clrRed;          // Arrow Color
input color           InpTextColor         = clrLightSalmon;  // Text Label Color

input group "=== Position Sizing & Trading ==="
input bool            InpEnableTrading     = false;           // Enable auto-trading? (Set true to place orders)
input double          InpLotSize           = 0.1;             // Fixed Lot Size
input bool            InpUseRiskPercent    = false;           // Size lots based on account risk %
input double          InpRiskPercent       = 1.0;             // Risk % of Balance (used if UseRiskPercent = true)
input int             InpEntryBuffer       = 0;               // Entry Buffer (Points below Signal Low to trigger)
input int             InpSLBuffer          = 0;               // Stop Loss Buffer (Points above Signal High to set SL)
input double          InpRiskReward        = 2.0;             // Risk:Reward ratio (TakeProfit = Entry - R:R * SL_distance)
input ulong           InpMagicNumber       = 882000;          // Magic Number
input int             InpSlippage          = 10;              // Slippage (Points)

input group "=== Trailing Stop Settings ==="
input bool            InpUseTrailing       = false;           // Enable Trailing Stop
input int             InpTrailingStart     = 200;             // Trailing Start (Points)
input int             InpTrailingStep      = 50;              // Trailing Step (Points)

//--- GLOBALS
CTrade         m_trade;
CSymbolInfo    m_symbol;
CPositionInfo  m_position;

int            m_ema_handle = INVALID_HANDLE;
datetime       m_last_bar_time = 0;
bool           m_signal_active = false;
double         m_signal_high = 0;
double         m_signal_low = 0;
datetime       m_signal_time = 0;
string         m_signal_pattern = "";

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   if(!m_symbol.Name(_Symbol))
   {
      Print("[Init] Symbol error!");
      return(INIT_FAILED);
   }

   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_trade.SetDeviationInPoints(InpSlippage);

   // Initialize EMA handle if filter is active
   if(InpUseEMAFilter)
   {
      m_ema_handle = iMA(_Symbol, InpTimeframe, InpEMAPeriod, 0, InpEMAMethod, PRICE_CLOSE);
      if(m_ema_handle == INVALID_HANDLE)
      {
         Print("[Init] Failed to create EMA handle!");
         return(INIT_FAILED);
      }
   }

   m_last_bar_time = 0;
   m_signal_active = false;
   m_signal_high = 0;
   m_signal_low = 0;
   m_signal_time = 0;
   m_signal_pattern = "";

   PrintFormat("[Init] All Candle Detector EA successfully loaded on %s on timeframe %s. Trading enabled: %s",
               _Symbol, EnumToString(InpTimeframe), string(InpEnableTrading));

   EventSetTimer(1);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(m_ema_handle != INVALID_HANDLE)
   {
      IndicatorRelease(m_ema_handle);
   }
   EventKillTimer();
   Print("[Deinit] EA unloaded.");
}

//--- Helper functions to get candle data safely in MQL5
double GetOpen(int shift)
{
   double val[1];
   if(CopyOpen(_Symbol, InpTimeframe, shift, 1, val) > 0) return val[0];
   return 0;
}

double GetHigh(int shift)
{
   double val[1];
   if(CopyHigh(_Symbol, InpTimeframe, shift, 1, val) > 0) return val[0];
   return 0;
}

double GetLow(int shift)
{
   double val[1];
   if(CopyLow(_Symbol, InpTimeframe, shift, 1, val) > 0) return val[0];
   return 0;
}

double GetClose(int shift)
{
   double val[1];
   if(CopyClose(_Symbol, InpTimeframe, shift, 1, val) > 0) return val[0];
   return 0;
}

datetime GetTime(int shift)
{
   datetime val[1];
   if(CopyTime(_Symbol, InpTimeframe, shift, 1, val) > 0) return val[0];
   return 0;
}

//+------------------------------------------------------------------+
//| Set Trade Filling Mode                                           |
//+------------------------------------------------------------------+
bool SetTradeFillingMode()
{
   uint filling = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0)
   {
      m_trade.SetTypeFilling(ORDER_FILLING_FOK);
      return true;
   }
   else if((filling & SYMBOL_FILLING_IOC) != 0)
   {
      m_trade.SetTypeFilling(ORDER_FILLING_IOC);
      return true;
   }

   ENUM_SYMBOL_TRADE_EXECUTION exec = (ENUM_SYMBOL_TRADE_EXECUTION)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_EXEMODE);
   if(exec == SYMBOL_TRADE_EXECUTION_MARKET)
   {
      m_trade.SetTypeFilling(ORDER_FILLING_FOK);
   }
   else
   {
      m_trade.SetTypeFilling(ORDER_FILLING_RETURN);
   }
   return true;
}

//+------------------------------------------------------------------+
//| Check if position is open                                        |
//+------------------------------------------------------------------+
bool IsPositionOpen()
{
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol)
         {
            if(PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
            {
               return true;
            }
         }
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Calculate Lot Size                                               |
//+------------------------------------------------------------------+
double CalculateLotSize(double sl_distance_points)
{
   if(!InpUseRiskPercent || InpRiskPercent <= 0)
      return InpLotSize;

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double risk_amount = balance * (InpRiskPercent / 100.0);
   double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double point = m_symbol.Point();

   if(tick_value <= 0 || tick_size <= 0 || point <= 0 || sl_distance_points <= 0)
      return InpLotSize;

   double point_value = (tick_value / tick_size) * point;
   double calculated_lot = risk_amount / (sl_distance_points * point_value);

   double lot_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   if(lot_step <= 0) lot_step = 0.01;

   calculated_lot = MathRound(calculated_lot / lot_step) * lot_step;

   if(calculated_lot < min_lot) calculated_lot = min_lot;
   if(calculated_lot > max_lot) calculated_lot = max_lot;

   return calculated_lot;
}

//+------------------------------------------------------------------+
//| Get Specific Candle Pattern Name                                 |
//+------------------------------------------------------------------+
string GetCandlePatternName(double O, double H, double L, double C, double &uw_pct, double &body_pct, double &lw_pct)
{
   double range = H - L;
   if(range <= 0) return "None";

   double body       = MathAbs(O - C);
   double upperWick  = H - MathMax(O, C);
   double lowerWick  = MathMin(O, C) - L;

   uw_pct   = (upperWick / range) * 100.0;
   body_pct = (body      / range) * 100.0;
   lw_pct   = (lowerWick / range) * 100.0;

   // All example patterns (C2-C7) are red (bearish) candles
   if(C >= O) return "None";

   // Pattern C2
   if(InpDetectC2 &&
      uw_pct >= 70.0 && uw_pct <= 80.0 &&
      body_pct >= 18.0 && body_pct <= 30.0 &&
      lw_pct >= 0.0 && lw_pct <= 3.0)
   {
      return "C2";
   }

   // Pattern C3
   if(InpDetectC3 &&
      uw_pct >= 23.0 && uw_pct <= 33.0 &&
      body_pct >= 62.0 && body_pct <= 72.0 &&
      lw_pct >= 0.0 && lw_pct <= 7.0)
   {
      return "C3";
   }

   // Pattern C4
   if(InpDetectC4 &&
      uw_pct >= 36.0 && uw_pct <= 46.0 &&
      body_pct >= 40.0 && body_pct <= 51.0 &&
      lw_pct >= 9.0 && lw_pct <= 18.0)
   {
      return "C4";
   }

   // Pattern C5
   if(InpDetectC5 &&
      uw_pct >= 30.0 && uw_pct <= 39.0 &&
      body_pct >= 55.0 && body_pct <= 65.0 &&
      lw_pct >= 2.0 && lw_pct <= 9.0)
   {
      return "C5";
   }

   // Pattern C6
   if(InpDetectC6 &&
      uw_pct >= 44.0 && uw_pct <= 54.0 &&
      body_pct >= 41.0 && body_pct <= 52.0 &&
      lw_pct >= 1.0 && lw_pct <= 8.0)
   {
      return "C6";
   }

   // Pattern C7
   if(InpDetectC7 &&
      uw_pct >= 73.0 && uw_pct <= 83.0 &&
      body_pct >= 10.0 && body_pct <= 18.0 &&
      lw_pct >= 4.0 && lw_pct <= 12.0)
   {
      return "C7";
   }

   return "None";
}

//+------------------------------------------------------------------+
//| Check For Signal on Closed Bar                                   |
//+------------------------------------------------------------------+
void CheckForSignal()
{
   datetime currentTime = GetTime(0);
   if(currentTime == 0) return;
   if(currentTime == m_last_bar_time) return;

   // A new bar opened. We scan the just-closed bar at index 1
   m_last_bar_time = currentTime;

   double o = GetOpen(1);
   double h = GetHigh(1);
   double l = GetLow(1);
   double c = GetClose(1);
   datetime t = GetTime(1);

   double range = h - l;
   if(range <= 0) return;

   // Filter tiny candles by points
   if(InpMinCandlePoints > 0 && (range / m_symbol.Point()) < InpMinCandlePoints)
   {
      return;
   }

   // Previous green candle rule
   if(InpRequirePrevGreen)
   {
      double prev_o = GetOpen(2);
      double prev_c = GetClose(2);
      if(prev_c <= prev_o) return;
   }

   // Calculate percentages and find if matches a specific pattern
   double uw_pct = 0, body_pct = 0, lw_pct = 0;
   string pattern = GetCandlePatternName(o, h, l, c, uw_pct, body_pct, lw_pct);

   bool isMatch = false;
   if(pattern != "None")
   {
      isMatch = true;
   }
   else if(InpDetectGeneral)
   {
      // Fallback to General Rejection Rule
      if(c < o && // Must be bearish red
         uw_pct >= InpUpperWickMin && uw_pct <= InpUpperWickMax &&
         body_pct >= InpBodyMin && body_pct <= InpBodyMax &&
         lw_pct >= 0.0 && lw_pct <= InpLowerWickMax)
      {
         pattern = "Rejection";
         isMatch = true;
      }
   }

   // Log detailed candle profile
   PrintFormat("[Bar Scan] Time: %s | O: %.5f H: %.5f L: %.5f C: %.5f | UW: %.1f%%, Body: %.1f%%, LW: %.1f%% | Pattern: %s",
               TimeToString(t), o, h, l, c, uw_pct, body_pct, lw_pct, pattern);

   if(!isMatch) return;

   // EMA Filter (High above EMA, Close below EMA)
   if(InpUseEMAFilter && m_ema_handle != INVALID_HANDLE)
   {
      double emaVal[1];
      if(CopyBuffer(m_ema_handle, 0, 1, 1, emaVal) > 0)
      {
         if(!(h > emaVal[0] && c < emaVal[0]))
         {
            PrintFormat("   Signal %s at %s filtered by EMA (EMA=%.5f, High=%.5f, Close=%.5f)",
                        pattern, TimeToString(t), emaVal[0], h, c);
            return;
         }
      }
      else
      {
         return; // Couldn't copy buffer
      }
   }

   // Signal Confirmed!
   m_signal_active = true;
   m_signal_high = h;
   m_signal_low = l;
   m_signal_time = t;
   m_signal_pattern = pattern;

   PrintFormat("[SIGNAL DETECTED] %s pattern found on %s at %s! High: %.5f, Low: %.5f",
               pattern, _Symbol, TimeToString(t), h, l);

   // Draw Chart Objects
   if(InpDrawArrows)
   {
      string arrowName = "CandleArrow_" + TimeToString(t);
      ObjectDelete(0, arrowName); // clean up if exists
      if(ObjectCreate(0, arrowName, OBJ_ARROW_DOWN, 0, t, h))
      {
         ObjectSetInteger(0, arrowName, OBJPROP_COLOR, InpArrowColor);
         ObjectSetInteger(0, arrowName, OBJPROP_WIDTH, 2);
         ObjectSetInteger(0, arrowName, OBJPROP_ANCHOR, ANCHOR_BOTTOM);
         ObjectSetString(0, arrowName, OBJPROP_TOOLTIP, pattern + " signal arrow");
      }
   }

   if(InpDrawLabels)
   {
      string labelName = "CandleLabel_" + TimeToString(t);
      ObjectDelete(0, labelName); // clean up if exists
      double offset = range * 0.15;
      if(offset <= 0) offset = 10 * m_symbol.Point();

      if(ObjectCreate(0, labelName, OBJ_TEXT, 0, t, h + offset))
      {
         ObjectSetString(0, labelName, OBJPROP_TEXT, pattern);
         ObjectSetInteger(0, labelName, OBJPROP_COLOR, InpTextColor);
         ObjectSetInteger(0, labelName, OBJPROP_FONTSIZE, 10);
         ObjectSetInteger(0, labelName, OBJPROP_ANCHOR, ANCHOR_BOTTOM);
         ObjectSetString(0, labelName, OBJPROP_TOOLTIP, pattern + " signal text");
      }
   }
}

//+------------------------------------------------------------------+
//| Check For Breakout Entry                                         |
//+------------------------------------------------------------------+
void CheckForBreakout()
{
   if(!InpEnableTrading) return;
   if(!m_signal_active) return;

   datetime current_bar_start = GetTime(0);
   if(current_bar_start == 0) return;

   // The breakout trigger is ONLY valid for the next immediate candle
   if(current_bar_start > m_signal_time + PeriodSeconds(InpTimeframe))
   {
      PrintFormat("[Breakout Timeout] Signal candle from %s has expired. Discarding signal.",
                  TimeToString(m_signal_time));
      m_signal_active = false;
      return;
   }

   // If already in a position, don't enter again
   if(IsPositionOpen())
   {
      m_signal_active = false;
      return;
   }

   double bid = m_symbol.Bid();
   double triggerPrice = m_signal_low - InpEntryBuffer * m_symbol.Point();

   // Breakout condition: Bid price falls below the signal low (minus buffer)
   if(bid <= triggerPrice)
   {
      PrintFormat("[Breakout Triggered] Bid %.5f <= Low %.5f. Executing Sell order.", bid, triggerPrice);

      // We turn off signal active immediately to act as an Anti-Race Lock
      m_signal_active = false;

      SetTradeFillingMode();

      double stopLossPoints = (m_signal_high + InpSLBuffer * m_symbol.Point()) - triggerPrice;
      if(stopLossPoints <= 0) stopLossPoints = 100 * m_symbol.Point();

      double lot = CalculateLotSize(stopLossPoints / m_symbol.Point());

      double sl = m_signal_high + InpSLBuffer * m_symbol.Point();
      double tp = triggerPrice - (stopLossPoints * InpRiskReward);

      // Normalize SL and TP
      sl = NormalizeDouble(sl, _Digits);
      tp = NormalizeDouble(tp, _Digits);

      if(m_trade.Sell(lot, _Symbol, bid, sl, tp, "Breakout " + m_signal_pattern))
      {
         if(m_trade.ResultRetcode() == 10009 || m_trade.ResultRetcode() == 10008)
         {
            PrintFormat("[TRADE SUCCESS] Short position entered successfully. Ticket: %I64u, Lot: %.2f, SL: %.5f, TP: %.5f",
                        m_trade.ResultDeal(), lot, sl, tp);
         }
         else
         {
            PrintFormat("[TRADE ERROR] Order submission failed. Return Code: %u", m_trade.ResultRetcode());
         }
      }
      else
      {
         PrintFormat("[TRADE FAILED] Trade execution failed entirely. Error code: %d", GetLastError());
      }
   }
}

//+------------------------------------------------------------------+
//| Manage Trailing Stop for Open Short Positions                    |
//+------------------------------------------------------------------+
void ManageTrailingStop()
{
   if(!InpEnableTrading || !InpUseTrailing) return;

   double ask = m_symbol.Ask();
   double point = m_symbol.Point();

   int total = PositionsTotal();
   for(int i = total - 1; i >= 0; i--)
   {
      if(m_position.SelectByIndex(i))
      {
         if(m_position.Symbol() == _Symbol && m_position.Magic() == InpMagicNumber)
         {
            if(m_position.PositionType() == POSITION_TYPE_SELL)
            {
               double openPrice = m_position.PriceOpen();
               double currentSL = m_position.StopLoss();

               // Distance from entry in points
               double profitPoints = (openPrice - ask) / point;

               if(profitPoints >= InpTrailingStart)
               {
                  double newSL = ask + InpTrailingStep * point;
                  newSL = NormalizeDouble(newSL, _Digits);

                  // Only trail SL downwards (tighter stop for short position)
                  if(currentSL == 0 || newSL < currentSL)
                  {
                     if(m_trade.PositionModify(m_position.Ticket(), newSL, m_position.TakeProfit()))
                     {
                        PrintFormat("[Trailing Stop] Position %I64u Stop Loss moved from %.5f to %.5f (Ask: %.5f)",
                                    m_position.Ticket(), currentSL, newSL, ask);
                     }
                  }
               }
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   if(!m_symbol.RefreshRates()) return;

   CheckForSignal();
   CheckForBreakout();
   ManageTrailingStop();
}

//+------------------------------------------------------------------+
//| Timer function for responsive position trailing                 |
//+------------------------------------------------------------------+
void OnTimer()
{
   // No high-frequency logic needed on timer, but keeps things alive and responsive
}
