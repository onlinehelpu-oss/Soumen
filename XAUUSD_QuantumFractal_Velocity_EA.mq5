//+------------------------------------------------------------------+
//|                                XAUUSD_QuantumFractal_Velocity_EA |
//|                                                     Jules C.     |
//|                                              https://www.mql5.com|
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, Jules C."
#property link      "https://www.mql5.com"
#property version   "1.00"
#property strict

// Include Standard Library Trades
#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\PositionInfo.mqh>

//--- Expert Advisor Inputs ---
input string      Inp_EADesc                 = "--- QUANTUM FRACTAL VELOCITY SYSTEM ---"; // System Strategy Description
input group       "--- RISK & MONEY MANAGEMENT ---"
input double      Inp_FixedLotSize           = 0.1;           // Fixed Lot Size (if AutoRisk = 0)
input double      Inp_RiskPercent            = 1.0;           // Risk Percentage per Trade (0 = Disabled, uses Fixed Lot)
input double      Inp_MaxSpreadPoints        = 50.0;          // Max Allowed Spread in Points (1 point = 0.01 on Gold)
input double      Inp_SlippagePoints         = 30.0;          // Execution Slippage in Points
input ulong       Inp_MagicNumber            = 882026;        // EA Unique Magic Number

input group       "--- FRACTAL REGIME FILTER (HURST) ---"
input int         Inp_HurstPeriod            = 30;            // Period for Hurst Exponent Calculation (Bars)
input double      Inp_HurstTrendThreshold    = 0.56;          // Threshold (> this is Persistent Trend, buy breakouts)
input double      Inp_HurstMeanRevertThresh  = 0.44;          // Threshold (< this is Anti-Persistent, buy pullbacks)

input group       "--- DYNAMIC VOLATILITY BANDS ---"
input int         Inp_ATRPeriod              = 14;            // ATR Period for volatility scaling
input double      Inp_KeltnerMultiplier      = 2.0;           // Keltner Channel Volatility Multiplier
input ENUM_TIMEFRAMES Inp_SignalTimeframe    = PERIOD_H1;     // Analysis/Regime Identification Timeframe

input group       "--- RISK-REWARD RATIOS (ATR MULTIPLIERS) ---"
input double      Inp_SL_ATRMultiplier       = 1.5;           // Stop Loss ATR Multiplier
input double      Inp_TP_ATRMultiplier       = 3.0;           // Take Profit ATR Multiplier
input bool        Inp_EnableTrailingStop     = true;          // Enable ATR-based Trailing Stop
input double      Inp_TrailingTriggerATRMult = 1.0;           // Trailing Start Trigger ATR Multiplier
input double      Inp_TrailingStopATRMult    = 1.2;           // Trailing Distance ATR Multiplier

input group       "--- SESSION & TIME CONTROLS ---"
input bool        Inp_UseSessionFilter       = true;          // Restrict Entries to High Volume Sessions
input int         Inp_StartHourGMT           = 7;             // Session Start Hour (GMT/EET Broker Time - London Open)
input int         Inp_EndHourGMT             = 21;            // Session End Hour (GMT/EET Broker Time - NY Close)

//--- Global Variables ---
CTrade            m_trade;                                    // CTrade Execution Object
CSymbolInfo       m_symbol;                                   // CSymbolInfo Helper Object
CPositionInfo     m_position;                                 // CPositionInfo Helper Object
int               m_atr_handle         = INVALID_HANDLE;      // ATR Indicator Handle
double            m_points_scale       = 1.0;                 // Points conversion scale based on digits
bool              m_is_tester          = false;               // Cache for Strategy Tester state
bool              m_is_visual          = false;               // Cache for Visual Mode state

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // Check if we are running on XAUUSD / GOLD
   string symbol_name = Symbol();
   if(StringFind(symbol_name, "XAU") < 0 && StringFind(symbol_name, "GOLD") < 0)
   {
      Print("[WARNING] QuantumFractal EA is designed and optimized specifically for XAUUSD (GOLD). Running on a different symbol may produce suboptimal results.");
   }

   // Initialize Symbol Info Helper
   if(!m_symbol.Name(symbol_name))
   {
      Print("[ERROR] Failed to initialize CSymbolInfo for symbol: ", symbol_name);
      return(INIT_FAILED);
   }
   m_symbol.Refresh();

   // Set Magic Number for trade operations
   m_trade.SetExpertMagicNumber(Inp_MagicNumber);

   // Determine Broker Execution Mode & Filling Mode dynamically (Crucial for XM Netting/Hedging types)
   ConfigureFillingMode();

   // Scale Points configuration for Gold (usually 2 digits, i.e. 1 point = 0.01)
   m_points_scale = m_symbol.Point();

   // Cache Strategy Tester state to bypass live trade checks and heavy rendering
   m_is_tester = (bool)MQLInfoInteger(MQL_TESTER);
   m_is_visual = (bool)MQLInfoInteger(MQL_VISUAL_MODE);

   // Initialize ATR Indicator for Volatility Analysis
   m_atr_handle = iATR(symbol_name, Inp_SignalTimeframe, Inp_ATRPeriod);
   if(m_atr_handle == INVALID_HANDLE)
   {
      Print("[ERROR] Failed to initialize ATR indicator handle. OnInit aborted.");
      return(INIT_FAILED);
   }

   Print("[INIT SUCCESS] QuantumFractal EA Initialized. Magic: ", Inp_MagicNumber, " | Digits: ", m_symbol.Digits(), " | Pt Scale: ", m_points_scale);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   // Release indicator handles
   if(m_atr_handle != INVALID_HANDLE)
   {
      IndicatorRelease(m_atr_handle);
      m_atr_handle = INVALID_HANDLE;
   }
   Print("[DEINIT] QuantumFractal EA cleaned up and stopped. Reason: ", reason);
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // Refresh Symbol Market Data
   if(!m_symbol.RefreshRates())
   {
      return;
   }

   // Check spread filter (Bypass in Strategy Tester if OHLC/Open Price modes don't provide real-time spread logs)
   double current_spread = (m_symbol.Ask() - m_symbol.Bid()) / m_symbol.Point();
   if(!m_is_tester && current_spread > Inp_MaxSpreadPoints)
   {
      if(m_is_visual)
      {
         Comment("Spread too high: ", DoubleToString(current_spread, 1), " points.");
      }
      return;
   }

   // Manage trailing stops and active positions first
   ManageActivePositions();

   // Trade restriction checks
   if(!IsMarketSessionOpen()) return;
   if(HasOpenPosition()) return; // One position at a time rule

   // Verify we have completed bars available to prevent signal repainting
   datetime current_bar_time[];
   if(CopyTime(Symbol(), Inp_SignalTimeframe, 0, 2, current_bar_time) < 2)
   {
      return;
   }

   // Check and Execute Trading Strategy
   CheckStrategySignal();
}

//+------------------------------------------------------------------+
//| Dynamically configures the filling mode for CTrade               |
//+------------------------------------------------------------------+
void ConfigureFillingMode()
{
   uint filling_flags = (uint)SymbolInfoInteger(Symbol(), SYMBOL_FILLING_MODE);

   if((filling_flags & SYMBOL_FILLING_FOK) != 0)
   {
      m_trade.SetTypeFilling(ORDER_FILLING_FOK);
   }
   else if((filling_flags & SYMBOL_FILLING_IOC) != 0)
   {
      m_trade.SetTypeFilling(ORDER_FILLING_IOC);
   }
   else
   {
      m_trade.SetTypeFilling(ORDER_FILLING_RETURN);
   }
}

//+------------------------------------------------------------------+
//| Strategy Signal Evaluation & Execution                           |
//+------------------------------------------------------------------+
void CheckStrategySignal()
{
   // Retrieve ATR Value
   double atr_values[];
   ArraySetAsSeries(atr_values, true);
   if(CopyBuffer(m_atr_handle, 0, 1, 2, atr_values) < 2)
   {
      Print("[STRATEGY] Error copying ATR values. Execution bypassed.");
      return;
   }
   double current_atr = atr_values[0];
   if(current_atr <= 0) return;

   // Calculate Hurst Exponent on the signal timeframe using completed bars (bars 1 to Inp_HurstPeriod)
   double hurst = CalculateHurstExponent(Inp_SignalTimeframe, Inp_HurstPeriod);
   if(hurst < 0) return; // Error in calculation

   // Get Keltner Channels (using MA + ATR multiplier)
   double close_array[];
   ArraySetAsSeries(close_array, true);
   if(CopyClose(Symbol(), Inp_SignalTimeframe, 1, Inp_HurstPeriod, close_array) < Inp_HurstPeriod)
   {
      return;
   }

   // Compute Simple Moving Average of Close
   double sma = 0;
   for(int i = 0; i < Inp_HurstPeriod; i++)
   {
      sma += close_array[i];
   }
   sma /= (double)Inp_HurstPeriod;

   double upper_keltner = sma + (Inp_KeltnerMultiplier * current_atr);
   double lower_keltner = sma - (Inp_KeltnerMultiplier * current_atr);
   double last_close = close_array[0];

   // Decision logic based on Hurst Exponent Regimes
   bool signal_buy = false;
   bool signal_sell = false;
   string regime_desc = "";

   if(hurst >= Inp_HurstTrendThreshold)
   {
      // --- TREND PERSISTENT REGIME ---
      // Breakout Strategy: Trend is strong, buy when price breaks out of the upper Keltner channel, sell on lower breakout
      regime_desc = "Strong Trend Regime (Hurst >= " + DoubleToString(Inp_HurstTrendThreshold, 2) + ")";
      if(last_close > upper_keltner)
      {
         signal_buy = true;
      }
      else if(last_close < lower_keltner)
      {
         signal_sell = true;
      }
   }
   else if(hurst <= Inp_HurstMeanRevertThresh)
   {
      // --- MEAN-REVERTING REGIME ---
      // Pullback Strategy: Market oscillates, buy when price touches lower channel (oversold), sell when price touches upper channel (overbought)
      regime_desc = "Mean-Reverting Regime (Hurst <= " + DoubleToString(Inp_HurstMeanRevertThresh, 2) + ")";
      if(last_close < lower_keltner)
      {
         signal_buy = true;
      }
      else if(last_close > upper_keltner)
      {
         signal_sell = true;
      }
   }
   else
   {
      // --- RANDOM WALK REGIME ---
      // Sideways/No edge. Stand aside to preserve capital.
      regime_desc = "Random Walk Regime (No Clear Edge)";
   }

   // Display Diagnostic Dashboard on screen (Only if visual mode is active to prevent Strategy Tester lag)
   if(m_is_visual)
   {
      string comment_text = "=== QUANTUM FRACTAL VELOCITY SYSTEM ===\n" +
                            "Symbol: " + Symbol() + "\n" +
                            "Hurst Exponent: " + DoubleToString(hurst, 3) + " (" + regime_desc + ")\n" +
                            "ATR Volatility: " + DoubleToString(current_atr, 2) + " USD\n" +
                            "Keltner Upper: " + DoubleToString(upper_keltner, 2) + "\n" +
                            "Keltner Lower: " + DoubleToString(lower_keltner, 2) + "\n" +
                            "Last Close: " + DoubleToString(last_close, 2) + "\n" +
                            "=====================================";
      Comment(comment_text);
   }

   // Execute Trade
   if(signal_buy)
   {
      ExecuteBuyTrade(current_atr);
   }
   else if(signal_sell)
   {
      ExecuteSellTrade(current_atr);
   }
}

//+------------------------------------------------------------------+
//| Calculate Hurst Exponent using Rescaled Range (R/S) Method       |
//+------------------------------------------------------------------+
double CalculateHurstExponent(ENUM_TIMEFRAMES timeframe, int period)
{
   double closes[];
   ArraySetAsSeries(closes, true);

   // Copy historical close data (+1 to perform returns calculation)
   int copied = CopyClose(Symbol(), timeframe, 1, period + 1, closes);
   if(copied < period + 1)
   {
      return -1.0;
   }

   // 1. Calculate Logarithmic Returns
   double returns[];
   ArrayResize(returns, period);
   double mean_return = 0;

   for(int i = 0; i < period; i++)
   {
      returns[i] = MathLog(closes[i] / closes[i + 1]);
      mean_return += returns[i];
   }
   mean_return /= (double)period;

   // 2. Calculate Mean-Adjusted Deviations & Cumulative Deviation (Z)
   double cum_deviation[];
   ArrayResize(cum_deviation, period);
   double sum_deviation = 0;
   double max_z = -99999999.0;
   double min_z = 99999999.0;

   for(int i = 0; i < period; i++)
   {
      sum_deviation += (returns[i] - mean_return);
      cum_deviation[i] = sum_deviation;
      if(cum_deviation[i] > max_z) max_z = cum_deviation[i];
      if(cum_deviation[i] < min_z) min_z = cum_deviation[i];
   }

   // 3. Calculate Range (R)
   double range = max_z - min_z;

   // 4. Calculate Standard Deviation (S)
   double sum_variance = 0;
   for(int i = 0; i < period; i++)
   {
      sum_variance += MathPow(returns[i] - mean_return, 2);
   }
   double std_dev = MathSqrt(sum_variance / (double)period);

   if(std_dev == 0) return 0.5; // Avoid division by zero, defaults to random walk

   // 5. Calculate Rescaled Range (R/S)
   double rs_ratio = range / std_dev;

   // 6. Calculate Hurst Exponent (H = log(R/S) / log(N))
   double hurst = MathLog(rs_ratio) / MathLog((double)period);

   // Bound the Hurst Exponent dynamically to the theoretical limit [0, 1]
   if(hurst < 0.0) hurst = 0.0;
   if(hurst > 1.0) hurst = 1.0;

   return hurst;
}

//+------------------------------------------------------------------+
//| Execute BUY Order                                                |
//+------------------------------------------------------------------+
void ExecuteBuyTrade(double atr_val)
{
   double ask = m_symbol.Ask();
   double stop_loss = NormalizeDouble(ask - (Inp_SL_ATRMultiplier * atr_val), m_symbol.Digits());
   double take_profit = NormalizeDouble(ask + (Inp_TP_ATRMultiplier * atr_val), m_symbol.Digits());
   double lot_size = CalculateLotSize(ask - stop_loss);

   // Set Anti-Race Lock check to ensure no duplicates
   if(HasOpenPosition()) return;

   if(m_trade.Buy(lot_size, Symbol(), ask, stop_loss, take_profit, "QuantumFractal BUY"))
   {
      Print("[ORDER SUCCESS] Long Entry Triggered. Entry: ", ask, " | SL: ", stop_loss, " | TP: ", take_profit, " | Lots: ", lot_size);
   }
   else
   {
      Print("[ORDER FAILED] Long Entry failed. Error code: ", m_trade.ResultRetcode(), " Description: ", m_trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Execute SELL Order                                               |
//+------------------------------------------------------------------+
void ExecuteSellTrade(double atr_val)
{
   double bid = m_symbol.Bid();
   double stop_loss = NormalizeDouble(bid + (Inp_SL_ATRMultiplier * atr_val), m_symbol.Digits());
   double take_profit = NormalizeDouble(bid - (Inp_TP_ATRMultiplier * atr_val), m_symbol.Digits());
   double lot_size = CalculateLotSize(stop_loss - bid);

   // Set Anti-Race Lock check to ensure no duplicates
   if(HasOpenPosition()) return;

   if(m_trade.Sell(lot_size, Symbol(), bid, stop_loss, take_profit, "QuantumFractal SELL"))
   {
      Print("[ORDER SUCCESS] Short Entry Triggered. Entry: ", bid, " | SL: ", stop_loss, " | TP: ", take_profit, " | Lots: ", lot_size);
   }
   else
   {
      Print("[ORDER FAILED] Short Entry failed. Error code: ", m_trade.ResultRetcode(), " Description: ", m_trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Dynamic Position Lot Sizing Based on Account Balance & Risk %     |
//+------------------------------------------------------------------+
double CalculateLotSize(double sl_distance)
{
   if(Inp_RiskPercent <= 0 || sl_distance <= 0)
   {
      return NormalizeLotSize(Inp_FixedLotSize);
   }

   double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double tick_value = m_symbol.TickValue();
   double tick_size = m_symbol.TickSize();

   if(tick_value <= 0 || tick_size <= 0)
   {
      return NormalizeLotSize(Inp_FixedLotSize);
   }

   // Risk Formula: Position Size = (Balance * Risk%) / (SL in points * PointValue)
   double risk_amount = free_margin * (Inp_RiskPercent / 100.0);
   double sl_points = sl_distance / m_symbol.Point();
   double point_value = (tick_value / tick_size) * m_symbol.Point();

   double computed_lot = risk_amount / (sl_points * point_value);

   return NormalizeLotSize(computed_lot);
}

//+------------------------------------------------------------------+
//| Normalize Position Size According to Broker Volume Guidelines   |
//+------------------------------------------------------------------+
double NormalizeLotSize(double computed_lot)
{
   double min_lot = m_symbol.LotsMin();
   double max_lot = m_symbol.LotsMax();
   double lot_step = m_symbol.LotsStep();

   double normalized_lot = MathRound(computed_lot / lot_step) * lot_step;

   if(normalized_lot < min_lot) normalized_lot = min_lot;
   if(normalized_lot > max_lot) normalized_lot = max_lot;

   return NormalizeDouble(normalized_lot, 2);
}

//+------------------------------------------------------------------+
//| Manage Trailing Stop-Loss for Active Positions                    |
//+------------------------------------------------------------------+
void ManageActivePositions()
{
   if(!Inp_EnableTrailingStop) return;

   // Copy standard ATR value
   double atr_values[];
   ArraySetAsSeries(atr_values, true);
   if(CopyBuffer(m_atr_handle, 0, 0, 1, atr_values) < 1) return;
   double current_atr = atr_values[0];

   double trail_trigger = Inp_TrailingTriggerATRMult * current_atr;
   double trail_distance = Inp_TrailingStopATRMult * current_atr;

   int total_positions = PositionsTotal();
   for(int i = total_positions - 1; i >= 0; i--)
   {
      if(m_position.SelectByIndex(i))
      {
         if(m_position.Symbol() == Symbol() && m_position.Magic() == Inp_MagicNumber)
         {
            double entry_price = m_position.PriceOpen();
            double current_sl = m_position.StopLoss();

            if(m_position.PositionType() == POSITION_TYPE_BUY)
            {
               double bid = m_symbol.Bid();
               // Only trigger trailing if price has moved at least trailing trigger distance in our favor
               if(bid - entry_price > trail_trigger)
               {
                  double new_sl = NormalizeDouble(bid - trail_distance, m_symbol.Digits());
                  // Only modify if new SL is higher than current SL (or if current SL is 0)
                  if(new_sl > current_sl || current_sl == 0)
                  {
                     m_trade.PositionModify(m_position.Ticket(), new_sl, m_position.TakeProfit());
                  }
               }
            }
            else if(m_position.PositionType() == POSITION_TYPE_SELL)
            {
               double ask = m_symbol.Ask();
               // Only trigger trailing if price has moved at least trailing trigger distance in our favor
               if(entry_price - ask > trail_trigger)
               {
                  double new_sl = NormalizeDouble(ask + trail_distance, m_symbol.Digits());
                  // Only modify if new SL is lower than current SL (or if current SL is 0)
                  if(new_sl < current_sl || current_sl == 0)
                  {
                     m_trade.PositionModify(m_position.Ticket(), new_sl, m_position.TakeProfit());
                  }
               }
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Check if we already have an open position with our Magic Number |
//+------------------------------------------------------------------+
bool HasOpenPosition()
{
   int total_positions = PositionsTotal();
   for(int i = 0; i < total_positions; i++)
   {
      if(m_position.SelectByIndex(i))
      {
         if(m_position.Symbol() == Symbol() && m_position.Magic() == Inp_MagicNumber)
         {
            return true;
         }
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Verify Session/Time Controls                                     |
//+------------------------------------------------------------------+
bool IsMarketSessionOpen()
{
   if(!Inp_UseSessionFilter) return true;

   datetime current_time = TimeCurrent();
   MqlDateTime dt;
   TimeToStruct(current_time, dt);

   // Restrict weekend trading (Saturday and Sunday are closed anyway, but filter out)
   if(dt.day_of_week == 0 || dt.day_of_week == 6) return false;

   // Check if broker hours align with the specified session parameters
   if(dt.hour < Inp_StartHourGMT || dt.hour > Inp_EndHourGMT)
   {
      return false;
   }

   return true;
}
