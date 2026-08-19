//+------------------------------------------------------------------+
//|                                        ThreeWhiteSoldiers_EA.mq5 |
//|                                  Copyright 2025, Expert Advisor  |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2025"
#property link      "https://www.mql5.com"
#property version   "1.10"
#property description "Three White Soldiers BUY Strategy with W-Pattern Filter Expert Advisor for MT5"

#include <Trade\Trade.mqh>

//--- Enums
enum ENUM_STOP_LOSS_MODE
  {
   SL_MODE_SWING_LOW         = 0, // Swing Low
   SL_MODE_FIRST_CANDLE_LOW  = 1  // First Candle Low
  };

//--- Input Parameters
input group "--- Timeframe Settings ---"
input ENUM_TIMEFRAMES      InpSignalTimeframe = PERIOD_M15;    // Signal Timeframe

input group "--- W-Pattern (Double Bottom) Filter Settings ---"
input bool                 InpUseWPatternFilter     = true;   // Enable W-Pattern Filter
input int                  InpWPatternLookback      = 30;     // Lookback Bars for W-Pattern Detection
input double               InpWPatternTolerancePts  = 50.0;   // Max Diff between Bottom 1 and Bottom 2 (Points)
input double               InpMinWHeightPts         = 30.0;   // Min Height between Bottoms and Peak (Points)

input group "--- Stop Loss & Take Profit Settings ---"
input ENUM_STOP_LOSS_MODE  InpStopLossMode    = SL_MODE_FIRST_CANDLE_LOW; // Stop Loss Mode
input int                  InpSwingLowLookback = 10;          // Swing Low Lookback Period (bars)
input double               InpSLBufferPoints   = 10.0;        // SL Buffer (in Points)
input double               InpRiskReward       = 2.0;         // Risk to Reward Ratio (e.g. 1.0, 1.5, 2.0, 3.0)

input group "--- Risk & Trade Settings ---"
input double               InpLotSize         = 0.01;        // Lot Size (Minimum 0.01)
input bool                 InpOnePositionAtATime = true;     // Only One Open Position At A Time
input ulong                InpMagicNumber     = 333001;      // Magic Number
input ulong                InpSlippage        = 10;          // Slippage (in Points)

//--- Global Variables
CTrade         m_trade;
datetime       m_last_bar_time = 0;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
  {
   // Set magic number for trade operations
   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_trade.SetDeviationInPoints(InpSlippage);

   // Configure trade filling mode
   ConfigureFillingMode();

   // Validate inputs
   if(InpLotSize < 0.01)
     {
      Print("[WARNING] InpLotSize is below 0.01. Resetting to minimum 0.01.");
     }

   if(InpSwingLowLookback < 1)
     {
      Print("[ERROR] InpSwingLowLookback must be at least 1.");
      return(INIT_PARAMETERS_INCORRECT);
     }

   if(InpRiskReward <= 0)
     {
      Print("[ERROR] InpRiskReward must be greater than 0.");
      return(INIT_PARAMETERS_INCORRECT);
     }

   if(InpWPatternLookback < 10)
     {
      Print("[ERROR] InpWPatternLookback must be at least 10 bars.");
      return(INIT_PARAMETERS_INCORRECT);
     }

   PrintFormat("[INIT] Three White Soldiers EA initialized on %s. Signal Timeframe: %s | W-Filter: %s",
               _Symbol, EnumToString(InpSignalTimeframe), InpUseWPatternFilter ? "ENABLED" : "DISABLED");

   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   PrintFormat("[DEINIT] Three White Soldiers EA deinitialized (Reason code: %d)", reason);
  }

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
  {
   // Check if a new bar has opened on the signal timeframe
   datetime current_bar_time = iTime(_Symbol, InpSignalTimeframe, 0);
   if(current_bar_time == 0)
     {
      // History data not yet available
      return;
     }

   // Process strategy strictly once per new signal timeframe candle
   if(current_bar_time == m_last_bar_time)
     {
      return;
     }

   // Update last bar time so we only process once per candle completion
   m_last_bar_time = current_bar_time;

   // Check "One Position At A Time" rule
   if(InpOnePositionAtATime && HasOpenPosition())
     {
      return;
     }

   // Fetch completed candles data on the signal timeframe
   // Candle 1 = index 3
   // Candle 2 = index 2
   // Candle 3 = index 1 (most recently closed candle)
   MqlRates rates[];
   ArraySetAsSeries(rates, true);

   int req_lookback = MathMax(InpWPatternLookback + 5, InpSwingLowLookback + 5);
   req_lookback = MathMax(req_lookback, 10);

   if(CopyRates(_Symbol, InpSignalTimeframe, 0, req_lookback, rates) < req_lookback)
     {
      Print("[WARNING] Not enough history rates copied on timeframe ", EnumToString(InpSignalTimeframe));
      return;
     }

   // Rate indices:
   // rates[1] = Candle 3 (last completed candle)
   // rates[2] = Candle 2
   // rates[3] = Candle 1

   bool candle1_bullish = (rates[3].close > rates[3].open);
   bool candle2_bullish = (rates[2].close > rates[2].open) && (rates[2].close > rates[3].close);
   bool candle3_bullish = (rates[1].close > rates[1].open) && (rates[1].close > rates[2].close);

   // Check Three White Soldiers Pattern
   if(candle1_bullish && candle2_bullish && candle3_bullish)
     {
      // If W-Pattern filter is enabled, verify W-pattern formation starting at candle 1 / 2 / 3
      if(InpUseWPatternFilter)
        {
         if(!IsWPatternStarting(rates))
           {
            PrintFormat("[FILTER] Three White Soldiers pattern detected at %s, but W-Pattern filter NOT satisfied. Trade skipped.",
                        TimeToString(rates[1].time, TIME_DATE|TIME_MINUTES));
            return;
           }
        }

      PrintFormat("[SIGNAL] Three White Soldiers Pattern %s detected at %s! Candle 1 (O:%.5f, C:%.5f), Candle 2 (O:%.5f, C:%.5f), Candle 3 (O:%.5f, C:%.5f)",
                  InpUseWPatternFilter ? "(with W-Pattern Confirmation)" : "",
                  TimeToString(rates[1].time, TIME_DATE|TIME_MINUTES),
                  rates[3].open, rates[3].close,
                  rates[2].open, rates[2].close,
                  rates[1].open, rates[1].close);

      ExecuteBuyEntry(rates);
     }
  }

//+------------------------------------------------------------------+
//| Check if Three White Soldiers form at the start of W second leg  |
//+------------------------------------------------------------------+
bool IsWPatternStarting(const MqlRates &rates[])
  {
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int max_rates = ArraySize(rates);

   // Second bottom (Bottom 2) should be formed around Candle 1 (index 3) or near index 1..4
   // Find local minimum for Second Bottom (Bottom 2) around indices 1..5
   int bottom2_idx = 3; // Default to candle 1 low
   double bottom2_low = rates[3].low;

   for(int i = 1; i <= MathMin(5, max_rates - 1); i++)
     {
      if(rates[i].low < bottom2_low)
        {
         bottom2_low = rates[i].low;
         bottom2_idx = i;
        }
     }

   // Search backwards for the intervening peak (Neckline peak between Bottom 1 and Bottom 2)
   int peak_idx = -1;
   double peak_high = -1.0;

   // Peak must be strictly prior to bottom2_idx (e.g., index bottom2_idx + 2 to bottom2_idx + 15)
   int search_start = bottom2_idx + 2;
   int search_end = MathMin(bottom2_idx + 15, max_rates - 5);

   for(int i = search_start; i <= search_end; i++)
     {
      if(rates[i].high > peak_high)
        {
         peak_high = rates[i].high;
         peak_idx = i;
        }
     }

   if(peak_idx == -1) return false;

   // Search further backwards for First Bottom (Bottom 1) prior to peak_idx
   int bottom1_idx = -1;
   double bottom1_low = 999999.0;

   int b1_start = peak_idx + 2;
   int b1_end = MathMin(peak_idx + 15, MathMin(InpWPatternLookback, max_rates - 1));

   for(int i = b1_start; i <= b1_end; i++)
     {
      if(rates[i].low < bottom1_low)
        {
         bottom1_low = rates[i].low;
         bottom1_idx = i;
        }
     }

   if(bottom1_idx == -1) return false;

   // --- Validate W-Pattern Criteria ---

   // 1. Bottom 1 and Bottom 2 must be within the user-specified tolerance distance
   double bottom_diff_pts = MathAbs(bottom1_low - bottom2_low) / point;
   if(bottom_diff_pts > InpWPatternTolerancePts)
     {
      return false;
     }

   // 2. Minimum height requirement: Peak must be higher than both bottoms by at least InpMinWHeightPts
   double height1_pts = (peak_high - bottom1_low) / point;
   double height2_pts = (peak_high - bottom2_low) / point;

   if(height1_pts < InpMinWHeightPts || height2_pts < InpMinWHeightPts)
     {
      return false;
     }

   // 3. Three White Soldiers (rates[3], rates[2], rates[1]) must be moving UP from Bottom 2
   // Close of Candle 3 (rates[1].close) must be significantly higher than Bottom 2
   if(rates[1].close <= bottom2_low)
     {
      return false;
     }

   PrintFormat("[W-PATTERN CONFIRMED] Bottom1 (idx:%d, low:%.5f), Peak (idx:%d, high:%.5f), Bottom2 (idx:%d, low:%.5f). Diff: %.1f pts, Height: %.1f pts",
               bottom1_idx, bottom1_low, peak_idx, peak_high, bottom2_idx, bottom2_low, bottom_diff_pts, height2_pts);

   return true;
  }

//+------------------------------------------------------------------+
//| Execute Buy Entry at Market                                      |
//+------------------------------------------------------------------+
void ExecuteBuyEntry(const MqlRates &rates[])
  {
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   if(ask <= 0)
     {
      Print("[ERROR] Invalid Ask price.");
      return;
     }

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int digits   = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   // Calculate Stop Loss
   double raw_sl = 0.0;

   if(InpStopLossMode == SL_MODE_FIRST_CANDLE_LOW)
     {
      // Low of Candle 1 (index 3) minus buffer
      raw_sl = rates[3].low - (InpSLBufferPoints * point);
     }
   else if(InpStopLossMode == SL_MODE_SWING_LOW)
     {
      // Find lowest low over the swing lookback period
      double swing_low = rates[1].low;
      int lookback_limit = MathMin(ArraySize(rates) - 1, InpSwingLowLookback);

      for(int i = 1; i <= lookback_limit; i++)
        {
         if(rates[i].low < swing_low)
           {
            swing_low = rates[i].low;
           }
        }
      raw_sl = swing_low - (InpSLBufferPoints * point);
     }

   double sl = NormalizeDouble(raw_sl, digits);

   // Calculate Risk and Take Profit
   // Risk = Entry Price - Stop Loss
   double risk = ask - sl;
   if(risk <= 0)
     {
      PrintFormat("[ERROR] Invalid SL (%.5f) relative to Ask price (%.5f). Trade aborted.", sl, ask);
      return;
     }

   // Take Profit = Entry Price + (Risk * RiskReward)
   double raw_tp = ask + (risk * InpRiskReward);
   double tp = NormalizeDouble(raw_tp, digits);

   // Adjust SL/TP for broker minimum stop level requirement
   long stops_level = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double min_stop_dist = stops_level * point;

   if(min_stop_dist > 0)
     {
      if((ask - sl) < min_stop_dist)
        {
         sl = NormalizeDouble(ask - min_stop_dist, digits);
         risk = ask - sl;
         tp = NormalizeDouble(ask + (risk * InpRiskReward), digits);
         PrintFormat("[INFO] SL adjusted to comply with StopLevel. New SL: %.5f, New TP: %.5f", sl, tp);
        }
     }

   // Calculate lot size starting from minimum 0.01 lot
   double lot = NormalizeLotSize(InpLotSize);

   // Check free margin before sending order
   double required_margin = 0.0;
   if(!OrderCalcMargin(ORDER_TYPE_BUY, _Symbol, lot, ask, required_margin))
     {
      Print("[WARNING] OrderCalcMargin failed. Executing trade attempt anyway.");
     }
   else
     {
      double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
      if(required_margin > free_margin)
        {
         PrintFormat("[ERROR] Not enough free margin. Required: %.2f, Available: %.2f", required_margin, free_margin);
         return;
        }
     }

   // Execute Market Buy Order
   PrintFormat("[BUY EXECUTION] Opening BUY order: Lots: %.2f, Entry: %.5f, SL: %.5f, TP: %.5f (Risk: %.5f)",
               lot, ask, sl, tp, risk);

   if(m_trade.Buy(lot, _Symbol, ask, sl, tp, "Three White Soldiers BUY"))
     {
      PrintFormat("[SUCCESS] BUY Order placed successfully. Ticket: %I64u", m_trade.ResultOrder());
     }
   else
     {
      PrintFormat("[ERROR] BUY Order failed. Code: %d, Message: %s",
                  m_trade.ResultRetcode(), m_trade.ResultRetcodeDescription());
     }
  }

//+------------------------------------------------------------------+
//| Check if an open position exists for this symbol and magic       |
//+------------------------------------------------------------------+
bool HasOpenPosition()
  {
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
        {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
            PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
           {
            return true;
           }
        }
     }
   return false;
  }

//+------------------------------------------------------------------+
//| Normalize lot size based on broker specifications                |
//+------------------------------------------------------------------+
double NormalizeLotSize(double requested_lot)
  {
   double min_volume  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_volume  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step_volume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   // Enforce minimum 0.01 lot
   if(min_volume < 0.01) min_volume = 0.01;

   double lot = MathMax(requested_lot, min_volume);
   lot = MathMin(lot, max_volume);

   if(step_volume > 0)
     {
      lot = MathFloor(lot / step_volume) * step_volume;
     }

   return NormalizeDouble(lot, 2);
  }

//+------------------------------------------------------------------+
//| Configure appropriate execution filling mode                    |
//+------------------------------------------------------------------+
void ConfigureFillingMode()
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
