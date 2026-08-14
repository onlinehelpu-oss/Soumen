//+------------------------------------------------------------------+
//|                                         MainEMABreakdown_EA.mq5  |
//|                                                            Jules |
//|                                                                  |
//| An MT5 Expert Advisor executing a Main EMA Breakdown Sell strategy |
//| strictly following user specifications.                          |
//+------------------------------------------------------------------+
#property copyright "Jules"
#property link      ""
#property version   "1.00"

#include <Trade\Trade.mqh>

//--- Define Custom Timeframe Selection Enum
enum ENUM_CUSTOM_TIMEFRAME
{
   TF_1_MIN  = 1,     // 1 Minute
   TF_3_MIN  = 3,     // 3 Minutes
   TF_5_MIN  = 5,     // 5 Minutes
   TF_15_MIN = 15,    // 15 Minutes
   TF_30_MIN = 30,    // 30 Minutes
   TF_1_HOUR = 60,    // 1 Hour
   TF_1_DAY  = 1440   // 1 Day
};

//--- Input Parameters
input group "---- Timeframe & EMA Settings ----"
input ENUM_CUSTOM_TIMEFRAME InpTimeframe = TF_1_HOUR;        // Candle Timeframe
input int                  InpEMAPeriod = 34;                // Main EMA Period (e.g. 15, 21, 34, 50, 200)

input group "---- Execution Settings ----"
input double               InpLotSize = 0.01;                // Trade Lot Size
input int                  InpCoolDownSeconds = 300;         // Cooldown Period (Seconds) after trade close

input group "---- Take Profit: Risk-to-Reward ----"
input bool                 InpUseRRTarget = true;            // Risk-to-Reward basis: ON/OFF
input double               InpRiskRewardRatio = 2.0;         // Risk-to-Reward Ratio (e.g. 1.0, 2.0, etc.)

input group "---- Take Profit: Dollar Basis ----"
input bool                 InpUseDollarTarget = false;       // Profit in Dollar basis: ON/OFF
input double               InpDollarTarget = 2.0;            // Dollar basis profit target (e.g. 2.0, 5.0, etc.)

//--- Global Variables
#define EA_MAGIC 823471
CTrade trade;

int      m_ema_handle = INVALID_HANDLE;
datetime m_last_candle_time = 0;

//--- Setup Tracking Variables
bool     m_setup_active = false;
double   m_signal_high = 0.0;
double   m_signal_low = 0.0;
datetime m_signal_candle_time = 0;

//+------------------------------------------------------------------+
//| Get Timeframe Enum Value                                         |
//+------------------------------------------------------------------+
ENUM_TIMEFRAMES GetTimeframe(ENUM_CUSTOM_TIMEFRAME tf)
{
   switch(tf)
   {
      case TF_1_MIN:   return PERIOD_M1;
      case TF_3_MIN:   return PERIOD_M3;
      case TF_5_MIN:   return PERIOD_M5;
      case TF_15_MIN:  return PERIOD_M15;
      case TF_30_MIN:  return PERIOD_M30;
      case TF_1_HOUR:  return PERIOD_H1;
      case TF_1_DAY:   return PERIOD_D1;
      default:         return PERIOD_CURRENT;
   }
}

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // Set Expert Magic Number
   trade.SetExpertMagicNumber(EA_MAGIC);

   // Initialize EMA Indicator
   m_ema_handle = iMA(_Symbol, GetTimeframe(InpTimeframe), InpEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
   if(m_ema_handle == INVALID_HANDLE)
   {
      Print("Error: Failed to create EMA indicator handle.");
      return INIT_FAILED;
   }

   m_last_candle_time = 0;
   m_setup_active = false;

   PrintFormat("Main EMA Breakdown EA Initialized on Timeframe %d Min, EMA Period %d.", InpTimeframe, InpEMAPeriod);
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
}

//+------------------------------------------------------------------+
//| Normalize Price                                                  |
//+------------------------------------------------------------------+
double NormalizePrice(double price)
{
   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tick_size > 0)
   {
      return MathRound(price / tick_size) * tick_size;
   }
   return NormalizeDouble(price, _Digits);
}

//+------------------------------------------------------------------+
//| Normalize Lot Size                                               |
//+------------------------------------------------------------------+
double NormalizeLotSize(double lot)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double min_vol = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_vol = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   if(step > 0)
   {
      lot = MathRound(lot / step) * step;
   }
   if(lot < min_vol) lot = min_vol;
   if(lot > max_vol) lot = max_vol;
   return NormalizeDouble(lot, 2);
}

//+------------------------------------------------------------------+
//| Check if position is open for this Magic Number and Symbol      |
//+------------------------------------------------------------------+
bool IsPositionOpen()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == EA_MAGIC)
         {
            return true;
         }
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Get last close time from history for cooldown enforcement        |
//+------------------------------------------------------------------+
datetime GetLastCloseTime()
{
   datetime last_close = 0;
   if(HistorySelect(0, TimeCurrent()))
   {
      int total = HistoryDealsTotal();
      for(int i = total - 1; i >= 0; i--)
      {
         ulong ticket = HistoryDealGetTicket(i);
         if(ticket > 0)
         {
            long magic = HistoryDealGetInteger(ticket, DEAL_MAGIC);
            if(magic == EA_MAGIC)
            {
               long entry_type = HistoryDealGetInteger(ticket, DEAL_ENTRY);
               if(entry_type == DEAL_ENTRY_OUT || entry_type == DEAL_ENTRY_OUT_BY)
               {
                  datetime deal_time = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
                  if(deal_time > last_close)
                  {
                     last_close = deal_time;
                  }
               }
            }
         }
      }
   }
   return last_close;
}

//+------------------------------------------------------------------+
//| Resolve broker order filling mode                                |
//+------------------------------------------------------------------+
uint GetFillingMode()
{
   long filling = 0;
   if(SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE, filling))
   {
      if((filling & SYMBOL_FILLING_FOK) != 0) return ORDER_FILLING_FOK;
      if((filling & SYMBOL_FILLING_IOC) != 0) return ORDER_FILLING_IOC;
   }
   return ORDER_FILLING_RETURN;
}

//+------------------------------------------------------------------+
//| Check for completed candle close & evaluate signal                |
//+------------------------------------------------------------------+
void OnNewCandle()
{
   MqlRates rates[];
   if(CopyRates(_Symbol, GetTimeframe(InpTimeframe), 1, 1, rates) < 1)
   {
      Print("Warning: Failed to copy completed candle rates.");
      return;
   }

   double ema_values[];
   if(CopyBuffer(m_ema_handle, 0, 1, 1, ema_values) < 1)
   {
      Print("Warning: Failed to copy EMA values.");
      return;
   }

   double open  = rates[0].open;
   double high  = rates[0].high;
   double low   = rates[0].low;
   double close = rates[0].close;
   double ema   = ema_values[0];

   // Signal Logic Check:
   // - Must be a Red candle (close < open)
   // - Candle crossed below and closed below EMA, OR touched the EMA and closed below EMA
   //   This is equivalent to: high >= ema && close < ema
   if(close < open && high >= ema && close < ema)
   {
      m_signal_high = high;
      m_signal_low = low;
      m_signal_candle_time = rates[0].time;
      m_setup_active = true;

      PrintFormat("Valid Signal Candle detected @ %s. High: %.5f, Low: %.5f, EMA: %.5f. Setup activated.",
                  TimeToString(rates[0].time, TIME_DATE|TIME_MINUTES), m_signal_high, m_signal_low, ema);
   }
   else
   {
      m_setup_active = false;
   }
}

//+------------------------------------------------------------------+
//| Execute Sell Entry                                               |
//+------------------------------------------------------------------+
void ExecuteSellEntry(double bid)
{
   double sl = m_signal_high;
   double tp = 0.0;

   if(InpUseRRTarget)
   {
      double risk_distance = sl - bid;
      if(risk_distance <= 0) risk_distance = _Point;
      double tp_distance = risk_distance * InpRiskRewardRatio;
      tp = bid - tp_distance;
      tp = NormalizePrice(tp);
   }

   sl = NormalizePrice(sl);
   double lot = NormalizeLotSize(InpLotSize);

   trade.SetTypeFilling((ENUM_ORDER_TYPE_FILLING)GetFillingMode());

   PrintFormat("Executing Sell Breakout: Lot=%.2f, Bid=%.5f, SL=%.5f, TP=%.5f", lot, bid, sl, tp);

   if(trade.Sell(lot, _Symbol, bid, sl, tp, "EMA Breakdown Sell"))
   {
      m_setup_active = false; // Disable setup so we don't re-trigger
      Print("Sell order submitted successfully.");
   }
   else
   {
      Print("Error placing Sell order: ", trade.ResultRetcode(), " - ", trade.ResultComment());
   }
}

//+------------------------------------------------------------------+
//| Monitor active position profit for Dollar target                 |
//+------------------------------------------------------------------+
void CheckDollarTarget()
{
   if(!InpUseDollarTarget) return;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == EA_MAGIC)
         {
            double profit = PositionGetDouble(POSITION_PROFIT);
            if(profit >= InpDollarTarget)
            {
               PrintFormat("Dollar profit target reached: Floating Profit=%.2f >= Target=%.2f. Closing position.", profit, InpDollarTarget);
               trade.PositionClose(ticket);
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| OnTick function                                                  |
//+------------------------------------------------------------------+
void OnTick()
{
   // Check for new candle completion
   datetime times[];
   if(CopyTime(_Symbol, GetTimeframe(InpTimeframe), 0, 1, times) >= 1)
   {
      if(times[0] != m_last_candle_time)
      {
         m_last_candle_time = times[0];
         OnNewCandle();
      }
   }

   // Monitor and enforce Dollar target if applicable
   CheckDollarTarget();

   // Check for next immediate candle breakout entry
   if(m_setup_active && !IsPositionOpen())
   {
      datetime current_time[];
      if(CopyTime(_Symbol, GetTimeframe(InpTimeframe), 0, 1, current_time) >= 1)
      {
         datetime expected_current_time = m_signal_candle_time + PeriodSeconds(GetTimeframe(InpTimeframe));

         if(current_time[0] > expected_current_time)
         {
            // Setup expired: next immediate candle completed without breaking the low
            Print("Setup expired: The immediate next candle completed without breaking the signal candle's low.");
            m_setup_active = false;
            return;
         }
         else if(current_time[0] == expected_current_time)
         {
            // We are strictly on the next immediate candle
            double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
            if(bid < m_signal_low)
            {
               // Enforce Cooldown period
               if(TimeCurrent() - GetLastCloseTime() < InpCoolDownSeconds)
               {
                  static datetime last_cooldown_print = 0;
                  if(TimeCurrent() - last_cooldown_print > 10)
                  {
                     Print("Breakout detected, but entry blocked by Cooldown Period.");
                     last_cooldown_print = TimeCurrent();
                  }
                  return;
               }

               // All criteria met -> execute trade!
               ExecuteSellEntry(bid);
            }
         }
      }
   }
}
//+------------------------------------------------------------------+
