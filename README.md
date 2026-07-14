# arc-canteen CLI

The CLI for you to build your next great project on Arc!

More about Arc here: https://docs.arc.io/arc-chain

- Use `arc-canteen context | claude` in order to pipe App Kits, Arc Sample Apps and Arc / Circle docs into your build. More here: https://github.com/the-canteen-dev/context-arc
- Use our testnet RPC once you log in: https://arc-node.thecanteenapp.com/
- Make yourself known as an Arc + Canteen builder to the Circle + Canteen teams! This CLI enables Canteen, Arc and Circle to provide you prizes, support and (maybe even) investment as you keep on building!

Make sure you rotate your key regularly if you're having trouble logging in: `arc-canteen rotate-rpc-key`

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
| `arc-canteen rpc-url [--export]` | Print the JSON-RPC URL with your token embedded |
| `arc-canteen rotate-rpc-key` | Mint a fresh token, update `config.yaml` + `$RPC`, invalidate the old one |
| `arc-canteen shell-init` | Print rc snippet that auto-loads `$RPC` in every shell |
| `arc-canteen context` | Dump agent context (AGENTS.md + paths to docs and samples) |
| `arc-canteen context sync` | Clone/pull developer docs + samples from context-arc |

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

### Agent context

`arc-canteen context sync` clones [the-canteen-dev/context-arc](https://github.com/the-canteen-dev/context-arc) into `~/.arc-canteen/context/`. That repo bundles developer docs for Arc + Circle plus 5 sample codebases (as submodules). Subsequent `sync` invocations `git pull --recurse-submodules`.

`arc-canteen context` prints `AGENTS.md` plus a flat path manifest — pipe-friendly:

```bash
arc-canteen context | claude          # or aider / cody / cursor
arc-canteen context --paths           # just the paths, no entry-point content
arc-canteen context --full            # also inline every .md / .yaml
```

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

### Auto-load $RPC

`arc-canteen login` writes `export RPC='<url>'` to `~/.arc-canteen/env`. To make every new shell pick it up, install one line:

```bash
arc-canteen shell-init >> ~/.bashrc      # or ~/.zshrc
```

After that, `$RPC` is set in every shell with no per-session step.

### Rotating your key

```bash
arc-canteen rotate-rpc-key
```

Mints a fresh token (re-auth via your stored GitHub credential — no browser
step), rewrites `~/.arc-canteen/config.yaml` and `~/.arc-canteen/env`, and
invalidates the old token server-side. Update anything that hard-coded the
old URL (a project `.env`, a CI secret) with the new one.

Tokens are good for 90 days; the dashboard nudges you as that approaches.
`arc-canteen logout` also invalidates the token server-side.

## Local state

- `~/.arc-canteen/config.yaml` — auth (token + when it was issued) + profile + cached updates
- `~/.arc-canteen/settings.yaml` — chain + event_name
- `~/.arc-canteen/queue.yaml` — append-only event queue (synced to server)
- `~/.arc-canteen/env` — `export RPC='…'`; sourced by your shell rc

## Server

The CLI talks to `https://arc-cli-server.thecanteenapp.com`. The server is intentionally idempotent — re-sending an event that already landed is harmless, so the local queue can be replayed at any time.
