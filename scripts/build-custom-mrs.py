#!/usr/bin/env python3
"""Скомпилировать кастомные .list из rules/custom/ в .mrs.

Эти списки ведутся ВРУЧНУЮ (в отличие от зеркал в mirror-external.py) и
нигде в апстриме не существуют. Скрипт берёт каждый ``rules/custom/<kind>/*.list``
и собирает рядом одноимённый ``.mrs`` через ``mihomo convert-ruleset``.

behavior выбирается по имени каталога:
  geosite/ -> domain
  geoip/   -> ipcidr

mihomo ищется в: $MIHOMO_BIN, затем в PATH, затем .tools/mihomo.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CUSTOM_DIR = REPO_ROOT / "rules" / "custom"

# Каталог -> behavior для convert-ruleset
DIR_BEHAVIOR = {
    "geosite": "domain",
    "geoip": "ipcidr",
}


def find_mihomo() -> str:
    env_bin = os.environ.get("MIHOMO_BIN")
    if env_bin and Path(env_bin).is_file() and os.access(env_bin, os.X_OK):
        return env_bin
    found = shutil.which("mihomo")
    if found:
        return found
    local_bin = REPO_ROOT / ".tools" / "mihomo"
    if local_bin.is_file() and os.access(local_bin, os.X_OK):
        return str(local_bin)
    raise SystemExit(
        "mihomo не найден. Укажите $MIHOMO_BIN, поставьте в PATH "
        "или положите бинарь в .tools/mihomo"
    )


def main() -> int:
    mihomo = find_mihomo()
    print(f"mihomo: {mihomo}\n")

    list_files = sorted(CUSTOM_DIR.glob("*/*.list"))
    if not list_files:
        print("Нет .list файлов в rules/custom/*/ — нечего собирать.")
        return 0

    failures: list[str] = []
    built = 0
    for src in list_files:
        kind = src.parent.name
        behavior = DIR_BEHAVIOR.get(kind)
        if behavior is None:
            print(f"  [SKIP] {src.relative_to(REPO_ROOT)}: неизвестный каталог '{kind}'")
            continue
        dst = src.with_suffix(".mrs")
        try:
            subprocess.run(
                [mihomo, "convert-ruleset", behavior, "text", str(src), str(dst)],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"  [ERROR] {src.relative_to(REPO_ROOT)}: {e.stderr or e}")
            failures.append(str(src.relative_to(REPO_ROOT)))
            continue
        built += 1
        print(f"  built {dst.relative_to(REPO_ROOT)} ({behavior})")

    if failures:
        print(f"\nFailed on {len(failures)} file(s):", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print(f"\nDone. Built {built} .mrs file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
