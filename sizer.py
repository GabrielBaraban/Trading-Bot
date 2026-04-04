"""
sizer.py — Calculates how much we should trade when copying a swap.

Price source: DexScreener public API (free, no key, Base-native).
Falls back to WETH price from DexScreener if a token has no direct USD pair.
"""

from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import aiohttp

import config

log = logging.getLogger(__name__)

DEXSCREENER_URL = "https://api.dexscreener.com/latest/dex/tokens/{address}"
WETH = config.WETH_ADDRESS.lower()

# Simple in-process cache to avoid hammering the API for the same token
_price_cache: dict[str, tuple[float, float]] = {}  # address -> (price_usd, timestamp)
_CACHE_TTL = 30.0  # seconds


async def get_token_price_usd(session: aiohttp.ClientSession, token_address: str) -> Optional[float]:
    """Return the USD price of a token on Base using DexScreener."""
    addr = token_address.lower()

    # Check cache
    import time
    if addr in _price_cache:
        price, ts = _price_cache[addr]
        if time.time() - ts < _CACHE_TTL:
            return price

    url = DEXSCREENER_URL.format(address=addr)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status != 200:
                log.warning("DexScreener returned %d for %s", resp.status, addr)
                return None
            data = await resp.json()
    except Exception as exc:
        log.warning("Price fetch failed for %s: %s", addr, exc)
        return None

    pairs = data.get("pairs") or []
    # Filter to Base chain and find highest-liquidity pair
    base_pairs = [p for p in pairs if p.get("chainId") == "base"]
    if not base_pairs:
        log.warning("No Base pairs found for %s", addr)
        return None

    best = max(base_pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
    price_str = best.get("priceUsd")
    if not price_str:
        return None

    price = float(price_str)
    import time
    _price_cache[addr] = (price, time.time())
    log.debug("Price %s = $%.6f (liquidity $%.0f)", addr[:10], price,
              float(best.get("liquidity", {}).get("usd", 0) or 0))
    return price


@dataclass
class SizeResult:
    our_amount_usd: float        # USD value we will trade
    our_amount_raw: int          # raw token units (for executor)
    token_in_price_usd: float    # price used for sizing
    their_amount_usd: float      # original trade value in USD
    skip: bool = False
    skip_reason: str = ""


async def calculate_size(
    session: aiohttp.ClientSession,
    token_in: str,
    amount_in_raw: int,
    token_in_decimals: int,
) -> SizeResult:
    """
    Given the original trade's token_in and raw amount, return how much we copy.
    """
    price = await get_token_price_usd(session, token_in)
    if price is None:
        return SizeResult(0, 0, 0, 0, skip=True, skip_reason="price_unavailable")

    their_usd = (amount_in_raw / 10 ** token_in_decimals) * price
    our_usd   = their_usd * config.COPY_RATIO

    if our_usd < config.MIN_TRADE_USD:
        return SizeResult(
            0, 0, price, their_usd,
            skip=True,
            skip_reason=f"below_min (${our_usd:.2f} < ${config.MIN_TRADE_USD})",
        )

    our_usd = min(our_usd, config.MAX_TRADE_USD)
    our_raw = int((our_usd / price) * 10 ** token_in_decimals)

    return SizeResult(
        our_amount_usd=our_usd,
        our_amount_raw=our_raw,
        token_in_price_usd=price,
        their_amount_usd=their_usd,
    )
