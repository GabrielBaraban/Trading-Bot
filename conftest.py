"""
Pytest configuration.

Ensures the repo root is importable and injects dummy environment
variables so `config.py` (which fails fast on missing settings) can be
imported during tests without a real `.env`. Set here, before any test
module imports `config`, so `load_dotenv(override=False)` cannot clobber them.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

os.environ["BASE_RPC_WSS"] = "wss://test.invalid/ws"
os.environ["BASE_RPC_HTTPS"] = "https://test.invalid"
os.environ["MY_WALLET_ADDRESS"] = "0x" + "00" * 20
os.environ["MY_PRIVATE_KEY"] = "0x" + "11" * 32
os.environ["WATCHED_WALLETS"] = "0x" + "22" * 20
