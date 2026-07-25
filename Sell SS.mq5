//+------------------------------------------------------------------+
//|                                                      Sell SS.mq5 |
//|                                                            Jules |
//|                     Red-ShootingStar / Red-Pinbar Breakout EA    |
//+------------------------------------------------------------------+
#property copyright "Jules"
#property link      "https://github.com"
#property version   "1.02"
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

input group "--- Regime EMA Settings ---"
input bool            InpUseEMAFilter      = false;          // Filter signals with Regime EMA (Close < EMA)?
input int             InpRegimeEMAPeriod   = 26;             // Regime EMA Period
input ENUM_APPLIED_PRICE InpEMAAppliedPrice = PRICE_CLOSE;   // EMA Applied Price

input group "--- Candle Pattern Controls ---"
input bool            InpRedCandleOnly     = false;          // Require signal candle to be Red? (false allows green rejection stars)
input bool            InpRequirePrevGreen  = false;          // Require the previous candle to be Green?

input group "--- Candle Geometry Settings ---"
input double          InpUpperWickMin      = 50.0;           // Upper wick min percentage (e.g. >= 50% for strong rejection)
input double          InpUpperWickMax      = 100.0;          // Upper wick max percentage (up to 100% for full pinbars)
input double          InpBodyMin           = 0.0;            // Body min percentage (0% allows dojis)
input double          InpBodyMax           = 40.0;           // Body max percentage (up to 40% for strong rejection stars)
input double          InpLowerWickMax      = 30.0;           // Lower wick max percentage (up to 30% allows small lower shadows)
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
bool           m_trigger_active = false;
double         m_trigger_low = 0;
double         m_trigger_high = 0;
datetime       m_trigger_start_time = 0;
datetime       m_trigger_expiry_time = 0;
int            m_ema_handle = INVALID_HANDLE;
double         m_last_bid = 0;

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

    // Initialize EMA handle
    m_ema_handle = iMA(_Symbol, InpTimeframe, InpRegimeEMAPeriod, 0, MODE_EMA, InpEMAAppliedPrice);
    if (m_ema_handle == INVALID_HANDLE) {
        Print("❌ Failed to create EMA indicator handle.");
        return INIT_FAILED;
    }

    m_last_checked_bar_time = iTime(_Symbol, InpTimeframe, 0);
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
    if (m_ema_handle != INVALID_HANDLE) {
        IndicatorRelease(m_ema_handle);
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

    // Check for new bar completion to scan for signal
    datetime current_bar_time = iTime(_Symbol, InpTimeframe, 0);
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
                PrintFormat("🔥 BREAKOUT DETECTED: Bid %.2f crossed below threshold %.2f (Signal Low: %.2f, Buffer: %.2f)",
                            current_bid, threshold, m_trigger_low, buffer);

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

    // Copy rates of completed bars (index 1 and index 2)
    MqlRates rates[];
    ArraySetAsSeries(rates, true);
    if (CopyRates(_Symbol, InpTimeframe, 1, 2, rates) < 2) {
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

    // Detailed candidate logging
    PrintFormat("📊 Candidate candle found. Color: %s | Upper Wick: %.1f%%, Body: %.1f%%, Lower Wick: %.1f%%, Range: %.2f",
                (c < o ? "RED" : "GREEN"), upper_wick_pct, body_pct, lower_wick_pct, total_range);

    if (upper_wick_pct < InpUpperWickMin || upper_wick_pct > InpUpperWickMax) {
        PrintFormat("❌ Candle rejected: Upper Wick %.1f%% is outside bounds (%.1f%% - %.1f%%)", upper_wick_pct, InpUpperWickMin, InpUpperWickMax);
        return;
    }
    if (body_pct < InpBodyMin || body_pct > InpBodyMax) {
        PrintFormat("❌ Candle rejected: Body %.1f%% is outside bounds (%.1f%% - %.1f%%)", body_pct, InpBodyMin, InpBodyMax);
        return;
    }
    if (lower_wick_pct < 0.0 || lower_wick_pct > InpLowerWickMax) {
        PrintFormat("❌ Candle rejected: Lower Wick %.1f%% is outside bounds (0.0%% - %.1f%%)", lower_wick_pct, InpLowerWickMax);
        return;
    }

    // Regime EMA Filter Check:
    if (InpUseEMAFilter) {
        double ema_values[];
        ArraySetAsSeries(ema_values, true);
        if (CopyBuffer(m_ema_handle, 0, 1, 1, ema_values) < 1) {
            Print("⚠️ Error copying EMA values for trend filter.");
            return;
        }
        double current_ema = ema_values[0];

        if (c >= current_ema) {
            PrintFormat("🔍 Signal rejected: Close (%.2f) is above or equal to Regime EMA (%.2f)", c, current_ema);
            return;
        }
    }

    // We have a verified signal!
    m_trigger_active = true;
    m_trigger_low = l;
    m_trigger_high = h;
    m_trigger_start_time = iTime(_Symbol, InpTimeframe, 0); // Trigger begins at start of current bar 0
    m_trigger_expiry_time = m_trigger_start_time + PeriodSeconds(InpTimeframe); // Expires at end of bar 0

    PrintFormat("🎯 REJECTION SIGNAL GENERATED: %s (%s). Wick=%.1f%%, Body=%.1f%%, Lower=%.1f%%.",
                _Symbol, (c < o ? "RED" : "GREEN"), upper_wick_pct, body_pct, lower_wick_pct);
    PrintFormat("👉 Breakout watch low: %.2f | SL target: %.2f | Window: %s to %s",
                l, h, TimeToString(m_trigger_start_time), TimeToString(m_trigger_expiry_time));
}

//+------------------------------------------------------------------+
//| Execute Short Entry (Market Sell)                                |
//+------------------------------------------------------------------+
void ExecuteShortEntry(double trigger_price)
{
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
    if (m_trade.Sell(lots, _Symbol, entry_price, sl_price, tp_price, InpTradeTag)) {
        ulong ticket = m_trade.ResultOrder();
        if (ticket > 0 || m_trade.ResultRetcode() == 10009) {
            PrintFormat("✅ TRADE SUCCESSFUL: Short position entered on %s. Lots: %.2f, Entry: %.2f, SL: %.2f, TP: %.2f",
                        _Symbol, lots, entry_price, sl_price, tp_price);
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
        if (PositionGetSymbol(i) == _Symbol) {
            long magic = PositionGetInteger(POSITION_MAGIC);
            if (magic != InpMagicNumber) continue;

            long type = PositionGetInteger(POSITION_TYPE);
            if (type != POSITION_TYPE_SELL) continue;

            ulong ticket = PositionGetInteger(POSITION_TICKET);
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
        if (PositionGetSymbol(i) == _Symbol) {
            long magic = PositionGetInteger(POSITION_MAGIC);
            if (magic == InpMagicNumber) return true;
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
        if (PositionGetSymbol(i) == _Symbol) {
            long magic = PositionGetInteger(POSITION_MAGIC);
            if (magic == InpMagicNumber) {
                ulong ticket = PositionGetInteger(POSITION_TICKET);
                m_trade.PositionClose(ticket);
                PrintFormat("✅ Closed Position Ticket: %I64u.", ticket);
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
                     "  RED SHOOTING STAR BREAKOUT EA (XM GOLD) \n" +
                     "==================================================\n" +
                     "  Symbol: " + _Symbol + "\n" +
                     "  Timeframe: " + EnumToString(InpTimeframe) + "\n" +
                     "  Regime EMA (" + (string)InpRegimeEMAPeriod + "): " + (m_ema_handle != INVALID_HANDLE ? "OK" : "Error") + "\n" +
                     "  EMA Filter: " + (InpUseEMAFilter ? "ENABLED" : "DISABLED") + "\n" +
                     "  Red Candle Only: " + (InpRedCandleOnly ? "YES" : "NO") + "\n" +
                     "  Prev Green Required: " + (InpRequirePrevGreen ? "YES" : "NO") + "\n" +
                     "  Risk:Reward Ratio: " + DoubleToString(InpRiskRewardRatio, 2) + "\n" +
                     "  Lot Size: " + (InpUseDynamicLot ? "Dynamic (" + DoubleToString(InpRiskPercentage, 2) + "%)" : "Fixed (" + DoubleToString(InpFixedLotSize, 2) + ")") + "\n" +
                     "--------------------------------------------------\n" +
                     "  Active Breakout Trigger: " + (m_trigger_active ? "YES" : "NO") + "\n";

    if (m_trigger_active) {
        comment += "  Trigger Candle Low: " + DoubleToString(m_trigger_low, _Digits) + "\n" +
                   "  Trigger Candle High (SL): " + DoubleToString(m_trigger_high, _Digits) + "\n" +
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
