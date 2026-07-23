//+------------------------------------------------------------------+
//|                                                XAUUSD_ProEA.mq5  |
//|                    Professional EA - Fully Rectified             |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"

#include <Trade/Trade.mqh>

CTrade Trade;

//========================= INPUTS ==================================//
input group "--- General Settings ---"
input ENUM_TIMEFRAMES TrendTF      = PERIOD_H1;
input ENUM_TIMEFRAMES EntryTF      = PERIOD_M5;
input int    MagicNumber           = 20260723;
input bool   OneTradeOnly          = true;

input group "--- Strategy Parameters ---"
input int    EMAPeriod             = 200;
input int    ATRPeriod             = 14;
input double RR                    = 2.0;
input double EntryBufferPoints     = 10;

input group "--- Candle Patterns ---"
input double HammerLowerWickPct    = 65.0;
input double HammerUpperWickPct    = 10.0;
input double HammerBodyPct         = 25.0;
input double ShootUpperWickPct     = 65.0;
input double ShootLowerWickPct     = 10.0;
input double ShootBodyPct          = 25.0;
input double MinCandlePoints       = 100;
input double MaxCandlePoints       = 3000;

input group "--- Trading Filters ---"
input double MinATRPoints          = 150;
input double MaxATRPoints          = 5000;
input int    MaxSpreadPoints       = 30;
input bool   EnableLondon          = true;
input bool   EnableNewYork         = true;
input int    LondonStartHour       = 8;
input int    LondonEndHour         = 17;
input int    NewYorkStartHour      = 13;
input int    NewYorkEndHour        = 22;

input group "--- Risk & Lot Management ---"
input bool   UseRiskPercent        = true;
input double RiskPercent           = 1.0;
input double FixedLot              = 0.10;
input bool   EnableBreakEven       = true;
input double BreakEvenRR           = 1.0;
input bool   EnableTrailing        = true;
input double TrailDistancePoints   = 300;
input double TrailStepPoints       = 50;
input int    MaxDailyTrades        = 5;
input double MaxDailyLossPercent   = 3.0;

//========================= GLOBALS =================================//
int emaHandle = INVALID_HANDLE;
int atrHandle = INVALID_HANDLE;

datetime lastCheckedBarTime = 0;
datetime lastResetDay = 0;
int DailyTrades = 0;

//========================= STRUCT ==================================//
struct CandleInfo
{
   double open,high,low,close;
   double range;
   double body;
   double upperWick;
   double lowerWick;
   double bodyPct;
   double upperPct;
   double lowerPct;
};

//========================= SIGNAL ==================================//
enum SignalType
{
   SIGNAL_NONE = 0,
   SIGNAL_BUY,
   SIGNAL_SELL
};

struct EntrySignal
{
   SignalType type;
   datetime   signalTime;
   double     entry;
   double     stop;
   double     target;
   bool       valid;
};

EntrySignal CurrentSignal;

//========================= NATIVE COMPATIBILITY HELPERS ============//
double GetClose(ENUM_TIMEFRAMES tf, int shift)
{
   double arr[1];
   if(CopyClose(_Symbol, tf, shift, 1, arr) == 1)
      return arr[0];
   return 0;
}

double GetOpen(ENUM_TIMEFRAMES tf, int shift)
{
   double arr[1];
   if(CopyOpen(_Symbol, tf, shift, 1, arr) == 1)
      return arr[0];
   return 0;
}

double GetHigh(ENUM_TIMEFRAMES tf, int shift)
{
   double arr[1];
   if(CopyHigh(_Symbol, tf, shift, 1, arr) == 1)
      return arr[0];
   return 0;
}

double GetLow(ENUM_TIMEFRAMES tf, int shift)
{
   double arr[1];
   if(CopyLow(_Symbol, tf, shift, 1, arr) == 1)
      return arr[0];
   return 0;
}

datetime GetTime(ENUM_TIMEFRAMES tf, int shift)
{
   datetime arr[1];
   if(CopyTime(_Symbol, tf, shift, 1, arr) == 1)
      return arr[0];
   return 0;
}

//========================= INIT ====================================//
int OnInit()
{
   emaHandle = iMA(_Symbol,TrendTF,EMAPeriod,0,MODE_EMA,PRICE_CLOSE);
   atrHandle = iATR(_Symbol,EntryTF,ATRPeriod);

   if(emaHandle==INVALID_HANDLE || atrHandle==INVALID_HANDLE)
   {
      Print("Failed to create indicator handles.");
      return(INIT_FAILED);
   }

   Trade.SetExpertMagicNumber(MagicNumber);
   SetTradeFillingMode();

   Print("XAUUSD Professional EA Loaded successfully.");
   return(INIT_SUCCEEDED);
}

//========================= DEINIT ==================================//
void OnDeinit(const int reason)
{
   if(emaHandle!=INVALID_HANDLE)
      IndicatorRelease(emaHandle);

   if(atrHandle!=INVALID_HANDLE)
      IndicatorRelease(atrHandle);
}

//========================= FILLING MODE ============================//
void SetTradeFillingMode()
{
   uint filling = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0)
      Trade.SetTypeFilling(ORDER_FILLING_FOK);
   else if((filling & SYMBOL_FILLING_IOC) != 0)
      Trade.SetTypeFilling(ORDER_FILLING_IOC);
   else
      Trade.SetTypeFilling(ORDER_FILLING_RETURN);
}

//========================= COMPLIANCE & DAILY LIMITS ===============//
void CheckDailyReset()
{
   MqlDateTime curr;
   TimeToStruct(TimeCurrent(), curr);
   curr.hour = 0;
   curr.min = 0;
   curr.sec = 0;
   datetime todayStart = StructToTime(curr);

   if(todayStart != lastResetDay)
   {
      DailyTrades = 0;
      lastResetDay = todayStart;
      Print("Daily stats reset for a new day: ", TimeToString(todayStart, TIME_DATE));
   }
}

int GetDailyTradesCount()
{
   MqlDateTime curr;
   TimeToStruct(TimeCurrent(), curr);
   curr.hour = 0;
   curr.min = 0;
   curr.sec = 0;
   datetime todayStart = StructToTime(curr);

   HistorySelect(todayStart, TimeCurrent());
   int totalDeals = HistoryDealsTotal();
   int count = 0;

   for(int i = 0; i < totalDeals; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket > 0)
      {
         long magic = HistoryDealGetInteger(ticket, DEAL_MAGIC);
         ENUM_DEAL_ENTRY entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(ticket, DEAL_ENTRY);
         if(magic == MagicNumber && entry == DEAL_ENTRY_IN)
         {
            count++;
         }
      }
   }
   return count;
}

void UpdateDailyStats()
{
   CheckDailyReset();
   int histTrades = GetDailyTradesCount();
   if(histTrades > DailyTrades)
      DailyTrades = histTrades;
}

bool CheckDailyLoss()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double maxLossAmount = balance * (MaxDailyLossPercent / 100.0);

   MqlDateTime curr;
   TimeToStruct(TimeCurrent(), curr);
   curr.hour = 0;
   curr.min = 0;
   curr.sec = 0;
   datetime todayStart = StructToTime(curr);

   HistorySelect(todayStart, TimeCurrent());
   int totalDeals = HistoryDealsTotal();
   double dailyRealizedPnl = 0;

   for(int i = 0; i < totalDeals; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket > 0)
      {
         long magic = HistoryDealGetInteger(ticket, DEAL_MAGIC);
         if(magic == MagicNumber)
         {
            dailyRealizedPnl += HistoryDealGetDouble(ticket, DEAL_PROFIT)
                              + HistoryDealGetDouble(ticket, DEAL_COMMISSION)
                              + HistoryDealGetDouble(ticket, DEAL_SWAP);
         }
      }
   }

   double floatingPnl = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionSelectByTicket(ticket))
      {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == MagicNumber)
         {
            floatingPnl += PositionGetDouble(POSITION_PROFIT);
         }
      }
   }

   double totalDailyPnl = dailyRealizedPnl + floatingPnl;
   if(totalDailyPnl < 0 && MathAbs(totalDailyPnl) >= maxLossAmount)
   {
      return false; // limit exceeded
   }

   return true;
}

bool AllowNewTrade()
{
   if(DailyTrades >= MaxDailyTrades)
      return false;

   return true;
}

void RegisterTrade()
{
   DailyTrades++;
}

//========================= LOT SIZE ================================//
double CalculateLot(double stopPoints)
{
   double lot = FixedLot;
   if(UseRiskPercent && stopPoints > 0)
   {
      double balance=AccountInfoDouble(ACCOUNT_BALANCE);
      double riskMoney=balance*RiskPercent/100.0;
      double tickValue=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
      double tickSize=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);

      lot=riskMoney/((stopPoints*_Point/tickSize)*tickValue);
   }

   double minLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double maxLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);

   lot=MathMax(minLot,MathMin(maxLot,lot));
   lot=MathFloor(lot/step)*step;
   return NormalizeDouble(lot,2);
}

//========================= FILTERS ================================//
bool GetEMA(double &ema0,double &ema1)
{
   double b[2];
   if(CopyBuffer(emaHandle,0,0,2,b)!=2)
      return false;
   ema0=b[0];
   ema1=b[1];
   return true;
}

bool TrendBuy()
{
   double e0,e1;
   if(!GetEMA(e0,e1)) return false;
   double close0 = GetClose(TrendTF, 0);
   return (close0 > e0 && e0 > e1);
}

bool TrendSell()
{
   double e0,e1;
   if(!GetEMA(e0,e1)) return false;
   double close0 = GetClose(TrendTF, 0);
   return (close0 < e0 && e0 < e1);
}

double CurrentATRPoints()
{
   double b[1];
   if(CopyBuffer(atrHandle,0,0,1,b)!=1)
      return 0;
   return b[0]/_Point;
}

bool ATRFilter()
{
   double atr = CurrentATRPoints();
   return (atr>=MinATRPoints && atr<=MaxATRPoints);
}

bool SpreadFilter()
{
   int spread=(int)SymbolInfoInteger(_Symbol,SYMBOL_SPREAD);
   return spread<=MaxSpreadPoints;
}

bool SessionFilter()
{
   MqlDateTime t;
   TimeToStruct(TimeCurrent(),t);

   bool london = EnableLondon &&
                 t.hour>=LondonStartHour &&
                 t.hour<LondonEndHour;

   bool ny = EnableNewYork &&
             t.hour>=NewYorkStartHour &&
             t.hour<NewYorkEndHour;

   return (london || ny);
}

bool AllowTrading()
{
   return SpreadFilter()
       && ATRFilter()
       && SessionFilter();
}

//========================= CANDLE PATTERNS =========================//
bool GetCandleInfo(ENUM_TIMEFRAMES tf,int shift,CandleInfo &c)
{
   c.open  = GetOpen(tf,shift);
   c.high  = GetHigh(tf,shift);
   c.low   = GetLow(tf,shift);
   c.close = GetClose(tf,shift);

   c.range = c.high-c.low;
   if(c.range<=0) return false;

   c.body = MathAbs(c.close-c.open);
   c.upperWick = c.high - MathMax(c.open,c.close);
   c.lowerWick = MathMin(c.open,c.close) - c.low;

   c.bodyPct  = (c.body/c.range)*100.0;
   c.upperPct = (c.upperWick/c.range)*100.0;
   c.lowerPct = (c.lowerWick/c.range)*100.0;

   return true;
}

bool IsBullish(CandleInfo &c){ return c.close>c.open; }
bool IsBearish(CandleInfo &c){ return c.close<c.open; }

bool IsHammer(CandleInfo &c)
{
   return IsBullish(c)
      && c.lowerPct>=HammerLowerWickPct
      && c.upperPct<=HammerUpperWickPct
      && c.bodyPct<=HammerBodyPct;
}

bool IsBullPinBar(CandleInfo &c)
{
   return c.lowerPct>=60.0 && c.bodyPct<=30.0;
}

bool IsShootingStar(CandleInfo &c)
{
   return IsBearish(c)
      && c.upperPct>=ShootUpperWickPct
      && c.lowerPct<=ShootLowerWickPct
      && c.bodyPct<=ShootBodyPct;
}

bool IsBearPinBar(CandleInfo &c)
{
   return c.upperPct>=60.0 && c.bodyPct<=30.0;
}

//========================= ENTRY & BREAKOUT LOGIC ==================//
bool HasOpenPosition()
{
   if(!OneTradeOnly)
      return false;

   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket>0 &&
         PositionSelectByTicket(ticket) &&
         PositionGetString(POSITION_SYMBOL)==_Symbol &&
         PositionGetInteger(POSITION_MAGIC)==MagicNumber)
         return true;
   }
   return false;
}

void ResetSignal()
{
   CurrentSignal.type=SIGNAL_NONE;
   CurrentSignal.valid=false;
   CurrentSignal.entry=0;
   CurrentSignal.stop=0;
   CurrentSignal.target=0;
   CurrentSignal.signalTime=0;
}

void SetBuySignal(double high,double low,double rr)
{
   CurrentSignal.type=SIGNAL_BUY;
   CurrentSignal.entry=high+EntryBufferPoints*_Point;
   CurrentSignal.stop=low;
   double risk=CurrentSignal.entry-low;
   CurrentSignal.target=CurrentSignal.entry+risk*rr;
   CurrentSignal.signalTime=GetTime(EntryTF, 1);
   CurrentSignal.valid=true;
}

void SetSellSignal(double high,double low,double rr)
{
   CurrentSignal.type=SIGNAL_SELL;
   CurrentSignal.entry=low-EntryBufferPoints*_Point;
   CurrentSignal.stop=high;
   double risk=high-CurrentSignal.entry;
   CurrentSignal.target=CurrentSignal.entry-risk*rr;
   CurrentSignal.signalTime=GetTime(EntryTF, 1);
   CurrentSignal.valid=true;
}

bool BuyBreakout()
{
   if(!CurrentSignal.valid || CurrentSignal.type!=SIGNAL_BUY)
      return false;

   return SymbolInfoDouble(_Symbol,SYMBOL_ASK)>=CurrentSignal.entry;
}

bool SellBreakout()
{
   if(!CurrentSignal.valid || CurrentSignal.type!=SIGNAL_SELL)
      return false;

   return SymbolInfoDouble(_Symbol,SYMBOL_BID)<=CurrentSignal.entry;
}

bool ExecuteEntry(double lots)
{
   if(HasOpenPosition())
      return false;

   Trade.SetExpertMagicNumber(MagicNumber);

   if(BuyBreakout())
   {
      bool ok=Trade.Buy(lots,_Symbol,0,
                        CurrentSignal.stop,
                        CurrentSignal.target,
                        "Hammer/PinBar Buy");
      if(ok)
      {
         ResetSignal();
         RegisterTrade();
      }
      return ok;
   }

   if(SellBreakout())
   {
      bool ok=Trade.Sell(lots,_Symbol,0,
                         CurrentSignal.stop,
                         CurrentSignal.target,
                         "ShootingStar Sell");
      if(ok)
      {
         ResetSignal();
         RegisterTrade();
      }
      return ok;
   }

   return false;
}

void ScanForSignal()
{
   datetime currentBarTime = GetTime(EntryTF, 0);
   if(currentBarTime == lastCheckedBarTime || currentBarTime == 0)
      return; // Already checked or invalid time

   lastCheckedBarTime = currentBarTime;

   // If a signal was valid but didn't break out on the immediate next candle, invalidate it.
   if(CurrentSignal.valid)
   {
      Print("New candle opened on ", EnumToString(EntryTF), ". Previous signal expired without breakout. Resetting signal.");
      ResetSignal();
   }

   // Check filters before scanning
   if(!AllowTrading())
      return;

   CandleInfo c;
   if(!GetCandleInfo(EntryTF, 1, c))
      return;

   double rangePoints = c.range / _Point;
   if(rangePoints < MinCandlePoints || rangePoints > MaxCandlePoints)
      return;

   // Scan for Hammer / Bull Pin Bar (BUY)
   if(IsHammer(c) || IsBullPinBar(c))
   {
      if(TrendBuy())
      {
         SetBuySignal(c.high, c.low, RR);
         Print("Hammer/PinBar BUY signal registered at ", TimeToString(CurrentSignal.signalTime));
      }
   }
   // Scan for Shooting Star / Bear Pin Bar (SELL)
   else if(IsShootingStar(c) || IsBearPinBar(c))
   {
      if(TrendSell())
      {
         SetSellSignal(c.high, c.low, RR);
         Print("ShootingStar/PinBar SELL signal registered at ", TimeToString(CurrentSignal.signalTime));
      }
   }
}

void EntryEngine()
{
   if(!CurrentSignal.valid)
      return;

   datetime currentBarTime = GetTime(EntryTF, 0);
   if(currentBarTime > CurrentSignal.signalTime + PeriodSeconds(EntryTF))
   {
      Print("Signal invalidated: Next candle closed without breakout.");
      ResetSignal();
      return;
   }

   if(!SpreadFilter())
      return;

   if(HasOpenPosition())
      return;

   UpdateDailyStats();
   if(!AllowNewTrade())
      return;

   if(!CheckDailyLoss())
      return;

   double stopPoints = MathAbs(CurrentSignal.entry - CurrentSignal.stop) / _Point;
   double lots = CalculateLot(stopPoints);

   ExecuteEntry(lots);
}

//========================= TRADE MANAGEMENT ========================//
void ManageBreakEven()
{
   if(!EnableBreakEven) return;

   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=MagicNumber) continue;

      ENUM_POSITION_TYPE type=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double open=PositionGetDouble(POSITION_PRICE_OPEN);
      double sl=PositionGetDouble(POSITION_SL);
      double tp=PositionGetDouble(POSITION_TP);

      double price=(type==POSITION_TYPE_BUY)?
         SymbolInfoDouble(_Symbol,SYMBOL_BID):
         SymbolInfoDouble(_Symbol,SYMBOL_ASK);

      double risk=MathAbs(open-sl);
      if(risk<=0) continue;

      if(type==POSITION_TYPE_BUY && price>=open+risk*BreakEvenRR && sl<open)
         Trade.PositionModify(ticket,open,tp);

      if(type==POSITION_TYPE_SELL && price<=open-risk*BreakEvenRR && (sl>open || sl==0))
         Trade.PositionModify(ticket,open,tp);
   }
}

void ManageTrailing()
{
   if(!EnableTrailing) return;

   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=MagicNumber) continue;

      ENUM_POSITION_TYPE type=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double sl=PositionGetDouble(POSITION_SL);
      double tp=PositionGetDouble(POSITION_TP);
      double open=PositionGetDouble(POSITION_PRICE_OPEN);

      if(type==POSITION_TYPE_BUY)
      {
         double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
         double newSL=bid-TrailDistancePoints*_Point;
         double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
         newSL = MathRound(newSL / tickSize) * tickSize;

         if(newSL>sl+TrailStepPoints*_Point && newSL>open)
            Trade.PositionModify(ticket,newSL,tp);
      }
      else
      {
         double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
         double newSL=ask+TrailDistancePoints*_Point;
         double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
         newSL = MathRound(newSL / tickSize) * tickSize;

         if((sl==0 || newSL<sl-TrailStepPoints*_Point) && (sl==0 || newSL<open))
            Trade.PositionModify(ticket,newSL,tp);
      }
   }
}

//========================= DASHBOARD ===============================//
void UpdateDashboard()
{
   if(MQLInfoInteger(MQL_TESTER) && !MQLInfoInteger(MQL_VISUAL_MODE))
      return; // Skip drawing on non-visual Strategy Tester to maximize speed

   string text = "==================================================\n";
   text += "            XAUUSD PROFESSIONAL EA DASHBOARD\n";
   text += "==================================================\n";
   text += " Account Balance: " + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + "\n";
   text += " Daily Trades: " + IntegerToString(DailyTrades) + " / " + IntegerToString(MaxDailyTrades) + "\n";
   text += " Spread: " + IntegerToString((int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD)) + " (Max: " + IntegerToString(MaxSpreadPoints) + ")\n";
   text += " Current ATR (Pts): " + DoubleToString(CurrentATRPoints(), 1) + "\n";
   text += " Trading Allowed: " + (AllowTrading() ? "YES" : "NO") + "\n";
   text += "--------------------------------------------------\n";
   text += " Current Signal State:\n";
   if(CurrentSignal.valid)
   {
      text += "   Type: " + (CurrentSignal.type == SIGNAL_BUY ? "BUY (Hammer/PinBar)" : "SELL (ShootingStar/PinBar)") + "\n";
      text += "   Entry Level: " + DoubleToString(CurrentSignal.entry, _Digits) + "\n";
      text += "   Stop Loss: " + DoubleToString(CurrentSignal.stop, _Digits) + "\n";
      text += "   Take Profit: " + DoubleToString(CurrentSignal.target, _Digits) + "\n";
      text += "   Signal Candle Time: " + TimeToString(CurrentSignal.signalTime) + "\n";
   }
   else
   {
      text += "   No Active Signal\n";
   }
   text += "==================================================\n";

   Comment(text);
}

//========================= TICK ====================================//
void OnTick()
{
   UpdateDailyStats();

   ManageBreakEven();
   ManageTrailing();

   ScanForSignal();
   EntryEngine();

   UpdateDashboard();
}
//+------------------------------------------------------------------+
