import unittest
from unittest.mock import MagicMock, patch
from delta_gamma_compression import GammaBot, DeltaClient, StrategyState
from datetime import datetime, timezone

class TestGammaBot(unittest.TestCase):
    def setUp(self):
        self.bot = GammaBot("test", "test", dry_run=True)
        # Mock client to avoid network calls
        self.bot.client.get_tickers = MagicMock(return_value={'result': []})
        self.bot.client.place_order = MagicMock(return_value={'result': {'order_id': '123'}, 'success': True})

    def test_parse_symbol(self):
        # Test standard Delta format
        # C-BTC-100000-140226
        res = self.bot.parse_symbol("C-BTC-100000-140226")
        self.assertEqual(res['type'], 'call')
        self.assertEqual(res['asset'], 'BTC')
        self.assertEqual(res['strike'], 100000)
        self.assertEqual(res['date'], '140226')

        # Test Put
        res = self.bot.parse_symbol("P-BTC-90000-150226")
        self.assertEqual(res['type'], 'put')
        self.assertEqual(res['strike'], 90000)

    def test_find_entry_candidates(self):
        # Mock tickers
        today_str = self.bot.get_0dte_expiry_date()

        mock_tickers = {
            'C-BTC-60000-' + today_str: {
                'symbol': 'C-BTC-60000-' + today_str,
                'contract_type': 'call_options',
                'underlying_asset_symbol': 'BTC',
                'greeks': {'delta': '0.18'} # Perfect match
            },
            'C-BTC-61000-' + today_str: {
                'symbol': 'C-BTC-61000-' + today_str,
                'contract_type': 'call_options',
                'underlying_asset_symbol': 'BTC',
                'greeks': {'delta': '0.10'} # Far
            },
            'P-BTC-50000-' + today_str: {
                'symbol': 'P-BTC-50000-' + today_str,
                'contract_type': 'put_options',
                'underlying_asset_symbol': 'BTC',
                'greeks': {'delta': '-0.18'} # Perfect match
            }
        }
        self.bot.tickers = mock_tickers

        call, put = self.bot.find_entry_candidates()
        self.assertIsNotNone(call)
        self.assertIsNotNone(put)
        self.assertEqual(call['symbol'], 'C-BTC-60000-' + today_str)
        self.assertEqual(put['symbol'], 'P-BTC-50000-' + today_str)

    def test_check_adjustments(self):
        # Setup positions
        self.bot.positions = {
            'call': {
                'symbol': 'C-BTC-60000-140226',
                'entry_price': 100,
                'last_reset_price': 100,
                'size': 1,
                'strike': 60000
            }
        }

        # Mock tickers with price increase
        self.bot.tickers = {
            'C-BTC-60000-140226': {
                'symbol': 'C-BTC-60000-140226',
                'mark_price': 130, # 30% increase
                'quotes': {'best_bid': 129, 'best_ask': 131}
            }
        }

        # Use patch to mock perform_adjustment
        with patch.object(self.bot, 'perform_adjustment') as mock_adj:
            self.bot.check_adjustments()
            mock_adj.assert_called_with('call', 'put')

    def test_check_compression(self):
        # Setup positions with narrow spread
        self.bot.positions = {
            'call': {'symbol': 'C', 'strike': 60100},
            'put': {'symbol': 'P', 'strike': 60000}
        }
        # Spread = 100 <= 400

        # Mock index price (spot)
        self.bot.client.get_index_price = MagicMock(return_value=60050)

        with patch.object(self.bot, 'execute_iron_fly_hedge') as mock_hedge:
            self.bot.check_compression()
            mock_hedge.assert_called()

if __name__ == '__main__':
    unittest.main()
