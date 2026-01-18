import time
import os
import sys
import json
import hashlib
from urllib.parse import urlparse, parse_qs, quote

try:
    import pandas as pd
    import gspread
    import datetime
    import numpy as np
    import requests
    from oauth2client.service_account import ServiceAccountCredentials
    from fyers_apiv3 import fyersModel
    from scipy.stats import norm
except ImportError as e:
    print("\n" + "="*60)
    print("❌ MISSING DEPENDENCIES")
    print("="*60)
    print(f"Error: {e}")
    print("\nPlease install the required libraries using pip:")
    print("👉 pip install -r requirements.txt")
    print("\nOr install them manually:")
    print("👉 pip install pandas gspread oauth2client fyers_apiv3 scipy numpy requests")
    print("="*60 + "\n")
    sys.exit(1)

# --- CONFIGURATION ---
# 1. FYERS API CREDENTIALS (DEFAULTS)
DEFAULT_CLIENT_ID = "XXXXXXXXXX-100"
DEFAULT_SECRET_KEY = "XXXXXXXXXX"
DEFAULT_REDIRECT_URI = "http://127.0.0.1"

# 2. GOOGLE SHEET SETTINGS
SPREADSHEET_ID = "1FN6qKkCyWsw2SrlGKCJ09rDbtJX_S8G-rCAB0og55_8"
CREDENTIALS_FILE = "credentials.json"
GSHEET_SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

# 3. TRADING CONFIG
# List of symbols to track (NSE Equity, Index, or MCX)
SYMBOLS = [
    "NSE:NIFTY50-INDEX",
    "NSE:NIFTYBANK-INDEX",
    "NSE:RELIANCE-EQ"
]
EXPIRY_DATE = "AUTO" # Set to "AUTO" to fetch the nearest expiry for EACH symbol.
RISK_FREE_RATE = 0.07

# 4. AUTH PATHS
CONFIG_FILE = "fyers_login_details.json"
TOKENS_DIR = "AccessToken"
TODAY = str(datetime.date.today())
TOKEN_PATH = os.path.join(TOKENS_DIR, f"{TODAY}.json")


# --- AUTHENTICATION LOGIC ---

def load_or_prompt_creds():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)

    print("---- Enter your Fyers Login Credentials (v3) ----")
    use_defaults = False
    if "XXXXXXXXXX" not in DEFAULT_CLIENT_ID:
        print(f"Found default credentials for Client ID: {DEFAULT_CLIENT_ID}")
        if input("Use these defaults? (Y/N): ").strip().upper() == "Y":
            creds = {
                "api_key": DEFAULT_CLIENT_ID,
                "api_secret": DEFAULT_SECRET_KEY,
                "redirect_url": DEFAULT_REDIRECT_URI,
            }
            use_defaults = True

    if not use_defaults:
        creds = {
            "api_key": input("Enter APP ID (e.g., ABCDE12345-100): ").strip(),
            "api_secret": input("Enter SECRET ID: ").strip(),
            "redirect_url": input("Enter Redirect URL (must match app): ").strip(),
        }

    if input("Save to 'fyers_login_details.json'? (Y/N): ").strip().upper() == "Y":
        with open(CONFIG_FILE, "w") as f:
            json.dump(creds, f, indent=2)
        print(f"Saved '{CONFIG_FILE}'.")
    else:
        print("Skipping save.")
    return creds

def build_auth_url(app_id, redirect_uri, state="sample_state"):
    base = "https://api-t1.fyers.in/api/v3/generate-authcode"
    params = (
        f"client_id={quote(app_id)}"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        f"&response_type=code"
        f"&state={quote(state)}"
        f"&scope=openid"
        f"&nonce={int(time.time())}"
    )
    return f"{base}?{params}"

def extract_code(user_input):
    if user_input.startswith("http://") or user_input.startswith("https://"):
        q = parse_qs(urlparse(user_input).query)
        code = q.get("code", [None])[0]
        if not code:
            raise ValueError("No 'code' param found in the provided URL.")
        return code
    return user_input

def sha256_appIdHash(app_id, secret_id):
    return hashlib.sha256(f"{app_id}:{secret_id}".encode("utf-8")).hexdigest()

def validate_authcode(app_id, secret_id, auth_code, max_retries=5):
    url = "https://api-t1.fyers.in/api/v3/validate-authcode"
    payload = {
        "grant_type": "authorization_code",
        "appIdHash": sha256_appIdHash(app_id, secret_id),
        "code": auth_code,
    }
    headers = {"Content-Type": "application/json"}

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=20)
            if r.status_code == 503:
                sleep_s = min(2 ** attempt, 30)
                print(f"[{attempt}/{max_retries}] 503 from auth server. Retrying in {sleep_s}s...")
                time.sleep(sleep_s)
                continue
            r.raise_for_status()
            data = r.json()
            if data.get("s") == "error":
                raise RuntimeError(f"Fyers error {data.get('code')}: {data.get('message')}")
            return data
        except requests.RequestException as e:
            if attempt == max_retries:
                raise
            sleep_s = min(2 ** attempt, 30)
            print(f"[{attempt}/{max_retries}] Network error: {e}. Retrying in {sleep_s}s...")
            time.sleep(sleep_s)

def get_fyers_client():
    creds = load_or_prompt_creds()
    app_id = creds["api_key"]
    secret_id = creds["api_secret"]
    redirect_uri = creds["redirect_url"]

    access_token = None
    if os.path.exists(TOKEN_PATH):
        try:
            with open(TOKEN_PATH, "r") as f:
                access_token = json.load(f)
            print(f"✅ Loaded Access Token from {TOKEN_PATH}")
        except Exception as e:
            print(f"⚠️ Error loading token file: {e}")

    if not access_token:
        print("⚠️ Token not found or invalid. Initiating Login...")
        auth_url = build_auth_url(app_id, redirect_uri)
        print("\nLogin URL (open in browser, allow & complete login):")
        print(auth_url)

        try:
            import webbrowser
            webbrowser.open(auth_url)
            print("(Browser tab opened)")
        except:
            pass

        user_val = input("\nPaste the FULL redirect URL after login, or just the 'code=' value here: ").strip()
        try:
            auth_code = extract_code(user_val)
            token_resp = validate_authcode(app_id, secret_id, auth_code)
            access_token = token_resp.get("access_token")
            if not access_token:
                raise RuntimeError(f"Unexpected token response: {token_resp}")

            os.makedirs(TOKENS_DIR, exist_ok=True)
            with open(TOKEN_PATH, "w") as f:
                json.dump(access_token, f)
            print(f"\n✅ Login successful. Token saved to {TOKEN_PATH}")
        except Exception as e:
            print(f"\n❌ Login Failed: {e}")
            sys.exit(1)

    return fyersModel.FyersModel(client_id=app_id, token=access_token, is_async=False, log_path="")


# --- MATH ENGINE ---
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

def get_expiry_identifier(fyers, symbol, user_config_date="AUTO"):
    print(f"   -> Fetching Expiry for: {symbol}...")
    try:
        data = {"symbol": symbol, "strikecount": 1}
        response = fyers.optionchain(data=data)
        if 'data' in response and 'expiryData' in response['data']:
            expiry_list = response['data']['expiryData']
            valid_expiries = []
            for item in expiry_list:
                try:
                    dt = datetime.datetime.strptime(item['date'], "%d-%m-%Y").date()
                    valid_expiries.append({'date_obj': dt, 'str': item['date'], 'code': item['expiry']})
                except: continue

            valid_expiries.sort(key=lambda x: x['date_obj'])
            today = datetime.date.today()

            target_date_obj = None
            if user_config_date != "AUTO":
                 try:
                     target_date_obj = datetime.datetime.strptime(user_config_date, "%Y-%m-%d").date()
                 except:
                     print(f"   [Error] Invalid Config Date Format: {user_config_date}")
                     return None, None

            for item in valid_expiries:
                if target_date_obj:
                    if item['date_obj'] == target_date_obj:
                        print(f"   -> ✅ Found Match! Expiry: {item['str']} Code: {item['code']}")
                        return item['code'], item['date_obj'].strftime("%Y-%m-%d")
                else:
                    if item['date_obj'] >= today:
                         print(f"   -> ✅ Auto-Selected Nearest Expiry: {item['str']} Code: {item['code']}")
                         return item['code'], item['date_obj'].strftime("%Y-%m-%d")

            print(f"   -> [Warning] No matching expiry found.")
            return None, None
        return None, None
    except Exception as e:
        print(f"   -> [Error] Expiry Lookup Failed: {e}")
        return None, None

# --- QUOTE ENRICHMENT ---
def fetch_and_merge_quotes(fyers, chain_data):
    if not chain_data: return chain_data
    symbols = [item['symbol'] for item in chain_data if 'symbol' in item]
    if not symbols: return chain_data

    batch_size = 50
    quote_map = {}
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i+batch_size]
        try:
            q_data = {"symbols": ",".join(batch)}
            response = fyers.quotes(data=q_data)
            if 'd' in response:
                for q_item in response['d']:
                    sym = q_item.get('n')
                    val = q_item.get('v')
                    if sym and val:
                        quote_map[sym] = val
        except Exception as e:
            print(f"   [Warning] Quote Batch Failed: {e}")

    for item in chain_data:
        sym = item.get('symbol')
        if sym in quote_map:
            q = quote_map[sym]
            item['ltp'] = q.get('lp', item.get('ltp', 0))
            item['volume'] = q.get('volume', 0)
            if 'volume' not in q and 'vol' in q: item['volume'] = q['vol']
            item['oi'] = q.get('oi', 0)
            item['prev_close_price'] = q.get('prev_close_price', item.get('ltp', 0) - q.get('ch', 0))

    return chain_data

def get_smart_trend(price_chg, oi_chg):
    if price_chg > 0 and oi_chg > 0: return "Long Buildup"
    if price_chg < 0 and oi_chg > 0: return "Short Buildup"
    if price_chg < 0 and oi_chg < 0: return "Long Unwinding"
    if price_chg > 0 and oi_chg < 0: return "Short Covering"
    if price_chg > 0 and oi_chg == 0: return "Buying (Flat OI)"
    if price_chg < 0 and oi_chg == 0: return "Selling (Flat OI)"
    return "Neutral"

def get_safe_val(data_dict, keys_to_try):
    for key in keys_to_try:
        if key in data_dict:
            return data_dict[key]
    return 0

# --- SHEET ---
def get_client():
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, GSHEET_SCOPE)
    client = gspread.authorize(creds)
    return client

def get_or_create_worksheet(client, title):
    try:
        return client.open_by_key(SPREADSHEET_ID).worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        # Create new worksheet
        try:
            return client.open_by_key(SPREADSHEET_ID).add_worksheet(title=title, rows=100, cols=26)
        except Exception as e:
            print(f"   [Error] Could not create worksheet '{title}': {e}")
            return None
    except Exception as e:
        print(f"   [Error] Worksheet access failed: {e}")
        return None

# --- PROCESSING LOGIC ---
def analyze_chain_data(chain_data, spot_price, T):
    clean_data = []
    strikes_dict = {}
    for item in chain_data:
        strikes_dict.setdefault(item['strike_price'], {})[item['option_type']] = item

    for strike in sorted(strikes_dict.keys()):
        ce, pe = strikes_dict[strike].get('CE'), strikes_dict[strike].get('PE')
        if not ce or not pe: continue

        oi_chg_keys = ['oi_change', 'oich', 'changeinOpenInterest', 'net_change_oi', 'change_oi', 'oiChange']
        def get_v(d, k): return d.get(k, 0)

        c_oi = get_v(ce, 'oi')
        c_chng_oi = get_safe_val(ce, oi_chg_keys)
        c_vol, c_ltp = get_v(ce, 'volume'), get_v(ce, 'ltp')
        c_chng = c_ltp - get_v(ce, 'prev_close_price')

        p_oi = get_v(pe, 'oi')
        p_chng_oi = get_safe_val(pe, oi_chg_keys)
        p_vol, p_ltp = get_v(pe, 'volume'), get_v(pe, 'ltp')
        p_chng = p_ltp - get_v(pe, 'prev_close_price')

        c_iv = get_implied_volatility(c_ltp, spot_price, strike, T, RISK_FREE_RATE, 'CE') * 100
        p_iv = get_implied_volatility(p_ltp, spot_price, strike, T, RISK_FREE_RATE, 'PE') * 100
        c_greeks = calculate_greeks(spot_price, strike, T, RISK_FREE_RATE, c_iv/100, 'CE')
        p_greeks = calculate_greeks(spot_price, strike, T, RISK_FREE_RATE, p_iv/100, 'PE')

        c_trend = get_smart_trend(c_chng, c_chng_oi)
        p_trend = get_smart_trend(p_chng, p_chng_oi)

        signal = ""
        min_oi = 100
        if c_oi > min_oi and p_oi > min_oi:
            if c_chng_oi < 0 and p_chng_oi > 0 and p_oi > c_oi: signal = "STRONG BUY CE 🚀"
            elif p_chng_oi < 0 and c_chng_oi > 0 and c_oi > p_oi: signal = "STRONG BUY PE 🩸"
            elif p_chng_oi > 0 and p_oi > c_oi * 1.5: signal = "Bullish Bias 🟢"
            elif c_chng_oi > 0 and c_oi > p_oi * 1.5: signal = "Bearish Bias 🔴"

        clean_data.append([
            c_oi, c_chng_oi, c_vol, round(c_iv,2), c_trend,
            c_greeks['delta'], c_greeks['theta'], c_greeks['gamma'], c_greeks['vega'],
            c_ltp, round(c_chng,2), strike, signal,
            p_ltp, round(p_chng,2), p_greeks['delta'], p_greeks['theta'], p_greeks['gamma'], p_greeks['vega'],
            p_trend, round(p_iv,2), p_vol, p_chng_oi, p_oi
        ])

    cols = ['Call OI', 'Call Chng OI', 'Call Vol', 'Call IV', 'Call Trend', 'Call Delta', 'Call Theta', 'Call Gamma', 'Call Vega', 'Call LTP', 'Call Chng', 'Strike Price', '⚠️ SIGNAL ⚠️', 'Put LTP', 'Put Chng', 'Put Delta', 'Put Theta', 'Put Gamma', 'Put Vega', 'Put Trend', 'Put IV', 'Put Vol', 'Put Chng OI', 'Put OI']
    return pd.DataFrame(clean_data, columns=cols)

def process_symbol(fyers, symbol, has_credentials, client):
    """Process a single symbol: fetch, analyze, update sheet/console."""
    print(f"\n   🔄 Processing: {symbol} ...")

    expiry_code, resolved_expiry_date = get_expiry_identifier(fyers, symbol, EXPIRY_DATE)
    if not expiry_code: return None

    spot_price = 0
    try:
        q = fyers.quotes(data={"symbols": symbol})
        spot_price = q['d'][0]['v'].get('lp', 0)
    except: pass

    if spot_price == 0:
        print(f"   [Warning] Spot Price 0 for {symbol}. Skipping.")
        return None

    try:
        # Reduced strikecount for multi-symbol performance
        data = {"symbol": symbol, "strikecount": 30, "timestamp": expiry_code}
        response = fyers.optionchain(data=data)
        if 'data' not in response or 'optionsChain' not in response['data']:
            print(f"   [Error] No Option Chain data for {symbol}")
            return None
        chain_data = response['data']['optionsChain']
    except Exception as e:
        print(f"   [Error] API call failed: {e}")
        return None

    chain_data = fetch_and_merge_quotes(fyers, chain_data)
    T = get_time_to_expiry(resolved_expiry_date)
    df = analyze_chain_data(chain_data, spot_price, T)

    if df.empty: return None

    pcr = 0; sentiment = "-"; max_ce = 0; max_pe = 0
    try:
        pcr = round(df['Put OI'].sum() / df['Call OI'].sum(), 2) if df['Call OI'].sum() > 0 else 0
        sentiment = "BULLISH" if pcr > 1.2 else "BEARISH" if pcr < 0.6 else "NEUTRAL"
        max_ce = df.loc[df['Call OI'].idxmax()]['Strike Price']
        max_pe = df.loc[df['Put OI'].idxmax()]['Strike Price']
    except: pass

    # Update Sheet
    if has_credentials and client:
        try:
            # Clean tab name (max 31 chars)
            tab_name = symbol.replace("NSE:", "").replace("MCX:", "").replace("-INDEX", "").replace("-EQ", "")[:30]
            sheet = get_or_create_worksheet(client, tab_name)
            if sheet:
                sheet.clear()
                sheet.append_row([f"Symbol: {symbol}", f"Spot: {spot_price}", f"Updated: {datetime.datetime.now().strftime('%H:%M:%S')}", f"Expiry: {resolved_expiry_date}"])
                sheet.append_row([f"PCR: {pcr} ({sentiment})", f"Support: {max_pe}", f"Resistance: {max_ce}", "Source: Fyers API"])
                sheet.append_row(df.columns.tolist())
                sheet.update(range_name='A4', values=df.values.tolist())
                print(f"   -> ✅ Updated Sheet '{tab_name}'")
        except Exception as e:
            print(f"   [Error] Sheet Update Failed: {e}")

    return {
        'symbol': symbol,
        'spot': spot_price,
        'pcr': pcr,
        'sentiment': sentiment,
        'signals': df[df['⚠️ SIGNAL ⚠️'] != ""]
    }

def print_dashboard(aggregated_results):
    """Prints a consolidated dashboard for all symbols."""
    print("\n" + "="*80)
    print(f"📊 MULTI-SYMBOL DASHBOARD | {datetime.datetime.now().strftime('%H:%M:%S')}")
    print("="*80)

    for res in aggregated_results:
        print(f"🔹 {res['symbol']:<20} | Spot: {res['spot']:<8} | PCR: {res['pcr']:<4} ({res['sentiment']})")
        if not res['signals'].empty:
            print("   🚀 Signals:")
            # Indent signals for readability
            sig_str = res['signals'][['Strike Price', '⚠️ SIGNAL ⚠️']].to_string(index=False, header=False)
            for line in sig_str.split('\n'):
                print(f"      {line}")
        else:
            print("   (No strong signals)")
        print("-" * 40)
    print("="*80 + "\n")

def run_live_cycle():
    has_credentials = False
    client = None
    if os.path.exists(CREDENTIALS_FILE):
        has_credentials = True
        try:
            client = get_client()
        except Exception as e:
            print(f"   [Error] Google Auth Failed: {e}")
            has_credentials = False
    else:
        print("\n" + "="*60)
        print(f"⚠️  MISSING FILE: '{CREDENTIALS_FILE}'")
        print("   Google Sheet updates will be SKIPPED.")
        print("="*60 + "\n")

    fyers = get_fyers_client()
    if not fyers: return

    print(f"--- Fyers API Connected. Tracking {len(SYMBOLS)} symbols ---")

    while True:
        try:
            print(f"\n[{datetime.datetime.now().strftime('%H:%M:%S')}] ⏳ Starting Cycle...")

            results = []
            for symbol in SYMBOLS:
                res = process_symbol(fyers, symbol, has_credentials, client)
                if res:
                    results.append(res)
                time.sleep(1) # Small delay between symbols

            print_dashboard(results)

            time.sleep(60)

        except KeyboardInterrupt: break
        except Exception as e:
            print(f"   [Error] Cycle crashed: {e}"); time.sleep(10)

if __name__ == "__main__":
    run_live_cycle()
