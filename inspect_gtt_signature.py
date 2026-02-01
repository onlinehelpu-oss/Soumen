from fyers_apiv3 import fyersModel
import inspect

try:
    print("Docstring for place_gtt_order:")
    print(fyersModel.FyersModel.place_gtt_order.__doc__)

    print("\nSignature:")
    print(inspect.signature(fyersModel.FyersModel.place_gtt_order))
except Exception as e:
    print(f"Error: {e}")
