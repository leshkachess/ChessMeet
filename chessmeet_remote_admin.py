from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from tkinter import filedialog
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, Y, Button, Entry, Frame, Label, Scrollbar, Text, Tk, messagebox
from tkinter import ttk

APP_TITLE = "ChessMeet Remote Admin Client v0.13"
ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"


def parse_env() -> dict[str, str]:
    data: dict[str, str] = {}
    if ENV_PATH.exists():
        for raw in ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip().strip('"').strip("'")
    return data


class RemoteAdmin:
    def __init__(self) -> None:
        env = parse_env()
        self.root = Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("980x720")

        Label(self.root, text="ChessMeet Remote Admin Client v0.13", font=("Segoe UI", 15, "bold"), pady=8).pack()

        top = Frame(self.root)
        top.pack(fill="x", padx=10, pady=6)
        Label(top, text="Admin API URL:").pack(side=LEFT)
        self.url_entry = Entry(top, width=52)
        self.url_entry.pack(side=LEFT, padx=6)
        self.url_entry.insert(0, env.get("ADMIN_API_URL") or env.get("WEBAPP_URL") or "https://your-service.up.railway.app")
        Label(top, text="ADMIN_TOKEN:").pack(side=LEFT, padx=(12, 2))
        self.token_entry = Entry(top, width=34, show="*")
        self.token_entry.pack(side=LEFT, padx=6)
        self.token_entry.insert(0, env.get("ADMIN_TOKEN", ""))

        buttons = Frame(self.root)
        buttons.pack(fill="x", padx=10, pady=4)
        Button(buttons, text="Health", command=self.load_health).pack(side=LEFT, padx=4)
        Button(buttons, text="Users", command=self.load_users).pack(side=LEFT, padx=4)
        Button(buttons, text="Games", command=self.load_games).pack(side=LEFT, padx=4)
        Button(buttons, text="Reports", command=self.load_reports).pack(side=LEFT, padx=4)
        Button(buttons, text="Puzzles", command=self.load_puzzles).pack(side=LEFT, padx=4)
        Button(buttons, text="Export users.csv", command=lambda: self.download_csv("/api/admin/export/users.csv", "users.csv")).pack(side=LEFT, padx=4)
        Button(buttons, text="Export games.csv", command=lambda: self.download_csv("/api/admin/export/games.csv", "games.csv")).pack(side=LEFT, padx=4)

        action = Frame(self.root)
        action.pack(fill="x", padx=10, pady=4)
        Label(action, text="Telegram ID:").pack(side=LEFT)
        self.tg_entry = Entry(action, width=18)
        self.tg_entry.pack(side=LEFT, padx=4)
        Button(action, text="Block", command=self.block_user).pack(side=LEFT, padx=4)
        Button(action, text="Unblock", command=self.unblock_user).pack(side=LEFT, padx=4)
        Label(action, text="Game ID:").pack(side=LEFT, padx=(16, 2))
        self.game_entry = Entry(action, width=12)
        self.game_entry.pack(side=LEFT, padx=4)
        Button(action, text="Cancel game", command=self.cancel_game).pack(side=LEFT, padx=4)

        Label(self.root, text="Broadcast text:").pack(anchor="w", padx=10, pady=(10, 2))
        self.broadcast_text = Text(self.root, height=4, wrap="word")
        self.broadcast_text.pack(fill="x", padx=10)
        bbuttons = Frame(self.root)
        bbuttons.pack(fill="x", padx=10, pady=4)
        Button(bbuttons, text="Send to all", command=lambda: self.broadcast(None)).pack(side=LEFT, padx=4)
        Button(bbuttons, text="Send to Telegram ID", command=lambda: self.broadcast(self.tg_entry.get().strip())).pack(side=LEFT, padx=4)

        result_frame = Frame(self.root)
        result_frame.pack(fill=BOTH, expand=True, padx=10, pady=10)
        scrollbar = Scrollbar(result_frame)
        scrollbar.pack(side=RIGHT, fill=Y)
        self.result = Text(result_frame, wrap="none", yscrollcommand=scrollbar.set)
        self.result.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.config(command=self.result.yview)

        self.write("Ready. This client works with Railway/remote server via ADMIN_TOKEN.\n")

    def base_url(self) -> str:
        return self.url_entry.get().strip().rstrip("/")

    def token(self) -> str:
        return self.token_entry.get().strip()

    def write(self, text: str) -> None:
        self.result.insert(END, text + ("" if text.endswith("\n") else "\n"))
        self.result.see(END)

    def request(self, path: str, method: str = "GET", data: dict | None = None):
        url = self.base_url() + path
        headers = {"X-Admin-Token": self.token()}
        body = None
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    return json.loads(raw)
                except Exception:
                    return raw
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {e.code}: {detail}") from e

    def show_json(self, data) -> None:
        self.result.delete("1.0", END)
        self.write(json.dumps(data, ensure_ascii=False, indent=2))

    def load_health(self):
        try: self.show_json(self.request("/api/admin/health"))
        except Exception as e: messagebox.showerror(APP_TITLE, str(e))

    def load_users(self):
        try: self.show_json(self.request("/api/admin/users?limit=200"))
        except Exception as e: messagebox.showerror(APP_TITLE, str(e))

    def load_games(self):
        try: self.show_json(self.request("/api/admin/games?limit=200"))
        except Exception as e: messagebox.showerror(APP_TITLE, str(e))

    def load_reports(self):
        try: self.show_json(self.request("/api/admin/reports?limit=200"))
        except Exception as e: messagebox.showerror(APP_TITLE, str(e))

    def load_puzzles(self):
        try:
            data = self.request("/api/admin/puzzles")
            # Keep output readable: first 20 puzzles only, count remains visible.
            if isinstance(data, dict) and isinstance(data.get("puzzles"), list):
                data = dict(data)
                data["puzzles"] = data["puzzles"][:20]
                data["note"] = "Showing first 20 puzzles. Use API directly for full list."
            self.show_json(data)
        except Exception as e: messagebox.showerror(APP_TITLE, str(e))

    def download_csv(self, path: str, default_name: str):
        url = self.base_url() + path
        req = urllib.request.Request(url, headers={"X-Admin-Token": self.token()}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                content = resp.read()
            target = filedialog.asksaveasfilename(
                title="Save CSV",
                defaultextension=".csv",
                initialfile=default_name,
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            )
            if not target:
                return
            Path(target).write_bytes(content)
            self.write(f"Saved CSV: {target}")
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))

    def block_user(self):
        tg = self.tg_entry.get().strip()
        if not tg: return messagebox.showwarning(APP_TITLE, "Telegram ID is required")
        try: self.show_json(self.request(f"/api/admin/users/{int(tg)}/block", method="POST"))
        except Exception as e: messagebox.showerror(APP_TITLE, str(e))

    def unblock_user(self):
        tg = self.tg_entry.get().strip()
        if not tg: return messagebox.showwarning(APP_TITLE, "Telegram ID is required")
        try: self.show_json(self.request(f"/api/admin/users/{int(tg)}/unblock", method="POST"))
        except Exception as e: messagebox.showerror(APP_TITLE, str(e))

    def cancel_game(self):
        gid = self.game_entry.get().strip()
        if not gid: return messagebox.showwarning(APP_TITLE, "Game ID is required")
        try: self.show_json(self.request(f"/api/admin/games/{int(gid)}/cancel", method="POST"))
        except Exception as e: messagebox.showerror(APP_TITLE, str(e))

    def broadcast(self, telegram_id: str | None):
        text = self.broadcast_text.get("1.0", END).strip()
        if not text: return messagebox.showwarning(APP_TITLE, "Broadcast text is empty")
        data = {"text": text}
        if telegram_id:
            data["telegram_id"] = int(telegram_id)
        try: self.show_json(self.request("/api/admin/broadcast", method="POST", data=data))
        except Exception as e: messagebox.showerror(APP_TITLE, str(e))

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    RemoteAdmin().run()
