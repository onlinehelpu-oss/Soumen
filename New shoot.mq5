//+------------------------------------------------------------------+
//|                                                    New shoot.mq5 |
//|                                                            Jules |
//|                                                                  |
//| Optimized for: GOLD.i# / GOLD (XAUUSD) on XM Platform            |
//| Features:                                                        |
//| - Strictly 1 open position at a time (zero double entries).      |
//| - Anti-Race Lock on orders (cannot double-trigger on rapid ticks)|
//| - Custom candle geometry: Rejection red candle with upper wick   |
//|   minimum more than 50% (or configurable) of total candle range.  |
//| - Previous candle must be green.                                 |
//| - High above EMA, Close below EMA (configurable timeframe & period).|
//| - Entry: Breakout of signal candle low on the next immediate bar.|
//| - Stop Loss: Signal candle High.                                 |
//| - Take Profit: Configurable Risk:Reward ratio.                   |
//| - Full compatibility with XM broker volume steps and tick sizes.|
//+------------------------------------------------------------------+
#property copyright "Jules"
#property link      ""
#property version   "1.30"

#include <Trade\Trade.mqh>

//--- Inputs
input group "=== Strategy Parameters ==="
input ENUM_TIMEFRAMES InpTimeframe       = PERIOD_M15;      // Timeframe (e.g. 1m, 3m, 5m, 15m, 30m, 1H, 4H, 1D etc.)
input int             InpEMAPeriod      = 21;              // EMA Period Close-basis
input ENUM_MA_METHOD  InpMAMethod       = MODE_EMA;        // MA Method
input ENUM_APPLIED_PRICE InpAppliedPrice = PRICE_CLOSE;     // Applied Price
input double          InpMinUpperWickPct= 50.0;            // Min Upper Wick % of candle (default >=50%)
input double          InpMinCandlePoints = 50.0;           // Ignore tiny candles: Min range in Points (e.g. 50 points = $0.50 on GOLD.i#)
input double          InpMinCandlePct    = 0.05;           // Ignore tiny candles: Min range as % of close (0 to disable)

input group "=== Risk Management ==="
input double          InpLotSize        = 0.1;             // Lot Size (if fixed lot size)
input bool            InpUseRiskPercent = false;           // Use Risk % sizing
input double          InpRiskPercent    = 1.0;             // Risk % per trade of Account Balance
input double          InpRiskReward     = 2.0;             // Risk to Reward Ratio (e.g. 2.0 for 1:2, 1.0 for 1:1)
input ulong           InpMagicNumber    = 881234;          // Unique Magic Number to identify trades
input ulong           InpSlippage       = 10;              // Slippage in points (optimized for GOLD.i# volatility)

//--- Globals
CTrade      m_trade;
int         m_ema_handle = INVALID_HANDLE;
datetime    m_last_bar_time = 0;
bool        m_signal_active = false;
double      m_signal_high = 0;
double      m_signal_low = 0;
datetime    m_signal_time = 0;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // Set magic number
   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_trade.SetDeviationInPoints(InpSlippage);

   // Create EMA indicator handle
   m_ema_handle = iMA(_Symbol, InpTimeframe, InpEMAPeriod, 0, InpMAMethod, InpAppliedPrice);
   if(m_ema_handle == INVALID_HANDLE)
   {
      Print("[Init] Failed to create EMA indicator handle! Error: ", GetLastError());
      return(INIT_FAILED);
   }

   m_last_bar_time = 0;
   m_signal_active = false;
   m_signal_high = 0;
   m_signal_low = 0;
   m_signal_time = 0;

   Print("[Init] New shoot EA Initialized successfully on symbol: ", _Symbol);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(m_ema_handle != INVALID_HANDLE)
   {
      IndicatorRelease(m_ema_handle);
   }
   Print("[Deinit] New shoot EA Deinitialized.");
}

//+------------------------------------------------------------------+
//| Helper to get current bar time                                   |
//+------------------------------------------------------------------+
datetime GetCurrentBarTime()
{
   datetime time[1];
   if(CopyTime(_Symbol, InpTimeframe, 0, 1, time) > 0)
   {
      return time[0];
   }
   return 0;
}

//+------------------------------------------------------------------+
//| Helper to check if a position is open (Guarantees one position)  |
//+------------------------------------------------------------------+
bool IsPositionOpen()
{
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetString(POSITION_SYMBOL) == _Symbol)
         {
            if(PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
            {
               return true;
            }
         }
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Helper to normalize volume                                       |
//+------------------------------------------------------------------+
double NormalizeVolume(double volume)
{
   double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   if(step_lot <= 0) step_lot = 0.01;

   double normalized = MathRound(volume / step_lot) * step_lot;
   if(normalized < min_lot) normalized = min_lot;
   if(normalized > max_lot) normalized = max_lot;

   return normalized;
}

//+------------------------------------------------------------------+
//| Helper to normalize price                                        |
//+------------------------------------------------------------------+
double NormalizePrice(double price)
{
   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tick_size <= 0) return price;
   return MathRound(price / tick_size) * tick_size;
}

//+------------------------------------------------------------------+
//| Helper to calculate lot size based on risk                       |
//+------------------------------------------------------------------+
double CalculateLotSize(double price_gap)
{
   if(!InpUseRiskPercent)
   {
      return NormalizeVolume(InpLotSize);
   }

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double risk_amount = balance * (InpRiskPercent / 100.0);

   double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);

   if(tick_value <= 0 || tick_size <= 0 || point <= 0 || price_gap <= 0)
   {
      return NormalizeVolume(InpLotSize);
   }

   double risk_points = price_gap / point;
   double value_per_point = tick_value * (point / tick_size);
   if(value_per_point <= 0)
   {
      return NormalizeVolume(InpLotSize);
   }

   double lot_size = risk_amount / (risk_points * value_per_point);
   return NormalizeVolume(lot_size);
}

//+------------------------------------------------------------------+
//| Check signal candle rules                                        |
//+------------------------------------------------------------------+
bool CheckSignalCandle(double &out_high, double &out_low)
{
   MqlRates rates[3];
   // Copy shift 1, 2, 3
   if(CopyRates(_Symbol, InpTimeframe, 1, 3, rates) < 3)
   {
      return false;
   }

   // Chronological order: rates[0] is shift 3, rates[1] is shift 2, rates[2] is shift 1.
   MqlRates prev_candle = rates[1];   // Shift 2
   MqlRates sig_candle = rates[2];    // Shift 1

   // 1. Previous candle of signal candle must be green
   if(prev_candle.close <= prev_candle.open)
   {
      return false;
   }

   // 2. Signal candle must be red
   if(sig_candle.close >= sig_candle.open)
   {
      return false;
   }

   // 3. EMA Check: Get EMA value at shift 1 (sig_candle.time)
   double ema_buffer[1];
   if(CopyBuffer(m_ema_handle, 0, 1, 1, ema_buffer) < 1)
   {
      return false;
   }
   double ema_val = ema_buffer[0];

   // Signal candle high must be above EMA, close must be below EMA
   if(sig_candle.high <= ema_val || sig_candle.close >= ema_val)
   {
      return false;
   }

   // 4. Geometry Check: Total range and ignore tiny candle filter
   double total_range = sig_candle.high - sig_candle.low;
   if(total_range <= 0)
   {
      return false;
   }

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(InpMinCandlePoints > 0 && total_range < InpMinCandlePoints * point)
   {
      return false;
   }
   if(InpMinCandlePct > 0 && (total_range / sig_candle.close) * 100.0 < InpMinCandlePct)
   {
      return false;
   }

   // Upper wick of a red candle: High - Open
   double upper_wick = sig_candle.high - sig_candle.open;
   double upper_wick_pct = (upper_wick / total_range) * 100.0;

   // Rejection candle: upper wick must be minimum configurable percentage of total candle (default >= 50%)
   if(upper_wick_pct < InpMinUpperWickPct)
   {
      return false;
   }

   // All conditions satisfied!
   out_high = sig_candle.high;
   out_low = sig_candle.low;
   return true;
}

//+------------------------------------------------------------------+
//| Update interactive chart comment dashboard                      |
//+------------------------------------------------------------------+
void UpdateDashboard()
{
   string text = "=== Flexible Upper Wick Rejection EA (New shoot) ===\n";
   text += "Symbol: " + _Symbol + "\n";
   text += "Timeframe: " + StringSubstr(EnumToString(InpTimeframe), 7) + "\n";
   text += "EMA Period: " + IntegerToString(InpEMAPeriod) + "\n";
   text += "Min Upper Wick %: " + DoubleToString(InpMinUpperWickPct, 1) + "%\n";
   text += "Min Candle Points: " + DoubleToString(InpMinCandlePoints, 1) + "\n";

   if(m_signal_active)
   {
      text += "Signal Status: ACTIVE (Waiting for breakout below Low of: " + DoubleToString(m_signal_low, _Digits) + ")\n";
      text += "Signal High (SL): " + DoubleToString(m_signal_high, _Digits) + "\n";
      text += "Signal Candle Time: " + TimeToString(m_signal_time, TIME_DATE|TIME_MINUTES) + "\n";
   }
   else
   {
      text += "Signal Status: IDLE (No active breakout pending)\n";
   }

   text += "Active Position: " + (IsPositionOpen() ? "YES" : "NO") + "\n";
   text += "Account Balance: " + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + "\n";

   Comment(text);
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // Always guarantee only one open position under this magic number is processed
   if(IsPositionOpen())
   {
      m_signal_active = false; // Disable breakout tracking when position is already open
      UpdateDashboard();
      return;
   }

   // Check for new bar
   datetime current_bar_time = GetCurrentBarTime();
   if(current_bar_time == 0) return; // Wait for valid connection/time

   if(current_bar_time != m_last_bar_time)
   {
      // New bar opened!
      m_last_bar_time = current_bar_time;

      // If we had an active signal on the previous bar, it is now expired/ignored because the next candle has completed.
      if(m_signal_active)
      {
         Print("[Expiry] Signal expired. Low (", m_signal_low, ") was not broken during the immediate next candle.");
         m_signal_active = false;
      }

      // Check for a new signal candle (shift 1)
      double out_high = 0;
      double out_low = 0;
      if(CheckSignalCandle(out_high, out_low))
      {
         m_signal_active = true;
         m_signal_high = out_high;
         m_signal_low = out_low;

         // Get the time of the signal candle (which is shift 1)
         datetime time_buf[1];
         if(CopyTime(_Symbol, InpTimeframe, 1, 1, time_buf) > 0)
         {
            m_signal_time = time_buf[0];
         }
         Print("[Signal] New Signal Candle Detected! High: ", m_signal_high, ", Low: ", m_signal_low, ", Time: ", m_signal_time);
      }
   }

   // Check for breakout entry if signal is active
   if(m_signal_active)
   {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      if(bid > 0 && bid < m_signal_low)
      {
         // Double safety check right before ordering
         if(!IsPositionOpen())
         {
            // --- ANTI-RACE LOCK PATTERN ---
            // Turn off signal active state IMMEDIATELY before placing order to prevent race conditions
            // from multi-ticking during trade processing.
            m_signal_active = false;

            // Breakout occurred! Execute entry
            double risk_price_gap = m_signal_high - m_signal_low;
            double lots = CalculateLotSize(risk_price_gap);

            double entry_price = bid;
            double sl = m_signal_high;
            double tp = entry_price - InpRiskReward * (sl - entry_price);

            // Normalize prices for broker compliance
            sl = NormalizePrice(sl);
            tp = NormalizePrice(tp);
            entry_price = NormalizePrice(entry_price);

            Print("[Entry] Breakout! Entering Sell order. Symbol: ", _Symbol, ", Lots: ", lots, ", Entry Price: ", entry_price, ", SL: ", sl, ", TP: ", tp);

            if(!m_trade.Sell(lots, _Symbol, entry_price, sl, tp, "Flexible Rejection Sell"))
            {
               Print("[Error] Error placing Sell order: ", GetLastError());
               // Re-enable signal active state ONLY on complete trade placement failure
               m_signal_active = true;
            }
         }
         else
         {
            m_signal_active = false;
         }
      }
   }

   UpdateDashboard();
}
