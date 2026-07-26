//+------------------------------------------------------------------+
//|                                 M1_MeanReversion_Scalper.mq5      |
//+------------------------------------------------------------------+
#property copyright "Built with Claude"
#property version   "1.00"
#property description "M1 Mean-Reversion Scalper — Bollinger Band + RSI fade, ADX range filter."
#property description "NOT validated as profitable. Requires real-tick backtesting,"
#property description "walk-forward validation, and demo forward-testing before live use."

#include <Trade\Trade.mqh>

//====================================================================
// INPUTS
//====================================================================
input group "=== Bollinger Band Fade Entry ==="
input int               InpBBPeriod           = 20;           // Bollinger Band period
input double            InpBBDeviation        = 2.0;          // Bollinger Band deviation
input int               InpRSIPeriod          = 14;
input double            InpRSIOversold        = 25.0;         // RSI below this + price below lower band = BUY
input double            InpRSIOverbought      = 75.0;         // RSI above this + price above upper band = SELL

input group "=== Range Filter (mean-reversion fails in strong trends) ==="
input int               InpADXPeriod          = 14;
input double            InpMaxADXForReversion = 25.0;         // Only trade when ADX is BELOW this (range-bound, not trending)

input group "=== Exit: ATR-Scaled SL/TP ==="
input int               InpATRPeriod          = 14;
input double            InpATR_SL_Mult        = 1.0;          // Stop loss = ATR * this
input double            InpATR_TP_Mult        = 1.2;          // Take profit = ATR * this — modest target, mean-reversion trades are usually quick
input bool              InpUseTrailingManagement = false;     // OFF by default — test the raw signal first
input double            InpBreakevenTrigger_ATR = 0.8;
input double            InpBreakevenLock_ATR    = 0.2;
input double            InpTrailing_ATR         = 1.0;
input int               InpMaxHoldBars          = 30;
input bool              InpExtendAfterBreakeven = true;

input group "=== Trade Filters ==="
input double            InpMaxSpreadPoints    = 30.0;
input bool              InpUseSessionFilter   = true;
input int               InpSessionStartHour   = 7;
input int               InpSessionEndHour     = 20;
input int               InpMaxTradesPerDay    = 8;

input group "=== Position Sizing ==="
input double            InpLotSize            = 0.01;
input double            InpRiskPercent        = 1.0;
input double            InpMaxMarginUtilizationPct = 50.0;

input group "=== Account Protection ==="
input double            InpMaxDrawdownPercent = 15.0;
input bool              InpCloseAllOnMaxDrawdown = true;

input group "=== Execution ==="
input long              InpMagicNumber        = 20260301;

input group "=== Diagnostics ==="
input bool              InpLogSignals         = true;

//====================================================================
// GLOBALS
//====================================================================
CTrade g_trade;

int g_h_bb  = INVALID_HANDLE;
int g_h_rsi = INVALID_HANDLE;
int g_h_adx = INVALID_HANDLE;
int g_h_atr = INVALID_HANDLE;

datetime g_last_bar_time      = 0;
double   g_peak_equity        = 0.0;
bool     g_kill_switch_warned = false;
int      g_trades_today       = 0;
datetime g_current_day        = 0;

//====================================================================
// INIT
//====================================================================
int OnInit()
{
   g_h_bb  = iBands(Symbol(), PERIOD_M1, InpBBPeriod, 0, InpBBDeviation, PRICE_CLOSE);
   g_h_rsi = iRSI(Symbol(), PERIOD_M1, InpRSIPeriod, PRICE_CLOSE);
   g_h_adx = iADX(Symbol(), PERIOD_M1, InpADXPeriod);
   g_h_atr = iATR(Symbol(), PERIOD_M1, InpATRPeriod);

   if(g_h_bb == INVALID_HANDLE || g_h_rsi == INVALID_HANDLE || g_h_adx == INVALID_HANDLE || g_h_atr == INVALID_HANDLE)
   {
      Print("[INIT] Failed to create one or more indicator handles.");
      return INIT_FAILED;
   }

   g_trade.SetExpertMagicNumber(InpMagicNumber);
   ConfigureFillingMode();

   g_peak_equity = AccountInfoDouble(ACCOUNT_EQUITY);
   g_kill_switch_warned = false;
   g_current_day = 0;
   g_trades_today = 0;

   Print("[INIT] M1_MeanReversion_Scalper initialized. Enforced 0.01 lot minimum. NOT validated as profitable — test on real tick data first.");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(g_h_bb  != INVALID_HANDLE) IndicatorRelease(g_h_bb);
   if(g_h_rsi != INVALID_HANDLE) IndicatorRelease(g_h_rsi);
   if(g_h_adx != INVALID_HANDLE) IndicatorRelease(g_h_adx);
   if(g_h_atr != INVALID_HANDLE) IndicatorRelease(g_h_atr);
}

//====================================================================
// HELPERS
//====================================================================
double NormalizePrice(double price)
{
   int digits = (int)SymbolInfoInteger(Symbol(), SYMBOL_DIGITS);
   return NormalizeDouble(price, digits);
}

void ConfigureFillingMode()
{
   uint filling = (uint)SymbolInfoInteger(Symbol(), SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0)        g_trade.SetTypeFilling(ORDER_FILLING_FOK);
   else if((filling & SYMBOL_FILLING_IOC) != 0)   g_trade.SetTypeFilling(ORDER_FILLING_IOC);
   else                                           g_trade.SetTypeFilling(ORDER_FILLING_RETURN);
}

bool GetOwnPositionTicket(ulong &out_ticket)
{
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong t = PositionGetTicket(i);
      if(t > 0 && PositionGetString(POSITION_SYMBOL) == Symbol() && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
      {
         out_ticket = t;
         return true;
      }
   }
   return false;
}

void CloseAllOwnPositions()
{
   int total = PositionsTotal();
   for(int i = total - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t > 0 && PositionGetString(POSITION_SYMBOL) == Symbol() && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         g_trade.PositionClose(t);
   }
}

bool IsNewBar()
{
   datetime bar_time = (datetime)SeriesInfoInteger(Symbol(), PERIOD_M1, SERIES_LASTBAR_DATE);
   if(bar_time != g_last_bar_time)
   {
      g_last_bar_time = bar_time;
      return true;
   }
   return false;
}

bool InSession()
{
   if(!InpUseSessionFilter) return true;
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int h = dt.hour;
   if(InpSessionStartHour <= InpSessionEndHour)
      return (h >= InpSessionStartHour && h < InpSessionEndHour);
   else
      return (h >= InpSessionStartHour || h < InpSessionEndHour);
}

void RefreshDailyCounter()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   dt.hour = 0; dt.min = 0; dt.sec = 0;
   datetime today = StructToTime(dt);
   if(today != g_current_day)
   {
      g_current_day = today;
      g_trades_today = 0;
   }
}

// MQL5 native replacement for iClose
double GetClose(string symbol, ENUM_TIMEFRAMES timeframe, int shift)
{
   double close[1];
   if(CopyClose(symbol, timeframe, shift, 1, close) > 0)
      return close[0];
   return 0.0;
}

// Compile-proof native replacement for iBarShift to avoid naming conflicts on newer MT5 platforms
int CustomBarShift(string symbol, ENUM_TIMEFRAMES timeframe, datetime time, bool exact = false)
{
   datetime bar_times[];
   // Copy up to 2000 bars from current chart history
   int copied = CopyTime(symbol, timeframe, 0, 2000, bar_times);
   if(copied <= 0) return -1;

   // bar_times is sorted chronologically ascending: bar_times[copied - 1] is the newest bar
   for(int i = copied - 1; i >= 0; i--)
   {
      if(bar_times[i] <= time)
      {
         int shift = (copied - 1) - i;
         if(exact && bar_times[i] != time) return -1;
         return shift;
      }
   }
   return -1;
}

//====================================================================
// MAIN TICK HANDLER
//====================================================================
void OnTick()
{
   double current_equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(current_equity > g_peak_equity) g_peak_equity = current_equity;
   double drawdown_pct = (g_peak_equity > 0.0) ? (g_peak_equity - current_equity) / g_peak_equity * 100.0 : 0.0;

   if(drawdown_pct >= InpMaxDrawdownPercent)
   {
      if(!g_kill_switch_warned)
      {
         PrintFormat("[RISK] *** MAX DRAWDOWN HIT (%.1f%% >= %.1f%%). Peak=%.2f Current=%.2f. Halting new trades. ***",
                     drawdown_pct, InpMaxDrawdownPercent, g_peak_equity, current_equity);
         g_kill_switch_warned = true;
      }
      if(InpCloseAllOnMaxDrawdown) CloseAllOwnPositions();
      return;
   }

   RefreshDailyCounter();

   ulong ticket;
   if(GetOwnPositionTicket(ticket))
   {
      ManageOpenPosition(ticket);
   }

   if(!IsNewBar()) return;

   if(GetOwnPositionTicket(ticket)) return; // one position at a time

   if(g_trades_today >= InpMaxTradesPerDay)
   {
      return;
   }

   if(!InSession())
   {
      return;
   }

   MqlTick tick;
   if(!SymbolInfoTick(Symbol(), tick)) return;
   double point_val = SymbolInfoDouble(Symbol(), SYMBOL_POINT);
   if(point_val <= 0.0) return;
   double spread_points = (tick.ask - tick.bid) / point_val;
   if(spread_points > InpMaxSpreadPoints)
   {
      if(InpLogSignals) PrintFormat("[SIGNAL] Spread too wide (%.1f > %.1f pts), skipping.", spread_points, InpMaxSpreadPoints);
      return;
   }

   EvaluateEntry(tick);
}

//====================================================================
// ENTRY EVALUATION (runs once per closed M1 bar)
//====================================================================
void EvaluateEntry(const MqlTick &tick)
{
   // iBands buffer indices: 0 = base(middle), 1 = upper, 2 = lower
   double mid[1], upper[1], lower[1], rsi[1], adx[1], atr[1];

   // start_pos=1 -> array[0] is the LAST FULLY CLOSED bar (shift 1). Verified explicitly here
   // since an indexing mix-up here was exactly the bug that broke the previous EA.
   if(CopyBuffer(g_h_bb, 0, 1, 1, mid)   < 1) return;
   if(CopyBuffer(g_h_bb, 1, 1, 1, upper) < 1) return;
   if(CopyBuffer(g_h_bb, 2, 1, 1, lower) < 1) return;
   if(CopyBuffer(g_h_rsi, 0, 1, 1, rsi)  < 1) return;
   if(CopyBuffer(g_h_adx, 0, 1, 1, adx)  < 1) return;
   if(CopyBuffer(g_h_atr, 0, 1, 1, atr)  < 1) return;

   double close1 = GetClose(Symbol(), PERIOD_M1, 1); // last closed bar's close (shift 1, matches array[0] above)
   double current_atr = atr[0];
   if(current_atr <= 0.0) return;

   bool range_bound = adx[0] <= InpMaxADXForReversion;

   bool buy_signal  = range_bound && (close1 <= lower[0]) && (rsi[0] <= InpRSIOversold);
   bool sell_signal = range_bound && (close1 >= upper[0]) && (rsi[0] >= InpRSIOverbought);

   if(InpLogSignals)
   {
      PrintFormat("[SIGNAL] close=%.2f mid=%.2f upper=%.2f lower=%.2f RSI=%.1f ADX=%.1f(max %.1f, range=%s) -> BUY=%s SELL=%s",
                  close1, mid[0], upper[0], lower[0], rsi[0], adx[0], InpMaxADXForReversion, range_bound?"Y":"N",
                  buy_signal?"Y":"N", sell_signal?"Y":"N");
   }

   if(buy_signal)       ExecuteOrder(ORDER_TYPE_BUY, tick, current_atr);
   else if(sell_signal) ExecuteOrder(ORDER_TYPE_SELL, tick, current_atr);
}

//====================================================================
// ORDER EXECUTION
//====================================================================
void ExecuteOrder(ENUM_ORDER_TYPE order_type, const MqlTick &tick, double current_atr)
{
   double point_val = SymbolInfoDouble(Symbol(), SYMBOL_POINT);
   double sl_distance = current_atr * InpATR_SL_Mult;
   double tp_distance = current_atr * InpATR_TP_Mult;

   double entry_price = (order_type == ORDER_TYPE_BUY) ? tick.ask : tick.bid;
   double sl, tp;
   if(order_type == ORDER_TYPE_BUY) { sl = entry_price - sl_distance; tp = entry_price + tp_distance; }
   else                             { sl = entry_price + sl_distance; tp = entry_price - tp_distance; }
   sl = NormalizePrice(sl);
   tp = NormalizePrice(tp);

   double lots = InpLotSize;
   if(InpRiskPercent > 0.0)
   {
      double balance    = AccountInfoDouble(ACCOUNT_BALANCE);
      double risk_val   = balance * (InpRiskPercent / 100.0);
      double tick_value = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_VALUE);
      double tick_size  = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_SIZE);
      if(tick_size > 0.0)
      {
         double loss_points = sl_distance / point_val;
         if(loss_points > 0.0)
         {
            double value_per_point = (tick_value / tick_size) * point_val;
            lots = risk_val / (loss_points * value_per_point);
         }
      }
   }

   double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   if(free_margin > 0.0)
   {
      double required_margin = 0.0;
      if(OrderCalcMargin(order_type, Symbol(), lots, entry_price, required_margin) && required_margin > 0.0)
      {
         double max_allowed = free_margin * (InpMaxMarginUtilizationPct / 100.0);
         if(required_margin > max_allowed)
            lots *= (max_allowed / required_margin);
      }
   }

   double volume_step = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_STEP);
   double min_lot     = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MIN);
   double max_lot     = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MAX);

   // Override min_lot to 0.01 if the broker reports smaller, or enforce 0.01 as the absolute minimum size for XM
   if(min_lot < 0.01) min_lot = 0.01;
   if(volume_step <= 0.0) volume_step = 0.01;

   // Round to nearest volume step
   lots = MathRound(lots / volume_step) * volume_step;

   // Fallback to absolute minimum lot instead of skipping trade completely
   if(lots < min_lot)
   {
      lots = min_lot;
   }

   // Cap at maximum lot size
   if(max_lot > 0.0 && lots > max_lot)
   {
      lots = max_lot;
   }

   bool res;
   if(order_type == ORDER_TYPE_BUY) res = g_trade.Buy(lots, Symbol(), entry_price, sl, tp, "M1_MR_Buy");
   else                             res = g_trade.Sell(lots, Symbol(), entry_price, sl, tp, "M1_MR_Sell");

   if(res)
   {
      g_trades_today++;
      PrintFormat("[EXECUTION] %s placed. Lots=%.2f SL=%.2f TP=%.2f", EnumToString(order_type), lots, sl, tp);
   }
   else
   {
      PrintFormat("[ERROR] Order failed. Code=%d Comment=%s", g_trade.ResultRetcode(), g_trade.ResultComment());
   }
}

//====================================================================
// POSITION MANAGEMENT
//====================================================================
void ManageOpenPosition(ulong ticket)
{
   if(!PositionSelectByTicket(ticket)) return;

   double entry_price = PositionGetDouble(POSITION_PRICE_OPEN);
   double current_sl  = PositionGetDouble(POSITION_SL);
   double current_tp  = PositionGetDouble(POSITION_TP);
   long   pos_type    = PositionGetInteger(POSITION_TYPE);
   datetime pos_time  = (datetime)PositionGetInteger(POSITION_TIME);

   double atr[1];
   if(CopyBuffer(g_h_atr, 0, 0, 1, atr) < 1 || atr[0] <= 0.0) return;
   double current_atr = atr[0];

   MqlTick tick;
   if(!SymbolInfoTick(Symbol(), tick)) return;

   double point_val = SymbolInfoDouble(Symbol(), SYMBOL_POINT);
   double min_stop_step = MathMax(point_val, SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_SIZE)) * 2.0;

   bool is_locked = (pos_type == POSITION_TYPE_BUY)  ? (current_sl > 0.0 && current_sl >= entry_price)
                   : (pos_type == POSITION_TYPE_SELL) ? (current_sl > 0.0 && current_sl <= entry_price)
                   : false;

   int bars_held = CustomBarShift(Symbol(), PERIOD_M1, pos_time, false);
   if(bars_held >= InpMaxHoldBars && (!InpExtendAfterBreakeven || !is_locked))
   {
      PrintFormat("[EXIT] Held %d bars with no profit lock (limit %d). Closing.", bars_held, InpMaxHoldBars);
      g_trade.PositionClose(ticket);
      return;
   }

   if(!InpUseTrailingManagement) return; // pure SL/TP mode

   double be_trigger_dist = current_atr * InpBreakevenTrigger_ATR;
   double be_lock_dist    = current_atr * InpBreakevenLock_ATR;
   double trail_dist      = current_atr * InpTrailing_ATR;

   if(pos_type == POSITION_TYPE_BUY)
   {
      double profit_dist = tick.bid - entry_price;
      if(profit_dist >= be_trigger_dist)
      {
         double lock_sl = NormalizePrice(entry_price + be_lock_dist);
         if(lock_sl - current_sl > min_stop_step)
         {
            if(g_trade.PositionModify(ticket, lock_sl, current_tp))
               PrintFormat("[MGMT] BUY breakeven locked: SL -> %.2f", lock_sl);
            return;
         }
      }
      if(profit_dist >= trail_dist)
      {
         double target_sl = NormalizePrice(tick.bid - trail_dist);
         if(target_sl - current_sl > min_stop_step)
         {
            if(g_trade.PositionModify(ticket, target_sl, current_tp))
               PrintFormat("[MGMT] BUY trailing: SL -> %.2f", target_sl);
         }
      }
   }
   else if(pos_type == POSITION_TYPE_SELL)
   {
      double profit_dist = entry_price - tick.ask;
      if(profit_dist >= be_trigger_dist)
      {
         double lock_sl = NormalizePrice(entry_price - be_lock_dist);
         if(current_sl == 0.0 || current_sl - lock_sl > min_stop_step)
         {
            if(g_trade.PositionModify(ticket, lock_sl, current_tp))
               PrintFormat("[MGMT] SELL breakeven locked: SL -> %.2f", lock_sl);
            return;
         }
      }
      if(profit_dist >= trail_dist)
      {
         double target_sl = NormalizePrice(tick.ask + trail_dist);
         if(current_sl == 0.0 || current_sl - target_sl > min_stop_step)
         {
            if(g_trade.PositionModify(ticket, target_sl, current_tp))
               PrintFormat("[MGMT] SELL trailing: SL -> %.2f", target_sl);
         }
      }
   }
}
