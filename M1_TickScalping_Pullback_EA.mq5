//+------------------------------------------------------------------+
//|                                   M1_TickScalping_Pullback_EA.mq5|
//|                    Copyright 2025, Advanced Quantitative Trading |
//|                                      https://www.mql5.com        |
//+------------------------------------------------------------------+
#property copyright "Copyright 2025, Advanced Quantitative Trading"
#property link      "https://www.mql5.com"
#property version   "1.00"
#property description "Advanced MetaTrader 5 (MQL5) M1 Tick-Scalping EA with Pullback Entry Model."
#property description "Utilizes real-time tick microstructure, M1 context, adaptive impulse/pullback scoring, tick re-acceleration, and momentum decay exits."

#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\AccountInfo.mqh>

//+------------------------------------------------------------------+
//| ENUMERATIONS                                                     |
//+------------------------------------------------------------------+
enum ENUM_ENTRY_MODE
  {
   ENTRY_IMMEDIATE_REACCELERATION = 0, // Immediate Re-acceleration
   ENTRY_MICRO_PULLBACK_BREAK     = 1, // Break of Micro Pullback High/Low
   ENTRY_BREAK_VELOCITY_CONFIRM   = 2, // Break + Tick Re-acceleration Confirmation (Default)
   ENTRY_BREAK_ACCEL_CONFIRM      = 3  // Break + Acceleration Confirmation
  };

enum ENUM_SL_MODE
  {
   SL_MODE_FIXED_POINTS     = 0, // Fixed Points
   SL_MODE_ATR_BASED        = 1, // ATR-based
   SL_MODE_PULLBACK_STRUCT  = 2, // Pullback Structure-based
   SL_MODE_IMPULSE_BASED    = 3, // Impulse Extreme-based
   SL_MODE_HYBRID_ADAPTIVE  = 4  // Hybrid Adaptive (Structure + Volatility Buffer)
  };

enum ENUM_TP_MODE
  {
   TP_MODE_FIXED_RR         = 0, // Fixed Risk-to-Reward Ratio
   TP_MODE_ATR_TARGET       = 1, // ATR Multiple Target
   TP_MODE_DYNAMIC_TARGET   = 2, // Dynamic Impulse Target
   TP_MODE_MOMENTUM_DECAY   = 3  // Momentum Decay Exit Only
  };

enum ENUM_MARKET_REGIME
  {
   REGIME_LOW_VOLATILITY    = 0, // Low Volatility (Avoid)
   REGIME_NORMAL_VOLATILITY = 1, // Normal Volatility (Selective)
   REGIME_EXPANSION         = 2, // Range Expansion (Preferred)
   REGIME_EXTREME_VOL       = 3, // Extreme Volatility (Stricter Filter)
   REGIME_ABNORMAL_SPREAD   = 4  // Abnormal Spread (Disable Trading)
  };

enum ENUM_SETUP_STATE
  {
   STATE_IDLE               = 0, // Monitoring for Impulse
   STATE_WAIT_FOR_PULLBACK  = 1, // Impulse Detected -> Measuring Pullback
   STATE_PULLBACK_VALIDATED = 2, // Valid Pullback -> Waiting for Re-acceleration
   STATE_REACCELERATING     = 3  // Re-accelerating -> Ready for Order Execution
  };

enum ENUM_SIGNAL_DIR
  {
   SIGNAL_NONE = 0,
   SIGNAL_BUY  = 1,
   SIGNAL_SELL = -1
  };

enum ENUM_RISK_MODE
  {
   RISK_FIXED_LOT      = 0, // Fixed Lot Size
   RISK_PCT_BALANCE    = 1, // Percentage of Account Balance
   RISK_PCT_EQUITY     = 2, // Percentage of Account Equity
   RISK_BASED_ON_SL    = 3  // Monetary Risk Calculated from Stop Loss
  };

//+------------------------------------------------------------------+
//| STRUCTS                                                          |
//+------------------------------------------------------------------+
struct TickRecord
  {
   double            bid;
   double            ask;
   double            mid;
   double            spread_pts;
   long              time_msc;
   double            price_change;
   int               direction; // +1 = Up, -1 = Down, 0 = Flat
   double            displacement;
   double            ticks_per_sec;
  };

struct SetupData
  {
   ENUM_SETUP_STATE  state;
   ENUM_SIGNAL_DIR   direction;
   long              impulse_start_time;
   long              impulse_peak_time;
   double            impulse_start_price;
   double            impulse_peak_price;
   double            impulse_displacement;
   double            impulse_score;

   double            pullback_extreme_price;     // Pullback Low (BUY) / Pullback High (SELL)
   double            pullback_micro_high_price;  // Local High during pullback (BUY micro resistance)
   double            pullback_micro_low_price;   // Local Low during pullback (SELL micro support)
   double            pullback_depth_pct;
   double            pullback_score;
   long              pullback_start_time;
   int               pullback_ticks_count;

   double            reaccel_score;
   double            trade_score;

   datetime          m1_candle_time;
  };

//+------------------------------------------------------------------+
//| INPUT PARAMETERS                                                 |
//+------------------------------------------------------------------+
input group "=== 1. Strategy Context & Timeframe ==="
input ENUM_TIMEFRAMES InpContextTimeframe     = PERIOD_M1;  // Primary Context Timeframe
input int             InpEMAFastPeriod         = 9;          // Fast EMA Period
input int             InpEMASlowPeriod         = 21;         // Slow EMA Period
input int             InpATRPeriod             = 14;         // ATR Period
input int             InpSwingLookback         = 10;         // Swing High/Low Lookback Bars
input double          InpRangeExpansionFactor  = 1.1;        // Min M1 Range Expansion vs Avg

input group "=== 2. Tick Engine & Rolling Windows ==="
input int             InpTickHistorySize       = 100;        // Max Rolling Ticks History
input int             InpWindowShort           = 10;         // Short Tick Window
input int             InpWindowMed             = 20;         // Medium Tick Window
input int             InpWindowLong            = 50;         // Long Tick Window
input int             InpTimeWindowMS          = 3000;       // Time Window Duration (ms)

input group "=== 3. Impulse Detection Engine ==="
input double          InpWeightVelocity        = 0.25;       // Impulse Weight: Tick Velocity
input double          InpWeightDisplacement    = 0.25;       // Impulse Weight: Price Displacement
input double          InpWeightImbalance       = 0.20;       // Impulse Weight: Directional Imbalance
input double          InpWeightAcceleration    = 0.15;       // Impulse Weight: Acceleration
input double          InpWeightRangeExpansion  = 0.15;       // Impulse Weight: Range Expansion
input double          InpImpulseScoreThreshold = 50.0;       // Min Impulse Score Threshold (0-100)
input double          InpMinImpulseDisplacePts = 30.0;       // Min Impulse Displacement (Points)

input group "=== 4. Pullback Engine ==="
input double          InpMinPullbackDepthPct   = 0.10;       // Min Pullback Depth (10% of Impulse)
input double          InpMaxPullbackDepthPct   = 0.75;       // Max Pullback Depth (75% of Impulse)
input double          InpMaxCounterVelRatio    = 0.80;       // Max Counter-Direction Velocity Ratio
input int             InpMaxPullbackDurationSec= 60;         // Max Pullback Duration (Seconds)
input double          InpPullbackScoreThreshold= 50.0;       // Min Pullback Quality Score (0-100)

input group "=== 5. Re-acceleration & Entry Trigger ==="
input ENUM_ENTRY_MODE InpEntryMode             = ENTRY_BREAK_VELOCITY_CONFIRM; // Entry Execution Mode
input double          InpReaccelScoreThreshold = 50.0;       // Min Re-acceleration Score (0-100)
input int             InpMinDirectionalTicks   = 2;          // Min Consecutive Directional Ticks
input double          InpMinTradeScore         = 55.0;       // Min Unified TradeScore (0-100)

input group "=== 6. Spread & Execution Protection ==="
input double          InpMaxAllowedSpreadPts   = 50.0;       // Max Allowed Spread (Points)
input double          InpMaxSpreadExpansionRatio= 2.5;       // Max Spread Expansion Ratio vs Avg
input int             InpMaxSlippage           = 20;         // Max Allowed Slippage (Points)
input bool            InpUseSessionFilter      = false;      // Enable Trading Session Filter
input int             InpSessionStartHour      = 1;          // Session Start Hour (Broker Time)
input int             InpSessionEndHour        = 23;         // Session End Hour (Broker Time)

input group "=== 7. Stop Loss & Risk Management ==="
input ENUM_SL_MODE    InpSLMode                = SL_MODE_HYBRID_ADAPTIVE; // Stop Loss Mode
input double          InpSLFixedPoints         = 150.0;      // Fixed SL Distance (Points)
input double          InpSLATRMultiplier       = 1.5;        // ATR SL Multiplier
input double          InpSLVolatilityBufferPts = 20.0;       // Volatility Buffer Points
input ENUM_RISK_MODE  InpRiskMode              = RISK_FIXED_LOT; // Lot Sizing Risk Mode
input double          InpFixedLotSize          = 0.01;       // Fixed Trade Volume
input double          InpRiskPercent           = 1.0;        // Risk Percentage (% of Balance/Equity)
input double          InpMaxMarginUtilPct      = 70.0;       // Max Margin Utilization (%)
input double          InpMaxDailyLossPct       = 5.0;        // Max Daily Loss Percentage (%)
input double          InpDailyProfitTargetPct  = 10.0;       // Daily Profit Target Percentage (%)

input group "=== 8. Take Profit & Momentum Exit ==="
input ENUM_TP_MODE    InpTPMode                = TP_MODE_FIXED_RR; // Take Profit Mode
input double          InpRiskRewardRatio       = 2.0;        // Risk-to-Reward Ratio (e.g. 2.0 = 1:2)
input double          InpTPATRMultiplier       = 3.0;        // ATR TP Multiplier
input bool            InpUseTrailingSL         = true;       // Enable Trailing Stop Loss
input double          InpTrailingStartRR       = 1.0;        // Trailing Start Trigger (RR Ratio)
input double          InpTrailingStepPts       = 30.0;       // Trailing Step Distance (Points)
input bool            InpEnableMomentumDecayExit= true;     // Enable Early Exit on Momentum Collapse

input group "=== 9. Cooldown & Position Limits ==="
input int             InpMinTimeBetweenTradesSec= 15;        // Min Time Between Trades (Seconds)
input int             InpMaxTradesPerM1Candle  = 3;          // Max Executed Trades per M1 Candle
input int             InpCooldownAfterSLSec    = 60;         // Cooldown Period After SL (Seconds)
input bool            InpOnePositionPerDirection= true;      // Limit to 1 Position per Direction
input int             InpMaxSimultaneousPos    = 1;          // Max Total Simultaneous Positions

input group "=== 10. Debug & Visuals ==="
input bool            InpEnableDebugVisuals    = true;       // Enable Visual Objects on Chart
input bool            InpEnableJournalLogs     = true;       // Enable Detailed Journal Debug Logging

//+------------------------------------------------------------------+
//| GLOBAL VARIABLES & CLASSES                                       |
//+------------------------------------------------------------------+
CTrade         m_trade;
CSymbolInfo    m_symbol;
CPositionInfo  m_position;
CAccountInfo   m_account;

int            m_atr_handle         = INVALID_HANDLE;
int            m_ema_fast_handle    = INVALID_HANDLE;
int            m_ema_slow_handle    = INVALID_HANDLE;

TickRecord     m_tick_ring[];
int            m_tick_count          = 0;
int            m_tick_head           = 0;

SetupData      m_setup;
ENUM_MARKET_REGIME m_current_regime  = REGIME_NORMAL_VOLATILITY;

datetime       m_last_trade_time     = 0;
datetime       m_last_sl_time        = 0;
datetime       m_last_spread_spike_time= 0;
int            m_trades_current_candle= 0;
datetime       m_current_m1_candle   = 0;

double         m_daily_start_equity  = 0.0;
datetime       m_daily_reset_time    = 0;
double         m_avg_spread_pts      = 0.0;

// Tester Metrics Reporting
int            m_total_trades        = 0;
int            m_winning_trades      = 0;
int            m_losing_trades       = 0;
double         m_total_profit        = 0.0;
double         m_total_loss          = 0.0;

//+------------------------------------------------------------------+
//| TICK ENGINE UTILITIES                                            |
//+------------------------------------------------------------------+
void AddTickRecord(const MqlTick &tick)
  {
   if(ArraySize(m_tick_ring) != InpTickHistorySize)
      ArrayResize(m_tick_ring, InpTickHistorySize);

   int idx = m_tick_head;
   TickRecord rec = {0};

   rec.bid = tick.bid;
   rec.ask = tick.ask;
   rec.mid = (tick.bid + tick.ask) * 0.5;
   rec.spread_pts = (m_symbol.Point() > 0) ? (tick.ask - tick.bid) / m_symbol.Point() : 0.0;
   rec.time_msc = tick.time_msc;

   if(m_tick_count > 0)
     {
      int prev_idx = (m_tick_head - 1 + InpTickHistorySize) % InpTickHistorySize;
      double diff = rec.mid - m_tick_ring[prev_idx].mid;
      rec.price_change = diff;
      rec.direction = (diff > 0) ? 1 : ((diff < 0) ? -1 : 0);
      rec.displacement = MathAbs(diff);

      long dt_msc = rec.time_msc - m_tick_ring[prev_idx].time_msc;
      rec.ticks_per_sec = (dt_msc > 0) ? (1000.0 / (double)dt_msc) : 10.0;
     }
   else
     {
      rec.price_change = 0.0;
      rec.direction = 0;
      rec.displacement = 0.0;
      rec.ticks_per_sec = 0.0;
     }

   m_tick_ring[idx] = rec;
   m_tick_head = (m_tick_head + 1) % InpTickHistorySize;
   if(m_tick_count < InpTickHistorySize)
      m_tick_count++;

   // Update rolling average spread
   if(m_avg_spread_pts == 0.0)
      m_avg_spread_pts = rec.spread_pts;
   else
      m_avg_spread_pts = m_avg_spread_pts * 0.95 + rec.spread_pts * 0.05;
  }

TickRecord GetTickRelative(int steps_back)
  {
   TickRecord dummy = {0};
   if(steps_back < 0 || steps_back >= m_tick_count)
      return dummy;
   int idx = (m_tick_head - 1 - steps_back + InpTickHistorySize * 10) % InpTickHistorySize;
   return m_tick_ring[idx];
  }

void ComputeRollingTickMetrics(int window, double &vel_pts_sec, double &displace_pts, double &dir_imbalance, double &accel, int &consec_dir)
  {
   vel_pts_sec = 0.0;
   displace_pts = 0.0;
   dir_imbalance = 0.0;
   accel = 0.0;
   consec_dir = 0;

   int n = MathMin(window, m_tick_count - 1);
   if(n < 2) return;

   TickRecord latest = GetTickRelative(0);
   TickRecord oldest = GetTickRelative(n);

   long elapsed_msc = latest.time_msc - oldest.time_msc;
   double elapsed_sec = (elapsed_msc > 0) ? ((double)elapsed_msc / 1000.0) : 0.1;
   if(elapsed_sec <= 0.001) elapsed_sec = 0.001;

   displace_pts = (m_symbol.Point() > 0) ? MathAbs(latest.mid - oldest.mid) / m_symbol.Point() : 0.0;
   vel_pts_sec = displace_pts / elapsed_sec;

   int buy_ticks = 0, sell_ticks = 0;
   int current_dir = latest.direction;
   consec_dir = 0;

   for(int i = 0; i < n; i++)
     {
      TickRecord r = GetTickRelative(i);
      if(r.direction > 0) buy_ticks++;
      else if(r.direction < 0) sell_ticks++;

      if(i == 0 || (current_dir != 0 && r.direction == current_dir))
        {
         if(i == 0) current_dir = r.direction;
         if(r.direction == current_dir && current_dir != 0)
            consec_dir++;
        }
     }

   int total_dir = buy_ticks + sell_ticks;
   if(total_dir > 0)
      dir_imbalance = ((double)(buy_ticks - sell_ticks) / (double)total_dir); // -1.0 to +1.0
   else
      dir_imbalance = 0.0;

   // Calculate acceleration between recent half and older half
   int half = n / 2;
   if(half >= 1)
     {
      TickRecord mid_rec = GetTickRelative(half);
      double dt1 = (latest.time_msc - mid_rec.time_msc) / 1000.0;
      double dt2 = (mid_rec.time_msc - oldest.time_msc) / 1000.0;
      if(dt1 <= 0.001) dt1 = 0.001;
      if(dt2 <= 0.001) dt2 = 0.001;

      double v1 = ((m_symbol.Point() > 0) ? MathAbs(latest.mid - mid_rec.mid) / m_symbol.Point() : 0.0) / dt1;
      double v2 = ((m_symbol.Point() > 0) ? MathAbs(mid_rec.mid - oldest.mid) / m_symbol.Point() : 0.0) / dt2;
      accel = v1 - v2;
     }
  }

//+------------------------------------------------------------------+
//| M1 CONTEXT & MARKET REGIME                                       |
//+------------------------------------------------------------------+
void UpdateMarketContext(double &atr_pts, double &m1_range_pts, double &body_ratio, double &ema_fast, double &ema_slow, bool &is_expansion)
  {
   atr_pts = 0.0;
   m1_range_pts = 0.0;
   body_ratio = 0.0;
   ema_fast = 0.0;
   ema_slow = 0.0;
   is_expansion = false;

   double atr_buf[1], ema_f_buf[1], ema_s_buf[1];
   if(CopyBuffer(m_atr_handle, 0, 0, 1, atr_buf) > 0)
      atr_pts = (m_symbol.Point() > 0) ? atr_buf[0] / m_symbol.Point() : 0.0;
   if(CopyBuffer(m_ema_fast_handle, 0, 0, 1, ema_f_buf) > 0)
      ema_fast = ema_f_buf[0];
   if(CopyBuffer(m_ema_slow_handle, 0, 0, 1, ema_s_buf) > 0)
      ema_slow = ema_s_buf[0];

   MqlRates rates[];
   if(CopyRates(_Symbol, InpContextTimeframe, 0, InpSwingLookback + 1, rates) >= InpSwingLookback)
     {
      int last_idx = ArraySize(rates) - 1;
      double h = rates[last_idx].high;
      double l = rates[last_idx].low;
      double o = rates[last_idx].open;
      double c = rates[last_idx].close;

      double range = h - l;
      double body = MathAbs(c - o);
      m1_range_pts = (m_symbol.Point() > 0) ? range / m_symbol.Point() : 0.0;
      body_ratio = (range > 0) ? (body / range) : 0.0;

      // Calculate historical average M1 candle range
      double sum_range = 0.0;
      for(int i = 0; i < last_idx; i++)
         sum_range += (rates[i].high - rates[i].low);
      double avg_range = sum_range / (double)last_idx;

      if(avg_range > 0.0 && range >= avg_range * InpRangeExpansionFactor)
         is_expansion = true;
     }

   // Classify Market Regime
   TickRecord cur_tick = GetTickRelative(0);
   if(cur_tick.spread_pts > InpMaxAllowedSpreadPts || cur_tick.spread_pts > m_avg_spread_pts * InpMaxSpreadExpansionRatio)
      m_current_regime = REGIME_ABNORMAL_SPREAD;
   else if(atr_pts < 30.0)
      m_current_regime = REGIME_LOW_VOLATILITY;
   else if(atr_pts > 450.0)
      m_current_regime = REGIME_EXTREME_VOL;
   else if(is_expansion)
      m_current_regime = REGIME_EXPANSION;
   else
      m_current_regime = REGIME_NORMAL_VOLATILITY;
  }

//+------------------------------------------------------------------+
//| IMPULSE DETECTION ENGINE                                         |
//+------------------------------------------------------------------+
double EvaluateImpulse(ENUM_SIGNAL_DIR &dir_out)
  {
   dir_out = SIGNAL_NONE;
   if(m_tick_count < InpWindowMed) return 0.0;

   double vel, displace, imbalance, accel;
   int consec;
   ComputeRollingTickMetrics(InpWindowMed, vel, displace, imbalance, accel, consec);

   if(displace < InpMinImpulseDisplacePts) return 0.0;

   // Score components (0 - 100)
   double s_vel       = MathMin(100.0, (vel / 100.0) * 100.0);
   double s_displace  = MathMin(100.0, (displace / (InpMinImpulseDisplacePts * 2.0)) * 100.0);
   double s_imbalance = MathAbs(imbalance) * 100.0;
   double s_accel     = MathMin(100.0, MathMax(0.0, (accel / 30.0) * 100.0));

   double atr_pts, m1_range, body_ratio, ema_f, ema_s;
   bool is_expansion;
   UpdateMarketContext(atr_pts, m1_range, body_ratio, ema_f, ema_s, is_expansion);
   double s_expansion = is_expansion ? 100.0 : 50.0;

   double w_tot = InpWeightVelocity + InpWeightDisplacement + InpWeightImbalance + InpWeightAcceleration + InpWeightRangeExpansion;
   if(w_tot <= 0.0) w_tot = 1.0;

   double score = (s_vel * InpWeightVelocity +
                   s_displace * InpWeightDisplacement +
                   s_imbalance * InpWeightImbalance +
                   s_accel * InpWeightAcceleration +
                   s_expansion * InpWeightRangeExpansion) / w_tot;

   if(imbalance > 0.15 && consec >= InpMinDirectionalTicks)
      dir_out = SIGNAL_BUY;
   else if(imbalance < -0.15 && consec >= InpMinDirectionalTicks)
      dir_out = SIGNAL_SELL;

   return score;
  }

//+------------------------------------------------------------------+
//| PULLBACK QUALITY ENGINE                                          |
//+------------------------------------------------------------------+
double EvaluatePullbackQuality(double &depth_pct_out)
  {
   depth_pct_out = 0.0;
   if(m_setup.state != STATE_WAIT_FOR_PULLBACK && m_setup.state != STATE_PULLBACK_VALIDATED)
      return 0.0;

   TickRecord cur = GetTickRelative(0);
   double impulse_range = m_setup.impulse_displacement;
   if(impulse_range <= 0.001) return 0.0;

   double pb_dist = 0.0;
   if(m_setup.direction == SIGNAL_BUY)
     {
      // Track peak extension if price pushes higher before pulling back
      if(cur.mid > m_setup.impulse_peak_price)
        {
         m_setup.impulse_peak_price = cur.mid;
         m_setup.impulse_displacement = (m_symbol.Point() > 0) ? MathAbs(cur.mid - m_setup.impulse_start_price) / m_symbol.Point() : 0.0;
        }

      pb_dist = (m_symbol.Point() > 0) ? (m_setup.impulse_peak_price - cur.mid) / m_symbol.Point() : 0.0;

      // Track lowest price reached during pullback (Structure SL)
      if(cur.mid < m_setup.pullback_extreme_price || m_setup.pullback_extreme_price == 0.0)
         m_setup.pullback_extreme_price = cur.mid;

      // Lock micro-pullback resistance level at top 25% of pullback distance
      if(m_setup.state == STATE_WAIT_FOR_PULLBACK)
         m_setup.pullback_micro_high_price = m_setup.pullback_extreme_price + (m_setup.impulse_peak_price - m_setup.pullback_extreme_price) * 0.25;
     }
   else if(m_setup.direction == SIGNAL_SELL)
     {
      // Track peak extension if price pushes lower before pulling back
      if(cur.mid < m_setup.impulse_peak_price)
        {
         m_setup.impulse_peak_price = cur.mid;
         m_setup.impulse_displacement = (m_symbol.Point() > 0) ? MathAbs(cur.mid - m_setup.impulse_start_price) / m_symbol.Point() : 0.0;
        }

      pb_dist = (m_symbol.Point() > 0) ? (cur.mid - m_setup.impulse_peak_price) / m_symbol.Point() : 0.0;

      // Track highest price reached during pullback (Structure SL)
      if(cur.mid > m_setup.pullback_extreme_price || m_setup.pullback_extreme_price == 0.0)
         m_setup.pullback_extreme_price = cur.mid;

      // Lock micro-pullback support level at bottom 25% of pullback distance
      if(m_setup.state == STATE_WAIT_FOR_PULLBACK)
         m_setup.pullback_micro_low_price = m_setup.pullback_extreme_price - (m_setup.pullback_extreme_price - m_setup.impulse_peak_price) * 0.25;
     }

   depth_pct_out = pb_dist / m_setup.impulse_displacement;

   // Check Invalidation
   long elapsed_sec = TimeCurrent() - m_setup.pullback_start_time;
   if(depth_pct_out > InpMaxPullbackDepthPct || elapsed_sec > InpMaxPullbackDurationSec)
     {
      if(InpEnableJournalLogs)
         PrintFormat("SETUP INVALIDATED: Depth=%.2f (Max=%.2f), Elapsed=%d s", depth_pct_out, InpMaxPullbackDepthPct, elapsed_sec);
      m_setup.state = STATE_IDLE;
      return 0.0;
     }

   // Evaluate counter-direction tick pressure
   double vel, displace, imbalance, accel;
   int consec;
   ComputeRollingTickMetrics(InpWindowShort, vel, displace, imbalance, accel, consec);

   // If counter momentum explodes opposite to impulse direction
   if((m_setup.direction == SIGNAL_BUY && imbalance < -0.8 && vel > 150.0) ||
      (m_setup.direction == SIGNAL_SELL && imbalance > 0.8 && vel > 150.0))
     {
      if(InpEnableJournalLogs)
         PrintFormat("SETUP INVALIDATED: Counter-momentum explosion! Imbalance=%.2f, Vel=%.2f", imbalance, vel);
      m_setup.state = STATE_IDLE;
      return 0.0;
     }

   // Calculate Quality Score
   double depth_score = 100.0 - MathAbs(depth_pct_out - 0.382) * 120.0; // Optimal depth near ~38.2%
   depth_score = MathMax(0.0, MathMin(100.0, depth_score));

   double counter_press_score = MathMax(0.0, 100.0 - (vel / 100.0) * 40.0);
   double spread_score = (cur.spread_pts <= m_avg_spread_pts) ? 100.0 : MathMax(0.0, 100.0 - (cur.spread_pts - m_avg_spread_pts) * 5.0);

   double pb_score = (depth_score * 0.5) + (counter_press_score * 0.3) + (spread_score * 0.2);
   return pb_score;
  }

//+------------------------------------------------------------------+
//| RE-ACCELERATION TRIGGER ENGINE                                   |
//+------------------------------------------------------------------+
double EvaluateReacceleration()
  {
   if(m_setup.state != STATE_PULLBACK_VALIDATED)
      return 0.0;

   double vel, displace, imbalance, accel;
   int consec;
   ComputeRollingTickMetrics(InpWindowShort, vel, displace, imbalance, accel, consec);

   bool dir_matches = (m_setup.direction == SIGNAL_BUY && imbalance > 0.1) ||
                      (m_setup.direction == SIGNAL_SELL && imbalance < -0.1);

   if(!dir_matches || consec < InpMinDirectionalTicks)
      return 0.0;

   double s_vel     = MathMin(100.0, (vel / 80.0) * 100.0);
   double s_accel   = MathMin(100.0, MathMax(0.0, (accel / 25.0) * 100.0));
   double s_imb     = MathAbs(imbalance) * 100.0;
   double s_consec  = MathMin(100.0, (consec / 4.0) * 100.0);

   double reaccel_score = (s_vel * 0.35) + (s_accel * 0.25) + (s_imb * 0.25) + (s_consec * 0.15);

   // Check Entry Mode Specific Trigger
   TickRecord cur = GetTickRelative(0);
   bool break_confirmed = false;

   switch(InpEntryMode)
     {
      case ENTRY_IMMEDIATE_REACCELERATION:
         break_confirmed = true;
         break;
      case ENTRY_MICRO_PULLBACK_BREAK:
         if(m_setup.direction == SIGNAL_BUY && cur.mid >= m_setup.pullback_micro_high_price) break_confirmed = true;
         if(m_setup.direction == SIGNAL_SELL && cur.mid <= m_setup.pullback_micro_low_price) break_confirmed = true;
         break;
      case ENTRY_BREAK_VELOCITY_CONFIRM:
         if(m_setup.direction == SIGNAL_BUY && cur.mid >= m_setup.pullback_micro_high_price && vel > 20.0) break_confirmed = true;
         if(m_setup.direction == SIGNAL_SELL && cur.mid <= m_setup.pullback_micro_low_price && vel > 20.0) break_confirmed = true;
         break;
      case ENTRY_BREAK_ACCEL_CONFIRM:
         if(m_setup.direction == SIGNAL_BUY && cur.mid >= m_setup.pullback_micro_high_price && accel > 5.0) break_confirmed = true;
         if(m_setup.direction == SIGNAL_SELL && cur.mid <= m_setup.pullback_micro_low_price && accel > 5.0) break_confirmed = true;
         break;
     }

   if(!break_confirmed)
      return 0.0;

   return reaccel_score;
  }

//+------------------------------------------------------------------+
//| UNIFIED TRADE SCORE ENGINE                                       |
//+------------------------------------------------------------------+
double CalculateTradeScore(double impulse_score, double pullback_score, double reaccel_score)
  {
   TickRecord cur = GetTickRelative(0);
   double spread_quality = (cur.spread_pts <= InpMaxAllowedSpreadPts) ? MathMax(0.0, 100.0 - (cur.spread_pts / InpMaxAllowedSpreadPts) * 30.0) : 0.0;
   double vol_quality = (m_current_regime == REGIME_EXPANSION) ? 100.0 : ((m_current_regime == REGIME_NORMAL_VOLATILITY) ? 80.0 : 40.0);
   double exec_quality = 90.0;

   double total = (impulse_score * 0.25) +
                  (pullback_score * 0.25) +
                  (reaccel_score * 0.25) +
                  (vol_quality * 0.10) +
                  (spread_quality * 0.10) +
                  (exec_quality * 0.05);

   return total;
  }

//+------------------------------------------------------------------+
//| STOP LOSS & TAKE PROFIT CALCULATION                              |
//+------------------------------------------------------------------+
void CalculateSLTP(ENUM_SIGNAL_DIR dir, double entry_price, double &sl_price, double &tp_price)
  {
   double atr_pts, m1_range, body_ratio, ema_f, ema_s;
   bool is_exp;
   UpdateMarketContext(atr_pts, m1_range, body_ratio, ema_f, ema_s, is_exp);

   double sl_pts = InpSLFixedPoints;

   switch(InpSLMode)
     {
      case SL_MODE_FIXED_POINTS:
         sl_pts = InpSLFixedPoints;
         break;
      case SL_MODE_ATR_BASED:
         sl_pts = atr_pts * InpSLATRMultiplier;
         break;
      case SL_MODE_PULLBACK_STRUCT:
         if(dir == SIGNAL_BUY)
            sl_pts = (m_symbol.Point() > 0) ? MathAbs(entry_price - m_setup.pullback_extreme_price) / m_symbol.Point() : InpSLFixedPoints;
         else
            sl_pts = (m_symbol.Point() > 0) ? MathAbs(m_setup.pullback_extreme_price - entry_price) / m_symbol.Point() : InpSLFixedPoints;
         break;
      case SL_MODE_IMPULSE_BASED:
         if(dir == SIGNAL_BUY)
            sl_pts = (m_symbol.Point() > 0) ? MathAbs(entry_price - m_setup.impulse_start_price) / m_symbol.Point() : InpSLFixedPoints;
         else
            sl_pts = (m_symbol.Point() > 0) ? MathAbs(m_setup.impulse_start_price - entry_price) / m_symbol.Point() : InpSLFixedPoints;
         break;
      case SL_MODE_HYBRID_ADAPTIVE:
         if(dir == SIGNAL_BUY)
            sl_pts = ((m_symbol.Point() > 0) ? MathAbs(entry_price - m_setup.pullback_extreme_price) / m_symbol.Point() : InpSLFixedPoints) + InpSLVolatilityBufferPts;
         else
            sl_pts = ((m_symbol.Point() > 0) ? MathAbs(m_setup.pullback_extreme_price - entry_price) / m_symbol.Point() : InpSLFixedPoints) + InpSLVolatilityBufferPts;
         break;
     }

   sl_pts = MathMax(sl_pts, (double)m_symbol.StopsLevel() + 10.0);

   if(dir == SIGNAL_BUY)
      sl_price = entry_price - (sl_pts * m_symbol.Point());
   else
      sl_price = entry_price + (sl_pts * m_symbol.Point());

   // Take Profit
   double tp_pts = sl_pts * InpRiskRewardRatio;
   if(InpTPMode == TP_MODE_ATR_TARGET)
      tp_pts = atr_pts * InpTPATRMultiplier;
   else if(InpTPMode == TP_MODE_DYNAMIC_TARGET)
      tp_pts = m_setup.impulse_displacement * 1.2;

   if(dir == SIGNAL_BUY)
      tp_price = entry_price + (tp_pts * m_symbol.Point());
   else
      tp_price = entry_price - (tp_pts * m_symbol.Point());

   sl_price = m_symbol.NormalizePrice(sl_price);
   tp_price = m_symbol.NormalizePrice(tp_price);
  }

//+------------------------------------------------------------------+
//| POSITION SIZING & RISK ENGINE                                    |
//+------------------------------------------------------------------+
double CalculateLotSize(double sl_distance_pts)
  {
   double lot = InpFixedLotSize;

   if(InpRiskMode == RISK_PCT_BALANCE || InpRiskMode == RISK_PCT_EQUITY || InpRiskMode == RISK_BASED_ON_SL)
     {
      double capital = (InpRiskMode == RISK_PCT_EQUITY) ? m_account.Equity() : m_account.Balance();
      double risk_amount = capital * (InpRiskPercent / 100.0);

      double tick_val = m_symbol.TickValue();
      double tick_sz  = m_symbol.TickSize();
      if(tick_val > 0.0 && tick_sz > 0.0 && sl_distance_pts > 0.0)
        {
         double loss_per_lot = (sl_distance_pts * m_symbol.Point() / tick_sz) * tick_val;
         if(loss_per_lot > 0.0)
            lot = risk_amount / loss_per_lot;
        }
     }

   // Normalize lot size according to symbol specs
   double min_lot = m_symbol.LotsMin();
   double max_lot = m_symbol.LotsMax();
   double step_lot = m_symbol.LotsStep();

   lot = MathFloor(lot / step_lot) * step_lot;
   lot = MathMax(min_lot, MathMin(max_lot, lot));

   // Check Margin Utilization Limit
   double margin_req = 0.0;
   if(OrderCalcMargin(ORDER_TYPE_BUY, _Symbol, lot, m_symbol.Ask(), margin_req))
     {
      double free_margin = m_account.FreeMargin();
      if(margin_req > free_margin * (InpMaxMarginUtilPct / 100.0))
        {
         double max_allowed_margin = free_margin * (InpMaxMarginUtilPct / 100.0);
         lot = lot * (max_allowed_margin / margin_req);
         lot = MathFloor(lot / step_lot) * step_lot;
         lot = MathMax(min_lot, lot);
        }
     }

   return lot;
  }

//+------------------------------------------------------------------+
//| EXECUTION CHECKS & ORDER PLACEMENT                               |
//+------------------------------------------------------------------+
bool CheckTradingFilters()
  {
   if(!m_symbol.IsSynchronized()) return false;
   if(m_current_regime == REGIME_ABNORMAL_SPREAD || m_current_regime == REGIME_LOW_VOLATILITY) return false;

   TickRecord cur = GetTickRelative(0);
   if(cur.spread_pts > InpMaxAllowedSpreadPts) return false;

   // Session filter
   if(InpUseSessionFilter)
     {
      MqlDateTime dt;
      TimeToStruct(TimeCurrent(), dt);
      if(dt.hour < InpSessionStartHour || dt.hour >= InpSessionEndHour)
         return false;
     }

   // Cooldowns
   if(TimeCurrent() - m_last_trade_time < InpMinTimeBetweenTradesSec) return false;
   if(TimeCurrent() - m_last_sl_time < InpCooldownAfterSLSec) return false;

   // Daily Loss & Profit Target
   double current_equity = m_account.Equity();
   if(m_daily_start_equity > 0.0)
     {
      double daily_pnl_pct = ((current_equity - m_daily_start_equity) / m_daily_start_equity) * 100.0;
      if(daily_pnl_pct <= -InpMaxDailyLossPct || daily_pnl_pct >= InpDailyProfitTargetPct)
         return false;
     }

   // Position Count Limits
   int total_pos = 0;
   int dir_pos = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(m_position.SelectByIndex(i) && m_position.Symbol() == _Symbol)
        {
         total_pos++;
         if(m_setup.direction == SIGNAL_BUY && m_position.PositionType() == POSITION_TYPE_BUY) dir_pos++;
         if(m_setup.direction == SIGNAL_SELL && m_position.PositionType() == POSITION_TYPE_SELL) dir_pos++;
        }
     }

   if(total_pos >= InpMaxSimultaneousPos) return false;
   if(InpOnePositionPerDirection && dir_pos > 0) return false;

   return true;
  }

void ExecuteTrade()
  {
   if(!CheckTradingFilters()) return;

   double entry_price = (m_setup.direction == SIGNAL_BUY) ? m_symbol.Ask() : m_symbol.Bid();
   double sl_price = 0.0, tp_price = 0.0;
   CalculateSLTP(m_setup.direction, entry_price, sl_price, tp_price);

   double sl_pts = (m_symbol.Point() > 0) ? MathAbs(entry_price - sl_price) / m_symbol.Point() : InpSLFixedPoints;
   double lot = CalculateLotSize(sl_pts);

   m_trade.SetDeviationInPoints(InpMaxSlippage);

   // Configure Filling Mode
   uint filling = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0)
      m_trade.SetTypeFilling(ORDER_FILLING_FOK);
   else if((filling & SYMBOL_FILLING_IOC) != 0)
      m_trade.SetTypeFilling(ORDER_FILLING_IOC);
   else
      m_trade.SetTypeFilling(ORDER_FILLING_RETURN);

   bool success = false;
   if(m_setup.direction == SIGNAL_BUY)
     {
      if(m_trade.Buy(lot, _Symbol, entry_price, sl_price, tp_price, "M1 Tick Scalp Buy"))
         success = true;
     }
   else if(m_setup.direction == SIGNAL_SELL)
     {
      if(m_trade.Sell(lot, _Symbol, entry_price, sl_price, tp_price, "M1 Tick Scalp Sell"))
         success = true;
     }

   if(success)
     {
      m_last_trade_time = TimeCurrent();
      m_trades_current_candle++;

      if(InpEnableJournalLogs)
        {
         PrintFormat("=========================================");
         PrintFormat("TRADE EXECUTED: %s", (m_setup.direction == SIGNAL_BUY) ? "BUY" : "SELL");
         PrintFormat("IMPULSE SCORE    : %.2f", m_setup.impulse_score);
         PrintFormat("PULLBACK SCORE   : %.2f", m_setup.pullback_score);
         PrintFormat("RE-ACCELERATION  : %.2f", m_setup.reaccel_score);
         PrintFormat("FINAL TRADE SCORE: %.2f", m_setup.trade_score);
         PrintFormat("ENTRY: %.5f | SL: %.5f | TP: %.5f | LOT: %.2f", entry_price, sl_price, tp_price, lot);
         PrintFormat("=========================================");
        }

      m_setup.state = STATE_IDLE;
     }
  }

//+------------------------------------------------------------------+
//| MOMENTUM DECAY EXIT & TRAILING STOP                              |
//+------------------------------------------------------------------+
void ManageOpenPositions()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!m_position.SelectByIndex(i) || m_position.Symbol() != _Symbol)
         continue;

      ulong ticket = m_position.Ticket();
      ENUM_POSITION_TYPE type = m_position.PositionType();
      double open_price = m_position.PriceOpen();
      double current_sl = m_position.StopLoss();
      double current_tp = m_position.TakeProfit();
      double current_price = (type == POSITION_TYPE_BUY) ? m_symbol.Bid() : m_symbol.Ask();

      double profit_pts = (type == POSITION_TYPE_BUY) ? (current_price - open_price) / m_symbol.Point()
                                                      : (open_price - current_price) / m_symbol.Point();

      double sl_dist_pts = (type == POSITION_TYPE_BUY) ? (open_price - current_sl) / m_symbol.Point()
                                                       : (current_sl - open_price) / m_symbol.Point();

      // 1. Momentum Decay Detector Exit
      if(InpEnableMomentumDecayExit && profit_pts > 30.0)
        {
         double vel, displace, imbalance, accel;
         int consec;
         ComputeRollingTickMetrics(InpWindowShort, vel, displace, imbalance, accel, consec);

         bool momentum_collapsed = false;
         if(type == POSITION_TYPE_BUY && imbalance < -0.4 && vel < 30.0) momentum_collapsed = true;
         if(type == POSITION_TYPE_SELL && imbalance > 0.4 && vel < 30.0) momentum_collapsed = true;

         if(momentum_collapsed)
           {
            if(InpEnableJournalLogs)
               PrintFormat("MOMENTUM DECAY DETECTED: Closing position #%d early at +%.1f pts", ticket, profit_pts);
            m_trade.PositionClose(ticket);
            continue;
           }
        }

      // 2. Trailing Stop Management
      if(InpUseTrailingSL && sl_dist_pts > 0.0)
        {
         if(profit_pts >= sl_dist_pts * InpTrailingStartRR)
           {
            double new_sl = 0.0;
            if(type == POSITION_TYPE_BUY)
              {
               new_sl = current_price - (InpTrailingStepPts * m_symbol.Point());
               if(new_sl > current_sl + (10.0 * m_symbol.Point()))
                 {
                  new_sl = m_symbol.NormalizePrice(new_sl);
                  m_trade.PositionModify(ticket, new_sl, current_tp);
                 }
              }
            else if(type == POSITION_TYPE_SELL)
              {
               new_sl = current_price + (InpTrailingStepPts * m_symbol.Point());
               if(current_sl == 0.0 || new_sl < current_sl - (10.0 * m_symbol.Point()))
                 {
                  new_sl = m_symbol.NormalizePrice(new_sl);
                  m_trade.PositionModify(ticket, new_sl, current_tp);
                 }
              }
           }
        }
     }
  }

//+------------------------------------------------------------------+
//| VISUAL CHART DRAWING                                             |
//+------------------------------------------------------------------+
void UpdateChartVisuals()
  {
   if(!InpEnableDebugVisuals || MQLInfoInteger(MQL_TESTER)) return;

   string prefix = "M1_Scalp_";

   // 1. Setup Status Panel
   string panel_name = prefix + "Panel";
   if(ObjectFind(0, panel_name) < 0)
     {
      ObjectCreate(0, panel_name, OBJ_RECTANGLE_LABEL, 0, 0, 0);
      ObjectSetInteger(0, panel_name, OBJPROP_CORNER, (long)CORNER_LEFT_UPPER);
      ObjectSetInteger(0, panel_name, OBJPROP_XDISTANCE, 20);
      ObjectSetInteger(0, panel_name, OBJPROP_YDISTANCE, 30);
      ObjectSetInteger(0, panel_name, OBJPROP_XSIZE, 260);
      ObjectSetInteger(0, panel_name, OBJPROP_YSIZE, 160);
      ObjectSetInteger(0, panel_name, OBJPROP_BGCOLOR, (long)clrBlack);
      ObjectSetInteger(0, panel_name, OBJPROP_BORDER_TYPE, (long)BORDER_FLAT);
      ObjectSetInteger(0, panel_name, OBJPROP_COLOR, (long)clrGold);
     }

   string text_name = prefix + "Label";
   if(ObjectFind(0, text_name) < 0)
     {
      ObjectCreate(0, text_name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, text_name, OBJPROP_CORNER, (long)CORNER_LEFT_UPPER);
      ObjectSetInteger(0, text_name, OBJPROP_XDISTANCE, 30);
      ObjectSetInteger(0, text_name, OBJPROP_YDISTANCE, 40);
      ObjectSetInteger(0, text_name, OBJPROP_COLOR, (long)clrWhite);
      ObjectSetString(0, text_name, OBJPROP_FONT, "Courier New");
      ObjectSetInteger(0, text_name, OBJPROP_FONTSIZE, 9);
     }

   string state_str = "IDLE";
   if(m_setup.state == STATE_WAIT_FOR_PULLBACK) state_str = "WAIT_FOR_PULLBACK";
   else if(m_setup.state == STATE_PULLBACK_VALIDATED) state_str = "PULLBACK_VALIDATED";
   else if(m_setup.state == STATE_REACCELERATING) state_str = "REACCELERATING";

   string msg = StringFormat("M1 TICK SCALPING EA\n"+
                             "---------------------\n"+
                             "STATE     : %s\n"+
                             "DIR       : %s\n"+
                             "IMPULSE   : %.1f (%.1f pts)\n"+
                             "PULLBACK  : %.1f (%.1f%%)\n"+
                             "REACCEL   : %.1f\n"+
                             "TRADE SCORE: %.1f\n"+
                             "REGIME    : %d",
                             state_str,
                             (m_setup.direction == SIGNAL_BUY) ? "BUY" : ((m_setup.direction == SIGNAL_SELL) ? "SELL" : "NONE"),
                             m_setup.impulse_score,
                             m_setup.impulse_displacement,
                             m_setup.pullback_score,
                             m_setup.pullback_depth_pct * 100.0,
                             m_setup.reaccel_score,
                             m_setup.trade_score,
                             (int)m_current_regime);

   ObjectSetString(0, text_name, OBJPROP_TEXT, msg);
   ChartRedraw(0);
  }

void CleanVisualObjects()
  {
   ObjectsDeleteAll(0, "M1_Scalp_");
  }

//+------------------------------------------------------------------+
//| EXPERT INITIALIZATION FUNCTION                                   |
//+------------------------------------------------------------------+
int OnInit()
  {
   m_symbol.Name(_Symbol);
   if(!m_symbol.RefreshRates())
     {
      Print("Failed to refresh symbol rates during initialization.");
      return INIT_FAILED;
     }

   // Initialize Indicators
   m_atr_handle = iATR(_Symbol, InpContextTimeframe, InpATRPeriod);
   m_ema_fast_handle = iMA(_Symbol, InpContextTimeframe, InpEMAFastPeriod, 0, MODE_EMA, PRICE_CLOSE);
   m_ema_slow_handle = iMA(_Symbol, InpContextTimeframe, InpEMASlowPeriod, 0, MODE_EMA, PRICE_CLOSE);

   if(m_atr_handle == INVALID_HANDLE || m_ema_fast_handle == INVALID_HANDLE || m_ema_slow_handle == INVALID_HANDLE)
     {
      Print("Failed to initialize indicator handles.");
      return INIT_FAILED;
     }

   // Setup Setup state struct
   m_setup.state = STATE_IDLE;
   m_setup.direction = SIGNAL_NONE;

   m_daily_start_equity = m_account.Equity();
   m_daily_reset_time = TimeCurrent();

   PrintFormat("M1 Tick Scalping EA Initialized Successfully on %s", _Symbol);
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
//| EXPERT DEINITIALIZATION FUNCTION                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   if(m_atr_handle != INVALID_HANDLE) IndicatorRelease(m_atr_handle);
   if(m_ema_fast_handle != INVALID_HANDLE) IndicatorRelease(m_ema_fast_handle);
   if(m_ema_slow_handle != INVALID_HANDLE) IndicatorRelease(m_ema_slow_handle);

   CleanVisualObjects();
   Print("M1 Tick Scalping EA Deinitialized.");
  }

//+------------------------------------------------------------------+
//| EXPERT TICK FUNCTION                                             |
//+------------------------------------------------------------------+
void OnTick()
  {
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick)) return;

   m_symbol.RefreshRates();
   AddTickRecord(tick);

   // Check M1 Candle Reset for Trade Limits
   datetime m1_time = iTime(_Symbol, PERIOD_M1, 0);
   if(m1_time != m_current_m1_candle)
     {
      m_current_m1_candle = m1_time;
      m_trades_current_candle = 0;
     }

   // Daily Equity Baseline Reset (00:00 Server Time)
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(dt.hour == 0 && dt.min == 0 && dt.sec <= 5)
     {
      m_daily_start_equity = m_account.Equity();
     }

   // 1. Manage Active Positions
   ManageOpenPositions();

   // 2. Core State Machine Evaluation
   switch(m_setup.state)
     {
      case STATE_IDLE:
        {
         ENUM_SIGNAL_DIR imp_dir;
         double imp_score = EvaluateImpulse(imp_dir);
         if(imp_score >= InpImpulseScoreThreshold && imp_dir != SIGNAL_NONE)
           {
            int window_ticks = MathMin(InpWindowMed, m_tick_count - 1);
            TickRecord oldest_imp = GetTickRelative(window_ticks);
            double mid_price = (tick.bid + tick.ask) * 0.5;

            m_setup.state = STATE_WAIT_FOR_PULLBACK;
            m_setup.direction = imp_dir;
            m_setup.impulse_score = imp_score;
            m_setup.impulse_start_time = oldest_imp.time_msc;
            m_setup.impulse_peak_time = tick.time_msc;
            m_setup.impulse_start_price = oldest_imp.mid;
            m_setup.impulse_peak_price = mid_price;

            double disp_pts = (m_symbol.Point() > 0) ? MathAbs(mid_price - oldest_imp.mid) / m_symbol.Point() : 0.0;
            m_setup.impulse_displacement = MathMax(disp_pts, InpMinImpulseDisplacePts);

            m_setup.pullback_extreme_price = mid_price;
            m_setup.pullback_micro_high_price = mid_price;
            m_setup.pullback_micro_low_price = mid_price;
            m_setup.pullback_start_time = TimeCurrent();

            if(InpEnableJournalLogs)
               PrintFormat("IMPULSE DETECTED: Score=%.1f, Dir=%s, Start=%.5f, Peak=%.5f, Disp=%.1f pts",
                           imp_score, (imp_dir == SIGNAL_BUY) ? "BUY" : "SELL", oldest_imp.mid, mid_price, m_setup.impulse_displacement);
           }
         break;
        }

      case STATE_WAIT_FOR_PULLBACK:
      case STATE_PULLBACK_VALIDATED:
        {
         double depth_pct;
         double pb_score = EvaluatePullbackQuality(depth_pct);
         m_setup.pullback_depth_pct = depth_pct;
         m_setup.pullback_score = pb_score;

         if(m_setup.state == STATE_IDLE) break; // Setup was invalidated inside EvaluatePullbackQuality

         if(depth_pct >= InpMinPullbackDepthPct && depth_pct <= InpMaxPullbackDepthPct && pb_score >= InpPullbackScoreThreshold)
           {
            m_setup.state = STATE_PULLBACK_VALIDATED;

            // Check Re-acceleration Trigger
            double reaccel_score = EvaluateReacceleration();
            m_setup.reaccel_score = reaccel_score;

            if(reaccel_score >= InpReaccelScoreThreshold)
              {
               m_setup.state = STATE_REACCELERATING;
               double trade_score = CalculateTradeScore(m_setup.impulse_score, pb_score, reaccel_score);
               m_setup.trade_score = trade_score;

               if(trade_score >= InpMinTradeScore)
                 {
                  ExecuteTrade();
                 }
               else
                 {
                  if(InpEnableJournalLogs)
                     PrintFormat("TRADE REJECTED: Low TradeScore=%.1f (Min=%.1f)", trade_score, InpMinTradeScore);
                  m_setup.state = STATE_IDLE;
                 }
              }
           }
         break;
        }

      default:
         m_setup.state = STATE_IDLE;
         break;
     }

   // 3. Update Chart Visual Dashboard
   UpdateChartVisuals();
  }

//+------------------------------------------------------------------+
//| TRADE TRANSACTION CALLBACK FOR BACKTESTER METRICS                |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                         const MqlTradeRequest &request,
                         const MqlTradeResult &result)
  {
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD)
     {
      ulong deal_ticket = trans.deal;
      if(HistoryDealSelect(deal_ticket))
        {
         string deal_symbol = HistoryDealGetString(deal_ticket, DEAL_SYMBOL);
         long deal_entry    = HistoryDealGetInteger(deal_ticket, DEAL_ENTRY);

         if(deal_symbol == _Symbol && deal_entry == DEAL_ENTRY_OUT)
           {
            m_total_trades++;
            double profit     = HistoryDealGetDouble(deal_ticket, DEAL_PROFIT);
            double swap       = HistoryDealGetDouble(deal_ticket, DEAL_SWAP);
            double commission = HistoryDealGetDouble(deal_ticket, DEAL_COMMISSION);
            double pnl        = profit + swap + commission;

            if(pnl > 0.0)
              {
               m_winning_trades++;
               m_total_profit += pnl;
              }
            else
              {
               m_losing_trades++;
               m_total_loss += MathAbs(pnl);
               m_last_sl_time = TimeCurrent(); // Update SL cooldown baseline
              }
           }
        }
     }
  }

//+------------------------------------------------------------------+
//| TESTER REPORTING FUNCTION                                        |
//+------------------------------------------------------------------+
double OnTester()
  {
   double profit_factor = 0.0;
   if(m_total_loss > 0.0)
      profit_factor = m_total_profit / m_total_loss;
   else if(m_total_profit > 0.0)
      profit_factor = 99.0;

   double win_rate = (m_total_trades > 0) ? ((double)m_winning_trades / (double)m_total_trades) * 100.0 : 0.0;

   PrintFormat("=== TESTER SUMMARY METRICS ===");
   PrintFormat("Total Trades : %d", m_total_trades);
   PrintFormat("Wins / Losses: %d / %d", m_winning_trades, m_losing_trades);
   PrintFormat("Win Rate     : %.2f%%", win_rate);
   PrintFormat("Total Profit : %.2f", m_total_profit);
   PrintFormat("Total Loss   : %.2f", m_total_loss);
   PrintFormat("Profit Factor: %.2f", profit_factor);

   return profit_factor;
  }
//+------------------------------------------------------------------+
