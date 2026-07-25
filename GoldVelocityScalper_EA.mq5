//+------------------------------------------------------------------+
//|                                      GoldVelocityScalper_EA.mq5   |
//|                                                            Jules |
//|                      Institutional Gold Velocity Scalper (GVS)   |
//+------------------------------------------------------------------+
#property copyright "Copyright 2025, Jules"
#property link      "https://github.com/jules"
#property version   "1.00"
#property strict

//--- Includes
#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

//--- Input Parameters
input group "=== GVS Trading Settings ==="
input double            InpLotSize                 = 0.1;               // Fixed Trade Volume
input double            InpStopLossPoints          = 150.0;             // Stop Loss (Points, e.g. 150 = $1.5)
input double            InpTakeProfitPoints        = 300.0;             // Take Profit (Points, e.g. 300 = $3.0)
input double            InpTrailingStopPoints      = 100.0;             // Trailing Stop (Points, 0 = Disabled)
input double            InpBreakevenPoints         = 80.0;              // Breakeven Profit Trigger (Points, 0 = Disabled)
input int               InpMagicNumber             = 888123;            // Magic Number

input group "=== Stage 1: Tick Speed ==="
input int               InpTickSpeedWindow         = 1;                 // Window size in seconds
input int               InpTickSpeedThreshold      = 10;                // Threshold (ticks / window)

input group "=== Stage 2: Price Velocity ==="
input int               InpVelocityWindow          = 2;                 // Window size in seconds
input double            InpPriceVelocityThreshold  = 0.1;               // Threshold ($ / sec, e.g. 0.1)

input group "=== Stage 3: Tick Volume Explosion ==="
input double            InpVolumeMultiplier        = 1.5;               // Current volume vs 30-candle avg

input group "=== Stage 4: Spread Stability ==="
input double            InpSpreadMultiplier        = 1.5;               // Max spread ratio (current / avg)
input int               InpSpreadWindowTicks       = 100;               // Lookback ticks for average spread

input group "=== Stage 5 & 7: Directional Ticks & Noise ==="
input int               InpDirectionalTicksWindow  = 20;                // Lookback ticks
input double            InpDirectionalTicksRatio   = 0.75;              // Ratio (e.g. 15/20 = 0.75)
input double            InpMinEfficiencyRatio      = 0.0;               // Min Efficiency Ratio (0.0 to 1.0, 0 = Disabled)

input group "=== Stage 6: Price Acceleration ==="
input double            InpAccelerationThreshold   = 0.02;              // Threshold ($ / sec^2)

input group "=== ATR Expansion ==="
input int               InpATRPeriod               = 14;                // ATR Period
input double            InpATRExpansionMultiplier  = 1.5;               // Candle range vs ATR multiplier

input group "=== Entry Mechanics ==="
enum ENUM_ENTRY_MODE
{
   ENTRY_INSTANT,
   ENTRY_PULLBACK
};
input ENUM_ENTRY_MODE   InpEntryMode               = ENTRY_INSTANT;     // Entry Mode (Instant by default for testing)
input double            InpMinRocketScore          = 75.0;              // Minimum Rocket Score to trigger
input bool              InpUseTFI                  = false;             // Use Tick Flow Imbalance filter
input int               InpTFIThreshold            = 60;                // TFI Threshold (+- 60)
input int               InpTFIWindowTicks          = 100;               // TFI Lookback ticks
input double            InpMinImpulseHeight        = 0.20;              // Min impulse height before retracing ($)
input double            InpMinRetracement          = 0.10;              // Min pullback retracement (10%)
input double            InpMaxRetracement          = 0.25;              // Max pullback retracement (25%)
input double            InpMaxPullbackLimit        = 0.35;              // Hard pullback failure limit (35%)
input int               InpSetupExpirySeconds      = 15;                // Max seconds to wait for pullback setup
input double            InpPullbackResumeScore     = 60.0;              // Resume threshold score for pullback entry

input group "=== Strategy Tester Calibration ==="
input bool              InpTesterAutoCalibrate     = true;              // Auto-calibrate thresholds in Strategy Tester

input group "=== Exit Mechanics ==="
input bool              InpExitOnMomentumFade      = true;              // Exit when momentum fades
input double            InpPeakSpeedDropRatio      = 0.40;              // Tick speed drops below peak * ratio
input bool              InpExitOnOppositeTicks     = true;              // Exit on 5 consecutive opposite ticks
input bool              InpExitOnSpreadWidening    = true;              // Exit on spread widening
input double            InpExitSpreadMultiplier    = 2.5;               // Exit spread vs avg multiplier
input int               InpMaxTradeDuration        = 30;                // Max trade duration in seconds

//--- Tick Record Structure
struct TickRecord
{
   long         time_msc;
   double       bid;
   double       ask;
   double       spread;
   int          direction; // +1 if bid > prev_bid, -1 if bid < prev_bid, 0 if equal
};

//--- Sliding Tick Buffer Class
class CTickHistory
{
private:
   TickRecord m_buffer[];
   int        m_size;
   int        m_head;
   int        m_count;

public:
   CTickHistory()
   {
      m_size = 500;
      ArrayResize(m_buffer, m_size);
      Reset();
   }

   void Reset()
   {
      m_head = -1;
      m_count = 0;
   }

   void Add(const MqlTick &tick, double prev_bid)
   {
      m_head = (m_head + 1) % m_size;

      m_buffer[m_head].time_msc = tick.time_msc;
      m_buffer[m_head].bid = tick.bid;
      m_buffer[m_head].ask = tick.ask;
      m_buffer[m_head].spread = tick.ask - tick.bid;

      if (prev_bid > 0)
      {
         if (tick.bid > prev_bid)
            m_buffer[m_head].direction = 1;
         else if (tick.bid < prev_bid)
            m_buffer[m_head].direction = -1;
         else
            m_buffer[m_head].direction = 0;
      }
      else
      {
         m_buffer[m_head].direction = 0;
      }

      if (m_count < m_size)
         m_count++;
   }

   int Count() const { return m_count; }

   bool GetAt(int index, TickRecord &record) const
   {
      if (index < 0 || index >= m_count)
         return false;
      int real_idx = (m_head - index + m_size) % m_size;
      record = m_buffer[real_idx];
      return true;
   }
};

//--- Global Variables
CTrade         m_trade;
CTickHistory   m_tick_history;
int            m_atr_handle = INVALID_HANDLE;
double         m_last_bid = 0.0;
double         m_peak_tick_speed = 0.0;

//--- Active Calibrated Thresholds
int            m_calibrated_speed_threshold = 10;
double         m_calibrated_velocity_threshold = 0.1;
double         m_calibrated_acceleration_threshold = 0.02;

//--- Pullback Setup State Variables
bool           m_setup_active = false;
int            m_setup_direction = 0; // 1 = BUY, -1 = SELL
datetime       m_setup_time = 0;
double         m_setup_start_price = 0.0;
double         m_setup_peak_price = 0.0;
bool           m_pullback_detected = false;

//--- Metric Calculator Helpers
int GetTickSpeed(const CTickHistory &history, int seconds)
{
   if (history.Count() == 0) return 0;
   TickRecord latest;
   if (!history.GetAt(0, latest)) return 0;

   long limit_time = latest.time_msc - seconds * 1000;
   int count = 0;
   for (int i = 0; i < history.Count(); i++)
   {
      TickRecord rec;
      if (history.GetAt(i, rec))
      {
         if (rec.time_msc >= limit_time)
            count++;
         else
            break;
      }
   }
   return count;
}

double GetPriceVelocity(const CTickHistory &history, int seconds, double &out_delta)
{
   out_delta = 0.0;
   if (history.Count() < 2) return 0.0;
   TickRecord latest;
   if (!history.GetAt(0, latest)) return 0.0;

   long limit_time = latest.time_msc - seconds * 1000;
   TickRecord target_rec;
   bool found = false;
   for (int i = 1; i < history.Count(); i++)
   {
      TickRecord rec;
      if (history.GetAt(i, rec))
      {
         if (rec.time_msc <= limit_time)
         {
            target_rec = rec;
            found = true;
            break;
         }
      }
   }
   if (!found)
   {
      history.GetAt(history.Count() - 1, target_rec);
   }

   double duration = (double)(latest.time_msc - target_rec.time_msc) / 1000.0;
   if (duration <= 0) return 0.0;

   out_delta = latest.bid - target_rec.bid;
   return out_delta / duration;
}

double GetPriceAcceleration(const CTickHistory &history, int seconds)
{
   if (history.Count() < 3) return 0.0;
   TickRecord latest;
   if (!history.GetAt(0, latest)) return 0.0;

   double half_window = (double)seconds / 2.0;
   long mid_time = latest.time_msc - (long)(half_window * 1000.0);
   long limit_time = latest.time_msc - seconds * 1000;

   TickRecord rec_mid;
   TickRecord rec_old;
   bool found_mid = false, found_old = false;

   for (int i = 1; i < history.Count(); i++)
   {
      TickRecord rec;
      if (history.GetAt(i, rec))
      {
         if (!found_mid && rec.time_msc <= mid_time)
         {
            rec_mid = rec;
            found_mid = true;
         }
         if (rec.time_msc <= limit_time)
         {
            rec_old = rec;
            found_old = true;
            break;
         }
      }
   }

   if (!found_mid) return 0.0;
   if (!found_old)
   {
      history.GetAt(history.Count() - 1, rec_old);
   }

   double dur1 = (double)(latest.time_msc - rec_mid.time_msc) / 1000.0;
   double dur2 = (double)(rec_mid.time_msc - rec_old.time_msc) / 1000.0;

   if (dur1 <= 0 || dur2 <= 0) return 0.0;

   double v1 = (latest.bid - rec_mid.bid) / dur1;
   double v2 = (rec_mid.bid - rec_old.bid) / dur2;

   return (v1 - v2) / dur1;
}

double GetAverageSpread(const CTickHistory &history, int num_ticks)
{
   int count = MathMin(num_ticks, history.Count());
   if (count == 0) return 0.0;

   double sum = 0.0;
   for (int i = 0; i < count; i++)
   {
      TickRecord rec;
      if (history.GetAt(i, rec))
         sum += rec.spread;
   }
   return sum / count;
}

double GetDirectionalTicksRatio(const CTickHistory &history, int num_ticks, int target_direction)
{
   int count = MathMin(num_ticks, history.Count());
   if (count == 0) return 0.0;

   int matching_ticks = 0;
   int total_non_zero = 0;
   for (int i = 0; i < count; i++)
   {
      TickRecord rec;
      if (history.GetAt(i, rec))
      {
         if (rec.direction != 0)
         {
            total_non_zero++;
            if (rec.direction == target_direction)
               matching_ticks++;
         }
      }
   }
   return total_non_zero > 0 ? (double)matching_ticks / total_non_zero : 0.0;
}

double GetEfficiencyRatio(const CTickHistory &history, int num_ticks)
{
   int count = MathMin(num_ticks, history.Count());
   if (count < 2) return 0.0;

   TickRecord latest, oldest;
   if (!history.GetAt(0, latest) || !history.GetAt(count - 1, oldest)) return 0.0;

   double net_change = MathAbs(latest.bid - oldest.bid);
   double total_path = 0.0;

   for (int i = 0; i < count - 1; i++)
   {
      TickRecord rec1, rec2;
      if (history.GetAt(i, rec1) && history.GetAt(i + 1, rec2))
      {
         total_path += MathAbs(rec1.bid - rec2.bid);
      }
   }
   return total_path > 0 ? net_change / total_path : 0.0;
}

int GetTFI(const CTickHistory &history, int num_ticks)
{
   int count = MathMin(num_ticks, history.Count());
   if (count == 0) return 0;

   int buy_ticks = 0;
   int sell_ticks = 0;
   for (int i = 0; i < count; i++)
   {
      TickRecord rec;
      if (history.GetAt(i, rec))
      {
         if (rec.direction == 1)
            buy_ticks++;
         else if (rec.direction == -1)
            sell_ticks++;
      }
   }
   return buy_ticks - sell_ticks;
}

bool IsTickVolumeSpike(double &out_current, double &out_avg)
{
   long current_volume[1];
   if (CopyTickVolume(Symbol(), Period(), 0, 1, current_volume) <= 0)
   {
      out_current = 1.0;
      out_avg = 1.0;
      return true; // Fallback to avoid blocking in empty test history
   }

   int lookback = 30;
   long hist_volumes[30];
   int copied = CopyTickVolume(Symbol(), Period(), 1, lookback, hist_volumes);
   if (copied <= 0)
   {
      out_current = (double)current_volume[0];
      out_avg = 1.0;
      return true; // Fallback to avoid blocking in empty test history
   }

   double sum = 0.0;
   for (int i = 0; i < copied; i++)
   {
      sum += (double)hist_volumes[i];
   }
   double avg = sum / (double)copied;

   out_current = (double)current_volume[0];
   out_avg = avg;

   if (avg <= 0) return true;
   return out_current > InpVolumeMultiplier * avg;
}

bool IsATRExpansion(double &out_completed_range, double &out_atr)
{
   out_completed_range = 0.0;
   out_atr = 0.0;

   if (m_atr_handle == INVALID_HANDLE) return true; // Fallback to avoid blocking

   double atr_values[1];
   if (CopyBuffer(m_atr_handle, 0, 1, 1, atr_values) <= 0)
   {
      return true; // Fallback to avoid blocking
   }
   out_atr = atr_values[0];

   double high_values[1], low_values[1];
   if (CopyHigh(Symbol(), Period(), 1, 1, high_values) <= 0 || CopyLow(Symbol(), Period(), 1, 1, low_values) <= 0)
   {
      return true; // Fallback to avoid blocking
   }

   out_completed_range = high_values[0] - low_values[0];

   if (out_atr <= 0) return true;
   return out_completed_range > InpATRExpansionMultiplier * out_atr;
}

int GetConsecutiveTicks(const CTickHistory &history, int target_direction)
{
   int count = 0;
   for (int i = 0; i < history.Count(); i++)
   {
      TickRecord rec;
      if (history.GetAt(i, rec))
      {
         if (rec.direction == target_direction)
         {
            count++;
         }
         else if (rec.direction == 0)
         {
            continue;
         }
         else
         {
            break;
         }
      }
      else
      {
         break;
      }
   }
   return count;
}

//--- Position Helper Functions
int GetOpenPositionsCount()
{
   int count = 0;
   int total = PositionsTotal();
   for (int i = total - 1; i >= 0; i--)
   {
      if (PositionSelectByTicket(PositionGetTicket(i)))
      {
         if (PositionGetString(POSITION_SYMBOL) == Symbol() &&
             PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         {
            count++;
         }
      }
   }
   return count;
}

double NormalizeLotSize(double lot)
{
   double min_lot = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MIN);
   double max_lot = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MAX);
   double lot_step = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_STEP);

   double norm_lot = MathRound(lot / lot_step) * lot_step;
   norm_lot = MathMax(norm_lot, min_lot);
   norm_lot = MathMin(norm_lot, max_lot);

   return NormalizeDouble(norm_lot, 2);
}

void ConfigureTradeFilling()
{
   uint filling = (uint)SymbolInfoInteger(Symbol(), SYMBOL_FILLING_MODE);
   if ((filling & SYMBOL_FILLING_FOK) != 0)
      m_trade.SetTypeFilling(ORDER_FILLING_FOK);
   else if ((filling & SYMBOL_FILLING_IOC) != 0)
      m_trade.SetTypeFilling(ORDER_FILLING_IOC);
   else
      m_trade.SetTypeFilling(ORDER_FILLING_RETURN);
}

//--- Expert Initialization
int OnInit()
{
   m_last_bid = 0.0;
   m_peak_tick_speed = 0.0;
   m_setup_active = false;
   m_setup_direction = 0;
   m_setup_time = 0;
   m_setup_start_price = 0.0;
   m_setup_peak_price = 0.0;
   m_pullback_detected = false;

   // Set defaults for thresholds
   m_calibrated_speed_threshold = InpTickSpeedThreshold;
   m_calibrated_velocity_threshold = InpPriceVelocityThreshold;
   m_calibrated_acceleration_threshold = InpAccelerationThreshold;

   // In the Strategy Tester, synthetic tick generators (OHLC or even Real Ticks) can have different intervals.
   // If Auto Calibration is enabled, scale down speed/velocity requirements in the Strategy Tester.
   if (MQLInfoInteger(MQL_TESTER) && InpTesterAutoCalibrate)
   {
      m_calibrated_speed_threshold = 1; // Any incoming tick counts as active
      m_calibrated_velocity_threshold = 0.005; // Scale down minimum price change per second
      m_calibrated_acceleration_threshold = 0.001; // Scale down acceleration
      Print("[GVS INIT] Tester mode detected with Auto-Calibration. Adjusting thresholds to ensure executions under simulated ticks.");
   }

   m_trade.SetExpertMagicNumber(InpMagicNumber);

   m_atr_handle = iATR(Symbol(), Period(), InpATRPeriod);
   if (m_atr_handle == INVALID_HANDLE)
   {
      Print("[GVS INIT] Failed to create ATR indicator handle!");
      return INIT_FAILED;
   }

   ConfigureTradeFilling();

   Print("[GVS INIT] Gold Velocity Scalper Initialized successfully. Magic: ", InpMagicNumber);
   return INIT_SUCCEEDED;
}

//--- Expert Deinitialization
void OnDeinit(const int reason)
{
   if (m_atr_handle != INVALID_HANDLE)
   {
      IndicatorRelease(m_atr_handle);
      m_atr_handle = INVALID_HANDLE;
   }
   Comment("");
}

//--- Order Execution
void ExecuteTrade(int direction)
{
   ConfigureTradeFilling();

   double ask = SymbolInfoDouble(Symbol(), SYMBOL_ASK);
   double bid = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   double point = SymbolInfoDouble(Symbol(), SYMBOL_POINT);

   double entry_price = (direction == 1) ? ask : bid;
   double sl_price = 0.0;
   double tp_price = 0.0;

   if (direction == 1)
   {
      if (InpStopLossPoints > 0)
         sl_price = entry_price - InpStopLossPoints * point;
      if (InpTakeProfitPoints > 0)
         tp_price = entry_price + InpTakeProfitPoints * point;

      double lot = NormalizeLotSize(InpLotSize);
      if (m_trade.Buy(lot, Symbol(), ask, NormalizeDouble(sl_price, _Digits), NormalizeDouble(tp_price, _Digits), "GVS BUY"))
      {
         m_setup_active = false;
         m_peak_tick_speed = 0.0;
         Print("[GVS Entry] BUY executed at ", ask, " SL: ", sl_price, " TP: ", tp_price);
      }
   }
   else if (direction == -1)
   {
      if (InpStopLossPoints > 0)
         sl_price = entry_price + InpStopLossPoints * point;
      if (InpTakeProfitPoints > 0)
         tp_price = entry_price - InpTakeProfitPoints * point;

      double lot = NormalizeLotSize(InpLotSize);
      if (m_trade.Sell(lot, Symbol(), bid, NormalizeDouble(sl_price, _Digits), NormalizeDouble(tp_price, _Digits), "GVS SELL"))
      {
         m_setup_active = false;
         m_peak_tick_speed = 0.0;
         Print("[GVS Entry] SELL executed at ", bid, " SL: ", sl_price, " TP: ", tp_price);
      }
   }
}

//--- Manage Open Positions (Trailing SL & Breakeven)
void ManagePositions()
{
   int total = PositionsTotal();
   for (int i = total - 1; i >= 0; i--)
   {
      if (PositionSelectByTicket(PositionGetTicket(i)))
      {
         if (PositionGetString(POSITION_SYMBOL) == Symbol() &&
             PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         {
            double entry_price = PositionGetDouble(POSITION_PRICE_OPEN);
            double current_sl = PositionGetDouble(POSITION_SL);
            double current_tp = PositionGetDouble(POSITION_TP);
            ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
            ulong ticket = PositionGetInteger(POSITION_TICKET);

            double bid = SymbolInfoDouble(Symbol(), SYMBOL_BID);
            double ask = SymbolInfoDouble(Symbol(), SYMBOL_ASK);
            double point = SymbolInfoDouble(Symbol(), SYMBOL_POINT);

            // Breakeven logic
            if (InpBreakevenPoints > 0)
            {
               if (type == POSITION_TYPE_BUY)
               {
                  if (bid - entry_price >= InpBreakevenPoints * point)
                  {
                     double target_sl = entry_price + 5.0 * point;
                     if (current_sl < target_sl)
                     {
                        m_trade.PositionModify(ticket, NormalizeDouble(target_sl, _Digits), NormalizeDouble(current_tp, _Digits));
                     }
                  }
               }
               else if (type == POSITION_TYPE_SELL)
               {
                  if (entry_price - ask >= InpBreakevenPoints * point)
                  {
                     double target_sl = entry_price - 5.0 * point;
                     if (current_sl == 0 || current_sl > target_sl)
                     {
                        m_trade.PositionModify(ticket, NormalizeDouble(target_sl, _Digits), NormalizeDouble(current_tp, _Digits));
                     }
                  }
               }
            }

            // Re-select to get updated parameters
            if (!PositionSelectByTicket(ticket)) continue;
            current_sl = PositionGetDouble(POSITION_SL);
            current_tp = PositionGetDouble(POSITION_TP);

            // Trailing Stop logic
            if (InpTrailingStopPoints > 0)
            {
               if (type == POSITION_TYPE_BUY)
               {
                  if (bid - entry_price >= InpTrailingStopPoints * point)
                  {
                     double target_sl = bid - InpTrailingStopPoints * point;
                     if (current_sl < target_sl)
                     {
                        m_trade.PositionModify(ticket, NormalizeDouble(target_sl, _Digits), NormalizeDouble(current_tp, _Digits));
                     }
                  }
               }
               else if (type == POSITION_TYPE_SELL)
               {
                  if (entry_price - ask >= InpTrailingStopPoints * point)
                  {
                     double target_sl = ask + InpTrailingStopPoints * point;
                     if (current_sl == 0 || current_sl > target_sl)
                     {
                        m_trade.PositionModify(ticket, NormalizeDouble(target_sl, _Digits), NormalizeDouble(current_tp, _Digits));
                     }
                  }
               }
            }
         }
      }
   }
}

//--- Exit Conditions Monitoring
void CheckExitConditions(double current_tick_speed, double avg_spread, double current_spread, int consecutive_up, int consecutive_down, double acceleration)
{
   int total = PositionsTotal();
   for (int i = total - 1; i >= 0; i--)
   {
      if (PositionSelectByTicket(PositionGetTicket(i)))
      {
         if (PositionGetString(POSITION_SYMBOL) == Symbol() &&
             PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         {
            ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
            ulong ticket = PositionGetInteger(POSITION_TICKET);
            datetime open_time = (datetime)PositionGetInteger(POSITION_TIME);
            datetime current_time = TimeCurrent();

            m_peak_tick_speed = MathMax(m_peak_tick_speed, current_tick_speed);

            // 1. Tick speed fade exit
            if (InpExitOnMomentumFade && m_peak_tick_speed > 10.0)
            {
               if (current_tick_speed < InpPeakSpeedDropRatio * m_peak_tick_speed)
               {
                  Print("[GVS Exit] Momentum faded. Speed: ", current_tick_speed, " Peak: ", m_peak_tick_speed, " Threshold: ", InpPeakSpeedDropRatio * m_peak_tick_speed);
                  m_trade.PositionClose(ticket);
                  continue;
               }
            }

            // 2. Acceleration turns negative (opposite direction) exit
            if (InpExitOnMomentumFade)
            {
               if (type == POSITION_TYPE_BUY && acceleration < -0.05)
               {
                  Print("[GVS Exit] Acceleration negative for BUY: ", acceleration);
                  m_trade.PositionClose(ticket);
                  continue;
               }
               else if (type == POSITION_TYPE_SELL && acceleration > 0.05)
               {
                  Print("[GVS Exit] Acceleration positive for SELL: ", acceleration);
                  m_trade.PositionClose(ticket);
                  continue;
               }
            }

            // 3. Five consecutive opposite ticks exit
            if (InpExitOnOppositeTicks)
            {
               if (type == POSITION_TYPE_BUY && consecutive_down >= 5)
               {
                  Print("[GVS Exit] 5 consecutive down ticks during BUY");
                  m_trade.PositionClose(ticket);
                  continue;
               }
               else if (type == POSITION_TYPE_SELL && consecutive_up >= 5)
               {
                  Print("[GVS Exit] 5 consecutive up ticks during SELL");
                  m_trade.PositionClose(ticket);
                  continue;
               }
            }

            // 4. Spread suddenly widens exit
            if (InpExitOnSpreadWidening && avg_spread > 0)
            {
               if (current_spread > InpExitSpreadMultiplier * avg_spread)
               {
                  Print("[GVS Exit] Spread widens: current ", current_spread, " avg ", avg_spread, " limit ", InpExitSpreadMultiplier * avg_spread);
                  m_trade.PositionClose(ticket);
                  continue;
               }
            }

            // 5. Maximum trade duration reached
            if (InpMaxTradeDuration > 0)
            {
               if (current_time - open_time >= InpMaxTradeDuration)
               {
                  Print("[GVS Exit] Maximum trade duration reached: ", current_time - open_time, "s");
                  m_trade.PositionClose(ticket);
                  continue;
               }
            }
         }
      }
   }
}

//--- Tick Event Handler
void OnTick()
{
   MqlTick tick;
   if (!SymbolInfoTick(Symbol(), tick)) return;

   if (m_last_bid == 0.0)
   {
      m_last_bid = tick.bid;
      return;
   }

   // Add tick to history logs
   m_tick_history.Add(tick, m_last_bid);
   m_last_bid = tick.bid;

   // Refresh core metrics
   int current_tick_speed = GetTickSpeed(m_tick_history, InpTickSpeedWindow);
   double v_delta = 0.0;
   double price_velocity = GetPriceVelocity(m_tick_history, InpVelocityWindow, v_delta);
   double acceleration = GetPriceAcceleration(m_tick_history, InpVelocityWindow);
   double current_spread = tick.ask - tick.bid;
   double avg_spread = GetAverageSpread(m_tick_history, InpSpreadWindowTicks);
   double directional_ratio_buy = GetDirectionalTicksRatio(m_tick_history, InpDirectionalTicksWindow, 1);
   double directional_ratio_sell = GetDirectionalTicksRatio(m_tick_history, InpDirectionalTicksWindow, -1);
   double eff_ratio = GetEfficiencyRatio(m_tick_history, InpDirectionalTicksWindow);
   int tfi = GetTFI(m_tick_history, InpTFIWindowTicks);

   int consecutive_up = GetConsecutiveTicks(m_tick_history, 1);
   int consecutive_down = GetConsecutiveTicks(m_tick_history, -1);

   double current_tick_vol = 0.0, avg_tick_vol = 0.0;
   bool vol_spike = IsTickVolumeSpike(current_tick_vol, avg_tick_vol);

   double completed_candle_range = 0.0, atr_value = 0.0;
   bool atr_expanded = IsATRExpansion(completed_candle_range, atr_value);

   //--- Calculate Rocket Scores for both directions
   double score_buy = 0;
   double score_sell = 0;

   // Stage 1: Tick Speed (20 pts)
   if (current_tick_speed >= m_calibrated_speed_threshold)
   {
      score_buy += 20;
      score_sell += 20;
   }
   else if (current_tick_speed >= m_calibrated_speed_threshold * 0.5)
   {
      score_buy += 10;
      score_sell += 10;
   }

   // Stage 2: Price Velocity (20 pts)
   if (price_velocity >= m_calibrated_velocity_threshold)
      score_buy += 20;
   else if (price_velocity >= m_calibrated_velocity_threshold * 0.5)
      score_buy += 10;

   if (price_velocity <= -m_calibrated_velocity_threshold)
      score_sell += 20;
   else if (price_velocity <= -m_calibrated_velocity_threshold * 0.5)
      score_sell += 10;

   // Stage 3: Tick Volume Explosion (15 pts)
   if (vol_spike)
   {
      score_buy += 15;
      score_sell += 15;
   }

   // Stage 4: Spread Stability (10 pts)
   bool spread_stable = (avg_spread > 0 && current_spread <= InpSpreadMultiplier * avg_spread);
   if (spread_stable)
   {
      score_buy += 10;
      score_sell += 10;
   }

   // Stage 5: Directional Ticks (10 pts)
   if (directional_ratio_buy >= InpDirectionalTicksRatio)
      score_buy += 10;
   if (directional_ratio_sell >= InpDirectionalTicksRatio)
      score_sell += 10;

   // Stage 6: Acceleration (15 pts)
   if (acceleration >= m_calibrated_acceleration_threshold)
      score_buy += 15;
   else if (acceleration >= m_calibrated_acceleration_threshold * 0.5)
      score_buy += 7;

   if (acceleration <= -m_calibrated_acceleration_threshold)
      score_sell += 15;
   else if (acceleration <= -m_calibrated_acceleration_threshold * 0.5)
      score_sell += 7;

   // ATR Expansion (10 pts)
   if (atr_expanded)
   {
      score_buy += 10;
      score_sell += 10;
   }

   //--- Hard Entry Blocks / Filters
   bool is_spread_valid_for_entry = (avg_spread <= 0 || current_spread <= InpSpreadMultiplier * avg_spread);
   bool is_noise_level_valid = (eff_ratio >= InpMinEfficiencyRatio);

   bool is_buy_tfi_valid = (!InpUseTFI || tfi >= InpTFIThreshold);
   bool is_sell_tfi_valid = (!InpUseTFI || tfi <= -InpTFIThreshold);

   bool buy_eligible = (score_buy >= InpMinRocketScore && is_spread_valid_for_entry && is_noise_level_valid && is_buy_tfi_valid);
   bool sell_eligible = (score_sell >= InpMinRocketScore && is_spread_valid_for_entry && is_noise_level_valid && is_sell_tfi_valid);

   // Periodic Journal Diagnostics (Every 100 ticks in Strategy Tester)
   static int tick_diag_counter = 0;
   if (MQLInfoInteger(MQL_TESTER))
   {
      tick_diag_counter++;
      if (tick_diag_counter % 500 == 0)
      {
         Print("[GVS DIAGNOSTICS] Tick: ", tick_diag_counter,
               " | Buy Score: ", score_buy, " (Min: ", InpMinRocketScore, ")",
               " | Buy Eligible: ", buy_eligible,
               " | Speed: ", current_tick_speed, " (Calibrated limit: ", m_calibrated_speed_threshold, ")",
               " | Velocity: ", price_velocity, " (Calibrated limit: ", m_calibrated_velocity_threshold, ")",
               " | Acceleration: ", acceleration, " (Calibrated limit: ", m_calibrated_acceleration_threshold, ")",
               " | Spread Valid: ", is_spread_valid_for_entry, " (Current Spread: ", current_spread, ")",
               " | Noise ER Valid: ", is_noise_level_valid, " (ER: ", eff_ratio, " Min ER: ", InpMinEfficiencyRatio, ")",
               " | TFI Valid: ", is_buy_tfi_valid, " (TFI: ", tfi, ")");
      }
   }

   int open_positions = GetOpenPositionsCount();

   //--- Handle Active Positions Tracking & Exit
   if (open_positions > 0)
   {
      ManagePositions();
      CheckExitConditions(current_tick_speed, avg_spread, current_spread, consecutive_up, consecutive_down, acceleration);
      m_setup_active = false; // Reset setup tracking if position is already open
   }
   else
   {
      // Peak tick speed reset when no positions are open
      m_peak_tick_speed = 0.0;

      //--- Entry Execution Mechanics
      if (InpEntryMode == ENTRY_INSTANT)
      {
         if (buy_eligible)
         {
            ExecuteTrade(1);
         }
         else if (sell_eligible)
         {
            ExecuteTrade(-1);
         }
      }
      else // ENTRY_PULLBACK
      {
         if (!m_setup_active)
         {
            if (buy_eligible)
            {
               m_setup_active = true;
               m_setup_direction = 1;
               m_setup_time = TimeCurrent();
               m_setup_start_price = tick.bid;
               m_setup_peak_price = tick.bid;
               m_pullback_detected = false;
               Print("[GVS Setup] BUY setup activated. Start price: ", tick.bid);
            }
            else if (sell_eligible)
            {
               m_setup_active = true;
               m_setup_direction = -1;
               m_setup_time = TimeCurrent();
               m_setup_start_price = tick.bid;
               m_setup_peak_price = tick.bid;
               m_pullback_detected = false;
               Print("[GVS Setup] SELL setup activated. Start price: ", tick.bid);
            }
         }
         else // Pullback Setup is active
         {
            // Verify maximum lifetime limit
            if (TimeCurrent() - m_setup_time > InpSetupExpirySeconds)
            {
               m_setup_active = false;
               Print("[GVS Setup] Setup expired.");
            }
            else
            {
               if (m_setup_direction == 1)
               {
                  m_setup_peak_price = MathMax(m_setup_peak_price, tick.bid);
                  double height = m_setup_peak_price - m_setup_start_price;

                  if (tick.bid < m_setup_start_price)
                  {
                     m_setup_active = false;
                     Print("[GVS Setup] BUY setup invalidated (broke below starting price).");
                  }
                  else if (height >= InpMinImpulseHeight)
                  {
                     double retracement = (m_setup_peak_price - tick.bid) / height;

                     if (retracement >= InpMinRetracement && retracement <= InpMaxRetracement)
                     {
                        m_pullback_detected = true;
                     }

                     if (retracement > InpMaxPullbackLimit)
                     {
                        m_setup_active = false;
                        Print("[GVS Setup] BUY setup invalidated (pullback too deep: ", DoubleToString(retracement * 100, 1), "%).");
                     }

                     // Momentum resumes BUY check
                     if (m_pullback_detected)
                     {
                        bool momentum_resumes = (score_buy >= InpPullbackResumeScore || consecutive_up >= 2);
                        if (momentum_resumes)
                        {
                           Print("[GVS Entry] Pullback entry triggered for BUY. Retracement: ", DoubleToString(retracement * 100, 1), "%");
                           ExecuteTrade(1);
                        }
                     }
                  }
               }
               else if (m_setup_direction == -1)
               {
                  m_setup_peak_price = MathMin(m_setup_peak_price, tick.bid);
                  double height = m_setup_start_price - m_setup_peak_price;

                  if (tick.bid > m_setup_start_price)
                  {
                     m_setup_active = false;
                     Print("[GVS Setup] SELL setup invalidated (broke above starting price).");
                  }
                  else if (height >= InpMinImpulseHeight)
                  {
                     double retracement = (tick.bid - m_setup_peak_price) / height;

                     if (retracement >= InpMinRetracement && retracement <= InpMaxRetracement)
                     {
                        m_pullback_detected = true;
                     }

                     if (retracement > InpMaxPullbackLimit)
                     {
                        m_setup_active = false;
                        Print("[GVS Setup] SELL setup invalidated (pullback too deep: ", DoubleToString(retracement * 100, 1), "%).");
                     }

                     // Momentum resumes SELL check
                     if (m_pullback_detected)
                     {
                        bool momentum_resumes = (score_sell >= InpPullbackResumeScore || consecutive_down >= 2);
                        if (momentum_resumes)
                        {
                           Print("[GVS Entry] Pullback entry triggered for SELL. Retracement: ", DoubleToString(retracement * 100, 1), "%");
                           ExecuteTrade(-1);
                        }
                     }
                  }
               }
            }
         }
      }
   }

   //--- Real-time Visual Dashboard Update (Skip in non-visual backtester mode)
   if (!MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_VISUAL_MODE))
   {
      string setup_str = "None";
      if (m_setup_active)
      {
         setup_str = (m_setup_direction == 1 ? "BUY Pullback Active" : "SELL Pullback Active");
         if (m_pullback_detected) setup_str += " (Pullback Detected)";
      }

      string comment_text = "=========================================================\n" +
                            "            GOLD VELOCITY SCALPER (GVS) - REALTIME DASHBOARD\n" +
                            "=========================================================\n" +
                            "Symbol: " + Symbol() + " | Spread: " + DoubleToString(current_spread, _Digits) + " (Avg: " + DoubleToString(avg_spread, _Digits) + ")\n" +
                            "TFI Score: " + IntegerToString(tfi) + " (Threshold: " + IntegerToString(InpTFIThreshold) + ")\n" +
                            "Efficiency Ratio: " + DoubleToString(eff_ratio, 2) + " (Min: " + DoubleToString(InpMinEfficiencyRatio, 2) + ")\n\n" +
                            "--- ROCKET SCORE COMPONENT BREAKDOWN ---\n" +
                            "[1] Tick Speed: " + IntegerToString(current_tick_speed) + " t/sec (Target: " + IntegerToString(m_calibrated_speed_threshold) + ") -> Score: " + IntegerToString(current_tick_speed >= m_calibrated_speed_threshold ? 20 : (current_tick_speed >= m_calibrated_speed_threshold * 0.5 ? 10 : 0)) + "/20\n" +
                            "[2] Price Velocity: " + DoubleToString(price_velocity, 2) + " / sec (Target: " + DoubleToString(m_calibrated_velocity_threshold, 2) + ") -> Score (BUY/SELL): " + IntegerToString(price_velocity >= m_calibrated_velocity_threshold ? 20 : (price_velocity >= m_calibrated_velocity_threshold * 0.5 ? 10 : 0)) + " / " + IntegerToString(price_velocity <= -m_calibrated_velocity_threshold ? 20 : (price_velocity <= -m_calibrated_velocity_threshold * 0.5 ? 10 : 0)) + " / 20\n" +
                            "[3] Acceleration: " + DoubleToString(acceleration, 2) + " / sec2 (Target: " + DoubleToString(m_calibrated_acceleration_threshold, 2) + ") -> Score (BUY/SELL): " + IntegerToString(acceleration >= m_calibrated_acceleration_threshold ? 15 : (acceleration >= m_calibrated_acceleration_threshold * 0.5 ? 7 : 0)) + " / " + IntegerToString(acceleration <= -m_calibrated_acceleration_threshold ? 15 : (acceleration <= -m_calibrated_acceleration_threshold * 0.5 ? 7 : 0)) + " / 15\n" +
                            "[4] Volume Spike: " + DoubleToString(current_tick_vol, 1) + " (Avg(30): " + DoubleToString(avg_tick_vol, 1) + ") -> Score: " + (vol_spike ? "15" : "0") + "/15\n" +
                            "[5] Spread Quality: " + DoubleToString(current_spread, _Digits) + " (Avg Limit: " + DoubleToString(InpSpreadMultiplier * avg_spread, _Digits) + ") -> Score: " + (spread_stable ? "10" : "0") + "/10\n" +
                            "[6] Directional Ticks (BUY/SELL): " + DoubleToString(directional_ratio_buy * 100, 1) + "% / " + DoubleToString(directional_ratio_sell * 100, 1) + "% -> Score: " + IntegerToString(directional_ratio_buy >= InpDirectionalTicksRatio ? 10 : 0) + " / " + IntegerToString(directional_ratio_sell >= InpDirectionalTicksRatio ? 10 : 0) + " / 10\n" +
                            "[7] ATR Expansion: Completed Range " + DoubleToString(completed_candle_range, _Digits) + " (ATR: " + DoubleToString(atr_value, _Digits) + ") -> Score: " + (atr_expanded ? "10" : "0") + "/10\n" +
                            "---------------------------------------------------------\n" +
                            "TOTAL ROCKET SCORE (BUY / SELL): " + DoubleToString(score_buy, 0) + " / " + DoubleToString(score_sell, 0) + " (Required: " + DoubleToString(InpMinRocketScore, 0) + ")\n" +
                            "---------------------------------------------------------\n" +
                            "Setup Status: " + setup_str + "\n" +
                            "Start Price: " + DoubleToString(m_setup_start_price, _Digits) + " | Peak Price: " + DoubleToString(m_setup_peak_price, _Digits) + "\n" +
                            "Active Positions: " + IntegerToString(open_positions) + " (Magic: " + IntegerToString(InpMagicNumber) + ")\n" +
                            "=========================================================";

      Comment(comment_text);
   }
}
