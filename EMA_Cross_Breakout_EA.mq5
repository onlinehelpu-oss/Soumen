//+------------------------------------------------------------------+
//|                                     EMA_Cross_Breakout_EA.mq5    |
//|                                  Copyright 2026, Jules           |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, Jules"
#property link      "https://www.mql5.com"
#property version   "1.00"

// Include standard trade library
#include <Trade\Trade.mqh>

// Define custom timeframe selection enum as requested
enum ENUM_CUSTOM_TIMEFRAME
{
   TF_1_MIN = 1,   // 1 Minute
   TF_3_MIN = 3,   // 3 Minutes
   TF_5_MIN = 5,   // 5 Minutes
   TF_15_MIN = 15, // 15 Minutes
   TF_30_MIN = 30, // 30 Minutes
   TF_1_HOUR = 60, // 1 Hour
   TF_4_HOUR = 240,// 4 Hours
   TF_1_DAY = 1440 // 1 Day
};

//+------------------------------------------------------------------+
//| Input Parameters                                                 |
//+------------------------------------------------------------------+
input group "=== Strategy Parameters ==="
input ENUM_CUSTOM_TIMEFRAME InpTimeframe = TF_15_MIN;       // Candle Timeframe
input int                   InpMainEMAPeriod = 34;          // Main EMA Period (e.g. 34)
input int                   InpFastEMAPeriod = 9;           // Fast EMA Period (e.g. 9)
input int                   InpSlowEMAPeriod = 15;          // Slow EMA Period (e.g. 15)
input double                InpRiskReward = 2.0;            // Risk-to-Reward Ratio (e.g. 2.0)

input group "=== Higher Timeframe Filter ==="
input bool                  InpUseHTFFilter = true;         // Use Higher Timeframe Filter?
input ENUM_CUSTOM_TIMEFRAME InpHigherTimeframe = TF_15_MIN; // Higher Timeframe
input int                   InpHTFEMAPeriod = 9;            // Higher Timeframe EMA Period

input group "=== Trade Execution Parameters ==="
input double                InpLotSize = 0.01;              // Lot Size
input ulong                 InpMagicNumber = 20260305;      // Magic Number
input ulong                 InpSlippage = 10;               // Slippage in points

//+------------------------------------------------------------------+
//| Global Variables                                                 |
//+------------------------------------------------------------------+
int      m_handle_main = INVALID_HANDLE;
int      m_handle_fast = INVALID_HANDLE;
int      m_handle_slow = INVALID_HANDLE;
int      m_handle_htf  = INVALID_HANDLE;

datetime m_last_bar_time = 0;

// Setup tracking
bool     m_is_setup_active = false;
double   m_signal_candle_low = 0.0;
double   m_signal_candle_high = 0.0;
datetime m_setup_bar_time = 0;

CTrade   m_trade;

//+------------------------------------------------------------------+
//| Helper: Convert Custom Timeframe Enum to standard ENUM_TIMEFRAMES|
//+------------------------------------------------------------------+
ENUM_TIMEFRAMES GetTimeframe(ENUM_CUSTOM_TIMEFRAME tf)
{
   switch(tf)
   {
      case TF_1_MIN:  return PERIOD_M1;
      case TF_3_MIN:  return PERIOD_M3;
      case TF_5_MIN:  return PERIOD_M5;
      case TF_15_MIN: return PERIOD_M15;
      case TF_30_MIN: return PERIOD_M30;
      case TF_1_HOUR: return PERIOD_H1;
      case TF_4_HOUR: return PERIOD_H4;
      case TF_1_DAY:  return PERIOD_D1;
      default:        return PERIOD_CURRENT;
   }
}

//+------------------------------------------------------------------+
//| Helper: Normalize Price according to tick size and digits        |
//+------------------------------------------------------------------+
double NormalizePrice(double price)
{
   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tick_size == 0) return NormalizeDouble(price, _Digits);
   return NormalizeDouble(MathRound(price / tick_size) * tick_size, _Digits);
}

//+------------------------------------------------------------------+
//| Helper: Set dynamic trade filling mode                           |
//+------------------------------------------------------------------+
void SetTradeFillingMode()
{
   uint filling = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0)
   {
      m_trade.SetTypeFilling(ORDER_FILLING_FOK);
   }
   else if((filling & SYMBOL_FILLING_IOC) != 0)
   {
      m_trade.SetTypeFilling(ORDER_FILLING_IOC);
   }
   else
   {
      m_trade.SetTypeFilling(ORDER_FILLING_RETURN);
   }
}

//+------------------------------------------------------------------+
//| Helper: Check if a position is already open                      |
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
//| Helper: Evaluate if the candle is a valid Signal Candle          |
//+------------------------------------------------------------------+
bool IsSignalCandle(MqlRates &rates[], double &ema_main[], double &ema_fast[], double &ema_slow[])
{
   // Index 1 is the completed candle
   double open_p  = rates[1].open;
   double high_p  = rates[1].high;
   double close_p = rates[1].close;

   double main_v = ema_main[1];
   double fast_v = ema_fast[1];
   double slow_v = ema_slow[1];

   // Verify we have valid numeric values
   if(main_v == EMPTY_VALUE || fast_v == EMPTY_VALUE || slow_v == EMPTY_VALUE)
      return false;

   // 1. The candle must be RED / bearish.
   if(close_p >= open_p)
      return false;

   // 2. The candle must close below the Main EMA.
   if(close_p >= main_v)
      return false;

   // 3. The same candle must cross below or touch and cross below the Fast EMA.
   // High must be >= Fast EMA and Close must be < Fast EMA
   if(high_p < fast_v || close_p >= fast_v)
      return false;

   // 4. The same candle must cross below or touch and cross below the Slow EMA.
   // High must be >= Slow EMA and Close must be < Slow EMA
   if(high_p < slow_v || close_p >= slow_v)
      return false;

   // 5. The same red candle must therefore cross the Main EMA.
   // Since close is already checked to be below Main EMA, High must be >= Main EMA
   if(high_p < main_v)
      return false;

   return true;
}

//+------------------------------------------------------------------+
//| Helper: Execute SELL Order                                       |
//+------------------------------------------------------------------+
void ExecuteSellEntry(double entry_price)
{
   SetTradeFillingMode();

   // Calculate risk and TP/SL
   double risk = m_signal_candle_high - entry_price;
   if(risk <= 0)
   {
      risk = _Point; // Fallback to avoid negative or zero risk
   }

   double sl = m_signal_candle_high;
   double tp = entry_price - (risk * InpRiskReward);

   // Normalize SL and TP
   sl = NormalizePrice(sl);
   tp = NormalizePrice(tp);
   entry_price = NormalizePrice(entry_price);

   m_trade.SetExpertMagicNumber(InpMagicNumber);

   PrintFormat("EMA_Cross_Breakout_EA: Sending SELL market order. Entry: %f, SL: %f, TP: %f, Lots: %f",
               entry_price, sl, tp, InpLotSize);

   if(m_trade.Sell(InpLotSize, _Symbol, 0, sl, tp, "EMA Cross Breakout"))
   {
      ulong ticket = m_trade.ResultOrder();
      PrintFormat("EMA_Cross_Breakout_EA: SELL order sent successfully. Ticket: %I64u", ticket);
   }
   else
   {
      PrintFormat("EMA_Cross_Breakout_EA: Failed to send SELL order. Return code: %d, Description: %s",
                  m_trade.ResultRetcode(), m_trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // Set magic number on trade object
   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_trade.SetDeviationInPoints(InpSlippage);

   // Convert timeframe
   ENUM_TIMEFRAMES tf = GetTimeframe(InpTimeframe);

   // Create indicator handles
   m_handle_main = iMA(_Symbol, tf, InpMainEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
   m_handle_fast = iMA(_Symbol, tf, InpFastEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
   m_handle_slow = iMA(_Symbol, tf, InpSlowEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);

   if(m_handle_main == INVALID_HANDLE || m_handle_fast == INVALID_HANDLE || m_handle_slow == INVALID_HANDLE)
   {
      Print("EMA_Cross_Breakout_EA: Error initializing indicator handles.");
      return INIT_FAILED;
   }

   // Initialize Higher Timeframe EMA handle if used
   if(InpUseHTFFilter)
   {
      ENUM_TIMEFRAMES htf = GetTimeframe(InpHigherTimeframe);
      m_handle_htf = iMA(_Symbol, htf, InpHTFEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
      if(m_handle_htf == INVALID_HANDLE)
      {
         Print("EMA_Cross_Breakout_EA: Error initializing HTF indicator handle.");
         return INIT_FAILED;
      }
   }

   // Set filling mode dynamically
   SetTradeFillingMode();

   Print("EMA_Cross_Breakout_EA: Initialized successfully.");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   // Properly release indicator handles
   if(m_handle_main != INVALID_HANDLE)
      IndicatorRelease(m_handle_main);
   if(m_handle_fast != INVALID_HANDLE)
      IndicatorRelease(m_handle_fast);
   if(m_handle_slow != INVALID_HANDLE)
      IndicatorRelease(m_handle_slow);
   if(m_handle_htf != INVALID_HANDLE)
      IndicatorRelease(m_handle_htf);

   Print("EMA_Cross_Breakout_EA: Deinitialized.");
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // Convert timeframe
   ENUM_TIMEFRAMES tf = GetTimeframe(InpTimeframe);

   // Retrieve rates
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied_rates = CopyRates(_Symbol, tf, 0, 3, rates);
   if(copied_rates < 3)
   {
      return; // Not enough history yet
   }

   datetime current_bar_time = rates[0].time;

   // Check for new bar open
   if(current_bar_time != m_last_bar_time)
   {
      // Retrieve EMA buffers
      double ema_main[];
      double ema_fast[];
      double ema_slow[];
      ArraySetAsSeries(ema_main, true);
      ArraySetAsSeries(ema_fast, true);
      ArraySetAsSeries(ema_slow, true);

      if(CopyBuffer(m_handle_main, 0, 0, 3, ema_main) < 3 ||
         CopyBuffer(m_handle_fast, 0, 0, 3, ema_fast) < 3 ||
         CopyBuffer(m_handle_slow, 0, 0, 3, ema_slow) < 3)
      {
         // Fail-safe: do not update m_last_bar_time to retry next tick
         return;
      }

      // Since a new bar has opened, the previous "immediate next candle" setup is now expired
      if(m_is_setup_active)
      {
         PrintFormat("EMA_Cross_Breakout_EA: Setup expired. Immediate next candle closed without breakout. Signal Candle Low was: %f", m_signal_candle_low);
         m_is_setup_active = false;
      }

      // Evaluate if the completed candle (index 1) is a valid Signal Candle
      bool htf_filter_passed = true;
      if(InpUseHTFFilter)
      {
         MqlRates htf_rates[];
         double htf_ema[];
         ArraySetAsSeries(htf_rates, true);
         ArraySetAsSeries(htf_ema, true);

         ENUM_TIMEFRAMES htf_tf = GetTimeframe(InpHigherTimeframe);
         if(CopyRates(_Symbol, htf_tf, 0, 2, htf_rates) >= 2 &&
            CopyBuffer(m_handle_htf, 0, 0, 2, htf_ema) >= 2)
         {
            double htf_close = htf_rates[1].close;
            double htf_ema_val = htf_ema[1];
            if(htf_close >= htf_ema_val)
            {
               htf_filter_passed = false;
            }
         }
         else
         {
            htf_filter_passed = false; // Safe fallback: if we cannot read HTF data, do not trigger
         }
      }

      if(htf_filter_passed && IsSignalCandle(rates, ema_main, ema_fast, ema_slow))
      {
         m_is_setup_active = true;
         m_signal_candle_low = rates[1].low;
         m_signal_candle_high = rates[1].high;
         m_setup_bar_time = current_bar_time; // This is rates[0].time

         PrintFormat("EMA_Cross_Breakout_EA: Valid Signal Candle detected! Time: %s, High: %f, Low: %f. Monitoring immediate next candle for breakout.",
                     TimeToString(rates[1].time), m_signal_candle_high, m_signal_candle_low);
      }

      m_last_bar_time = current_bar_time;
   }

   // Check active breakout setup
   if(m_is_setup_active)
   {
      // Double check that we are still on the immediate next candle
      if(current_bar_time != m_setup_bar_time)
      {
         m_is_setup_active = false;
         return;
      }

      // Get current Bid price
      double current_bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

      // Breakout check: breaks below the LOW of the Signal Candle
      if(current_bid < m_signal_candle_low)
      {
         // Ensure no active position to respect One-Position-at-a-Time constraint
         if(!IsPositionOpen())
         {
            ExecuteSellEntry(current_bid);
         }
         else
         {
            Print("EMA_Cross_Breakout_EA: Breakout occurred, but trade skipped because a position is already open.");
         }

         // Deactivate setup immediately upon breakout check (only one trigger attempt)
         m_is_setup_active = false;
      }
   }
}
//+------------------------------------------------------------------+
