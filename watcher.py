"""
watcher.py — Watches Base chain for swaps from WATCHED_WALLETS.

Strategy: subscribe to confirmed blocks via WebSocket, then inspect
every transaction in the block. Confirmed-only avoids mempool noise
and gives decoder.py the real on-chain data it needs.
"""

from __future__ import annotations
import asyncio
import logging
from typing import AsyncIterator

from web3 import AsyncWeb3, WebSocketProvider

import config
from decoder import SwapInfo, decode_transaction

log = logging.getLogger(__name__)


async def watch_swaps() -> AsyncIterator[SwapInfo]:
    """
    Async generator — yields SwapInfo for every swap made by a watched wallet.
    Reconnects automatically on connection drop.
    """
    while True:
        try:
            async for swap in _watch_once():
                yield swap
        except Exception as exc:
            log.error("WebSocket error: %s — reconnecting in 5 s", exc)
            await asyncio.sleep(5)


async def _watch_once() -> AsyncIterator[SwapInfo]:
    log.info("Connecting to Base WSS: %s", config.RPC_WSS)
    async with AsyncWeb3(WebSocketProvider(config.RPC_WSS)) as w3:
        subscription_id = await w3.eth.subscribe("newHeads")
        log.info("Subscribed to newHeads (id=%s)", subscription_id)

        async for message in w3.socket.process_subscriptions():
            block_hash = message["result"].get("hash")
            if not block_hash:
                continue

            try:
                block = await w3.eth.get_block(block_hash, full_transactions=True)
            except Exception as exc:
                log.warning("Could not fetch block %s: %s", block_hash, exc)
                continue

            block_number = block.get("number", "?")
            txs = block.get("transactions", [])
            log.debug("Block %s — %d txs", block_number, len(txs))

            for tx in txs:
                tx_dict = dict(tx)
                sender = (tx_dict.get("from") or "").lower()
                if sender not in config.WATCHED_WALLETS:
                    continue

                swap = decode_transaction(tx_dict)
                if swap is None:
                    continue

                log.info(
                    "Swap detected  wallet=%s  tx=%s  %s → %s",
                    sender[:10], swap.tx_hash[:12],
                    swap.token_in[:10], swap.token_out[:10],
                )
                yield swap
