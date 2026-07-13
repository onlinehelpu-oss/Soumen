//+------------------------------------------------------------------+
//|                                         ShootingStar_Pro_EA.mq5 |
//|                                  Copyright 2024, Trading Robot   |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, Trading Robot"
#property link      "https://www.mql5.com"
#property version   "1.10"
#property strict
#property description "Professional Institutional-Grade Shooting Star EA"

//--- Include Standard Libraries
#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\AccountInfo.mqh>

//--- Enums
enum ENUM_RISK_MODE { RISK_FIXED_LOT, RISK_FIXED_AMOUNT, RISK_PERCENTAGE };
enum ENUM_TIMEFRAME_SELECT { TF_M1=PERIOD_M1, TF_M3=PERIOD_M3, TF_M5=PERIOD_M5, TF_M15=PERIOD_M15, TF_M30=PERIOD_M30, TF_H1=PERIOD_H1, TF_H4=PERIOD_H4, TF_D1=PERIOD_D1, TF_W1=PERIOD_W1 };

//--- Input Parameters
input group "--- STRATEGY SETTINGS ---"
input ENUM_TIMEFRAME_SELECT InpSignalTF = TF_M15;        // Signal Timeframe
input double InpMinUpperWickPct = 70.0;                 // Minimum Upper Wick %
input double InpMaxLowerWickPct = 10.0;                 // Maximum Lower Wick %
input double InpMaxBodyPct = 20.0;                      // Maximum Body %
input bool   InpRequireBearish = true;                  // Require Bearish Candle
input bool   InpRequirePrevBullish = true;              // Require Previous Bullish Candle
input int    InpMinCandleRange = 0;                     // Minimum Candle Range (Points)
input int    InpMaxCandleRange = 0;                     // Maximum Candle Range (Points, 0=Disabled)

input group "--- ENTRY & EXIT SETTINGS ---"
input double InpRRRatio = 3.0;                          // Risk Reward Ratio
input int    InpEntryBuffer = 0;                       // Entry Buffer (Points)
input int    InpSLBuffer = 0;                          // Stop Loss Buffer (Points)
input bool   InpInvalidateOnBreak = true;               // Invalidate Signal if High Broken

input group "--- MONEY MANAGEMENT ---"
input ENUM_RISK_MODE InpRiskMode = RISK_PERCENTAGE;     // Risk Management Mode
input double InpRiskAmount = 100.0;                     // Risk Amount (Fixed $)
input double InpRiskPercent = 1.0;                      // Risk Percent (%)
input double InpFixedLot = 0.1;                         // Fixed Lot Size

input group "--- FILTERS ---"
input bool   InpEnableEMA = false;                      // Enable EMA Filter
input int    InpEMAPeriod = 200;                        // EMA Period
input bool   InpEnableATR = false;                      // Enable ATR Filter
input int    InpATRPeriod = 14;                         // ATR Period
input double InpATRMultiplier = 1.5;                    // ATR Multiplier
input bool   InpEnableSpreadFilter = true;              // Enable Spread Filter
input int    InpMaxSpread = 0;                          // Maximum Spread (Points, 0=Disabled)
input bool   InpEnableSessionFilter = false;            // Enable Session Filter
input string InpLondonStart = "08:00";                  // London Start (HH:MM)
input string InpLondonEnd = "16:00";                    // London End (HH:MM)
input string InpNYStart = "13:00";                      // NY Start (HH:MM)
input string InpNYEnd = "21:00";                        // NY End (HH:MM)

input group "--- TRADE MANAGEMENT ---"
input bool   InpAutoBE = true;                          // Auto Break-even
input double InpBETriggerRR = 1.0;                      // Break-even Trigger (RR)
input bool   InpPartialTP = false;                      // Partial Take Profit
input double InpPartialClosePct = 50.0;                 // Partial Close %
input double InpPartialTriggerRR = 1.5;                 // Partial TP Trigger (RR)
input bool   InpTrailingStop = false;                   // Trailing Stop
input int    InpTrailingDist = 200;                    // Trailing Distance (Points)

input group "--- DAILY PROTECTION ---"
input double InpMaxDailyLoss = 1000.0;                  // Maximum Daily Loss ($)
input int    InpMaxDailyTrades = 5;                     // Maximum Daily Trades

input group "--- SYSTEM SETTINGS ---"
input int    InpMagicNum = 123456;                      // Magic Number
input int    InpSlippage = 30;                          // Slippage (Points)
input string InpTradeComment = "ShootingStarPro";       // Trade Comment
input bool   InpEnableDashboard = true;                 // Enable Dashboard
input bool   InpEnableAlerts = true;                    // Enable MT5 Alerts
input bool   InpEnablePush = false;                     // Enable Push Notifications
input bool   InpEnableEmail = false;                    // Enable Email Notifications
input bool   InpEnableBuy = true;                       // Enable Buy Trades (Hammer)
input bool   InpEnableSell = true;                      // Enable Sell Trades (Shooting Star)

//+------------------------------------------------------------------+
//| Class CSignalData                                                |
//+------------------------------------------------------------------+
struct SignalData {
   datetime time; double high; double low; double entryPrice; double stopLoss; double takeProfit;
   bool isSell; bool isValid; bool isTriggered; bool isInvalidated;
};

//+------------------------------------------------------------------+
//| Class CReportManager                                             |
//+------------------------------------------------------------------+
class CReportManager {
public:
   static void Export(string symbol, int magic, double initialBalance) {
      string fnCsv = "SSP_Report_" + symbol + ".csv";
      string fnHtml = "SSP_Report_" + symbol + ".html";
      double tp = 0, tl = 0, peak = initialBalance, cur = initialBalance, maxDD = 0;
      int w = 0, l = 0;
      HistorySelect(0, TimeCurrent());
      for(int i=0; i<HistoryDealsTotal(); i++) {
         ulong t = HistoryDealGetTicket(i);
         if(t>0 && HistoryDealGetInteger(t, DEAL_MAGIC) == magic && HistoryDealGetString(t, DEAL_SYMBOL) == symbol) {
            double p = HistoryDealGetDouble(t, DEAL_PROFIT) + HistoryDealGetDouble(t, DEAL_COMMISSION) + HistoryDealGetDouble(t, DEAL_SWAP);
            if(p > 0) { w++; tp += p; } else if(p < 0) { l++; tl += MathAbs(p); }
            cur += p; if(cur > peak) peak = cur;
            double dd = peak - cur; if(dd > maxDD) maxDD = dd;
         }
      }
      int total = w + l; double wr = (total > 0) ? (double)w / total * 100.0 : 0;
      double pf = (tl > 0) ? tp / tl : tp;
      int h = FileOpen(fnCsv, FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
      if(h != INVALID_HANDLE) {
         FileWrite(h, "Trades", "WinRate", "PF", "MaxDD");
         FileWrite(h, IntegerToString(total), DoubleToString(wr, 2), DoubleToString(pf, 2), DoubleToString(maxDD, 2));
         FileClose(h);
      }
      h = FileOpen(fnHtml, FILE_WRITE | FILE_ANSI);
      if(h != INVALID_HANDLE) {
         FileWriteString(h, "<html><body><h1>Report " + symbol + "</h1><p>Trades: " + IntegerToString(total) + "<br>Win Rate: " + DoubleToString(wr, 2) + "%<br>PF: " + DoubleToString(pf, 2) + "<br>MaxDD: " + DoubleToString(maxDD, 2) + "</p></body></html>");
         FileClose(h);
      }
   }
};

//+------------------------------------------------------------------+
//| Class CShootingStarEA                                            |
//+------------------------------------------------------------------+
class CShootingStarEA {
private:
   CTrade m_trade; CSymbolInfo m_symbol; CPositionInfo m_pos; CAccountInfo m_acc;
   SignalData m_signal; int m_tradesToday; double m_dailyPL; datetime m_lastReset;
   int m_ema, m_atr; bool m_partialClosed; string m_lastReason;
   void ResetDailyStats(); bool IsWithinSession(); bool CheckFilters(bool isSell); double CalculateLotSize(double d, double price, ENUM_ORDER_TYPE type);
   void ScanForSignal(); void HandleEntry(); void HandleExits(); void UpdateUI();
   void DrawObjects(); void RemoveObjects();
   void DrawLabel(string l, string t, int x, int y, color c);
   bool CheckSessionTime(string s, string e);
   double GetEMA(int i); double GetATR(int i);
   void SendNotifications(string m);
public:
   CShootingStarEA() : m_tradesToday(0), m_dailyPL(0), m_lastReset(0), m_partialClosed(false) { m_signal.isValid = false; }
   ~CShootingStarEA() { IndicatorRelease(m_ema); IndicatorRelease(m_atr); }
   int OnInit() {
      if(!m_symbol.Name(_Symbol)) return(INIT_FAILED);
      m_trade.SetExpertMagicNumber(InpMagicNum); m_trade.SetTypeFillingBySymbol(m_symbol.Name());
      m_trade.SetDeviationInPoints(InpSlippage);
      m_ema = iMA(m_symbol.Name(), (ENUM_TIMEFRAMES)InpSignalTF, InpEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
      m_atr = iATR(m_symbol.Name(), (ENUM_TIMEFRAMES)InpSignalTF, InpATRPeriod);
      ResetDailyStats(); EventSetTimer(1); return(INIT_SUCCEEDED);
   }
   void OnDeinit(const int r) { RemoveObjects(); if(MQLInfoInteger(MQL_TESTER)) CReportManager::Export(m_symbol.Name(), InpMagicNum, m_acc.Balance()); }
   void OnTick() {
      if(!m_symbol.RefreshRates()) return;
      static datetime lr = 0; if(TimeCurrent() - lr > 60) { ResetDailyStats(); lr = TimeCurrent(); }
      HandleExits();
   static datetime lt = 0;
   datetime ct = iTime(m_symbol.Name(), (ENUM_TIMEFRAMES)InpSignalTF, 0);

   if(ct > 0) {
      if(ct != lt) {
         lt = ct;
         ScanForSignal();
      }
      else if(lt == 0) {
         lt = ct;
         ScanForSignal();
      }
   }
      HandleEntry();
   }
   void OnTimer() { if(InpEnableDashboard) UpdateUI(); }
};

CShootingStarEA EA;
int OnInit() { return EA.OnInit(); }
void OnDeinit(const int r) { EA.OnDeinit(r); }
void OnTick() { EA.OnTick(); }
void OnTimer() { EA.OnTimer(); }

void CShootingStarEA::ResetDailyStats() {
   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt); dt.hour=0; dt.min=0; dt.sec=0; datetime ts = StructToTime(dt);
   if(m_lastReset < ts) { m_tradesToday=0; m_dailyPL=0; m_lastReset=ts; }
   double p=0; HistorySelect(ts, TimeCurrent());
   for(int i=0; i<HistoryDealsTotal(); i++) {
      ulong t = HistoryDealGetTicket(i);
      if(t>0 && HistoryDealGetInteger(t, DEAL_MAGIC) == InpMagicNum && HistoryDealGetString(t, DEAL_SYMBOL) == m_symbol.Name())
         p += HistoryDealGetDouble(t, DEAL_PROFIT) + HistoryDealGetDouble(t, DEAL_COMMISSION) + HistoryDealGetDouble(t, DEAL_SWAP);
   }
   m_dailyPL = p;
}

void CShootingStarEA::ScanForSignal() {
   m_signal.isValid = false; m_signal.isTriggered = false; m_signal.isInvalidated = false; m_partialClosed = false;
   m_lastReason = "Scanning...";
   MqlRates r[]; ArraySetAsSeries(r, true); if(CopyRates(m_symbol.Name(), (ENUM_TIMEFRAMES)InpSignalTF, 0, 3, r) < 3) return;
   double rg = r[1].high - r[1].low; if(rg <= 0) return;
   double point = m_symbol.Point();

   bool sellFound = false;
   if(InpEnableSell) {
      if(InpRequirePrevBullish && r[2].close <= r[2].open) m_lastReason = "Sell: Prev candle not bullish";
      else if(InpRequireBearish && r[1].close >= r[1].open) m_lastReason = "Sell: Current candle not bearish";
      else {
         double b=MathAbs(r[1].close-r[1].open), u=r[1].high-MathMax(r[1].open,r[1].close), l=MathMin(r[1].open,r[1].close)-r[1].low;
         double uP=(u/rg)*100, lP=(l/rg)*100, bP=(b/rg)*100;
         if(uP<InpMinUpperWickPct) m_lastReason = "Sell: Upper wick too small ("+DoubleToString(uP,1)+"%)";
         else if(lP>InpMaxLowerWickPct) m_lastReason = "Sell: Lower wick too large ("+DoubleToString(lP,1)+"%)";
         else if(bP>InpMaxBodyPct) m_lastReason = "Sell: Body too large ("+DoubleToString(bP,1)+"%)";
         else if(InpMinCandleRange>0 && rg<InpMinCandleRange*point) m_lastReason = "Sell: Range too small";
         else if(InpMaxCandleRange>0 && rg>InpMaxCandleRange*point) m_lastReason = "Sell: Range too large";
         else if(!CheckFilters(true)) m_lastReason = "Sell: Filters rejected";
         else {
            m_signal.isValid=true; m_signal.isSell=true; m_signal.time=r[1].time; m_signal.high=r[1].high; m_signal.low=r[1].low;
            m_signal.entryPrice=m_symbol.NormalizePrice(r[1].low - InpEntryBuffer*point); m_signal.stopLoss=m_symbol.NormalizePrice(r[1].high + InpSLBuffer*point);
            m_signal.takeProfit=m_symbol.NormalizePrice(m_signal.entryPrice - MathAbs(m_signal.entryPrice - m_signal.stopLoss)*InpRRRatio);
            DrawObjects(); m_lastReason = "Sell: Signal Detected!"; sellFound = true;
         }
      }
   }
   if(sellFound) return;

   if(InpEnableBuy) {
      if(InpRequirePrevBullish && r[2].close >= r[2].open) { m_lastReason = "Buy: Prev candle not bearish"; return; }
      if(InpRequireBearish && r[1].close <= r[1].open) { m_lastReason = "Buy: Current candle not bullish"; return; }
      double b=MathAbs(r[1].close-r[1].open), u=r[1].high-MathMax(r[1].open,r[1].close), l=MathMin(r[1].open,r[1].close)-r[1].low;
      double lP=(l/rg)*100, uP=(u/rg)*100, bP=(b/rg)*100;
      if(lP<InpMinUpperWickPct) { m_lastReason = "Buy: Lower wick too small ("+DoubleToString(lP,1)+"%)"; return; }
      if(uP>InpMaxLowerWickPct) { m_lastReason = "Buy: Upper wick too large ("+DoubleToString(uP,1)+"%)"; return; }
      if(bP>InpMaxBodyPct) { m_lastReason = "Buy: Body too large ("+DoubleToString(bP,1)+"%)"; return; }
      if(InpMinCandleRange>0 && rg<InpMinCandleRange*point) { m_lastReason = "Buy: Range too small"; return; }
      if(InpMaxCandleRange>0 && rg>InpMaxCandleRange*point) { m_lastReason = "Buy: Range too large"; return; }
      if(!CheckFilters(false)) { m_lastReason = "Buy: Filters rejected"; return; }
      m_signal.isValid=true; m_signal.isSell=false; m_signal.time=r[1].time; m_signal.high=r[1].high; m_signal.low=r[1].low;
      m_signal.entryPrice=m_symbol.NormalizePrice(r[1].high + InpEntryBuffer*point); m_signal.stopLoss=m_symbol.NormalizePrice(r[1].low - InpSLBuffer*point);
      m_signal.takeProfit=m_symbol.NormalizePrice(m_signal.entryPrice + MathAbs(m_signal.entryPrice - m_signal.stopLoss)*InpRRRatio);
      DrawObjects(); m_lastReason = "Buy: Signal Detected!";
   }
}

void CShootingStarEA::HandleEntry() {
   if(!m_signal.isValid || m_signal.isTriggered || m_signal.isInvalidated || PositionSelect(m_symbol.Name())) return;
   if(m_tradesToday >= InpMaxDailyTrades || m_dailyPL <= -InpMaxDailyLoss) return;
   double b=m_symbol.Bid(), a=m_symbol.Ask();
   if(InpInvalidateOnBreak && ((m_signal.isSell && b > m_signal.high) || (!m_signal.isSell && a < m_signal.low))) { m_signal.isInvalidated=true; RemoveObjects(); return; }
   double slD = MathAbs(m_signal.entryPrice - m_signal.stopLoss);
   if(m_signal.isSell && m_symbol.NormalizePrice(b) <= m_signal.entryPrice) {
      double lots = CalculateLotSize(slD, b, ORDER_TYPE_SELL);
      if(m_trade.Sell(lots, m_symbol.Name(), b, m_signal.stopLoss, m_signal.takeProfit, InpTradeComment)) { m_signal.isTriggered=true; m_tradesToday++; SendNotifications("SELL " + m_symbol.Name()); }
   } else if(!m_signal.isSell && m_symbol.NormalizePrice(a) >= m_signal.entryPrice) {
      double lots = CalculateLotSize(slD, a, ORDER_TYPE_BUY);
      if(m_trade.Buy(lots, m_symbol.Name(), a, m_signal.stopLoss, m_signal.takeProfit, InpTradeComment)) { m_signal.isTriggered=true; m_tradesToday++; SendNotifications("BUY " + m_symbol.Name()); }
   }
}

void CShootingStarEA::HandleExits() {
   if(!PositionSelect(m_symbol.Name()) || m_pos.Magic() != InpMagicNum) { m_partialClosed = false; return; }
   double e=m_pos.PriceOpen(), c=m_pos.PriceCurrent(), sl=m_pos.StopLoss(), tp=m_pos.TakeProfit(), slD=MathAbs(e-sl);
   if(slD<=0) return; double rr=MathAbs(c-e)/slD;
   if(InpAutoBE && rr>=InpBETriggerRR && ((m_pos.PositionType()==POSITION_TYPE_SELL && sl>e) || (m_pos.PositionType()==POSITION_TYPE_BUY && sl<e)))
      m_trade.PositionModify(m_symbol.Name(), e, tp);
   if(InpPartialTP && !m_partialClosed && rr>=InpPartialTriggerRR) {
      double vol = m_trade.CheckVolume(m_symbol.Name(), m_pos.Volume()*(InpPartialClosePct/100.0), c, (m_pos.PositionType()==POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY);
      if(vol >= m_symbol.LotsMin()) { if(m_trade.PositionClosePartial(m_pos.Ticket(), vol)) m_partialClosed = true; }
   }
   if(InpTrailingStop) {
      double d=InpTrailingDist*m_symbol.Point();
      if(m_pos.PositionType()==POSITION_TYPE_SELL) { double ns=m_symbol.NormalizePrice(m_symbol.Ask()+d); if((ns<sl||sl==0)&&ns<e-d) m_trade.PositionModify(m_symbol.Name(), ns, tp); }
      else { double ns=m_symbol.NormalizePrice(m_symbol.Bid()-d); if((ns>sl||sl==0)&&ns>e+d) m_trade.PositionModify(m_symbol.Name(), ns, tp); }
   }
}

void CShootingStarEA::DrawObjects() {
   string p = "SSP_"; RemoveObjects();
   ObjectCreate(0, p+"E", OBJ_HLINE, 0, 0, m_signal.entryPrice); ObjectSetInteger(0, p+"E", OBJPROP_COLOR, clrBlue);
   ObjectCreate(0, p+"S", OBJ_HLINE, 0, 0, m_signal.stopLoss); ObjectSetInteger(0, p+"S", OBJPROP_COLOR, clrRed);
   ObjectCreate(0, p+"T", OBJ_HLINE, 0, 0, m_signal.takeProfit); ObjectSetInteger(0, p+"T", OBJPROP_COLOR, clrGreen);
   if(m_signal.isSell) { ObjectCreate(0, p+"A", OBJ_ARROW_DOWN, 0, m_signal.time, m_signal.high + 10*m_symbol.Point()); ObjectSetInteger(0, p+"A", OBJPROP_COLOR, clrRed); }
   else { ObjectCreate(0, p+"A", OBJ_ARROW_UP, 0, m_signal.time, m_signal.low - 10*m_symbol.Point()); ObjectSetInteger(0, p+"A", OBJPROP_COLOR, clrGreen); }
}
void CShootingStarEA::RemoveObjects() { ObjectsDeleteAll(0, "SSP_"); }

void CShootingStarEA::UpdateUI() {
   string n = "SSP_D_"; int x = 10, y = 20, s = 15;
   DrawLabel(n+"1", "Balance: "+DoubleToString(m_acc.Balance(),2) + " (" + DoubleToString(m_acc.Equity(),2) + ")", x, y, clrWhite); y+=s;
   DrawLabel(n+"2", "Daily P/L: "+DoubleToString(m_dailyPL,2), x, y, m_dailyPL>=0?clrLime:clrRed); y+=s;
   DrawLabel(n+"3", "Trades Today: "+IntegerToString(m_tradesToday)+"/"+IntegerToString(InpMaxDailyTrades), x, y, clrWhite); y+=s;
   string sess = "None"; if(CheckSessionTime(InpLondonStart, InpLondonEnd)) sess = "London"; else if(CheckSessionTime(InpNYStart, InpNYEnd)) sess = "New York";
   DrawLabel(n+"4", "Session: "+sess + " | Spread: "+IntegerToString(m_symbol.Spread()), x, y, clrWhite); y+=s;
   string st = "Watching"; if(m_signal.isValid && !m_signal.isTriggered) st = "Signal!"; if(PositionSelect(m_symbol.Name())) st = "In Position";
   DrawLabel(n+"5", "Status: "+st, x, y, clrCyan); y+=s;
   DrawLabel(n+"6", "Last Result: "+m_lastReason, x, y, clrGray);
}
void CShootingStarEA::DrawLabel(string l, string t, int x, int y, color c) {
   if(ObjectFind(0,l)<0) ObjectCreate(0,l,OBJ_LABEL,0,0,0);
   ObjectSetString(0,l,OBJPROP_TEXT,t); ObjectSetInteger(0,l,OBJPROP_XDISTANCE,x); ObjectSetInteger(0,l,OBJPROP_YDISTANCE,y); ObjectSetInteger(0,l,OBJPROP_COLOR,c);
}

bool CShootingStarEA::IsWithinSession() {
   if(!InpEnableSessionFilter) return true;
   return CheckSessionTime(InpLondonStart, InpLondonEnd) || CheckSessionTime(InpNYStart, InpNYEnd);
}
bool CShootingStarEA::CheckSessionTime(string s, string e) {
   MqlDateTime dt; TimeToStruct(TimeCurrent(), dt);
   datetime st = StringToTime(IntegerToString(dt.year)+"."+IntegerToString(dt.mon)+"."+IntegerToString(dt.day)+" "+s);
   datetime et = StringToTime(IntegerToString(dt.year)+"."+IntegerToString(dt.mon)+"."+IntegerToString(dt.day)+" "+e);
   if(et<st) et+=24*3600; return (TimeCurrent()>=st && TimeCurrent()<=et);
}

bool CShootingStarEA::CheckFilters(bool isSell) {
   if(!IsWithinSession()) { m_lastReason = (isSell?"Sell":"Buy")+": Session restricted"; return false; }
   if(InpEnableSpreadFilter && InpMaxSpread > 0 && m_symbol.Spread() > InpMaxSpread) { m_lastReason = (isSell?"Sell":"Buy")+": Spread too high ("+IntegerToString(m_symbol.Spread())+")"; return false; }

   if(InpEnableEMA) {
      double e=GetEMA(1);
      if(e>0) {
         if(isSell && m_symbol.Bid() > e) { m_lastReason = "Sell: Price above EMA"; return false; }
         if(!isSell && m_symbol.Ask() < e) { m_lastReason = "Buy: Price below EMA"; return false; }
      }
   }
   if(InpEnableATR) {
      double a=GetATR(1); if(a<=0) return false;
      MqlRates r[];
      if(CopyRates(m_symbol.Name(),(ENUM_TIMEFRAMES)InpSignalTF,1,1,r)==1) {
         if((r[0].high-r[0].low) < (a * InpATRMultiplier)) { m_lastReason = (isSell?"Sell":"Buy")+": Range < ATR Multiplier"; return false; }
      }
   }
   return true;
}

double CShootingStarEA::CalculateLotSize(double d, double price, ENUM_ORDER_TYPE type) {
   if(d<=0) return m_symbol.LotsMin();
   double l = (InpRiskMode==RISK_FIXED_LOT) ? InpFixedLot : (InpRiskMode==RISK_FIXED_AMOUNT ? InpRiskAmount : m_acc.Balance()*InpRiskPercent/100.0) / (d*m_symbol.TickValue()/m_symbol.TickSize());
   l = m_trade.CheckVolume(m_symbol.Name(), l, price, type); double step=m_symbol.LotsStep(); l=MathFloor(l/step)*step;
   return MathMax(m_symbol.LotsMin(), MathMin(m_symbol.LotsMax(), l));
}

double CShootingStarEA::GetEMA(int i) { double b[]; ArraySetAsSeries(b,true); return (CopyBuffer(m_ema,0,i,1,b)>0)?b[0]:0; }
double CShootingStarEA::GetATR(int i) { double b[]; ArraySetAsSeries(b,true); return (CopyBuffer(m_atr,0,i,1,b)>0)?b[0]:0; }
void CShootingStarEA::SendNotifications(string m) { if(InpEnableAlerts) Alert(m); if(InpEnablePush) SendNotification(m); if(InpEnableEmail) SendMail("SSP", m); }
