//+------------------------------------------------------------------+
//|                               VWAP_EMA_Pullback_Breakout_EA.mq5  |
//|                                  Copyright 2026, Jules (Agent)   |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, Jules (Agent)"
#property link      "https://www.mql5.com"
#property version   "1.00"
#property strict

// Include Standard Libraries
#include <Trade\SymbolInfo.mqh>
#include <Trade\Trade.mqh>

//+------------------------------------------------------------------+
//| Enums                                                            |
//+------------------------------------------------------------------+
enum ENUM_SIGNAL_TIMEFRAME
{
   TF_M1  = PERIOD_M1,   // M1 (1 Minute)
   TF_M3  = PERIOD_M3,   // M3 (3 Minutes)
   TF_M5  = PERIOD_M5,   // M5 (5 Minutes)
   TF_M15 = PERIOD_M15,  // M15 (15 Minutes) - Default
   TF_M30 = PERIOD_M30,  // M30 (30 Minutes)
   TF_H1  = PERIOD_H1,   // H1 (1 Hour)
   TF_H4  = PERIOD_H4,   // H4 (4 Hours)
   TF_D1  = PERIOD_D1    // D1 (1 Day)
};

enum ENUM_RISK_REWARD
{
   RR_1_1,     // 1:1
   RR_1_1_5,   // 1:1.5
   RR_1_2,     // 1:2 (Default)
   RR_1_3,     // 1:3
   RR_CUSTOM   // Custom
};

enum ENUM_MONEY_MANAGEMENT
{
   MM_FIXED_LOT, // Fixed Lot
   MM_RISK_PCT   // Risk %
};

//+------------------------------------------------------------------+
//| Input Parameters                                                 |
//+------------------------------------------------------------------+
input group "=== Signal Timeframe ==="
input ENUM_SIGNAL_TIMEFRAME InpTimeframe = TF_M15;         // Signal Timeframe

input group "=== EMA Settings ==="
input int                   InpEMAPeriod = 34;             // EMA Period
input ENUM_APPLIED_PRICE    InpEMAPrice = PRICE_CLOSE;     // EMA Applied Price

input group "=== VWAP Settings ==="
input bool                  InpEnableDailyVWAP = true;     // Enable Daily VWAP
input bool                  InpResetVWAPDaily = true;      // Reset VWAP at start of day
input bool                  InpEnableSessionVWAP = false;  // Enable Session VWAP
input string                InpSessionStart = "09:00";     // Session Start Time (HH:MM)
input ENUM_APPLIED_PRICE    InpVWAPSource = PRICE_CLOSE;   // VWAP Price Source

input group "=== Entry Settings ==="
input double                InpEntryBufferPoints = 0.0;    // Entry Buffer (points)
input double                InpMinCandlePoints = 0.0;      // Min Candle Range (points) - Avoid Tiny
input double                InpMinCandlePct = 0.0;         // Min Candle Range (%) - Avoid Tiny
input double                InpMaxCandlePoints = 0.0;      // Max Candle Range (points) - Ignore Too Big (0 to disable)
input double                InpMaxCandlePct = 0.0;         // Max Candle Range (%) - Ignore Too Big (0 to disable)

input group "=== Risk Reward Settings ==="
input ENUM_RISK_REWARD      InpRiskReward = RR_1_2;        // Risk Reward Ratio
input double                InpCustomRR = 2.0;             // Custom RR Multiplier

input group "=== Money Management ==="
input ENUM_MONEY_MANAGEMENT InpMMType = MM_FIXED_LOT;      // Money Management Type
input double                InpFixedLot = 0.1;             // Fixed Lot Size
input double                InpRiskPercent = 1.0;          // Risk % of Balance

input group "=== Trading Filters ==="
input bool                  InpOnePositionOnly = true;     // One Open Position Only
input bool                  InpUseSessionFilter = false;   // Trading Session Filter
input string                InpSessionStartTime = "09:00"; // Session Start (HH:MM)
input string                InpSessionEndTime = "22:00";   // Session End (HH:MM)
input ulong                 InpMagicNumber = 123456;       // Magic Number

//+------------------------------------------------------------------+
//| Global Variables                                                 |
//+------------------------------------------------------------------+
CSymbolInfo m_symbol;
CTrade      m_trade;
int         m_ema_handle = INVALID_HANDLE;

datetime    m_last_bar_time = 0;
bool        m_signal_active = false;
double      m_signal_high = 0.0;
double      m_signal_low = 0.0;
datetime    m_signal_bar_time = 0;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // Initialize Symbol Info
   if(!m_symbol.Name(_Symbol))
   {
      Print("Error: Failed to initialize CSymbolInfo for symbol: ", _Symbol);
      return INIT_FAILED;
   }
   m_symbol.Refresh();

   // Configure Trade class
   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_trade.SetDeviationInPoints(10);

   // Create EMA Handle
   m_ema_handle = iMA(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, InpEMAPeriod, 0, MODE_EMA, InpEMAPrice);
   if(m_ema_handle == INVALID_HANDLE)
   {
      Print("Error: Failed to create EMA indicator handle!");
      return INIT_FAILED;
   }

   // Reset Global Variables
   m_last_bar_time = 0;
   m_signal_active = false;
   m_signal_high = 0.0;
   m_signal_low = 0.0;
   m_signal_bar_time = 0;

   // Enable 1-second Timer for Dashboard and Real-Time updates
   EventSetTimer(1);

   // Print startup message
   PrintFormat("VWAP + EMA Trend Pullback Breakout EA initialized on %s. Timeframe: %s",
               _Symbol, EnumToString((ENUM_SIGNAL_TIMEFRAME)InpTimeframe));

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   // Clean up Timer
   EventKillTimer();

   // Clean up indicator handles
   if(m_ema_handle != INVALID_HANDLE)
   {
      IndicatorRelease(m_ema_handle);
      m_ema_handle = INVALID_HANDLE;
   }

   // Delete Chart Labels
   ObjectsDeleteAll(0, "VWAP_EA_");
   ChartRedraw();

   Print("VWAP + EMA Trend Pullback Breakout EA deinitialized.");
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // Standard sanity checks (bypass if running in Strategy Tester)
   bool is_tester = (bool)MQLInfoInteger(MQL_TESTER);
   if(!is_tester)
   {
      if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED))
         return;
   }

   datetime current_bar_times[1];
   if(CopyTime(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, 0, 1, current_bar_times) <= 0)
      return;

   datetime current_bar_time = current_bar_times[0];

   // 1. Process New Bar on the selected timeframe
   if(current_bar_time != m_last_bar_time)
   {
      // If we had a pending breakout signal, its one-candle window has now closed
      if(m_signal_active)
      {
         PrintFormat("[Signal Expired] Next candle failed to break High (%.5f) of Signal candle from %s. Discarding.",
                     m_signal_high, TimeToString(m_signal_bar_time));
         m_signal_active = false;
      }

      // Check Bar 1 (the completed candle) for a valid signal setup
      double open_arr[1], close_arr[1], high_arr[1], low_arr[1];
      datetime time_arr[1];

      if(CopyOpen(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, 1, 1, open_arr) > 0 &&
         CopyClose(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, 1, 1, close_arr) > 0 &&
         CopyHigh(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, 1, 1, high_arr) > 0 &&
         CopyLow(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, 1, 1, low_arr) > 0 &&
         CopyTime(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, 1, 1, time_arr) > 0)
      {
         double o_val = open_arr[0];
         double c_val = close_arr[0];
         double h_val = high_arr[0];
         double l_val = low_arr[0];

         double vwap_val = GetVWAP(1);
         double ema_val = GetEMA(1);

         // Evaluate candle size conditions (Avoid Tiny candle / Ignore Too Big candle)
         double candle_range = h_val - l_val;
         double candle_points = candle_range / m_symbol.Point();
         double candle_pct = (o_val > 0) ? (candle_range / o_val) * 100.0 : 0.0;

         bool is_tiny = false;
         if(InpMinCandlePoints > 0 && candle_points < InpMinCandlePoints) is_tiny = true;
         if(InpMinCandlePct > 0 && candle_pct < InpMinCandlePct) is_tiny = true;

         bool is_too_big = false;
         if(InpMaxCandlePoints > 0 && candle_points > InpMaxCandlePoints) is_too_big = true;
         if(InpMaxCandlePct > 0 && candle_pct > InpMaxCandlePct) is_too_big = true;

         if(!is_tiny && !is_too_big && vwap_val > 0 && ema_val > 0)
         {
            // Signal Candle Condition 1: any candle open below VWAP and close above VWAP
            bool cond1 = (o_val < vwap_val && c_val > vwap_val);

            // Signal Candle Condition 2: any candle crosses below EMA and close above EMA
            bool cond2 = (l_val < ema_val && c_val > ema_val);

            // Trend Filter: Current candle Close > VWAP AND VWAP > EMA
            bool trend_ok = (c_val > vwap_val && vwap_val > ema_val);

            if((cond1 || cond2) && trend_ok)
            {
               m_signal_active = true;
               m_signal_high = h_val;
               m_signal_low = l_val;
               m_signal_bar_time = time_arr[0];

               PrintFormat("[Signal Triggered] Valid Signal Candle at %s. High: %.5f, Low: %.5f. Range: %.1f points. Awaiting next-candle breakout above %.5f.",
                           TimeToString(m_signal_bar_time), m_signal_high, m_signal_low, candle_points, m_signal_high + InpEntryBufferPoints * m_symbol.Point());
            }
         }
      }
      m_last_bar_time = current_bar_time;
   }

   // 2. Monitor Breakout Trigger on Every Tick
   if(m_signal_active)
   {
      if(IsInTradingSession())
      {
         if(!InpOnePositionOnly || CountOpenPositions() == 0)
         {
            double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
            if (ask <= 0.0)
            {
               m_symbol.RefreshRates();
               ask = m_symbol.Ask();
            }
            double breakout_level = m_signal_high + InpEntryBufferPoints * m_symbol.Point();

            if(ask > 0.0 && ask > breakout_level)
            {
               double entry_price = ask;
               double sl_price = m_signal_low;
               double risk_dist = entry_price - sl_price;

               if(risk_dist > 0)
               {
                  // Calculate Take Profit based on Selected Risk Reward Ratio
                  double rr_mult = 2.0;
                  switch(InpRiskReward)
                  {
                     case RR_1_1:     rr_mult = 1.0; break;
                     case RR_1_1_5:   rr_mult = 1.5; break;
                     case RR_1_2:     rr_mult = 2.0; break;
                     case RR_1_3:     rr_mult = 3.0; break;
                     case RR_CUSTOM:  rr_mult = InpCustomRR; break;
                  }

                  double tp_price = entry_price + risk_dist * rr_mult;

                  // Position Sizing
                  double lot = InpFixedLot;
                  if(InpMMType == MM_RISK_PCT)
                  {
                     double balance = AccountInfoDouble(ACCOUNT_BALANCE);
                     double risk_amount = balance * (InpRiskPercent / 100.0);
                     double tick_val = m_symbol.TickValue();
                     double tick_sz = m_symbol.TickSize();
                     if(tick_sz > 0 && tick_val > 0)
                     {
                        double risk_per_lot = (risk_dist / tick_sz) * tick_val;
                        if(risk_per_lot > 0)
                        {
                           lot = risk_amount / risk_per_lot;
                        }
                     }
                  }

                  lot = NormalizeLotSize(lot);

                  PrintFormat("[Breakout Executed] Price %.5f > Level %.5f. Placing BUY. Lot: %.2f, SL: %.5f, TP: %.5f",
                              entry_price, breakout_level, lot, sl_price, tp_price);

                  if(ExecuteBuy(entry_price, sl_price, tp_price, lot))
                  {
                     m_signal_active = false; // Successfully filled, clear signal
                  }
               }
               else
               {
                  Print("[Error] Invalid risk distance (zero or negative). Discarding signal.");
                  m_signal_active = false;
               }
            }
         }
      }
   }

}

//+------------------------------------------------------------------+
//| Timer function                                                   |
//+------------------------------------------------------------------+
void OnTimer()
{
   UpdateDashboard();
}

//+------------------------------------------------------------------+
//| Helper: CustomBarShift implementation for MQL5                   |
//+------------------------------------------------------------------+
int CustomBarShift(string symbol, ENUM_TIMEFRAMES timeframe, datetime time, bool exact=false)
{
   datetime t_arr[];
   if(CopyTime(symbol, timeframe, 0, 1, t_arr) <= 0)
      return -1;

   datetime latest_time = t_arr[0];
   if(time >= latest_time)
      return 0;

   datetime bar_time[1];
   int copied = CopyTime(symbol, timeframe, time, 1, bar_time);
   if(copied <= 0)
      return -1;

   int bars = Bars(symbol, timeframe, bar_time[0], latest_time);
   if(bars > 0)
   {
      int idx = bars - 1;
      if(exact && bar_time[0] != time)
         return -1;
      return idx;
   }

   return -1;
}

//+------------------------------------------------------------------+
//| Helper: Calculate VWAP on the chart timeframe                    |
//+------------------------------------------------------------------+
double GetVWAP(int bar_index)
{
   datetime bar_time[1];
   if(CopyTime(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, bar_index, 1, bar_time) <= 0)
      return 0.0;

   MqlDateTime dt_bar;
   TimeToStruct(bar_time[0], dt_bar);

   datetime start_time = 0;

   if(InpEnableDailyVWAP)
   {
      MqlDateTime dt_start = dt_bar;
      dt_start.hour = 0;
      dt_start.min = 0;
      dt_start.sec = 0;
      start_time = StructToTime(dt_start);
   }

   if(InpEnableSessionVWAP)
   {
      MqlDateTime dt_start = dt_bar;
      int hour = 9, min = 0;
      string parts[];
      if(StringSplit(InpSessionStart, ':', parts) >= 2)
      {
         hour = (int)StringToInteger(parts[0]);
         min = (int)StringToInteger(parts[1]);
      }
      dt_start.hour = hour;
      dt_start.min = min;
      dt_start.sec = 0;
      datetime session_time = StructToTime(dt_start);

      if(bar_time[0] >= session_time)
      {
         start_time = session_time;
      }
      else
      {
         if(InpResetVWAPDaily)
         {
            dt_start.hour = 0;
            dt_start.min = 0;
            start_time = StructToTime(dt_start);
         }
         else
         {
            start_time = bar_time[0] - 86400; // default last 24h
         }
      }
   }

   // Safety check: Fallback to midnight of current bar if start_time is uninitialized
   if(start_time <= 0)
   {
      MqlDateTime dt_start = dt_bar;
      dt_start.hour = 0;
      dt_start.min = 0;
      dt_start.sec = 0;
      start_time = StructToTime(dt_start);
   }

   int start_bar_idx = CustomBarShift(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, start_time, false);
   if(start_bar_idx < 0)
      start_bar_idx = CustomBarShift(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, start_time, true);

   if(start_bar_idx < bar_index)
      start_bar_idx = bar_index;

   int count = start_bar_idx - bar_index + 1;
   if(count <= 0)
      return 0.0;

   double high[], low[], close[], open[];
   long volume[];

   // Bounds-safe array copying to prevent out-of-range runtime exceptions
   int copied = CopyHigh(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, bar_index, count, high);
   if(copied <= 0) return 0.0;

   if(CopyLow(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, bar_index, copied, low) < copied) return 0.0;
   if(CopyClose(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, bar_index, copied, close) < copied) return 0.0;
   if(CopyOpen(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, bar_index, copied, open) < copied) return 0.0;

   bool has_real_vol = false;
   if(CopyRealVolume(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, bar_index, copied, volume) >= copied)
   {
      for(int i = 0; i < copied; i++)
      {
         if(volume[i] > 0)
         {
            has_real_vol = true;
            break;
         }
      }
   }

   if(!has_real_vol)
   {
      if(CopyTickVolume(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, bar_index, copied, volume) < copied)
         return 0.0;
   }

   double sum_pv = 0.0;
   double sum_v = 0.0;

   for(int i = 0; i < copied; i++)
   {
      double price = 0.0;
      switch(InpVWAPSource)
      {
         case PRICE_CLOSE:     price = close[i]; break;
         case PRICE_OPEN:      price = open[i]; break;
         case PRICE_HIGH:      price = high[i]; break;
         case PRICE_LOW:       price = low[i]; break;
         case PRICE_MEDIAN:    price = (high[i] + low[i]) / 2.0; break;
         case PRICE_TYPICAL:   price = (high[i] + low[i] + close[i]) / 3.0; break;
         case PRICE_WEIGHTED:  price = (high[i] + low[i] + 2.0 * close[i]) / 4.0; break;
         default:              price = close[i]; break;
      }

      double vol = (double)volume[i];
      if(vol <= 0) vol = 1.0;

      sum_pv += price * vol;
      sum_v += vol;
   }

   if(sum_v > 0)
      return sum_pv / sum_v;

   return 0.0;
}

//+------------------------------------------------------------------+
//| Helper: Get EMA value                                            |
//+------------------------------------------------------------------+
double GetEMA(int bar_index)
{
   double ema_val[1];
   if(CopyBuffer(m_ema_handle, 0, bar_index, 1, ema_val) <= 0)
      return 0.0;
   return ema_val[0];
}

//+------------------------------------------------------------------+
//| Helper: Count open positions for this Symbol & Magic Number     |
//+------------------------------------------------------------------+
int CountOpenPositions()
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
//| Helper: Check if current time is within trading session          |
//+------------------------------------------------------------------+
bool IsInTradingSession()
{
   if(!InpUseSessionFilter)
      return true;

   datetime now_time = TimeCurrent();
   MqlDateTime dt;
   TimeToStruct(now_time, dt);

   MqlDateTime dt_start = dt;
   int shour = 9, smin = 0;
   string sparts[];
   if(StringSplit(InpSessionStartTime, ':', sparts) >= 2)
   {
      shour = (int)StringToInteger(sparts[0]);
      smin = (int)StringToInteger(sparts[1]);
   }
   dt_start.hour = shour;
   dt_start.min = smin;
   dt_start.sec = 0;
   datetime start_dt = StructToTime(dt_start);

   MqlDateTime dt_end = dt;
   int ehour = 22, emin = 0;
   string eparts[];
   if(StringSplit(InpSessionEndTime, ':', eparts) >= 2)
   {
      ehour = (int)StringToInteger(eparts[0]);
      emin = (int)StringToInteger(eparts[1]);
   }
   dt_end.hour = ehour;
   dt_end.min = emin;
   dt_end.sec = 0;
   datetime end_dt = StructToTime(dt_end);

   if(start_dt <= end_dt)
   {
      return (now_time >= start_dt && now_time <= end_dt);
   }
   else
   {
      // Midnight boundary cross
      return (now_time >= start_dt || now_time <= end_dt);
   }
}

//+------------------------------------------------------------------+
//| Helper: Normalize lot size based on symbol properties            |
//+------------------------------------------------------------------+
double NormalizeLotSize(double lot)
{
   m_symbol.Refresh();

   double min_lot = m_symbol.LotsMin();
   double max_lot = m_symbol.LotsMax();
   double step_lot = m_symbol.LotsStep();

   if(step_lot <= 0.0)
      step_lot = 0.01;

   double normalized = MathRound(lot / step_lot) * step_lot;

   // Determine precision from dynamic step lot size
   int precision = 2;
   if(step_lot >= 1.0) precision = 0;
   else if(step_lot >= 0.1) precision = 1;
   else if(step_lot >= 0.01) precision = 2;
   else if(step_lot >= 0.001) precision = 3;
   else if(step_lot >= 0.0001) precision = 4;

   normalized = NormalizeDouble(normalized, precision);

   if(normalized < min_lot) normalized = min_lot;
   if(normalized > max_lot) normalized = max_lot;

   return normalized;
}

//+------------------------------------------------------------------+
//| Helper: Execute BUY trade via CTrade                            |
//+------------------------------------------------------------------+
bool ExecuteBuy(double entry, double sl, double tp, double lot)
{
   // Set proper filling mode dynamically for compatibility across all brokers (e.g. XM)
   uint filling = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0)
      m_trade.SetTypeFilling(ORDER_FILLING_FOK);
   else if((filling & SYMBOL_FILLING_IOC) != 0)
      m_trade.SetTypeFilling(ORDER_FILLING_IOC);
   else
      m_trade.SetTypeFilling(ORDER_FILLING_RETURN);

   if(m_trade.Buy(lot, _Symbol, entry, sl, tp, "VWAP + EMA Pullback"))
   {
      ulong retcode = m_trade.ResultRetcode();
      if(retcode == TRADE_RETCODE_DONE || retcode == TRADE_RETCODE_PLACED)
      {
         PrintFormat("[BUY Success] Price: %.5f, Lot: %.2f, SL: %.5f, TP: %.5f. Ticket: %I64u",
                     m_trade.ResultPrice(), lot, sl, tp, m_trade.ResultDeal());
         return true;
      }
      else
      {
         PrintFormat("[BUY Failed] Error Code: %u (%s)", retcode, m_trade.ResultComment());
      }
   }
   else
   {
      PrintFormat("[BUY Failed] Trade call failed. Code: %u (%s)", m_trade.ResultRetcode(), m_trade.ResultComment());
   }
   return false;
}

//+------------------------------------------------------------------+
//| Helper: Draw Dashboard labels                                    |
//+------------------------------------------------------------------+
void DrawLabel(string name, string text, int x, int y, color clr)
{
   string obj_name = "VWAP_EA_" + name;
   if(ObjectFind(0, obj_name) < 0)
   {
      ObjectCreate(0, obj_name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, obj_name, OBJPROP_XDISTANCE, x);
      ObjectSetInteger(0, obj_name, OBJPROP_YDISTANCE, y);
      ObjectSetInteger(0, obj_name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetString(0, obj_name, OBJPROP_FONT, "Segoe UI");
      ObjectSetInteger(0, obj_name, OBJPROP_FONTSIZE, 10);
      ObjectSetInteger(0, obj_name, OBJPROP_SELECTABLE, false);
   }
   else
   {
      ObjectSetInteger(0, obj_name, OBJPROP_YDISTANCE, y);
   }
   ObjectSetString(0, obj_name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, obj_name, OBJPROP_COLOR, clr);
}

//+------------------------------------------------------------------+
//| Helper: Update on-chart real-time dashboard                      |
//+------------------------------------------------------------------+
void UpdateDashboard()
{
   // Skip dashboard processing in Strategy Tester non-visual mode to maximize speed
   if(MQLInfoInteger(MQL_TESTER) && !MQLInfoInteger(MQL_VISUAL_MODE))
      return;

   int x_start = 20;
   int y_start = 20;
   int y_spacing = 18;

   double current_vwap = GetVWAP(0);
   double current_ema = GetEMA(0);

   double close_val = 0.0;
   double close_arr[1];
   if(CopyClose(_Symbol, (ENUM_TIMEFRAMES)InpTimeframe, 0, 1, close_arr) > 0)
   {
      close_val = close_arr[0];
   }

   string trend_str = "NEUTRAL";
   color trend_color = clrLightGray;
   if(close_val > current_vwap && current_vwap > current_ema)
   {
      trend_str = "BULLISH";
      trend_color = clrMediumSpringGreen;
   }
   else if(close_val < current_vwap && current_vwap < current_ema)
   {
      trend_str = "BEARISH";
      trend_color = clrTomato;
   }

   DrawLabel("Title", "=== VWAP + EMA Trend Pullback Breakout EA ===", x_start, y_start, clrGold); y_start += y_spacing;
   DrawLabel("TF", "Signal Timeframe: " + EnumToString((ENUM_SIGNAL_TIMEFRAME)InpTimeframe), x_start, y_start, clrWhite); y_start += y_spacing;
   DrawLabel("EMA", StringFormat("EMA Filter: Period %d, Price %s (Current: %.5f)", InpEMAPeriod, EnumToString(InpEMAPrice), current_ema), x_start, y_start, clrWhite); y_start += y_spacing;
   DrawLabel("VWAP", StringFormat("VWAP Filter: %s (Current: %.5f)", InpEnableDailyVWAP ? "Daily" : "Session", current_vwap), x_start, y_start, clrWhite); y_start += y_spacing;
   DrawLabel("Trend", "Trend Condition (Close > VWAP > EMA): " + trend_str, x_start, y_start, trend_color); y_start += y_spacing;

   string sig_status = "No Active Signal";
   color sig_color = clrLightGray;
   if(m_signal_active)
   {
      sig_status = StringFormat("Breakout Pending (High: %.5f, Low: %.5f, Entry Level: %.5f)",
                                m_signal_high, m_signal_low, m_signal_high + InpEntryBufferPoints * m_symbol.Point());
      sig_color = clrYellow;
   }
   DrawLabel("Signal", "Signal Status: " + sig_status, x_start, y_start, sig_color); y_start += y_spacing;

   int pos = CountOpenPositions();
   DrawLabel("Pos", StringFormat("Open Positions: %d | Limit: %s", pos, InpOnePositionOnly ? "One position max" : "No limit"), x_start, y_start, clrWhite); y_start += y_spacing;

   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   DrawLabel("Acc", StringFormat("Balance: %.2f | Equity: %.2f", bal, eq), x_start, y_start, clrWhite);

   ChartRedraw();
}
