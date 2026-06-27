#!/usr/bin/env python3
"""Единая точка входа: подтянуть внешние источники и собрать ВСЕ .mrs.

Что делает:
  1) Подтягивает изменения из удалённых репозиториев:
       - scripts/mirror-external.py  (MetaCubeX / legiz / roscomvpn / itdoginfo)
       - scripts/category-ru.py      (RU-домены; нужен пакет requests)
  2) Собирает все .mrs:
       - зеркальные .mrs (внутри mirror-external.py)
       - rules/category-ru.mrs        (из category-ru.yaml)
       - rules/ip-for-ru/lists/*.mrs  (из уже закоммиченных *.yaml)
       - rules/custom/**/*.mrs        (через scripts/build-custom-mrs.py)
  3) Безотказность: если mihomo не найден в системе — скачивает его сам
     под текущую ОС/архитектуру (Linux / macOS / Windows, amd64 / arm64).

Примечание про ip-for-ru: исходные данные (IPinfo CSV + MaxMind MMDB) требуют
приватных секретов, поэтому здесь данные НЕ обновляются — только пересобираются
.mrs из уже имеющихся .yaml. Полное обновление этих списков делает CI
(.github/workflows/build-ip-for-ru.yml) с секретами репозитория.

Запуск:  python scripts/build-all.py
Опции через окружение:
  MIHOMO_BIN=/path/to/mihomo   — использовать конкретный бинарь
  MIHOMO_VERSION=v1.19.27      — запинить версию (по умолчанию latest)
"""
from __future__ import annotations

import gzip
import importlib.util
import io
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / ".tools"

MIHOMO_VERSION = os.environ.get("MIHOMO_VERSION", "latest")
MIHOMO_VERSION_FALLBACK = "v1.19.27"
GH_API_LATEST = "https://api.github.com/repos/MetaCubeX/mihomo/releases/latest"
GH_DL = "https://github.com/MetaCubeX/mihomo/releases/download"


# --------------------------------------------------------------------------- #
# mihomo: кроссплатформенный резолвер с авто-загрузкой
# --------------------------------------------------------------------------- #
def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "mihomo_based_by_davoyan/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _resolve_version() -> str:
    if MIHOMO_VERSION != "latest":
        return MIHOMO_VERSION
    try:
        tag = json.loads(_download(GH_API_LATEST)).get("tag_name")
        if tag:
            return tag
    except Exception as e:  # noqa: BLE001
        print(f"  ! не удалось узнать latest mihomo ({e}); фолбэк {MIHOMO_VERSION_FALLBACK}")
    return MIHOMO_VERSION_FALLBACK


def _platform_asset(version: str) -> tuple[str, str]:
    """Вернуть (asset_filename, ext) под текущую ОС/архитектуру."""
    sysname = platform.system().lower()
    sys_map = {"linux": "linux", "darwin": "darwin", "windows": "windows"}
    osname = sys_map.get(sysname)
    if osname is None:
        raise RuntimeError(f"неподдерживаемая ОС: {platform.system()}")

    machine = platform.machine().lower()
    arch = {
        "x86_64": "amd64", "amd64": "amd64", "x64": "amd64",
        "aarch64": "arm64", "arm64": "arm64",
    }.get(machine)
    if arch is None:
        raise RuntimeError(f"неподдерживаемая архитектура: {machine}")

    ext = "zip" if osname == "windows" else "gz"
    return f"mihomo-{osname}-{arch}-{version}.{ext}", ext


def _extract_to_tools(raw: bytes, ext: str) -> Path:
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    if ext == "gz":
        dst = TOOLS_DIR / "mihomo"
        dst.write_bytes(gzip.decompress(raw))
    else:  # zip (Windows) — внутри лежит .exe
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            exe = next((n for n in zf.namelist() if n.lower().endswith(".exe")), None)
            if exe is None:
                raise RuntimeError("в zip-архиве mihomo не найден .exe")
            dst = TOOLS_DIR / "mihomo.exe"
            dst.write_bytes(zf.read(exe))
    dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return dst


def ensure_mihomo() -> str:
    # 1) явный путь
    env_bin = os.environ.get("MIHOMO_BIN")
    if env_bin and Path(env_bin).is_file():
        return env_bin
    # 2) PATH
    found = shutil.which("mihomo") or shutil.which("mihomo.exe")
    if found:
        return found
    # 3) ранее скачанный в .tools
    for name in ("mihomo", "mihomo.exe"):
        cached = TOOLS_DIR / name
        if cached.is_file():
            return str(cached)
    # 4) качаем
    version = _resolve_version()
    asset, ext = _platform_asset(version)
    url = f"{GH_DL}/{version}/{asset}"
    print(f"-> mihomo не найден, скачиваю {asset}")
    path = _extract_to_tools(_download(url), ext)
    print(f"   ок: {path}")
    return str(path)


# --------------------------------------------------------------------------- #
# Шаги сборки (под-скрипты импортируются in-process — без зависимости от
# sys.executable, что делает запуск устойчивым к любому способу старта Python)
# --------------------------------------------------------------------------- #
def load_module(script_rel: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / script_rel)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def call_main(script_rel: str, name: str) -> None:
    """Импортировать под-скрипт и вызвать его main(); ненулевой код = ошибка."""
    mod = load_module(script_rel, name)
    try:
        rc = mod.main()
    except SystemExit as e:  # некоторые скрипты делают sys.exit(N)
        rc = e.code
    if rc:
        raise RuntimeError(f"{script_rel} -> код {rc}")


def convert(mihomo: str, behavior: str, fmt: str, src: Path, dst: Path) -> None:
    subprocess.run(
        [mihomo, "convert-ruleset", behavior, fmt, str(src), str(dst)],
        check=True, capture_output=True, text=True,
    )


def python_exe() -> str:
    """Надёжно найти интерпретатор Python (для pip)."""
    if Path(sys.executable).name.lower().startswith("python"):
        return sys.executable
    return shutil.which("python3") or shutil.which("python") or sys.executable


def ensure_requests() -> bool:
    try:
        import requests  # noqa: F401
        return True
    except ImportError:
        print("-> ставлю пакет requests (нужен для category-ru.py)")
        try:
            subprocess.run(
                [python_exe(), "-m", "pip", "install", "--quiet", "requests"], check=True
            )
            import requests  # noqa: F401
            return True
        except Exception as e:  # noqa: BLE001
            print(f"  ! не удалось установить requests ({e}) — пропускаю обновление category-ru")
            return False


def main() -> int:
    print("== build-all ==\n")
    mihomo = ensure_mihomo()
    os.environ["MIHOMO_BIN"] = mihomo  # под-скрипты подхватят этот же бинарь
    print(f"mihomo: {mihomo}\n")

    failures: list[str] = []

    def step(label: str, fn) -> None:
        print(f"== {label} ==")
        try:
            fn()
            print(f"   OK: {label}\n")
        except subprocess.CalledProcessError as e:  # noqa: PERF203
            print(f"   FAIL: {label}: {(e.stderr or e.stdout or e)}\n")
            failures.append(label)
        except Exception as e:  # noqa: BLE001
            print(f"   FAIL: {label}: {e}\n")
            failures.append(label)

    # 1) Зеркала внешних источников (+ их .mrs)
    step("mirror-external (внешние источники + .mrs)",
         lambda: call_main("scripts/mirror-external.py", "mirror_external"))

    # 2) category-ru: обновить из апстрима (если есть requests) и собрать .mrs
    def category_ru() -> None:
        if ensure_requests():
            call_main("scripts/category-ru.py", "category_ru")
        else:
            print("   (пропуск обновления; собираю .mrs из имеющегося category-ru.yaml)")
        src = REPO_ROOT / "rules/category-ru.yaml"
        if not src.is_file():
            raise RuntimeError("нет rules/category-ru.yaml")
        convert(mihomo, "domain", "yaml", src, REPO_ROOT / "rules/category-ru.mrs")
    step("category-ru (RU-домены + .mrs)", category_ru)

    # 3) ip-for-ru: только пересборка .mrs из закоммиченных .yaml (данные требуют секретов)
    def ip_for_ru() -> None:
        yamls = sorted((REPO_ROOT / "rules/ip-for-ru/lists").glob("*.yaml"))
        if not yamls:
            raise RuntimeError("нет rules/ip-for-ru/lists/*.yaml")
        for y in yamls:
            convert(mihomo, "ipcidr", "yaml", y, y.with_suffix(".mrs"))
            print(f"   built {y.with_suffix('.mrs').relative_to(REPO_ROOT)}")
    step("ip-for-ru (.mrs из .yaml; данные обновляет CI)", ip_for_ru)

    # 4) Кастомные .mrs
    step("custom (.mrs из rules/custom/**/*.list)",
         lambda: call_main("scripts/build-custom-mrs.py", "build_custom_mrs"))

    # Итог
    total_mrs = len(list((REPO_ROOT / "rules").rglob("*.mrs")))
    print("== Итог ==")
    print(f"  .mrs в репозитории: {total_mrs}")
    if failures:
        print(f"  Ошибки на шагах ({len(failures)}):")
        for f in failures:
            print(f"    - {f}")
        return 1
    print("  Все шаги выполнены успешно.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
