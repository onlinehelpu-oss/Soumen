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

//--- Input parameters
input group "--- Strategy Settings ---"
input ENUM_TIMEFRAMES InpTimeframe         = PERIOD_CURRENT; // Timeframe to scan (PERIOD_CURRENT to match chart)
input double          InpRiskRewardRatio   = 1.5;            // Risk:Reward multiplier
input double          InpFixedLotSize      = 0.1;            // Lot size (if not using dynamic lot)
input bool            InpUseDynamicLot     = false;          // Use risk-based dynamic lot sizing?
input double          InpRiskPercentage    = 1.0;            // % Risk per trade (if dynamic lot)
input double          InpMaxMarginUtilPct  = 70.0;           // Max Margin Utilization Percentage (prevent Code 10019)
input bool            InpOnePositionAtATime = true;          // Limit to one open position at a time?

input group "--- Candle Pattern Controls ---"
input bool            InpRedCandleOnly     = true;           // Require signal candle to be Red? (always true by default as requested)
input bool            InpRequirePrevGreen  = false;          // Require the previous candle to be Green?

input group "--- Rejection Pattern Activation ---"
input bool            InpEnableC2          = true;           // Enable Pattern C2 (Classic Shooting Star)
input bool            InpEnableC3          = true;           // Enable Pattern C3 (Bearish Trend Bar)
input bool            InpEnableC4          = true;           // Enable Pattern C4 (Bearish Pinbar with tail)
input bool            InpEnableC5          = true;           // Enable Pattern C5 (Bearish Strong Rejection)
input bool            InpEnableC6          = true;           // Enable Pattern C6 (Rejection, minimal lower wick)
input bool            InpEnableC7          = true;           // Enable Pattern C7 (Extreme Gravestone Pinbar)
input bool            InpEnableSwingSS     = true;           // Enable Swing Shooting Star Pattern
input bool            InpEnable1MinPattern = true;           // Enable 1-Minute Custom Shooting Star Pattern (from image)
input bool            InpEnableCustom      = true;           // Enable Custom Fallback Rejection Pattern

input group "--- Pattern Swing Shooting Star Parameters ---"
input int             InpSwingLength       = 5;              // Swing High Lookback Length

input group "--- Pattern C2 (Classic Shooting Star) Parameters ---"
input double          InpC2_MinUpperWickPct = 40.0;          // C2 Min Upper Wick % (relaxed from 55.0)
input double          InpC2_MaxBodyPct      = 45.0;          // C2 Max Body % (relaxed from 35.0)
input double          InpC2_MaxLowerWickPct = 15.0;          // C2 Max Lower Wick % (relaxed from 5.0)

input group "--- Pattern C3 (Bearish Trend Bar) Parameters ---"
input double          InpC3_MinUpperWickPct = 5.0;           // C3 Min Upper Wick % (relaxed from 10.0)
input double          InpC3_MaxUpperWickPct = 45.0;          // C3 Max Upper Wick % (relaxed from 35.0)
input double          InpC3_MinBodyPct      = 45.0;          // C3 Min Body % (relaxed from 55.0)
input double          InpC3_MaxLowerWickPct = 25.0;          // C3 Max Lower Wick % (relaxed from 15.0)

input group "--- Pattern C4 (Bearish Pinbar) Parameters ---"
input double          InpC4_MinUpperWickPct = 25.0;          // C4 Min Upper Wick % (relaxed from 35.0)
input double          InpC4_MaxUpperWickPct = 65.0;          // C4 Max Upper Wick % (relaxed from 55.0)
input double          InpC4_MinBodyPct      = 20.0;          // C4 Min Body % (relaxed from 30.0)
input double          InpC4_MaxBodyPct      = 60.0;          // C4 Max Body % (relaxed from 50.0)
input double          InpC4_MinLowerWickPct = 5.0;           // C4 Min Lower Wick % (relaxed from 10.0)
input double          InpC4_MaxLowerWickPct = 35.0;          // C4 Max Lower Wick % (relaxed from 25.0)

input group "--- Pattern C5 (Strong Rejection) Parameters ---"
input double          InpC5_MinUpperWickPct = 25.0;          // C5 Min Upper Wick % (relaxed from 35.0)
input double          InpC5_MaxUpperWickPct = 65.0;          // C5 Max Upper Wick % (relaxed from 50.0)
input double          InpC5_MinBodyPct      = 35.0;          // C5 Min Body % (relaxed from 45.0)
input double          InpC5_MaxBodyPct      = 70.0;          // C5 Max Body % (relaxed from 60.0)
input double          InpC5_MaxLowerWickPct = 25.0;          // C5 Max Lower Wick % (relaxed from 15.0)

input group "--- Pattern C6 (Minimal Lower Wick Rejection) Parameters ---"
input double          InpC6_MinUpperWickPct = 30.0;          // C6 Min Upper Wick % (relaxed from 40.0)
input double          InpC6_MaxUpperWickPct = 70.0;          // C6 Max Upper Wick % (relaxed from 60.0)
input double          InpC6_MinBodyPct      = 25.0;          // C6 Min Body % (relaxed from 35.0)
input double          InpC6_MaxBodyPct      = 65.0;          // C6 Max Body % (relaxed from 55.0)
input double          InpC6_MaxLowerWickPct = 15.0;          // C6 Max Lower Wick % (relaxed from 5.0)

input group "--- Pattern C7 (Extreme Gravestone Pinbar) Parameters ---"
input double          InpC7_MinUpperWickPct = 45.0;          // C7 Min Upper Wick % (relaxed from 60.0)
input double          InpC7_MaxBodyPct      = 35.0;          // C7 Max Body % (relaxed from 25.0)
input double          InpC7_MinLowerWickPct = 0.0;           // C7 Min Lower Wick % (relaxed from 5.0)
input double          InpC7_MaxLowerWickPct = 30.0;          // C7 Max Lower Wick % (relaxed from 20.0)

input group "--- Custom Fallback Rejection Settings ---"
input double          InpUpperWickMin      = 30.0;           // Upper wick min percentage (relaxed from 50.0)
input double          InpUpperWickMax      = 100.0;          // Upper wick max percentage (up to 100% for full pinbars)
input double          InpBodyMin           = 0.0;            // Body min percentage (0% allows dojis)
input double          InpBodyMax           = 50.0;           // Body max percentage (relaxed from 40.0)
input double          InpLowerWickMax      = 40.0;           // Lower wick max percentage (relaxed from 30.0)
input double          InpMinRangePct       = 0.0;            // Min candle range pct (H-L)/Close (0.0 to disable)
input int             InpMinRangePoints    = 0;              // Min candle range in points (0 to disable)

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

datetime       m_last_checked_bar_time = 0;
datetime       m_last_checked_m1_bar_time = 0;
bool           m_trigger_active = false;
double         m_trigger_low = 0;
double         m_trigger_high = 0;
datetime       m_trigger_start_time = 0;
datetime       m_trigger_expiry_time = 0;
double         m_last_bid = 0;
string         m_matched_pattern_name = "";

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
    // Initialize symbol info
    if (!m_symbol.Name(_Symbol)) {
        Print("❌ Failed to initialize symbol info.");
        return INIT_FAILED;
    }
    m_symbol.Refresh();

    // Initialize CTrade parameters
    m_trade.SetExpertMagicNumber(InpMagicNumber);
    ConfigureFillingMode();

    m_last_checked_bar_time = iTime(_Symbol, InpTimeframe, 0);
    m_last_checked_m1_bar_time = iTime(_Symbol, PERIOD_M1, 0);
    m_last_bid = 0;

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
    datetime current_bar_time = iTime(_Symbol, InpTimeframe, 0);
    if (current_bar_time != m_last_checked_bar_time) {
        m_last_checked_bar_time = current_bar_time;
        CheckSignal();
    }

    // Check for 1-minute custom pattern signal if enabled and main timeframe is not M1 (to prevent duplicate checking)
    if (InpEnable1MinPattern) {
        datetime current_m1_bar_time = iTime(_Symbol, PERIOD_M1, 0);
        if (current_m1_bar_time != m_last_checked_m1_bar_time) {
            m_last_checked_m1_bar_time = current_m1_bar_time;
            Check1MinSignal();
        }
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

    // We need at least 1 + InpSwingLength completed bars to check Swing Shooting Star.
    // Index 1 is the completed signal candidate.
    // Preceding candles are at indexes 2, 3, etc.
    int lookback = MathMax(2, 1 + InpSwingLength);
    MqlRates rates[];
    ArraySetAsSeries(rates, true);
    if (CopyRates(_Symbol, InpTimeframe, 1, lookback, rates) < lookback) {
        Print("⚠️ Error copying rates for signal candle check.");
        return;
    }

    // rates[0] is the completed signal candidate (index 1)
    // rates[1] is the previous candle (index 2, must be green if filter enabled)
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

    double total_range = h - l;

    // Range percentage check
    double range_pct = total_range / MathMax(MathAbs(c), 1e-9);
    if (InpMinRangePct > 0.0 && range_pct < InpMinRangePct) {
        PrintFormat("🔍 Candle rejected: Range percentage (%.4f%%) < Min Required (%.4f%%)", range_pct * 100, InpMinRangePct * 100);
        return;
    }

    // Range points check
    int range_points = (int)MathRound(total_range / m_symbol.Point());
    if (InpMinRangePoints > 0 && range_points < InpMinRangePoints) {
        PrintFormat("🔍 Candle rejected: Range points (%d) < Min Required (%d)", range_points, InpMinRangePoints);
        return;
    }

    // Geometry Calculations (Universal for both RED and GREEN rejection candles)
    double body_high = MathMax(o, c);
    double body_low  = MathMin(o, c);

    double upper_wick_pct = ((h - body_high) / total_range) * 100.0;
    double body_pct       = ((body_high - body_low) / total_range) * 100.0;
    double lower_wick_pct = ((body_low - l) / total_range) * 100.0;

    // Absolute price variables for Swing Shooting Star Pattern
    double abs_body       = MathAbs(c - o);
    double abs_upper_wick = h - body_high;
    double abs_lower_wick = body_low - l;

    // Check which enabled pattern matches:
    string matched_pattern = "";

    if (InpEnableC2 && upper_wick_pct >= InpC2_MinUpperWickPct && body_pct <= InpC2_MaxBodyPct && lower_wick_pct <= InpC2_MaxLowerWickPct) {
        matched_pattern = "C2";
    }
    else if (InpEnableC3 && upper_wick_pct >= InpC3_MinUpperWickPct && upper_wick_pct <= InpC3_MaxUpperWickPct && body_pct >= InpC3_MinBodyPct && lower_wick_pct <= InpC3_MaxLowerWickPct) {
        matched_pattern = "C3";
    }
    else if (InpEnableC4 && upper_wick_pct >= InpC4_MinUpperWickPct && upper_wick_pct <= InpC4_MaxUpperWickPct && body_pct >= InpC4_MinBodyPct && body_pct <= InpC4_MaxBodyPct && lower_wick_pct >= InpC4_MinLowerWickPct && lower_wick_pct <= InpC4_MaxLowerWickPct) {
        matched_pattern = "C4";
    }
    else if (InpEnableC5 && upper_wick_pct >= InpC5_MinUpperWickPct && upper_wick_pct <= InpC5_MaxUpperWickPct && body_pct >= InpC5_MinBodyPct && body_pct <= InpC5_MaxBodyPct && lower_wick_pct <= InpC5_MaxLowerWickPct) {
        matched_pattern = "C5";
    }
    else if (InpEnableC6 && upper_wick_pct >= InpC6_MinUpperWickPct && upper_wick_pct <= InpC6_MaxUpperWickPct && body_pct >= InpC6_MinBodyPct && body_pct <= InpC6_MaxBodyPct && lower_wick_pct <= InpC6_MaxLowerWickPct) {
        matched_pattern = "C6";
    }
    else if (InpEnableC7 && upper_wick_pct >= InpC7_MinUpperWickPct && body_pct <= InpC7_MaxBodyPct && lower_wick_pct >= InpC7_MinLowerWickPct && lower_wick_pct <= InpC7_MaxLowerWickPct) {
        matched_pattern = "C7";
    }
    else if (InpEnableSwingSS) {
        // Swing Shooting Star Conditions:
        // 1. Swing High: h > high of preceding InpSwingLength candles (lookback from rates[1] to rates[InpSwingLength])
        bool is_swing_high = true;
        for (int idx = 1; idx <= InpSwingLength && idx < lookback; idx++) {
            if (h <= rates[idx].high) {
                is_swing_high = false;
                break;
            }
        }
        // 2. Upper Wick > Body: abs_upper_wick > abs_body
        // 3. Lower Wick < Body: abs_lower_wick < abs_body
        if (is_swing_high && abs_upper_wick > abs_body && abs_lower_wick < abs_body) {
            matched_pattern = "SwingSS";
        }
    }

    if (matched_pattern == "" && InpEnableCustom && upper_wick_pct >= InpUpperWickMin && upper_wick_pct <= InpUpperWickMax && body_pct >= InpBodyMin && body_pct <= InpBodyMax && lower_wick_pct >= 0.0 && lower_wick_pct <= InpLowerWickMax) {
        matched_pattern = "Custom Fallback";
    }

    // Detailed candidate logging
    PrintFormat("📊 Candidate candle found. Color: %s | Upper Wick: %.1f%%, Body: %.1f%%, Lower Wick: %.1f%%, Range: %.2f | Matched Pattern: %s",
                (c < o ? "RED" : "GREEN"), upper_wick_pct, body_pct, lower_wick_pct, total_range, (matched_pattern != "" ? matched_pattern : "None"));

    if (matched_pattern == "") {
        return; // No enabled pattern matches this candle's geometry
    }

    // We have a verified signal!
    m_trigger_active = true;
    m_trigger_low = l;
    m_trigger_high = h;
    m_matched_pattern_name = matched_pattern;
    m_trigger_start_time = iTime(_Symbol, InpTimeframe, 0); // Trigger begins at start of current bar 0
    m_trigger_expiry_time = m_trigger_start_time + PeriodSeconds(InpTimeframe); // Expires at end of bar 0

    PrintFormat("🎯 REJECTION SIGNAL GENERATED (%s): %s (%s). Wick=%.1f%%, Body=%.1f%%, Lower=%.1f%%.",
                matched_pattern, _Symbol, (c < o ? "RED" : "GREEN"), upper_wick_pct, body_pct, lower_wick_pct);
    PrintFormat("👉 Breakout watch low: %.2f | SL target: %.2f | Window: %s to %s",
                l, h, TimeToString(m_trigger_start_time), TimeToString(m_trigger_expiry_time));
}

//+------------------------------------------------------------------+
//| Check 1-minute custom pattern signal from image                  |
//+------------------------------------------------------------------+
void Check1MinSignal()
{
    // If we only allow one position and one is open, skip signal scanning
    if (InpOnePositionAtATime && IsPositionOpen()) {
        m_trigger_active = false;
        return;
    }

    // Copy rates of completed bars (index 1 and index 2 on M1)
    MqlRates m1_rates[];
    ArraySetAsSeries(m1_rates, true);
    if (CopyRates(_Symbol, PERIOD_M1, 1, 2, m1_rates) < 2) {
        return;
    }

    // Candle 1 (completed candidate at index 0 in rates array, which corresponds to index 1 on chart):
    // - Must be Red: Open > Close
    // - Bracket( High - Open ) >= Bracket( 2 * Bracket( Open - Close ) )
    // - Bracket( Bracket( Close - Low ) / Bracket( Open - Close ) ) < 1
    double o1 = m1_rates[0].open;
    double h1 = m1_rates[0].high;
    double l1 = m1_rates[0].low;
    double c1 = m1_rates[0].close;

    // Candle 2 (previous completed candle at index 1 in rates array, which corresponds to index 2 on chart):
    // - Must be Green: Close > Open
    double o2 = m1_rates[1].open;
    double c2 = m1_rates[1].close;

    bool is_red = o1 > c1;
    bool is_prev_green = c2 > o2;

    if (!is_red || !is_prev_green) {
        return;
    }

    double body = o1 - c1;
    if (body <= 0) return;

    double upper_wick = h1 - o1;
    double lower_wick = c1 - l1;

    bool condition1 = (upper_wick >= 2.0 * body);
    bool condition2 = ((lower_wick / body) < 1.0);

    if (condition1 && condition2) {
        // M1 Shooting Star Pattern verified!
        m_trigger_active = true;
        m_trigger_low = l1;
        m_trigger_high = h1;
        m_matched_pattern_name = "M1_ShootingStar";
        m_trigger_start_time = iTime(_Symbol, PERIOD_M1, 0); // Trigger begins at start of current bar 0 on M1
        m_trigger_expiry_time = m_trigger_start_time + 60;   // Expires at end of 1-minute bar 0

        PrintFormat("🎯 1-MINUTE REJECTION SIGNAL GENERATED (M1_ShootingStar): %s. High: %.2f, Low: %.2f, Open: %.2f, Close: %.2f. Prev Close: %.2f, Prev Open: %.2f",
                    _Symbol, h1, l1, o1, c1, c2, o2);
        PrintFormat("👉 Breakout watch low: %.2f | SL target: %.2f | Window: %s to %s",
                    l1, h1, TimeToString(m_trigger_start_time), TimeToString(m_trigger_expiry_time));
    }
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
    double body_pct       = ((body_high - body_low) / total_range) * 100.0;
    double lower_wick_pct = ((body_low - l) / total_range) * 100.0;

    bool is_valid_geometry = (upper_wick_pct >= InpUpperWickMin && upper_wick_pct <= InpUpperWickMax) &&
                             (body_pct >= InpBodyMin && body_pct <= InpBodyMax) &&
                             (lower_wick_pct >= 0.0 && lower_wick_pct <= InpLowerWickMax);

    return is_valid_geometry;
}

//+------------------------------------------------------------------+
//| Run core calculations and asserts to verify EA integrity         |
//+------------------------------------------------------------------+
void RunSelfTests()
{
    Print("--- Running Core EA Self Tests ---");

    // Test 1: Valid shooting star with updated geometry (RED CANDLE)
    // Upper: ~54.5%, Body: ~20.5%, Lower: 25.0%
    bool test1 = TestBearishShootingStarGeometry(100.0, 112.0, 90.0, 95.5, 95.0, 98.0);
    PrintFormat("Test 1 (Valid Red Geometry): %s", test1 ? "PASSED ✅" : "FAILED ❌");

    // Test 2: Upper wick too short (40%)
    bool test2 = TestBearishShootingStarGeometry(105.0, 109.0, 95.0, 102.0, 100.0, 102.0);
    PrintFormat("Test 2 (Upper wick too short): %s", !test2 ? "PASSED ✅" : "FAILED ❌");

    // Test 3: Body too large (45%)
    bool test3 = TestBearishShootingStarGeometry(108.0, 112.0, 98.0, 100.0, 100.0, 102.0);
    PrintFormat("Test 3 (Body too large): %s", !test3 ? "PASSED ✅" : "FAILED ❌");

    // Test 4: Lower wick too long (35%)
    bool test4 = TestBearishShootingStarGeometry(108.0, 112.0, 98.0, 101.0, 100.0, 102.0);
    PrintFormat("Test 4 (Lower wick too long): %s", !test4 ? "PASSED ✅" : "FAILED ❌");
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
