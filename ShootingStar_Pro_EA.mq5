//+------------------------------------------------------------------+
//|                                         ShootingStar_Pro_EA.mq5 |
//|                                  Copyright 2024, Jules & Trading |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, Jules & Trading Robot"
#property link      "https://www.mql5.com"
#property version   "2.00"
#property strict
#property description "Professional Shooting Star & Hammer Strategy EA"
#property description "Optimized for Prop Firms and Quantitative Research"

//--- Include Trade library
#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\AccountInfo.mqh>

//--- ENUMS
enum ENUM_RISK_MODE {
   RISK_MODE_USD,    // Risk Amount ($)
   RISK_MODE_PCT,    // Risk Percentage (%)
   RISK_MODE_FIXED   // Fixed Lot Size
};

enum ENUM_SIGNAL_TF {
   TF_M1  = PERIOD_M1,
   TF_M3  = PERIOD_M3,
   TF_M5  = PERIOD_M5,
   TF_M15 = PERIOD_M15,
   TF_M30 = PERIOD_M30,
   TF_H1  = PERIOD_H1,
   TF_H4  = PERIOD_H4,
   TF_D1  = PERIOD_D1,
   TF_W1  = PERIOD_W1
};

enum ENUM_RR_SELECT {
   RR_1_0 = 10,   // 1.0
   RR_1_5 = 15,   // 1.5
   RR_2_0 = 20,   // 2.0
   RR_2_5 = 25,   // 2.5
   RR_3_0 = 30,   // 3.0
   RR_4_0 = 40,   // 4.0
   RR_5_0 = 50    // 5.0
};

//--- INPUT PARAMETERS
sinput group "=== RISK MANAGEMENT ==="
input double         InpRiskAmount     = 100.0;         // Risk Amount ($)
input double         InpRiskPercent    = 1.0;           // Risk Percentage (%)
input double         InpFixedLot       = 0.1;           // Fixed Lot Size
input bool           InpUseFixedLot    = false;         // Use Fixed Lot
input ENUM_RR_SELECT InpRRSelection    = RR_3_0;        // Risk Reward Ratio
input double         InpMaxDailyLoss   = 500.0;         // Maximum Daily Loss ($)
input int            InpMaxDailyTrades = 5;             // Maximum Daily Trades
input int            InpSlippage       = 30;            // Slippage (Points)
input int            InpMagicNumber    = 123456;        // Magic Number
input string         InpTradeComment   = "SS_Pro_EA";   // Trade Comment

sinput group "=== STRATEGY SETTINGS ==="
input ENUM_SIGNAL_TF InpTimeframe      = TF_M15;        // Signal Timeframe
input double InpMinUpperWickPct        = 70.0;          // Min Upper Wick Percentage
input double InpMaxLowerWickPct        = 10.0;          // Max Lower Wick Percentage
input double InpMaxBodyPct             = 20.0;          // Max Body Percentage
input bool   InpRequireBearish         = true;          // Require Bearish Candle (SS)
input double InpMinCandleRange         = 0;             // Minimum Candle Range (Points)
input double InpMaxCandleRange         = 0;             // Maximum Candle Range (Points)
input int    InpEntryBuffer            = 10;            // Entry Buffer (Points)
input int    InpStopLossBuffer         = 10;            // Stop Loss Buffer (Points)
input bool   InpEnableBuyTrades        = true;          // Enable Buy Trades (Hammer)
input bool   InpEnableSellTrades       = true;          // Enable Sell Trades (SS)
input bool   InpCancelOnInvalidation   = true;          // Cancel if price breaks high/low before entry

sinput group "=== ADVANCED TRADE MGMT ==="
input bool   InpAutoBreakeven          = false;         // Auto Breakeven (True/False)
input double InpBETriggerRR            = 1.0;           // Breakeven Trigger RR
input bool   InpPartialTP              = false;         // Partial Take Profit (True/False)
input double InpPartialClosePct        = 50.0;          // Partial Close Percentage
input double InpPartialTPTriggerRR     = 1.5;           // Partial TP Trigger RR
input bool   InpTrailingStop           = false;         // Trailing Stop (True/False)
input int    InpTrailingStopDistance   = 200;           // Trailing Stop Distance (Points)

sinput group "=== OPTIONAL FILTERS ==="
input bool   InpUseEMAFilter           = false;         // EMA Trend Filter
input int    InpEMAPeriod              = 200;           // EMA Period
input bool   InpUseATRFilter           = false;         // ATR Volatility Filter
input int    InpATRPeriod              = 14;            // ATR Period
input double InpMinATRMult             = 0.5;           // Min Candle Size (ATR Mult)
input double InpMaxSpread              = 50.0;          // Maximum Spread Allowed (Points)

sinput group "=== TRADING SESSIONS ==="
input bool   InpTradeLondon            = false;         // Enable London Session
input string InpLondonStart            = "08:00";       // London Start Time
input string InpLondonEnd              = "16:00";       // London End Time
input bool   InpTradeNY                = false;         // Enable New York Session
input string InpNYStart                = "13:00";       // New York Start Time
input string InpNYEnd                  = "21:00";       // New York End Time

sinput group "=== NOTIFICATIONS & VISUALS ==="
input bool   InpEnableDashboard        = true;          // Enable Dashboard
input bool   InpEnableNotifications    = true;          // Enable Notifications
input bool   InpEnablePush             = false;         // Enable Push Notifications
input bool   InpEnableEmail            = false;         // Enable Email Alerts
input color  InpColorSignal            = clrOrange;     // Signal Marker Color
input color  InpColorBox               = clrDimGray;    // Risk Reward Box Color

//--- GLOBAL VARIABLES
CTrade         m_trade;
CSymbolInfo    m_symbol;
CPositionInfo  m_position;
CAccountInfo   m_account;

datetime m_last_signal_time = 0;
bool     m_signal_active    = false;
int      m_signal_type      = 0; // 1=Buy, -1=Sell
double   m_signal_high      = 0;
double   m_signal_low       = 0;
double   m_signal_entry     = 0;
double   m_signal_sl        = 0;
double   m_signal_tp        = 0;

int      m_daily_trades     = 0;
datetime m_last_daily_reset = 0;
double   m_daily_profit     = 0;

int      m_handle_ema       = INVALID_HANDLE;
int      m_handle_atr       = INVALID_HANDLE;

string m_csv_filename = "SS_Research_Report.csv";

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   if(!m_symbol.Name(_Symbol)) return INIT_FAILED;
   m_trade.SetExpertMagicNumber(InpMagicNumber);

   //--- Indicators
   if(InpUseEMAFilter)
      m_handle_ema = iMA(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, InpEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);

   if(InpUseATRFilter)
      m_handle_atr = iATR(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, InpATRPeriod);

   EventSetTimer(1);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   ObjectsDeleteAll(0, "SS_");

   if(MQLInfoInteger(MQL_TESTER))
      ExportReport();
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   CheckDailyReset();

   if(m_daily_trades >= InpMaxDailyTrades && InpMaxDailyTrades > 0) return;
   if(m_daily_profit <= -InpMaxDailyLoss && InpMaxDailyLoss > 0) return;

   m_symbol.RefreshRates();

   CheckForSignal();
   MonitorEntry();
   ManageTrades();

   if(InpEnableDashboard) UpdateDashboard();
}

//+------------------------------------------------------------------+
//| Signal Detection                                                 |
//+------------------------------------------------------------------+
void CheckForSignal()
{
   datetime current_bar_time = iTime(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, 0);
   if(current_bar_time == m_last_signal_time) return;

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, 1, 1, rates) < 1) return;

   MqlRates candle = rates[0];
   double range = candle.high - candle.low;
   if(range <= 0) return;

   double upper_wick = candle.high - MathMax(candle.open, candle.close);
   double lower_wick = MathMin(candle.open, candle.close) - candle.low;
   double body       = MathAbs(candle.open - candle.close);

   double upper_wick_pct = (upper_wick / range) * 100.0;
   double lower_wick_pct = (lower_wick / range) * 100.0;
   double body_pct       = (body / range) * 100.0;

   //--- Pattern Recognition
   bool is_ss = (upper_wick_pct >= InpMinUpperWickPct && lower_wick_pct <= InpMaxLowerWickPct && body_pct <= InpMaxBodyPct);
   if(InpRequireBearish && candle.close >= candle.open) is_ss = false;

   bool is_hammer = (lower_wick_pct >= InpMinUpperWickPct && upper_wick_pct <= InpMaxLowerWickPct && body_pct <= InpMaxBodyPct);
   if(InpRequireBearish && candle.close <= candle.open) is_hammer = false;

   //--- Filter Processing
   bool filters_ok = true;
   if(InpMinCandleRange > 0 && range < InpMinCandleRange * _Point) filters_ok = false;
   if(InpMaxCandleRange > 0 && range > InpMaxCandleRange * _Point) filters_ok = false;
   if(!IsInsideSession()) filters_ok = false;

   if(InpUseATRFilter && m_handle_atr != INVALID_HANDLE) {
      double atr[]; if(CopyBuffer(m_handle_atr, 0, 1, 1, atr) > 0) if(range < atr[0] * InpMinATRMult) filters_ok = false;
   }

   int signal = 0;
   if(is_ss && InpEnableSellTrades && filters_ok) signal = -1;
   if(is_hammer && InpEnableBuyTrades && filters_ok) signal = 1;

   //--- EMA Filter
   if(signal != 0 && InpUseEMAFilter && m_handle_ema != INVALID_HANDLE) {
      double ema[];
      if(CopyBuffer(m_handle_ema, 0, 1, 1, ema) > 0) {
         if(signal == -1 && candle.close > ema[0]) signal = 0;
         if(signal == 1 && candle.close < ema[0]) signal = 0;
      }
   }

   if(signal != 0) {
      m_last_signal_time = current_bar_time;
      m_signal_active = true;
      m_signal_type   = signal;
      m_signal_high   = candle.high;
      m_signal_low    = candle.low;

      double rr_val = (double)InpRRSelection / 10.0;

      if(m_signal_type == -1) { // SS
         m_signal_entry = m_signal_low - InpEntryBuffer * _Point;
         m_signal_sl    = m_signal_high + InpStopLossBuffer * _Point;
         double risk    = m_signal_sl - m_signal_entry;
         m_signal_tp    = m_signal_entry - (risk * rr_val);
      } else { // Hammer
         m_signal_entry = m_signal_high + InpEntryBuffer * _Point;
         m_signal_sl    = m_signal_low - InpStopLossBuffer * _Point;
         double risk    = m_signal_entry - m_signal_sl;
         m_signal_tp    = m_signal_entry + (risk * rr_val);
      }

      if(InpEnableNotifications) SendAlert("Signal Detected: " + (m_signal_type==1?"Hammer":"Shooting Star"));
      DrawSignalObjects(candle.time, m_signal_type);
   }
}

//+------------------------------------------------------------------+
//| Monitor Breakout                                                 |
//+------------------------------------------------------------------+
void MonitorEntry()
{
   if(!m_signal_active) return;

   //--- Invalidation Check
   if(InpCancelOnInvalidation) {
      if(m_signal_type == -1 && m_symbol.Bid() > m_signal_high) { m_signal_active = false; ObjectsDeleteAll(0, "SS_Pending_"); return; }
      if(m_signal_type == 1  && m_symbol.Ask() < m_signal_low)  { m_signal_active = false; ObjectsDeleteAll(0, "SS_Pending_"); return; }
   }

   if(PositionSelect(_Symbol)) return;
   if(m_symbol.Spread() > InpMaxSpread && InpMaxSpread > 0) return;

   if(m_signal_type == -1) {
      if(m_symbol.Bid() <= m_signal_entry) { ExecuteOrder(ORDER_TYPE_SELL); m_signal_active = false; ObjectsDeleteAll(0, "SS_Pending_"); }
   } else {
      if(m_symbol.Ask() >= m_signal_entry) { ExecuteOrder(ORDER_TYPE_BUY); m_signal_active = false; ObjectsDeleteAll(0, "SS_Pending_"); }
   }
}

//+------------------------------------------------------------------+
//| Order Execution                                                  |
//+------------------------------------------------------------------+
void ExecuteOrder(ENUM_ORDER_TYPE type)
{
   double sl_dist = MathAbs(m_signal_entry - m_signal_sl);
   double lot = CalculateLotSize(sl_dist);
   if(lot <= 0) return;

   double sl = m_symbol.NormalizePrice(m_signal_sl);
   double tp = m_symbol.NormalizePrice(m_signal_tp);
   double pr = (type == ORDER_TYPE_BUY) ? m_symbol.Ask() : m_symbol.Bid();

   if(m_trade.PositionOpen(_Symbol, type, lot, pr, sl, tp, InpTradeComment)) {
      if(m_trade.ResultRetcode() == TRADE_RETCODE_DONE || m_trade.ResultRetcode() == TRADE_RETCODE_PLACED) m_daily_trades++;
   }
}

//+------------------------------------------------------------------+
//| Risk Calculation                                                 |
//+------------------------------------------------------------------+
double CalculateLotSize(double sl_pts)
{
   if(InpUseFixedLot) return InpFixedLot;

   double risk_usd = (InpRiskPercent > 0) ? m_account.Balance() * (InpRiskPercent / 100.0) : InpRiskAmount;
   double tv = m_symbol.TickValue();
   double ts = m_symbol.TickSize();

   if(ts == 0 || sl_pts == 0 || tv == 0) return 0;

   double lot = risk_usd / ( (sl_pts / ts) * tv );
   double step = m_symbol.LotsStep();
   lot = MathFloor(lot / step) * step;

   return MathMax(m_symbol.LotsMin(), MathMin(m_symbol.LotsMax(), lot));
}

//+------------------------------------------------------------------+
//| Trade Management                                                 |
//+------------------------------------------------------------------+
void ManageTrades()
{
   if(!PositionSelect(_Symbol)) return;
   if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) return;

   ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
   double entry = PositionGetDouble(POSITION_PRICE_OPEN);
   double sl    = PositionGetDouble(POSITION_SL);
   double tp    = PositionGetDouble(POSITION_TP);
   double price = (type == POSITION_TYPE_BUY) ? m_symbol.Bid() : m_symbol.Ask();

   double risk = MathAbs(entry - sl);
   if(risk <= 0) return;
   double profit = (type == POSITION_TYPE_BUY) ? (price - entry) : (entry - price);
   double rr = profit / risk;

   if(InpAutoBreakeven && rr >= InpBETriggerRR) {
      if(type == POSITION_TYPE_BUY && sl < entry) m_trade.PositionModify(_Symbol, entry, tp);
      if(type == POSITION_TYPE_SELL && sl > entry) m_trade.PositionModify(_Symbol, entry, tp);
   }

   if(InpPartialTP && rr >= InpPartialTPTriggerRR) {
      double vol = PositionGetDouble(POSITION_VOLUME);
      if(vol > m_symbol.LotsMin()) {
         double step = m_symbol.LotsStep();
         double close_vol = MathFloor(vol * (InpPartialClosePct / 100.0) / step) * step;
         if(close_vol >= m_symbol.LotsMin()) m_trade.PositionClosePartial(_Symbol, close_vol);
      }
   }

   if(InpTrailingStop && profit > InpTrailingStopDistance * _Point) {
      double nsl = (type == POSITION_TYPE_BUY) ? m_symbol.NormalizePrice(price - InpTrailingStopDistance * _Point) : m_symbol.NormalizePrice(price + InpTrailingStopDistance * _Point);
      if(type == POSITION_TYPE_BUY && nsl > sl) m_trade.PositionModify(_Symbol, nsl, tp);
      if(type == POSITION_TYPE_SELL && nsl < sl) m_trade.PositionModify(_Symbol, nsl, tp);
   }
}

//+------------------------------------------------------------------+
//| Helpers                                                          |
//+------------------------------------------------------------------+
bool IsInsideSession()
{
   if(!InpTradeLondon && !InpTradeNY) return true;
   datetime now = TimeCurrent();
   MqlDateTime dt;
   TimeToStruct(now, dt);
   int mins = dt.hour * 60 + dt.min;
   bool ok = false;
   if(InpTradeLondon) ok |= CheckSessionTime(InpLondonStart, InpLondonEnd, mins);
   if(InpTradeNY)     ok |= CheckSessionTime(InpNYStart, InpNYEnd, mins);
   return ok;
}

bool CheckSessionTime(string s, string e, int m)
{
   string p1[], p2[];
   if(StringSplit(s, ':', p1)!=2 || StringSplit(e, ':', p2)!=2) return false;
   int t1 = (int)StringToInteger(p1[0])*60 + (int)StringToInteger(p1[1]);
   int t2 = (int)StringToInteger(p2[0])*60 + (int)StringToInteger(p2[1]);
   return (t1 < t2) ? (m >= t1 && m <= t2) : (m >= t1 || m <= t2);
}

void CheckDailyReset()
{
   datetime today = iTime(_Symbol, PERIOD_D1, 0);
   if(today != m_last_daily_reset) { m_last_daily_reset = today; m_daily_trades = 0; }
   m_daily_profit = 0;
   HistorySelect(m_last_daily_reset, TimeCurrent());
   for(int i=HistoryDealsTotal()-1; i>=0; i--) {
      ulong t = HistoryDealGetTicket(i);
      if(HistoryDealGetInteger(t, DEAL_MAGIC) == InpMagicNumber)
         m_daily_profit += HistoryDealGetDouble(t, DEAL_PROFIT) + HistoryDealGetDouble(t, DEAL_COMMISSION) + HistoryDealGetDouble(t, DEAL_SWAP);
   }
}

void SendAlert(string msg)
{
   string full = "SS Pro Signal: " + msg + " on " + _Symbol;
   if(InpEnableNotifications) Alert(full);
   if(InpEnablePush) SendNotification(full);
   if(InpEnableEmail) SendMail("EA Signal", full);
}

void DrawSignalObjects(datetime t, int type)
{
   ObjectsDeleteAll(0, "SS_Pending_");
   string name = "SS_Signal_" + TimeToString(t);
   double arrow_p = (type == 1) ? m_signal_low - 100*_Point : m_signal_high + 100*_Point;
   ObjectCreate(0, name, (type == 1 ? OBJ_ARROW_UP : OBJ_ARROW_DOWN), 0, t, arrow_p);
   ObjectSetInteger(0, name, OBJPROP_COLOR, InpColorSignal);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 3);

   DrawLine("SS_Pending_Entry", m_signal_entry, clrAqua, STYLE_SOLID);
   DrawLine("SS_Pending_SL", m_signal_sl, clrRed, STYLE_DASH);
   DrawLine("SS_Pending_TP", m_signal_tp, clrLime, STYLE_DASH);

   ObjectCreate(0, "SS_Pending_Box", OBJ_RECTANGLE, 0, t, m_signal_entry, t + PeriodSeconds((ENUM_TIMEFRAMES)InpTimeframe)*3, m_signal_sl);
   ObjectSetInteger(0, "SS_Pending_Box", OBJPROP_COLOR, InpColorBox);
   ObjectSetInteger(0, "SS_Pending_Box", OBJPROP_FILL, true);
   ObjectSetInteger(0, "SS_Pending_Box", OBJPROP_BACK, true);
}

void DrawLine(string n, double p, color c, ENUM_LINE_STYLE s)
{
   ObjectCreate(0, n, OBJ_HLINE, 0, 0, p);
   ObjectSetInteger(0, n, OBJPROP_COLOR, c);
   ObjectSetInteger(0, n, OBJPROP_STYLE, s);
}

void UpdateDashboard()
{
   string text = "--- SHOOTING STAR PRO EA ---\n";
   text += "Balance: " + DoubleToString(m_account.Balance(), 2) + "  Equity: " + DoubleToString(m_account.Equity(), 2) + "\n";
   text += "Risk USD: " + DoubleToString(InpUseFixedLot ? 0 : (InpRiskPercent > 0 ? m_account.Balance() * (InpRiskPercent/100.0) : InpRiskAmount), 2) + "\n";
   text += "Daily PL: " + DoubleToString(m_daily_profit, 2) + " (" + (string)m_daily_trades + " trades)\n";
   text += "Spread: " + (string)m_symbol.Spread() + " | Session: " + (IsInsideSession() ? "ON" : "OFF") + "\n";
   text += "Last Signal: " + (m_signal_active ? (m_signal_type==1?"HAMMER":"SS") : "NONE") + "\n";
   if(PositionSelect(_Symbol)) text += "STATUS: IN TRADE (" + DoubleToString(PositionGetDouble(POSITION_PROFIT), 2) + ")\n";
   else text += "STATUS: SCANNING...\n";
   Comment(text);
}

//+------------------------------------------------------------------+
//| Backtesting / Research Module                                    |
//+------------------------------------------------------------------+
void ExportReport()
{
   int fh = FileOpen(m_csv_filename, FILE_WRITE | FILE_CSV | FILE_ANSI | FILE_SHARE_READ);
   if(fh != INVALID_HANDLE)
   {
      FileWrite(fh, "--- Quantitative Research Report ---");
      FileWrite(fh, "Stat", "Value");
      FileWrite(fh, "Total Trades", (string)TesterStatistics(STAT_TRADES));
      FileWrite(fh, "Win Rate %", DoubleToString(TesterStatistics(STAT_PROFIT_TRADES) / MathMax(1, TesterStatistics(STAT_TRADES)) * 100.0, 2));
      FileWrite(fh, "Profit Factor", DoubleToString(TesterStatistics(STAT_PROFIT_FACTOR), 2));
      FileWrite(fh, "Sharpe Ratio", DoubleToString(TesterStatistics(STAT_SHARPE_RATIO), 2));
      FileWrite(fh, "Max Drawdown %", DoubleToString(TesterStatistics(STAT_EQUITY_DDREL_PERCENT), 2));
      FileWrite(fh, "Recovery Factor", DoubleToString(TesterStatistics(STAT_RECOVERY_FACTOR), 2));
      FileWrite(fh, "Expected Payoff", DoubleToString(TesterStatistics(STAT_EXPECTED_PAYOFF), 2));

      double total_win_trades = TesterStatistics(STAT_PROFIT_TRADES);
      double total_loss_trades = TesterStatistics(STAT_LOSS_TRADES);
      double avg_win = (total_win_trades > 0) ? TesterStatistics(STAT_GROSS_PROFIT) / total_win_trades : 0;
      double avg_loss = (total_loss_trades > 0) ? TesterStatistics(STAT_GROSS_LOSS) / total_loss_trades : 0;

      FileWrite(fh, "Average Win", DoubleToString(avg_win, 2));
      FileWrite(fh, "Average Loss", DoubleToString(avg_loss, 2));
      FileWrite(fh, "Consecutive Wins", (string)TesterStatistics(STAT_CONPROFITMAX_TRADES));
      FileWrite(fh, "Consecutive Losses", (string)TesterStatistics(STAT_CONLOSSMAX_TRADES));
      FileClose(fh);
   }
}

double OnTester()
{
   double trades = TesterStatistics(STAT_TRADES);
   if(trades < 10) return 0;
   // Hedge Fund Metric: Win Rate * Profit Factor / Max DD
   double pf = TesterStatistics(STAT_PROFIT_FACTOR);
   double dd = TesterStatistics(STAT_EQUITY_DDREL_PERCENT);
   if(dd <= 0) dd = 1.0;
   return pf / dd;
}
