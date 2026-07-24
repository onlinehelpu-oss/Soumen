//+------------------------------------------------------------------+
//|                                LuxAlgo_Swing_Candle_Patterns_EA.mq5 |
//|                                  Copyright 2024, Expert Developer|
//|                                                                  |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, Expert Developer"
#property link      ""
#property version   "1.00"
#property strict

// Include standard trade library
#include <Trade\Trade.mqh>

/*
   LuxAlgo Swing Candle Patterns EA

   Pattern Descriptions:
   - Hammer: The hammer candlestick pattern is formed of a short body with a long lower wick,
     and is found at the bottom of a downward trend. A hammer shows that although there
     were selling pressures during the day, ultimately a strong buying pressure drove
     the price back up.

   - Inverted Hammer: The inverted hammer is a similar pattern than the hammer pattern.
     The only difference being that the upper wick is long, while the lower wick is short.
     It indicates a buying pressure, followed by a selling pressure that was not strong
     enough to drive the market price down. The inverse hammer suggests that buyers will
     soon have control of the market.

   - Bullish Engulfing: The bullish engulfing pattern is formed of two candlesticks.
     The first candle is a short red body that is completely engulfed by a larger green candle.
     Though the second day opens lower than the first, the bullish market pushes the price up,
     culminating in an obvious win for buyers.

   - Hanging Man: The hanging man is the bearish equivalent of a hammer; it has the same shape
     but forms at the end of an uptrend. It indicates that there was a significant sell-off
     during the day, but that buyers were able to push the price up again. The large sell-off
     is often seen as an indication that the bulls are losing control of the market.

   - Shooting Star: The shooting star is the same shape as the inverted hammer, but is formed
     in an uptrend: it has a small lower body, and a long upper wick. Usually, the market will
     gap slightly higher on opening and rally to an intra-day high before closing at a price
     just above the open – like a star falling to the ground.

   - Bearish Engulfing: A bearish engulfing pattern occurs at the end of an uptrend.
     The first candle has a small green body that is engulfed by a subsequent long red candle.
     It signifies a peak or slowdown of price movement, and is a sign of an impending market
     downturn. The lower the second candle goes, the more significant the trend is likely to be.
*/

//--- Input Parameters
input group "=== LuxAlgo Strategy Settings ==="
input int      InpLength          = 21;       // Swing Pivot Length (default 21)
input double   InpMinCandlePoints = 100.0;    // Min Candle Range in Points to Avoid Tiny Candles (0 to disable)

input group "=== Risk Management Settings ==="
input double   InpRiskReward      = 2.0;      // Take Profit Risk-to-Reward Ratio (e.g. 1.5, 2.0)
input double   InpLotSize         = 0.1;      // Trade Lot Size
input ulong    InpMagic           = 123456;   // Magic Number
input ulong    InpSlippage        = 30;       // Slippage in Points

//--- Global Variables
CTrade   m_trade;
datetime m_last_bar_time = 0;
string   m_last_signal_msg = "None";

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // Set magic number
   m_trade.SetExpertMagicNumber(InpMagic);

   // Configure trade filling mode dynamically
   SetTradeFillingMode(m_trade);

   // Verify inputs
   if(InpLength <= 0)
   {
      Print("Invalid Input: InpLength must be greater than 0.");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpRiskReward <= 0)
   {
      Print("Invalid Input: InpRiskReward must be greater than 0.");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpLotSize <= 0)
   {
      Print("Invalid Input: InpLotSize must be greater than 0.");
      return INIT_PARAMETERS_INCORRECT;
   }

   Print("LuxAlgo Swing Candle Patterns EA Initialized Successfully.");
   return INIT_SUCCEEDED;
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
   // Display current dashboard on chart
   UpdateDashboard();

   // Only run on the open of a new bar
   if(!IsNewBar()) return;

   // Strict rule: One position at a time
   if(HasOpenPosition()) return;

   // Fetch rate data. We need enough bars to cover the swing length and pivots
   int required_bars = 2 * InpLength + 5;
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(Symbol(), Period(), 0, required_bars, rates);
   if(copied < required_bars)
   {
      PrintFormat("Warning: Insufficient bars copied (%d/%d). Waiting for more data.", copied, required_bars);
      return;
   }

   // Determine indices:
   // rates[0] is the current active bar (incomplete).
   // rates[1] is the bar that just closed.
   // The Pivot Candidate bar is at index P = InpLength + 1.
   // The Next Immediate bar after the pivot is index InpLength (which is P - 1).
   int P = InpLength + 1;

   // Calculate candle geometry for Pivot Candidate
   double o = rates[P].open;
   double h = rates[P].high;
   double l = rates[P].low;
   double c = rates[P].close;
   double d = MathAbs(c - o);
   double total_range = h - l;

   // Filter out tiny candles if rule is enabled
   if(InpMinCandlePoints > 0 && total_range < InpMinCandlePoints * SymbolInfoDouble(Symbol(), SYMBOL_POINT))
   {
      return;
   }

   // Pattern checks
   bool is_hammer   = (MathMin(o, c) - l > d) && (h - MathMax(o, c) < d);
   bool is_ihammer  = (h - MathMax(o, c) > d) && (MathMin(o, c) - l < d);
   bool is_bulleng  = (rates[P].close > rates[P].open) && (rates[P+1].close < rates[P+1].open) && (rates[P].close > rates[P+1].open) && (rates[P].open < rates[P+1].close);

   bool is_hanging  = (MathMin(o, c) - l > d) && (h - MathMax(o, c) < d);
   bool is_shooting = (h - MathMax(o, c) > d) && (MathMin(o, c) - l < d);
   bool is_beareng  = (rates[P].close < rates[P].open) && (rates[P+1].close > rates[P+1].open) && (rates[P].close < rates[P+1].open) && (rates[P].open > rates[P+1].close);

   // Check for Swing Low (Pivot Low)
   if(IsPivotLow(rates, P, InpLength))
   {
      string pattern_title = "";
      if(is_hammer)        pattern_title = "Hammer";
      else if(is_ihammer)  pattern_title = "Inverted Hammer";
      else if(is_bulleng)  pattern_title = "Bullish Engulfing";

      if(pattern_title != "")
      {
         m_last_signal_msg = StringFormat("Swing Low Pattern [%s] confirmed on Bar [%d]", pattern_title, P);
         Print(m_last_signal_msg);

         // Trigger BUY if the next immediate candle (index InpLength) broke the signal candle high
         if(rates[InpLength].high > rates[P].high)
         {
            ExecuteBuy(rates[P].high, rates[P].low);
         }
         else
         {
            PrintFormat("Breakout failed: Next candle high (%.5f) did not exceed signal high (%.5f).", rates[InpLength].high, rates[P].high);
         }
      }
   }

   // Check for Swing High (Pivot High)
   if(IsPivotHigh(rates, P, InpLength))
   {
      string pattern_title = "";
      if(is_hanging)        pattern_title = "Hanging Man";
      else if(is_shooting)  pattern_title = "Shooting Star";
      else if(is_beareng)   pattern_title = "Bearish Engulfing";

      if(pattern_title != "")
      {
         m_last_signal_msg = StringFormat("Swing High Pattern [%s] confirmed on Bar [%d]", pattern_title, P);
         Print(m_last_signal_msg);

         // Trigger SELL if the next immediate candle (index InpLength) broke the signal candle low
         if(rates[InpLength].low < rates[P].low)
         {
            ExecuteSell(rates[P].high, rates[P].low);
         }
         else
         {
            PrintFormat("Breakout failed: Next candle low (%.5f) did not fall below signal low (%.5f).", rates[InpLength].low, rates[P].low);
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Pivot High Check                                                 |
//+------------------------------------------------------------------+
bool IsPivotHigh(const MqlRates &rates[], int P, int len)
{
   double val = rates[P].high;
   // Compare with left bars (older, larger indices)
   for(int i = 1; i <= len; i++)
   {
      if(rates[P + i].high > val) return false;
   }
   // Compare with right bars (newer, smaller indices)
   for(int i = 1; i <= len; i++)
   {
      if(rates[P - i].high >= val) return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| Pivot Low Check                                                  |
//+------------------------------------------------------------------+
bool IsPivotLow(const MqlRates &rates[], int P, int len)
{
   double val = rates[P].low;
   // Compare with left bars (older, larger indices)
   for(int i = 1; i <= len; i++)
   {
      if(rates[P + i].low < val) return false;
   }
   // Compare with right bars (newer, smaller indices)
   for(int i = 1; i <= len; i++)
   {
      if(rates[P - i].low <= val) return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| Execute BUY Position                                             |
//+------------------------------------------------------------------+
void ExecuteBuy(double signal_high, double signal_low)
{
   double ask = SymbolInfoDouble(Symbol(), SYMBOL_ASK);
   double lot = NormalizeVolume(InpLotSize);

   // Stop Loss is signal candle pattern low
   double sl = NormalizeDouble(signal_low, Digits());
   double sl_dist = ask - sl;

   if(sl_dist <= 0)
   {
      Print("Cannot open BUY: Stop Loss is above or equal to Entry Price.");
      return;
   }

   // Take Profit calculated by Risk-to-Reward Ratio
   double tp = NormalizeDouble(ask + InpRiskReward * sl_dist, Digits());

   if(!IsSLTPValid(ORDER_TYPE_BUY, ask, sl, tp))
   {
      Print("Cannot open BUY: Stop Loss or Take Profit violates stop level requirements.");
      return;
   }

   PrintFormat("Opening BUY Position: Lot=%.2f, Entry=%.5f, SL=%.5f, TP=%.5f", lot, ask, sl, tp);
   m_trade.Buy(lot, Symbol(), ask, sl, tp, "LuxAlgo Swing Buy");
}

//+------------------------------------------------------------------+
//| Execute SELL Position                                            |
//+------------------------------------------------------------------+
void ExecuteSell(double signal_high, double signal_low)
{
   double bid = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   double lot = NormalizeVolume(InpLotSize);

   // Stop Loss is signal candle pattern high
   double sl = NormalizeDouble(signal_high, Digits());
   double sl_dist = sl - bid;

   if(sl_dist <= 0)
   {
      Print("Cannot open SELL: Stop Loss is below or equal to Entry Price.");
      return;
   }

   // Take Profit calculated by Risk-to-Reward Ratio
   double tp = NormalizeDouble(bid - InpRiskReward * sl_dist, Digits());

   if(!IsSLTPValid(ORDER_TYPE_SELL, bid, sl, tp))
   {
      Print("Cannot open SELL: Stop Loss or Take Profit violates stop level requirements.");
      return;
   }

   PrintFormat("Opening SELL Position: Lot=%.2f, Entry=%.5f, SL=%.5f, TP=%.5f", lot, bid, sl, tp);
   m_trade.Sell(lot, Symbol(), bid, sl, tp, "LuxAlgo Swing Sell");
}

//+------------------------------------------------------------------+
//| Check for open positions with the same Magic Number            |
//+------------------------------------------------------------------+
bool HasOpenPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(PositionGetSymbol(i) == Symbol())
      {
         if(PositionGetInteger(POSITION_MAGIC) == InpMagic)
         {
            return true;
         }
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Detect New Candle Open                                           |
//+------------------------------------------------------------------+
bool IsNewBar()
{
   datetime rates_time[];
   if(CopyTime(Symbol(), Period(), 0, 1, rates_time) < 1) return false;
   if(rates_time[0] != m_last_bar_time)
   {
      m_last_bar_time = rates_time[0];
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Set trade filling mode explicitly                                |
//+------------------------------------------------------------------+
void SetTradeFillingMode(CTrade &trade)
{
   uint filling = (uint)SymbolInfoInteger(Symbol(), SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0)
   {
      trade.SetTypeFilling(ORDER_FILLING_FOK);
   }
   else if((filling & SYMBOL_FILLING_IOC) != 0)
   {
      trade.SetTypeFilling(ORDER_FILLING_IOC);
   }
   else
   {
      trade.SetTypeFilling(ORDER_FILLING_RETURN);
   }
}

//+------------------------------------------------------------------+
//| Volume Normalization                                             |
//+------------------------------------------------------------------+
double NormalizeVolume(double volume)
{
   double min_lot = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MIN);
   double max_lot = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MAX);
   double step_lot = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_STEP);

   double normalized = MathRound(volume / step_lot) * step_lot;
   if(normalized < min_lot) normalized = min_lot;
   if(normalized > max_lot) normalized = max_lot;

   return NormalizeDouble(normalized, 2);
}

//+------------------------------------------------------------------+
//| Validate SL & TP against broker stops level                      |
//+------------------------------------------------------------------+
bool IsSLTPValid(ENUM_ORDER_TYPE order_type, double entry, double sl, double tp)
{
   double stop_level = SymbolInfoInteger(Symbol(), SYMBOL_TRADE_STOPS_LEVEL) * SymbolInfoDouble(Symbol(), SYMBOL_POINT);
   if(stop_level == 0) return true;

   if(order_type == ORDER_TYPE_BUY)
   {
      if(sl > 0 && entry - sl < stop_level) return false;
      if(tp > 0 && tp - entry < stop_level) return false;
   }
   else if(order_type == ORDER_TYPE_SELL)
   {
      if(sl > 0 && sl - entry < stop_level) return false;
      if(tp > 0 && entry - tp < stop_level) return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| Visual dashboard update                                          |
//+------------------------------------------------------------------+
void UpdateDashboard()
{
   string dashboard = "==================================================\n" +
                      "   LUXALGO SWING CANDLE PATTERNS EA (MT5)\n" +
                      "==================================================\n" +
                      StringFormat("   Symbol: %s | Timeframe: %s\n", Symbol(), EnumToString(Period())) +
                      StringFormat("   Pivot Length: %d\n", InpLength) +
                      StringFormat("   Risk-to-Reward Ratio: 1 : %.1f\n", InpRiskReward) +
                      StringFormat("   Lot Size: %.2f | Magic Number: %I64u\n", InpLotSize, InpMagic) +
                      StringFormat("   Min Candle Points: %.1f\n", InpMinCandlePoints) +
                      "--------------------------------------------------\n" +
                      "   Last Signal/Status:\n" +
                      "   " + m_last_signal_msg + "\n" +
                      "==================================================\n";
   Comment(dashboard);
}
