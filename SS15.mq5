//+------------------------------------------------------------------+
//|                                                   SS15.mq5       |
//|                                  Copyright 2024, Trading Robot |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, Trading Robot"
#property link      "https://www.mql5.com"
#property version   "6.00"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\PositionInfo.mqh>

//--- INPUT PARAMETERS
input group "Strategy Settings"
input ENUM_TIMEFRAMES InpTimeframe      = PERIOD_M15;      // Timeframe
input double         InpRRMultiplier    = 1.0;             // Risk:Reward Multiplier
input double         InpEntryBuffer     = 0.05;            // Entry Buffer (Points)
input int            InpMagic           = 123456;          // Magic Number
input bool           InpGlobalOnePos    = true;            // One Position at a time (Global for this Magic)

input group "EMA Filter"
input bool           InpUseEMAFilter    = true;            // Use EMA Filter?
input int            InpEMAPeriod       = 15;              // EMA Period
input ENUM_MA_METHOD InpEMAMethod       = MODE_EMA;        // MA Method

input group "Candle Detection Rules"
input bool           InpRequireRedSignal = true;           // Signal Candle MUST be Red
input bool           InpRequirePrevGreen = true;           // Previous Candle MUST be Green
input double         InpMinRangePct      = 0.15;           // Min Candle Range % (Ignore tiny candles)

input group "Candle Geometry (Rejection Shape)"
input double         InpUpperWickMin    = 40.0;            // Upper Wick Min %
input double         InpUpperWickMax    = 90.0;            // Upper Wick Max %
input double         InpBodyMin         = 1.0;             // Body Min %
input double         InpBodyMax         = 40.0;            // Body Max %
input double         InpLowerWickMax    = 35.0;            // Lower Wick Max %

input group "Context Filters"
input bool           InpUseDayHighFilter = false;          // Use Day High Filter?

input group "Position Sizing"
input double         InpLots            = 0.1;             // Fixed Lot Size
input bool           InpUseAllocation   = false;           // Use Allocation instead of Fixed Lots
input double         InpAllocAmount     = 20000.0;         // Allocation Amount (in Currency)

input group "Session Times (Optional)"
input bool           InpUseSession      = false;           // Use Session Cutoffs?
input string         InpEntryCutoff     = "22:00";         // Entry Cutoff
input string         InpExitTime        = "23:50";         // Force Exit Time

//--- GLOBALS
CTrade         m_trade;
CSymbolInfo    m_symbol;
CPositionInfo  m_position;

int            m_handleEMA = INVALID_HANDLE;
datetime       m_lastBarTime;
datetime       m_signalBarTime = 0;
double         m_triggerLow    = 0;
double         m_triggerHigh   = 0;
bool           m_waitingForBreakout = false;

//--- Detect and set the broker's correct filling mode
void SetBrokerFillingMode()
{
   uint filling = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0)
   {
      m_trade.SetTypeFilling(ORDER_FILLING_FOK);
      Print("Filling mode set to ORDER_FILLING_FOK (FOK).");
   }
   else if((filling & SYMBOL_FILLING_IOC) != 0)
   {
      m_trade.SetTypeFilling(ORDER_FILLING_IOC);
      Print("Filling mode set to ORDER_FILLING_IOC (IOC).");
   }
   else
   {
      m_trade.SetTypeFilling(ORDER_FILLING_RETURN);
      Print("Filling mode set to ORDER_FILLING_RETURN.");
   }
}

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   if(!m_symbol.Name(_Symbol))
   {
      Print("Symbol error");
      return(INIT_FAILED);
   }

   m_trade.SetExpertMagicNumber(InpMagic);

   // Set correct broker filling mode
   SetBrokerFillingMode();

   // Initialize EMA handle
   m_handleEMA = iMA(_Symbol, InpTimeframe, InpEMAPeriod, 0, InpEMAMethod, PRICE_CLOSE);
   if(m_handleEMA == INVALID_HANDLE)
   {
      Print("Failed to create EMA handle");
      return(INIT_FAILED);
   }

   m_lastBarTime = 0;
   m_waitingForBreakout = false;

   EventSetTimer(1);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(m_handleEMA != INVALID_HANDLE)
      IndicatorRelease(m_handleEMA);
   EventKillTimer();
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   if(!m_symbol.RefreshRates()) return;

   CheckForSignal();
   CheckForBreakout();
}

//+------------------------------------------------------------------+
//| Timer function for session management                            |
//+------------------------------------------------------------------+
void OnTimer()
{
   if(InpUseSession)
      CheckSessionExits();
}

//+------------------------------------------------------------------+
//| Detect Rejection Candle Signal                                   |
//+------------------------------------------------------------------+
void CheckForSignal()
{
   datetime currentTime = iTime(_Symbol, InpTimeframe, 0);
   if(currentTime == 0) return; // Wait for data

   bool isNewBar = false;
   if(m_lastBarTime == 0)
   {
      m_lastBarTime = currentTime;
      isNewBar = true; // Force analysis on startup
   }
   else if(currentTime != m_lastBarTime)
   {
      m_lastBarTime = currentTime;
      isNewBar = true;
   }

   if(!isNewBar) return;

   // A bar just closed. Check the previous bar (index 1)
   double o = iOpen(_Symbol, InpTimeframe, 1);
   double h = iHigh(_Symbol, InpTimeframe, 1);
   double l = iLow(_Symbol, InpTimeframe, 1);
   double c = iClose(_Symbol, InpTimeframe, 1);

   double prev_o = iOpen(_Symbol, InpTimeframe, 2);
   double prev_c = iClose(_Symbol, InpTimeframe, 2);

   double totalRange = h - l;

   // Robust journal diagnostics
   double upperWickPct = (totalRange > 0) ? ((h - MathMax(o, c)) / totalRange) * 100.0 : 0.0;
   double bodyPct      = (totalRange > 0) ? (MathAbs(o - c) / totalRange) * 100.0 : 0.0;
   double lowerWickPct = (totalRange > 0) ? ((MathMin(o, c) - l) / totalRange) * 100.0 : 0.0;
   bool prev_green = (prev_c > prev_o);

   PrintFormat("[Diagnostic] Completed bar at %s. Range: %.2f (Min required: %.2f%% of close = %.2f), UpperWick: %.1f%% (Min: %.1f%%, Max: %.1f%%), Body: %.1f%% (Min: %.1f%%, Max: %.1f%%), LowerWick: %.1f%% (Max: %.1f%%), Red Signal Candle: %s, Previous Green: %s",
               TimeToString(iTime(_Symbol, InpTimeframe, 1)), totalRange, InpMinRangePct, (c > 0) ? (InpMinRangePct * c / 100.0) : 0.0, upperWickPct, InpUpperWickMin, InpUpperWickMax, bodyPct, InpBodyMin, InpBodyMax, lowerWickPct, InpLowerWickMax, (c < o) ? "Yes" : "No", prev_green ? "Yes" : "No");

   // Rule: Signal Candle Red (Optional but default True)
   if(InpRequireRedSignal && c >= o)
   {
      Print("[Diagnostic] -> Rejected: Signal candle is not red.");
      return;
   }

   // Rule: Previous Candle Green (Optional but default True)
   if(InpRequirePrevGreen)
   {
      if(prev_o == 0) return;
      if(prev_c <= prev_o)
      {
         Print("[Diagnostic] -> Rejected: Previous candle was not green.");
         return;
      }
   }

   // Rule: Ignore Tiny Candles
   if(totalRange <= 0) return;
   if(c > 0 && (totalRange / c) * 100.0 < InpMinRangePct)
   {
      PrintFormat("[Diagnostic] -> Rejected: Candle range %.2f is too small (below InpMinRangePct of %.2f%%).", totalRange, InpMinRangePct);
      return;
   }

   // Rule: Geometry Check (Long Upper Wick, Small Body, Small Lower Wick)
   bool validGeometry = (upperWickPct >= InpUpperWickMin && upperWickPct <= InpUpperWickMax) &&
                        (bodyPct >= InpBodyMin && bodyPct <= InpBodyMax) &&
                        (lowerWickPct >= 0 && lowerWickPct <= InpLowerWickMax);

   if(!validGeometry)
   {
      Print("[Diagnostic] -> Rejected: Candle geometry does not satisfy rejection limits.");
      return;
   }

   // Rule: EMA Filter (High > EMA and Close < EMA)
   if(InpUseEMAFilter)
   {
      double ema[1];
      if(CopyBuffer(m_handleEMA, 0, 1, 1, ema) <= 0)
      {
         Print("[Diagnostic] -> Rejected: Could not fetch EMA value.");
         return;
      }

      // Fine-tuned condition: Rejection MUST cross the EMA
      if(!(h > ema[0] && c < ema[0]))
      {
         PrintFormat("[Diagnostic] -> Rejected: Candle high (%.5f) and close (%.5f) do not cross EMA (%.5f).", h, c, ema[0]);
         return;
      }
   }

   // Rule: Day High filter (Optional)
   if(InpUseDayHighFilter)
   {
      datetime startOfDay = iTime(_Symbol, PERIOD_D1, 0);
      int barsToday = Bars(_Symbol, InpTimeframe, startOfDay, TimeCurrent());
      int highestBar = iHighest(_Symbol, InpTimeframe, MODE_HIGH, barsToday, 1);
      double dayHigh = iHigh(_Symbol, InpTimeframe, highestBar);

      if(h < dayHigh - m_symbol.Point())
      {
         PrintFormat("[Diagnostic] -> Rejected: High (%.5f) is below Day High (%.5f).", h, dayHigh);
         return;
      }
   }

   // Signal Confirmed
   m_signalBarTime = iTime(_Symbol, InpTimeframe, 1);
   m_triggerLow = l;
   m_triggerHigh = h;
   m_waitingForBreakout = true;

   PrintFormat(">>> [SIGNAL VALIDATED & ACTIVE] %s at %s. Low: %.5f, High: %.5f, EMA: %.5f, Close: %.5f. Waiting for breakout.",
               _Symbol, TimeToString(m_signalBarTime), l, h, (InpUseEMAFilter ? iMA_EMA_Value(1) : 0), c);
}

//+------------------------------------------------------------------+
//| Helper to get EMA value for logging                              |
//+------------------------------------------------------------------+
double iMA_EMA_Value(int index)
{
   double buffer[1];
   if(CopyBuffer(m_handleEMA, 0, index, 1, buffer) > 0) return buffer[0];
   return 0;
}

//+------------------------------------------------------------------+
//| Monitor for Breakout Entry                                       |
//+------------------------------------------------------------------+
void CheckForBreakout()
{
   if(!m_waitingForBreakout) return;

   datetime barStartTime = iTime(_Symbol, InpTimeframe, 0);
   if(barStartTime == 0) return;

   // Trigger valid only for the NEXT candle after signal
   if(barStartTime > m_signalBarTime + PeriodSeconds(InpTimeframe))
   {
      PrintFormat("[Signal Expired] Current bar time %s is past immediate next candle time %s.",
                  TimeToString(barStartTime), TimeToString(m_signalBarTime + PeriodSeconds(InpTimeframe)));
      m_waitingForBreakout = false;
      return;
   }

   // Do not enter if not in the next candle yet
   if(barStartTime <= m_signalBarTime) return;

   // Position Limits
   if(InpGlobalOnePos)
   {
      if(AnyPositionOpen(InpMagic)) return;
   }
   else
   {
      if(PositionSelectByMagic(InpMagic)) return;
   }

   // Session Limits
   if(InpUseSession && IsTimePast(InpEntryCutoff))
   {
      Print("[Breakout Cancelled] Entry past cutoff session time.");
      m_waitingForBreakout = false;
      return;
   }

   double bid = m_symbol.Bid();
   double threshold = NormalizePrice(m_triggerLow - InpEntryBuffer);

   // Breakout Entry: Check if bid price has broken or is below the threshold level
   if(bid <= threshold && bid > 0.0)
   {
      double entryPrice = bid;
      double sl = NormalizePrice(m_triggerHigh);
      double risk = sl - entryPrice;

      if(risk <= 0)
      {
         m_waitingForBreakout = false;
         return;
      }

      double tp = NormalizePrice(entryPrice - (InpRRMultiplier * risk));
      double lots = CalculateLots(entryPrice);

      PrintFormat("[Executing Breakout Entry] Bid: %.5f <= Threshold: %.5f. Placing Sell Order. SL: %.5f, TP: %.5f, Lots: %.2f",
                  bid, threshold, sl, tp, lots);

      if(m_trade.Sell(lots, _Symbol, entryPrice, sl, tp, "RedShoot"))
      {
         PrintFormat("SELL Order Submitted. Retcode: %d, Comment: %s", m_trade.ResultRetcode(), m_trade.ResultComment());
         m_waitingForBreakout = false;
      }
      else
      {
         PrintFormat("SELL Order Placement Failed. Retcode: %d, Comment: %s", m_trade.ResultRetcode(), m_trade.ResultComment());
      }
   }
}

//+------------------------------------------------------------------+
//| Check Session Exits                                              |
//+------------------------------------------------------------------+
void CheckSessionExits()
{
   if(IsTimePast(InpExitTime))
   {
      if(PositionSelectByMagic(InpMagic))
      {
         m_trade.PositionClose(_Symbol);
         Print("[Session Exit] Forced exit time reached. Closed position.");
      }
      m_waitingForBreakout = false;
   }
}

//+------------------------------------------------------------------+
//| Helper to check if current time >= HH:MM                         |
//+------------------------------------------------------------------+
bool IsTimePast(string timeStr)
{
   MqlDateTime dt;
   TimeCurrent(dt);
   string parts[];
   if(StringSplit(timeStr, ':', parts) != 2) return false;
   int hour = (int)StringToInteger(parts[0]);
   int min = (int)StringToInteger(parts[1]);
   if(dt.hour > hour) return true;
   if(dt.hour == hour && dt.min >= min) return true;
   return false;
}

//+------------------------------------------------------------------+
//| Calculate Lot Size                                               |
//+------------------------------------------------------------------+
double CalculateLots(double price)
{
   if(!InpUseAllocation) return InpLots;
   if(price <= 0) return InpLots;
   double qty = InpAllocAmount / price;
   double step = m_symbol.LotsStep();
   double lots = MathFloor(qty / step) * step;
   double minLot = m_symbol.LotsMin();
   double maxLot = m_symbol.LotsMax();
   if(lots < minLot) lots = minLot;
   if(lots > maxLot) lots = maxLot;
   return lots;
}

//+------------------------------------------------------------------+
//| Normalize Price to Tick Size                                     |
//+------------------------------------------------------------------+
double NormalizePrice(double price)
{
   double tickSize = m_symbol.TickSize();
   if(tickSize == 0) return price;
   return MathRound(price / tickSize) * tickSize;
}

//+------------------------------------------------------------------+
//| Select position by Magic Number and Symbol                       |
//+------------------------------------------------------------------+
bool PositionSelectByMagic(long magic)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(PositionSelectByTicket(ticket))
      {
         if(PositionGetInteger(POSITION_MAGIC) == magic && POSITION_SYMBOL == _Symbol)
            return true;
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Check if ANY position is open with this Magic Number             |
//+------------------------------------------------------------------+
bool AnyPositionOpen(long magic)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(PositionSelectByTicket(ticket))
      {
         if(PositionGetInteger(POSITION_MAGIC) == magic)
            return true;
      }
   }
   return false;
}
