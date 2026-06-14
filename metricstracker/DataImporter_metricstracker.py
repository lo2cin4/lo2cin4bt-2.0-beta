

import glob
import os

from rich.table import Table

from .utils.ConsoleUtils_utils_metricstracker import get_console

console = get_console()


def list_parquet_files(directory):

    pattern = os.path.join(directory, "*.parquet")
    return sorted(glob.glob(pattern))


def show_parquet_files(files):

    table = Table(title="可用 Parquet 檔案", show_lines=True, border_style="#dbac30")
    table.add_column("編號", style="bold white", no_wrap=True)
    table.add_column("檔案名稱", style="bold white", no_wrap=True)

    for idx, file in enumerate(files, 1):
        table.add_row(
            f"[white]{idx}[/white]", f"[#1e90ff]{os.path.basename(file)}[/#1e90ff]"
        )

    console.print(table)


def select_files(files, user_input):
    """
    ????????????????????? deterministic fallback?
    """
    user_input = str(user_input or "").strip().lower()
    if user_input in ("", "all", "*"):
        return files
    try:
        idxs = [int(x) for x in user_input.split(",") if x.strip().isdigit()]
        selected = [files[i - 1] for i in idxs if 1 <= i <= len(files)]
        if selected:
            return selected
        return files
    except Exception:
        return files
