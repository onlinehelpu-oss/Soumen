//+------------------------------------------------------------------+
//|                                         RedShootingStar_EA.mq5 |
//|                                  Copyright 2024, Trading Robot |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, Trading Robot"
#property link      "https://www.mql5.com"
#property version   "3.00"
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

input group "Candle Geometry (Relaxed for more signals)"
input double         InpUpperWickMin    = 40.0;            // Upper Wick Min % (Relaxed from 50)
input double         InpUpperWickMax    = 90.0;            // Upper Wick Max % (Relaxed from 80)
input double         InpBodyMin         = 1.0;             // Body Min % (Relaxed from 5)
input double         InpBodyMax         = 40.0;            // Body Max % (Relaxed from 30)
input double         InpLowerWickMax    = 35.0;            // Lower Wick Max % (Relaxed from 25)
input double         InpMinRangePct     = 0.05;            // Min Candle Range % (Relaxed from 0.15)
input bool           InpRequirePrevGreen= false;           // Require Previous Candle to be Green? (Default False)

input group "Context Filters"
input bool           InpUseDayHighFilter = false;          // Use Day High Filter? (Default False for more signals)

input group "Position Sizing"
input double         InpLots            = 0.1;             // Fixed Lot Size (if not using allocation)
input bool           InpUseAllocation   = false;           // Use Allocation instead of Fixed Lots
input double         InpAllocAmount     = 20000.0;         // Allocation Amount (in Currency)

input group "Session Times (Optional for Crypto)"
input bool           InpUseSession      = false;           // Use Session Cutoffs?
input string         InpEntryCutoff     = "22:00";         // Entry Cutoff
input string         InpExitTime        = "23:50";         // Force Exit Time

//--- GLOBALS
CTrade         m_trade;
CSymbolInfo    m_symbol;
CPositionInfo  m_position;

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

   m_lastBid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   EventSetTimer(1);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
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
//| Detect Red Shooting Star Signal                                  |
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

   // Basic Filters
   if(c >= o) return; // Must be red

   // Optional Previous Green Filter
   if(InpRequirePrevGreen)
   {
      MqlDateTime dt_curr, dt_prev;
      datetime t_curr = iTime(_Symbol, InpTimeframe, 1);
      datetime t_prev = iTime(_Symbol, InpTimeframe, 2);
      TimeToStruct(t_curr, dt_curr);
      TimeToStruct(t_prev, dt_prev);
      bool isFirstCandle = (dt_curr.day != dt_prev.day || t_prev == 0);

      if(!isFirstCandle && prev_c <= prev_o) return;
   }

   double totalRange = h - l;
   if(totalRange <= 0) return;

   if((totalRange / c) * 100.0 < InpMinRangePct) return;

   // Geometry
   double upperWickPct = ((h - o) / totalRange) * 100.0;
   double bodyPct      = ((o - c) / totalRange) * 100.0;
   double lowerWickPct = ((c - l) / totalRange) * 100.0;

   bool validGeometry = (upperWickPct >= InpUpperWickMin && upperWickPct <= InpUpperWickMax) &&
                        (bodyPct >= InpBodyMin && bodyPct <= InpBodyMax) &&
                        (lowerWickPct >= 0 && lowerWickPct <= InpLowerWickMax);

   if(!validGeometry) return;

   // Optional Day High check
   if(InpUseDayHighFilter)
   {
      datetime startOfDay = iTime(_Symbol, PERIOD_D1, 0);
      int barsToday = Bars(_Symbol, InpTimeframe, startOfDay, TimeCurrent());
      int highestBar = iHighest(_Symbol, InpTimeframe, MODE_HIGH, barsToday, 1);
      double dayHigh = iHigh(_Symbol, InpTimeframe, highestBar);

      bool isAtDayHigh = (h >= dayHigh - m_symbol.Point());
      if(!isAtDayHigh) return;
   }

   // Signal Confirmed
   m_signalBarTime = iTime(_Symbol, InpTimeframe, 1);
   m_triggerLow = l;
   m_triggerHigh = h;
   m_waitingForBreakout = true;

   PrintFormat("Signal Detected: %s at %s. Low: %.2f, High: %.2f, U:%.1f%%, B:%.1f%%, L:%.1f%%",
               _Symbol, TimeToString(m_signalBarTime), l, h, upperWickPct, bodyPct, lowerWickPct);
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
      Print("Signal expired for ", _Symbol);
      m_waitingForBreakout = false;
      return;
   }

   // Do not enter if not in the next candle yet
   if(barStartTime <= m_signalBarTime) return;

   // Check Global/Local Position limit
   if(InpGlobalOnePos)
   {
      if(AnyPositionOpen(InpMagic)) return;
   }
   else
   {
      if(PositionSelectByMagic(InpMagic)) return;
   }

   // Check Cutoff Time
   if(InpUseSession && IsTimePast(InpEntryCutoff))
   {
      m_waitingForBreakout = false;
      return;
   }

   double bid = m_symbol.Bid();
   double threshold = NormalizePrice(m_triggerLow - InpEntryBuffer);

   // Strict cross condition
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
            PrintFormat("SELL Order Executed: %s @ %.2f, SL: %.2f, TP: %.2f, Lots: %.2f", _Symbol, entryPrice, sl, tp, lots);
            m_waitingForBreakout = false;
         }
         else
         {
            PrintFormat("SELL Order Failed: %d - %s", m_trade.ResultRetcode(), m_trade.ResultRetcodeDescription());
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
         Print("Force Exit Time Reached. Closing position for ", _Symbol);
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
