"""JSON-RPC client against the Arc proxy.

The same swrm_ token minted by `arc-canteen login` and stored at
`auth.server_token` in config.yaml authenticates here — both the CLI
server and the RPC proxy read the shared Postgres `sessions` table.

The chain → URL mapping lives in CHAIN_RPC_URLS. To add mainnet (or
any future chain), drop an entry in there and ALLOWED_CHAINS in
settings.py. No other code changes needed.
"""

from __future__ import annotations

import httpx

from . import config, settings
from . import errlog


CHAIN_RPC_URLS = {
    "testnet": "https://rpc.testnet.arc-node.thecanteenapp.com/",
    # mainnet endpoint not yet provisioned; calling on mainnet raises
    # RPCError("no RPC endpoint configured for chain 'mainnet'").
}


class RPCError(RuntimeError):
    """Auth, allowlist, rate-limit, or transport failure. Every raise
    also lands in ~/.arc-canteen/error.log."""

    def __init__(self, message: str):
        super().__init__(message)
        errlog.log("rpc", message)


def url_for_chain(chain: str | None = None, token: str | None = None) -> str | None:
    """Public JSON-RPC URL for `chain`. If `token` is given, embed it
    as the URL-based auth segment (/v1/<token>) — usable in viem,
    ethers, cast, web3.py with no extra headers. Returns None if the
    chain has no configured endpoint."""
    chain = chain or settings.load().get("chain", "testnet")
    base = CHAIN_RPC_URLS.get(chain)
    if not base:
        return None
    base = base.rstrip("/")
    return f"{base}/v1/{token}" if token else base + "/"


def call(method: str, params: list | None = None, timeout: float = 30.0) -> dict:
    """Make a JSON-RPC call. Returns the parsed response envelope
    (which may contain `result` or `error`). Raises RPCError on
    transport, auth, or non-200 HTTP failures — the caller maps those
    to user-friendly output."""
    token = config.get("auth.server_token")
    if not token:
        raise RPCError("not logged in; run 'arc-canteen login' first")

    chain = settings.load().get("chain", "testnet")
    url = CHAIN_RPC_URLS.get(chain)
    if not url:
        raise RPCError(f"no RPC endpoint configured for chain {chain!r}")

    payload = {"jsonrpc": "2.0", "method": method, "params": params or [], "id": 1}
    try:
        resp = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
    except (httpx.RequestError, httpx.TimeoutException) as e:
        raise RPCError(f"network error contacting {url}: {e}")

    if resp.status_code == 401:
        raise RPCError("token rejected; run 'arc-canteen login' again")
    if resp.status_code == 403:
        raise RPCError(f"method '{method}' not allowed by the proxy")
    if resp.status_code == 429:
        raise RPCError("rate limit exceeded; retry shortly")
    if resp.status_code == 502:
        raise RPCError("upstream node unavailable; try again in a moment")
    if resp.status_code != 200:
        raise RPCError(f"unexpected HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        return resp.json()
    except ValueError:
        raise RPCError("upstream returned non-JSON response")
