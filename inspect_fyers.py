from fyers_apiv3 import fyersModel
import inspect

print("Methods in fyersModel.FyersModel:")
try:
    # We don't need a real token to inspect the class
    client = fyersModel.FyersModel(client_id="test", token="test")
    methods = [m for m in dir(client) if not m.startswith("_")]
    for m in methods:
        print(f"- {m}")

    if 'place_gtt' in methods:
        print("\nplace_gtt found!")
    else:
        print("\nplace_gtt NOT found!")

    # Check for other gtt related methods
    gtt_methods = [m for m in methods if 'gtt' in m.lower()]
    print("\nGTT related methods:", gtt_methods)

except Exception as e:
    print(f"Error inspecting: {e}")
