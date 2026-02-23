
import pandas as pd
import numpy as np
from delta_bot import SYMBOL_STATES, DeltaClient, TIMEFRAME_RES, LOOKBACK_CANDLES, compute_indicators, SymbolState

# Simulate Delta Client to get contract specs if possible, or just mock it.
# We will mock the state for PnL calculation testing.

def test_pnl_calculation():
    print("Testing PnL Calculation Logic...")

    # 1. Inverse Contract (BTCUSD) - Short
    # Entry: 60000, Exit: 58000, Qty: 100 contracts (Contract Value = 1 USD)
    # Notional Entry (USD) = 100 * 1 = 100 USD.
    # Inverse PnL (BTC) = Qty * Val * (1/Exit - 1/Entry) [For Short] ??
    # Wait, for Inverse Short:
    #   Entry at 60000. Sell 100 USD worth. You get 100/60000 = 0.001666 BTC.
    #   Exit at 58000. Buy 100 USD worth. You pay 100/58000 = 0.001724 BTC.
    #   PnL (BTC) = Entry_BTC - Exit_BTC = 0.001666 - 0.001724 = -0.000057 BTC (Loss? No, price went down, Short should profit).
    #   Ah, Inverse Logic:
    #   Short means you SELL contracts.
    #   PnL = (1/Entry - 1/Exit) * Qty * ContractVal.
    #   If Price Drops (Entry > Exit): 1/Entry < 1/Exit. Result is Negative.
    #   So standard formula (1/Entry - 1/Exit) gives negative for profit on short?
    #   Let's check Delta Docs or standard Inverse formula.
    #   Usually: PnL = Direction * Qty * Val * (1/Entry - 1/Exit).
    #   If Short (Dir = -1): -1 * (1/Entry - 1/Exit) = (1/Exit - 1/Entry).
    #   Example: Entry 60000, Exit 58000.
    #   1/58000 (0.00001724) - 1/60000 (0.00001666) = +0.00000057 BTC. Profit!
    #   So Formula for Short Inverse: Qty * Val * (1/Exit - 1/Entry).

    #   Fees:
    #   Entry Fee (BTC) = Notional_BTC_Entry * FeeRate = (Qty*Val/Entry) * FeeRate.
    #   Exit Fee (BTC) = Notional_BTC_Exit * FeeRate = (Qty*Val/Exit) * FeeRate.

    #   Total PnL (USD) = Total PnL (BTC) * ExitPrice (Approx).

    sym = "BTCUSD"
    st = SymbolState(sym)
    st.is_inverse = True
    st.contract_value = 1.0
    st.qty = 100 # 100 contracts
    st.entry_price = 60000.0
    exit_price = 58000.0
    taker_fee = 0.0005 # 0.05%

    print(f"\n--- {sym} (Inverse Short) ---")
    print(f"Entry: {st.entry_price}, Exit: {exit_price}, Qty: {st.qty}")

    # Calculate
    # Short PnL (BTC)
    pnl_btc = st.qty * st.contract_value * (1/exit_price - 1/st.entry_price)
    print(f"PnL (BTC): {pnl_btc:.8f}")

    # Gross PnL (USD)
    gross_pnl_usd = pnl_btc * exit_price
    print(f"Gross PnL (USD): {gross_pnl_usd:.4f}")

    # Fees (BTC)
    entry_val_btc = (st.qty * st.contract_value) / st.entry_price
    exit_val_btc = (st.qty * st.contract_value) / exit_price
    total_fee_btc = (entry_val_btc + exit_val_btc) * taker_fee
    print(f"Fees (BTC): {total_fee_btc:.8f}")

    # Fees (USD)
    total_fee_usd = total_fee_btc * exit_price
    print(f"Fees (USD): {total_fee_usd:.4f}")

    net_pnl_usd = gross_pnl_usd - total_fee_usd
    print(f"Net PnL (USD): {net_pnl_usd:.4f}")

    # Manual Check:
    # Profit = (60000 - 58000) / 60000 * 100 USD = 3.33 USD?
    # Inverse PnL is non-linear.
    # 100 USD at 60000 = 0.001666 BTC.
    # 100 USD at 58000 = 0.001724 BTC.
    # Diff = 0.000057 BTC.
    # Value at Exit (58000) = 0.000057 * 58000 = 3.33 USD. Matches.

    # 2. Linear Contract (SOLUSD - Assumption: Linear on Delta for this example, or maybe ETHUSDT)
    # Let's assume SOLUSD is Linear (Settled in USDT).
    # Entry: 100, Exit: 95, Qty: 10 Contracts (Contract Value = 1 SOL? No, usually 0.1 or 1).
    # Let's say Contract Value = 1 SOL.
    # Short Entry 100. Exit 95.
    # PnL = (Entry - Exit) * Qty * ContractVal = (100 - 95) * 10 * 1 = 50 USDT.
    # Fees = Notional * FeeRate.
    # Notional Entry = 100 * 10 * 1 = 1000. Fee = 0.5.
    # Notional Exit = 95 * 10 * 1 = 950. Fee = 0.475.
    # Total Fee = 0.975.
    # Net = 50 - 0.975 = 49.025.

    sym_lin = "SOLUSD"
    st_lin = SymbolState(sym_lin)
    st_lin.is_inverse = False # Linear
    st_lin.contract_value = 1.0
    st_lin.qty = 10
    st_lin.entry_price = 100.0
    exit_price_lin = 95.0

    print(f"\n--- {sym_lin} (Linear Short) ---")
    print(f"Entry: {st_lin.entry_price}, Exit: {exit_price_lin}, Qty: {st_lin.qty}")

    # Calculate
    gross_pnl_lin = (st_lin.entry_price - exit_price_lin) * st_lin.qty * st_lin.contract_value
    print(f"Gross PnL (USD): {gross_pnl_lin:.4f}")

    entry_notional = st_lin.qty * st_lin.contract_value * st_lin.entry_price
    exit_notional = st_lin.qty * st_lin.contract_value * exit_price_lin

    fees_lin = (entry_notional + exit_notional) * taker_fee
    print(f"Fees (USD): {fees_lin:.4f}")

    net_pnl_lin = gross_pnl_lin - fees_lin
    print(f"Net PnL (USD): {net_pnl_lin:.4f}")

if __name__ == "__main__":
    test_pnl_calculation()
