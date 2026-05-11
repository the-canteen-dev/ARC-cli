"""arc-canteen CLI - main entry point."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from typing import Annotated, Optional
from datetime import datetime, timezone

import json as _json
from pathlib import Path as _Path

from . import config, auth, upgrade
from . import push as _push
from . import rpc as _rpc
from . import context as _context

# Sourceable env file. login() writes `export RPC=…` here; users add
# `[ -f ~/.arc-canteen/env ] && . ~/.arc-canteen/env` to their shell rc.
_SHELL_ENV_FILE = _Path.home() / ".arc-canteen" / "env"


def _write_shell_env(rpc_url: str) -> None:
    _SHELL_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SHELL_ENV_FILE.write_text(f"export RPC='{rpc_url}'\n")

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
# Root — no subcommand → show status
# ---------------------------------------------------------------------------

@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    upgrade.maybe_print_upgrade_notice()
    if config.is_logged_in():
        if not _push.ping():
            console.print("[yellow](server unreachable — changes will sync when it's back up)[/yellow]")
        else:
            _push.drain_queue()
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

    # Luma email
    while True:
        luma_email = typer.prompt("Luma invite email", default="").strip()
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


@app.command()
def logout() -> None:
    """Clear your local credentials."""
    if not config.is_logged_in():
        console.print("[dim]Not logged in.[/dim]")
        return
    handle = config.get("auth.github_handle")
    if typer.confirm(f"Log out @{handle}?", default=True):
        _push.push_event("logout", {"github_username": handle})
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
        console.print(f"[yellow]{remaining} still pending.[/yellow]")


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

    if not last_traction:
        lines.append(f"  {strong} Run [bold]arc-canteen update-traction[/bold] to log users you've talked to or onboarded  [dim](no traction updates yet)[/dim]")
    else:
        lines.append(f"  {weak} Run [bold]arc-canteen update-traction[/bold] to log users you've talked to or onboarded  [dim](last update {_fmt_date(last_traction)})[/dim]")

    if not last_product:
        lines.append(f"  {strong} Run [bold]arc-canteen update-product[/bold] to share feature and product updates  [dim](no product updates yet)[/dim]")
    else:
        lines.append(f"  {weak} Run [bold]arc-canteen update-product[/bold] to share feature and product updates  [dim](last update {_fmt_date(last_product)})[/dim]")

    # ── Agent context callout (persistent — distinct from the cohort CTAs
    # above; this is about using arc-canteen for agentic / dev work). ──
    lines.append("")
    lines.append("  [dim]Agent context — Arc + Circle developer docs and reference codebases:[/dim]")
    lines.append("    [dim]docs/[/dim]     docs.arc.network · developers.circle.com · circlefin-skills [dim](~130 pages)[/dim]")
    lines.append("    [dim]samples/[/dim]  arc-commerce · arc-multichain-wallet · arc-escrow · arc-fintech · arc-p2p-payments")
    if _context.is_synced():
        lines.append(f"  {weak} Pipe to your agent:  [bold]arc-canteen context | <agent>[/bold]")
    else:
        lines.append(f"  {strong} Run [bold]arc-canteen context sync[/bold] to fetch [dim](first-time)[/dim]")

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
        table.add_row("Luma email", email)
    console.print(Panel(table, title="[bold]Profile[/bold]", border_style="cyan"))


@profile_app.command("edit")
def profile_edit() -> None:
    """Edit your Discord handle, Telegram, and Luma email."""
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

    # Luma email (optional)
    cur = config.get("profile.luma_email") or ""
    while True:
        val = typer.prompt("Luma invite email (optional, '-' to clear)", default=cur).strip()
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
