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

## Local state

- `~/.arc-canteen/config.yaml` — auth + profile + cached updates
- `~/.arc-canteen/queue.yaml` — append-only event queue (synced to server)

## Server

The CLI talks to `https://arc-cli-server.thecanteenapp.com`. The server is intentionally idempotent — re-sending an event that already landed is harmless, so the local queue can be replayed at any time.
