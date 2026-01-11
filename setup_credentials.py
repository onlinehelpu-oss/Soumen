"""
Setup Fyers Credentials
=======================

Run this script one time to automatically create the 'fyers_login_details.json'
file with the correct format and credentials.
"""

import json

# --- Your Credentials ---
# These were provided in the chat.
credentials = {
  "client_id": "J7UEBL09X7-100",
  "secret_key": "FWL8GKS2KO",
  "redirect_url": "http://google.com"
}

# --- File Creation ---
file_name = "fyers_login_details.json"

try:
    with open(file_name, 'w') as f:
        json.dump(credentials, f, indent=2)

    print(f"✅ Successfully created '{file_name}' with the following content:")
    print(json.dumps(credentials, indent=2))
    print("\nYou are now ready to run the main bot script.")

except Exception as e:
    print(f"❌ An error occurred while creating the file: {e}")
