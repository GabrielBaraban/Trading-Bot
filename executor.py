"""
executor.py — Paper trade simulator.

No real transactions are sent. We record simulated fills using the
current on-chain price (via DexScreener) at the moment of execution,
and estimate gas cost using the current Base gas price.

When PAPER_TRADING=false is set in .env, this module will be extended
to send real transactions via Uniswap V3.
"""

from __future__ import annotations
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import aiohttp
from web3 import AsyncWeb3, WebSocketProvider

import config
from decoder import SwapInfo
from sizer import SizeResult, get_token_price_usd

log = logging.getLogger(__name__)

PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() != "false"

# Approximate gas units for a Uniswap V3 exactInputSingle swap on Base
SWAP_GAS_UNITS = 150_000


@dataclass
class PaperFill:
    """Result of a simulated (paper) trade execution."""
    original_tx:        str
    copied_wallet:      str
    dex:                str

    token_in_address:   str
    token_out_address:  str
    token_in_symbol:    str
    token_out_symbol:   str
    token_in_decimals:  int
    token_out_decimals: int

    their_amount_usd:   float
    our_amount_usd:     float
    our_amount_raw:     int

    token_in_price_usd:  float
    token_out_price_usd: float

    # Simulated fill price: how many token_out per token_in
    simulated_price:    float

    estimated_gas_usd:  float
    gas_price_gwei:     float

    timestamp:          float = field(default_factory=time.time)
    is_paper:           bool  = True


async def execute_paper(
    w3: AsyncWeb3,
    session: aiohttp.ClientSession,
    swap: SwapInfo,
    size: SizeResult,
) -> Optional[PaperFill]:
    """
    Simulate executing the copy trade. Returns a PaperFill or None on failure.
    """
    if not PAPER_TRADING:
        raise RuntimeError("Live trading not yet implemented — keep PAPER_TRADING=true")

    # ── Fetch token metadata ──────────────────────────────────────
    token_in_meta  = await _token_meta(w3, swap.token_in)
    token_out_meta = await _token_meta(w3, swap.token_out)
    if token_in_meta is None or token_out_meta is None:
        log.warning("Could not fetch token metadata for %s / %s", swap.token_in, swap.token_out)
        return None

    in_symbol, in_decimals   = token_in_meta
    out_symbol, out_decimals = token_out_meta

    # ── Fetch token_out price for simulated fill ──────────────────
    token_out_price = await get_token_price_usd(session, swap.token_out)
    if token_out_price is None:
        log.warning("Cannot get token_out price for %s", swap.token_out)
        token_out_price = 0.0

    # Simulated price ratio: token_in / token_out
    simulated_price = (
        size.token_in_price_usd / token_out_price
        if token_out_price > 0 else 0.0
    )

    # ── Estimate gas cost in USD ──────────────────────────────────
    gas_price_wei, eth_usd = await asyncio.gather(
        _get_gas_price(w3),
        get_token_price_usd(session, config.WETH_ADDRESS),
    )
    eth_usd = eth_usd or 0.0
    gas_price_gwei = gas_price_wei / 1e9
    estimated_gas_usd = (SWAP_GAS_UNITS * gas_price_wei / 1e18) * eth_usd

    log.info(
        "[PAPER] Copy %s→%s  our=$%.2f  gas≈$%.4f",
        in_symbol, out_symbol, size.our_amount_usd, estimated_gas_usd,
    )

    return PaperFill(
        original_tx        = swap.tx_hash,
        copied_wallet      = swap.trader,
        dex                = swap.dex,
        token_in_address   = swap.token_in,
        token_out_address  = swap.token_out,
        token_in_symbol    = in_symbol,
        token_out_symbol   = out_symbol,
        token_in_decimals  = in_decimals,
        token_out_decimals = out_decimals,
        their_amount_usd   = size.their_amount_usd,
        our_amount_usd     = size.our_amount_usd,
        our_amount_raw     = size.our_amount_raw,
        token_in_price_usd  = size.token_in_price_usd,
        token_out_price_usd = token_out_price,
        simulated_price    = simulated_price,
        estimated_gas_usd  = estimated_gas_usd,
        gas_price_gwei     = gas_price_gwei,
    )


# ── Helpers ───────────────────────────────────────────────────

import asyncio
from functools import lru_cache

_meta_cache: dict[str, tuple[str, int]] = {}

async def _token_meta(w3: AsyncWeb3, address: str) -> Optional[tuple[str, int]]:
    """Return (symbol, decimals) for an ERC-20 token, cached."""
    addr = address.lower()
    if addr in _meta_cache:
        return _meta_cache[addr]

    # Native ETH / WETH shortcut
    if addr == config.WETH_ADDRESS.lower():
        _meta_cache[addr] = ("WETH", 18)
        return ("WETH", 18)

    try:
        checksum = w3.to_checksum_address(addr)
        contract = w3.eth.contract(address=checksum, abi=config.ERC20_ABI)
        symbol, decimals = await asyncio.gather(
            contract.functions.symbol().call(),
            contract.functions.decimals().call(),
        )
        _meta_cache[addr] = (symbol, decimals)
        return (symbol, decimals)
    except Exception as exc:
        log.warning("Token meta fetch failed for %s: %s", addr, exc)
        return None


async def _get_gas_price(w3: AsyncWeb3) -> int:
    try:
        return await w3.eth.gas_price
    except Exception:
        return 1_000_000  # 0.001 gwei fallback
