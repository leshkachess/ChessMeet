from __future__ import annotations

import getpass
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"


def parse_env() -> dict[str, str]:
    data: dict[str, str] = {}
    if ENV_PATH.exists():
        for raw in ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def prompt(default: str, label: str, secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    if secret:
        value = getpass.getpass(f"{label}{suffix}: ").strip()
    else:
        value = input(f"{label}{suffix}: ").strip()
    return value or default


def multipart_upload(url: str, token: str, db_path: Path) -> tuple[int, str]:
    boundary = "----ChessMeetBoundary7MA4YWxkTrZu0gW"
    mime = mimetypes.guess_type(str(db_path))[0] or "application/octet-stream"
    file_bytes = db_path.read_bytes()
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{db_path.name}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        file_bytes,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(
        url.rstrip("/") + "/api/admin/db/restore",
        data=body,
        method="POST",
        headers={
            "X-Admin-Token": token,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def main() -> int:
    env = parse_env()
    default_url = env.get("ADMIN_API_URL") or env.get("WEBAPP_URL") or ""
    default_token = env.get("ADMIN_TOKEN") or ""
    default_db = str(ROOT / "chess_irl.sqlite3")

    print("ChessMeet DB Restore → Railway")
    print("This uploads your LOCAL chess_irl.sqlite3 to the remote Railway server.")
    print("Make sure this is the old local database that contains your users/games/badges.\n")

    url = prompt(default_url, "Railway/Admin API URL").rstrip("/")
    token = prompt(default_token, "ADMIN_TOKEN", secret=bool(default_token == ""))
    db_input = prompt(default_db, "Local SQLite DB path")
    db_path = Path(db_input).expanduser().resolve()

    if not url.startswith("https://") and not url.startswith("http://"):
        print("ERROR: URL must start with https:// or http://")
        return 1
    if not token:
        print("ERROR: ADMIN_TOKEN is required")
        return 1
    if not db_path.exists():
        print(f"ERROR: DB file not found: {db_path}")
        return 1

    print(f"\nUploading: {db_path}")
    print(f"Size: {db_path.stat().st_size} bytes")
    print(f"Target: {url}/api/admin/db/restore")
    confirm = input("Type RESTORE to overwrite remote Railway DB: ").strip()
    if confirm != "RESTORE":
        print("Cancelled.")
        return 0

    status, text = multipart_upload(url, token, db_path)
    print(f"\nHTTP {status}")
    try:
        print(json.dumps(json.loads(text), ensure_ascii=False, indent=2))
    except Exception:
        print(text)
    if 200 <= status < 300:
        print("\nDone. Open /health and then Telegram bot. If data still looks empty, redeploy/restart Railway service once.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
