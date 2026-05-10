"""GitHub Device Flow OAuth for arc-canteen CLI."""

import httpx
import time
from rich.console import Console

console = Console()

GITHUB_CLIENT_ID = "Ov23liTpDFXKEw56hVH1"


def device_flow_login() -> dict:
    """Run GitHub Device Flow. Returns token response dict with 'access_token'."""
    try:
        resp = httpx.post(
            "https://github.com/login/device/code",
            data={"client_id": GITHUB_CLIENT_ID, "scope": ""},
            headers={"Accept": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
    except httpx.RequestError as e:
        raise RuntimeError(f"Network error contacting GitHub: {e}")

    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"GitHub error: {data.get('error_description', data['error'])}")

    device_code = data["device_code"]
    user_code = data["user_code"]
    verification_uri = data["verification_uri"]
    interval = data.get("interval", 5)
    expires_in = data.get("expires_in", 900)

    console.print(f"\n  [bold]1.[/bold] Open  → [cyan]{verification_uri}[/cyan]")
    console.print(f"  [bold]2.[/bold] Enter → [bold yellow]{user_code}[/bold yellow]\n")

    try:
        import webbrowser
        webbrowser.open(verification_uri)
    except Exception:
        pass

    deadline = time.time() + expires_in
    with console.status("[dim]Waiting for authorization...[/dim]"):
        while time.time() < deadline:
            time.sleep(interval)
            try:
                poll = httpx.post(
                    "https://github.com/login/oauth/access_token",
                    data={
                        "client_id": GITHUB_CLIENT_ID,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                    headers={"Accept": "application/json"},
                    timeout=10,
                )
                result = poll.json()
            except httpx.RequestError:
                continue

            if "access_token" in result:
                return result

            err = result.get("error", "")
            if err == "authorization_pending":
                continue
            elif err == "slow_down":
                interval += 5
            elif err == "expired_token":
                raise RuntimeError("Code expired. Run `arc-canteen login` again.")
            elif err == "access_denied":
                raise RuntimeError("Authorization denied.")
            else:
                raise RuntimeError(f"Unexpected response: {result}")

    raise RuntimeError("Authorization timed out.")


def get_github_user(token: str) -> dict:
    resp = httpx.get(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()
