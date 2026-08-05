//+------------------------------------------------------------------+
//|                    RegimeEMA_VWAP_Strategy_AllInOne.mq5           |
//|  Single-file EA: plots VWAP + Swing High/Low directly on the      |
//|  chart AND runs the short-side strategy - no separate indicator   |
//|  files required.                                                  |
//|                                                                    |
//|  RULES:                                                            |
//|   Signal candle:                                                  |
//|     - candle High crosses above / touches the Regime EMA          |
//|       AND candle closes back BELOW the EMA                        |
//|     - the signal candle must be RED  (close < open)                |
//|     - the candle immediately BEFORE it must be GREEN               |
//|     - the signal candle Close must be BELOW the VWAP computed     |
//|       on a user-selectable timeframe                              |
//|   Entry:                                                           |
//|     - Sell as soon as the very NEXT candle breaks below the        |
//|       signal candle's Low (checked tick by tick, not on close)     |
//|     - if that next candle closes without breaking the low, the     |
//|       signal is discarded                                          |
//|   Stop Loss  : the signal candle's High                            |
//|   Take Profit: the most recent confirmed Swing Low prior to the    |
//|                signal candle                                       |
//+------------------------------------------------------------------+
#property copyright "Custom strategy conversion - single file version"
#property version   "2.00"
#property strict

#include <Trade\Trade.mqh>

//--- VWAP anchor / source options
enum ENUM_VWAP_ANCHOR
  {
   ANCHOR_SESSION = 0,
   ANCHOR_WEEK    = 1,
   ANCHOR_MONTH   = 2,
   ANCHOR_QUARTER = 3,
   ANCHOR_YEAR    = 4
  };

enum ENUM_VWAP_SOURCE
  {
   SRC_HLC3  = 0,
   SRC_CLOSE = 1,
   SRC_OHLC4 = 2
  };

//--- Inputs -----------------------------------------------------------------
input group "=== Regime EMA / Signal timeframe ==="
input ENUM_TIMEFRAMES    InpTradeTimeframe = PERIOD_CURRENT; // Timeframe for EMA / signal candle / swings (PERIOD_CURRENT = follows the chart)
input int                 InpEMAPeriod      = 34;           // Regime EMA period (e.g. 15 / 21 / 50 / 200)
input ENUM_MA_METHOD      InpEMAMethod      = MODE_EMA;     // MA method
input ENUM_APPLIED_PRICE  InpEMAPrice       = PRICE_CLOSE;  // EMA applied price

input group "=== VWAP filter & plot ==="
input ENUM_TIMEFRAMES    InpVWAPTimeframe  = PERIOD_CURRENT; // Timeframe used for the VWAP (independent of trade TF; PERIOD_CURRENT = follows the chart)
input ENUM_VWAP_ANCHOR    InpVWAPAnchor     = ANCHOR_SESSION; // VWAP anchor period
input ENUM_VWAP_SOURCE     InpVWAPSource     = SRC_HLC3;    // VWAP source price
input color                InpVWAPColor      = clrDodgerBlue;// VWAP line color
input int                  InpVWAPHistoryBars= 300;         // How many bars of VWAP line to draw

input group "=== Swing Target & plot ==="
input int                 InpSwingLookback  = 14;           // Pivot lookback (bars each side)
input bool                 InpShowSwingHighs = true;        // Draw swing high markers
input bool                 InpShowSwingLows  = true;        // Draw swing low markers
input color                InpSwingHighColor = clrRed;
input color                InpSwingLowColor  = clrTeal;
input int                  InpSwingHistoryBars = 1000;       // How far back to scan/draw swings on init
input bool                 InpRequireSwingTarget = true;    // Skip trade if no valid swing low found

input group "=== Trade management ==="
input double               InpLots           = 0.10;
input ulong                 InpMagic          = 990034;
input bool                  InpDrawObjects    = true;       // Draw all chart visuals

//--- Trading object
CTrade trade;
int    g_emaHandle = INVALID_HANDLE;

//--- VWAP running state
double   g_vwapSumPV        = 0.0;
double   g_vwapSumV         = 0.0;
double   g_vwapPrevValue    = 0.0;
datetime g_vwapPrevTime     = 0;
datetime g_vwapLastProcessed= 0;

//--- Swing history (parallel arrays, chronological order)
datetime g_swingLowTimes[];
double   g_swingLowValues[];
datetime g_swingHighTimes[];
double   g_swingHighValues[];

//--- Strategy state
datetime g_lastBarTime             = 0;
datetime g_signalBarTime           = 0;
double   g_signalHigh              = 0.0;
double   g_signalLow               = 0.0;
bool     g_waitingForBreak         = false;
bool     g_entryTriggeredForSignal = false;
bool     g_stopsInvalidLogged      = false;

//--- Open-position tracking (for the SL/TP lines + "one trade at a time")
bool     g_positionLinesDrawn      = false;
ulong    g_trackedPositionTicket   = 0;

//+------------------------------------------------------------------+
double GetSourcePrice(double o, double h, double l, double c)
  {
   switch(InpVWAPSource)
     {
      case SRC_CLOSE: return c;
      case SRC_OHLC4: return (o + h + l + c) / 4.0;
      default:        return (h + l + c) / 3.0; // SRC_HLC3
     }
  }

//+------------------------------------------------------------------+
bool IsNewVWAPPeriod(datetime cur, datetime prev)
  {
   if(prev == 0)
      return true;

   MqlDateTime a, b;
   TimeToStruct(cur,  a);
   TimeToStruct(prev, b);

   switch(InpVWAPAnchor)
     {
      case ANCHOR_SESSION:
         return (a.day != b.day || a.mon != b.mon || a.year != b.year);

      case ANCHOR_WEEK:
        {
         int curDow  = (a.day_of_week == 0) ? 7 : a.day_of_week;
         int prevDow = (b.day_of_week == 0) ? 7 : b.day_of_week;
         datetime curWeekStart  = cur  - (curDow  - 1) * 86400;
         datetime prevWeekStart = prev - (prevDow - 1) * 86400;
         MqlDateTime wa, wb;
         TimeToStruct(curWeekStart,  wa);
         TimeToStruct(prevWeekStart, wb);
         return !(wa.year == wb.year && wa.mon == wb.mon && wa.day == wb.day);
        }

      case ANCHOR_MONTH:
         return (a.mon != b.mon || a.year != b.year);

      case ANCHOR_QUARTER:
        {
         int curQ  = (a.mon - 1) / 3;
         int prevQ = (b.mon - 1) / 3;
         return (curQ != prevQ || a.year != b.year);
        }

      case ANCHOR_YEAR:
         return (a.year != b.year);
     }
   return false;
  }

//+------------------------------------------------------------------+
void DrawVWAPSegment(datetime t1, double v1, datetime t2, double v2)
  {
   if(!InpDrawObjects)
      return;

   string name = "VWAP_seg_" + IntegerToString((int)t2);
   ObjectDelete(0, name);
   ObjectCreate(0, name, OBJ_TREND, 0, t1, v1, t2, v2);
   ObjectSetInteger(0, name, OBJPROP_COLOR, InpVWAPColor);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 2);
   ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
   ObjectSetInteger(0, name, OBJPROP_RAY_LEFT,  false);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
  }

//+------------------------------------------------------------------+
//| Build VWAP history + draw it once at start-up                    |
//+------------------------------------------------------------------+
void InitVWAPHistory()
  {
   int bars = Bars(_Symbol, InpVWAPTimeframe);
   if(bars <= 1)
      return;

   int count = MathMin(bars, InpVWAPHistoryBars + 500); // extra bars behind the draw window to anchor running sums
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   int copied = CopyRates(_Symbol, InpVWAPTimeframe, 0, count, rates);
   if(copied <= 0)
      return;

   ObjectsDeleteAll(0, "VWAP_seg_");

   g_vwapSumPV = 0.0;
   g_vwapSumV  = 0.0;
   double   prevVal  = 0.0;
   datetime prevTime  = 0;
   int drawStart = MathMax(1, copied - InpVWAPHistoryBars);

   for(int i = 0; i < copied; i++)
     {
      bool newPeriod = (i == 0) ? true : IsNewVWAPPeriod(rates[i].time, rates[i-1].time);
      if(newPeriod)
        {
         g_vwapSumPV = 0.0;
         g_vwapSumV  = 0.0;
        }

      double src = GetSourcePrice(rates[i].open, rates[i].high, rates[i].low, rates[i].close);
      double vol = (double)rates[i].real_volume;
      if(vol == 0.0)
         vol = (double)rates[i].tick_volume;

      g_vwapSumPV += src * vol;
      g_vwapSumV  += vol;

      double val = (g_vwapSumV > 0.0) ? (g_vwapSumPV / g_vwapSumV) : src;

      if(i >= drawStart)
         DrawVWAPSegment(rates[i-1].time, prevVal, rates[i].time, val);

      prevVal  = val;
      prevTime = rates[i].time;
     }

   g_vwapPrevValue     = prevVal;
   g_vwapPrevTime      = prevTime;
   g_vwapLastProcessed = prevTime;
  }

//+------------------------------------------------------------------+
//| Called every tick: extends the VWAP line whenever a new VWAP-     |
//| timeframe bar closes                                              |
//+------------------------------------------------------------------+
void UpdateVWAPIfNewBar()
  {
   datetime curTime = iTime(_Symbol, InpVWAPTimeframe, 0);
   if(curTime == g_vwapLastProcessed)
      return; // still the same forming bar, nothing to append yet

   double o = iOpen(_Symbol,  InpVWAPTimeframe, 1);
   double h = iHigh(_Symbol,  InpVWAPTimeframe, 1);
   double l = iLow(_Symbol,   InpVWAPTimeframe, 1);
   double c = iClose(_Symbol, InpVWAPTimeframe, 1);
   long   tv = iVolume(_Symbol, InpVWAPTimeframe, 1);
   datetime t1 = iTime(_Symbol, InpVWAPTimeframe, 1);

   bool newPeriod = IsNewVWAPPeriod(t1, g_vwapPrevTime);
   if(newPeriod)
     {
      g_vwapSumPV = 0.0;
      g_vwapSumV  = 0.0;
     }

   double src = GetSourcePrice(o, h, l, c);
   double vol = (double)tv;
   g_vwapSumPV += src * vol;
   g_vwapSumV  += vol;

   double val = (g_vwapSumV > 0.0) ? (g_vwapSumPV / g_vwapSumV) : src;

   DrawVWAPSegment(g_vwapPrevTime, g_vwapPrevValue, t1, val);

   g_vwapPrevValue     = val;
   g_vwapPrevTime      = t1;
   g_vwapLastProcessed = curTime;
  }

//+------------------------------------------------------------------+
//| Live (still-forming-bar) VWAP value, used for the entry filter    |
//+------------------------------------------------------------------+
double GetLiveVWAPValue()
  {
   datetime t0 = iTime(_Symbol, InpVWAPTimeframe, 0);
   bool newPeriod = IsNewVWAPPeriod(t0, g_vwapPrevTime);

   double sumPV = newPeriod ? 0.0 : g_vwapSumPV;
   double sumV  = newPeriod ? 0.0 : g_vwapSumV;

   double o = iOpen(_Symbol,  InpVWAPTimeframe, 0);
   double h = iHigh(_Symbol,  InpVWAPTimeframe, 0);
   double l = iLow(_Symbol,   InpVWAPTimeframe, 0);
   double c = iClose(_Symbol, InpVWAPTimeframe, 0); // last traded price
   long   tv = iVolume(_Symbol, InpVWAPTimeframe, 0);

   double src = GetSourcePrice(o, h, l, c);
   sumPV += src * (double)tv;
   sumV  += (double)tv;

   return (sumV > 0.0) ? (sumPV / sumV) : src;
  }

//+------------------------------------------------------------------+
void AppendSwingLow(datetime t, double v)
  {
   int n = ArraySize(g_swingLowTimes);
   ArrayResize(g_swingLowTimes,  n + 1);
   ArrayResize(g_swingLowValues, n + 1);
   g_swingLowTimes[n]  = t;
   g_swingLowValues[n] = v;

   if(InpDrawObjects && InpShowSwingLows)
     {
      string name = "SwingL_" + IntegerToString((int)t);
      ObjectDelete(0, name);
      ObjectCreate(0, name, OBJ_ARROW, 0, t, v);
      ObjectSetInteger(0, name, OBJPROP_ARROWCODE, 233);
      ObjectSetInteger(0, name, OBJPROP_COLOR, InpSwingLowColor);
      ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_TOP);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
     }
  }

void AppendSwingHigh(datetime t, double v)
  {
   int n = ArraySize(g_swingHighTimes);
   ArrayResize(g_swingHighTimes,  n + 1);
   ArrayResize(g_swingHighValues, n + 1);
   g_swingHighTimes[n]  = t;
   g_swingHighValues[n] = v;

   if(InpDrawObjects && InpShowSwingHighs)
     {
      string name = "SwingH_" + IntegerToString((int)t);
      ObjectDelete(0, name);
      ObjectCreate(0, name, OBJ_ARROW, 0, t, v);
      ObjectSetInteger(0, name, OBJPROP_ARROWCODE, 234);
      ObjectSetInteger(0, name, OBJPROP_COLOR, InpSwingHighColor);
      ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_BOTTOM);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
     }
  }

//+------------------------------------------------------------------+
//| Build swing high/low history + draw it once at start-up          |
//+------------------------------------------------------------------+
void InitSwingHistory()
  {
   ArrayResize(g_swingLowTimes,   0);
   ArrayResize(g_swingLowValues,  0);
   ArrayResize(g_swingHighTimes,  0);
   ArrayResize(g_swingHighValues, 0);

   int length = MathMax(1, InpSwingLookback);
   int bars   = Bars(_Symbol, InpTradeTimeframe);
   if(bars <= 2 * length + 1)
      return;

   int count = MathMin(bars, InpSwingHistoryBars + 2 * length + 5);
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   int copied = CopyRates(_Symbol, InpTradeTimeframe, 0, count, rates);
   if(copied <= 0)
      return;

   ObjectsDeleteAll(0, "SwingH_");
   ObjectsDeleteAll(0, "SwingL_");

   int lastPivot = copied - length - 1;
   for(int i = length; i <= lastPivot; i++)
     {
      bool isHigh = true, isLow = true;
      for(int k = i - length; k <= i + length; k++)
        {
         if(k == i)
            continue;
         if(rates[k].high >= rates[i].high)
            isHigh = false;
         if(rates[k].low <= rates[i].low)
            isLow = false;
         if(!isHigh && !isLow)
            break;
        }
      if(isHigh)
         AppendSwingHigh(rates[i].time, rates[i].high);
      if(isLow)
         AppendSwingLow(rates[i].time, rates[i].low);
     }
  }

//+------------------------------------------------------------------+
//| Called once per newly closed trade-timeframe bar: checks whether  |
//| the bar that just became old enough (length bars behind) is a     |
//| confirmed swing high/low                                          |
//+------------------------------------------------------------------+
void UpdateSwingsOnNewBar()
  {
   int length = MathMax(1, InpSwingLookback);
   int shiftC = length + 1; // candidate bar: now has "length" closed bars on both sides

   datetime timeC = iTime(_Symbol, InpTradeTimeframe, shiftC);
   if(timeC == 0)
      return;

   double highC = iHigh(_Symbol, InpTradeTimeframe, shiftC);
   double lowC  = iLow(_Symbol,  InpTradeTimeframe, shiftC);

   bool isHigh = true, isLow = true;
   for(int k = shiftC - length; k <= shiftC + length; k++)
     {
      if(k == shiftC)
         continue;
      double hk = iHigh(_Symbol, InpTradeTimeframe, k);
      double lk = iLow(_Symbol,  InpTradeTimeframe, k);
      if(hk >= highC)
         isHigh = false;
      if(lk <= lowC)
         isLow = false;
      if(!isHigh && !isLow)
         break;
     }

   if(isHigh)
      AppendSwingHigh(timeC, highC);
   if(isLow)
      AppendSwingLow(timeC, lowC);
  }

//+------------------------------------------------------------------+
double GetPreviousSwingLow(datetime beforeTime)
  {
   int n = ArraySize(g_swingLowTimes);
   for(int i = n - 1; i >= 0; i--)
     {
      if(g_swingLowTimes[i] <= beforeTime)
         return g_swingLowValues[i];
     }
   return -1.0;
  }

//+------------------------------------------------------------------+
void DrawMarker(string name, datetime t, double price, int arrowCode, color clr, string text)
  {
   if(!InpDrawObjects)
      return;

   ObjectDelete(0, name);
   ObjectCreate(0, name, OBJ_ARROW, 0, t, price);
   ObjectSetInteger(0, name, OBJPROP_ARROWCODE, arrowCode);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 2);

   string lblName = name + "_lbl";
   ObjectDelete(0, lblName);
   ObjectCreate(0, lblName, OBJ_TEXT, 0, t, price);
   ObjectSetString(0, lblName, OBJPROP_TEXT, text);
   ObjectSetInteger(0, lblName, OBJPROP_COLOR, clr);
  }

//+------------------------------------------------------------------+
//| True while this EA already has an open position on the symbol    |
//| (enforces "one trade at a time")                                  |
//+------------------------------------------------------------------+
bool HasOpenPosition()
  {
   if(!PositionSelect(_Symbol))
      return false;
   if(PositionGetInteger(POSITION_MAGIC) != (long)InpMagic)
      return false;
   return true;
  }

//+------------------------------------------------------------------+
//| Draw / refresh the SL and TP horizontal lines for the live trade  |
//+------------------------------------------------------------------+
void DrawPositionLines(double slPrice, double tpPrice)
  {
   if(!InpDrawObjects)
      return;

   string slName = "pos_SL_line";
   string tpName = "pos_TP_line";

   ObjectDelete(0, slName);
   ObjectCreate(0, slName, OBJ_HLINE, 0, 0, slPrice);
   ObjectSetInteger(0, slName, OBJPROP_COLOR, clrRed);
   ObjectSetInteger(0, slName, OBJPROP_STYLE, STYLE_DASH);
   ObjectSetInteger(0, slName, OBJPROP_WIDTH, 1);
   ObjectSetString(0, slName, OBJPROP_TEXT, "SL " + DoubleToString(slPrice, _Digits));
   ObjectSetInteger(0, slName, OBJPROP_SELECTABLE, false);

   if(tpPrice > 0.0)
     {
      ObjectDelete(0, tpName);
      ObjectCreate(0, tpName, OBJ_HLINE, 0, 0, tpPrice);
      ObjectSetInteger(0, tpName, OBJPROP_COLOR, clrLime);
      ObjectSetInteger(0, tpName, OBJPROP_STYLE, STYLE_DASH);
      ObjectSetInteger(0, tpName, OBJPROP_WIDTH, 1);
      ObjectSetString(0, tpName, OBJPROP_TEXT, "TP " + DoubleToString(tpPrice, _Digits));
      ObjectSetInteger(0, tpName, OBJPROP_SELECTABLE, false);
     }

   g_positionLinesDrawn = true;
  }

void RemovePositionLines()
  {
   ObjectDelete(0, "pos_SL_line");
   ObjectDelete(0, "pos_TP_line");
   g_positionLinesDrawn = false;
  }

//+------------------------------------------------------------------+
//| Keep the SL/TP lines in sync with the live position each tick:    |
//| draw them while a position is open, remove them once it closes    |
//| (also picks up broker-side SL/TP modifications, e.g. trailing).   |
//+------------------------------------------------------------------+
void SyncPositionLines()
  {
   if(HasOpenPosition())
     {
      double slPrice = PositionGetDouble(POSITION_SL);
      double tpPrice = PositionGetDouble(POSITION_TP);
      g_trackedPositionTicket = (ulong)PositionGetInteger(POSITION_TICKET);
      DrawPositionLines(slPrice, tpPrice);
     }
   else if(g_positionLinesDrawn)
     {
      RemovePositionLines();
      g_trackedPositionTicket = 0;
     }
  }

//+------------------------------------------------------------------+
//| Checks the entry price / SL / TP against the broker's minimum     |
//| stop distance (SYMBOL_TRADE_STOPS_LEVEL) so OrderSend doesn't get  |
//| rejected with "Invalid stops" (error 4756)                        |
//+------------------------------------------------------------------+
bool StopsAreValid(double entryPrice, double slPrice, double tpPrice)
  {
   long stopsLevelPoints = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   long freezeLevelPoints = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   long minPoints = MathMax(stopsLevelPoints, freezeLevelPoints);
   double minDistance = (double)minPoints * _Point;

   // Some brokers report 0 -- still enforce at least a few points of headroom
   if(minDistance <= 0.0)
      minDistance = 5.0 * _Point;

   if((slPrice - entryPrice) < minDistance)
      return false;

   if(tpPrice > 0.0 && (entryPrice - tpPrice) < minDistance)
      return false;

   return true;
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   g_emaHandle = iMA(_Symbol, InpTradeTimeframe, InpEMAPeriod, 0, InpEMAMethod, InpEMAPrice);
   if(g_emaHandle == INVALID_HANDLE)
     {
      Print("Failed to create Regime EMA handle. Error: ", GetLastError());
      return(INIT_FAILED);
     }

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetTypeFillingBySymbol(_Symbol);

   g_lastBarTime = iTime(_Symbol, InpTradeTimeframe, 0);

   InitVWAPHistory();
   InitSwingHistory();

   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   if(g_emaHandle != INVALID_HANDLE)
      IndicatorRelease(g_emaHandle);

   ObjectsDeleteAll(0, "VWAP_seg_");
   ObjectsDeleteAll(0, "SwingH_");
   ObjectsDeleteAll(0, "SwingL_");
   ObjectsDeleteAll(0, "sig_");
   ObjectsDeleteAll(0, "entry_");
   RemovePositionLines();
  }

//+------------------------------------------------------------------+
//| Called once per newly closed bar on InpTradeTimeframe             |
//+------------------------------------------------------------------+
void OnNewBar()
  {
   UpdateSwingsOnNewBar();

   //--- one trade at a time: don't look for / arm new signals while a position is open
   if(HasOpenPosition())
     {
      g_waitingForBreak = false;
      return;
     }

   double high1  = iHigh(_Symbol,  InpTradeTimeframe, 1);
   double low1   = iLow(_Symbol,   InpTradeTimeframe, 1);
   double open1  = iOpen(_Symbol,  InpTradeTimeframe, 1);
   double close1 = iClose(_Symbol, InpTradeTimeframe, 1);
   double open2  = iOpen(_Symbol,  InpTradeTimeframe, 2);
   double close2 = iClose(_Symbol, InpTradeTimeframe, 2);
   datetime time1 = iTime(_Symbol, InpTradeTimeframe, 1);

   long periodSecs = PeriodSeconds(InpTradeTimeframe);

   //--- invalidate a pending signal once its one allowed follow-up candle has closed
   if(g_waitingForBreak && !g_entryTriggeredForSignal)
     {
      if(time1 >= g_signalBarTime + periodSecs)
         g_waitingForBreak = false;
     }

   //--- detect a new signal candle using the bar that just closed
   double emaArr[];
   ArraySetAsSeries(emaArr, true);
   if(CopyBuffer(g_emaHandle, 0, 1, 1, emaArr) <= 0)
      return;
   double emaVal = emaArr[0];

   bool touchedOrCrossedAboveEma = (high1 > emaVal);
   bool closedBelowEma           = (close1 < emaVal);
   bool isRedCandle              = (close1 < open1);
   bool prevIsGreenCandle        = (close2 > open2);

   double vwapVal = GetLiveVWAPValue();
   bool isBelowVwap              = (close1 < vwapVal);

   if(touchedOrCrossedAboveEma && closedBelowEma && isRedCandle && prevIsGreenCandle && isBelowVwap)
     {
      g_signalBarTime           = time1;
      g_signalHigh               = high1;
      g_signalLow                = low1;
      g_waitingForBreak          = true;
      g_entryTriggeredForSignal  = false;
      g_stopsInvalidLogged       = false;

      DrawMarker("sig_" + TimeToString(time1), time1, high1 + 5 * _Point, 218, clrOrange, "SIGNAL");
     }
  }

//+------------------------------------------------------------------+
//| Checked on every tick: watches for the low-break entry trigger    |
//+------------------------------------------------------------------+
void CheckIntrabarBreak()
  {
   if(!g_waitingForBreak || g_entryTriggeredForSignal)
      return;

   //--- one trade at a time: never open a second position
   if(HasOpenPosition())
     {
      g_waitingForBreak = false;
      return;
     }

   long periodSecs = PeriodSeconds(InpTradeTimeframe);
   datetime curBarTime = iTime(_Symbol, InpTradeTimeframe, 0);

   if(curBarTime != g_signalBarTime + periodSecs)
      return; // only the candle right after the signal candle may trigger entry

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(bid > g_signalLow)
      return; // low not broken yet - keep waiting within this same candle

   //--- low IS broken and VWAP filter was already satisfied on the signal candle.
   //    signal gets exactly ONE order attempt so a rejection can't spam OrderSend.
   double tp = GetPreviousSwingLow(g_signalBarTime);
   if(InpRequireSwingTarget && tp <= 0.0)
     {
      Print("Signal skipped: no confirmed swing low found for a Take Profit target.");
      g_entryTriggeredForSignal = true;
      g_waitingForBreak         = false;
      return;
     }

   double sl      = g_signalHigh;
   double tpFinal = (tp > 0.0) ? tp : 0.0;

   //--- respect the broker's minimum stop distance so the order isn't rejected.
   //    Distance to SL grows as price keeps falling, so don't give up on the
   //    signal for this reason alone - just keep waiting (throttle the log so
   //    it doesn't repeat every tick), still within this same candle.
   if(!StopsAreValid(bid, sl, tpFinal))
     {
      if(!g_stopsInvalidLogged)
        {
         Print("Waiting: SL/TP too close to price for this broker's stop level ",
               "(bid=", bid, " sl=", sl, " tp=", tpFinal, "). Will keep watching this candle.");
         g_stopsInvalidLogged = true;
        }
      return;
     }

   g_entryTriggeredForSignal = true; // consumed - about to send the one and only order attempt
   g_waitingForBreak         = false;

   if(trade.Sell(InpLots, _Symbol, bid, sl, tpFinal, "RegimeEMA+VWAP short"))
     {
      DrawMarker("entry_" + TimeToString(curBarTime), curBarTime, bid, 234, clrRed, "SELL");
      DrawPositionLines(sl, tpFinal);
     }
   else
     {
      Print("Sell order failed. Error: ", GetLastError());
     }
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   UpdateVWAPIfNewBar();

   datetime curBarTime = iTime(_Symbol, InpTradeTimeframe, 0);
   if(curBarTime != g_lastBarTime)
     {
      g_lastBarTime = curBarTime;
      OnNewBar();
     }

   CheckIntrabarBreak();
   SyncPositionLines();
  }
//+------------------------------------------------------------------+
