import time
import pandas as pd
import gspread
import datetime
import numpy as np
import os
import webbrowser
from oauth2client.service_account import ServiceAccountCredentials
from fyers_apiv3 import fyersModel
from scipy.stats import norm

# --- CONFIGURATION ---
# 1. FYERS API CREDENTIALS
# Go to https://myapi.fyers.in/dashboard to get these
CLIENT_ID = "XXXXXXXXXX-100"  # Format: XXXXXX-100
SECRET_KEY = "XXXXXXXXXX"
REDIRECT_URI = "http://127.0.0.1" # Must match what you set in Fyers App
USER_NAME = "XS0000"              # Your Fyers User ID
TOTP_KEY = ""                     # Leave empty for manual login

# 2. GOOGLE SHEET SETTINGS
SPREADSHEET_ID = "1FN6qKkCyWsw2SrlGKCJ09rDbtJX_S8G-rCAB0og55_8"
CREDENTIALS_FILE = "credentials.json"
GSHEET_SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

# 3. TRADING CONFIG
# For Fyers, Symbols look like: "NSE:NIFTYBANK-INDEX" or "NSE:NIFTY50-INDEX"
SYMBOL = "NSE:RELIANCE-EQ"
EXPIRY_DATE = "2026-02-24" # Format: YYYY-MM-DD. Ensure this matches the STOCK monthly expiry!
RISK_FREE_RATE = 0.07


# --- MATH ENGINE: BLACK-SCHOLES IV SOLVER ---
def get_implied_volatility(price, spot, strike, t, r, flag):
    if price <= 0.05 or t <= 0.0001: return 0
    low, high = 0.01, 5.0
    for _ in range(15):
        mid = (low + high) / 2
        try:
            d1 = (np.log(spot / strike) + (r + 0.5 * mid ** 2) * t) / (mid * np.sqrt(t))
            d2 = d1 - mid * np.sqrt(t)
            if flag == 'CE':
                theo = spot * norm.cdf(d1) - strike * np.exp(-r * t) * norm.cdf(d2)
            else:
                theo = strike * np.exp(-r * t) * norm.cdf(-d2) - spot * norm.cdf(-d1)
            if abs(theo - price) < 0.1: return mid
            if theo > price: high = mid
            else: low = mid
        except: return 0
    return (low + high) / 2

# --- MATH ENGINE: GREEKS ---
def calculate_greeks(spot, strike, t, r, iv, opt_type):
    try:
        if iv <= 0.001 or t <= 0.0001 or spot <= 0: return {'delta': 0, 'theta': 0, 'gamma': 0, 'vega': 0}
        d1 = (np.log(spot / strike) + (r + 0.5 * iv ** 2) * t) / (iv * np.sqrt(t))
        d2 = d1 - iv * np.sqrt(t)
        if opt_type == 'CE':
            delta = norm.cdf(d1)
            theta = (-spot * norm.pdf(d1) * iv / (2 * np.sqrt(t)) - r * strike * np.exp(-r * t) * norm.cdf(d2)) / 365
        else:
            delta = norm.cdf(d1) - 1
            theta = (-spot * norm.pdf(d1) * iv / (2 * np.sqrt(t)) + r * strike * np.exp(-r * t) * norm.cdf(-d2)) / 365
        gamma = norm.pdf(d1) / (spot * iv * np.sqrt(t))
        vega = spot * np.sqrt(t) * norm.pdf(d1) / 100
        return {'delta': round(delta, 3), 'theta': round(theta, 2), 'gamma': round(gamma, 5), 'vega': round(vega, 2)}
    except: return {'delta': 0, 'theta': 0, 'gamma': 0, 'vega': 0}

def get_time_to_expiry(expiry_date_str):
    try:
        expiry = datetime.datetime.strptime(expiry_date_str, "%Y-%m-%d")
        today = datetime.datetime.now()
        expiry = expiry.replace(hour=15, minute=30, second=0)
        diff = expiry - today
        T = (diff.days + diff.seconds / 86400) / 365.0
        return max(T, 0.00001)
    except: return 0.00001

# --- CONNECT & AUTHENTICATION ---
def get_access_token():
    print("--- Fyers Auto-Login ---")

    # 1. Create Session
    session = fyersModel.SessionModel(
        client_id=CLIENT_ID,
        secret_key=SECRET_KEY,
        redirect_uri=REDIRECT_URI,
        response_type="code",
        grant_type="authorization_code"
    )

    # 2. Generate and Open Login URL
    auth_link = session.generate_authcode()
    print("\n1. Opening Browser to Login...")
    webbrowser.open(auth_link)

    # 3. User Pastes the URL
    print("\n2. After login, you will be redirected to Google (or your redirect URI).")
    print("   COPY the entire URL from the address bar and paste it below.")
    new_url = input("   👉 Paste Redirect URL: ").strip()

    # 4. Extract Code & Generate Token
    try:
        if "auth_code=" not in new_url:
            print("\n❌ Error: URL does not contain 'auth_code'. Try again.")
            return None

        auth_code = new_url.split("auth_code=")[1].split("&")[0]
        session.set_token(auth_code)
        response = session.generate_token()

        if "access_token" in response:
            access_token = response["access_token"]
            with open("access_token.txt", "w") as f:
                f.write(access_token)
            print("\n✅ SUCCESS! Token saved to 'access_token.txt'.")
            return access_token
        else:
            print(f"\n❌ Login Failed: {response}")
            return None

    except Exception as e:
        print(f"\n❌ Exception: {e}")
        return None

def get_fyers_client():
    token = None
    if os.path.exists("access_token.txt"):
        with open("access_token.txt", "r") as f:
            token = f.read().strip()

    if not token:
        print("⚠️ Access token not found or invalid. Initiating login...")
        token = get_access_token()

    if not token:
        print("❌ Error: Could not obtain access token.")
        return None

    return fyersModel.FyersModel(client_id=CLIENT_ID, token=token, is_async=False, log_path="")

# --- EXPIRY LOOKUP ---
def get_expiry_identifier(fyers, symbol, user_config_date):
    print(f"   -> Matching Expiry for: {user_config_date}...")
    try:
        data = {"symbol": symbol, "strikecount": 1}
        response = fyers.optionchain(data=data)
        if 'data' in response and 'expiryData' in response['data']:
            for item in response['data']['expiryData']:
                fyers_date_str = item['date']
                fyers_expiry_code = item['expiry']
                try:
                    dt = datetime.datetime.strptime(fyers_date_str, "%d-%m-%Y")
                    if dt.strftime("%Y-%m-%d") == user_config_date:
                        print(f"   -> ✅ Found Match! Expiry Code: {fyers_expiry_code}")
                        return fyers_expiry_code
                except: continue
            print(f"   -> [Warning] Expiry {user_config_date} not found.")
            return None
        return None
    except Exception as e:
        print(f"   -> [Error] Expiry Lookup Failed: {e}")
        return None

# --- NEW: ADVANCED TREND LOGIC ---
def get_smart_trend(price_chg, oi_chg):
    if price_chg > 0 and oi_chg > 0: return "Long Buildup"
    if price_chg < 0 and oi_chg > 0: return "Short Buildup"
    if price_chg < 0 and oi_chg < 0: return "Long Unwinding"
    if price_chg > 0 and oi_chg < 0: return "Short Covering"
    if price_chg > 0 and oi_chg == 0: return "Buying (Flat OI)"
    if price_chg < 0 and oi_chg == 0: return "Selling (Flat OI)"
    return "Neutral"

# --- HELPER: ROBUST VALUE GETTER ---
def get_safe_val(data_dict, keys_to_try):
    """Tries multiple potential key names for OI Change"""
    for key in keys_to_try:
        if key in data_dict:
            return data_dict[key]
    return 0

# --- SHEET ---
def setup_sheet():
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, GSHEET_SCOPE)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).sheet1

# --- MAIN LOOP ---
def run_live_cycle():
    fyers = get_fyers_client()
    if not fyers: return

    print(f"--- Fyers API Connected. Tracking {SYMBOL} ---")
    expiry_code = get_expiry_identifier(fyers, SYMBOL, EXPIRY_DATE)
    if not expiry_code: return

    # DEBUG FLAG
    printed_debug = False

    while True:
        try:
            print(f"\n[{datetime.datetime.now().strftime('%H:%M:%S')}] ⏳ Fetching Fyers Data...")

            spot_price = 0
            try:
                q = fyers.quotes(data={"symbols": SYMBOL})
                spot_price = q['d'][0]['v'].get('lp', 0)
            except: pass

            if spot_price == 0:
                print("   [Warning] Spot Price 0. Retrying..."); time.sleep(5); continue

            data = {"symbol": SYMBOL, "strikecount": 500, "timestamp": expiry_code}
            response = fyers.optionchain(data=data)

            if 'data' not in response or 'optionsChain' not in response['data']:
                print(f"   [Error] API Error. Response: {response}"); time.sleep(5); continue

            chain_data = response['data']['optionsChain']

            # --- DEBUG: PRINT RAW KEYS ONCE ---
            if not printed_debug and len(chain_data) > 0:
                print("\n" + "="*50)
                print("   🔍 DEBUG: RAW DATA KEYS FROM FYERS")
                print(f"   Keys: {list(chain_data[0].keys())}")
                print("   Please check this list if OI Change is missing!")
                print("="*50 + "\n")
                printed_debug = True

            clean_data = []
            T = get_time_to_expiry(EXPIRY_DATE)
            strikes_dict = {}
            for item in chain_data:
                strikes_dict.setdefault(item['strike_price'], {})[item['option_type']] = item

            for strike in sorted(strikes_dict.keys()):
                ce, pe = strikes_dict[strike].get('CE'), strikes_dict[strike].get('PE')
                if not ce or not pe: continue

                # --- KEY HUNTER ---
                # We try all known variations for OI Change
                oi_chg_keys = ['oi_change', 'oich', 'changeinOpenInterest', 'net_change_oi', 'change_oi', 'oiChange']

                def get_v(d, k): return d.get(k, 0)

                # Extract
                c_oi = get_v(ce, 'oi')
                c_chng_oi = get_safe_val(ce, oi_chg_keys) # Hunts for the key
                c_vol, c_ltp = get_v(ce, 'volume'), get_v(ce, 'ltp')
                c_chng = c_ltp - get_v(ce, 'prev_close_price')

                p_oi = get_v(pe, 'oi')
                p_chng_oi = get_safe_val(pe, oi_chg_keys) # Hunts for the key
                p_vol, p_ltp = get_v(pe, 'volume'), get_v(pe, 'ltp')
                p_chng = p_ltp - get_v(pe, 'prev_close_price')

                # Math Engine
                c_iv = get_implied_volatility(c_ltp, spot_price, strike, T, RISK_FREE_RATE, 'CE') * 100
                p_iv = get_implied_volatility(p_ltp, spot_price, strike, T, RISK_FREE_RATE, 'PE') * 100
                c_greeks = calculate_greeks(spot_price, strike, T, RISK_FREE_RATE, c_iv/100, 'CE')
                p_greeks = calculate_greeks(spot_price, strike, T, RISK_FREE_RATE, p_iv/100, 'PE')

                # Trend & Signal
                c_trend = get_smart_trend(c_chng, c_chng_oi)
                p_trend = get_smart_trend(p_chng, p_chng_oi)

                signal = ""
                min_oi = 100
                if c_oi > min_oi and p_oi > min_oi:
                    if c_chng_oi < 0 and p_chng_oi > 0 and p_oi > c_oi: signal = "STRONG BUY CE 🚀"
                    elif p_chng_oi < 0 and c_chng_oi > 0 and c_oi > p_oi: signal = "STRONG BUY PE 🩸"
                    # Bias Logic (Active if OI Change is working)
                    elif p_chng_oi > 0 and p_oi > c_oi * 1.5: signal = "Bullish Bias 🟢"
                    elif c_chng_oi > 0 and c_oi > p_oi * 1.5: signal = "Bearish Bias 🔴"

                clean_data.append([
                    c_oi, c_chng_oi, c_vol, round(c_iv,2), c_trend,
                    c_greeks['delta'], c_greeks['theta'], c_greeks['gamma'], c_greeks['vega'],
                    c_ltp, round(c_chng,2), strike, signal,
                    p_ltp, round(p_chng,2), p_greeks['delta'], p_greeks['theta'], p_greeks['gamma'], p_greeks['vega'],
                    p_trend, round(p_iv,2), p_vol, p_chng_oi, p_oi
                ])

            if not clean_data: continue

            # Push
            cols = ['Call OI', 'Call Chng OI', 'Call Vol', 'Call IV', 'Call Trend', 'Call Delta', 'Call Theta', 'Call Gamma', 'Call Vega', 'Call LTP', 'Call Chng', 'Strike Price', '⚠️ SIGNAL ⚠️', 'Put LTP', 'Put Chng', 'Put Delta', 'Put Theta', 'Put Gamma', 'Put Vega', 'Put Trend', 'Put IV', 'Put Vol', 'Put Chng OI', 'Put OI']
            df = pd.DataFrame(clean_data, columns=cols)

            try:
                pcr = round(df['Put OI'].sum() / df['Call OI'].sum(), 2) if df['Call OI'].sum() > 0 else 0
                sentiment = "BULLISH" if pcr > 1.2 else "BEARISH" if pcr < 0.6 else "NEUTRAL"
                max_ce = df.loc[df['Call OI'].idxmax()]['Strike Price']
                max_pe = df.loc[df['Put OI'].idxmax()]['Strike Price']
            except: pcr=0; sentiment="-"; max_ce=0; max_pe=0

            try:
                sheet = setup_sheet()
                sheet.clear()
                sheet.append_row([f"Symbol: {SYMBOL}", f"Spot: {spot_price}", f"Updated: {datetime.datetime.now().strftime('%H:%M:%S')}", f"Expiry: {EXPIRY_DATE}"])
                sheet.append_row([f"PCR: {pcr} ({sentiment})", f"Support: {max_pe}", f"Resistance: {max_ce}", "Source: Fyers API (Key Hunter V2.1)"])
                sheet.append_row(df.columns.tolist())
                sheet.update(range_name='A4', values=df.values.tolist())
                print(f"   -> ✅ Updated {len(df)} strikes.")
            except Exception as e:
                print(f"   [Error] Google Sheet Update Failed: {e}")

            time.sleep(60)

        except KeyboardInterrupt: break
        except Exception as e:
            print(f"   [Error] Cycle crashed: {e}"); time.sleep(10)

if __name__ == "__main__":
    run_live_cycle()
