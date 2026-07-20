//+------------------------------------------------------------------+
//|                                         RedShootingStar_EA.mq5 |
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

#ifndef MODE_HIGH
#define MODE_HIGH 1
#endif

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

input group "New Candle Detection Logic (Flexible Rejection)"
input double         MinUpperWickPct    = 50.0;            // Minimum upper wick %
input double         MaxBodyPct         = 50.0;            // Maximum body %
input double         MaxLowerWickPct    = 20.0;            // Maximum lower wick %

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
double         m_lastBid = 0;

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

   // Initialize EMA handle
   m_handleEMA = iMA(_Symbol, InpTimeframe, InpEMAPeriod, 0, InpEMAMethod, PRICE_CLOSE);
   if(m_handleEMA == INVALID_HANDLE)
   {
      Print("Failed to create EMA handle");
      return(INIT_FAILED);
   }

   m_lastBid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

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

   m_lastBid = m_symbol.Bid();
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
   if(currentTime == m_lastBarTime) return;

   // A bar just closed. Check the previous bar (index 1)
   m_lastBarTime = currentTime;

   double o = iOpen(_Symbol, InpTimeframe, 1);
   double h = iHigh(_Symbol, InpTimeframe, 1);
   double l = iLow(_Symbol, InpTimeframe, 1);
   double c = iClose(_Symbol, InpTimeframe, 1);

   double prev_o = iOpen(_Symbol, InpTimeframe, 2);
   double prev_c = iClose(_Symbol, InpTimeframe, 2);

   // Rule: Signal Candle Red (Optional but default True)
   if(InpRequireRedSignal && c >= o) return;

   // Rule: Previous Candle Green (Optional but default True)
   if(InpRequirePrevGreen)
   {
      if(prev_o == 0) return;
      if(prev_c <= prev_o) return;
   }

   // Rule: Ignore Tiny Candles
   double totalRange = h - l;
   if(totalRange <= 0) return;
   if(c > 0 && (totalRange / c) * 100.0 < InpMinRangePct) return;

   // Rule: Geometry Check (Long Upper Wick, Small Body, Small Lower Wick)
   double upperWickPct = ((h - MathMax(o, c)) / totalRange) * 100.0;
   double bodyPct      = (MathAbs(o - c) / totalRange) * 100.0;
   double lowerWickPct = ((MathMin(o, c) - l) / totalRange) * 100.0;

   bool validGeometry = (upperWickPct >= InpUpperWickMin && upperWickPct <= InpUpperWickMax) &&
                        (bodyPct >= InpBodyMin && bodyPct <= InpBodyMax) &&
                        (lowerWickPct >= 0 && lowerWickPct <= InpLowerWickMax);

   bool IsRejection = (upperWickPct >= MinUpperWickPct) &&
                      (bodyPct      <= MaxBodyPct) &&
                      (lowerWickPct <= MaxLowerWickPct);

   if(!validGeometry && !IsRejection) return;

   // Rule: EMA Filter (High > EMA and Close < EMA)
   if(InpUseEMAFilter)
   {
      double ema[1];
      if(CopyBuffer(m_handleEMA, 0, 1, 1, ema) <= 0) return;

      // Fine-tuned condition: Rejection MUST cross the EMA (High above EMA and Close below EMA)
      if(!(h > ema[0] && c < ema[0])) return;
   }

   // Rule: Day High filter (Optional)
   if(InpUseDayHighFilter)
   {
      datetime startOfDay = iTime(_Symbol, PERIOD_D1, 0);
      int barsToday = Bars(_Symbol, InpTimeframe, startOfDay, TimeCurrent());
      int highestBar = iHighest(_Symbol, InpTimeframe, MODE_HIGH, barsToday, 1);
      double dayHigh = iHigh(_Symbol, InpTimeframe, highestBar);

      if(h < dayHigh - m_symbol.Point()) return;
   }

   // Signal Confirmed
   m_signalBarTime = iTime(_Symbol, InpTimeframe, 1);
   m_triggerLow = l;
   m_triggerHigh = h;
   m_waitingForBreakout = true;

   PrintFormat("Fine-tuned Signal Detected: %s at %s. High: %.2f, EMA: %.2f, Close: %.2f",
               _Symbol, TimeToString(m_signalBarTime), h, (InpUseEMAFilter ? iMA_EMA_Value(1) : 0), c);
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
      m_waitingForBreakout = false;
      return;
   }

   double bid = m_symbol.Bid();
   double threshold = NormalizePrice(m_triggerLow - InpEntryBuffer);

   // Breakout Entry
   if(m_lastBid >= threshold && bid < threshold)
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

      if(m_trade.Sell(lots, _Symbol, entryPrice, sl, tp, "RedShoot"))
      {
         if(m_trade.ResultRetcode() == 10009 || m_trade.ResultRetcode() == 10008)
         {
            PrintFormat("SELL Order Executed: %s @ %.2f, SL: %.2f, TP: %.2f", _Symbol, entryPrice, sl, tp);
            m_waitingForBreakout = false;
         }
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
         if(PositionGetInteger(POSITION_MAGIC) == magic && PositionGetString(POSITION_SYMBOL) == _Symbol)
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

//+------------------------------------------------------------------+
//| MQL5 Helper Compatibility Wrappers for MQL4-style functions      |
//+------------------------------------------------------------------+
double iOpen(string symbol, ENUM_TIMEFRAMES timeframe, int index)
{
   double val[1];
   if(CopyOpen(symbol, timeframe, index, 1, val) > 0) return val[0];
   return 0;
}

double iHigh(string symbol, ENUM_TIMEFRAMES timeframe, int index)
{
   double val[1];
   if(CopyHigh(symbol, timeframe, index, 1, val) > 0) return val[0];
   return 0;
}

double iLow(string symbol, ENUM_TIMEFRAMES timeframe, int index)
{
   double val[1];
   if(CopyLow(symbol, timeframe, index, 1, val) > 0) return val[0];
   return 0;
}

double iClose(string symbol, ENUM_TIMEFRAMES timeframe, int index)
{
   double val[1];
   if(CopyClose(symbol, timeframe, index, 1, val) > 0) return val[0];
   return 0;
}

datetime iTime(string symbol, ENUM_TIMEFRAMES timeframe, int index)
{
   datetime val[1];
   if(CopyTime(symbol, timeframe, index, 1, val) > 0) return val[0];
   return 0;
}

int iHighest(string symbol, ENUM_TIMEFRAMES timeframe, int type, int count, int start)
{
   double highs[];
   ArraySetAsSeries(highs, true);
   int copied = CopyHigh(symbol, timeframe, start, count, highs);
   if(copied <= 0) return -1;

   double maxVal = highs[0];
   int maxIdx = 0;
   for(int i = 1; i < copied; i++)
   {
      if(highs[i] > maxVal)
      {
         maxVal = highs[i];
         maxIdx = i;
      }
   }
   return start + maxIdx;
}
