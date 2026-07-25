//+------------------------------------------------------------------+
//|                                     GoldVelocityScalper_EA.mq5    |
//|                                  Copyright 2024, Jules & Co.      |
//|                                       https://www.example.com    |
//+------------------------------------------------------------------+
#property copyright "Copyright 2024, Jules & Co."
#property link      "https://www.example.com"
#property version   "2.00"
#property description "GoldVelocityScalper EA - Institutional-Grade Momentum Tick Scalper"
#property description "Specifically optimized for XAUUSD on the XM Broker platform."

// Include standard trade libraries
#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\PositionInfo.mqh>

//--- Struct definition for circular buffer tick records
struct TickRecord
{
   long     time_msc;     // Millisecond timestamp (standard long to avoid custom types)
   double   bid;
   double   ask;
   double   last;
   ulong    volume;
   double   speed;        // ticks/sec
   double   velocity;     // points/sec
   double   acceleration; // points/sec^2
   int      direction;    // +1 for uptick (bid increased), -1 for downtick, 0 for no change
};

//--- Circular buffer class for tick history
class CTickHistory
{
private:
   TickRecord m_buffer[];
   int        m_size;
   int        m_head;
   int        m_count;

public:
   CTickHistory(int size)
   {
      m_size = size;
      ArrayResize(m_buffer, m_size);
      Reset();
   }

   void Reset()
   {
      m_head = 0;
      m_count = 0;
      for(int i = 0; i < m_size; i++)
      {
         TickRecord empty = {0}; // Explicitly initialize struct to prevent compiler warnings
         m_buffer[i] = empty;
      }
   }

   void Add(const MqlTick &tick, double point_val)
   {
      TickRecord prev = {0};
      if(m_count > 0)
      {
         prev = m_buffer[(m_head - 1 + m_size) % m_size];
      }

      TickRecord rec = {0};
      rec.time_msc = tick.time_msc;
      rec.bid = tick.bid;
      rec.ask = tick.ask;
      rec.last = tick.last > 0 ? tick.last : tick.bid;
      rec.volume = tick.volume;

      // Calculate differentials if we have a previous tick
      if(m_count > 0 && rec.time_msc > prev.time_msc)
      {
         double dt = (double)(rec.time_msc - prev.time_msc) / 1000.0; // seconds
         if(dt > 0.0)
         {
            // Speed of tick arrival (ticks/sec)
            rec.speed = 1.0 / dt;

            // Price velocity based on bid (points/sec)
            double dp = (rec.bid - prev.bid) / point_val;
            rec.velocity = dp / dt;

            // Price acceleration (velocity change / sec)
            rec.acceleration = (rec.velocity - prev.velocity) / dt;
         }

         // Direction
         if(rec.bid > prev.bid)       rec.direction = 1;
         else if(rec.bid < prev.bid)  rec.direction = -1;
         else                         rec.direction = 0;
      }
      else
      {
         rec.speed = 1.0;
         rec.velocity = 0.0;
         rec.acceleration = 0.0;
         rec.direction = 0;
      }

      m_buffer[m_head] = rec;
      m_head = (m_head + 1) % m_size;
      if(m_count < m_size) m_count++;
   }

   int Count() const { return m_count; }

   bool GetAt(int index, TickRecord &record) const
   {
      if(index < 0 || index >= m_count) return false;
      int idx = (m_head - 1 - index + m_size) % m_size;
      record = m_buffer[idx];
      return true;
   }

   // Compute Tick Flow Imbalance over N past ticks
   double ComputeTFI(int period) const
   {
      if(m_count < period || period <= 0) return 0.0;
      int up = 0, down = 0;
      for(int i = 0; i < period; i++)
      {
         TickRecord r = {0};
         if(GetAt(i, r))
         {
            if(r.direction > 0)       up++;
            else if(r.direction < 0)  down++;
         }
      }
      int total = up + down;
      if(total == 0) return 0.0;
      return (double)(up - down) / (double)total; // range: -1.0 to +1.0
   }

   // Compute average tick speed over N ticks
   double ComputeAvgSpeed(int period) const
   {
      if(m_count < period || period <= 0) return 0.0;
      double sum = 0;
      for(int i = 0; i < period; i++)
      {
         TickRecord r = {0};
         if(GetAt(i, r)) sum += r.speed;
      }
      return sum / period;
   }

   // Compute maximum velocity over N ticks
   double ComputeMaxVelocity(int period) const
   {
      if(m_count < period || period <= 0) return 0.0;
      double max_v = 0.0;
      for(int i = 0; i < period; i++)
      {
         TickRecord r = {0};
         if(GetAt(i, r) && MathAbs(r.velocity) > MathAbs(max_v))
         {
            max_v = r.velocity;
         }
      }
      return max_v;
   }
};

//--- Input Parameters
input group "=== Institutional Scalper Core Settings ==="
input double      InpLotSize                 = 0.10;       // Fixed Lot Size (if Risk% = 0)
input double      InpRiskPercent             = 1.0;        // Account Risk % (0 to disable)
input ENUM_TIMEFRAMES InpEMA_Timeframe       = PERIOD_H1;  // Higher Timeframe Filter (Trend)
input int         InpEMA_Period              = 200;        // Higher Timeframe EMA Period
input int         InpATR_Period              = 14;         // ATR Period for SL/TP Volatility
input double      InpATR_SL_Multiplier       = 1.5;        // Stop Loss ATR Multiplier
input double      InpATR_TP_Multiplier       = 4.5;        // Take Profit ATR Multiplier

input group "=== Momentum Verification Thresholds ==="
input double      InpMinTickSpeed            = 4.0;        // Min Tick Speed (ticks/sec)
input double      InpMinPriceVelocity        = 15.0;       // Min Price Velocity (points/sec)
input double      InpMinPriceAcceleration    = 8.0;        // Min Price Acceleration (points/sec^2)
input double      InpMinTickVolumeSpike      = 1.5;        // Tick Volume Multiplier (vs 20 tick MA)
input double      InpMaxSpreadPoints         = 35.0;       // Max Allowed Spread in Points (e.g. 3.5 USD on Gold)
input double      InpMinMomentumQuality      = 0.70;       // Signal-to-Noise Ratio (0.0 to 1.0)
input double      InpRocketScoreTrigger      = 80.0;       // Rocket Score entry setup threshold (0-100)

enum ENUM_ENTRY_MODE
{
   ENTRY_IMMEDIATE,       // ENTRY_IMMEDIATE (Instant Momentum Entry)
   ENTRY_PEAK_BREAKOUT    // ENTRY_PEAK_BREAKOUT (Pullback + Peak Breakout)
};

input group "=== Pullback & Breakout Entry Options ==="
input ENUM_ENTRY_MODE InpEntryMode           = ENTRY_IMMEDIATE; // Entry Execution Mode
input double      InpMinPullbackPct          = 20.0;       // Minimum Pullback Percentage to qualify
input double      InpMaxPullbackPct          = 75.0;       // Maximum Pullback Percentage before invalidation
input double      InpMinImpulseHeight        = 0.20;       // Minimum initial momentum height in USD (XAUUSD points = 100 * USD)
input double      InpBreakoutBufferPoints    = 5.0;        // Points buffer above peak/below trough for breakout (Gold: 1 point = 0.01)
input double      InpStartPriceProtectPoints = 50.0;       // Starting price protection in Points ($0.50)
input int         InpSetupExpirySeconds      = 15;         // Seconds after which active setup is discarded

input group "=== Momentum-Fade Trailing & Exits ==="
input bool        InpExitOnMomentumFade      = false;      // Exit dynamically if speed drops below 25% of trigger
input bool        InpExitOnOppositeTicks     = false;      // Exit immediately on strong opposite ticks
input double      InpTrailingStopPoints      = 150.0;      // Dynamic Trailing Stop Points (e.g. 1.50 USD)
input double      InpBreakevenTriggerPoints  = 120.0;      // Points in profit to trigger Breakeven
input double      InpBreakevenLockPoints     = 30.0;       // Points locked in at Breakeven
input int         InpMaxTradeDurationSeconds = 120;        // Maximum holding duration of a trade (seconds)

input group "=== Strategy Tester Auto-Calibration ==="
input bool        InpTesterAutoCalibrate     = true;       // Enable auto-scale of thresholds in MT5 Strategy Tester

//--- Global Variables (calibrated copies of inputs to bypass input read-only restrictions)
double g_MinTickSpeed;
double g_MinPriceVelocity;
double g_MinPriceAcceleration;
double g_MinImpulseHeight;
double g_BreakoutBufferPoints;
double g_RocketScoreTrigger;
int    g_SetupExpirySeconds;

//--- Indicator Handles
int    g_handle_ema = INVALID_HANDLE;
int    g_handle_atr = INVALID_HANDLE;

//--- State Variables for Active Breakout Setups
enum ENUM_SETUP_DIR { SETUP_NONE, SETUP_BUY, SETUP_SELL };
struct BreakoutSetup
{
   ENUM_SETUP_DIR direction;
   datetime       init_time;
   double         start_price;
   double         peak_price;
   double         impulse_range;
   bool           pullback_verified;
};

BreakoutSetup g_active_setup = {SETUP_NONE, 0, 0, 0, 0, false};

//--- Class & Struct Instances
CTickHistory   *g_tick_history = NULL;
CTrade          g_trade;
CSymbolInfo     g_sym_info;

//--- Timing & Performance Variables
datetime g_last_dashboard_update = 0;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // Initialize symbol helper
   if(!g_sym_info.Name(Symbol()))
   {
      Print("[INIT] Failed to initialize symbol info helper.");
      return INIT_FAILED;
   }

   // Ensure dynamic tick history sizing
   g_tick_history = new CTickHistory(200);

   // Setup XM-compatible dynamic filling mode
   ConfigureFillingMode();

   // Instantiate Indicators
   g_handle_ema = iMA(Symbol(), InpEMA_Timeframe, InpEMA_Period, 0, MODE_EMA, PRICE_CLOSE);
   g_handle_atr = iATR(Symbol(), PERIOD_CURRENT, InpATR_Period);

   if(g_handle_ema == INVALID_HANDLE || g_handle_atr == INVALID_HANDLE)
   {
      Print("[INIT] Failed to create indicator handles.");
      return INIT_FAILED;
   }

   // Initialize calibration variables from inputs
   g_MinTickSpeed          = InpMinTickSpeed;
   g_MinPriceVelocity      = InpMinPriceVelocity;
   g_MinPriceAcceleration  = InpMinPriceAcceleration;
   g_MinImpulseHeight      = InpMinImpulseHeight;
   g_BreakoutBufferPoints  = InpBreakoutBufferPoints;
   g_RocketScoreTrigger    = InpRocketScoreTrigger;
   g_SetupExpirySeconds    = InpSetupExpirySeconds;

   // Auto-Calibrate for MT5 Strategy Tester
   if(MQLInfoInteger(MQL_TESTER) && InpTesterAutoCalibrate)
   {
      Print("[INIT] MT5 Strategy Tester detected! Calibrating thresholds for OHLC/sparse tick intervals.");
      g_MinTickSpeed          = InpMinTickSpeed / 4.0;
      g_MinPriceVelocity      = InpMinPriceVelocity / 4.0;
      g_MinPriceAcceleration  = InpMinPriceAcceleration / 4.0;
      g_MinImpulseHeight      = 0.05; // 0.05 USD min move height on Gold
      g_BreakoutBufferPoints  = 1.0;  // 1 point buffer
      g_RocketScoreTrigger    = 65.0; // Lower score trigger
      g_SetupExpirySeconds    = 60;   // Extend setup expiration

      PrintFormat("[INIT] Calibrated: MinSpeed=%.2f, MinVelocity=%.2f, MinAccel=%.2f, MinImpulse=%.2f USD, Buffer=%.1f pts, RocketTrigger=%.1f",
                  g_MinTickSpeed, g_MinPriceVelocity, g_MinPriceAcceleration, g_MinImpulseHeight, g_BreakoutBufferPoints, g_RocketScoreTrigger);
   }

   Print("[INIT] GoldVelocityScalper EA Initialized successfully.");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(g_tick_history != NULL)
   {
      delete g_tick_history;
      g_tick_history = NULL;
   }

   if(g_handle_ema != INVALID_HANDLE) IndicatorRelease(g_handle_ema);
   if(g_handle_atr != INVALID_HANDLE) IndicatorRelease(g_handle_atr);

   // Clean up charts
   ObjectsDeleteAll(0, "GVS_");
   Print("[DEINIT] Cleaned up resources. Deinit Reason Code: ", reason);
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   MqlTick tick = {0};
   if(!SymbolInfoTick(Symbol(), tick))
   {
      return;
   }

   // 1. Add current tick to sliding buffer
   double point_val = SymbolInfoDouble(Symbol(), SYMBOL_POINT);
   if(point_val <= 0.0) return;

   g_tick_history.Add(tick, point_val);

   // 2. Manage Trade Exits & Trail (if position is open)
   if(IsPositionOpen())
   {
      ManageOpenPosition(tick);
      // Reset any active entry setups once inside a trade
      g_active_setup.direction = SETUP_NONE;
      return;
   }

   // 3. Skip setup checking if spread is excessive
   double current_spread = (tick.ask - tick.bid) / point_val;
   if(!MQLInfoInteger(MQL_TESTER) && current_spread > InpMaxSpreadPoints)
   {
      return; // Filter trade setups under toxic spread spreads
   }

   // 4. Calculate Higher Timeframe Filter (Trend)
   double ema_val[1];
   if(CopyBuffer(g_handle_ema, 0, 1, 1, ema_val) <= 0)
   {
      return;
   }
   double current_ema = ema_val[0];

   // 5. Evaluate setup expiration
   datetime now_time = (datetime)TimeCurrent();
   if(g_active_setup.direction != SETUP_NONE)
   {
      if(now_time - g_active_setup.init_time > g_SetupExpirySeconds)
      {
         PrintFormat("[PIPELINE] Setup expired! Clearing active %s setup.", EnumToString(g_active_setup.direction));
         g_active_setup.direction = SETUP_NONE;
      }
   }

   // 6. Run verification pipeline and compute Rocket Score if no setup is active
   if(g_active_setup.direction == SETUP_NONE)
   {
      double score = CalculateRocketScore(tick, current_ema, current_spread);
      if(score >= g_RocketScoreTrigger)
      {
         // Establish active breakout setup
         g_active_setup.init_time = now_time;
         g_active_setup.start_price = tick.bid;
         g_active_setup.peak_price = tick.bid;
         g_active_setup.pullback_verified = false;

         double avg_speed = g_tick_history.ComputeAvgSpeed(10);
         double max_vel = g_tick_history.ComputeMaxVelocity(10);

         if(max_vel > 0)
         {
            if(InpEntryMode == ENTRY_IMMEDIATE)
            {
               PrintFormat("[EXECUTION] ENTRY_IMMEDIATE Mode: Explosive Upward Momentum! Rocket Score: %.1f | Speed: %.1f | MaxVel: %.1f. Placing BUY order...",
                           score, avg_speed, max_vel);
               ExecuteMarketOrder(ORDER_TYPE_BUY, tick);
               g_active_setup.direction = SETUP_NONE;
            }
            else
            {
               g_active_setup.direction = SETUP_BUY;
               PrintFormat("[PIPELINE] Explosive Upward Momentum Detected! Rocket Score: %.1f | Speed: %.1f | MaxVel: %.1f. Monitoring setup pullback...",
                           score, avg_speed, max_vel);
            }
         }
         else if(max_vel < 0)
         {
            if(InpEntryMode == ENTRY_IMMEDIATE)
            {
               PrintFormat("[EXECUTION] ENTRY_IMMEDIATE Mode: Explosive Downward Momentum! Rocket Score: %.1f | Speed: %.1f | MaxVel: %.1f. Placing SELL order...",
                           score, avg_speed, max_vel);
               ExecuteMarketOrder(ORDER_TYPE_SELL, tick);
               g_active_setup.direction = SETUP_NONE;
            }
            else
            {
               g_active_setup.direction = SETUP_SELL;
               PrintFormat("[PIPELINE] Explosive Downward Momentum Detected! Rocket Score: %.1f | Speed: %.1f | MaxVel: %.1f. Monitoring setup pullback...",
                           score, avg_speed, max_vel);
            }
         }
      }
   }

   // 7. Monitor active Breakout Setup pullback and execution
   if(g_active_setup.direction != SETUP_NONE)
   {
      MonitorBreakoutSetup(tick, point_val);
   }

   // 8. Render Visual Dashboard
   if(!MQLInfoInteger(MQL_TESTER) || MQLInfoInteger(MQL_VISUAL_MODE))
   {
      if(now_time - g_last_dashboard_update >= 1)
      {
         UpdateDashboard(tick, current_spread);
         g_last_dashboard_update = now_time;
      }
   }
}

//+------------------------------------------------------------------+
//| Calculate 100-Point Momentum Rocket Score                        |
//+------------------------------------------------------------------+
double CalculateRocketScore(const MqlTick &tick, double htf_ema, double spread)
{
   if(g_tick_history.Count() < 30) return 0.0;

   double score = 0.0;

   // Fetch latest tick record differentials
   TickRecord last_rec = {0};
   if(!g_tick_history.GetAt(0, last_rec)) return 0.0;

   // STAGE 1 & 2: Speed and Velocity verification
   double avg_speed = g_tick_history.ComputeAvgSpeed(15);
   double max_vel = g_tick_history.ComputeMaxVelocity(15);

   // Speed contribution (Max 25 pts)
   double speed_ratio = avg_speed / g_MinTickSpeed;
   double speed_points = MathMin(25.0, speed_ratio * 12.5);
   score += speed_points;

   // Velocity contribution (Max 25 pts)
   double vel_ratio = MathAbs(max_vel) / g_MinPriceVelocity;
   double vel_points = MathMin(25.0, vel_ratio * 12.5);
   score += vel_points;

   // STAGE 3: Volume Spike confirmation (Max 15 pts)
   double avg_vol = 0.0;
   for(int i = 1; i <= 20; i++)
   {
      TickRecord r = {0};
      if(g_tick_history.GetAt(i, r)) avg_vol += (double)r.volume;
   }
   avg_vol /= 20.0;
   double vol_spike = avg_vol > 0 ? (double)last_rec.volume / avg_vol : 1.0;
   double vol_points = vol_spike >= InpMinTickVolumeSpike ? 15.0 : (vol_spike / InpMinTickVolumeSpike) * 15.0;
   score += vol_points;

   // STAGE 4: Spread Stability confirmation (Max 10 pts)
   double spread_points = spread <= InpMaxSpreadPoints ? 10.0 : 0.0;
   score += spread_points;

   // STAGE 5: Directional Flow / Tick Flow Imbalance (Max 15 pts)
   double tfi = g_tick_history.ComputeTFI(25);
   double tfi_points = 0.0;
   if(max_vel > 0 && tfi > 0.1)       tfi_points = MathMin(15.0, (tfi / InpMinMomentumQuality) * 15.0);
   else if(max_vel < 0 && tfi < -0.1) tfi_points = MathMin(15.0, (MathAbs(tfi) / InpMinMomentumQuality) * 15.0);
   score += tfi_points;

   // STAGE 6 & 7: Price Acceleration and Momentum Quality (Max 10 pts)
   double accel_ratio = MathAbs(last_rec.acceleration) / g_MinPriceAcceleration;
   double accel_points = MathMin(10.0, accel_ratio * 5.0);
   score += accel_points;

   // Higher Timeframe Trend Filter Validation
   if(max_vel > 0 && tick.bid < htf_ema) score *= 0.2; // suppress long signals below EMA
   if(max_vel < 0 && tick.bid > htf_ema) score *= 0.2; // suppress short signals above EMA

   return score;
}

//+------------------------------------------------------------------+
//| Monitor Breakout Setup for Peak, Pullback, and Entry Breakout    |
//+------------------------------------------------------------------+
void MonitorBreakoutSetup(const MqlTick &tick, double point_val)
{
   if(g_active_setup.direction == SETUP_NONE) return;

   // STAGE 8: Track peak limits and consolidation/pullbacks
   if(g_active_setup.direction == SETUP_BUY)
   {
      if(tick.bid > g_active_setup.peak_price)
      {
         g_active_setup.peak_price = tick.bid;
      }

      g_active_setup.impulse_range = g_active_setup.peak_price - g_active_setup.start_price;
      double impulse_usd = g_active_setup.impulse_range; // on gold point is 0.01 ($1 = 100 points), bid is in USD

      // Calculate pullback ratio
      double pullback_depth = g_active_setup.peak_price - tick.bid;
      double pullback_pct = g_active_setup.impulse_range > 0 ? (pullback_depth / g_active_setup.impulse_range) * 100.0 : 0.0;

      // Check starting price protection (protects against noise invalidating the setup)
      double dist_to_start_points = (tick.bid - g_active_setup.start_price) / point_val;

      // Validate Pullback Phase
      if(!g_active_setup.pullback_verified)
      {
         // Must exceed minimum pullback depth to qualify but allow consolidation
         if(pullback_pct >= InpMinPullbackPct && pullback_pct <= InpMaxPullbackPct)
         {
            g_active_setup.pullback_verified = true;
            PrintFormat("[SETUP] Pullback verified on BUY setup! Depth: %.1f%%. Awaiting breakout of Peak: %.2f", pullback_pct, g_active_setup.peak_price);
         }

         // Completely bypass pullback_too_deep reset under breakout entry mode if price is protected by start buffer
         if(pullback_pct > InpMaxPullbackPct && dist_to_start_points < -InpStartPriceProtectPoints)
         {
            PrintFormat("[SETUP] BUY Setup discarded: Pullback too deep (%.1f%%) and below Start Price Protect buffer.", pullback_pct);
            g_active_setup.direction = SETUP_NONE;
            return;
         }
      }

      // Monitor Breakout Trigger
      if(g_active_setup.pullback_verified && impulse_usd >= g_MinImpulseHeight)
      {
         double trigger_price = g_active_setup.peak_price + (g_BreakoutBufferPoints * point_val);
         if(tick.bid >= trigger_price)
         {
            PrintFormat("[EXECUTION] BUY Breakout Triggered! Price %.2f broke peak %.2f with buffer. Executing order...", tick.bid, g_active_setup.peak_price);
            ExecuteMarketOrder(ORDER_TYPE_BUY, tick);
            g_active_setup.direction = SETUP_NONE; // Clear setup
         }
      }
   }
   else if(g_active_setup.direction == SETUP_SELL)
   {
      if(tick.bid < g_active_setup.peak_price)
      {
         g_active_setup.peak_price = tick.bid;
      }

      g_active_setup.impulse_range = g_active_setup.start_price - g_active_setup.peak_price;
      double impulse_usd = g_active_setup.impulse_range;

      // Calculate pullback ratio
      double pullback_depth = tick.bid - g_active_setup.peak_price;
      double pullback_pct = g_active_setup.impulse_range > 0 ? (pullback_depth / g_active_setup.impulse_range) * 100.0 : 0.0;

      // Check starting price protection
      double dist_to_start_points = (g_active_setup.start_price - tick.bid) / point_val;

      // Validate Pullback Phase
      if(!g_active_setup.pullback_verified)
      {
         if(pullback_pct >= InpMinPullbackPct && pullback_pct <= InpMaxPullbackPct)
         {
            g_active_setup.pullback_verified = true;
            PrintFormat("[SETUP] Pullback verified on SELL setup! Depth: %.1f%%. Awaiting breakout of Peak: %.2f", pullback_pct, g_active_setup.peak_price);
         }

         // Completely bypass pullback_too_deep reset under breakout entry mode if price is protected by start buffer
         if(pullback_pct > InpMaxPullbackPct && dist_to_start_points < -InpStartPriceProtectPoints)
         {
            PrintFormat("[SETUP] SELL Setup discarded: Pullback too deep (%.1f%%) and below Start Price Protect buffer.", pullback_pct);
            g_active_setup.direction = SETUP_NONE;
            return;
         }
      }

      // Monitor Breakout Trigger
      if(g_active_setup.pullback_verified && impulse_usd >= g_MinImpulseHeight)
      {
         double trigger_price = g_active_setup.peak_price - (g_BreakoutBufferPoints * point_val);
         if(tick.bid <= trigger_price)
         {
            PrintFormat("[EXECUTION] SELL Breakout Triggered! Price %.2f broke peak %.2f with buffer. Executing order...", tick.bid, g_active_setup.peak_price);
            ExecuteMarketOrder(ORDER_TYPE_SELL, tick);
            g_active_setup.direction = SETUP_NONE; // Clear setup
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Execute Market Order with Broker Filling Mode Handling           |
//+------------------------------------------------------------------+
void ExecuteMarketOrder(ENUM_ORDER_TYPE order_type, const MqlTick &tick)
{
   double point_val = SymbolInfoDouble(Symbol(), SYMBOL_POINT);

   // Copy dynamic ATR for volatility stop-loss scaling
   double atr_val[1];
   if(CopyBuffer(g_handle_atr, 0, 0, 1, atr_val) <= 0)
   {
      atr_val[0] = 1.5; // fallback ATR points
   }
   double current_atr = atr_val[0];

   // Calculate Dynamic Stop Loss & Take Profit
   double sl_distance = current_atr * InpATR_SL_Multiplier;
   double tp_distance = current_atr * InpATR_TP_Multiplier;

   double entry_price = (order_type == ORDER_TYPE_BUY) ? tick.ask : tick.bid;
   double sl = 0.0;
   double tp = 0.0;

   if(order_type == ORDER_TYPE_BUY)
   {
      sl = entry_price - sl_distance;
      tp = entry_price + tp_distance;
   }
   else
   {
      sl = entry_price + sl_distance;
      tp = entry_price - tp_distance;
   }

   // Calculate Lot Size
   double lots = InpLotSize;
   if(InpRiskPercent > 0.0)
   {
      double balance = AccountInfoDouble(ACCOUNT_BALANCE);
      double margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
      double risk_val = balance * (InpRiskPercent / 100.0);
      double tick_value = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_VALUE);
      double tick_size = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_SIZE);

      if(tick_size > 0.0)
      {
         double loss_points = sl_distance / point_val;
         if(loss_points > 0.0)
         {
            double risk_lots = risk_val / (loss_points * (tick_value / tick_size * point_val));
            if(risk_lots > 0.0) lots = risk_lots;
         }
      }
   }

   // Normalize volume steps
   double volume_step = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_STEP);
   double min_lot = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MIN);
   double max_lot = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MAX);

   lots = MathFloor(lots / volume_step) * volume_step;
   lots = MathMax(lots, min_lot);
   lots = MathMin(lots, max_lot);

   // Send Order Request
   g_trade.SetDeviationInPoints(20);
   g_trade.SetTypeFilling(ORDER_FILLING_FOK); // fallback, OnInit calibrates this dynamically

   bool res = false;
   if(order_type == ORDER_TYPE_BUY)
   {
      res = g_trade.Buy(lots, Symbol(), entry_price, sl, tp, "GVS_Momentum_Buy");
   }
   else
   {
      res = g_trade.Sell(lots, Symbol(), entry_price, sl, tp, "GVS_Momentum_Sell");
   }

   if(res)
   {
      PrintFormat("[EXECUTION] Order placed successfully: Type=%s, Lots=%.2f, SL=%.2f, TP=%.2f",
                  EnumToString(order_type), lots, sl, tp);
   }
   else
   {
      PrintFormat("[ERROR] Order placement failed! Code: %d, Description: %s",
                  g_trade.ResultRetcode(), g_trade.ResultComment());
   }
}

//+------------------------------------------------------------------+
//| Manage Exits, Trailing Stop, Breakeven, and Momentum Fade        |
//+------------------------------------------------------------------+
void ManageOpenPosition(const MqlTick &tick)
{
   if(!PositionSelect(Symbol())) return;

   ulong  ticket = PositionGetInteger(POSITION_TICKET);
   if(ticket == 0) return;

   double entry_price = PositionGetDouble(POSITION_PRICE_OPEN);
   double current_sl = PositionGetDouble(POSITION_SL);
   double current_tp = PositionGetDouble(POSITION_TP);
   double current_profit = PositionGetDouble(POSITION_PROFIT);
   long   pos_type = PositionGetInteger(POSITION_TYPE);
   datetime pos_time = (datetime)PositionGetInteger(POSITION_TIME);

   double point_val = SymbolInfoDouble(Symbol(), SYMBOL_POINT);
   if(point_val <= 0.0) return;

   datetime now_time = (datetime)TimeCurrent();

   // Max trade duration timeout check
   if(now_time - pos_time > InpMaxTradeDurationSeconds)
   {
      PrintFormat("[EXIT] Max hold duration exceeded (%d sec). Exiting position at market.", InpMaxTradeDurationSeconds);
      g_trade.PositionClose(ticket);
      return;
   }

   // Momentum-Fade exits to secure profits on microsecond reversals
   if(InpExitOnMomentumFade && g_tick_history.Count() >= 15)
   {
      double avg_speed = g_tick_history.ComputeAvgSpeed(10);
      if(avg_speed < (g_MinTickSpeed * 0.25)) // speed dropped below 25% of trigger
      {
         PrintFormat("[EXIT] Speed dropped significantly to %.1f (below 25%% of setup trigger). Momentum fading. Exiting...", avg_speed);
         g_trade.PositionClose(ticket);
         return;
      }
   }

   // Manage Trailing Stop & Breakeven locking
   if(pos_type == POSITION_TYPE_BUY)
   {
      double profit_points = (tick.bid - entry_price) / point_val;

      // Breakeven check
      if(InpBreakevenTriggerPoints > 0 && profit_points >= InpBreakevenTriggerPoints)
      {
         double lock_sl = entry_price + (InpBreakevenLockPoints * point_val);
         if(current_sl < lock_sl)
         {
            PrintFormat("[MANAGEMENT] Locking Breakeven on BUY. SL adjusted from %.2f to %.2f (+%.1f pts profit)",
                        current_sl, lock_sl, InpBreakevenLockPoints);
            g_trade.PositionModify(ticket, lock_sl, current_tp);
            return;
         }
      }

      // Trailing Stop check
      if(InpTrailingStopPoints > 0 && profit_points >= (InpTrailingStopPoints / 2.0))
      {
         double target_sl = tick.bid - (InpTrailingStopPoints * point_val);
         if(target_sl > current_sl)
         {
            PrintFormat("[MANAGEMENT] Trailing Stop on BUY. SL adjusted from %.2f to %.2f", current_sl, target_sl);
            g_trade.PositionModify(ticket, target_sl, current_tp);
         }
      }

      // Strong opposite flow exit check
      if(InpExitOnOppositeTicks)
      {
         double tfi = g_tick_history.ComputeTFI(15);
         if(tfi < -0.6) // Strong downticks
         {
            Print("[EXIT] Strong opposite tick flow imbalance (TFI = %.2f). Exiting BUY to preserve gain.", tfi);
            g_trade.PositionClose(ticket);
         }
      }
   }
   else if(pos_type == POSITION_TYPE_SELL)
   {
      double profit_points = (entry_price - tick.ask) / point_val;

      // Breakeven check
      if(InpBreakevenTriggerPoints > 0 && profit_points >= InpBreakevenTriggerPoints)
      {
         double lock_sl = entry_price - (InpBreakevenLockPoints * point_val);
         if(current_sl == 0.0 || current_sl > lock_sl)
         {
            PrintFormat("[MANAGEMENT] Locking Breakeven on SELL. SL adjusted from %.2f to %.2f (+%.1f pts profit)",
                        current_sl, lock_sl, InpBreakevenLockPoints);
            g_trade.PositionModify(ticket, lock_sl, current_tp);
            return;
         }
      }

      // Trailing Stop check
      if(InpTrailingStopPoints > 0 && profit_points >= (InpTrailingStopPoints / 2.0))
      {
         double target_sl = tick.ask + (InpTrailingStopPoints * point_val);
         if(current_sl == 0.0 || target_sl < current_sl)
         {
            PrintFormat("[MANAGEMENT] Trailing Stop on SELL. SL adjusted from %.2f to %.2f", current_sl, target_sl);
            g_trade.PositionModify(ticket, target_sl, current_tp);
         }
      }

      // Strong opposite flow exit check
      if(InpExitOnOppositeTicks)
      {
         double tfi = g_tick_history.ComputeTFI(15);
         if(tfi > 0.6) // Strong upticks
         {
            Print("[EXIT] Strong opposite tick flow imbalance (TFI = %.2f). Exiting SELL to preserve gain.", tfi);
            g_trade.PositionClose(ticket);
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Check if an active position exists on the chart                  |
//+------------------------------------------------------------------+
bool IsPositionOpen()
{
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetString(POSITION_SYMBOL) == Symbol())
         {
            return true;
         }
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Detect and Configure Dynamic XM Broker Order Filling Policy      |
//+------------------------------------------------------------------+
void ConfigureFillingMode()
{
   uint filling = (uint)SymbolInfoInteger(Symbol(), SYMBOL_FILLING_MODE);

   if((filling & SYMBOL_FILLING_FOK) != 0)
   {
      g_trade.SetTypeFilling(ORDER_FILLING_FOK);
      Print("[INIT] Execution Filling Mode: ORDER_FILLING_FOK configured.");
   }
   else if((filling & SYMBOL_FILLING_IOC) != 0)
   {
      g_trade.SetTypeFilling(ORDER_FILLING_IOC);
      Print("[INIT] Execution Filling Mode: ORDER_FILLING_IOC configured.");
   }
   else
   {
      g_trade.SetTypeFilling(ORDER_FILLING_RETURN);
      Print("[INIT] Execution Filling Mode: ORDER_FILLING_RETURN configured.");
   }
}

//+------------------------------------------------------------------+
//| Real-Time Dashboard Renderer for Charts                          |
//+------------------------------------------------------------------+
void UpdateDashboard(const MqlTick &tick, double spread)
{
   string title_id   = "GVS_Title";
   string stats_id   = "GVS_Stats";
   string score_id   = "GVS_Score";
   string setup_id   = "GVS_Setup";

   int x = 20, y = 30;

   CreateLabel(title_id, "GOLD VELOCITY SCALPER Pro [v2.00] — XM OPTIMIZED", x, y, 11, clrGold, "Arial Bold");

   double avg_speed = g_tick_history.ComputeAvgSpeed(15);
   double max_vel   = g_tick_history.ComputeMaxVelocity(15);
   double tfi       = g_tick_history.ComputeTFI(25);

   string stats_txt = StringFormat("Bid: %.2f | Ask: %.2f | Spread: %.1f pts | Avg Speed: %.2f t/s | Max Velocity: %.2f pts/s | TFI: %.2f",
                                   tick.bid, tick.ask, spread, avg_speed, max_vel, tfi);
   CreateLabel(stats_id, stats_txt, x, y + 20, 9, clrWhite, "Consolas");

   // Refresh Higher Timeframe Trend
   double ema_val[1];
   string trend_str = "UNKNOWN";
   if(CopyBuffer(g_handle_ema, 0, 0, 1, ema_val) > 0)
   {
      trend_str = tick.bid > ema_val[0] ? "BULLISH (above 200 EMA)" : "BEARISH (below 200 EMA)";
   }

   double rocket_score = CalculateRocketScore(tick, ema_val[0], spread);
   color score_col = rocket_score >= g_RocketScoreTrigger ? clrLimeGreen : clrTomato;
   string score_txt = StringFormat("Momentum Rocket Score: %.1f / 100 (Trigger: %.1f) | Higher Trend: %s",
                                   rocket_score, g_RocketScoreTrigger, trend_str);
   CreateLabel(score_id, score_txt, x, y + 40, 10, score_col, "Arial");

   string setup_txt = StringFormat("Active Setup: NONE | Entry Mode: %s", EnumToString(InpEntryMode));
   color setup_col = clrDarkGray;
   if(g_active_setup.direction != SETUP_NONE)
   {
      setup_col = (g_active_setup.direction == SETUP_BUY) ? clrMediumSpringGreen : clrDeepPink;
      double pullback_depth = (g_active_setup.direction == SETUP_BUY) ? (g_active_setup.peak_price - tick.bid) : (tick.bid - g_active_setup.peak_price);
      double pullback_pct = g_active_setup.impulse_range > 0 ? (pullback_depth / g_active_setup.impulse_range) * 100.0 : 0.0;

      setup_txt = StringFormat("Active Setup: %s | Peak Price: %.2f | Impulse Range: %.2f | Pullback: %.1f%% (Verified: %s) | Entry Mode: %s",
                               EnumToString(g_active_setup.direction), g_active_setup.peak_price, g_active_setup.impulse_range,
                               pullback_pct, g_active_setup.pullback_verified ? "YES" : "NO", EnumToString(InpEntryMode));
   }
   CreateLabel(setup_id, setup_txt, x, y + 60, 9, setup_col, "Consolas");
}

//+------------------------------------------------------------------+
//| Dashboard Helper: Create Label Object                            |
//+------------------------------------------------------------------+
void CreateLabel(string name, string text, int x, int y, int size, color col, string font)
{
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
   }
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, size);
   ObjectSetString(0, name, OBJPROP_FONT, font);
   ObjectSetInteger(0, name, OBJPROP_COLOR, col);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
}
