"""Funded testnet wallet — keys live client-side in ~/.arc-canteen/wallet.yaml.

The server mints the keypair (`cast wallet new` server-side) and funds it
with $5 of native testnet USDC from the faucet; the private key is
returned once and stored ONLY in wallet.yaml (0600). The server keeps
just the address (a `wallet_created` event), so losing wallet.yaml means
losing the wallet — a re-run of `arc-canteen wallet` provisions a fresh
one rather than recovering the old.
"""

from __future__ import annotations

import yaml
from datetime import datetime, timezone

import httpx

from . import config, paths
from .push import SERVER_URL

WALLET_FILE = paths.ARC_DIR / "wallet.yaml"
_TIMEOUT = 60  # the server waits for the funding tx to be mined

# Chain metadata for display. The server names the chain; id + explorer
# are client-side knowledge, like CHAIN_RPC_URLS in rpc.py.
CHAIN_INFO = {
    "testnet": {"chain_id": 5042002, "explorer": "https://testnet.arcscan.app"},
}


class WalletError(RuntimeError):
    pass


def load() -> dict | None:
    try:
        with open(WALLET_FILE) as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) and data.get("address") else None
    except (OSError, yaml.YAMLError):
        return None


def save(entry: dict) -> None:
    with paths.secure_open(WALLET_FILE) as f:
        yaml.dump(entry, f, default_flow_style=False, sort_keys=False)


def request_new() -> dict:
    """Ask the server for a fresh funded wallet and persist it locally.
    Returns the existing wallet instead if one is already on disk —
    never overwrites. Raises WalletError on any failure."""
    existing = load()
    if existing:
        return existing

    token = config.get("auth.server_token")
    if not token:
        raise WalletError("no server token — run `arc-canteen login` first")

    try:
        resp = httpx.post(
            f"{SERVER_URL}/wallet/new",
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT,
        )
    except (httpx.RequestError, httpx.TimeoutException) as e:
        raise WalletError(f"server unreachable: {e.__class__.__name__}")

    if resp.status_code != 200:
        try:
            detail = resp.json().get("error")
        except ValueError:
            detail = None
        raise WalletError(detail or f"server said {resp.status_code}")

    data = resp.json()
    if not data.get("address") or not data.get("private_key"):
        raise WalletError("malformed server response")

    entry = {
        "address": data["address"],
        "private_key": data["private_key"],
        "funded_tx": data.get("tx_hash"),
        "amount_usdc": data.get("amount_usdc"),
        "chain": data.get("chain", "testnet"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    save(entry)
    return entry
