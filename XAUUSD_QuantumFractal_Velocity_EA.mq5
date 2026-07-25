//+------------------------------------------------------------------+
//|                                XAUUSD_QuantumFractal_Velocity_EA |
//|                                                     Jules C.     |
//|                                              https://www.mql5.com|
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, Jules C."
#property link      "https://www.mql5.com"
#property version   "2.00"
#property strict

// Include Standard Library Trades
#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\PositionInfo.mqh>

//--- Expert Advisor Inputs ---
input string      Inp_EADesc                 = "--- QUANTUM BREAKOUT VELOCITY SYSTEM ---"; // System Strategy Description
input group       "--- RISK & MONEY MANAGEMENT ---";
input double      Inp_FixedLotSize           = 0.1;           // Fixed Lot Size (if AutoRisk = 0)
input double      Inp_RiskPercent            = 1.0;           // Risk Percentage per Trade (0 = Disabled, uses Fixed Lot)
input double      Inp_MaxSpreadPoints        = 40.0;          // Max Allowed Spread in Points (1 point = 0.01 on Gold)
input double      Inp_SlippagePoints         = 30.0;          // Execution Slippage in Points
input ulong       Inp_MagicNumber            = 882026;        // EA Unique Magic Number

input group       "--- TREND & REJECTION FILTERS ---";
input ENUM_TIMEFRAMES Inp_Timeframe          = PERIOD_M15;    // Trading & Analysis Timeframe (M15/M30 is optimal for XAUUSD)
input int         Inp_FastEMAPeriod          = 21;            // Fast EMA Period
input int         Inp_SlowEMAPeriod          = 200;           // Slow EMA Period for Major Trend Filter
input double      Inp_MinWickPct             = 45.0;          // Minimum Rejection Wick Percentage (e.g. 45%)
input double      Inp_EntryBufferPoints      = 10.0;          // Entry breakout buffer above/below signal high/low (points)
input double      Inp_MinCandlePoints        = 50.0;          // Ignore tiny candle noise (points)

input group       "--- RISK-REWARD RATIOS (STRUCTURAL SL/TP) ---";
input double      Inp_RewardToRiskRatio      = 2.5;           // TP is this multiplier of the tight structural SL distance
input double      Inp_MinSLPoints            = 80.0;          // Minimum allowed structural Stop Loss (points) to avoid spreads
input double      Inp_MaxSLPoints            = 350.0;         // Maximum allowed structural Stop Loss (points) to cut huge risk
input bool        Inp_EnableTrailingStop     = true;          // Enable Trailing Stop
input double      Inp_TrailingStartPoints    = 150.0;         // Trailing Trigger Points from Entry
input double      Inp_TrailingStepPoints     = 50.0;          // Trailing Step Points

input group       "--- SESSION & TIME CONTROLS ---";
input bool        Inp_UseSessionFilter       = true;          // Restrict Entries to High Volume Sessions
input int         Inp_StartHourGMT           = 7;             // Session Start Hour (GMT/EET Broker Time - London Open)
input int         Inp_EndHourGMT             = 20;            // Session End Hour (GMT/EET Broker Time - NY Session)

//--- Struct to hold Signal Candle setup data ---
struct SignalSetup
{
   bool     isActive;         // Whether a signal is active and waiting for a next-candle breakout
   datetime setupTime;        // Time of the signal candle
   int      direction;        // 1 for BUY, -1 for SELL
   double   triggerPrice;     // High + Buffer (for BUY), Low - Buffer (for SELL)
   double   stopLoss;         // Low - Buffer (for BUY), High + Buffer (for SELL)
   double   takeProfit;       // SL Distance * RR Ratio
   double   signalHigh;       // Cache signal high
   double   signalLow;        // Cache signal low
};

//--- Global Variables ---
CTrade            m_trade;                                    // CTrade Execution Object
CSymbolInfo       m_symbol;                                   // CSymbolInfo Helper Object
CPositionInfo     m_position;                                 // CPositionInfo Helper Object
int               m_fast_ema_handle    = INVALID_HANDLE;      // Fast EMA Handle
int               m_slow_ema_handle    = INVALID_HANDLE;      // Slow EMA Handle
double            m_points_scale       = 1.0;                 // Points conversion scale based on digits
bool              m_is_tester          = false;               // Cache for Strategy Tester state
bool              m_is_visual          = false;               // Cache for Visual Mode state
datetime          m_last_checked_bar   = 0;                   // Keep track of evaluated bars
SignalSetup       m_active_setup;                             // Tracks pending breakouts

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   string symbol_name = Symbol();
   if(StringFind(symbol_name, "XAU") < 0 && StringFind(symbol_name, "GOLD") < 0)
   {
      Print("[WARNING] Quantum Breakout EA is designed and optimized specifically for XAUUSD (GOLD).");
   }

   // Initialize Symbol Info Helper
   if(!m_symbol.Name(symbol_name))
   {
      Print("[ERROR] Failed to initialize CSymbolInfo for symbol: ", symbol_name);
      return(INIT_FAILED);
   }
   m_symbol.Refresh();

   // Set Magic Number for trade operations
   m_trade.SetExpertMagicNumber(Inp_MagicNumber);

   // Configure Broker Execution Filling Mode dynamically
   ConfigureFillingMode();

   m_points_scale = m_symbol.Point();
   m_is_tester = (bool)MQLInfoInteger(MQL_TESTER);
   m_is_visual = (bool)MQLInfoInteger(MQL_VISUAL_MODE);

   // Initialize EMA Indicators
   m_fast_ema_handle = iMA(symbol_name, Inp_Timeframe, Inp_FastEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
   m_slow_ema_handle = iMA(symbol_name, Inp_Timeframe, Inp_SlowEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);

   if(m_fast_ema_handle == INVALID_HANDLE || m_slow_ema_handle == INVALID_HANDLE)
   {
      Print("[ERROR] Failed to initialize indicator handles. OnInit aborted.");
      return(INIT_FAILED);
   }

   // Reset Signal Setup struct
   m_active_setup.isActive = false;
   m_last_checked_bar = 0;

   Print("[INIT SUCCESS] Quantum Breakout EA Initialized. Magic: ", Inp_MagicNumber, " | Digits: ", m_symbol.Digits());
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(m_fast_ema_handle != INVALID_HANDLE) IndicatorRelease(m_fast_ema_handle);
   if(m_slow_ema_handle != INVALID_HANDLE) IndicatorRelease(m_slow_ema_handle);
   Print("[DEINIT] Quantum Breakout EA stopped. Reason: ", reason);
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // Refresh Symbol Market Data
   if(!m_symbol.RefreshRates()) return;

   // Check spread filter (Bypass in Strategy Tester)
   double current_spread = (m_symbol.Ask() - m_symbol.Bid()) / m_symbol.Point();
   if(!m_is_tester && current_spread > Inp_MaxSpreadPoints)
   {
      return;
   }

   // Manage Trailing Stops on active positions
   ManageActivePositions();

   // One Position at a Time Constraint
   if(HasOpenPosition())
   {
      m_active_setup.isActive = false; // Reset setup if trade is already active
      return;
   }

   // Identify the start of a new bar on Inp_Timeframe
   datetime bar_time[];
   if(CopyTime(Symbol(), Inp_Timeframe, 0, 1, bar_time) < 1) return;

   bool is_new_bar = (bar_time[0] != m_last_checked_bar);

   // If a new bar opened, check if the previous pending breakout has expired (Strict 1-candle rule)
   if(is_new_bar)
   {
      if(m_active_setup.isActive)
      {
         // If a setup was defined on the previous bar, and we've now opened a new bar without entry, discard it!
         Print("[SETUP EXPIRED] Breakout did not occur during the next immediate candle. Discarding setup at ", m_active_setup.setupTime);
         m_active_setup.isActive = false;
      }

      m_last_checked_bar = bar_time[0];

      // Perform Signal Identification on the newly completed bar (Index 1)
      DetectSignalCandle();
   }

   // Check live intraday price movement for breakout triggers on the active signal setup
   if(m_active_setup.isActive)
   {
      CheckBreakoutTrigger();
   }
}

//+------------------------------------------------------------------+
//| Detects qualified pullback/rejection signal candle on bar index 1 |
//+------------------------------------------------------------------+
void DetectSignalCandle()
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(Symbol(), Inp_Timeframe, 1, 2, rates) < 2) return;

   MqlRates sig = rates[0]; // Completed candle (index 1)

   double range = (sig.high - sig.low) / m_points_scale;
   if(range < Inp_MinCandlePoints) return; // Ignore tiny noise candles

   // Calculate body and wicks
   double open_price = sig.open;
   double close_price = sig.close;
   double body_high = MathMax(open_price, close_price);
   double body_low = MathMin(open_price, close_price);

   double body_size = (body_high - body_low) / m_points_scale;
   double upper_wick = (sig.high - body_high) / m_points_scale;
   double lower_wick = (body_low - sig.low) / m_points_scale;

   // Calculate EMAs for Trend Filtering
   double fast_ema_array[], slow_ema_array[];
   ArraySetAsSeries(fast_ema_array, true);
   ArraySetAsSeries(slow_ema_array, true);

   if(CopyBuffer(m_fast_ema_handle, 0, 1, 1, fast_ema_array) < 1 ||
      CopyBuffer(m_slow_ema_handle, 0, 1, 1, slow_ema_array) < 1)
   {
      return;
   }

   double fast_ema = fast_ema_array[0];
   double slow_ema = slow_ema_array[0];

   bool is_uptrend = (fast_ema > slow_ema);
   bool is_downtrend = (fast_ema < slow_ema);

   // Restrict trading to specified active sessions
   if(!IsMarketSessionOpen()) return;

   // 1. BUY SIGNAL: BULLISH REJECTION / PULLBACK (e.g. Hammer / Pinbar / Touch 21 EMA in Uptrend)
   if(is_uptrend)
   {
      double lower_wick_pct = (range > 0) ? (lower_wick / range) * 100.0 : 0;

      // Candidate if we have a strong lower wick (rejection of lower prices)
      // OR if price has pulled back to touch the 21 EMA and closes above it
      bool lower_rejection = (lower_wick_pct >= Inp_MinWickPct);
      bool ema_pullback = (sig.low <= fast_ema && sig.close > fast_ema);

      if(lower_rejection || ema_pullback)
      {
         m_active_setup.isActive = true;
         m_active_setup.setupTime = sig.time;
         m_active_setup.direction = 1;
         m_active_setup.signalHigh = sig.high;
         m_active_setup.signalLow = sig.low;

         // Trigger price is the high of the signal candle + breakout buffer
         m_active_setup.triggerPrice = sig.high + (Inp_EntryBufferPoints * m_points_scale);

         // TIGHT STRUCTURAL STOP LOSS: Exactly under the low of the signal candle!
         double sl_distance = sig.high - sig.low + (Inp_EntryBufferPoints * 2.0 * m_points_scale);
         double sl_points = sl_distance / m_points_scale;

         // Constrain SL to safe structural points
         if(sl_points < Inp_MinSLPoints) sl_distance = Inp_MinSLPoints * m_points_scale;
         if(sl_points > Inp_MaxSLPoints) sl_distance = Inp_MaxSLPoints * m_points_scale;

         m_active_setup.stopLoss = m_active_setup.triggerPrice - sl_distance;
         m_active_setup.takeProfit = m_active_setup.triggerPrice + (sl_distance * Inp_RewardToRiskRatio);

         Print("[SETUP PENDING] Bullish Setup defined at ", sig.time,
               " | Trigger Ask: ", m_active_setup.triggerPrice,
               " | Structural SL: ", m_active_setup.stopLoss,
               " | Expected TP: ", m_active_setup.takeProfit);
      }
   }

   // 2. SELL SIGNAL: BEARISH REJECTION / PULLBACK (e.g. Shooting Star / Touch 21 EMA in Downtrend)
   if(is_downtrend && !m_active_setup.isActive)
   {
      double upper_wick_pct = (range > 0) ? (upper_wick / range) * 100.0 : 0;

      bool upper_rejection = (upper_wick_pct >= Inp_MinWickPct);
      bool ema_pullback = (sig.high >= fast_ema && sig.close < fast_ema);

      if(upper_rejection || ema_pullback)
      {
         m_active_setup.isActive = true;
         m_active_setup.setupTime = sig.time;
         m_active_setup.direction = -1;
         m_active_setup.signalHigh = sig.high;
         m_active_setup.signalLow = sig.low;

         // Trigger price is the low of the signal candle - breakout buffer
         m_active_setup.triggerPrice = sig.low - (Inp_EntryBufferPoints * m_points_scale);

         // TIGHT STRUCTURAL STOP LOSS: Exactly above the high of the signal candle!
         double sl_distance = sig.high - sig.low + (Inp_EntryBufferPoints * 2.0 * m_points_scale);
         double sl_points = sl_distance / m_points_scale;

         // Constrain SL to safe structural points
         if(sl_points < Inp_MinSLPoints) sl_distance = Inp_MinSLPoints * m_points_scale;
         if(sl_points > Inp_MaxSLPoints) sl_distance = Inp_MaxSLPoints * m_points_scale;

         m_active_setup.stopLoss = m_active_setup.triggerPrice + sl_distance;
         m_active_setup.takeProfit = m_active_setup.triggerPrice - (sl_distance * Inp_RewardToRiskRatio);

         Print("[SETUP PENDING] Bearish Setup defined at ", sig.time,
               " | Trigger Bid: ", m_active_setup.triggerPrice,
               " | Structural SL: ", m_active_setup.stopLoss,
               " | Expected TP: ", m_active_setup.takeProfit);
      }
   }
}

//+------------------------------------------------------------------+
//| Check Live Tick Prices to see if next candle breakout occurred   |
//+------------------------------------------------------------------+
void CheckBreakoutTrigger()
{
   if(!m_active_setup.isActive) return;

   if(m_active_setup.direction == 1)
   {
      // Buy Entry: price breaks above triggerPrice (Ask price)
      double current_ask = m_symbol.Ask();
      if(current_ask >= m_active_setup.triggerPrice)
      {
         // Verify we don't have extremely deep gap breakout beyond the SL/TP ratio
         if(current_ask > m_active_setup.triggerPrice + (150 * m_points_scale))
         {
            Print("[TRIGGER CANCELLED] Extreme breakout gap. Bypassing high risk buy at ", current_ask);
            m_active_setup.isActive = false;
            return;
         }

         double lot_size = CalculateLotSize(current_ask - m_active_setup.stopLoss);

         // Anti-race lock
         m_active_setup.isActive = false;

         if(m_trade.Buy(lot_size, Symbol(), current_ask, m_active_setup.stopLoss, m_active_setup.takeProfit, "Quantum Breakout BUY"))
         {
            Print("[ORDER SUCCESS] Long Breakout Triggered. Entry: ", current_ask,
                  " | SL: ", m_active_setup.stopLoss,
                  " | TP: ", m_active_setup.takeProfit,
                  " | Lots: ", lot_size,
                  " | Risk: ", DoubleToString(Inp_RiskPercent, 1), "%");
         }
         else
         {
            Print("[ORDER FAILED] Long Entry failed: ", m_trade.ResultRetcodeDescription());
         }
      }
   }
   else if(m_active_setup.direction == -1)
   {
      // Sell Entry: price breaks below triggerPrice (Bid price)
      double current_bid = m_symbol.Bid();
      if(current_bid <= m_active_setup.triggerPrice)
      {
         // Verify we don't have extremely deep gap breakout
         if(current_bid < m_active_setup.triggerPrice - (150 * m_points_scale))
         {
            Print("[TRIGGER CANCELLED] Extreme breakout gap. Bypassing high risk sell at ", current_bid);
            m_active_setup.isActive = false;
            return;
         }

         double lot_size = CalculateLotSize(m_active_setup.stopLoss - current_bid);

         // Anti-race lock
         m_active_setup.isActive = false;

         if(m_trade.Sell(lot_size, Symbol(), current_bid, m_active_setup.stopLoss, m_active_setup.takeProfit, "Quantum Breakout SELL"))
         {
            Print("[ORDER SUCCESS] Short Breakout Triggered. Entry: ", current_bid,
                  " | SL: ", m_active_setup.stopLoss,
                  " | TP: ", m_active_setup.takeProfit,
                  " | Lots: ", lot_size,
                  " | Risk: ", DoubleToString(Inp_RiskPercent, 1), "%");
         }
         else
         {
            Print("[ORDER FAILED] Short Entry failed: ", m_trade.ResultRetcodeDescription());
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Dynamic Position Lot Sizing Based on Account Balance & Risk %     |
//+------------------------------------------------------------------+
double CalculateLotSize(double sl_distance)
{
   if(Inp_RiskPercent <= 0 || sl_distance <= 0)
   {
      return NormalizeLotSize(Inp_FixedLotSize);
   }

   double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double tick_value = m_symbol.TickValue();
   double tick_size = m_symbol.TickSize();

   if(tick_value <= 0 || tick_size <= 0)
   {
      return NormalizeLotSize(Inp_FixedLotSize);
   }

   double risk_amount = free_margin * (Inp_RiskPercent / 100.0);
   double sl_points = sl_distance / m_points_scale;
   double point_value = (tick_value / tick_size) * m_symbol.Point();

   double computed_lot = risk_amount / (sl_points * point_value);

   return NormalizeLotSize(computed_lot);
}

//+------------------------------------------------------------------+
//| Normalize Position Size According to Broker Volume Guidelines   |
//+------------------------------------------------------------------+
double NormalizeLotSize(double computed_lot)
{
   double min_lot = m_symbol.LotsMin();
   double max_lot = m_symbol.LotsMax();
   double lot_step = m_symbol.LotsStep();

   double normalized_lot = MathRound(computed_lot / lot_step) * lot_step;

   if(normalized_lot < min_lot) normalized_lot = min_lot;
   if(normalized_lot > max_lot) normalized_lot = max_lot;

   return NormalizeDouble(normalized_lot, 2);
}

//+------------------------------------------------------------------+
//| Dynamically configures the filling mode for CTrade               |
//+------------------------------------------------------------------+
void ConfigureFillingMode()
{
   uint filling_flags = (uint)SymbolInfoInteger(Symbol(), SYMBOL_FILLING_MODE);

   if((filling_flags & SYMBOL_FILLING_FOK) != 0)
   {
      m_trade.SetTypeFilling(ORDER_FILLING_FOK);
   }
   else if((filling_flags & SYMBOL_FILLING_IOC) != 0)
   {
      m_trade.SetTypeFilling(ORDER_FILLING_IOC);
   }
   else
   {
      m_trade.SetTypeFilling(ORDER_FILLING_RETURN);
   }
}

//+------------------------------------------------------------------+
//| Manage Trailing Stop-Loss for Active Positions                    |
//+------------------------------------------------------------------+
void ManageActivePositions()
{
   if(!Inp_EnableTrailingStop) return;

   int total_positions = PositionsTotal();
   for(int i = total_positions - 1; i >= 0; i--)
   {
      if(m_position.SelectByIndex(i))
      {
         if(m_position.Symbol() == Symbol() && m_position.Magic() == Inp_MagicNumber)
         {
            double entry_price = m_position.PriceOpen();
            double current_sl = m_position.StopLoss();

            if(m_position.PositionType() == POSITION_TYPE_BUY)
            {
               double bid = m_symbol.Bid();
               // Check if price moved enough points in profit to start trailing
               if(bid - entry_price > Inp_TrailingStartPoints * m_points_scale)
               {
                  double new_sl = NormalizeDouble(bid - (Inp_TrailingStepPoints * m_points_scale), m_symbol.Digits());
                  if(new_sl > current_sl || current_sl == 0)
                  {
                     m_trade.PositionModify(m_position.Ticket(), new_sl, m_position.TakeProfit());
                  }
               }
            }
            else if(m_position.PositionType() == POSITION_TYPE_SELL)
            {
               double ask = m_symbol.Ask();
               // Check if price moved enough points in profit to start trailing
               if(entry_price - ask > Inp_TrailingStartPoints * m_points_scale)
               {
                  double new_sl = NormalizeDouble(ask + (Inp_TrailingStepPoints * m_points_scale), m_symbol.Digits());
                  if(new_sl < current_sl || current_sl == 0)
                  {
                     m_trade.PositionModify(m_position.Ticket(), new_sl, m_position.TakeProfit());
                  }
               }
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Check if we already have an open position with our Magic Number |
//+------------------------------------------------------------------+
bool HasOpenPosition()
{
   int total_positions = PositionsTotal();
   for(int i = 0; i < total_positions; i++)
   {
      if(m_position.SelectByIndex(i))
      {
         if(m_position.Symbol() == Symbol() && m_position.Magic() == Inp_MagicNumber)
         {
            return true;
         }
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Verify Session/Time Controls                                     |
//+------------------------------------------------------------------+
bool IsMarketSessionOpen()
{
   if(!Inp_UseSessionFilter) return true;

   datetime current_time = TimeCurrent();
   MqlDateTime dt;
   TimeToStruct(current_time, dt);

   // Restrict weekend trading (Saturday and Sunday are closed anyway, but filter out)
   if(dt.day_of_week == 0 || dt.day_of_week == 6) return false;

   // Check if broker hours align with the specified session parameters
   if(dt.hour < Inp_StartHourGMT || dt.hour > Inp_EndHourGMT)
   {
      return false;
   }

   return true;
}
