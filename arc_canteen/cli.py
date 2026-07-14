"""arc-canteen CLI - main entry point."""

from __future__ import annotations

import re
import subprocess
import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from typing import Annotated, Optional
from datetime import datetime, timedelta, timezone
from importlib import metadata
from pathlib import Path

import json as _json

from . import config, auth, upgrade, paths
from . import push as _push
from . import rpc as _rpc
from . import context as _context

# Sourceable env file. login() writes `export RPC=…` here; users add
# `[ -f ~/.arc-canteen/env ] && . ~/.arc-canteen/env` to their shell rc.
_SHELL_ENV_FILE = paths.ARC_DIR / "env"

# RPC/server token lifetime. The server doesn't enforce expiry yet, so
# this is a client-side policy: the dashboard nudges as a token ages and
# `arc-canteen rotate-rpc-key` mints a fresh one.
RPC_KEY_MAX_AGE_DAYS = 90
RPC_KEY_NUDGE_AFTER_DAYS = RPC_KEY_MAX_AGE_DAYS - 14  # start nudging ~2 weeks out


def _write_shell_env(rpc_url: str) -> None:
    paths.secure_write_text(_SHELL_ENV_FILE, f"export RPC='{rpc_url}'\n")


def _rpc_key_age() -> Optional[timedelta]:
    """Age of the current server/RPC token, or None if we have no record
    of when it was issued (logged in with an older CLI build, say)."""
    issued = config.get("auth.server_token_issued_at")
    if not issued:
        return None
    try:
        dt = datetime.fromisoformat(str(issued))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - dt

app = typer.Typer(
    name="arc-canteen",
    help="arc-canteen CLI — track your project's progress and submit to Arc.",
    no_args_is_help=False,
    add_completion=True,
)

profile_app = typer.Typer(help="View and edit your profile.", no_args_is_help=False)
update_app = typer.Typer(help="Submit traction and product updates.", no_args_is_help=False)
context_app = typer.Typer(
    help="Developer docs + sample codebases for Arc + Circle, for agent context.",
    no_args_is_help=False,
)

app.add_typer(profile_app, name="profile")
app.add_typer(update_app, name="update")
app.add_typer(context_app, name="context")

console = Console()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_login() -> None:
    if not config.is_logged_in():
        console.print("[red]Not logged in.[/red] Run [bold cyan]arc-canteen login[/bold cyan] first.")
        raise typer.Exit(1)


def _require_discord() -> None:
    if not config.has_discord():
        console.print(
            "[yellow]Discord handle required.[/yellow] "
            "Run [bold cyan]arc-canteen profile edit[/bold cyan]"
        )
        raise typer.Exit(1)


def _fmt_date(date_str) -> str:
    if not date_str:
        return "unknown"
    try:
        if isinstance(date_str, datetime):
            dt = date_str
        else:
            dt = datetime.fromisoformat(str(date_str))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        if delta.total_seconds() < 0:
            return dt.strftime("%b %d, %Y")
        days = delta.days
        if days == 0:
            h = delta.seconds // 3600
            m = (delta.seconds % 3600) // 60
            s = delta.seconds % 60
            if h == 0 and m == 0:
                return "just now" if s < 5 else f"{s}s ago"
            if h == 0:
                return f"{m}m ago"
            return f"{h}h ago"
        if days == 1:
            return "yesterday"
        if days < 7:
            return f"{days} days ago"
        if days < 30:
            w = days // 7
            return f"{w} week{'s' if w > 1 else ''} ago"
        return dt.strftime("%b %d, %Y")
    except Exception:
        return str(date_str)


def _get_multiline_text(hint: str) -> Optional[str]:
    """Collect multiline input inline. Empty line to finish."""
    console.print(f"[dim]{hint}[/dim]")
    console.print("[dim](Empty line to finish)[/dim]\n")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "":
            if lines:
                break
        else:
            lines.append(line)
    return "\n".join(lines).strip() or None


def _recent_updates(n: int = 5) -> list[dict]:
    """Return the n most recent updates across both traction and product."""
    traction = [{"kind": "traction", **u} for u in (config.get("updates.traction") or [])]
    product = [{"kind": "product", **u} for u in (config.get("updates.product") or [])]
    combined = sorted(traction + product, key=lambda u: u.get("date", ""), reverse=True)
    return combined[:n]


def _print_updates(updates: list[dict], full: bool = False) -> None:
    """Print a list of update dicts."""
    if not updates:
        console.print("[dim]No updates yet.[/dim]")
        return
    for u in updates:
        kind = u.get("kind", "")
        kind_label = "[cyan]product[/cyan]" if kind == "product" else "[green]traction[/green]"
        date_label = f"[dim]{_fmt_date(u.get('date'))}[/dim]"
        console.print(f"\n  {kind_label}  {date_label}")
        text = u.get("text", "")
        if text:
            snippet = text if (full or len(text) <= 120) else text[:120] + "…"
            console.print(f"  {snippet}")


# ---------------------------------------------------------------------------
# --version
# ---------------------------------------------------------------------------

def _cli_version() -> str:
    """Version from pyproject.toml when running out of a source checkout
    (it may be newer than what's installed); installed metadata otherwise."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(), re.M)
        if m:
            return m.group(1)
    except OSError:
        pass
    try:
        return metadata.version("arc-canteen")
    except metadata.PackageNotFoundError:
        return "unknown"


def _cli_commit() -> str:
    """Commit hash of the running code. In a source checkout (incl.
    editable installs) ask git — a leftover _build_info.py from an old
    build would lie. Installed copies use the hash baked in by
    hatch_build.py; a wheel built without git (sdist) → 'unknown'."""
    pkg_dir = Path(__file__).resolve().parent
    if (pkg_dir.parent / "pyproject.toml").exists():
        try:
            out = subprocess.run(
                ["git", "-C", str(pkg_dir), "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=2,
            )
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        from ._build_info import COMMIT  # baked by hatch_build.py; not in git
        if COMMIT:
            return COMMIT
    except ImportError:
        pass
    return "unknown"


def _version_callback(value: bool) -> None:
    if not value:
        return
    # Plain print so it's clean for scripts/$(...).
    print(f"arc-canteen {_cli_version()} (commit {_cli_commit()})")
    raise typer.Exit()


# ---------------------------------------------------------------------------
# Root — no subcommand → show status
# ---------------------------------------------------------------------------

@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option(
        "--version",
        help="Print the CLI version and commit hash, then exit.",
        callback=_version_callback,
        is_eager=True,
    )] = False,
) -> None:
    paths.ensure_dir()  # tighten ~/.arc-canteen to 0700 even if it predates this
    upgrade.maybe_print_upgrade_notice()
    if config.is_logged_in():
        if not _push.ping():
            console.print("[yellow](server unreachable — changes will sync when it's back up)[/yellow]")
        else:
            _push.drain_queue()
            pending = _push.pending_count()
            if pending:
                reason = f" ({_push.last_push_error})" if _push.last_push_error else ""
                console.print(
                    f"[yellow]{pending} event{'s' if pending != 1 else ''} queued, not yet pushed{reason} — "
                    f"run [bold]arc-canteen push[/bold] to retry.[/yellow]"
                )
    if ctx.invoked_subcommand is None:
        if not config.is_logged_in():
            console.print(Panel(
                "[bold]Welcome to arc-canteen CLI[/bold]\n\n"
                "Run [bold cyan]arc-canteen login[/bold cyan] to get started.",
                border_style="cyan",
                padding=(1, 2),
            ))
        else:
            _show_dashboard()


# ---------------------------------------------------------------------------
# arc-canteen login / logout
# ---------------------------------------------------------------------------

@app.command()
def login() -> None:
    """Authenticate with GitHub and set up your profile."""
    if config.is_logged_in():
        handle = config.get("auth.github_handle")
        console.print(f"Already logged in as [bold]@{handle}[/bold].")
        if not typer.confirm("Re-authenticate?", default=False):
            return

    console.print("\n[bold cyan]Logging in with GitHub...[/bold cyan]")
    try:
        token_data = auth.device_flow_login()
        token = token_data["access_token"]
        with console.status("Fetching your GitHub profile..."):
            user = auth.get_github_user(token)
    except RuntimeError as e:
        console.print(f"[red]Login failed:[/red] {e}")
        raise typer.Exit(1)

    cfg = config.load()
    cfg.setdefault("auth", {})
    cfg["auth"]["github_token"] = token
    cfg["auth"]["github_handle"] = user["login"]
    cfg["auth"]["github_name"] = user.get("name") or user["login"]
    config.save(cfg)

    console.print(f"\n[bold green]Logged in as @{user['login']}[/bold green]")

    with console.status("[dim]Registering with arc-canteen server...[/dim]"):
        ok = _push.cli_login(token)
    if not ok:
        console.print("[dim]Could not reach arc-canteen server — will retry later.[/dim]")

    _push.push_event("login", {"github_username": user["login"]})

    # Profile setup
    console.print("\n[bold]Complete your profile[/bold]\n")

    # Discord (required)
    discord = typer.prompt("Discord handle").strip().lstrip("@")
    while not discord:
        console.print("[red]Discord handle cannot be empty.[/red]")
        discord = typer.prompt("Discord handle").strip().lstrip("@")
    config.set_val("profile.discord", discord)

    # Telegram
    telegram = typer.prompt("Telegram handle", default="").strip().lstrip("@")
    if telegram:
        config.set_val("profile.telegram", f"@{telegram}")

    # Email (stored/synced as luma_email — the server expects that key)
    console.print(
        "\n[dim]If you joined through a Luma invite, enter the same email you used there.[/dim]\n"
        "[dim]Make sure it's accurate — we use it to reach you about grants, awards, and other support.[/dim]"
    )
    while True:
        luma_email = typer.prompt("Email", default="").strip()
        if not luma_email or ("@" in luma_email and "." in luma_email.split("@")[-1]):
            break
        console.print("[red]Please enter a valid email address.[/red]")
    if luma_email:
        config.set_val("profile.luma_email", luma_email)

    _push.push_event("profile_edit", {
        "discord": config.get("profile.discord"),
        "telegram": config.get("profile.telegram"),
        "luma_email": config.get("profile.luma_email"),
    })

    # Persist `export RPC=…` to a sourceable env file before the
    # quickstart panel — that way the user can act on the instructions
    # without re-running anything.
    _server_token = config.get("auth.server_token")
    if _server_token:
        _rpc_url = _rpc.url_for_chain(token=_server_token)
        if _rpc_url:
            _write_shell_env(_rpc_url)

    # Surface the JSON-RPC URL prominently — this is what users
    # actually want to do with the CLI session beyond telemetry.
    _show_rpc_quickstart()

    console.print(
        "\nRun [bold cyan]arc-canteen status[/bold cyan] to see your dashboard, "
        "or [bold cyan]arc-canteen --help[/bold cyan] to explore commands."
    )


def _show_rpc_quickstart() -> None:
    """Print the JSON-RPC URL + show how to get it into the user's
    shell. Login already wrote `export RPC=…` to ~/.arc-canteen/env;
    the user either sources it for this shell, or adds one line to
    their rc so every new shell picks it up automatically."""
    token = config.get("auth.server_token")
    if not token:
        return
    url = _rpc.url_for_chain(token=token)
    if not url:
        return

    console.print()
    console.print(Panel(
        f"[bold]{url}[/bold]",
        title="[bold cyan]Your JSON-RPC endpoint[/bold cyan]",
        border_style="cyan",
        padding=(0, 1),
    ))
    console.print()
    console.print(f"[bold]Saved to[/bold] [cyan]~/.arc-canteen/env[/cyan] ([dim]export RPC=…[/dim])")
    console.print()
    console.print("[bold]For this shell:[/bold]")
    console.print("  [cyan]source ~/.arc-canteen/env[/cyan]")
    console.print()
    console.print("[bold]For every new shell[/bold] [dim]— add one line to ~/.bashrc or ~/.zshrc:[/dim]")
    console.print("  [cyan][ -f ~/.arc-canteen/env ] && . ~/.arc-canteen/env[/cyan]")
    console.print(f"  [dim]# or: [/dim][cyan]arc-canteen shell-init >> ~/.bashrc[/cyan]")
    console.print()
    console.print("[bold]Then $RPC is set. Try it:[/bold]")
    console.print("  [cyan]cast block-number --rpc-url $RPC[/cyan]              [dim]# foundry[/dim]")
    console.print("  [cyan]cast chain-id      --rpc-url $RPC[/cyan]")
    console.print()
    console.print("[bold]In code:[/bold]")
    console.print("  [cyan]http(process.env.RPC)[/cyan]                                                [dim]# viem[/dim]")
    console.print("  [cyan]new JsonRpcProvider(process.env.RPC)[/cyan]                                 [dim]# ethers v6[/dim]")
    console.print("  [cyan]Web3(Web3.HTTPProvider(os.environ['RPC']))[/cyan]                           [dim]# web3.py[/dim]")


@app.command("rpc-url")
def rpc_url(
    export: Annotated[bool, typer.Option("--export", help="Print `export RPC=<url>` for `eval $(arc-canteen rpc-url --export)`.")] = False,
) -> None:
    """Print the JSON-RPC URL for the current chain with your server token embedded.

    Plain:    RPC=$(arc-canteen rpc-url)
    Eval:     eval $(arc-canteen rpc-url --export)
    """
    _require_login()
    token = config.get("auth.server_token")
    if not token:
        console.print("[red]No server token; run `arc-canteen login` first.[/red]")
        raise typer.Exit(1)
    url = _rpc.url_for_chain(token=token)
    if not url:
        from . import settings as _settings
        chain = _settings.load().get("chain", "testnet")
        console.print(f"[red]No RPC endpoint configured for chain {chain!r}[/red]")
        raise typer.Exit(1)
    # Plain print so it's clean for $(...) or eval $(...).
    print(f"export RPC='{url}'" if export else url)


@app.command("shell-init")
def shell_init() -> None:
    """Print the rc snippet that auto-loads $RPC in every new shell.

    One-time install:
        arc-canteen shell-init >> ~/.bashrc      # bash
        arc-canteen shell-init >> ~/.zshrc       # zsh
    """
    print('[ -f ~/.arc-canteen/env ] && . ~/.arc-canteen/env')


@app.command("rotate-rpc-key")
def rotate_rpc_key() -> None:
    """Mint a fresh RPC/server token and update your local config + $RPC.

    Re-authenticates against the arc-canteen server with your stored
    GitHub credential — no browser step — replaces the token in
    ~/.arc-canteen/config.yaml and ~/.arc-canteen/env, and (server
    permitting) invalidates the previous one. Anything that hard-coded
    the *old* URL — a project .env, a CI secret — needs the new one.
    """
    _require_login()
    github_token = config.get("auth.github_token")
    if not github_token:
        console.print("[red]No stored GitHub credential; run [bold cyan]arc-canteen login[/bold cyan] first.[/red]")
        raise typer.Exit(1)

    if not _push.ping():
        console.print("[yellow]arc-canteen server unreachable — try again when it's back up.[/yellow]")
        raise typer.Exit(1)

    old_token = config.get("auth.server_token")
    with console.status("[dim]rotating your RPC key...[/dim]"):
        ok = _push.cli_login(github_token)
    if not ok:
        console.print("[red]Rotation failed — the server didn't issue a new token.[/red]")
        raise typer.Exit(1)

    new_token = config.get("auth.server_token")
    if not new_token or new_token == old_token:
        console.print("[yellow]Server returned no new token; nothing changed.[/yellow]")
        return

    new_url = _rpc.url_for_chain(token=new_token)
    if new_url:
        _write_shell_env(new_url)

    console.print("[green]Rotated.[/green] A fresh token is in [cyan]~/.arc-canteen/config.yaml[/cyan].")
    if new_url:
        console.print()
        console.print(Panel(
            f"[bold]{new_url}[/bold]",
            title="[bold cyan]New JSON-RPC endpoint[/bold cyan]",
            border_style="cyan",
            padding=(0, 1),
        ))
        console.print()
        console.print("  [dim]Updated[/dim] [cyan]~/.arc-canteen/env[/cyan] — run [cyan]source ~/.arc-canteen/env[/cyan] for this shell.")
    console.print("  [dim]Anything that hard-coded the old URL (a project .env, a CI secret) needs the new one.[/dim]")
    console.print("  [dim]The previous token is invalidated server-side (a ~1 min auth cache means it may linger briefly).[/dim]")


@app.command()
def logout() -> None:
    """Clear your local credentials."""
    if not config.is_logged_in():
        console.print("[dim]Not logged in.[/dim]")
        return
    handle = config.get("auth.github_handle")
    if typer.confirm(f"Log out @{handle}?", default=True):
        _push.push_event("logout", {"github_username": handle})
        # Best-effort: invalidate the token server-side too, so a copy of
        # the old $RPC URL stops working. Offline (or an older server
        # without /auth/logout) just means it ages out instead.
        _push.server_logout()
        cfg = config.load()
        cfg.pop("auth", None)
        config.save(cfg)
        # Stale $RPC env file would still authenticate against a revoked
        # token, so wipe it on logout.
        try:
            _SHELL_ENV_FILE.unlink()
        except FileNotFoundError:
            pass
        console.print("[dim]Logged out.[/dim]")


# ---------------------------------------------------------------------------
# arc-canteen push
# ---------------------------------------------------------------------------

@app.command()
def push() -> None:
    """Push any queued local events to the arc-canteen server."""
    _require_login()
    if not _push.ping():
        console.print("[yellow]Server unreachable.[/yellow]")
        return
    count = _push.pending_count()
    if count == 0:
        console.print("[dim]Nothing to push.[/dim]")
        return
    console.print(f"Pushing [bold]{count}[/bold] queued event{'s' if count != 1 else ''}...")
    _push.drain_queue()
    remaining = _push.pending_count()
    if remaining == 0:
        console.print("[green]All events pushed.[/green]")
    else:
        reason = f" — {_push.last_push_error}" if _push.last_push_error else ""
        console.print(f"[yellow]{remaining} still pending{reason}.[/yellow]")


# ---------------------------------------------------------------------------
# arc-canteen context — agent-facing docs + sample codebases
# ---------------------------------------------------------------------------

@context_app.callback(invoke_without_command=True)
def _context_root(
    ctx: typer.Context,
    paths: Annotated[bool, typer.Option("--paths", help="Print only the file/path manifest, no AGENTS.md content.")] = False,
    full: Annotated[bool, typer.Option("--full", help="Inline the contents of every doc file (not just paths). Big output; useful for stateless agent runtimes.")] = False,
) -> None:
    """Dump agent context: AGENTS.md + paths to all docs and samples.

    Run `arc-canteen context sync` first to fetch the bundle from
    github.com/the-canteen-dev/context-arc.

    The default output gives an agent the entry-point doc plus a flat
    manifest of every available file — the agent can then read specific
    files off disk on demand. Use --full to inline doc content too.
    """
    if ctx.invoked_subcommand is not None:
        return

    if not _context.is_synced():
        console.print(
            f"[yellow]context not synced.[/yellow]\n"
            f"Run [bold cyan]arc-canteen context sync[/bold cyan] first."
        )
        raise typer.Exit(1)

    # Use plain print() so output is pipe-friendly (no Rich markup escaping
    # by the terminal width or by --no-color terminals).
    if not paths:
        entry = _context.read_entry()
        if entry:
            print("# AGENTS.md")
            print()
            print(entry)
            print()

    print(f"# Files available in {_context.CONTEXT_DIR}")
    print()
    for p in _context.list_paths():
        print(p)

    if full:
        print()
        print("# Doc contents")
        for rel, content in _context.iter_doc_contents():
            print()
            print(f"## {rel}")
            print()
            print(content)


@context_app.command("sync")
def context_sync() -> None:
    """Clone or pull the context-arc repo (with submodules) into ~/.arc-canteen/context/."""
    with console.status("[dim]syncing developer docs and samples...[/dim]"):
        try:
            _context.sync()
        except _context.ContextError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
    console.print(f"[green]Synced to[/green] [bold]{_context.CONTEXT_DIR}[/bold]")


# ---------------------------------------------------------------------------
# arc-canteen rpc — JSON-RPC against the configured Arc chain
# ---------------------------------------------------------------------------

@app.command()
def rpc(
    method: Annotated[str, typer.Argument(help="JSON-RPC method, e.g. eth_blockNumber")],
    params: Annotated[str, typer.Argument(help="JSON array of params, e.g. '[\"latest\"]'")] = "[]",
    raw: Annotated[bool, typer.Option("--raw", help="Print the full JSON-RPC envelope instead of just `result`.")] = False,
) -> None:
    """Make a JSON-RPC call to the configured Arc chain.

    Examples:
        arc-canteen rpc eth_blockNumber
        arc-canteen rpc eth_chainId
        arc-canteen rpc eth_getBalance '["0xabc...", "latest"]'
        arc-canteen rpc eth_sendRawTransaction '["0xf86c..."]'
    """
    _require_login()

    try:
        parsed = _json.loads(params)
    except _json.JSONDecodeError:
        console.print(f"[red]params must be valid JSON; got:[/red] {params!r}")
        raise typer.Exit(2)
    if not isinstance(parsed, list):
        console.print("[red]params must be a JSON array[/red]")
        raise typer.Exit(2)

    try:
        resp = _rpc.call(method, parsed)
    except _rpc.RPCError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    if raw:
        console.print_json(_json.dumps(resp))
        return

    if "error" in resp:
        e = resp["error"]
        console.print(f"[red]RPC error {e.get('code')}:[/red] {e.get('message')}")
        raise typer.Exit(1)

    result = resp.get("result")
    if isinstance(result, (dict, list)):
        console.print_json(_json.dumps(result))
    elif result is None:
        console.print("null")
    else:
        # bare string / number / bool: print on its own line, no quoting
        console.print(result)


# ---------------------------------------------------------------------------
# arc-canteen status
# ---------------------------------------------------------------------------

@app.command()
def status() -> None:
    """Show your arc-canteen dashboard."""
    _require_login()
    _show_dashboard()


def _show_dashboard() -> None:
    handle = config.get("auth.github_handle")
    name = config.get("auth.github_name")
    discord = config.get("profile.discord")
    telegram = config.get("profile.telegram")
    luma_email = config.get("profile.luma_email")
    title = f"[bold cyan]@{handle}[/bold cyan]"
    if name and name != handle:
        title += f"  [dim]{name}[/dim]"

    lines: list[str] = []

    # ── Profile ──
    lines.append(f"  [dim]GitHub[/dim]    @{handle}")
    lines.append(f"  [dim]Discord[/dim]   " + (f"@{discord}" if discord else "[red]not set[/red]"))
    lines.append(f"  [dim]Telegram[/dim]  " + (telegram if telegram else "[red]not set[/red]"))
    lines.append(f"  [dim]Email[/dim]     " + (luma_email if luma_email else "[red]not set[/red]"))
    lines.append("")

    # ── RPC URL ── (~85 chars, would wrap awkwardly into the column
    # alignment above; emit as label + value on two indented lines)
    server_token = config.get("auth.server_token")
    if server_token:
        rpc_url = _rpc.url_for_chain(token=server_token)
        if rpc_url:
            lines.append(f"  [dim]RPC[/dim]")
            lines.append(f"  [bold cyan]{rpc_url}[/bold cyan]")
            age = _rpc_key_age()
            if age is None:
                lines.append(
                    "  [yellow]↻ key age unknown — run [bold]arc-canteen rotate-rpc-key[/bold] "
                    "to refresh it and start tracking[/yellow]"
                )
            elif age.days >= RPC_KEY_MAX_AGE_DAYS:
                lines.append(
                    f"  [bold red]↻ this key is {age.days}d old (past the {RPC_KEY_MAX_AGE_DAYS}d limit) — "
                    f"run [bold]arc-canteen rotate-rpc-key[/bold] now[/bold red]"
                )
            elif age.days >= RPC_KEY_NUDGE_AFTER_DAYS:
                left = RPC_KEY_MAX_AGE_DAYS - age.days
                lines.append(
                    f"  [yellow]↻ this key is {age.days}d old — rotate within ~{left}d: "
                    f"[bold]arc-canteen rotate-rpc-key[/bold][/yellow]"
                )
            lines.append("")

    # ── Recent updates ──
    recent = _recent_updates(5)
    if recent:
        lines.append("  [dim]Recent updates[/dim]")
        for u in recent:
            kind = u.get("kind", "")
            kind_label = "[cyan]product[/cyan] " if kind == "product" else "[green]traction[/green]"
            date_label = f"[dim]{_fmt_date(u.get('date'))}[/dim]"
            text = u.get("text", "")
            snippet = (text[:80] + "…") if len(text) > 80 else text
            lines.append(f"  {kind_label}  {date_label}  {snippet}")

    lines.append("")

    # ── Call to action ──
    traction_updates = config.get("updates.traction") or []
    product_updates = config.get("updates.product") or []

    def _last_date(updates: list) -> Optional[str]:
        if not updates:
            return None
        return max(u.get("date", "") for u in updates) or None

    last_traction = _last_date(traction_updates)
    last_product  = _last_date(product_updates)

    strong = "[bold magenta]=>[/bold magenta]"
    weak   = "[yellow]->[/yellow]"

    if not telegram or not luma_email:
        lines.append(f"  {strong} Run [bold]arc-canteen profile-edit[/bold] to complete your profile")

    # Showcase — nudge until a first submission exists locally.
    if not SHOWCASE_FILE.exists():
        lines.append(f"  {weak} Run [bold]arc-canteen submit-showcase[/bold] to submit your project for the Arc Showcase  [dim](criteria: https://arc-oss.thecanteenapp.com/)[/dim]")

    # Agent context — one persistent line (state-aware, examples inline).
    if _context.is_synced():
        lines.append(f"  {weak} [bold]arc-canteen context | <agent>[/bold] to pipe Arc + Circle docs + samples (arc-commerce, arc-escrow, …)")
    else:
        lines.append(f"  {strong} Run [bold]arc-canteen context sync[/bold] for Arc + Circle docs + 5 sample codebases (arc-commerce, arc-escrow, …)")

    # Traction + product updates always at the bottom of the panel.
    if not last_traction:
        lines.append(f"  {strong} Run [bold]arc-canteen update-traction[/bold] to log users you've talked to or onboarded  [dim](no traction updates yet)[/dim]")
    else:
        lines.append(f"  {weak} Run [bold]arc-canteen update-traction[/bold] to log users you've talked to or onboarded  [dim](last update {_fmt_date(last_traction)})[/dim]")

    if not last_product:
        lines.append(f"  {strong} Run [bold]arc-canteen update-product[/bold] to share feature and product updates  [dim](no product updates yet)[/dim]")
    else:
        lines.append(f"  {weak} Run [bold]arc-canteen update-product[/bold] to share feature and product updates  [dim](last update {_fmt_date(last_product)})[/dim]")

    console.print(Panel("\n".join(lines), title=title, border_style="cyan", padding=(0, 1)))


# ---------------------------------------------------------------------------
# arc-canteen ls / history
# ---------------------------------------------------------------------------

@app.command("ls")
def ls(
    kind: Annotated[str, typer.Argument(help="traction | product | all")] = "all",
) -> None:
    """List all updates (traction and product)."""
    _require_login()
    _print_all_updates(kind, full=True)


@app.command("history")
def history(
    kind: Annotated[str, typer.Argument(help="traction | product | all")] = "all",
) -> None:
    """List all updates (alias for arc-canteen ls)."""
    _require_login()
    _print_all_updates(kind, full=True)


@app.command("profile-edit")
def profile_edit_shortcut() -> None:
    """Shortcut for arc-canteen profile edit."""
    _require_login()
    profile_edit()


@app.command("update-traction")
def update_traction_shortcut() -> None:
    """Shortcut for arc-canteen update traction."""
    _require_login()
    _require_discord()
    update_traction()


@app.command("update-product")
def update_product_shortcut() -> None:
    """Shortcut for arc-canteen update product."""
    _require_login()
    _require_discord()
    update_product()


@app.command("submit-puzzle")
def submit_puzzle() -> None:
    """Submit your answer to the current puzzle."""
    _require_login()
    _require_discord()

    console.print("[bold]Puzzle Submission[/bold]")
    console.print("[dim]Enter your answer below. Empty line to finish.[/dim]\n")

    text = _get_multiline_text("Your answer")
    if not text:
        console.print("[yellow]No answer submitted.[/yellow]")
        return

    puzzles: list = config.get("puzzles") or []
    puzzles.append({"date": datetime.now(timezone.utc).isoformat(), "text": text})
    config.set_val("puzzles", puzzles)

    _push.push_event("submit_puzzle", {"text": text})

    console.print("\n[green]Puzzle answer submitted.[/green]")


# ---------------------------------------------------------------------------
# arc-canteen submit-showcase
# ---------------------------------------------------------------------------

SHOWCASE_FILE = paths.ARC_DIR / "showcase.yaml"

_SHOWCASE_QUESTION = (
    "Why should we choose your project? What primitives are you exposing "
    "that other builders could find useful?\n"
    "Compared to the code out there for Arc builders (mostly in "
    "circlefin/arc-* repos), what tools and flows do you add?"
)


def _load_showcase() -> dict:
    """Prior showcase entry, so re-submissions start pre-filled."""
    try:
        with open(SHOWCASE_FILE) as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def _save_showcase(entry: dict) -> None:
    try:
        with paths.secure_open(SHOWCASE_FILE) as f:
            yaml.dump(entry, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    except OSError:
        pass


def _prompt_url(label: str, default: str, github: bool, optional: bool = False) -> str:
    """Prompt for a URL; loops until valid. Optional fields accept empty
    (and '-' to clear a pre-filled value). Bare domains get https://."""
    while True:
        val = typer.prompt(label, default=default).strip()
        if val == "-" and optional:
            val = ""
        if optional and not val:
            return ""
        if github and "github.com/" not in val:
            console.print("[red]Please enter a GitHub URL (github.com/...).[/red]")
            continue
        if not github and "." not in val:
            console.print("[red]Please enter a URL.[/red]")
            continue
        if not val.startswith(("http://", "https://")):
            val = f"https://{val}"
        return val


@app.command("submit-showcase")
def submit_showcase() -> None:
    """Submit your project as a candidate for the Arc Showcase."""
    _require_login()
    _require_discord()

    prior = _load_showcase()

    console.print(Panel(
        "[bold]Arc Showcase[/bold] — [cyan]https://arc-showcase.thecanteenapp.com/[/cyan]\n\n"
        "Submit your project as a candidate for the showcase. To be featured,\n"
        "you must commit to:\n\n"
        "  • [bold]Stay open source[/bold] — keep your code open, now and going forward.\n"
        "  • [bold]Expose useful primitives[/bold] — building blocks other Arc builders can\n"
        "    pick up (see arc-commerce / arc-p2p-payments for the shape of this).\n"
        "  • [bold]Document it clearly[/bold] — write down how your code works and how to\n"
        "    use it, so a builder can get going without reading every line.\n\n"
        "  • [bold]Bonus: a standalone infra-focused repo[/bold] — a separate repo holding\n"
        "    just the reusable building blocks, so builders can fork it without\n"
        "    the rest of your product. For example, Bagwork-fun/Bagwork is a\n"
        "    main repo, and Bagwork-fun/arc-plugins is its standalone repo.\n\n"
        "Read more about the criteria at [cyan]https://arc-oss.thecanteenapp.com/[/cyan]",
        border_style="cyan",
        padding=(1, 2),
    ))
    if prior:
        console.print("[dim]Pre-filled from your previous submission — edit as needed.[/dim]")
    console.print()

    repo = _prompt_url("GitHub link to the main repo", prior.get("repo") or "", github=True)
    live_site = _prompt_url("Link to the live site", prior.get("live_site") or "", github=False)
    standalone = _prompt_url(
        "Standalone infra-focused repo (optional, '-' to clear)",
        prior.get("standalone_repo") or "", github=True, optional=True,
    )

    console.print(f"\n[bold]{_SHOWCASE_QUESTION}[/bold]\n")
    prior_pitch = (prior.get("pitch") or "").strip()
    if prior_pitch:
        console.print(f"[dim]Previous answer:[/dim]\n{prior_pitch}\n")
        console.print("[dim](Type a new answer — empty line to finish — or press Enter right away to keep the previous one)[/dim]\n")
    else:
        console.print("[dim](Empty line to finish)[/dim]\n")

    # Like _get_multiline_text, but an immediate empty line means
    # "keep the previous answer" when one exists.
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "":
            if lines or prior_pitch:
                break
        else:
            lines.append(line)
    pitch = "\n".join(lines).strip() or prior_pitch
    if not pitch:
        console.print("[yellow]No answer given — submission cancelled.[/yellow]")
        raise typer.Exit(1)

    console.print()
    if not typer.confirm(
        "I affirm the live site will remain live",
        default=bool(prior.get("live_site_will_remain_live")),
    ):
        console.print("[yellow]The showcase requires a live site that stays live — submission cancelled.[/yellow]")
        raise typer.Exit(1)
    if not typer.confirm(
        "I affirm the repo(s) will remain open source",
        default=bool(prior.get("repos_will_remain_open")),
    ):
        console.print("[yellow]The showcase requires the code to stay open — submission cancelled.[/yellow]")
        raise typer.Exit(1)

    entry = {
        "repo": repo,
        "live_site": live_site,
        "standalone_repo": standalone or None,
        "live_site_will_remain_live": True,
        "repos_will_remain_open": True,
        "pitch": pitch,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }

    # The server keeps a single append-only event log, so every
    # (re)submission sends the complete entry as one fresh event.
    # The commitment booleans stay client-side: submission is cancelled
    # unless both are affirmed, so they're always true by construction.
    _push.push_event("submit_showcase", {
        k: v for k, v in entry.items()
        if k not in ("submitted_at", "live_site_will_remain_live", "repos_will_remain_open")
    })
    _save_showcase(entry)

    console.print("\n[green]Showcase submission sent.[/green]")
    console.print(
        f"[dim]Saved to[/dim] [cyan]{SHOWCASE_FILE}[/cyan][dim] — run[/dim] "
        f"[bold cyan]arc-canteen submit-showcase[/bold cyan][dim] again to edit and resubmit.[/dim]"
    )


def _print_all_updates(kind: str, full: bool = False) -> None:
    traction = config.get("updates.traction") or []
    product = config.get("updates.product") or []

    if kind == "traction":
        updates = [{"kind": "traction", **u} for u in traction]
    elif kind == "product":
        updates = [{"kind": "product", **u} for u in product]
    else:
        updates = (
            [{"kind": "traction", **u} for u in traction]
            + [{"kind": "product", **u} for u in product]
        )

    updates = sorted(updates, key=lambda u: u.get("date", ""), reverse=True)
    _print_updates(updates, full=full)


# ---------------------------------------------------------------------------
# arc-canteen profile
# ---------------------------------------------------------------------------

@profile_app.callback(invoke_without_command=True)
def _profile_root(ctx: typer.Context) -> None:
    """View and edit your profile."""
    if ctx.invoked_subcommand is None:
        _require_login()
        _profile_show()


def _profile_show() -> None:
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("GitHub", f"@{config.get('auth.github_handle')}")
    name = config.get("auth.github_name")
    if name:
        table.add_row("Name", name)
    discord = config.get("profile.discord")
    table.add_row("Discord", f"@{discord}" if discord else "[red]not set[/red]")
    telegram = config.get("profile.telegram")
    if telegram:
        table.add_row("Telegram", telegram)
    email = config.get("profile.luma_email")
    if email:
        table.add_row("Email", email)
    console.print(Panel(table, title="[bold]Profile[/bold]", border_style="cyan"))


@profile_app.command("edit")
def profile_edit() -> None:
    """Edit your Discord handle, Telegram, and email."""
    _require_login()

    console.print("[bold]Edit profile[/bold]  [dim](Enter to keep current value, '-' to clear optional fields)[/dim]\n")

    # Discord (required)
    cur = config.get("profile.discord") or ""
    val = typer.prompt("Discord handle", default=cur).strip().lstrip("@")
    if not val:
        console.print("[red]Discord handle is required.[/red]")
        raise typer.Exit(1)
    config.set_val("profile.discord", val)

    # Telegram (optional)
    cur = config.get("profile.telegram") or ""
    val = typer.prompt("Telegram (optional, '-' to clear)", default=cur).strip()
    if val == "-":
        config.set_val("profile.telegram", None)
    elif val:
        config.set_val("profile.telegram", val if val.startswith("@") else f"@{val}")

    # Email (optional; stored/synced as luma_email — the server expects that key)
    cur = config.get("profile.luma_email") or ""
    console.print(
        "[dim]If you joined through a Luma invite, use the same email you used there. "
        "We use it to reach you about grants, awards, and other support.[/dim]"
    )
    while True:
        val = typer.prompt("Email (optional, '-' to clear)", default=cur).strip()
        if val == "-" or not val or ("@" in val and "." in val.split("@")[-1]):
            break
        console.print("[red]Please enter a valid email address.[/red]")
    if val == "-":
        config.set_val("profile.luma_email", None)
    elif val:
        config.set_val("profile.luma_email", val)

    _push.push_event("profile_edit", {
        "discord": config.get("profile.discord"),
        "telegram": config.get("profile.telegram"),
        "luma_email": config.get("profile.luma_email"),
    })

    console.print("\n[green]Profile updated.[/green]")


# ---------------------------------------------------------------------------
# arc-canteen update
# ---------------------------------------------------------------------------

@update_app.callback(invoke_without_command=True)
def _update_root(ctx: typer.Context) -> None:
    """Show recent updates, or submit a new one."""
    if ctx.invoked_subcommand is None:
        _require_login()
        console.print(
            "  [bold]arc-canteen update-traction[/bold]  submit a traction update\n"
            "  [bold]arc-canteen update-product[/bold]   submit a product update\n"
        )
        _print_all_updates("all")


@update_app.command("traction")
def update_traction() -> None:
    """Submit a traction update — who's interested, who's using it."""
    _require_login()
    _require_discord()

    console.print("[bold]Traction Update[/bold]")
    console.print(
        "[dim]How many users have expressed interest? Who is using it? Give examples.[/dim]\n"
    )

    text = _get_multiline_text(
        "How many users have expressed interest in your product? "
        "Explain who and give examples. Who is using it currently?"
    )
    if not text:
        console.print("[yellow]No update submitted.[/yellow]")
        return

    updates: list = config.get("updates.traction") or []
    updates.append({"date": datetime.now(timezone.utc).isoformat(), "text": text})
    config.set_val("updates.traction", updates)

    _push.push_event("update_traction", {"text": text})

    console.print("\n[green]Traction update saved.[/green]")


@update_app.command("product")
def update_product() -> None:
    """Submit a product update — what you've shipped. Include a Loom if you have one."""
    _require_login()
    _require_discord()

    console.print("[bold]Product Update[/bold]")
    console.print(
        "[dim]What have you shipped? Include a Loom link in your update if you have one.[/dim]\n"
    )

    text = _get_multiline_text(
        "What have you shipped since your last update? "
        "Paste a Loom link (loom.com/share/...) if you have one — "
        "then describe the new features."
    )
    if not text:
        console.print("[yellow]No update submitted.[/yellow]")
        return

    updates: list = config.get("updates.product") or []
    updates.append({"date": datetime.now(timezone.utc).isoformat(), "text": text})
    config.set_val("updates.product", updates)

    _push.push_event("update_product", {"text": text})

    console.print("\n[green]Product update saved.[/green]")
