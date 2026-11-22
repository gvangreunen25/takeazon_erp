#!/usr/bin/env python3
"""Auto-discovers the Streamlit entrypoint in `takeazon_erp` and registers `modules.ecomm_research`."""

from pathlib import Path
import re
import shutil

ROOT_DIR = Path("/Users/gjvangreunen/workspace/takeazon_erp")
PAGE_LABEL = "🧭 Multi-Channel Wave Research"


def find_entrypoint(root_dir: Path) -> Path:
    if not root_dir.exists():
        raise FileNotFoundError(f"Directory not found: {root_dir}")

    root_py_files = sorted(root_dir.glob("*.py"))
    # Prefer the file that imports from `modules` or calls `.render(`
    for py_file in root_py_files:
        if py_file.name.startswith("gemini-code-"):
            continue
        text = py_file.read_text(encoding="utf-8", errors="ignore")
        if "modules" in text and (".render" in text or "streamlit" in text):
            return py_file

    available = [f.name for f in root_py_files if not f.name.startswith("gemini-code-")]
    raise FileNotFoundError(
        f"Could not identify the main entrypoint in {root_dir}. Root .py files found: {available}"
    )


def register_ecomm_research() -> None:
    app_path = find_entrypoint(ROOT_DIR)
    print(f"Detected main entrypoint: {app_path}")

    original = app_path.read_text(encoding="utf-8")
    updated = original

    # 1. Ensure `ecomm_research` is imported from `modules`
    if not re.search(r"\becomm_research\b", updated):
        block_match = re.search(
            r"(from\s+modules\s+import\s*\(\s*\n)(.*?)(\n\))",
            updated,
            flags=re.DOTALL,
        )
        line_match = re.search(r"(from\s+modules\s+import\s+[^\n]+)", updated)

        if block_match:
            h, body, f = block_match.groups()
            updated = (
                updated[: block_match.start()]
                + f"{h}    ecomm_research,\n{body}{f}"
                + updated[block_match.end() :]
            )
        elif line_match:
            updated = updated[: line_match.end()] + ", ecomm_research" + updated[line_match.end() :]
        else:
            updated = "from modules import ecomm_research\n" + updated

    # 2. Register route in entrypoint (supports Dict routing or List + if/elif routing)
    if "ecomm_research.render" not in updated:
        dict_entry_match = re.search(
            r'^([ \t]*)(["\'][^"\']+["\']\s*:\s*[a-zA-Z0-9_]+\.render\s*,?[ \t]*)$',
            updated,
            flags=re.MULTILINE,
        )
        elif_match = re.search(
            r'^([ \t]*elif\s+([a-zA-Z0-9_]+)\s*==\s*["\'][^"\']+["\']\s*:\s*\n)([ \t]+)([a-zA-Z0-9_]+\.render\([^\n]*\))',
            updated,
            flags=re.MULTILINE,
        )

        if dict_entry_match:
            indent, existing_line = dict_entry_match.groups()
            line_with_comma = existing_line if existing_line.rstrip().endswith(",") else existing_line.rstrip() + ","
            new_line = f'{indent}"{PAGE_LABEL}": ecomm_research.render,'
            updated = (
                updated[: dict_entry_match.start()]
                + f"{indent}{line_with_comma}\n{new_line}"
                + updated[dict_entry_match.end() :]
            )
            print(f"Inserted route into routing dictionary next to `{existing_line.strip()}`.")

        elif elif_match:
            _, var_name, body_indent, call_expr = elif_match.groups()
            arg_match = re.search(r"\.render\(([^)]*)\)", call_expr)
            render_args = arg_match.group(1) if arg_match else "auth_user"

            new_branch = f'elif {var_name} == "{PAGE_LABEL}":\n{body_indent}ecomm_research.render({render_args})\n'
            updated = updated[: elif_match.start()] + new_branch + updated[elif_match.start() :]

            list_item_match = re.search(
                r'^([ \t]*)(["\']🛒[^"\']+["\']\s*,?[ \t]*)$',
                updated,
                flags=re.MULTILINE,
            )
            if list_item_match and PAGE_LABEL not in updated:
                l_indent, l_line = list_item_match.groups()
                l_comma = l_line if l_line.rstrip().endswith(",") else l_line.rstrip() + ","
                updated = (
                    updated[: list_item_match.start()]
                    + f'{l_indent}{l_comma}\n{l_indent}"{PAGE_LABEL}",'
                    + updated[list_item_match.end() :]
                )
            print("Inserted route into `elif` page router.")

        else:
            print(f"\nCould not auto-detect routing pattern in {app_path.name}. Relevant lines:\n")
            for idx, line in enumerate(original.splitlines(), 1):
                if ".render" in line or "modules" in line or "radio" in line or "selectbox" in line or "navigation" in line:
                    print(f"  Line {idx}: {line}")
            return

    if updated == original:
        print(f"`ecomm_research` is already registered in {app_path.name}.")
        return

    backup_path = app_path.with_suffix(".py.bak")
    shutil.copy2(app_path, backup_path)
    app_path.write_text(updated, encoding="utf-8")
    print(f"Successfully updated {app_path} (backup saved at {backup_path}).")


if __name__ == "__main__":
    register_ecomm_research()