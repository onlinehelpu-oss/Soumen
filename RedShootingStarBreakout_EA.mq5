//+------------------------------------------------------------------+
//|                                   RedShootingStarBreakout_EA.mq5 |
//|                                                            Jules |
//|                     Red-ShootingStar / Red-Pinbar Breakout EA    |
//+------------------------------------------------------------------+
#property copyright "Jules"
#property link      "https://github.com"
#property version   "1.04"
#property strict

//--- Include Standard Libraries
#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\PositionInfo.mqh>

//--- Custom Timeframe Enum
enum ENUM_CUSTOM_TIMEFRAME
{
    TF_CURRENT = PERIOD_CURRENT, // Current Timeframe
    TF_M1      = PERIOD_M1,      // 1 Minute
    TF_M3      = PERIOD_M3,      // 3 Minutes
    TF_M5      = PERIOD_M5,      // 5 Minutes
    TF_M15     = PERIOD_M15,     // 15 Minutes
    TF_M30     = PERIOD_M30,     // 30 Minutes
    TF_H1      = PERIOD_H1,      // 1 Hour
    TF_D1      = PERIOD_D1       // 1 Day
};

//--- Input parameters
input group "--- Strategy Settings ---"
input ENUM_CUSTOM_TIMEFRAME InpTimeframe   = TF_CURRENT;     // Timeframe to scan
input double          InpRiskRewardRatio   = 1.5;            // Risk:Reward multiplier
input double          InpFixedLotSize      = 0.01;           // Lot size (if not using dynamic lot)
input double          InpMinLotSizeOverride = 0.01;          // Minimum Lot Size Override (0.01 standard minimum for Gold on XM)
input bool            InpUseDynamicLot     = false;          // Use risk-based dynamic lot sizing?
input double          InpRiskPercentage    = 1.0;            // % Risk per trade (if dynamic lot)
input double          InpMaxMarginUtilPct  = 70.0;           // Max Margin Utilization Percentage (prevent Code 10019)
input bool            InpOnePositionAtATime = true;          // Limit to one open position at a time?
input int             InpMinCandleRangePoints = 50;          // Min candle range in points to ignore tiny candles (0 to disable)

input group "--- EMA Trend Filter Settings ---"
input bool            InpUseEMAFilter      = true;           // Use EMA Trend Filter? (Signal Close < EMA)
input int             InpEMAPeriod         = 34;             // EMA Period (9, 15, 21, 34, 50, 200, etc.)
input ENUM_MA_METHOD  InpEMAMethod         = MODE_EMA;       // EMA Smoothing Method
input ENUM_APPLIED_PRICE InpEMAAppliedPrice = PRICE_CLOSE;   // EMA Applied Price

input group "--- EMA 15/9 Cross Trend Filter Settings ---"
input bool            InpUseEMACrossFilter = true;           // Use EMA 15/9 Cross & Touch Filter?
input int             InpEMA9Period        = 9;              // EMA 9 Period
input int             InpEMA15Period       = 15;             // EMA 15 Period
input ENUM_MA_METHOD  InpEMACrossMethod    = MODE_EMA;       // EMA Cross Smoothing Method
input ENUM_APPLIED_PRICE InpEMACrossAppliedPrice = PRICE_CLOSE; // EMA Cross Applied Price

input group "--- Candle Pattern Controls ---"
input bool            InpRedCandleOnly     = true;           // Require signal candle to be Red?
input bool            InpRequirePrevGreen  = false;          // Require the previous candle to be Green?
input double          InpMinUpperWickPct   = 50.0;           // Min Upper Wick % (e.g. 50.0%)
input double          InpMaxLowerWickPoints = 0.0;           // Max allowed lower wick in points (0.0 for strict absolute flat bottom)

input group "--- Breakout & Execution Settings ---"
input bool            InpUseTimeFilters    = false;          // Enable entry cutoff time filters?
input double          InpEntryBufferPoints = 5.0;            // Entry buffer below signal low in points (0.05 USD on GOLD)
input string          InpEntryCutoffTime   = "15:00";        // Cutoff time for entries (Broker HH:MM)
input string          InpForceExitTime     = "15:09";        // Time to force exit all open positions (Broker HH:MM)
input bool            InpForceExitDaily    = false;          // Force exit daily at cutoff time?
input int             InpMagicNumber       = 20260225;       // Magic Number
input string          InpTradeTag          = "ShootRej";     // Order Comment Tag

//--- State Variables
CTrade         m_trade;
CSymbolInfo    m_symbol;
CPositionInfo  m_position;

ENUM_TIMEFRAMES m_timeframe = PERIOD_CURRENT;
datetime       m_last_checked_bar_time = 0;
bool           m_trigger_active = false;
bool           m_had_position_open = false;
datetime       m_last_trade_closed_time = 0;
double         m_trigger_low = 0;
double         m_trigger_high = 0;
datetime       m_trigger_start_time = 0;
datetime       m_trigger_expiry_time = 0;
double         m_last_bid = 0;
string         m_matched_pattern_name = "";

//--- Indicator Handles
int            m_ema_handle = INVALID_HANDLE;
int            m_ema9_handle = INVALID_HANDLE;
int            m_ema15_handle = INVALID_HANDLE;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
    // Map custom timeframe to standard MQL5 timeframe
    m_timeframe = (ENUM_TIMEFRAMES)InpTimeframe;

    // Initialize symbol info
    if (!m_symbol.Name(_Symbol)) {
        Print("❌ Failed to initialize symbol info.");
        return INIT_FAILED;
    }
    m_symbol.Refresh();

    // Initialize CTrade parameters
    m_trade.SetExpertMagicNumber(InpMagicNumber);
    ConfigureFillingMode();

    m_last_checked_bar_time = iTime(_Symbol, m_timeframe, 0);
    m_last_bid = 0;

    // Create EMA indicator handle if filter is enabled
    if (InpUseEMAFilter) {
        m_ema_handle = iMA(_Symbol, m_timeframe, InpEMAPeriod, 0, InpEMAMethod, InpEMAAppliedPrice);
        if (m_ema_handle == INVALID_HANDLE) {
            Print("❌ Failed to create EMA handle for main timeframe.");
            return INIT_FAILED;
        }
    }

    // Create EMA 9 and EMA 15 indicator handles if filter is enabled
    if (InpUseEMACrossFilter) {
        m_ema9_handle = iMA(_Symbol, m_timeframe, InpEMA9Period, 0, InpEMACrossMethod, InpEMACrossAppliedPrice);
        if (m_ema9_handle == INVALID_HANDLE) {
            Print("❌ Failed to create EMA 9 handle.");
            return INIT_FAILED;
        }
        m_ema15_handle = iMA(_Symbol, m_timeframe, InpEMA15Period, 0, InpEMACrossMethod, InpEMACrossAppliedPrice);
        if (m_ema15_handle == INVALID_HANDLE) {
            Print("❌ Failed to create EMA 15 handle.");
            return INIT_FAILED;
        }
    }

    // Run core tests to verify calculation logic matches expectations
    RunSelfTests();

    PrintFormat("✅ Expert Advisor Initialized successfully for %s on timeframe %s.", _Symbol, EnumToString(InpTimeframe));
    return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    // Release indicator handle
    if (m_ema_handle != INVALID_HANDLE) {
        IndicatorRelease(m_ema_handle);
        m_ema_handle = INVALID_HANDLE;
    }
    if (m_ema9_handle != INVALID_HANDLE) {
        IndicatorRelease(m_ema9_handle);
        m_ema9_handle = INVALID_HANDLE;
    }
    if (m_ema15_handle != INVALID_HANDLE) {
        IndicatorRelease(m_ema15_handle);
        m_ema15_handle = INVALID_HANDLE;
    }
    Comment(""); // Clear chart comments
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
    // Refresh symbol prices
    if (!m_symbol.RefreshRates()) {
        return;
    }

    double current_bid = m_symbol.Bid();
    if (m_last_bid == 0) m_last_bid = current_bid;

    // Check for daily force exit
    if (InpForceExitDaily && IsPastTime(InpForceExitTime)) {
        if (IsPositionOpen()) {
            PrintFormat("⏰ Force Exit Time %s reached. Closing all open positions.", InpForceExitTime);
            CloseAllPositions();
        }
        m_trigger_active = false;
        UpdateDashboard();
        m_last_bid = current_bid;
        return;
    }

    // Check for new bar completion to scan for main timeframe signal
    datetime current_bar_time = iTime(_Symbol, m_timeframe, 0);
    if (current_bar_time != m_last_checked_bar_time) {
        m_last_checked_bar_time = current_bar_time;
        CheckSignal();
    }

    // Track breakout if trigger is active
    if (m_trigger_active) {
        datetime current_time = TimeCurrent();

        // Expiry check
        if (current_time >= m_trigger_expiry_time) {
            PrintFormat("⏳ Trigger expired for %s. Next candle completed without breakout.", _Symbol);
            m_trigger_active = false;
            UpdateDashboard();
            m_last_bid = current_bid;
            return;
        }

        // Only active inside the breakout candle window
        if (current_time >= m_trigger_start_time) {
            if (InpUseTimeFilters && IsPastTime(InpEntryCutoffTime)) {
                PrintFormat("⏰ Cutoff time %s passed. Discarding trigger.", InpEntryCutoffTime);
                m_trigger_active = false;
                UpdateDashboard();
                m_last_bid = current_bid;
                return;
            }

            if (InpOnePositionAtATime && IsPositionOpen()) {
                m_trigger_active = false;
                UpdateDashboard();
                m_last_bid = current_bid;
                return;
            }

            // Calculate entry threshold
            double buffer = InpEntryBufferPoints * m_symbol.Point();
            double threshold = m_trigger_low - buffer;
            threshold = NormalizePrice(threshold);

            // First-touch crossing check
            if (m_last_bid >= threshold && current_bid < threshold) {
                PrintFormat("🔥 BREAKOUT DETECTED: Bid %.2f crossed below threshold %.2f (Signal Low: %.2f, Buffer: %.2f) (Pattern: %s)",
                            current_bid, threshold, m_trigger_low, buffer, m_matched_pattern_name);

                ExecuteShortEntry(threshold);
                m_trigger_active = false; // Set to false immediately to prevent duplicate fills (Anti-Race Lock pattern)
            }
        }
    }

    // Perform tick backup check for standard risk SL/TP (safety net)
    CheckActivePositionsRisk();

    // Detect position close to track the exact close timestamp
    bool currently_open = IsPositionOpen();
    if (m_had_position_open && !currently_open) {
        m_last_trade_closed_time = TimeCurrent();
        PrintFormat("ℹ️ EA Position closed at %s. 5-minute cooldown active until %s.",
                    TimeToString(m_last_trade_closed_time),
                    TimeToString(m_last_trade_closed_time + 300));
    }
    m_had_position_open = currently_open;

    // Update visual chart dashboard
    UpdateDashboard();

    m_last_bid = current_bid;
}

//+------------------------------------------------------------------+
//| Check finished bar for Bearish Shooting Star / Pinbar            |
//+------------------------------------------------------------------+
void CheckSignal()
{
    // If we only allow one position and one is open, skip signal scanning
    if (InpOnePositionAtATime && IsPositionOpen()) {
        m_trigger_active = false;
        return;
    }

    // We need 2 completed bars.
    // Index 1 is the completed signal candidate.
    // Preceding candle is at index 2 (used to check if previous candle was green if filter is enabled).
    int lookback = 2;
    MqlRates rates[];
    ArraySetAsSeries(rates, true);
    if (CopyRates(_Symbol, m_timeframe, 1, lookback, rates) < lookback) {
        Print("⚠️ Error copying rates for signal candle check.");
        return;
    }

    // rates[0] is the completed signal candidate (index 1)
    // rates[1] is the previous candle (index 2)
    double o = rates[0].open;
    double h = rates[0].high;
    double l = rates[0].low;
    double c = rates[0].close;

    double prev_o = rates[1].open;
    double prev_c = rates[1].close;

    // Initial validation
    if (InpRedCandleOnly && c >= o) return;        // Candidate must be RED if filter enabled
    if (InpRequirePrevGreen && prev_c <= prev_o) return; // Previous candle must be GREEN if filter enabled
    if (c == 0 || h <= l) return;

    // EMA Filters evaluation
    bool ema_34_valid = true;
    if (InpUseEMAFilter && m_ema_handle != INVALID_HANDLE) {
        ema_34_valid = false;
        double ema_val[1];
        if (CopyBuffer(m_ema_handle, 0, 1, 1, ema_val) >= 1) {
            if (c < ema_val[0] && h > ema_val[0]) {
                ema_34_valid = true;
            }
        }
    }

    bool ema_cross_valid = true;
    if (InpUseEMACrossFilter && m_ema9_handle != INVALID_HANDLE && m_ema15_handle != INVALID_HANDLE) {
        ema_cross_valid = false;
        double ema9_val[1];
        double ema15_val[1];
        if (CopyBuffer(m_ema9_handle, 0, 1, 1, ema9_val) >= 1 && CopyBuffer(m_ema15_handle, 0, 1, 1, ema15_val) >= 1) {
            bool trend_ok = (ema15_val[0] > ema9_val[0]);
            bool touch_ok = (h > ema9_val[0] || h > ema15_val[0]);
            bool close_ok = (c < ema9_val[0] && c < ema15_val[0]);

            if (trend_ok && touch_ok && close_ok) {
                ema_cross_valid = true;
            }
        }
    }

    // If EMA filters are enabled, at least one of the active filters must be valid
    if (InpUseEMAFilter || InpUseEMACrossFilter) {
        bool signal_valid = false;
        if (InpUseEMAFilter && ema_34_valid) {
            signal_valid = true;
        }
        else if (InpUseEMACrossFilter && ema_cross_valid) {
            signal_valid = true;
        }

        if (!signal_valid) {
            double ema34 = 0, ema9 = 0, ema15 = 0;
            double temp_ema[1];
            if (m_ema_handle != INVALID_HANDLE && CopyBuffer(m_ema_handle, 0, 1, 1, temp_ema) >= 1) ema34 = temp_ema[0];
            if (m_ema9_handle != INVALID_HANDLE && CopyBuffer(m_ema9_handle, 0, 1, 1, temp_ema) >= 1) ema9 = temp_ema[0];
            if (m_ema15_handle != INVALID_HANDLE && CopyBuffer(m_ema15_handle, 0, 1, 1, temp_ema) >= 1) ema15 = temp_ema[0];

            PrintFormat("🔍 Candle rejected by EMA filters. Close: %.2f, High: %.2f | EMA34: %.2f, EMA9: %.2f, EMA15: %.2f",
                        c, h, ema34, ema9, ema15);
            return;
        }
    }

    double total_range = h - l;

    // Range points check to ignore tiny candle
    int range_points = (int)MathRound(total_range / m_symbol.Point());
    if (InpMinCandleRangePoints > 0 && range_points < InpMinCandleRangePoints) {
        PrintFormat("🔍 Candle rejected: Range points (%d) < Min Required (%d)", range_points, InpMinCandleRangePoints);
        return;
    }

    // Geometry Calculations
    double body_high = MathMax(o, c);
    double body_low  = MathMin(o, c);

    double upper_wick_pct = ((h - body_high) / total_range) * 100.0;
    double lower_wick_pct = ((body_low - l) / total_range) * 100.0;

    // Fine-tuned lower wick points calculation
    double point = (m_symbol.Point() > 0) ? m_symbol.Point() : 0.01;
    double lower_wick_points = (body_low - l) / point;

    // simplified check: long upper wick above minimum 50% (customizable via InpMinUpperWickPct) and zero lower wick
    bool upper_wick_ok = (upper_wick_pct >= InpMinUpperWickPct);
    bool zero_lower_wick = (lower_wick_points <= InpMaxLowerWickPoints + 0.0001);

    string matched_pattern = "";
    if (upper_wick_ok && zero_lower_wick) {
        if (InpUseEMAFilter && ema_34_valid) {
            matched_pattern = "LongUpperWick_EMA34";
        }
        else if (InpUseEMACrossFilter && ema_cross_valid) {
            matched_pattern = "LongUpperWick_EMACross";
        }
        else {
            matched_pattern = "LongUpperWickZeroLowerWick";
        }
    }

    // Detailed candidate logging
    PrintFormat("📊 Candidate candle found. Color: %s | Upper Wick: %.1f%%, Lower Wick: %.1f%%, Range: %.2f | Matched Pattern: %s",
                (c < o ? "RED" : "GREEN"), upper_wick_pct, lower_wick_pct, total_range, (matched_pattern != "" ? matched_pattern : "None"));

    if (matched_pattern == "") {
        return; // Pattern does not match
    }

    // We have a verified signal!
    m_trigger_active = true;
    m_trigger_low = l;
    m_trigger_high = h;
    m_matched_pattern_name = matched_pattern;
    m_trigger_start_time = iTime(_Symbol, m_timeframe, 0); // Trigger begins at start of current bar 0
    m_trigger_expiry_time = m_trigger_start_time + PeriodSeconds(m_timeframe); // Expires at end of bar 0

    PrintFormat("🎯 REJECTION SIGNAL GENERATED (%s): %s (%s). Upper Wick=%.1f%%, Lower Wick=%.1f%%.",
                matched_pattern, _Symbol, (c < o ? "RED" : "GREEN"), upper_wick_pct, lower_wick_pct);
    PrintFormat("👉 Breakout watch low: %.2f | SL target: %.2f | Window: %s to %s",
                l, h, TimeToString(m_trigger_start_time), TimeToString(m_trigger_expiry_time));
}


//+------------------------------------------------------------------+
//| Execute Short Entry (Market Sell)                                |
//+------------------------------------------------------------------+
void ExecuteShortEntry(double trigger_price)
{
    // Strict Guard: If one position at a time is active and a position is open, cancel entry
    if (InpOnePositionAtATime && IsPositionOpen()) {
        Print("⚠️ Position is already open and InpOnePositionAtATime is active. Skipping trade.");
        return;
    }

    // 5-Minute Cooldown Guard: Check if less than 5 minutes (300 seconds) has passed since the last trade was closed
    if (m_last_trade_closed_time > 0) {
        datetime current_time = TimeCurrent();
        long elapsed = current_time - m_last_trade_closed_time;
        if (elapsed < 300) {
            PrintFormat("⚠️ Trade execution skipped. Cooldown in effect. Only %d seconds elapsed of the required 300 since last position close.", elapsed);
            return;
        }
    }

    double entry_price = NormalizePrice(m_symbol.Bid());
    double sl_price = m_trigger_high;
    double risk = sl_price - entry_price;

    if (risk <= 0) {
        Print("⚠️ Invalid risk calculation (SL <= Entry). Skipping trade.");
        return;
    }

    double tp_price = entry_price - (InpRiskRewardRatio * risk);
    if (tp_price <= 0) {
        PrintFormat("⚠️ TP %.2f is less than or equal to zero. Skipping trade.", tp_price);
        return;
    }

    sl_price = NormalizePrice(sl_price);
    tp_price = NormalizePrice(tp_price);

    // Adjust Stop Levels for Broker Minimum Stop Level requirement
    double stop_level = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * m_symbol.Point();
    double current_bid = m_symbol.Bid();

    if (MathAbs(sl_price - current_bid) < stop_level) {
        sl_price = NormalizePrice(current_bid + stop_level);
    }
    if (MathAbs(current_bid - tp_price) < stop_level) {
        tp_price = NormalizePrice(current_bid - stop_level);
    }

    // Lot Size calculation
    double lots = InpFixedLotSize;
    if (InpUseDynamicLot) {
        lots = CalculateDynamicLotSize(risk);
    }

    lots = NormalizeLotSize(lots);
    lots = PerformMarginCheck(lots, entry_price);

    if (lots <= 0) {
        Print("⚠️ Lot size is 0 after limits check. Cancelling trade entry.");
        return;
    }

    // Order execution
    ResetLastError();
    string comment_tag = InpTradeTag + "_" + m_matched_pattern_name;
    if (m_trade.Sell(lots, _Symbol, entry_price, sl_price, tp_price, comment_tag)) {
        ulong ticket = m_trade.ResultOrder();
        if (ticket > 0 || m_trade.ResultRetcode() == 10009) {
            PrintFormat("✅ TRADE SUCCESSFUL: Short position entered on %s. Lots: %.2f, Entry: %.2f, SL: %.2f, TP: %.2f (Pattern: %s)",
                        _Symbol, lots, entry_price, sl_price, tp_price, m_matched_pattern_name);
        } else {
            PrintFormat("❌ Order rejected. Retcode: %d, Description: %s",
                        m_trade.ResultRetcode(), m_trade.ResultComment());
        }
    } else {
        PrintFormat("❌ Order execution error: %d", GetLastError());
    }
}

//+------------------------------------------------------------------+
//| Calculate Dynamic Lot size based on Risk amount                  |
//+------------------------------------------------------------------+
double CalculateDynamicLotSize(double risk)
{
    double balance = AccountInfoDouble(ACCOUNT_BALANCE);
    double risk_amount = balance * (InpRiskPercentage / 100.0);
    double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
    double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);

    if (risk_amount <= 0 || tick_value <= 0 || tick_size <= 0 || risk <= 0) {
        return InpFixedLotSize;
    }

    double risk_value_per_lot = (risk / tick_size) * tick_value;
    if (risk_value_per_lot <= 0) return InpFixedLotSize;

    return risk_amount / risk_value_per_lot;
}

//+------------------------------------------------------------------+
//| Normalize Lot Size to fit broker step & min/max bounds          |
//+------------------------------------------------------------------+
double NormalizeLotSize(double lots)
{
    double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
    if (InpMinLotSizeOverride > 0.0) {
        min_lot = InpMinLotSizeOverride;
    }
    double max_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
    double lot_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

    if (lots < min_lot) lots = min_lot;
    if (lots > max_lot) lots = max_lot;

    if (lot_step > 0) {
        lots = MathRound(lots / lot_step) * lot_step;
    }

    return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
//| Check free margin and scale down lots if needed                  |
//+------------------------------------------------------------------+
double PerformMarginCheck(double lots, double entry_price)
{
    double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
    double max_allowed_margin = free_margin * (InpMaxMarginUtilPct / 100.0);

    double required_margin = 0;
    if (!OrderCalcMargin(ORDER_TYPE_SELL, _Symbol, lots, entry_price, required_margin)) {
        return lots;
    }

    if (required_margin <= max_allowed_margin) {
        return lots;
    }

    double scale_factor = max_allowed_margin / required_margin;
    double scaled_lots = lots * scale_factor;
    scaled_lots = NormalizeLotSize(scaled_lots);

    PrintFormat("⚠️ Free Margin limit reached. Required: %.2f, Max Allowed: %.2f. Scaled down lots from %.2f to %.2f.",
                required_margin, max_allowed_margin, lots, scaled_lots);

    return scaled_lots;
}

//+------------------------------------------------------------------+
//| Check active positions risk limits tick-by-tick (safety net)     |
//+------------------------------------------------------------------+
void CheckActivePositionsRisk()
{
    double current_bid = m_symbol.Bid();

    for (int i = PositionsTotal() - 1; i >= 0; i--) {
        ulong ticket = PositionGetTicket(i);
        if (ticket > 0) {
            if (PositionGetString(POSITION_SYMBOL) == _Symbol) {
                long magic = PositionGetInteger(POSITION_MAGIC);
                if (magic != InpMagicNumber) continue;

                long type = PositionGetInteger(POSITION_TYPE);
                if (type != POSITION_TYPE_SELL) continue;

                double sl = PositionGetDouble(POSITION_SL);
                double tp = PositionGetDouble(POSITION_TP);

                // If price violates SL or TP, execute immediate close
                if (sl > 0 && current_bid >= sl) {
                    PrintFormat("⚡ Tick protection triggered: SL hit for ticket %I64u at %.2f (SL: %.2f). Closing position.", ticket, current_bid, sl);
                    m_trade.PositionClose(ticket);
                }
                else if (tp > 0 && current_bid <= tp) {
                    PrintFormat("⚡ Tick protection triggered: TP hit for ticket %I64u at %.2f (TP: %.2f). Closing position.", ticket, current_bid, tp);
                    m_trade.PositionClose(ticket);
                }
            }
        }
    }
}

//+------------------------------------------------------------------+
//| Helper to parse and compare broker time string                   |
//+------------------------------------------------------------------+
bool IsPastTime(string time_str)
{
    datetime current_time = TimeCurrent();
    MqlDateTime dt_struct;
    TimeToStruct(current_time, dt_struct);

    string parts[];
    if (StringSplit(time_str, ':', parts) < 2) return false;

    int target_hour = (int)StringToInteger(parts[0]);
    int target_minute = (int)StringToInteger(parts[1]);

    if (dt_struct.hour > target_hour) return true;
    if (dt_struct.hour == target_hour && dt_struct.min >= target_minute) return true;

    return false;
}

//+------------------------------------------------------------------+
//| Helper to check if symbol has active EA positions open           |
//+------------------------------------------------------------------+
bool IsPositionOpen()
{
    for (int i = PositionsTotal() - 1; i >= 0; i--) {
        ulong ticket = PositionGetTicket(i);
        if (ticket > 0) {
            if (PositionGetString(POSITION_SYMBOL) == _Symbol) {
                long magic = PositionGetInteger(POSITION_MAGIC);
                if (magic == InpMagicNumber) return true;
            }
        }
    }
    return false;
}

//+------------------------------------------------------------------+
//| Close all open positions managed by this EA                      |
//+------------------------------------------------------------------+
void CloseAllPositions()
{
    for (int i = PositionsTotal() - 1; i >= 0; i--) {
        ulong ticket = PositionGetTicket(i);
        if (ticket > 0) {
            if (PositionGetString(POSITION_SYMBOL) == _Symbol) {
                long magic = PositionGetInteger(POSITION_MAGIC);
                if (magic == InpMagicNumber) {
                    m_trade.PositionClose(ticket);
                    PrintFormat("✅ Closed Position Ticket: %I64u.", ticket);
                }
            }
        }
    }
}

//+------------------------------------------------------------------+
//| Normalize price to symbol tick size                              |
//+------------------------------------------------------------------+
double NormalizePrice(double price)
{
    double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
    if (tick_size <= 0) return NormalizeDouble(price, _Digits);
    return NormalizeDouble(MathRound(price / tick_size) * tick_size, _Digits);
}

//+------------------------------------------------------------------+
//| Configure standard filling modes based on broker configurations  |
//+------------------------------------------------------------------+
void ConfigureFillingMode()
{
    uint filling = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
    if ((filling & SYMBOL_FILLING_FOK) != 0) {
        m_trade.SetTypeFilling(ORDER_FILLING_FOK);
    }
    else if ((filling & SYMBOL_FILLING_IOC) != 0) {
        m_trade.SetTypeFilling(ORDER_FILLING_IOC);
    }
    else {
        m_trade.SetTypeFilling(ORDER_FILLING_RETURN);
    }
}

//+------------------------------------------------------------------+
//| Self test Bearish Shooting Star Geometry                         |
//+------------------------------------------------------------------+
bool TestBearishShootingStarGeometry(double o, double h, double l, double c, double prev_o, double prev_c)
{
    double total_range = h - l;
    if (total_range <= 0) return false;

    double body_high = MathMax(o, c);
    double body_low  = MathMin(o, c);

    double upper_wick_pct = ((h - body_high) / total_range) * 100.0;

    double point = (m_symbol.Point() > 0) ? m_symbol.Point() : 0.01;
    double lower_wick_points = (body_low - l) / point;

    bool is_valid_geometry = (upper_wick_pct >= InpMinUpperWickPct) && (lower_wick_points <= InpMaxLowerWickPoints + 0.0001);

    return is_valid_geometry;
}

//+------------------------------------------------------------------+
//| Run core calculations and asserts to verify EA integrity         |
//+------------------------------------------------------------------+
void RunSelfTests()
{
    Print("--- Running Core EA Self Tests ---");

    // Test 1: Valid: Upper wick = 60%, Low = Body Low (No lower wick)
    bool test1 = TestBearishShootingStarGeometry(100.0, 115.0, 90.0, 90.0, 95.0, 98.0);
    PrintFormat("Test 1 (Valid: Upper wick = 60%%, No lower wick): %s", test1 ? "PASSED ✅" : "FAILED ❌");

    // Test 2: Upper wick too short (23%)
    bool test2 = TestBearishShootingStarGeometry(100.0, 103.0, 90.0, 90.0, 95.0, 98.0);
    PrintFormat("Test 2 (Upper wick too short): %s", !test2 ? "PASSED ✅" : "FAILED ❌");

    // Test 3: Upper wick ok but has lower wick (l < body_low)
    bool test3 = TestBearishShootingStarGeometry(100.0, 115.0, 85.0, 90.0, 95.0, 98.0);
    PrintFormat("Test 3 (Upper wick ok but has lower wick): %s", !test3 ? "PASSED ✅" : "FAILED ❌");
}

//+------------------------------------------------------------------+
//| Update on-chart visual dashboard information                     |
//+------------------------------------------------------------------+
void UpdateDashboard()
{
    // Skip visual updates during fast non-visual strategy tester execution
    if (MQLInfoInteger(MQL_TESTER) && !MQLInfoInteger(MQL_VISUAL_MODE)) {
        return;
    }

    string comment = "==================================================\n" +
                     "  REJECTION BREAKOUT EA (XM GOLD) \n" +
                     "==================================================\n" +
                     "  Symbol: " + _Symbol + "\n" +
                     "  Timeframe: " + EnumToString(InpTimeframe) + "\n" +
                     "  Red Candle Only: " + (InpRedCandleOnly ? "YES" : "NO") + "\n" +
                     "  Prev Green Required: " + (InpRequirePrevGreen ? "YES" : "NO") + "\n" +
                     "  EMA Filter: " + (InpUseEMAFilter ? "YES (Period " + IntegerToString(InpEMAPeriod) + ")" : "NO") + "\n" +
                     "  Risk:Reward Ratio: " + DoubleToString(InpRiskRewardRatio, 2) + "\n" +
                     "  Lot Size: " + (InpUseDynamicLot ? "Dynamic (" + DoubleToString(InpRiskPercentage, 2) + "%)" : "Fixed (" + DoubleToString(InpFixedLotSize, 2) + ")") + "\n" +
                     "--------------------------------------------------\n" +
                     "  Active Breakout Trigger: " + (m_trigger_active ? "YES" : "NO") + "\n";

    if (m_trigger_active) {
        comment += "  Trigger Candle Low: " + DoubleToString(m_trigger_low, _Digits) + "\n" +
                   "  Trigger Candle High (SL): " + DoubleToString(m_trigger_high, _Digits) + "\n" +
                   "  Matched Pattern: " + m_matched_pattern_name + "\n" +
                   "  Start Time: " + TimeToString(m_trigger_start_time, TIME_DATE|TIME_MINUTES) + "\n" +
                   "  Expiry Time: " + TimeToString(m_trigger_expiry_time, TIME_DATE|TIME_MINUTES) + "\n";
    }

    comment += "--------------------------------------------------\n" +
               "  Broker Time: " + TimeToString(TimeCurrent(), TIME_DATE|TIME_MINUTES|TIME_SECONDS) + "\n" +
               "  Account Balance: " + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + "\n" +
               "  Free Margin: " + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2) + "\n" +
               "==================================================";

    Comment(comment);
}
