//+------------------------------------------------------------------+
//|                                         ShootingStar_Pro_EA.mq5 |
//|                                  Copyright 2024, Trading Robot   |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, Jules & Trading Robot"
#property link      "https://www.mql5.com"
#property version   "1.10"
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
   RISK_MODE_USD,    // Risk in USD
   RISK_MODE_PCT,    // Risk in Percent
   RISK_MODE_FIXED   // Fixed Lot Size
};

enum ENUM_SIGNAL_TF {
   TF_CURRENT = PERIOD_CURRENT,
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

//--- INPUT PARAMETERS
sinput group "=== STRATEGY SETTINGS ==="
input ENUM_SIGNAL_TF InpTimeframe = TF_M15;        // Signal Timeframe
input double InpMinUpperWickPct   = 70.0;          // Min Upper Wick % (of total range)
input double InpMaxLowerWickPct   = 10.0;          // Max Lower Wick % (of total range)
input double InpMaxBodyPct        = 20.0;          // Max Body % (of total range)
input bool   InpRequireBearish    = true;          // Require Bearish Candle (Hammer = Bullish)
input double InpMinCandleRangePts = 0;             // Min Candle Range (Points, 0=Disabled)
input double InpMaxCandleRangePts = 0;             // Max Candle Range (Points, 0=Disabled)

sinput group "=== ENTRY & EXIT ==="
input int    InpEntryBuffer       = 10;            // Entry Buffer (Points)
input int    InpStopLossBuffer    = 10;            // Stop Loss Buffer (Points)
input double InpRRRatio           = 3.0;           // Risk Reward Ratio (1:X)
input int    InpSlippage          = 30;            // Slippage (Points)
input bool   InpEnableBuy         = true;          // Enable Hammer (Buy)
input bool   InpEnableSell        = true;          // Enable Shooting Star (Sell)
input bool   InpCancelOnNewCandle = false;         // Cancel signal if no entry on next candle
input string InpTradeComment      = "ShootingStar_Pro"; // Trade Comment
input int    InpMagicNumber       = 123456;        // Magic Number

sinput group "=== RISK MANAGEMENT ==="
input ENUM_RISK_MODE InpRiskMode  = RISK_MODE_PCT; // Risk Mode
input double InpRiskValue         = 1.0;           // Risk Value (USD or %)
input double InpFixedLot          = 0.1;           // Fixed Lot Size (if Fixed Mode)
input double InpMaxDailyLoss      = 500.0;         // Max Daily Loss ($)
input int    InpMaxDailyTrades    = 5;             // Max Daily Trades
input double InpMaxSpread         = 50.0;          // Max Spread Allowed (Points)

sinput group "=== ADVANCED TRADE MGMT ==="
input bool   InpUseBreakeven      = false;         // Enable Breakeven
input double InpBETriggerRR       = 1.0;           // Breakeven Trigger (RR)
input bool   InpUsePartialTP      = false;         // Enable Partial Take Profit
input double InpPartialClosePct   = 50.0;          // Partial Close %
input double InpPartialTPTriggerRR= 1.5;           // Partial TP Trigger (RR)
input bool   InpUseTrailing       = false;         // Enable Trailing Stop
input int    InpTrailingDistPts   = 200;           // Trailing Distance (Points)

sinput group "=== FILTERS ==="
input bool   InpUseEMAFilter      = false;         // Enable EMA Trend Filter
input int    InpEMAPeriod         = 200;           // EMA Period
input bool   InpUseATRFilter      = false;         // Enable ATR Filter
input int    InpATRPeriod         = 14;            // ATR Period
input double InpMinATRMult        = 0.5;           // Min Candle Size (ATR Multiplier)

sinput group "=== SESSIONS ==="
input bool   InpTradeLondon       = false;         // Trade London Session
input string InpLondonStart       = "08:00";       // London Start (HH:MM)
input string InpLondonEnd         = "16:00";       // London End (HH:MM)
input bool   InpTradeNY           = false;         // Trade New York Session
input string InpNYStart           = "13:00";       // NY Start (HH:MM)
input string InpNYEnd             = "21:00";       // NY End (HH:MM)

sinput group "=== NOTIFICATIONS ==="
input bool   InpAlertMsg          = true;          // Terminal Alerts
input bool   InpAlertPush         = false;         // Push Notifications
input bool   InpAlertEmail        = false;         // Email Alerts

sinput group "=== VISUALS & DASHBOARD ==="
input bool   InpEnableDashboard   = true;          // Enable Dashboard
input bool   InpDrawObjects       = true;          // Draw Chart Objects
input color  InpColorSignal       = clrOrange;     // Signal Marker Color
input color  InpColorBox          = clrDimGray;    // RR Box Color

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

string m_csv_filename = "ShootingStar_Research.csv";

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
//| Export Report for Research Mode                                  |
//+------------------------------------------------------------------+
void ExportReport()
{
   int file_handle = FileOpen(m_csv_filename, FILE_WRITE | FILE_CSV | FILE_ANSI | FILE_SHARE_READ);
   if(file_handle != INVALID_HANDLE)
   {
      FileWrite(file_handle, "Stat", "Value");
      FileWrite(file_handle, "Total Trades", (string)TesterStatistics(STAT_TRADES));
      FileWrite(file_handle, "Profit Factor", DoubleToString(TesterStatistics(STAT_PROFIT_FACTOR), 2));
      FileWrite(file_handle, "Win Rate %", DoubleToString(TesterStatistics(STAT_PROFIT_TRADES) / MathMax(1, TesterStatistics(STAT_TRADES)) * 100.0, 2));
      FileWrite(file_handle, "Recovery Factor", DoubleToString(TesterStatistics(STAT_RECOVERY_FACTOR), 2));
      FileWrite(file_handle, "Sharpe Ratio", DoubleToString(TesterStatistics(STAT_SHARPE_RATIO), 2));
      FileWrite(file_handle, "Max Drawdown %", DoubleToString(TesterStatistics(STAT_EQUITY_DDREL_PERCENT), 2));
      FileWrite(file_handle, "Expectancy", DoubleToString(TesterStatistics(STAT_EXPECTED_PAYOFF), 2));
      FileClose(file_handle);
      Print("Research Report exported to ", m_csv_filename);
   }
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
//| Check for Candle Patterns                                        |
//+------------------------------------------------------------------+
void CheckForSignal()
{
   datetime current_bar_time = iTime(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, 0);
   if(current_bar_time == m_last_signal_time) return;

   // If a new candle started and we have an active pending signal, check if we should cancel it
   if(m_signal_active && InpCancelOnNewCandle)
   {
      m_signal_active = false;
      ObjectsDeleteAll(0, "SS_Pending_");
      Print("Signal Cancelled: New candle started without entry.");
   }

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

   //--- Shooting Star Detection
   bool is_ss = (upper_wick_pct >= InpMinUpperWickPct && lower_wick_pct <= InpMaxLowerWickPct && body_pct <= InpMaxBodyPct);
   if(InpRequireBearish && candle.close >= candle.open) is_ss = false;

   //--- Hammer Detection
   bool is_hammer = (lower_wick_pct >= InpMinUpperWickPct && upper_wick_pct <= InpMaxLowerWickPct && body_pct <= InpMaxBodyPct);
   if(InpRequireBearish && candle.close <= candle.open) is_hammer = false;

   //--- Filters
   bool filters_passed = true;
   if(InpMinCandleRangePts > 0 && range < InpMinCandleRangePts * _Point) filters_passed = false;
   if(InpMaxCandleRangePts > 0 && range > InpMaxCandleRangePts * _Point) filters_passed = false;

   if(InpUseATRFilter && m_handle_atr != INVALID_HANDLE) {
      double atr[]; if(CopyBuffer(m_handle_atr, 0, 1, 1, atr) > 0) if(range < atr[0] * InpMinATRMult) filters_passed = false;
   }

   if(!IsInsideSession()) filters_passed = false;

   int signal_found = 0;
   if(is_ss && InpEnableSell && filters_passed) signal_found = -1;
   if(is_hammer && InpEnableBuy && filters_passed) signal_found = 1;

   //--- EMA Filter
   if(signal_found != 0 && InpUseEMAFilter && m_handle_ema != INVALID_HANDLE)
   {
      double ema[];
      if(CopyBuffer(m_handle_ema, 0, 1, 1, ema) > 0)
      {
         if(signal_found == -1 && candle.close > ema[0]) signal_found = 0; // Sell only below EMA
         if(signal_found == 1 && candle.close < ema[0]) signal_found = 0;  // Buy only above EMA
      }
   }

   if(signal_found != 0)
   {
      m_last_signal_time = current_bar_time;
      m_signal_active = true;
      m_signal_type   = signal_found;
      m_signal_high   = candle.high;
      m_signal_low    = candle.low;

      if(m_signal_type == -1) // Shooting Star
      {
         m_signal_entry  = m_signal_low - InpEntryBuffer * _Point;
         m_signal_sl     = m_signal_high + InpStopLossBuffer * _Point;
         double risk_pts = m_signal_sl - m_signal_entry;
         m_signal_tp     = m_signal_entry - (risk_pts * InpRRRatio);
      }
      else // Hammer
      {
         m_signal_entry  = m_signal_high + InpEntryBuffer * _Point;
         m_signal_sl     = m_signal_low - InpStopLossBuffer * _Point;
         double risk_pts = m_signal_entry - m_signal_sl;
         m_signal_tp     = m_signal_entry + (risk_pts * InpRRRatio);
      }

      SendSignalAlert(m_signal_type == 1 ? "Hammer (Buy)" : "Shooting Star (Sell)");
      if(InpDrawObjects) DrawSignalObjects(candle.time, m_signal_type);
   }
}

//+------------------------------------------------------------------+
//| Monitor for Entry Breakout                                       |
//+------------------------------------------------------------------+
void MonitorEntry()
{
   if(!m_signal_active) return;

   //--- Check for invalidation (Price hits SL before entry)
   if(m_signal_type == -1) { // Sell
      if(m_symbol.Bid() > m_signal_high) {
         m_signal_active = false;
         ObjectsDeleteAll(0, "SS_Pending_");
         Print("Signal Invalidated: Price broke high before entry.");
         return;
      }
   } else { // Buy
      if(m_symbol.Ask() < m_signal_low) {
         m_signal_active = false;
         ObjectsDeleteAll(0, "SS_Pending_");
         Print("Signal Invalidated: Price broke low before entry.");
         return;
      }
   }

   if(PositionSelect(_Symbol)) return;
   if(m_symbol.Spread() > InpMaxSpread && InpMaxSpread > 0) return;

   //--- Breakout Entry
   if(m_signal_type == -1) { // Sell
      if(m_symbol.Bid() <= m_signal_entry) {
         ExecuteTrade(ORDER_TYPE_SELL);
         m_signal_active = false;
         ObjectsDeleteAll(0, "SS_Pending_");
      }
   } else { // Buy
      if(m_symbol.Ask() >= m_signal_entry) {
         ExecuteTrade(ORDER_TYPE_BUY);
         m_signal_active = false;
         ObjectsDeleteAll(0, "SS_Pending_");
      }
   }
}

//+------------------------------------------------------------------+
//| Execute Order                                                    |
//+------------------------------------------------------------------+
void ExecuteTrade(ENUM_ORDER_TYPE type)
{
   double sl_dist = MathAbs(m_signal_entry - m_signal_sl);
   double lot = CalculateLotSize(sl_dist);

   if(lot <= 0) return;

   double sl = m_symbol.NormalizePrice(m_signal_sl);
   double tp = m_symbol.NormalizePrice(m_signal_tp);
   double price = (type == ORDER_TYPE_BUY) ? m_symbol.Ask() : m_symbol.Bid();

   bool success = false;
   if(type == ORDER_TYPE_BUY)
      success = m_trade.Buy(lot, _Symbol, price, sl, tp, InpTradeComment);
   else
      success = m_trade.Sell(lot, _Symbol, price, sl, tp, InpTradeComment);

   if(success)
   {
      uint ret = m_trade.ResultRetcode();
      if(ret == TRADE_RETCODE_DONE || ret == TRADE_RETCODE_PLACED)
      {
         m_daily_trades++;
      }
   }
}

//+------------------------------------------------------------------+
//| Risk-Based Lot Sizing                                            |
//+------------------------------------------------------------------+
double CalculateLotSize(double sl_distance_price)
{
   if(InpRiskMode == RISK_MODE_FIXED) return InpFixedLot;

   double risk_amount = (InpRiskMode == RISK_MODE_USD) ? InpRiskValue :
                        AccountInfoDouble(ACCOUNT_BALANCE) * (InpRiskValue / 100.0);

   double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tick_size  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double lot_step   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   if(tick_size == 0 || sl_distance_price == 0) return 0;

   double lot = risk_amount / ( (sl_distance_price / tick_size) * tick_value );

   double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   lot = MathFloor(lot / lot_step) * lot_step;
   if(lot < min_lot) return 0;
   if(lot > max_lot) lot = max_lot;

   return lot;
}

//+------------------------------------------------------------------+
//| Trade Management (BE, Trailing, Partial)                         |
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

   double risk_pts = MathAbs(entry - sl);
   if(risk_pts <= 0) return;

   double profit_pts = (type == POSITION_TYPE_BUY) ? (price - entry) : (entry - price);
   double current_rr = profit_pts / risk_pts;

   //--- Breakeven
   if(InpUseBreakeven && current_rr >= InpBETriggerRR)
   {
      if(type == POSITION_TYPE_BUY && (sl < entry || sl == 0))
         m_trade.PositionModify(_Symbol, entry, tp);
      if(type == POSITION_TYPE_SELL && (sl > entry || sl == 0))
         m_trade.PositionModify(_Symbol, entry, tp);
   }

   //--- Partial TP
   if(InpUsePartialTP && current_rr >= InpPartialTPTriggerRR)
   {
      double vol = PositionGetDouble(POSITION_VOLUME);
      if(vol > m_symbol.LotsMin()) {
         double step = m_symbol.LotsStep();
         double close_vol = MathFloor(vol * (InpPartialClosePct / 100.0) / step) * step;
         if(close_vol >= m_symbol.LotsMin()) m_trade.PositionClosePartial(_Symbol, close_vol);
      }
   }

   //--- Trailing Stop
   if(InpUseTrailing && profit_pts > InpTrailingDistPts * _Point)
   {
      double new_sl = (type == POSITION_TYPE_BUY) ?
                      m_symbol.NormalizePrice(price - InpTrailingDistPts * _Point) :
                      m_symbol.NormalizePrice(price + InpTrailingDistPts * _Point);

      if(type == POSITION_TYPE_BUY && (new_sl > sl || sl == 0)) m_trade.PositionModify(_Symbol, new_sl, tp);
      if(type == POSITION_TYPE_SELL && (new_sl < sl || sl == 0)) m_trade.PositionModify(_Symbol, new_sl, tp);
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
   if(InpTradeNY) ok |= CheckSessionTime(InpNYStart, InpNYEnd, mins);
   return ok;
}

bool CheckSessionTime(string start_time, string end_time, int current_mins)
{
   string p1[], p2[];
   if(StringSplit(start_time, ':', p1) != 2 || StringSplit(end_time, ':', p2) != 2) return false;
   int t1 = (int)StringToInteger(p1[0]) * 60 + (int)StringToInteger(p1[1]);
   int t2 = (int)StringToInteger(p2[0]) * 60 + (int)StringToInteger(p2[1]);

   if(t1 < t2) // Normal session (e.g., 08:00 - 16:00)
      return (current_mins >= t1 && current_mins <= t2);
   else // Overnight session (e.g., 22:00 - 04:00)
      return (current_mins >= t1 || current_mins <= t2);
}

void CheckDailyReset()
{
   datetime today = iTime(_Symbol, PERIOD_D1, 0);
   if(today != m_last_daily_reset) {
      m_last_daily_reset = today;
      m_daily_trades = 0;
   }
   m_daily_profit = 0;
   HistorySelect(m_last_daily_reset, TimeCurrent());
   for(int i=HistoryDealsTotal()-1; i>=0; i--) {
      ulong t = HistoryDealGetTicket(i);
      if(HistoryDealGetInteger(t, DEAL_MAGIC) == InpMagicNumber)
         m_daily_profit += HistoryDealGetDouble(t, DEAL_PROFIT) + HistoryDealGetDouble(t, DEAL_COMMISSION) + HistoryDealGetDouble(t, DEAL_SWAP);
   }
}

void SendSignalAlert(string msg)
{
   string full = "SS Pro EA Signal: " + msg + " on " + _Symbol;
   if(InpAlertMsg) Alert(full);
   if(InpAlertPush) SendNotification(full);
   if(InpAlertEmail) SendMail("EA Signal", full);
}

void DrawSignalObjects(datetime time, int type)
{
   ObjectsDeleteAll(0, "SS_Pending_");
   string name = "SS_Signal_" + TimeToString(time);
   double arrow_p = (type == 1) ? m_signal_low - 100*_Point : m_signal_high + 100*_Point;
   ObjectCreate(0, name, (type == 1 ? OBJ_ARROW_UP : OBJ_ARROW_DOWN), 0, time, arrow_p);
   ObjectSetInteger(0, name, OBJPROP_COLOR, InpColorSignal);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 3);

   DrawLine("SS_Pending_Entry", m_signal_entry, clrAqua, STYLE_SOLID);
   DrawLine("SS_Pending_SL", m_signal_sl, clrRed, STYLE_DASH);
   DrawLine("SS_Pending_TP", m_signal_tp, clrLime, STYLE_DASH);

   ObjectCreate(0, "SS_Pending_Box", OBJ_RECTANGLE, 0, time, m_signal_entry, time + PeriodSeconds((ENUM_TIMEFRAMES)InpTimeframe)*3, m_signal_sl);
   ObjectSetInteger(0, "SS_Pending_Box", OBJPROP_COLOR, InpColorBox);
   ObjectSetInteger(0, "SS_Pending_Box", OBJPROP_FILL, true);
   ObjectSetInteger(0, "SS_Pending_Box", OBJPROP_BACK, true);
}

void DrawLine(string name, double price, color col, ENUM_LINE_STYLE style)
{
   ObjectCreate(0, name, OBJ_HLINE, 0, 0, price);
   ObjectSetInteger(0, name, OBJPROP_COLOR, col);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
}

void UpdateDashboard()
{
   string text = "--- SHOOTING STAR PRO EA ---\n";
   text += "Balance: " + DoubleToString(m_account.Balance(), 2) + "  Equity: " + DoubleToString(m_account.Equity(), 2) + "\n";
   text += "Daily PL: " + DoubleToString(m_daily_profit, 2) + " (" + (string)m_daily_trades + " trades)\n";
   text += "Spread: " + (string)m_symbol.Spread() + " | Session: " + (IsInsideSession() ? "ON" : "OFF") + "\n";
   text += "Last Signal: " + (m_signal_active ? (m_signal_type==1?"HAMMER":"SS") : "NONE") + "\n";

   if(PositionSelect(_Symbol)) text += "STATUS: IN TRADE (" + DoubleToString(PositionGetDouble(POSITION_PROFIT), 2) + ")\n";
   else text += "STATUS: SCANNING...\n";

   Comment(text);
}

double OnTester()
{
   double trades = TesterStatistics(STAT_TRADES);
   if(trades < 5) return 0;
   return TesterStatistics(STAT_PROFIT_FACTOR) * TesterStatistics(STAT_RECOVERY_FACTOR);
}
