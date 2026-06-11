//+------------------------------------------------------------------+
//|                                     EMA_StrictBodyCross.mq5      |
//|                                  Copyright 2024, MetaQuotes Ltd. |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, MetaQuotes Ltd."
#property link      "https://www.mql5.com"
#property version   "1.20"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>

//--- INPUT PARAMETERS
input group "Market Scanning"
input double   InpMinChangePct   = 5.0;      // Minimum 24h Change %
input int      InpScanInterval   = 300;      // Scan Interval (Seconds)

input group "Strategy Indicators"
input int      InpEmaFast        = 9;        // Fast EMA Period
input int      InpEmaSlow        = 15;       // Slow EMA Period
input int      InpEmaExit        = 50;       // Exit EMA Period
input int      InpAtrPeriod      = 14;       // ATR Period
input ENUM_TIMEFRAMES InpTimeframe = PERIOD_M30; // Trading Timeframe

input group "Strategy Constraints"
input double   InpMinRangePct    = 0.0;      // Min Body Size %
input double   InpMaxRangePct    = 2.0;      // Max Body Size %
input int      InpSwingLookback  = 50;       // Lookback for Swing High (TP)
input int      InpBufferPoints   = 50;       // Breakout Buffer (Points)

input group "Trade Settings"
input double   InpLotSize        = 0.1;      // Fixed Lot Size
input long     InpMagic          = 888888;   // Magic Number
input int      InpSlippage       = 10;       // Max Slippage (Points)

//--- GLOBAL VARIABLES
CTrade         trade;
string         watchlist[];
int            watchlist_count = 0;

struct SignalData {
   double trigger_high;
   double stop_loss;
   double take_profit;
   datetime signal_time;
};

struct ExitPendingData {
   double trigger_low;
   datetime signal_time;
};

// State management for multiple symbols
SignalData     pending_entries[];
string         pending_entry_symbols[];
ExitPendingData pending_exits[];
ulong          pending_exit_tickets[];

// Indicator Handle Cache to improve performance
struct HandleCache {
   string symbol;
   int h_fast;
   int h_slow;
   int h_exit;
   int h_atr;
};
HandleCache handles[];

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippage);

   EventSetTimer(InpScanInterval);
   ScanSymbols();
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   for(int i=0; i<ArraySize(handles); i++)
   {
      IndicatorRelease(handles[i].h_fast);
      IndicatorRelease(handles[i].h_slow);
      IndicatorRelease(handles[i].h_exit);
      IndicatorRelease(handles[i].h_atr);
   }
}

//+------------------------------------------------------------------+
//| Expert timer function                                            |
//+------------------------------------------------------------------+
void OnTimer()
{
   ScanSymbols();
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   for(int i=0; i<watchlist_count; i++)
   {
      string symbol = watchlist[i];
      if(!SymbolInfoInteger(symbol, SYMBOL_SELECT)) continue;

      ManageExits(symbol);

      if(!PositionSelectBySymbol(symbol))
      {
         CheckEntrySignal(symbol);
      }
   }
}

//+------------------------------------------------------------------+
//| Get Indicator handles from cache                                 |
//+------------------------------------------------------------------+
int GetHandle(string symbol, string type)
{
   int idx = -1;
   for(int i=0; i<ArraySize(handles); i++)
   {
      if(handles[i].symbol == symbol) { idx = i; break; }
   }

   if(idx == -1)
   {
      idx = ArraySize(handles);
      ArrayResize(handles, idx + 1);
      handles[idx].symbol = symbol;
      handles[idx].h_fast = iMA(symbol, InpTimeframe, InpEmaFast, 0, MODE_EMA, PRICE_CLOSE);
      handles[idx].h_slow = iMA(symbol, InpTimeframe, InpEmaSlow, 0, MODE_EMA, PRICE_CLOSE);
      handles[idx].h_exit = iMA(symbol, InpTimeframe, InpEmaExit, 0, MODE_EMA, PRICE_CLOSE);
      handles[idx].h_atr  = iATR(symbol, InpTimeframe, InpAtrPeriod);
   }

   if(type == "fast") return handles[idx].h_fast;
   if(type == "slow") return handles[idx].h_slow;
   if(type == "exit") return handles[idx].h_exit;
   if(type == "atr")  return handles[idx].h_atr;
   return INVALID_HANDLE;
}

//+------------------------------------------------------------------+
//| Scan for high-volatility symbols                                 |
//+------------------------------------------------------------------+
void ScanSymbols()
{
   watchlist_count = 0;
   ArrayFree(watchlist);

   int total = SymbolsTotal(false);
   for(int i=0; i<total; i++)
   {
      string name = SymbolName(i, false);
      MqlRates rates[];
      ArraySetAsSeries(rates, true);
      if(CopyRates(name, PERIOD_D1, 0, 2, rates) < 2) continue;

      double change = MathAbs((rates[0].close - rates[1].close) / rates[1].close) * 100.0;
      if(change >= InpMinChangePct)
      {
         if(SymbolSelect(name, true))
         {
            ArrayResize(watchlist, watchlist_count + 1);
            watchlist[watchlist_count] = name;
            watchlist_count++;
         }
      }
   }
   Print("Scanner: Found ", watchlist_count, " symbols meeting criteria.");
}

//+------------------------------------------------------------------+
//| Check for "Strict Body Cross" Signal                             |
//+------------------------------------------------------------------+
void CheckEntrySignal(string symbol)
{
   // 1. Pending Breakout Logic
   int pending_idx = -1;
   for(int i=0; i<ArraySize(pending_entry_symbols); i++)
   {
      if(pending_entry_symbols[i] == symbol) { pending_idx = i; break; }
   }

   if(pending_idx != -1)
   {
      double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
      if(ask > pending_entries[pending_idx].trigger_high)
      {
         if(trade.Buy(InpLotSize, symbol, ask, pending_entries[pending_idx].stop_loss, pending_entries[pending_idx].take_profit, "EMA Strict Body Cross"))
         {
            RemovePendingEntry(pending_idx);
         }
      }
      else if(iTime(symbol, InpTimeframe, 0) > pending_entries[pending_idx].signal_time + PeriodSeconds(InpTimeframe))
      {
         RemovePendingEntry(pending_idx);
      }
      return;
   }

   // 2. New Signal Detection
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(symbol, InpTimeframe, 0, 100, rates) < 100) return;

   double ema_fast[], ema_slow[];
   CopyBuffer(GetHandle(symbol, "fast"), 0, 0, 3, ema_fast);
   CopyBuffer(GetHandle(symbol, "slow"), 0, 0, 3, ema_slow);
   ArraySetAsSeries(ema_fast, true);
   ArraySetAsSeries(ema_slow, true);

   double open=rates[1].open, close=rates[1].close, high=rates[1].high, low=rates[1].low;

   bool trend_ok = (ema_fast[1] > ema_slow[1]) && (ema_slow[1] > ema_slow[2]);
   bool dip_ok = (low <= ema_slow[1]);
   double buf = InpBufferPoints * SymbolInfoDouble(symbol, SYMBOL_POINT);
   bool breakout_ok = (open < ema_fast[1]) && (open < ema_slow[1]) && (close > ema_fast[1] + buf) && (close > ema_slow[1] + buf);
   double body_pct = (MathAbs(close-open)/open)*100.0;
   bool confirm_ok = (close > open) && (high > rates[2].high) && (body_pct >= InpMinRangePct && body_pct <= InpMaxRangePct);

   if(trend_ok && dip_ok && breakout_ok && confirm_ok)
   {
      double s_high = 0;
      for(int i=2; i<=InpSwingLookback+1; i++) { if(rates[i].high > s_high) s_high = rates[i].high; }

      if(s_high > high)
      {
         SignalData sig; sig.trigger_high=high; sig.stop_loss=low; sig.take_profit=s_high; sig.signal_time=rates[1].time;
         AddPendingEntry(symbol, sig);
         Print(symbol, ": Signal detected. TP: ", s_high);
      }
   }
}

//+------------------------------------------------------------------+
//| Manage Positions                                                 |
//+------------------------------------------------------------------+
void ManageExits(string symbol)
{
   if(!PositionSelectBySymbol(symbol)) return;

   ulong ticket = PositionGetInteger(POSITION_TICKET);
   double open_p=PositionGetDouble(POSITION_PRICE_OPEN), sl=PositionGetDouble(POSITION_SL), tp=PositionGetDouble(POSITION_TP), bid=SymbolInfoDouble(symbol, SYMBOL_BID);

   double atr_buf[];
   CopyBuffer(GetHandle(symbol, "atr"), 0, 0, 1, atr_buf);
   if(ArraySize(atr_buf)>0 && (bid - open_p) >= atr_buf[0] && sl < open_p)
   {
      trade.PositionModify(ticket, open_p, tp);
      Print(symbol, ": BE move.");
   }

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(symbol, InpTimeframe, 0, 2, rates) < 2) return;

   double ema_ex[];
   CopyBuffer(GetHandle(symbol, "exit"), 0, 0, 1, ema_ex);
   if(ArraySize(ema_ex)>0 && rates[1].close < rates[1].open && rates[1].open < ema_ex[0] && rates[1].high >= ema_ex[0] && rates[1].close < ema_ex[0])
   {
      bool found = false;
      for(int i=0; i<ArraySize(pending_exit_tickets); i++) { if(pending_exit_tickets[i] == ticket) { found=true; break; } }
      if(!found) AddPendingExit(ticket, rates[1].low, rates[1].time);
   }

   int ex_idx = -1;
   for(int i=0; i<ArraySize(pending_exit_tickets); i++) { if(pending_exit_tickets[i] == ticket) { ex_idx = i; break; } }
   if(ex_idx != -1)
   {
      if(bid < pending_exits[ex_idx].trigger_low)
      {
         trade.PositionClose(ticket);
         RemovePendingExit(ex_idx);
      }
      else if(iTime(symbol, InpTimeframe, 0) > pending_exits[ex_idx].signal_time + PeriodSeconds(InpTimeframe))
      {
         RemovePendingExit(ex_idx);
      }
   }
}

//--- Helpers
void AddPendingEntry(string sym, SignalData &data) {
   int sz = ArraySize(pending_entry_symbols);
   ArrayResize(pending_entry_symbols, sz + 1); ArrayResize(pending_entries, sz + 1);
   pending_entry_symbols[sz] = sym; pending_entries[sz] = data;
}
void RemovePendingEntry(int idx) {
   int sz = ArraySize(pending_entry_symbols);
   for(int i=idx; i<sz-1; i++) { pending_entry_symbols[i] = pending_entry_symbols[i+1]; pending_entries[i] = pending_entries[i+1]; }
   ArrayResize(pending_entry_symbols, sz-1); ArrayResize(pending_entries, sz-1);
}
void AddPendingExit(ulong ticket, double trigger, datetime t) {
   int sz = ArraySize(pending_exit_tickets);
   ArrayResize(pending_exit_tickets, sz + 1); ArrayResize(pending_exits, sz + 1);
   pending_exit_tickets[sz] = ticket; pending_exits[sz].trigger_low = trigger; pending_exits[sz].signal_time = t;
}
void RemovePendingExit(int idx) {
   int sz = ArraySize(pending_exit_tickets);
   for(int i=idx; i<sz-1; i++) { pending_exit_tickets[i] = pending_exit_tickets[i+1]; pending_exits[i] = pending_exits[i+1]; }
   ArrayResize(pending_exit_tickets, sz-1); ArrayResize(pending_exits, sz-1);
}
