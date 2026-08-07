//+------------------------------------------------------------------+
//|                                                   Traffic Sell.mq5|
//|                                  Copyright 2024, Jules           |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, Jules"
#property link      "https://www.mql5.com"
#property version   "1.00"
#property strict

// Include Trade library
#include <Trade\Trade.mqh>

//--- Input Parameters ---
input group "=== STRATEGY SETTINGS ==="
input ENUM_TIMEFRAMES InpTimeframe            = PERIOD_M5;        // Trade Timeframe
input bool            InpUseSwingHighFilter   = true;             // Use Swing High Filter (ON/OFF)
input int             InpSwingLookback        = 14;               // Swing High Lookback (Left Side Pivot)
input double          InpRiskRewardRatio      = 2.0;              // Risk to Reward Ratio (e.g. 2.0 for 1:2)
input double          InpEntryBufferPoints    = 0.0;              // Entry Buffer in Points (Points below Green Low)
input double          InpSLBufferPoints       = 0.0;              // Stop Loss Buffer in Points (Points above Green High)

input group "=== RISK & TRADE MANAGEMENT ==="
input double          InpLotSize              = 0.1;              // Fixed Lot Size (if Dynamic Lot is OFF)
input bool            InpUseDynamicLot        = false;            // Use Dynamic Lot Sizing (Risk-Based)
input double          InpRiskPercent          = 1.0;              // Account Risk Percent per trade (1.0%)
input double          InpMaxMarginUtilizationPct = 70.0;          // Max Margin Utilization % (safety cap)
input double          InpMinLotSizeOverride   = 0.01;             // Minimum Lot Size Override (0.01 for Gold on XM)
input ulong           InpMagicNumber          = 20241015;         // Magic Number
input int             InpSlippage             = 30;               // Slippage in Points
input string          InpTradeComment         = "Traffic Sell";   // Trade Comment

//--- Global Variables ---
CTrade   m_trade;
datetime m_last_bar_time   = 0;
bool     m_setup_active    = false;
datetime m_setup_time      = 0;
double   m_breakout_level  = 0.0;
double   m_sl_level        = 0.0;

//--- Functions Forward Declarations ---
bool     IsGoldSymbol(string symbol);
void     SetTradeFillingMode(CTrade &trade, string symbol);
bool     IsSwingHigh(string symbol, ENUM_TIMEFRAMES tf, int index, int lookback);
double   GetOpen(string symbol, ENUM_TIMEFRAMES tf, int index);
double   GetHigh(string symbol, ENUM_TIMEFRAMES tf, int index);
double   GetLow(string symbol, ENUM_TIMEFRAMES tf, int index);
double   GetClose(string symbol, ENUM_TIMEFRAMES tf, int index);
datetime GetTime(string symbol, ENUM_TIMEFRAMES tf, int index);
string   StringTimeframe(ENUM_TIMEFRAMES tf);
void     UpdateDashboard();
int      GetActivePositionsCount();
void     ExecuteShortEntry(double entryPrice);
double   CalculateLotSize(double entryPrice, double slPrice);

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // Verify symbol is Gold
   if(!IsGoldSymbol(_Symbol))
   {
      Print("WARNING: This Expert Advisor is optimized and designed specifically for Gold (XAUUSD). Active symbol: ", _Symbol);
   }

   // Initialize Trade Class
   m_trade.SetExpertMagicNumber(InpMagicNumber);
   SetTradeFillingMode(m_trade, _Symbol);

   // Reset variables
   m_last_bar_time = 0;
   m_setup_active = false;
   m_setup_time = 0;
   m_breakout_level = 0.0;
   m_sl_level = 0.0;

   Print("Traffic Sell EA Initialized successfully.");
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Comment("");
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // Skip live trading checks in Strategy Tester to allow continuous backtesting
   bool is_tester = (bool)MQLInfoInteger(MQL_TESTER);
   if(!is_tester)
   {
      if(TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) == 0 || MQLInfoInteger(MQL_TRADE_ALLOWED) == 0)
         return;
   }

   // Check for New Bar
   datetime current_bar_time = GetTime(_Symbol, InpTimeframe, 0);
   if(current_bar_time <= 0) return; // CopyRates / History not synchronized yet

   bool is_new_bar = (current_bar_time != m_last_bar_time);
   if(is_new_bar)
   {
      // If we had an active setup from the previous bar, it has now expired!
      // This strictly enforces that the breakout must happen on the IMMEDIATE NEXT bar.
      if(m_setup_active)
      {
         Print("Immediate next candle closed without breakout. Setup at ", m_setup_time, " has expired and is ignored.");
         m_setup_active = false;
      }

      m_last_bar_time = current_bar_time;

      // Look for a completed signal candle at index 1
      double open1  = GetOpen(_Symbol, InpTimeframe, 1);
      double close1 = GetClose(_Symbol, InpTimeframe, 1);

      if(open1 > 0 && close1 > open1) // Completed candle is Green
      {
         bool swing_ok = true;
         if(InpUseSwingHighFilter)
         {
            swing_ok = IsSwingHigh(_Symbol, InpTimeframe, 1, InpSwingLookback);
         }

         if(swing_ok)
         {
            m_setup_active = true;
            m_setup_time = GetTime(_Symbol, InpTimeframe, 1);
            m_breakout_level = GetLow(_Symbol, InpTimeframe, 1) - InpEntryBufferPoints * _Point;
            m_sl_level = GetHigh(_Symbol, InpTimeframe, 1) + InpSLBufferPoints * _Point;

            Print("New Valid Setup Spotted!");
            Print("  Signal Time: ", m_setup_time);
            Print("  Green Low: ", GetLow(_Symbol, InpTimeframe, 1));
            Print("  Green High (SL): ", GetHigh(_Symbol, InpTimeframe, 1));
            Print("  Breakout Level: ", m_breakout_level);
            if(InpUseSwingHighFilter)
            {
               Print("  Swing High Check: PASSED (Lookback: ", InpSwingLookback, ")");
            }
         }
      }
   }

   // Breakout Monitoring & Execution
   if(m_setup_active)
   {
      if(GetActivePositionsCount() == 0)
      {
         double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         if(bid > 0 && bid <= m_breakout_level)
         {
            // Execute entry and deactivate the setup to prevent duplicate entries
            m_setup_active = false;
            ExecuteShortEntry(bid);
         }
      }
   }

   // Update Visual Chart Dashboard
   if(is_tester && !MQLInfoInteger(MQL_VISUAL_MODE))
   {
      // Skip dashboard updates in non-visual tester mode for maximum backtesting performance
   }
   else
   {
      UpdateDashboard();
   }
}

//+------------------------------------------------------------------+
//| Execute Short Breakout Entry                                     |
//+------------------------------------------------------------------+
void ExecuteShortEntry(double entryPrice)
{
   double risk = m_sl_level - entryPrice;
   if(risk <= 0)
   {
      Print("Error: Calculated risk is <= 0. SL: ", m_sl_level, " | Entry: ", entryPrice);
      return;
   }

   // Calculate TP based on Risk-to-Reward Ratio
   double tp = entryPrice - (risk * InpRiskRewardRatio);

   // Normalize levels to comply with broker digits
   double final_sl = NormalizeDouble(m_sl_level, _Digits);
   double final_tp = NormalizeDouble(tp, _Digits);
   double final_entry = NormalizeDouble(entryPrice, _Digits);

   // Validate against Broker Stop Levels to avoid Code 10015 (Invalid stops)
   double stopLevelPoints = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minStopDistance = stopLevelPoints * _Point;
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   if(MathAbs(final_sl - ask) < minStopDistance)
   {
      final_sl = ask + minStopDistance;
      final_sl = NormalizeDouble(final_sl, _Digits);
      Print("Adjusted SL to satisfy stop level constraint: ", final_sl);
   }
   if(MathAbs(ask - final_tp) < minStopDistance)
   {
      final_tp = ask - minStopDistance;
      final_tp = NormalizeDouble(final_tp, _Digits);
      Print("Adjusted TP to satisfy stop level constraint: ", final_tp);
   }

   // Calculate Lot size using Risk-based or Fixed methods
   double lots = CalculateLotSize(final_entry, final_sl);
   if(lots <= 0)
   {
      Print("Error: Calculated lot size is invalid: ", lots);
      return;
   }

   // Update Trade filling mode
   SetTradeFillingMode(m_trade, _Symbol);

   Print("Sending Sell Breakout Order...");
   Print("  Volume: ", lots);
   Print("  Entry Price: ", final_entry);
   Print("  Stop Loss: ", final_sl);
   Print("  Take Profit: ", final_tp);

   if(m_trade.Sell(lots, _Symbol, final_entry, final_sl, final_tp, InpTradeComment))
   {
      if(m_trade.ResultRetcode() == 10009 || m_trade.ResultRetcode() == 10008)
      {
         Print("Sell Order Successfully Filled! Deal Ticket: ", m_trade.ResultDeal());
      }
      else
      {
         Print("Order accepted but returned code: ", m_trade.ResultRetcode(), " (", m_trade.ResultRetcodeDescription(), ")");
      }
   }
   else
   {
      Print("Sell Order Failed! Code: ", m_trade.ResultRetcode(), " | Description: ", m_trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Calculate Lot Size based on risk management or fixed settings    |
//+------------------------------------------------------------------+
double CalculateLotSize(double entryPrice, double slPrice)
{
   double lot = InpLotSize;

   if(InpUseDynamicLot)
   {
      double balance = AccountInfoDouble(ACCOUNT_BALANCE);
      double riskVal = balance * (InpRiskPercent / 100.0);
      double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
      double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);

      if(tickValue > 0 && tickSize > 0 && MathAbs(entryPrice - slPrice) > 0)
      {
         double pointsRisk = MathAbs(entryPrice - slPrice);
         lot = riskVal / (pointsRisk * (tickValue / tickSize));
      }
   }

   // Retrieve Broker Volume Steps and Constraints
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(lotStep <= 0) lotStep = 0.01;

   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   if(minLot <= 0) minLot = 0.01;

   // Apply minimum override if specified (crucial for Gold on XM brokers)
   if(InpMinLotSizeOverride > 0 && minLot < InpMinLotSizeOverride)
   {
      minLot = InpMinLotSizeOverride;
   }

   double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(maxLot <= 0) maxLot = 100.0;

   // Round to step
   lot = MathRound(lot / lotStep) * lotStep;

   // Constrain within min/max lot size
   if(lot < minLot) lot = minLot;
   if(lot > maxLot) lot = maxLot;

   // Margin Validation to avoid Code 10019 (Not enough money)
   double marginRequired = 0;
   if(OrderCalcMargin(ORDER_TYPE_SELL, _Symbol, lot, entryPrice, marginRequired))
   {
      double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
      double maxAllowedMargin = freeMargin * (InpMaxMarginUtilizationPct / 100.0);
      if(marginRequired > maxAllowedMargin)
      {
         // Scale down volume dynamically to fit within safe margin thresholds
         lot = lot * (maxAllowedMargin / marginRequired);
         lot = MathRound(lot / lotStep) * lotStep;
         if(lot < minLot) lot = minLot;
         Print("Margin limit reached. Scaled down lot size to: ", lot);
      }
   }

   return NormalizeDouble(lot, 2);
}

//+------------------------------------------------------------------+
//| Count active positions owned by this EA on the current symbol     |
//+------------------------------------------------------------------+
int GetActivePositionsCount()
{
   int count = 0;
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
            PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         {
            count++;
         }
      }
   }
   return count;
}

//+------------------------------------------------------------------+
//| Check if symbol is Gold (XAUUSD / GOLD variants)                 |
//+------------------------------------------------------------------+
bool IsGoldSymbol(string symbol)
{
   string sym = symbol;
   StringToUpper(sym);
   if(StringFind(sym, "XAU") >= 0 || StringFind(sym, "GOLD") >= 0)
      return true;
   return false;
}

//+------------------------------------------------------------------+
//| Set Dynamic Filling Mode based on Broker Allowed Flags            |
//+------------------------------------------------------------------+
void SetTradeFillingMode(CTrade &trade, string symbol)
{
   uint filling = (uint)SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0)
      trade.SetTypeFilling(ORDER_FILLING_FOK);
   else if((filling & SYMBOL_FILLING_IOC) != 0)
      trade.SetTypeFilling(ORDER_FILLING_IOC);
}

//+------------------------------------------------------------------+
//| Check if signal candle is a Swing High (Left-Side Pivot Lookback) |
//+------------------------------------------------------------------+
bool IsSwingHigh(string symbol, ENUM_TIMEFRAMES tf, int index, int lookback)
{
   double highVal = GetHigh(symbol, tf, index);
   if(highVal <= 0) return false;

   for(int i = 1; i <= lookback; i++)
   {
      double otherHigh = GetHigh(symbol, tf, index + i);
      if(otherHigh > highVal)
         return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| Safe Custom-Buffered Price / Time Retrieval Helpers             |
//+------------------------------------------------------------------+
double GetOpen(string symbol, ENUM_TIMEFRAMES tf, int index)
{
   double arr[];
   ArraySetAsSeries(arr, true);
   int copied = CopyOpen(symbol, tf, index, 1, arr);
   if(copied > 0) return arr[0];
   return 0;
}

double GetHigh(string symbol, ENUM_TIMEFRAMES tf, int index)
{
   double arr[];
   ArraySetAsSeries(arr, true);
   int copied = CopyHigh(symbol, tf, index, 1, arr);
   if(copied > 0) return arr[0];
   return 0;
}

double GetLow(string symbol, ENUM_TIMEFRAMES tf, int index)
{
   double arr[];
   ArraySetAsSeries(arr, true);
   int copied = CopyLow(symbol, tf, index, 1, arr);
   if(copied > 0) return arr[0];
   return 0;
}

double GetClose(string symbol, ENUM_TIMEFRAMES tf, int index)
{
   double arr[];
   ArraySetAsSeries(arr, true);
   int copied = CopyClose(symbol, tf, index, 1, arr);
   if(copied > 0) return arr[0];
   return 0;
}

datetime GetTime(string symbol, ENUM_TIMEFRAMES tf, int index)
{
   datetime arr[];
   ArraySetAsSeries(arr, true);
   int copied = CopyTime(symbol, tf, index, 1, arr);
   if(copied > 0) return arr[0];
   return 0;
}

//+------------------------------------------------------------------+
//| Convert ENUM_TIMEFRAMES to printable String                      |
//+------------------------------------------------------------------+
string StringTimeframe(ENUM_TIMEFRAMES tf)
{
   switch(tf)
   {
      case PERIOD_M1:  return "M1";
      case PERIOD_M2:  return "M2";
      case PERIOD_M3:  return "M3";
      case PERIOD_M4:  return "M4";
      case PERIOD_M5:  return "M5";
      case PERIOD_M6:  return "M6";
      case PERIOD_M10: return "M10";
      case PERIOD_M12: return "M12";
      case PERIOD_M15: return "M15";
      case PERIOD_M20: return "M20";
      case PERIOD_M30: return "M30";
      case PERIOD_H1:  return "H1";
      case PERIOD_H2:  return "H2";
      case PERIOD_H3:  return "H3";
      case PERIOD_H4:  return "H4";
      case PERIOD_H6:  return "H6";
      case PERIOD_H8:  return "H8";
      case PERIOD_H12: return "H12";
      case PERIOD_D1:  return "D1";
      case PERIOD_W1:  return "W1";
      case PERIOD_MN1: return "MN1";
      default:         return "CURRENT";
   }
}

//+------------------------------------------------------------------+
//| Update Flicker-Free Chart Dashboard                              |
//+------------------------------------------------------------------+
void UpdateDashboard()
{
   string text = "==================================================\n";
   text += "                   TRAFFIC SELL EA                \n";
   text += "==================================================\n";
   text += " Symbol: " + _Symbol + " | Timeframe: " + StringTimeframe(InpTimeframe) + "\n";
   text += " Magic Number: " + IntegerToString(InpMagicNumber) + "\n";
   text += "--------------------------------------------------\n";
   text += " Swing High Filter: " + (InpUseSwingHighFilter ? "ON (Lookback: " + IntegerToString(InpSwingLookback) + ")" : "OFF") + "\n";
   text += " Risk-to-Reward Ratio: 1:" + DoubleToString(InpRiskRewardRatio, 1) + "\n";
   text += " Fixed Volume: " + DoubleToString(InpLotSize, 2) + "\n";
   text += " Dynamic Lot Sizing: " + (InpUseDynamicLot ? "ON (" + DoubleToString(InpRiskPercent, 1) + "%)" : "OFF") + "\n";
   text += "--------------------------------------------------\n";
   text += " Setup Active: " + (m_setup_active ? "YES" : "NO") + "\n";
   if(m_setup_active)
   {
      text += "   Setup Candle Time: " + TimeToString(m_setup_time, TIME_DATE|TIME_MINUTES) + "\n";
      text += "   Breakout Trigger Level: " + DoubleToString(m_breakout_level, _Digits) + "\n";
      text += "   Stop Loss Level: " + DoubleToString(m_sl_level, _Digits) + "\n";
   }
   text += " Active Positions (EA): " + IntegerToString(GetActivePositionsCount()) + "\n";
   text += "--------------------------------------------------\n";
   text += " Account Balance: " + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + "\n";
   text += " Free Margin: " + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2) + "\n";
   text += " Equity: " + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2) + "\n";
   text += "==================================================\n";

   Comment(text);
}
