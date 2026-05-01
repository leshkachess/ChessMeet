from __future__ import annotations

import getpass
import urllib.error
import urllib.request
from datetime import datetime
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


def main() -> int:
    env = parse_env()
    url = prompt(env.get("ADMIN_API_URL") or env.get("WEBAPP_URL") or "", "Railway/Admin API URL").rstrip("/")
    token = prompt(env.get("ADMIN_TOKEN") or "", "ADMIN_TOKEN", secret=not bool(env.get("ADMIN_TOKEN")))
    if not url.startswith("https://") and not url.startswith("http://"):
        print("ERROR: URL must start with https:// or http://")
        return 1
    if not token:
        print("ERROR: ADMIN_TOKEN is required")
        return 1
    target = ROOT / f"chess_irl_railway_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sqlite3"
    req = urllib.request.Request(
        url + "/api/admin/db/backup",
        method="GET",
        headers={"X-Admin-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            target.write_bytes(resp.read())
        print(f"Saved backup: {target}")
        return 0
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
