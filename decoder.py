
"""
decoder.py — Decodes on-chain transactions to find DEX swaps.

Supports:
  - Uniswap V3  exactInputSingle / exactInput
  - Uniswap V2  swapExactTokensForTokens / swapExactETHForTokens / swapExactTokensForETH

Returns a normalised SwapInfo dataclass so the rest of the bot
doesn't need to know which DEX was used.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Optional

from eth_abi import decode as abi_decode
from web3 import Web3

log = logging.getLogger(__name__)

# ── Known router addresses on Base (lowercase) ────────────────
ROUTERS = {
    # Uniswap V3 SwapRouter02
    "0x2626664c2603336e57b271c5c0b26f421741e481": "uniswap_v3",
    # Uniswap V2-style router (BaseSwap, SushiSwap on Base, etc.)
    "0x327df1e6de05895d2ab08513aadd9313fe505d86": "uniswap_v2",
}

# ── Function selectors (first 4 bytes of keccak of signature) ─
SELECTORS = {
    # Uniswap V3
    "0x04e45aaf": "v3_exactInputSingle",
    "0xb858183f": "v3_exactInput",
    # Uniswap V2
    "0x38ed1739": "v2_swapExactTokensForTokens",
    "0x7ff36ab5": "v2_swapExactETHForTokens",
    "0x18cbafe5": "v2_swapExactTokensForETH",
}


@dataclass
class SwapInfo:
    """Normalised swap data extracted from a transaction."""
    tx_hash:    str
    trader:     str          # address that sent the tx
    token_in:   str          # address
    token_out:  str          # address
    amount_in:  int          # raw integer (wei / smallest unit)
    dex:        str          # "uniswap_v3" | "uniswap_v2"
    is_eth_in:  bool = False # True when the trader sent native ETH


def decode_transaction(tx: dict) -> Optional[SwapInfo]:
    """
    Given a raw transaction dict from web3, attempt to decode it as a swap.
    Returns SwapInfo on success, None if it's not a recognisable swap.
    """
    to = (tx.get("to") or "").lower()
    if to not in ROUTERS:
        return None

    input_data: bytes = tx.get("input") or tx.get("data") or b""
    if len(input_data) < 4:
        return None

    selector = "0x" + input_data[:4].hex()
    method   = SELECTORS.get(selector)
    if not method:
        log.debug("Unknown selector %s on router %s", selector, to)
        return None

    dex     = ROUTERS[to]
    payload = input_data[4:]   # strip selector

    try:
        if method == "v3_exactInputSingle":
            return _decode_v3_exact_input_single(tx, payload, dex)
        elif method == "v3_exactInput":
            return _decode_v3_exact_input(tx, payload, dex)
        elif method == "v2_swapExactTokensForTokens":
            return _decode_v2_tokens_for_tokens(tx, payload, dex)
        elif method == "v2_swapExactETHForTokens":
            return _decode_v2_eth_for_tokens(tx, payload, dex)
        elif method == "v2_swapExactTokensForETH":
            return _decode_v2_tokens_for_eth(tx, payload, dex)
    except Exception as exc:
        log.warning("Failed to decode tx %s: %s", tx.get("hash", "?"), exc)

    return None


# ── V3 decoders ───────────────────────────────────────────────

def _decode_v3_exact_input_single(tx: dict, payload: bytes, dex: str) -> SwapInfo:
    # tuple: (address tokenIn, address tokenOut, uint24 fee,
    #         address recipient, uint256 amountIn,
    #         uint256 amountOutMinimum, uint160 sqrtPriceLimitX96)
    (token_in, token_out, _fee, _recipient,
     amount_in, _min_out, _sqrt) = abi_decode(
        ["address", "address", "uint24", "address",
         "uint256", "uint256", "uint160"],
        payload,
    )
    return SwapInfo(
        tx_hash   = _hex(tx["hash"]),
        trader    = tx["from"].lower(),
        token_in  = token_in.lower(),
        token_out = token_out.lower(),
        amount_in = amount_in,
        dex       = dex,
        is_eth_in = False,
    )


def _decode_v3_exact_input(tx: dict, payload: bytes, dex: str) -> Optional[SwapInfo]:
    # tuple: (bytes path, address recipient, uint256 amountIn, uint256 amountOutMinimum)
    (path_bytes, _recipient, amount_in, _min_out) = abi_decode(
        ["bytes", "address", "uint256", "uint256"],
        payload,
    )
    # The path is: tokenIn (20 bytes) | fee (3 bytes) | tokenOut (20 bytes) [| fee | token ...]
    if len(path_bytes) < 43:
        return None
    token_in  = "0x" + path_bytes[:20].hex()
    token_out = "0x" + path_bytes[-20:].hex()
    return SwapInfo(
        tx_hash   = _hex(tx["hash"]),
        trader    = tx["from"].lower(),
        token_in  = token_in.lower(),
        token_out = token_out.lower(),
        amount_in = amount_in,
        dex       = dex,
        is_eth_in = False,
    )


# ── V2 decoders ───────────────────────────────────────────────

def _decode_v2_tokens_for_tokens(tx: dict, payload: bytes, dex: str) -> SwapInfo:
    # (uint amountIn, uint amountOutMin, address[] path, address to, uint deadline)
    (amount_in, _min_out, path, _to, _deadline) = abi_decode(
        ["uint256", "uint256", "address[]", "address", "uint256"],
        payload,
    )
    return SwapInfo(
        tx_hash   = _hex(tx["hash"]),
        trader    = tx["from"].lower(),
        token_in  = path[0].lower(),
        token_out = path[-1].lower(),
        amount_in = amount_in,
        dex       = dex,
        is_eth_in = False,
    )


def _decode_v2_eth_for_tokens(tx: dict, payload: bytes, dex: str) -> SwapInfo:
    # (uint amountOutMin, address[] path, address to, uint deadline)
    (_min_out, path, _to, _deadline) = abi_decode(
        ["uint256", "address[]", "address", "uint256"],
        payload,
    )
    eth_value = tx.get("value", 0)
    return SwapInfo(
        tx_hash   = _hex(tx["hash"]),
        trader    = tx["from"].lower(),
        token_in  = path[0].lower(),
        token_out = path[-1].lower(),
        amount_in = eth_value,
        dex       = dex,
        is_eth_in = True,
    )


def _decode_v2_tokens_for_eth(tx: dict, payload: bytes, dex: str) -> SwapInfo:
    # (uint amountIn, uint amountOutMin, address[] path, address to, uint deadline)
    (amount_in, _min_out, path, _to, _deadline) = abi_decode(
        ["uint256", "uint256", "address[]", "address", "uint256"],
        payload,
    )
    return SwapInfo(
        tx_hash   = _hex(tx["hash"]),
        trader    = tx["from"].lower(),
        token_in  = path[0].lower(),
        token_out = path[-1].lower(),
        amount_in = amount_in,
        dex       = dex,
        is_eth_in = False,
    )


# ── Helpers ───────────────────────────────────────────────────

def _hex(value) -> str:
    if isinstance(value, bytes):
        return "0x" + value.hex()
    return str(value)
