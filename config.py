"""
config.py — Loads and validates all settings from .env
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(f"Missing required env variable: {key}")
    return val


# ── RPC ──────────────────────────────────────────────────────
RPC_WSS   = _require("BASE_RPC_WSS")
RPC_HTTPS = _require("BASE_RPC_HTTPS")

# ── Your wallet ───────────────────────────────────────────────
MY_ADDRESS     = _require("MY_WALLET_ADDRESS")
MY_PRIVATE_KEY = _require("MY_PRIVATE_KEY")

# ── Wallets to watch ─────────────────────────────────────────
WATCHED_WALLETS: set[str] = {
    w.strip().lower()
    for w in _require("WATCHED_WALLETS").split(",")
    if w.strip()
}

# ── Sizing ───────────────────────────────────────────────────
COPY_RATIO    = float(os.getenv("COPY_RATIO", "0.01"))   # 1% of their trade
MAX_TRADE_USD = float(os.getenv("MAX_TRADE_USD", "100"))
MIN_TRADE_USD = float(os.getenv("MIN_TRADE_USD", "5"))

# ── Execution ────────────────────────────────────────────────
SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "50"))      # 0.5%

# ── Base chain constants ──────────────────────────────────────
CHAIN_ID = 8453  # Base mainnet

# Uniswap V3 SwapRouter02 deployed on Base
UNISWAP_V3_ROUTER = "0x2626664c2603336E57B271c5C0b26F421741e481"

# Wrapped ETH on Base
WETH_ADDRESS = "0x4200000000000000000000000000000000000006"

# Uniswap V3 SwapRouter02 ABI (only the methods we need)
UNISWAP_V3_ROUTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn",  "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint24",  "name": "fee",      "type": "uint24"},
                    {"internalType": "address", "name": "recipient","type": "address"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountOutMinimum", "type": "uint256"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "internalType": "struct ISwapRouter.ExactInputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "exactInputSingle",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    }
]

# Minimal ERC-20 ABI (balanceOf + approve + decimals)
ERC20_ABI = [
    {"inputs": [{"name": "account", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "spender", "type": "address"},
                {"name": "amount",  "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}],
     "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [],
     "name": "decimals", "outputs": [{"name": "", "type": "uint8"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [],
     "name": "symbol", "outputs": [{"name": "", "type": "string"}],
     "stateMutability": "view", "type": "function"},
]
