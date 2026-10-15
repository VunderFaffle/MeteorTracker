import json
import subprocess
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


APP_DIR = Path(__file__).resolve().parent
DEFAULT_JSON = APP_DIR / "meteor_passes.json"
TRACKER_SCRIPT = APP_DIR / "skyshitassbitch.py"

COLUMNS = {
    "satellite": ("Спутник", 130, "text"),
    "aos_local": ("Дата и время AOS", 180, "datetime"),
    "aos_azimuth_deg": ("AOS азимут", 105, "number"),
    "maximum_local": ("Дата и время MAX", 180, "datetime"),
    "max_elevation_deg": ("Elevation", 95, "number"),
    "max_azimuth_deg": ("MAX азимут", 105, "number"),
    "los_local": ("Дата и время LOS", 180, "datetime"),
    "los_azimuth_deg": ("LOS азимут", 105, "number"),
    "aos_los_score_deg": ("Оценка AOS/LOS", 130, "number"),
    "match_type": ("Совпадение", 110, "text"),
    "matched_parameters": ("Совпало", 85, "number"),
    "total_miss_deg": ("Отклонение", 100, "number"),
}


class MeteorGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("METEOR Tracker — пролёты")
        self.geometry("1500x720")
        self.minsize(950, 480)
        self.json_path = DEFAULT_JSON
        self.rows: dict[str, dict] = {}
        self.sort_column = "aos_local"
        self.sort_reverse = False
        self.tracker_process: subprocess.Popen | None = None

        self._build_ui()
        self.load_json(show_error=False)

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self, padding=10)
        toolbar.pack(fill="x")

        ttk.Button(toolbar, text="Обновить расчёт", command=self.run_tracker).pack(side="left")
        ttk.Button(toolbar, text="Открыть JSON", command=self.choose_json).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Перечитать", command=self.load_json).pack(side="left", padx=(8, 18))

        ttk.Label(toolbar, text="Сортировать по:").pack(side="left")
        self.sort_choice = ttk.Combobox(
            toolbar,
            state="readonly",
            width=24,
            values=[info[0] for info in COLUMNS.values()],
        )
        self.sort_choice.current(list(COLUMNS).index(self.sort_column))
        self.sort_choice.pack(side="left", padx=6)
        self.sort_choice.bind("<<ComboboxSelected>>", self.sort_from_combobox)

        self.direction_button = ttk.Button(toolbar, text="По возрастанию ↑", command=self.toggle_direction)
        self.direction_button.pack(side="left")

        table_frame = ttk.Frame(self)
        table_frame.pack(fill="both", expand=True, padx=10)
        self.tree = ttk.Treeview(table_frame, columns=list(COLUMNS), show="headings")
        vertical = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        for key, (title, width, _) in COLUMNS.items():
            self.tree.heading(key, text=title, command=lambda column=key: self.sort_by_heading(column))
            self.tree.column(key, width=width, minwidth=75, anchor="center", stretch=False)

        self.status = tk.StringVar(value="JSON ещё не загружен")
        ttk.Label(self, textvariable=self.status, padding=(10, 8)).pack(fill="x")

    @staticmethod
    def display_value(key: str, value) -> str:
        if value is None:
            return ""
        value_type = COLUMNS[key][2]
        if value_type == "datetime":
            try:
                return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                return str(value)
        if value_type == "number":
            return f"{value:.1f}" if isinstance(value, float) else str(value)
        if key == "match_type":
            return {"exact": "точное", "close": "близкое"}.get(str(value), str(value))
        return str(value)

    def choose_json(self) -> None:
        selected = filedialog.askopenfilename(
            title="Открыть результаты пролётов",
            initialdir=self.json_path.parent,
            filetypes=(("JSON", "*.json"), ("Все файлы", "*.*")),
        )
        if selected:
            self.json_path = Path(selected)
            self.load_json()

    def load_json(self, show_error: bool = True) -> None:
        try:
            payload = json.loads(self.json_path.read_text(encoding="utf-8"))
            passes = payload.get("passes", [])
            if not isinstance(passes, list):
                raise ValueError("поле passes должно быть списком")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            self.status.set(f"Не удалось загрузить {self.json_path.name}")
            if show_error:
                messagebox.showerror("Ошибка JSON", str(exc))
            return

        self.rows.clear()
        self.tree.delete(*self.tree.get_children())
        for index, row in enumerate(passes):
            item_id = f"pass-{index}"
            self.rows[item_id] = row
            values = [self.display_value(key, row.get(key)) for key in COLUMNS]
            self.tree.insert("", "end", iid=item_id, values=values)
        self.apply_sort()
        generated = payload.get("generated_at_utc", "неизвестно")
        self.status.set(f"{self.json_path.name}: {len(passes)} пролётов · сформирован {generated}")

    def sort_value(self, item_id: str):
        value = self.rows[item_id].get(self.sort_column)
        value_type = COLUMNS[self.sort_column][2]
        if value is None:
            return float("inf") if value_type == "number" else ""
        if value_type == "number":
            try:
                return float(value)
            except (TypeError, ValueError):
                return float("inf")
        if value_type == "datetime":
            try:
                return datetime.fromisoformat(str(value)).timestamp()
            except ValueError:
                return float("inf")
        return str(value).casefold()

    def apply_sort(self) -> None:
        item_ids = list(self.tree.get_children())
        item_ids.sort(key=self.sort_value, reverse=self.sort_reverse)
        for position, item_id in enumerate(item_ids):
            self.tree.move(item_id, "", position)
        self.update_headings()

    def update_headings(self) -> None:
        for key, (title, _, _) in COLUMNS.items():
            marker = (" ↓" if self.sort_reverse else " ↑") if key == self.sort_column else ""
            self.tree.heading(key, text=title + marker)
        self.direction_button.configure(
            text="По убыванию ↓" if self.sort_reverse else "По возрастанию ↑"
        )

    def sort_by_heading(self, column: str) -> None:
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = False
        self.sort_choice.current(list(COLUMNS).index(column))
        self.apply_sort()

    def sort_from_combobox(self, _event=None) -> None:
        self.sort_column = list(COLUMNS)[self.sort_choice.current()]
        self.sort_reverse = False
        self.apply_sort()

    def toggle_direction(self) -> None:
        self.sort_reverse = not self.sort_reverse
        self.apply_sort()

    def run_tracker(self) -> None:
        if self.tracker_process is not None:
            return
        if not TRACKER_SCRIPT.exists():
            messagebox.showerror("Ошибка", f"Не найден файл {TRACKER_SCRIPT.name}")
            return
        self.status.set("Обновляю орбиты и рассчитываю пролёты…")
        self.update_idletasks()
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.tracker_process = subprocess.Popen(
            [sys.executable, str(TRACKER_SCRIPT)],
            cwd=APP_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=flags,
        )
        self.after(150, self._check_tracker)

    def _check_tracker(self) -> None:
        if self.tracker_process is None:
            return
        if self.tracker_process.poll() is None:
            self.after(150, self._check_tracker)
            return
        process = self.tracker_process
        self.tracker_process = None
        error = process.stderr.read().strip() if process.stderr else ""
        if process.returncode == 0:
            self.json_path = DEFAULT_JSON
            self.load_json()
        else:
            self.status.set("Расчёт завершился с ошибкой")
            messagebox.showerror("Ошибка расчёта", error or "Неизвестная ошибка")


MeteorGUI().mainloop()
