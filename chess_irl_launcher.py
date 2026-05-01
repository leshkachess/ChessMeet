
from __future__ import annotations

import json
import csv
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import (
    BOTH, END, LEFT, RIGHT, TOP, Y, Button, Canvas, Checkbutton, Entry, Frame, IntVar, Label,
    Scrollbar, StringVar, Text, Tk, messagebox, filedialog
)
from tkinter import ttk

APP_TITLE = "Chess IRL Server Launcher — Admin Dashboard v0.13.0"
LOCAL_URL = "http://localhost:8000"
TUNNEL_RE = re.compile(r"https://[-a-zA-Z0-9.]+\.trycloudflare\.com")


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def project_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = project_root()
ENV_PATH = ROOT / ".env"
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
CLOUDFLARED_CANDIDATES = [
    ROOT / "cloudflared.exe",
    Path("C:/cloudflared.exe"),
]


def parse_env() -> dict[str, str]:
    data: dict[str, str] = {}
    if not ENV_PATH.exists():
        return data
    for raw in ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def database_path() -> Path:
    env = parse_env()
    value = env.get("DATABASE_PATH", "./chess_irl.sqlite3")
    p = Path(value)
    if not p.is_absolute():
        p = ROOT / p
    return p


def unique_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            key = str(path.resolve()).lower()
        except Exception:
            key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, name: str) -> set[str]:
    if not table_exists(conn, name):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({name})").fetchall()}


def safe_count(conn: sqlite3.Connection, table: str, where: str = "", params: tuple = ()) -> int:
    if not table_exists(conn, table):
        return 0
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += " WHERE " + where
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    except Exception:
        return 0


def display_contact(row: sqlite3.Row, cols: set[str]) -> str:
    username = row["username"] if "username" in cols else None
    if username:
        return f"@{username}"
    for c in ("phone", "phone_number", "contact_phone", "mobile", "telephone"):
        if c in cols and row[c]:
            return str(row[c])
    return f"id:{row['telegram_id']}"


class Launcher:
    def __init__(self) -> None:
        self.root = Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("1060x760")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.cloudflared_process: subprocess.Popen | None = None
        self.server_process: subprocess.Popen | None = None
        self.tunnel_url: str | None = None
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.is_starting = False

        Label(
            self.root,
            text="Chess IRL Minsk — Admin Dashboard v0.13.0",
            font=("Segoe UI", 15, "bold"),
            pady=8,
        ).pack()

        self.status_label = Label(self.root, text="Статус: не запущено", fg="#555")
        self.status_label.pack()

        top_buttons = Frame(self.root)
        top_buttons.pack(pady=8)

        self.start_button = Button(top_buttons, text="▶ Запустить", width=18, command=self.start)
        self.start_button.pack(side=LEFT, padx=5)

        self.stop_button = Button(top_buttons, text="■ Остановить", width=18, command=self.stop, state="disabled")
        self.stop_button.pack(side=LEFT, padx=5)

        Button(top_buttons, text="Открыть локально", width=18, command=lambda: webbrowser.open(LOCAL_URL)).pack(side=LEFT, padx=5)
        Button(top_buttons, text="Health", width=12, command=lambda: webbrowser.open(f"{LOCAL_URL}/health")).pack(side=LEFT, padx=5)
        Button(top_buttons, text="Копировать URL", width=16, command=self.copy_url).pack(side=LEFT, padx=5)

        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill=BOTH, expand=True, padx=10, pady=8)

        self.server_tab = Frame(self.tabs)
        self.broadcast_tab = Frame(self.tabs)
        self.admin_tab = Frame(self.tabs)
        self.badges_tab = Frame(self.tabs)
        self.puzzles_tab = Frame(self.tabs)
        self.moderation_tab = Frame(self.tabs)
        self.logs_tab = Frame(self.tabs)

        self.tabs.add(self.server_tab, text="Server")
        self.tabs.add(self.broadcast_tab, text="Broadcast")
        self.tabs.add(self.admin_tab, text="Admin Dashboard")
        self.tabs.add(self.badges_tab, text="Badges")
        self.tabs.add(self.puzzles_tab, text="Puzzle Library")
        self.tabs.add(self.moderation_tab, text="Moderation")
        self.tabs.add(self.logs_tab, text="Logs")

        self.build_server_tab()
        self.build_broadcast_tab()
        self.build_admin_tab()
        self.build_badges_tab()
        self.build_puzzles_tab()
        self.build_moderation_tab()
        self.build_logs_tab()

        self.root.after(150, self.flush_logs)
        self.log(f"Project folder: {ROOT}")
        self.log("Launcher Admin Dashboard v0.13.0 загружен.")
        self.refresh_dashboard()
        self.refresh_badges()
        self.refresh_puzzles()
        self.refresh_moderation()

    def build_server_tab(self) -> None:
        Label(self.server_tab, text="Управление сервером", font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
        info = (
            "Нажми «Запустить». Launcher сам поднимет Cloudflare Tunnel, обновит WEBAPP_URL в .env "
            "и запустит Uvicorn сервер. Окна PowerShell больше не нужны."
        )
        Label(self.server_tab, text=info, wraplength=930, justify="left").pack(anchor="w", padx=12, pady=4)

        self.server_info_text = Text(self.server_tab, height=13, wrap="word")
        self.server_info_text.pack(fill=BOTH, expand=True, padx=12, pady=10)
        self.server_info_text.insert(END, f"ROOT: {ROOT}\n")
        self.server_info_text.insert(END, f".env: {ENV_PATH}\n")
        self.server_info_text.insert(END, f"database: {database_path()}\n")
        self.server_info_text.insert(END, f"venv python: {VENV_PYTHON}\n")

    def build_broadcast_tab(self) -> None:
        Label(self.broadcast_tab, text="Сообщение от бота пользователям", font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
        Label(
            self.broadcast_tab,
            text="Можно отправить всем пользователям из базы или одному Telegram ID. BOT_TOKEN берётся из .env.",
            wraplength=930,
            justify="left",
        ).pack(anchor="w", padx=12, pady=4)

        row = Frame(self.broadcast_tab)
        row.pack(fill="x", padx=12, pady=6)
        self.send_all_var = IntVar(value=1)
        Checkbutton(row, text="Отправить всем пользователям из базы", variable=self.send_all_var).pack(side=LEFT)
        Label(row, text=" или Telegram ID: ").pack(side=LEFT, padx=(14, 4))
        self.single_user_entry = Entry(row, width=24)
        self.single_user_entry.pack(side=LEFT)
        Button(row, text="Показать кол-во пользователей", command=self.show_user_count).pack(side=LEFT, padx=12)

        Label(self.broadcast_tab, text="Текст:").pack(anchor="w", padx=12, pady=(8, 2))
        self.broadcast_text = Text(self.broadcast_tab, height=8, wrap="word")
        self.broadcast_text.pack(fill="x", padx=12, pady=4)

        buttons = Frame(self.broadcast_tab)
        buttons.pack(anchor="w", padx=12, pady=8)
        Button(buttons, text="📨 Отправить сообщение", width=24, command=self.send_broadcast).pack(side=LEFT, padx=(0, 8))
        Button(buttons, text="Очистить текст", width=18, command=lambda: self.broadcast_text.delete("1.0", END)).pack(side=LEFT)

    def build_admin_tab(self) -> None:
        header = Frame(self.admin_tab)
        header.pack(fill="x", padx=12, pady=(12, 6))
        Label(header, text="Admin Dashboard", font=("Segoe UI", 12, "bold")).pack(side=LEFT)
        Button(header, text="🔄 Обновить dashboard", command=self.refresh_dashboard).pack(side=LEFT, padx=10)
        Button(header, text="💾 Экспорт базы", command=self.export_database).pack(side=LEFT, padx=5)
        Button(header, text="CSV users", command=self.export_users_csv).pack(side=LEFT, padx=5)
        Button(header, text="CSV games", command=self.export_games_csv).pack(side=LEFT, padx=5)

        actions = Frame(self.admin_tab)
        actions.pack(fill="x", padx=12, pady=(0, 8))
        Label(actions, text="Telegram ID:").pack(side=LEFT)
        self.admin_user_id_entry = Entry(actions, width=18)
        self.admin_user_id_entry.pack(side=LEFT, padx=4)
        Button(actions, text="Блок", command=self.admin_block_user).pack(side=LEFT, padx=3)
        Button(actions, text="Снять блок", command=self.admin_unblock_user).pack(side=LEFT, padx=3)
        Label(actions, text="Game ID:").pack(side=LEFT, padx=(16, 4))
        self.admin_game_id_entry = Entry(actions, width=12)
        self.admin_game_id_entry.pack(side=LEFT, padx=4)
        Button(actions, text="Удалить/отменить заявку", command=self.admin_cancel_game).pack(side=LEFT, padx=3)

        self.metrics_text = Text(self.admin_tab, height=10, wrap="word")
        self.metrics_text.pack(fill="x", padx=12, pady=6)

        Label(self.admin_tab, text="Последние пользователи", font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=12, pady=(8, 4))
        cols = ("telegram_id", "contact", "display_name", "city", "rating", "streak", "created_at")
        self.users_tree = ttk.Treeview(self.admin_tab, columns=cols, show="headings", height=12)
        headings = {
            "telegram_id": "Telegram ID",
            "contact": "@username / phone / id",
            "display_name": "Имя",
            "city": "Город",
            "rating": "Рейтинг",
            "streak": "Серия",
            "created_at": "Создан",
        }
        widths = {
            "telegram_id": 110,
            "contact": 190,
            "display_name": 170,
            "city": 100,
            "rating": 90,
            "streak": 70,
            "created_at": 170,
        }
        for col in cols:
            self.users_tree.heading(col, text=headings[col])
            self.users_tree.column(col, width=widths[col], anchor="w")
        self.users_tree.pack(fill=BOTH, expand=True, padx=12, pady=(0, 12))

    def build_badges_tab(self) -> None:
        header = Frame(self.badges_tab)
        header.pack(fill="x", padx=12, pady=(12, 6))
        Label(header, text="Badges / Значки", font=("Segoe UI", 12, "bold")).pack(side=LEFT)
        Button(header, text="🔄 Обновить", command=self.refresh_badges).pack(side=LEFT, padx=10)

        create_box = Frame(self.badges_tab)
        create_box.pack(fill="x", padx=12, pady=8)
        Label(create_box, text="Создать значок", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", columnspan=8, pady=(0, 5))
        Label(create_box, text="Иконка").grid(row=1, column=0, sticky="w")
        self.badge_icon_entry = Entry(create_box, width=8)
        self.badge_icon_entry.insert(0, "🏅")
        self.badge_icon_entry.grid(row=1, column=1, padx=5)
        Label(create_box, text="Название").grid(row=1, column=2, sticky="w")
        self.badge_title_entry = Entry(create_box, width=28)
        self.badge_title_entry.grid(row=1, column=3, padx=5)
        Label(create_box, text="Цвет").grid(row=1, column=4, sticky="w")
        self.badge_color_entry = Entry(create_box, width=12)
        self.badge_color_entry.insert(0, "#2f8a4b")
        self.badge_color_entry.grid(row=1, column=5, padx=5)
        Button(create_box, text="Создать", command=self.create_badge).grid(row=1, column=6, padx=8)
        Label(create_box, text="Описание").grid(row=2, column=0, sticky="w", pady=(8,0))
        self.badge_desc_entry = Entry(create_box, width=74)
        self.badge_desc_entry.grid(row=2, column=1, columnspan=6, sticky="we", padx=5, pady=(8,0))

        award_box = Frame(self.badges_tab)
        award_box.pack(fill="x", padx=12, pady=8)
        Label(award_box, text="Выдать значок пользователю", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", columnspan=8, pady=(0, 5))
        Label(award_box, text="Telegram ID").grid(row=1, column=0, sticky="w")
        self.award_user_entry = Entry(award_box, width=22)
        self.award_user_entry.grid(row=1, column=1, padx=5)
        Label(award_box, text="Badge ID").grid(row=1, column=2, sticky="w")
        self.award_badge_entry = Entry(award_box, width=12)
        self.award_badge_entry.grid(row=1, column=3, padx=5)
        Label(award_box, text="Заметка").grid(row=1, column=4, sticky="w")
        self.award_note_entry = Entry(award_box, width=30)
        self.award_note_entry.grid(row=1, column=5, padx=5)
        Button(award_box, text="Выдать", command=self.award_badge).grid(row=1, column=6, padx=8)

        cols = ("id", "icon", "title", "description", "color", "created_at")
        self.badges_tree = ttk.Treeview(self.badges_tab, columns=cols, show="headings", height=9)
        for col, title, width in [
            ("id", "ID", 60), ("icon", "Иконка", 80), ("title", "Название", 200),
            ("description", "Описание", 360), ("color", "Цвет", 100), ("created_at", "Создан", 170)
        ]:
            self.badges_tree.heading(col, text=title)
            self.badges_tree.column(col, width=width, anchor="w")
        self.badges_tree.pack(fill=BOTH, expand=True, padx=12, pady=(8, 6))

        self.badges_text = Text(self.badges_tab, height=8, wrap="word")
        self.badges_text.pack(fill="x", padx=12, pady=(0, 12))

    def build_moderation_tab(self) -> None:
        header = Frame(self.moderation_tab)
        header.pack(fill="x", padx=12, pady=(12, 6))
        Label(header, text="Moderation Center", font=("Segoe UI", 12, "bold")).pack(side=LEFT)
        Button(header, text="🔄 Обновить", command=self.refresh_moderation).pack(side=LEFT, padx=10)
        Button(header, text="📄 Экспорт moderation report", command=self.export_moderation_report).pack(side=LEFT, padx=5)

        self.moderation_text = Text(self.moderation_tab, height=28, wrap="word")
        self.moderation_text.pack(fill=BOTH, expand=True, padx=12, pady=8)

    def build_puzzles_tab(self) -> None:
        header = Frame(self.puzzles_tab)
        header.pack(fill="x", padx=12, pady=(12, 6))
        Label(header, text="Puzzle Library / База задач", font=("Segoe UI", 12, "bold")).pack(side=LEFT)
        Button(header, text="🔄 Обновить", command=self.refresh_puzzles).pack(side=LEFT, padx=10)
        Button(header, text="⚙ Создать/обновить кеш", command=self.force_puzzle_cache).pack(side=LEFT, padx=5)
        Button(header, text="📂 Папка проекта", command=self.open_project_folder).pack(side=LEFT, padx=5)
        Button(header, text="📋 Copy FEN", command=self.copy_selected_puzzle_fen).pack(side=LEFT, padx=5)
        Button(header, text="📄 Export CSV", command=self.export_puzzles_csv).pack(side=LEFT, padx=5)

        info = (
            "Показывает локальный кеш задач Lichess mate-in-1. Если список пустой — нажми "
            "«Создать/обновить кеш»: launcher запустит проектный Python и попросит backend заново собрать кеш. "
            "Обычные пользователи ответы не видят; это админ-просмотр для проверки базы."
        )
        Label(self.puzzles_tab, text=info, wraplength=960, justify="left").pack(anchor="w", padx=12, pady=(0, 8))

        search_row = Frame(self.puzzles_tab)
        search_row.pack(fill="x", padx=12, pady=(0, 8))
        Label(search_row, text="Поиск:").pack(side=LEFT)
        self.puzzle_search_var = StringVar()
        self.puzzle_search_entry = Entry(search_row, textvariable=self.puzzle_search_var, width=52)
        self.puzzle_search_entry.pack(side=LEFT, padx=8)
        Button(search_row, text="Найти", command=self.refresh_puzzles).pack(side=LEFT, padx=4)
        Button(search_row, text="Сброс", command=self.clear_puzzle_search).pack(side=LEFT, padx=4)
        Button(search_row, text="Открыть /api/config", command=lambda: webbrowser.open(f"{LOCAL_URL}/api/config")).pack(side=LEFT, padx=8)

        self.puzzle_files_label = Label(self.puzzles_tab, text="Файлы задач пока не проверены", fg="#555", justify="left")
        self.puzzle_files_label.pack(anchor="w", padx=12, pady=(0, 2))
        self.puzzles_info_label = Label(self.puzzles_tab, text="Задачи не загружены", fg="#555")
        self.puzzles_info_label.pack(anchor="w", padx=12, pady=(0, 6))

        cols = ("num", "id", "source", "title", "side", "solution", "fen")
        self.puzzles_tree = ttk.Treeview(self.puzzles_tab, columns=cols, show="headings", height=18)
        headings = {
            "num": "#",
            "id": "Puzzle ID",
            "source": "Source/Lichess",
            "title": "Название",
            "side": "Ход",
            "solution": "Решения",
            "fen": "FEN",
        }
        widths = {
            "num": 55,
            "id": 90,
            "source": 120,
            "title": 180,
            "side": 110,
            "solution": 170,
            "fen": 520,
        }
        for col in cols:
            self.puzzles_tree.heading(col, text=headings[col])
            self.puzzles_tree.column(col, width=widths[col], anchor="w")
        self.puzzles_tree.pack(fill=BOTH, expand=True, padx=12, pady=(0, 8))
        self.puzzles_tree.bind("<<TreeviewSelect>>", lambda _event: self.show_selected_puzzle())

        Label(self.puzzles_tab, text="Выбранная задача", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(4, 2))
        details_area = Frame(self.puzzles_tab)
        details_area.pack(fill="x", padx=12, pady=(0, 12))

        self.puzzle_details_text = Text(details_area, height=8, wrap="word")
        self.puzzle_details_text.pack(side=LEFT, fill="both", expand=True)

        board_box = Frame(details_area)
        board_box.pack(side=RIGHT, padx=(12, 0))
        Label(board_box, text="Просмотр доски", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.puzzle_board_canvas = Canvas(board_box, width=320, height=320, bg="#ffffff", highlightthickness=1, highlightbackground="#cccccc")
        self.puzzle_board_canvas.pack()
        self.puzzle_board_hint = Label(board_box, text="Выбери задачу", fg="#555")
        self.puzzle_board_hint.pack(anchor="w", pady=(4, 0))

    def clear_puzzle_search(self) -> None:
        if hasattr(self, "puzzle_search_var"):
            self.puzzle_search_var.set("")
        self.refresh_puzzles()

    def puzzle_search_roots(self) -> list[Path]:
        roots = [ROOT]
        try:
            roots.append(database_path().parent)
        except Exception:
            pass
        roots += [ROOT / "data", ROOT / "cache", ROOT / "src", ROOT / "webapp"]
        return [p for p in unique_paths(roots) if p.exists() and p.is_dir()]

    def puzzle_cache_candidates(self) -> list[Path]:
        exact_names = [
            "daily_puzzles_lichess_mate1_verified_300.json",
            "daily_puzzles_bigbench_mate1_150.json",
            "daily_puzzles_cache.json",
            "daily_puzzles.json",
        ]
        candidates: list[Path] = []
        for root in self.puzzle_search_roots():
            for name in exact_names:
                candidates.append(root / name)
            try:
                candidates += sorted(root.glob("*puzzle*.json"))
                candidates += sorted(root.glob("*mate*.json"))
                candidates += sorted(root.glob("daily_*.json"))
            except Exception:
                pass
        return unique_paths(candidates)

    def existing_puzzle_cache_files(self) -> list[Path]:
        return [p for p in self.puzzle_cache_candidates() if p.exists() and p.is_file()]

    def puzzle_cache_path(self) -> Path | None:
        files = self.existing_puzzle_cache_files()
        if not files:
            return None
        preferred = [p for p in files if p.name == "daily_puzzles_lichess_mate1_verified_300.json"]
        if preferred:
            return preferred[0]
        return files[0]

    def load_puzzles_from_file(self, path: Path) -> tuple[list[dict], str]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return [], f"Не удалось прочитать {path}: {exc}"
        if isinstance(raw, dict):
            puzzles = raw.get("puzzles") or raw.get("items") or raw.get("data") or []
            source = raw.get("source") or raw.get("source_url") or str(path.name)
        elif isinstance(raw, list):
            puzzles = raw
            source = str(path.name)
        else:
            puzzles = []
            source = str(path.name)
        clean = [p for p in puzzles if isinstance(p, dict)]
        return clean, f"{path.name} | source: {source} | path: {path}"

    def load_puzzles(self) -> tuple[list[dict], str]:
        path = self.puzzle_cache_path()
        if not path:
            roots = "; ".join(str(p) for p in self.puzzle_search_roots())
            return [], "Кеш задач не найден. Нажми «Создать/обновить кеш». Проверенные папки: " + roots
        return self.load_puzzles_from_file(path)

    def refresh_puzzle_files_label(self) -> None:
        if not hasattr(self, "puzzle_files_label"):
            return
        files = self.existing_puzzle_cache_files()
        if not files:
            roots = "; ".join(str(p) for p in self.puzzle_search_roots())
            self.puzzle_files_label.config(text=f"Найденные JSON-файлы задач: 0 | проверяю: {roots}")
            return
        shown = "; ".join(f"{p.name}" for p in files[:6])
        extra = f" + ещё {len(files) - 6}" if len(files) > 6 else ""
        self.puzzle_files_label.config(text=f"Найденные JSON-файлы задач: {len(files)} | {shown}{extra}")

    def refresh_puzzles(self) -> None:
        if not hasattr(self, "puzzles_tree"):
            return
        self.refresh_puzzle_files_label()
        for item in self.puzzles_tree.get_children():
            self.puzzles_tree.delete(item)
        self.puzzle_details_text.delete("1.0", END)
        if hasattr(self, "puzzle_board_canvas"):
            self.puzzle_board_canvas.delete("all")
            self.puzzle_board_hint.config(text="Выбери задачу")
        puzzles, source_label = self.load_puzzles()
        query = ""
        if hasattr(self, "puzzle_search_var"):
            query = self.puzzle_search_var.get().strip().lower()
        shown = 0
        filtered_indices: list[int] = []
        for real_index, puzzle in enumerate(puzzles):
            idx = real_index + 1
            solution_moves = puzzle.get("solution_moves") or puzzle.get("mate_moves") or []
            if isinstance(solution_moves, str):
                solution_moves = [solution_moves]
            solution_san = puzzle.get("solution_san") or ""
            solution_text = ", ".join([str(x) for x in solution_moves])
            if solution_san:
                solution_text = f"{solution_text} / {solution_san}" if solution_text else str(solution_san)
            lichess_id = puzzle.get("lichess_puzzle_id") or puzzle.get("source_id") or puzzle.get("source") or puzzle.get("PuzzleId") or ""
            row_text = " ".join([
                str(idx),
                str(puzzle.get("id") or ""),
                str(lichess_id),
                str(puzzle.get("title") or ""),
                str(puzzle.get("side") or puzzle.get("side_to_move") or ""),
                str(solution_text),
                str(puzzle.get("fen") or puzzle.get("FEN") or ""),
            ]).lower()
            if query and query not in row_text:
                continue
            filtered_indices.append(real_index)
            shown += 1
            self.puzzles_tree.insert(
                "",
                END,
                iid=str(real_index),
                values=(
                    idx,
                    puzzle.get("id") or "",
                    lichess_id,
                    puzzle.get("title") or "",
                    puzzle.get("side") or puzzle.get("side_to_move") or "",
                    solution_text,
                    puzzle.get("fen") or puzzle.get("FEN") or "",
                ),
            )
        self.puzzles_info_label.config(text=f"Всего задач: {len(puzzles)} | показано: {shown} | {source_label}")
        self._loaded_puzzles = puzzles
        self._loaded_puzzle_indices = filtered_indices

    def open_project_folder(self) -> None:
        try:
            if os.name == "nt":
                os.startfile(str(ROOT))
            else:
                webbrowser.open(ROOT.as_uri())
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Не удалось открыть папку: {exc}")

    def force_puzzle_cache(self) -> None:
        if not VENV_PYTHON.exists():
            messagebox.showerror(APP_TITLE, "Не найден .venv\\Scripts\\python.exe. Сначала установи зависимости проекта.")
            return
        if not (ROOT / "src" / "database.py").exists():
            messagebox.showerror(APP_TITLE, "Не найдена папка src/database.py. Launcher должен лежать в корне проекта.")
            return
        self.log("Puzzle Library: запускаю принудительное создание/обновление кеша задач. Это может занять 1–3 минуты.")
        threading.Thread(target=self._force_puzzle_cache_worker, daemon=True).start()

    def _force_puzzle_cache_worker(self) -> None:
        code = r'''
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path.cwd() / ".env")
from src.database import Database
root = Path.cwd()
db_path = os.getenv("DATABASE_PATH", str(root / "chess_irl.sqlite3"))
print(f"ROOT={root}", flush=True)
print(f"DATABASE_PATH={db_path}", flush=True)
db = Database(db_path)
print(f"PUZZLE_SOURCE={getattr(db, 'puzzle_source', '')}", flush=True)
print(f"PUZZLE_COUNT={len(getattr(db, 'daily_puzzles', []))}", flush=True)
try:
    cache = db._puzzle_cache_path()
    print(f"CACHE_PATH={cache}", flush=True)
    print(f"CACHE_EXISTS={cache.exists()}", flush=True)
except Exception as exc:
    print(f"CACHE_PATH_ERROR={exc}", flush=True)
'''
        try:
            proc = subprocess.Popen(
                [str(VENV_PYTHON), "-c", code],
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=self.creation_flags(),
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                self.log("puzzle-cache: " + line.strip())
            rc = proc.wait(timeout=300)
            self.log(f"puzzle-cache: finished with code {rc}")
        except subprocess.TimeoutExpired:
            self.log("puzzle-cache: timeout after 300 seconds")
            try:
                proc.kill()
            except Exception:
                pass
        except Exception as exc:
            self.log(f"puzzle-cache ERROR: {exc}")
        finally:
            self.root.after(0, self.refresh_puzzles)
            self.root.after(0, lambda: messagebox.showinfo(APP_TITLE, "Проверка кеша задач завершена. Нажми Обновить, если список не обновился автоматически."))

    def selected_puzzle(self) -> dict | None:
        if not hasattr(self, "puzzles_tree"):
            return None
        selected = self.puzzles_tree.selection()
        if not selected:
            return None
        try:
            index = int(selected[0])
            return self._loaded_puzzles[index]
        except Exception:
            return None

    def show_selected_puzzle(self) -> None:
        puzzle = self.selected_puzzle()
        self.puzzle_details_text.delete("1.0", END)
        if not puzzle:
            return
        solution_moves = puzzle.get("solution_moves") or []
        if isinstance(solution_moves, str):
            solution_moves = [solution_moves]
        lines = [
            f"Title: {puzzle.get('title') or ''}",
            f"ID: {puzzle.get('id') or ''}",
            f"Lichess Puzzle ID: {puzzle.get('lichess_puzzle_id') or ''}",
            f"Source: {puzzle.get('source') or ''}",
            f"Side: {puzzle.get('side') or puzzle.get('side_to_move') or ''}",
            f"FEN: {puzzle.get('fen') or puzzle.get('FEN') or ''}",
            f"Solution moves: {', '.join([str(x) for x in solution_moves])}",
            f"Solution SAN: {puzzle.get('solution_san') or ''}",
            f"Question: {puzzle.get('question') or ''}",
            f"Explanation: {puzzle.get('explanation') or ''}",
        ]
        self.puzzle_details_text.insert(END, "\n".join(lines))
        self.draw_selected_puzzle_board(puzzle)

    def draw_selected_puzzle_board(self, puzzle: dict) -> None:
        if not hasattr(self, "puzzle_board_canvas"):
            return
        canvas = self.puzzle_board_canvas
        canvas.delete("all")
        fen = str(puzzle.get("fen") or puzzle.get("FEN") or "").strip()
        if not fen:
            self.puzzle_board_hint.config(text="Нет FEN")
            return

        placement = fen.split()[0]
        side = fen.split()[1] if len(fen.split()) > 1 else "w"
        board = []
        for rank in placement.split("/"):
            row = []
            for ch in rank:
                if ch.isdigit():
                    row.extend([""] * int(ch))
                else:
                    row.append(ch)
            if len(row) < 8:
                row += [""] * (8 - len(row))
            board.append(row[:8])
        if len(board) != 8:
            self.puzzle_board_hint.config(text="Некорректный FEN")
            return

        pieces = {
            "K": "♔", "Q": "♕", "R": "♖", "B": "♗", "N": "♘", "P": "♙",
            "k": "♚", "q": "♛", "r": "♜", "b": "♝", "n": "♞", "p": "♟",
        }
        cell = 40
        light = "#eeeed2"
        dark = "#779954"
        for r in range(8):
            for c in range(8):
                x0 = c * cell
                y0 = r * cell
                color = light if (r + c) % 2 == 0 else dark
                canvas.create_rectangle(x0, y0, x0 + cell, y0 + cell, fill=color, outline=color)
                piece = board[r][c]
                if piece:
                    fill = "#f8f8f8" if piece.isupper() else "#111111"
                    outline = "#222222" if piece.isupper() else "#f8f8f8"
                    canvas.create_text(x0 + cell / 2 + 1, y0 + cell / 2 + 2, text=pieces.get(piece, piece), font=("Segoe UI Symbol", 25, "bold"), fill=outline)
                    canvas.create_text(x0 + cell / 2, y0 + cell / 2, text=pieces.get(piece, piece), font=("Segoe UI Symbol", 25, "bold"), fill=fill)

        solution_moves = puzzle.get("solution_moves") or []
        if isinstance(solution_moves, str):
            solution_moves = [solution_moves]
        self.puzzle_board_hint.config(text=f"Ход: {'белые' if side == 'w' else 'чёрные'} | Решение: {', '.join(map(str, solution_moves[:3])) or puzzle.get('solution_san') or ''}")

    def copy_selected_puzzle_fen(self) -> None:
        puzzle = self.selected_puzzle()
        if not puzzle:
            messagebox.showinfo(APP_TITLE, "Выбери задачу в таблице.")
            return
        fen = str(puzzle.get("fen") or puzzle.get("FEN") or "")
        if not fen:
            messagebox.showinfo(APP_TITLE, "У выбранной задачи нет FEN.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(fen)
        messagebox.showinfo(APP_TITLE, "FEN скопирован в буфер обмена.")

    def export_puzzles_csv(self) -> None:
        puzzles, _source_label = self.load_puzzles()
        if not puzzles:
            messagebox.showwarning(APP_TITLE, "Нет задач для экспорта.")
            return
        default_name = f"chess_irl_puzzles_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        target = filedialog.asksaveasfilename(
            title="Экспорт задач в CSV",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV file", "*.csv"), ("All files", "*.*")],
        )
        if not target:
            return
        try:
            with open(target, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["id", "title", "lichess_puzzle_id", "side", "fen", "solution_moves", "solution_san", "source"])
                for pz in puzzles:
                    moves = pz.get("solution_moves") or []
                    if isinstance(moves, str):
                        moves = [moves]
                    writer.writerow([
                        pz.get("id") or "",
                        pz.get("title") or "",
                        pz.get("lichess_puzzle_id") or "",
                        pz.get("side") or pz.get("side_to_move") or "",
                        pz.get("fen") or pz.get("FEN") or "",
                        " ".join([str(x) for x in moves]),
                        pz.get("solution_san") or "",
                        pz.get("source") or "",
                    ])
            messagebox.showinfo(APP_TITLE, f"Задачи экспортированы:\n{target}")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Ошибка экспорта задач: {exc}")

    def build_logs_tab(self) -> None:
        frame = Frame(self.logs_tab)
        frame.pack(fill=BOTH, expand=True, padx=12, pady=12)
        scrollbar = Scrollbar(frame)
        scrollbar.pack(side=RIGHT, fill=Y)
        self.log_text = Text(frame, wrap="word", yscrollcommand=scrollbar.set)
        self.log_text.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.config(command=self.log_text.yview)

    def log(self, message: str) -> None:
        self.log_queue.put(message)

    def flush_logs(self) -> None:
        while True:
            try:
                msg = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert(END, f"{msg}\n")
            self.log_text.see(END)
        self.root.after(150, self.flush_logs)

    def set_status(self, text: str) -> None:
        self.root.after(0, lambda: self.status_label.config(text=f"Статус: {text}"))

    def check_files(self) -> bool:
        if not ENV_PATH.exists():
            messagebox.showerror(APP_TITLE, "Не найден файл .env. Launcher должен лежать в корне проекта.")
            return False
        if not (ROOT / "src" / "main.py").exists():
            messagebox.showerror(APP_TITLE, "Не найдена папка src/main.py. Launcher должен лежать в корне проекта.")
            return False
        if not VENV_PYTHON.exists():
            messagebox.showerror(APP_TITLE, "Не найден .venv\\Scripts\\python.exe. Сначала установи зависимости проекта.")
            return False
        if not self.find_cloudflared():
            messagebox.showerror(APP_TITLE, "Не найден cloudflared.exe. Положи его в C:\\cloudflared.exe или в папку проекта.")
            return False
        return True

    def find_cloudflared(self):
        for candidate in CLOUDFLARED_CANDIDATES:
            if candidate.exists():
                return candidate
        return "cloudflared.exe"

    def creation_flags(self) -> int:
        if os.name != "nt":
            return 0
        return subprocess.CREATE_NO_WINDOW

    def start(self) -> None:
        if self.is_starting or self.server_process or self.cloudflared_process:
            self.log("Уже запущено или запускается.")
            return
        if not self.check_files():
            return
        self.is_starting = True
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.set_status("запускаю tunnel...")
        threading.Thread(target=self._start_worker, daemon=True).start()

    def _start_worker(self) -> None:
        try:
            cloudflared = self.find_cloudflared()
            self.log("Запускаю Cloudflare Tunnel...")
            self.cloudflared_process = subprocess.Popen(
                [str(cloudflared), "tunnel", "--url", LOCAL_URL],
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=self.creation_flags(),
            )

            found_url = None
            start_time = time.time()
            assert self.cloudflared_process.stdout is not None
            while True:
                line = self.cloudflared_process.stdout.readline()
                if line:
                    clean = line.strip()
                    self.log(f"cloudflared: {clean}")
                    match = TUNNEL_RE.search(clean)
                    if match and not found_url:
                        found_url = match.group(0)
                        break
                if self.cloudflared_process.poll() is not None:
                    raise RuntimeError("cloudflared остановился до получения tunnel URL")
                if time.time() - start_time > 45:
                    raise RuntimeError("Не получил trycloudflare URL за 45 секунд")

            self.tunnel_url = found_url
            self.update_env_url(found_url)
            self.set_status(f"tunnel OK: {found_url}")
            self.log(f"WEBAPP_URL обновлён в .env: {found_url}")

            self.log("Запускаю Uvicorn/FastAPI сервер...")
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            self.server_process = subprocess.Popen(
                [str(VENV_PYTHON), "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"],
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=self.creation_flags(),
            )
            self.set_status("сервер запущен")
            self.log("Готово. Теперь открой Telegram-бота и отправь /start.")
            self.log(f"Mini App URL: {found_url}")
            threading.Thread(target=self._read_process_log, args=(self.server_process, "server"), daemon=True).start()
            self._read_process_log(self.cloudflared_process, "cloudflared")
        except Exception as exc:
            self.log(f"ERROR: {exc}")
            self.set_status("ошибка запуска")
            self.root.after(0, lambda: self.start_button.config(state="normal"))
            self.root.after(0, lambda: self.stop_button.config(state="disabled"))
            self.stop()
        finally:
            self.is_starting = False

    def _read_process_log(self, process: subprocess.Popen, name: str) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            self.log(f"{name}: {line.strip()}")
        self.log(f"{name}: процесс завершён")

    def update_env_url(self, url: str) -> None:
        content = ENV_PATH.read_text(encoding="utf-8", errors="replace") if ENV_PATH.exists() else ""
        lines = content.splitlines()
        replaced = False
        new_lines = []
        for line in lines:
            if line.startswith("WEBAPP_URL="):
                new_lines.append(f"WEBAPP_URL={url}")
                replaced = True
            else:
                new_lines.append(line)
        if not replaced:
            new_lines.append(f"WEBAPP_URL={url}")
        ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    def copy_url(self) -> None:
        if not self.tunnel_url:
            messagebox.showinfo(APP_TITLE, "URL ещё не получен. Сначала нажми Запустить.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.tunnel_url)
        messagebox.showinfo(APP_TITLE, "URL скопирован в буфер обмена.")

    def stop(self) -> None:
        for proc, name in [(self.server_process, "server"), (self.cloudflared_process, "cloudflared")]:
            if proc and proc.poll() is None:
                self.log(f"Останавливаю {name}...")
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        self.server_process = None
        self.cloudflared_process = None
        self.set_status("остановлено")
        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")

    def get_user_ids(self) -> list[int]:
        db_path = database_path()
        if not db_path.exists():
            return []
        with sqlite3.connect(db_path) as conn:
            if not table_exists(conn, "users"):
                return []
            rows = conn.execute("SELECT telegram_id FROM users ORDER BY created_at DESC").fetchall()
            return [int(r[0]) for r in rows]

    def show_user_count(self) -> None:
        count = len(self.get_user_ids())
        self.log(f"В базе пользователей: {count}")
        messagebox.showinfo(APP_TITLE, f"В базе пользователей: {count}")

    def send_broadcast(self) -> None:
        text = self.broadcast_text.get("1.0", END).strip()
        if not text:
            messagebox.showwarning(APP_TITLE, "Введите текст сообщения.")
            return
        env = parse_env()
        token = env.get("BOT_TOKEN", "")
        if not token or "PASTE" in token:
            messagebox.showerror(APP_TITLE, "BOT_TOKEN не найден в .env.")
            return

        if self.send_all_var.get():
            recipients = self.get_user_ids()
        else:
            raw = self.single_user_entry.get().strip()
            if not raw.isdigit():
                messagebox.showwarning(APP_TITLE, "Введите Telegram ID или выберите отправку всем.")
                return
            recipients = [int(raw)]

        if not recipients:
            messagebox.showwarning(APP_TITLE, "Получатели не найдены.")
            return

        if not messagebox.askyesno(APP_TITLE, f"Отправить сообщение получателям: {len(recipients)}?"):
            return

        threading.Thread(target=self._send_broadcast_worker, args=(token, recipients, text), daemon=True).start()

    def _send_broadcast_worker(self, token: str, recipients: list[int], text: str) -> None:
        ok = 0
        failed = 0
        for chat_id in recipients:
            try:
                self._send_telegram_message(token, chat_id, text)
                ok += 1
                self.log(f"sent: {chat_id}")
                time.sleep(0.05)
            except Exception as exc:
                failed += 1
                self.log(f"failed {chat_id}: {exc}")
        self.log(f"Рассылка завершена. OK: {ok}, failed: {failed}")
        messagebox.showinfo(APP_TITLE, f"Рассылка завершена.\nOK: {ok}\nFailed: {failed}")

    def _send_telegram_message(self, token: str, chat_id: int, text: str) -> None:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": str(chat_id),
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            if not payload.get("ok"):
                raise RuntimeError(payload)

    def refresh_dashboard(self) -> None:
        db_path = database_path()
        self.metrics_text.delete("1.0", END)
        for item in self.users_tree.get_children():
            self.users_tree.delete(item)
        if not db_path.exists():
            self.metrics_text.insert(END, f"База не найдена: {db_path}\n")
            return

        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            with conn:
                users_count = safe_count(conn, "users")
                badges_count = safe_count(conn, "badges")
                awarded_badges_count = safe_count(conn, "user_badges")
                active_games = safe_count(conn, "game_requests", "status IN ('open','pending')")
                open_games = safe_count(conn, "game_requests", "status='open'")
                pending_games = safe_count(conn, "game_requests", "status='pending'")
                confirmed_games = safe_count(conn, "game_requests", "status='confirmed'")
                completed_games = safe_count(conn, "game_requests", "status='completed'")
                cancelled_games = safe_count(conn, "game_requests", "status='cancelled'")
                expired_games = safe_count(conn, "game_requests", "status='expired'")
                responses_count = safe_count(conn, "responses")
                chat_count = 0
                for t in ("chat_messages", "messages", "game_messages"):
                    if table_exists(conn, t):
                        chat_count = safe_count(conn, t)
                        break
                ratings_count = safe_count(conn, "ratings")
                puzzle_count = 0
                for t in ("daily_puzzle_attempts", "puzzle_attempts", "daily_puzzle_solves"):
                    if table_exists(conn, t):
                        puzzle_count = safe_count(conn, t)
                        break

                self.metrics_text.insert(END, "Admin Dashboard v0.13.0\n")
                self.metrics_text.insert(END, f"Database: {db_path}\n\n")
                self.metrics_text.insert(END, f"Пользователи: {users_count}\n")
                self.metrics_text.insert(END, f"Значки: {badges_count} | выдано: {awarded_badges_count}\n")
                self.metrics_text.insert(END, f"Активные заявки: {active_games}  | open: {open_games} | pending: {pending_games}\n")
                self.metrics_text.insert(END, f"Confirmed партии: {confirmed_games} | completed: {completed_games}\n")
                self.metrics_text.insert(END, f"Cancelled: {cancelled_games} | expired: {expired_games}\n")
                self.metrics_text.insert(END, f"Отклики: {responses_count}\n")
                self.metrics_text.insert(END, f"Сообщения в чатах: {chat_count}\n")
                self.metrics_text.insert(END, f"Оценки: {ratings_count}\n")
                self.metrics_text.insert(END, f"Решения задачек: {puzzle_count}\n")

                if table_exists(conn, "users"):
                    cols = table_columns(conn, "users")
                    selected_cols = ["telegram_id"]
                    for c in ["username", "display_name", "first_name", "profile_city", "city", "rating_avg", "rating_count", "daily_streak", "current_streak", "puzzle_streak", "created_at"]:
                        if c in cols:
                            selected_cols.append(c)
                    for phone_col in ("phone", "phone_number", "contact_phone", "mobile", "telephone"):
                        if phone_col in cols and phone_col not in selected_cols:
                            selected_cols.append(phone_col)
                    sql = f"SELECT {', '.join(selected_cols)} FROM users ORDER BY created_at DESC LIMIT 25"
                    rows = conn.execute(sql).fetchall()
                    for row in rows:
                        contact = display_contact(row, cols)
                        display_name = row["display_name"] if "display_name" in cols and row["display_name"] else (row["first_name"] if "first_name" in cols else "")
                        city = row["profile_city"] if "profile_city" in cols and row["profile_city"] else (row["city"] if "city" in cols else "")
                        rating_avg = row["rating_avg"] if "rating_avg" in cols and row["rating_avg"] is not None else 0
                        rating_count = row["rating_count"] if "rating_count" in cols and row["rating_count"] is not None else 0
                        rating = f"{float(rating_avg):.1f}★/{int(rating_count)}"
                        streak = ""
                        for sc in ("daily_streak", "current_streak", "puzzle_streak"):
                            if sc in cols and row[sc] is not None:
                                streak = str(row[sc])
                                break
                        created = row["created_at"] if "created_at" in cols else ""
                        self.users_tree.insert("", END, values=(row["telegram_id"], contact, display_name, city, rating, streak, created))
        except Exception as exc:
            self.metrics_text.insert(END, f"Ошибка чтения базы: {exc}\n")
        finally:
            try:
                conn.close()
            except Exception:
                pass


    def _export_table_csv(self, table: str, default_prefix: str) -> None:
        db_path = database_path()
        if not db_path.exists():
            messagebox.showerror(APP_TITLE, f"База не найдена: {db_path}")
            return
        target = filedialog.asksaveasfilename(
            title=f"Экспорт {table} в CSV",
            defaultextension=".csv",
            initialfile=f"{default_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
        )
        if not target:
            return
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            if not table_exists(conn, table):
                messagebox.showerror(APP_TITLE, f"Таблица {table} не найдена")
                return
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            cols = [d[0] for d in conn.execute(f"SELECT * FROM {table} LIMIT 1").description] if rows else list(table_columns(conn, table))
            with open(target, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(cols)
                for row in rows:
                    writer.writerow([row[c] if c in row.keys() else "" for c in cols])
            messagebox.showinfo(APP_TITLE, f"CSV экспортирован:\n{target}")
            self.log(f"CSV exported: {table} -> {target}")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Ошибка CSV экспорта: {exc}")
        finally:
            try: conn.close()
            except Exception: pass

    def export_users_csv(self) -> None:
        self._export_table_csv("users", "users")

    def export_games_csv(self) -> None:
        self._export_table_csv("game_requests", "games")

    def admin_block_user(self) -> None:
        raw = self.admin_user_id_entry.get().strip() if hasattr(self, "admin_user_id_entry") else ""
        if not raw.isdigit():
            messagebox.showwarning(APP_TITLE, "Введите Telegram ID")
            return
        user_id = int(raw)
        if not messagebox.askyesno(APP_TITLE, f"Заблокировать пользователя {user_id} от имени admin/system?"):
            return
        try:
            conn = sqlite3.connect(database_path())
            with conn:
                conn.execute("CREATE TABLE IF NOT EXISTS user_blocks (blocker_telegram_id INTEGER NOT NULL, blocked_telegram_id INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (blocker_telegram_id, blocked_telegram_id))")
                conn.execute("INSERT OR REPLACE INTO user_blocks (blocker_telegram_id, blocked_telegram_id, created_at) VALUES (?, ?, ?)", (0, user_id, datetime.utcnow().isoformat()))
            messagebox.showinfo(APP_TITLE, "Пользователь добавлен в системный блок-лист")
            self.refresh_dashboard(); self.refresh_moderation()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Ошибка блокировки: {exc}")
        finally:
            try: conn.close()
            except Exception: pass

    def admin_unblock_user(self) -> None:
        raw = self.admin_user_id_entry.get().strip() if hasattr(self, "admin_user_id_entry") else ""
        if not raw.isdigit():
            messagebox.showwarning(APP_TITLE, "Введите Telegram ID")
            return
        user_id = int(raw)
        try:
            conn = sqlite3.connect(database_path())
            with conn:
                if table_exists(conn, "user_blocks"):
                    conn.execute("DELETE FROM user_blocks WHERE blocker_telegram_id = 0 AND blocked_telegram_id = ?", (user_id,))
            messagebox.showinfo(APP_TITLE, "Системный блок снят")
            self.refresh_dashboard(); self.refresh_moderation()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Ошибка снятия блока: {exc}")
        finally:
            try: conn.close()
            except Exception: pass

    def admin_cancel_game(self) -> None:
        raw = self.admin_game_id_entry.get().strip() if hasattr(self, "admin_game_id_entry") else ""
        if not raw.isdigit():
            messagebox.showwarning(APP_TITLE, "Введите Game ID")
            return
        game_id = int(raw)
        if not messagebox.askyesno(APP_TITLE, f"Отменить заявку/партию #{game_id}?"):
            return
        try:
            conn = sqlite3.connect(database_path())
            with conn:
                if not table_exists(conn, "game_requests"):
                    raise RuntimeError("Таблица game_requests не найдена")
                conn.execute("UPDATE game_requests SET status='cancelled', cancel_reason='Admin moderation', updated_at=? WHERE id=?", (datetime.utcnow().isoformat(), game_id))
            messagebox.showinfo(APP_TITLE, f"Заявка #{game_id} отменена")
            self.refresh_dashboard(); self.refresh_moderation()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Ошибка отмены заявки: {exc}")
        finally:
            try: conn.close()
            except Exception: pass

    def export_database(self) -> None:
        db_path = database_path()
        if not db_path.exists():
            messagebox.showerror(APP_TITLE, f"База не найдена: {db_path}")
            return
        default_name = f"chess_irl_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sqlite3"
        target = filedialog.asksaveasfilename(
            title="Сохранить копию базы",
            defaultextension=".sqlite3",
            initialfile=default_name,
            filetypes=[("SQLite database", "*.sqlite3"), ("All files", "*.*")],
        )
        if not target:
            return
        try:
            shutil.copy2(db_path, target)
            messagebox.showinfo(APP_TITLE, f"База экспортирована:\n{target}")
            self.log(f"DB exported: {target}")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Ошибка экспорта: {exc}")


    def ensure_badge_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS badges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                icon TEXT NOT NULL DEFAULT '🏅',
                description TEXT DEFAULT '',
                color TEXT DEFAULT '#2f8a4b',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_badges (
                telegram_id INTEGER NOT NULL,
                badge_id INTEGER NOT NULL,
                is_visible INTEGER NOT NULL DEFAULT 0,
                note TEXT DEFAULT '',
                awarded_at TEXT NOT NULL,
                PRIMARY KEY (telegram_id, badge_id)
            )
        """)

    def refresh_badges(self) -> None:
        if not hasattr(self, "badges_tree"):
            return
        for item in self.badges_tree.get_children():
            self.badges_tree.delete(item)
        self.badges_text.delete("1.0", END)
        db_path = database_path()
        if not db_path.exists():
            self.badges_text.insert(END, f"База не найдена: {db_path}\n")
            return
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            with conn:
                self.ensure_badge_tables(conn)
                rows = conn.execute("SELECT * FROM badges ORDER BY id DESC").fetchall()
                for r in rows:
                    self.badges_tree.insert("", END, values=(r["id"], r["icon"], r["title"], r["description"], r["color"], r["created_at"]))
                self.badges_text.insert(END, f"Значков создано: {len(rows)}\n")
                self.badges_text.insert(END, f"Выдано пользователям: {safe_count(conn, 'user_badges')}\n")
                self.badges_text.insert(END, "\nПодсказка: чтобы выдать значок, скопируй ID из таблицы и введи Telegram ID пользователя.\n")
        except Exception as exc:
            self.badges_text.insert(END, f"Ошибка чтения значков: {exc}\n")
        finally:
            try: conn.close()
            except Exception: pass

    def create_badge(self) -> None:
        title = self.badge_title_entry.get().strip()
        if not title:
            messagebox.showerror(APP_TITLE, "Введите название значка")
            return
        icon = self.badge_icon_entry.get().strip() or "🏅"
        color = self.badge_color_entry.get().strip() or "#2f8a4b"
        desc = self.badge_desc_entry.get().strip()
        db_path = database_path()
        try:
            conn = sqlite3.connect(db_path)
            with conn:
                self.ensure_badge_tables(conn)
                conn.execute(
                    "INSERT INTO badges (title, icon, description, color, created_at) VALUES (?, ?, ?, ?, ?)",
                    (title, icon, desc, color, datetime.now().isoformat(timespec='seconds')),
                )
            self.badge_title_entry.delete(0, END)
            self.badge_desc_entry.delete(0, END)
            self.refresh_badges()
            messagebox.showinfo(APP_TITLE, "Значок создан")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Ошибка создания значка: {exc}")
        finally:
            try: conn.close()
            except Exception: pass

    def award_badge(self) -> None:
        try:
            telegram_id = int(self.award_user_entry.get().strip())
            badge_id = int(self.award_badge_entry.get().strip())
        except Exception:
            messagebox.showerror(APP_TITLE, "Введите корректные Telegram ID и Badge ID")
            return
        note = self.award_note_entry.get().strip()
        db_path = database_path()
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            with conn:
                self.ensure_badge_tables(conn)
                user = conn.execute("SELECT telegram_id, username, display_name, first_name FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
                if not user:
                    messagebox.showerror(APP_TITLE, "Пользователь не найден в базе")
                    return
                badge = conn.execute("SELECT id, title FROM badges WHERE id=?", (badge_id,)).fetchone()
                if not badge:
                    messagebox.showerror(APP_TITLE, "Значок не найден")
                    return
                conn.execute(
                    """
                    INSERT INTO user_badges (telegram_id, badge_id, is_visible, note, awarded_at)
                    VALUES (?, ?, 0, ?, ?)
                    ON CONFLICT(telegram_id, badge_id) DO UPDATE SET note=excluded.note
                    """,
                    (telegram_id, badge_id, note, datetime.now().isoformat(timespec='seconds')),
                )
            self.refresh_badges()
            messagebox.showinfo(APP_TITLE, f"Значок #{badge_id} выдан пользователю {telegram_id}. Пользователь сможет выбрать его в профиле.")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Ошибка выдачи значка: {exc}")
        finally:
            try: conn.close()
            except Exception: pass

    def refresh_moderation(self) -> None:
        if not hasattr(self, "moderation_text"):
            return
        self.moderation_text.delete("1.0", END)
        db_path = database_path()
        if not db_path.exists():
            self.moderation_text.insert(END, f"База не найдена: {db_path}\n")
            return
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            with conn:
                self.moderation_text.insert(END, "Moderation Center v0.10\n")
                self.moderation_text.insert(END, f"Database: {db_path}\n\n")
                self.moderation_text.insert(END, f"Жалобы: {safe_count(conn, 'user_reports')}\n")
                self.moderation_text.insert(END, f"No-show: {safe_count(conn, 'game_requests', 'no_show_target_id IS NOT NULL')}\n")
                self.moderation_text.insert(END, f"Блокировки: {safe_count(conn, 'user_blocks')}\n")
                self.moderation_text.insert(END, f"Фото с партий: {safe_count(conn, 'game_photos')}\n")
                self.moderation_text.insert(END, f"Сообщения в чатах: {safe_count(conn, 'chat_messages')}\n")
                self.moderation_text.insert(END, f"Избранные игроки: {safe_count(conn, 'favorite_players')}\n")
                self.moderation_text.insert(END, "\n--- Последние жалобы ---\n")
                if table_exists(conn, "user_reports"):
                    rows = conn.execute(
                        "SELECT * FROM user_reports ORDER BY id DESC LIMIT 20"
                    ).fetchall()
                    for r in rows:
                        self.moderation_text.insert(
                            END,
                            f"#{r['id']} reporter={r['reporter_telegram_id']} reported={r['reported_telegram_id']} game={r['game_id']} reason={r['reason']} comment={r['comment']} at={r['created_at']}\n",
                        )
                self.moderation_text.insert(END, "\n--- No-show ---\n")
                if table_exists(conn, "game_requests"):
                    rows = conn.execute(
                        "SELECT id, creator_telegram_id, accepted_response_id, place, date_label, time_label, no_show_reported_by, no_show_target_id, updated_at FROM game_requests WHERE no_show_target_id IS NOT NULL ORDER BY updated_at DESC LIMIT 20"
                    ).fetchall()
                    for r in rows:
                        self.moderation_text.insert(END, f"game #{r['id']} place={r['place']} when={r['date_label']} {r['time_label']} reported_by={r['no_show_reported_by']} target={r['no_show_target_id']}\n")
                self.moderation_text.insert(END, "\n--- Последние фото ---\n")
                if table_exists(conn, "game_photos"):
                    rows = conn.execute("SELECT id, game_id, uploader_telegram_id, caption, created_at FROM game_photos ORDER BY id DESC LIMIT 20").fetchall()
                    for r in rows:
                        self.moderation_text.insert(END, f"photo #{r['id']} game={r['game_id']} uploader={r['uploader_telegram_id']} caption={r['caption']} at={r['created_at']}\n")
                self.moderation_text.insert(END, "\n--- Последние сообщения чатов ---\n")
                if table_exists(conn, "chat_messages"):
                    rows = conn.execute("SELECT id, game_id, sender_telegram_id, text, created_at FROM chat_messages ORDER BY id DESC LIMIT 30").fetchall()
                    for r in rows:
                        msg = (r['text'] or '').replace('\n', ' ')[:180]
                        self.moderation_text.insert(END, f"msg #{r['id']} game={r['game_id']} sender={r['sender_telegram_id']} text={msg} at={r['created_at']}\n")
        except Exception as exc:
            self.moderation_text.insert(END, f"Ошибка чтения moderation data: {exc}\n")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def export_moderation_report(self) -> None:
        if not hasattr(self, "moderation_text"):
            return
        default_name = f"chess_irl_moderation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        target = filedialog.asksaveasfilename(
            title="Сохранить moderation report",
            defaultextension=".txt",
            initialfile=default_name,
            filetypes=[("Text file", "*.txt"), ("All files", "*.*")],
        )
        if not target:
            return
        try:
            Path(target).write_text(self.moderation_text.get("1.0", END), encoding="utf-8")
            messagebox.showinfo(APP_TITLE, f"Moderation report сохранён:\n{target}")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Ошибка экспорта: {exc}")

    def on_close(self) -> None:
        if self.server_process or self.cloudflared_process:
            if not messagebox.askyesno(APP_TITLE, "Остановить сервер и закрыть launcher?"):
                return
        self.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    Launcher().run()
