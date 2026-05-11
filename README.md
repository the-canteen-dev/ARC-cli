# arc-canteen CLI

Track your project's progress and submit to Arc.

## Installation

```bash
uv tool install git+https://github.com/the-canteen-dev/ARC-cli.git
```

This places the binary at `~/.local/bin/arc-canteen`.

## Upgrade

```bash
uv tool install --reinstall git+https://github.com/the-canteen-dev/ARC-cli.git
```

## Commands

### Top-level

| Command | Description |
|---|---|
| `arc-canteen` | Show your dashboard (default when no subcommand given) |
| `arc-canteen login` | Authenticate with GitHub and set up your profile |
| `arc-canteen logout` | Clear your local credentials |
| `arc-canteen status` | Show your dashboard |
| `arc-canteen push` | Push any queued local events to the server |
| `arc-canteen ls [traction\|product\|all]` | List all updates |
| `arc-canteen history [traction\|product\|all]` | Alias for `ls` |
| `arc-canteen profile-edit` | Shortcut for `arc-canteen profile edit` |
| `arc-canteen update-traction` | Shortcut for `arc-canteen update traction` |
| `arc-canteen update-product` | Shortcut for `arc-canteen update product` |
| `arc-canteen submit-puzzle` | Submit your answer to the current puzzle |
| `arc-canteen rpc <method> [params]` | JSON-RPC call to the configured Arc chain |

### `arc-canteen profile`

| Command | Description |
|---|---|
| `arc-canteen profile` | View your profile |
| `arc-canteen profile edit` | Edit your Discord handle, Telegram, and Luma email |

### `arc-canteen update`

| Command | Description |
|---|---|
| `arc-canteen update` | Show recent updates |
| `arc-canteen update traction` | Submit a traction update |
| `arc-canteen update product` | Submit a product update |

### JSON-RPC

Use `arc-canteen rpc <method> [params_json]` to make authenticated
JSON-RPC calls against the chain configured in `~/.arc-canteen/settings.yaml`.

```bash
arc-canteen rpc eth_blockNumber                          # → 0x27a766a
arc-canteen rpc eth_chainId                              # → 0x4cef52
arc-canteen rpc eth_getBalance '["0xabc...", "latest"]'  # → 0x1bc16d674...
arc-canteen rpc eth_call '[{"to":"0xabc","data":"0x70a08231"}, "latest"]'
arc-canteen rpc eth_sendRawTransaction '["0xf86c..."]'
arc-canteen rpc eth_blockNumber --raw                    # full envelope
```

The proxy enforces a method allowlist; calls to disallowed methods
return `method '<x>' not allowed by the proxy`.

## Local state

- `~/.arc-canteen/config.yaml` — auth + profile + cached updates
- `~/.arc-canteen/settings.yaml` — chain + event_name
- `~/.arc-canteen/queue.yaml` — append-only event queue (synced to server)

## Server

The CLI talks to `https://arc-cli-server.thecanteenapp.com`. The server is intentionally idempotent — re-sending an event that already landed is harmless, so the local queue can be replayed at any time.
