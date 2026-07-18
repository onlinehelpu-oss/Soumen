//==============================================================//
// Flexible Long Upper Wick Rejection Candle Detector EA
// Detects ANY candle that satisfies:
// 1. Upper Wick >= MinUpperWickPct of total candle range
// 2. Body <= MaxBodyPct of total candle range
// 3. Lower Wick <= MaxLowerWickPct of total candle range
//==============================================================//

#property copyright "Copyright 2023, Code Expert Writer"
#property link      "https://www.mql5.com"
#property version   "1.00"
#property description "Flexible Long Upper Wick Rejection Candle Detector EA"

//--- Include standard libraries
#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\SymbolInfo.mqh>

//--- Input Parameters
input group "--- Strategy Settings ---"
input ENUM_TIMEFRAMES InpTimeframe         = PERIOD_M15;       // Signal Timeframe
input double          InpMinUpperWickPct   = 50.0;             // Min Upper Wick (% of total range)
input double          InpMaxBodyPct        = 50.0;             // Max Body (% of total range)
input double          InpMaxLowerWickPct   = 20.0;             // Max Lower Wick (% of total range)
input int             InpMinCandleRange    = 50;               // Min Candle Range (Points)
input bool            InpRequireRedSignal  = true;             // Signal candle (shift=1) must be RED
input bool            InpRequireGreenPrev  = true;             // Previous candle (shift=2) must be GREEN

input group "--- Execution Settings ---"
input double          InpLotSize           = 0.1;              // Position Size (Lots)
input double          InpRiskPercent       = 1.0;              // Risk Percent per trade (0 = Use Fixed Lot)
input int             InpEntryBuffer       = 10;               // Entry Buffer (Points below Low)
input int             InpSLBuffer          = 10;               // Stop Loss Buffer (Points above High)
input double          InpRiskRewardRatio   = 2.0;              // Risk:Reward Ratio (e.g. 1.5, 2.0)

input group "--- Trailing Stop Settings ---"
input bool            InpUseTrailing       = true;             // Enable Trailing Stop
input int             InpTrailingStart     = 150;              // Trailing Start (Points)
input int             InpTrailingStep      = 50;               // Trailing Step (Points)

input group "--- General Settings ---"
input ulong           InpMagicNumber       = 881234;           // Magic Number
input int             InpSlippage          = 30;               // Allowed Slippage (Points)

//--- Global Objects
CTrade         m_trade;
CPositionInfo  m_position;
CSymbolInfo    m_symbol;

//--- Global State Variables
datetime g_LastBarTime            = 0;
bool     g_SignalActive           = false;
datetime g_SignalTime             = 0;
double   g_SignalLow              = 0;
double   g_SignalHigh             = 0;
double   g_BreakoutLow            = 0;
double   g_StopLossPrice          = 0;
double   g_TakeProfitPrice        = 0;
int      g_TotalTradesCount       = 0;
string   g_LastInvalidationReason = "";

//--- Helper functions for robust candle data fetching in MQL5
double GetOpen(int shift)
{
   double val[1];
   if(CopyOpen(_Symbol, InpTimeframe, shift, 1, val) > 0)
      return val[0];
   return 0;
}

double GetHigh(int shift)
{
   double val[1];
   if(CopyHigh(_Symbol, InpTimeframe, shift, 1, val) > 0)
      return val[0];
   return 0;
}

double GetLow(int shift)
{
   double val[1];
   if(CopyLow(_Symbol, InpTimeframe, shift, 1, val) > 0)
      return val[0];
   return 0;
}

double GetClose(int shift)
{
   double val[1];
   if(CopyClose(_Symbol, InpTimeframe, shift, 1, val) > 0)
      return val[0];
   return 0;
}

datetime GetTime(int shift)
{
   datetime val[1];
   if(CopyTime(_Symbol, InpTimeframe, shift, 1, val) > 0)
      return val[0];
   return 0;
}

//--------------------------------------------------------------//
// Returns TRUE if candle matches rejection criteria
//--------------------------------------------------------------//
bool IsLongUpperWickRejection(int shift=1)
{
   double O = GetOpen(shift);
   double H = GetHigh(shift);
   double L = GetLow(shift);
   double C = GetClose(shift);

   double Range = H - L;

   if(Range <= 0)
      return(false);

   double RangePoints = Range / m_symbol.Point();
   if(RangePoints < InpMinCandleRange)
      return(false);

   double Body       = MathAbs(O - C);
   double UpperWick  = H - MathMax(O,C);
   double LowerWick  = MathMin(O,C) - L;

   double UpperPct = (UpperWick / Range) * 100.0;
   double BodyPct  = (Body      / Range) * 100.0;
   double LowerPct = (LowerWick / Range) * 100.0;

   if(UpperPct < InpMinUpperWickPct)
      return(false);

   if(BodyPct > InpMaxBodyPct)
      return(false);

   if(LowerPct > InpMaxLowerWickPct)
      return(false);

   // Previous candle green confirmation
   if(InpRequireGreenPrev)
   {
      double O_prev = GetOpen(shift + 1);
      double C_prev = GetClose(shift + 1);
      if(C_prev <= O_prev)
         return(false);
   }

   // Signal candle red requirement
   if(InpRequireRedSignal)
   {
      if(C >= O)
         return(false);
   }

   return(true);
}

//--- Check if there is an active position for this EA
bool HasActivePosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(m_position.SelectByIndex(i))
      {
         if(m_position.Symbol() == _Symbol &&
            m_position.Magic() == InpMagicNumber)
         {
            return true;
         }
      }
   }
   return false;
}

//--- Trade filling mode helper
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

//--- Calculate Lot Size based on risk percentage or use fixed lot
double CalculateLotSize(double sl_distance_points)
{
   if(InpRiskPercent <= 0)
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

   // Normalize lot size
   double lot_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   calculated_lot = MathRound(calculated_lot / lot_step) * lot_step;

   if(calculated_lot < min_lot)
      calculated_lot = min_lot;
   if(calculated_lot > max_lot)
      calculated_lot = max_lot;

   return calculated_lot;
}

//--- Manage Trailing Stop for Open Short Positions
void ManageTrailingStop()
{
   if(!InpUseTrailing)
      return;

   double ask = m_symbol.Ask();

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(m_position.SelectByIndex(i))
      {
         if(m_position.Symbol() == _Symbol &&
            m_position.Magic() == InpMagicNumber)
         {
            if(m_position.PositionType() == POSITION_TYPE_SELL)
            {
               double open_price = m_position.PriceOpen();
               double current_sl = m_position.StopLoss();

               double profit_points = (open_price - ask) / m_symbol.Point();

               if(profit_points >= InpTrailingStart)
               {
                  double target_sl = ask + (InpTrailingStep * m_symbol.Point());
                  int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
                  target_sl = NormalizeDouble(target_sl, digits);

                  if(current_sl == 0 || target_sl < current_sl - (InpTrailingStep * m_symbol.Point()))
                  {
                     if(m_trade.PositionModify(m_position.Ticket(), target_sl, m_position.TakeProfit()))
                     {
                        PrintFormat("Trailing Stop updated for Position #%I64u. New SL: %.5f", m_position.Ticket(), target_sl);
                     }
                  }
               }
            }
         }
      }
   }
}

//--- Draw the live info dashboard
void UpdateDashboard()
{
   string comment = "";
   comment += "============================================================\n";
   comment += "         FLEXIBLE LONG UPPER WICK REJECTION EA (MT5)        \n";
   comment += "============================================================\n";
   comment += StringFormat(" Timeframe       : %s\n", EnumToString(InpTimeframe));
   comment += StringFormat(" Magic Number    : %I64u\n", InpMagicNumber);
   comment += StringFormat(" Account Balance : %.2f %s\n", AccountInfoDouble(ACCOUNT_BALANCE), AccountInfoString(ACCOUNT_CURRENCY));
   comment += StringFormat(" Account Equity  : %.2f %s\n", AccountInfoDouble(ACCOUNT_EQUITY), AccountInfoString(ACCOUNT_CURRENCY));
   comment += "------------------------------------------------------------\n";
   comment += "  STRATEGY SETTINGS:\n";
   comment += StringFormat("   Min Upper Wick : %.1f%%\n", InpMinUpperWickPct);
   comment += StringFormat("   Max Body       : %.1f%%\n", InpMaxBodyPct);
   comment += StringFormat("   Max Lower Wick : %.1f%%\n", InpMaxLowerWickPct);
   comment += StringFormat("   Min Range (Pts): %d\n", InpMinCandleRange);
   comment += StringFormat("   Risk Reward    : 1 : %.1f\n", InpRiskRewardRatio);
   comment += "------------------------------------------------------------\n";
   comment += "  CURRENT STATE:\n";

   if(HasActivePosition())
   {
      comment += "   Status        : [ POSITION OPEN ]\n";
   }
   else if(g_SignalActive)
   {
      comment += "   Status        : [ BREAKOUT PENDING ]\n";
      comment += StringFormat("   Signal Time   : %s\n", TimeToString(g_SignalTime));
      comment += StringFormat("   Breakout Low  : %.5f (Current Bid: %.5f)\n", g_BreakoutLow, m_symbol.Bid());
      comment += StringFormat("   Target SL     : %.5f\n", g_StopLossPrice);
      comment += StringFormat("   Target TP     : %.5f\n", g_TakeProfitPrice);
   }
   else
   {
      comment += "   Status        : [ SCANNING FOR REJECTION ]\n";
      if(g_LastInvalidationReason != "")
         comment += StringFormat("   Last Event    : %s\n", g_LastInvalidationReason);
   }

   comment += "------------------------------------------------------------\n";
   comment += StringFormat("  Total Trades Executed: %d\n", g_TotalTradesCount);
   comment += "============================================================\n";

   Comment(comment);
}

//==============================================================//
// Expert Initialization Function
//==============================================================//
int OnInit()
{
   // Input Validation
   if(InpMinUpperWickPct < 0.0 || InpMinUpperWickPct > 100.0)
   {
      Print("Error: MinUpperWickPct must be between 0 and 100.");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpMaxBodyPct < 0.0 || InpMaxBodyPct > 100.0)
   {
      Print("Error: MaxBodyPct must be between 0 and 100.");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpMaxLowerWickPct < 0.0 || InpMaxLowerWickPct > 100.0)
   {
      Print("Error: MaxLowerWickPct must be between 0 and 100.");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpRiskRewardRatio <= 0.0)
   {
      Print("Error: RiskRewardRatio must be greater than 0.");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpLotSize <= 0.0 && InpRiskPercent <= 0.0)
   {
      Print("Error: Please specify either a valid Lot Size or Risk Percent.");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(!m_symbol.Name(_Symbol))
   {
      Print("Error: Failed to initialize Symbol Info.");
      return INIT_FAILED;
   }

   m_symbol.RefreshRates();

   // Set trade parameters
   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_trade.SetDeviationInPoints(InpSlippage);
   SetTradeFillingMode();

   // Initialize last bar time
   g_LastBarTime = GetTime(0);

   // Set timer for dashboard updates
   EventSetTimer(1);

   Print("Flexible Upper Wick Rejection EA initialized successfully.");
   return INIT_SUCCEEDED;
}

//==============================================================//
// Expert Deinitialization Function
//==============================================================//
void OnDeinit(const int reason)
{
   EventKillTimer();
   Comment(""); // Clear chart comment
   Print("Flexible Upper Wick Rejection EA deinitialized. Reason: ", reason);
}

//==============================================================//
// Expert Tick Function
//==============================================================//
void OnTick()
{
   if(!m_symbol.RefreshRates())
      return;

   datetime current_bar_time = GetTime(0);
   if(current_bar_time == 0)
      return; // History not loaded yet

   if(g_LastBarTime == 0)
   {
      g_LastBarTime = current_bar_time;
      return;
   }

   // 1. Handle New Bar Transition on the Signal Timeframe
   if(current_bar_time != g_LastBarTime)
   {
      g_LastBarTime = current_bar_time;

      // If a signal was active but not triggered, it is now expired (next-candle-only breakout rule)
      if(g_SignalActive)
      {
         Print("Signal expired: The immediate next candle did not trigger a breakout.");
         g_SignalActive = false;
         g_LastInvalidationReason = "Immediate next candle closed without breakout";
      }

      // Scan for a new signal on the newly completed candle (shift 1)
      if(!HasActivePosition())
      {
         if(IsLongUpperWickRejection(1))
         {
            double low_val = GetLow(1);
            double high_val = GetHigh(1);

            int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

            g_BreakoutLow = NormalizeDouble(low_val - (InpEntryBuffer * m_symbol.Point()), digits);
            g_StopLossPrice = NormalizeDouble(high_val + (InpSLBuffer * m_symbol.Point()), digits);

            double sl_distance = g_StopLossPrice - g_BreakoutLow;
            if(sl_distance > 0)
            {
               g_TakeProfitPrice = NormalizeDouble(g_BreakoutLow - (sl_distance * InpRiskRewardRatio), digits);
               g_SignalActive = true;
               g_SignalTime = GetTime(1);
               g_SignalLow = low_val;
               g_SignalHigh = high_val;
               g_LastInvalidationReason = "";

               PrintFormat("New Signal Detected! Candle Time: %s. High: %.5f, Low: %.5f. Pending Short Breakout Trigger at: %.5f, SL: %.5f, TP: %.5f",
                  TimeToString(g_SignalTime), g_SignalHigh, g_SignalLow, g_BreakoutLow, g_StopLossPrice, g_TakeProfitPrice);
            }
         }
      }
   }

   // 2. Check for Breakout Trigger on Active Signal
   if(g_SignalActive && !HasActivePosition())
   {
      double bid = m_symbol.Bid();
      if(bid <= g_BreakoutLow)
      {
         PrintFormat("Breakout triggered! Bid: %.5f <= Breakout Level: %.5f. Executing SELL order...", bid, g_BreakoutLow);

         double sl_distance_points = (g_StopLossPrice - g_BreakoutLow) / m_symbol.Point();
         double lot = CalculateLotSize(sl_distance_points);

         // Ensure we have a valid positive lot size
         if(lot <= 0)
         {
            PrintFormat("Invalid Lot size calculated: %.2f. Order rejected.", lot);
            g_SignalActive = false;
            g_LastInvalidationReason = "Invalid Lot Size";
            return;
         }

         // Execute standard Sell Order
         if(m_trade.Sell(lot, _Symbol, bid, g_StopLossPrice, g_TakeProfitPrice, "Breakout Short"))
         {
            ulong result_order = m_trade.ResultOrder();
            uint ret_code = m_trade.ResultRetcode();

            if(ret_code == TRADE_RETCODE_DONE || ret_code == TRADE_RETCODE_PLACED)
            {
               PrintFormat("SELL Order successfully placed! Ticket: %I64u, Price: %.5f", result_order, m_trade.ResultPrice());
               g_SignalActive = false; // Reset signal on execution
               g_TotalTradesCount++;
               g_LastInvalidationReason = "Breakout order executed successfully";
            }
            else
            {
               PrintFormat("Error placing SELL Order. Retcode: %u, Description: %s", ret_code, m_trade.ResultRetcodeDescription());
               g_SignalActive = false;
               g_LastInvalidationReason = "Order placement failed";
            }
         }
         else
         {
            Print("Error calling m_trade.Sell(). Error Code: ", GetLastError());
            g_SignalActive = false;
            g_LastInvalidationReason = "Sell call failed";
         }
      }
   }

   // 3. Manage active positions (Trailing Stop)
   ManageTrailingStop();
}

//==============================================================//
// Expert Timer Function
//==============================================================//
void OnTimer()
{
   UpdateDashboard();
}
