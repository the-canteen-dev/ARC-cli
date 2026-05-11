"""arc-canteen CLI - main entry point."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from typing import Annotated, Optional
from datetime import datetime, timezone

import json as _json

from . import config, auth, upgrade
from . import push as _push
from . import rpc as _rpc

app = typer.Typer(
    name="arc-canteen",
    help="arc-canteen CLI — track your project's progress and submit to Arc.",
    no_args_is_help=False,
    add_completion=True,
)

profile_app = typer.Typer(help="View and edit your profile.", no_args_is_help=False)
update_app = typer.Typer(help="Submit traction and product updates.", no_args_is_help=False)

app.add_typer(profile_app, name="profile")
app.add_typer(update_app, name="update")

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

    console.print(
        "\nRun [bold cyan]arc-canteen status[/bold cyan] to see your dashboard, "
        "or [bold cyan]arc-canteen --help[/bold cyan] to explore commands."
    )


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
