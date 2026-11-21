"""One-time Gmail sign-in for the admin mailbox.

Usage (from backend/):  uv run python -m app.mail.connect

Needs GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET (a Google OAuth client of type "Desktop
app") in .env. Opens Google's consent page in the browser, receives the answer on a
local port, and writes GMAIL_REFRESH_TOKEN and GMAIL_ADDRESS into .env. The token is never printed.
"""

import asyncio
import base64
import hashlib
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx2

from app.config import REPO_ROOT, get_settings, secret
from app.mail.gmail import API, SCOPES, TOKEN_URL

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
WAIT_SECONDS = 300
DONE_PAGE = b"""<!doctype html><meta charset="utf-8"><title>CoO</title>
<body style="font-family:system-ui;background:linear-gradient(160deg,#cbbcf6,#f6cde0);
min-height:100vh;display:grid;place-items:center;margin:0;color:#3b2e5a">
<div style="background:#fffb;padding:32px 40px;border-radius:24px;text-align:center">
<h1 style="margin:0 0 8px">%s</h1><p style="margin:0">%s</p></div>"""


def write_env(path: Path, values: dict[str, str]) -> None:
    """Set these keys in a .env file, keeping every other line as it was."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(values)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and key in pending:
            lines[i] = f"{key}={pending.pop(key)}"
    lines += [f"{key}={value}" for key, value in pending.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _wait_for_code(state: str) -> tuple[HTTPServer, threading.Event, dict[str, str]]:
    answer: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server's name)
            query = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            if "code" not in query and "error" not in query:
                self.send_response(404)
                self.end_headers()
                return
            ok = query.get("state") == state and "code" in query
            answer.update(query if ok else {"error": query.get("error", "state mismatch")})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            title, line = (b"Gmail connected", b"You can close this tab.") if ok else (
                b"Not connected", b"Go back to the terminal for details.")  # fmt: skip
            self.wfile.write(DONE_PAGE % (title, line))
            done.set()

        def log_message(self, *args: object) -> None:  # keep the code out of the console
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, done, answer


async def main() -> int:
    settings = get_settings()
    client_id, client_secret = (settings.gmail_client_id or "").strip(), secret(settings.gmail_client_secret)
    if not client_id or not client_secret:
        print("Put GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET in .env first (Google Cloud console ->")
        print("Google Auth platform -> Clients -> Create client -> Desktop app).")
        return 1

    state = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    server, done, answer = _wait_for_code(state)
    redirect_uri = f"http://127.0.0.1:{server.server_port}/"
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # always return a refresh token
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if settings.gmail_address:
        params["login_hint"] = settings.gmail_address
    url = f"{AUTH_URL}?{urlencode(params)}"
    print("Opening Google sign-in in your browser. Sign in with the admin Gmail account")
    print("(it must be listed under Audience -> Test users), then allow both permissions.")
    print(f"\nIf no browser opens, paste this address into one:\n{url}\n")
    webbrowser.open(url)

    try:
        if not await asyncio.to_thread(done.wait, WAIT_SECONDS):
            print("No answer from Google within 5 minutes. Run this again when ready.")
            return 1
    finally:
        server.shutdown()
    if "error" in answer:
        print(f"Google didn't grant access: {answer['error']}.")
        return 1

    async with httpx2.AsyncClient(timeout=20.0) as client:
        token = await client.post(
            TOKEN_URL,
            data={
                "code": answer["code"],
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
        )
        if token.status_code != 200:
            print(f"Google refused the sign-in code ({token.status_code}): {token.json().get('error_description', '')}")
            return 1
        tokens = token.json()
        granted = set(tokens.get("scope", "").split())
        refresh = tokens.get("refresh_token")
        if not refresh:
            print("Google sent no refresh token. Remove CoO at https://myaccount.google.com/permissions")
            print("and run this again.")
            return 1
        profile = await client.get(f"{API}/profile", headers={"Authorization": f"Bearer {tokens['access_token']}"})
        if profile.status_code != 200:
            print(f"Signed in, but Gmail answered {profile.status_code}. Is the Gmail API enabled in the project?")
            return 1
    address = profile.json()["emailAddress"]

    write_env(REPO_ROOT / ".env", {"GMAIL_REFRESH_TOKEN": refresh, "GMAIL_ADDRESS": address})
    print(f"Connected Gmail: {address}. Saved to .env (the token isn't shown).")
    missing = [s.rsplit("/", 1)[-1] for s in SCOPES if s not in granted]
    if missing:
        print(f"Note: these permissions weren't granted: {', '.join(missing)}. Run again and tick them.")
    print("Restart the backend so it picks up the new mailbox.")
    return 0


if __name__ == "__main__":
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    raise SystemExit(asyncio.run(main(), loop_factory=loop_factory))
