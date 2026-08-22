"""
Tests for decoder.py — decoding Uniswap V2/V3 swap calldata.

Each test builds real calldata (selector + ABI-encoded payload) exactly as
it would appear on-chain, then asserts the decoded SwapInfo.
"""

from eth_abi import encode

from decoder import decode_transaction

# Router addresses recognised by the decoder (lowercase).
V3_ROUTER = "0x2626664c2603336e57b271c5c0b26f421741e481"
V2_ROUTER = "0x327df1e6de05895d2ab08513aadd9313fe505d86"

TOKEN_IN = "0x" + "11" * 20
TOKEN_OUT = "0x" + "22" * 20
MID_TOKEN = "0x" + "55" * 20
RECIPIENT = "0x" + "33" * 20
TRADER = "0x" + "44" * 20
TX_HASH = b"\xab" * 32


def _calldata(selector_hex: str, types: list[str], values: tuple) -> bytes:
    """selector (4 bytes) + ABI-encoded arguments."""
    return bytes.fromhex(selector_hex) + encode(types, values)


def test_decode_v3_exact_input_single():
    payload = _calldata(
        "04e45aaf",
        ["address", "address", "uint24", "address",
         "uint256", "uint256", "uint160"],
        (TOKEN_IN, TOKEN_OUT, 3000, RECIPIENT, 10 ** 18, 0, 0),
    )
    tx = {"to": V3_ROUTER, "from": TRADER, "hash": TX_HASH, "input": payload}

    swap = decode_transaction(tx)

    assert swap is not None
    assert swap.token_in == TOKEN_IN
    assert swap.token_out == TOKEN_OUT
    assert swap.amount_in == 10 ** 18
    assert swap.dex == "uniswap_v3"
    assert swap.is_eth_in is False
    assert swap.trader == TRADER


def test_decode_v3_exact_input_path():
    # path = tokenIn (20 bytes) | fee 3000 = 0x000bb8 | tokenOut (20 bytes)
    path = bytes.fromhex("11" * 20 + "000bb8" + "22" * 20)
    payload = _calldata(
        "b858183f",
        ["bytes", "address", "uint256", "uint256"],
        (path, RECIPIENT, 5 * 10 ** 17, 0),
    )
    tx = {"to": V3_ROUTER, "from": TRADER, "hash": TX_HASH, "input": payload}

    swap = decode_transaction(tx)

    assert swap is not None
    assert swap.token_in == TOKEN_IN
    assert swap.token_out == TOKEN_OUT
    assert swap.amount_in == 5 * 10 ** 17
    assert swap.dex == "uniswap_v3"


def test_decode_v2_tokens_for_tokens_multihop():
    path = [TOKEN_IN, MID_TOKEN, TOKEN_OUT]
    payload = _calldata(
        "38ed1739",
        ["uint256", "uint256", "address[]", "address", "uint256"],
        (7 * 10 ** 18, 0, path, RECIPIENT, 1_700_000_000),
    )
    tx = {"to": V2_ROUTER, "from": TRADER, "hash": TX_HASH, "input": payload}

    swap = decode_transaction(tx)

    assert swap is not None
    assert swap.token_in == TOKEN_IN     # first hop
    assert swap.token_out == TOKEN_OUT   # last hop
    assert swap.amount_in == 7 * 10 ** 18
    assert swap.dex == "uniswap_v2"
    assert swap.is_eth_in is False


def test_decode_v2_eth_for_tokens_uses_tx_value():
    path = [MID_TOKEN, TOKEN_OUT]
    payload = _calldata(
        "7ff36ab5",
        ["uint256", "address[]", "address", "uint256"],
        (0, path, RECIPIENT, 1_700_000_000),
    )
    tx = {
        "to": V2_ROUTER, "from": TRADER, "hash": TX_HASH,
        "input": payload, "value": 3 * 10 ** 18,
    }

    swap = decode_transaction(tx)

    assert swap is not None
    assert swap.is_eth_in is True
    assert swap.amount_in == 3 * 10 ** 18   # taken from tx value, not calldata
    assert swap.token_out == TOKEN_OUT


def test_non_router_target_returns_none():
    payload = _calldata(
        "04e45aaf",
        ["address", "address", "uint24", "address",
         "uint256", "uint256", "uint160"],
        (TOKEN_IN, TOKEN_OUT, 3000, RECIPIENT, 10 ** 18, 0, 0),
    )
    tx = {"to": "0x" + "99" * 20, "from": TRADER, "hash": TX_HASH, "input": payload}

    assert decode_transaction(tx) is None


def test_unknown_selector_returns_none():
    tx = {
        "to": V3_ROUTER, "from": TRADER, "hash": TX_HASH,
        "input": bytes.fromhex("deadbeef") + b"\x00" * 32,
    }

    assert decode_transaction(tx) is None


def test_truncated_input_returns_none():
    tx = {"to": V3_ROUTER, "from": TRADER, "hash": TX_HASH, "input": b"\x01\x02"}

    assert decode_transaction(tx) is None
