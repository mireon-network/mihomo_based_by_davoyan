#!/usr/bin/env python3
"""Собрать блок GeForce NOW для rules/custom/games.yaml.

Источники:
  - https://static.nvidiagrid.net/supported-public-game-list/locales/gfnpc-en-US.json
  - https://gist.github.com/Gr3gorywolf/1757c79ce1152966bf77bf8c6d069161 (gamedatabase.json)
  - https://github.com/jsnli/steamappidlist — data/games_appid.json (appid → имя Steam)

Скрипт идемпотентен: ручная «шапка» (всё до маркера GFN) сохраняется как есть,
а блок GeForce NOW и ручной хвост перегенерируются при каждом запуске.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GAMES_YAML = ROOT / "rules/custom/games.yaml"

GFN_URL = "https://static.nvidiagrid.net/supported-public-game-list/locales/gfnpc-en-US.json"
GDB_URL = "https://gist.githubusercontent.com/Gr3gorywolf/1757c79ce1152966bf77bf8c6d069161/raw/gamedatabase.json"
STEAM_URL = "https://raw.githubusercontent.com/jsnli/steamappidlist/master/data/games_appid.json"

GFN_MARKER = "  # --- GeForce NOW"

# Не попадают в games.yaml — см. rules/custom/games-launchers.yaml
LAUNCHER_PROCESS_EXACT = frozenset(
    {
        "battalionlauncher.exe",
        "steamlauncher.exe",
        "launcher.exe",
        "slauncher.exe",
        "dundeflauncher.exe",
        "wowslauncher.exe",
        "mycomgames.exe",
    }
)


def is_launcher_process(proc: str) -> bool:
    pl = proc.lower()
    if pl in LAUNCHER_PROCESS_EXACT:
        return True
    return pl.endswith("launcher.exe")

# Жанры GFN, где в метаданных явно указан сетевой/мультиплеерный геймплей.
ONLINE_GENRE_KEYWORDS = (
    "multiplayer",
    "massively multiplayer",
    "massively multiplayer online",
    "free to play",
    "free-to-play",
    "battle royale",
    "mmo",
    "online co-op",
    "online co op",
    " co-op",
    "co-op",
    "pvp",
    "competitive",
    "esports",
    "cross-platform multiplayer",
    "cross platform multiplayer",
)


def needs_online_gameplay(g: dict) -> bool:
    genres = " | ".join(x.lower() for x in (g.get("genres") or []))
    return any(k in genres for k in ONLINE_GENRE_KEYWORDS)


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "mihomo_based_by_davoyan/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[™®©]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def main() -> int:
    gfn = json.loads(fetch(GFN_URL))
    gdb = json.loads(fetch(GDB_URL))
    steam = json.loads(fetch(STEAM_URL))
    appid_name = {str(x["appid"]): x["name"] for x in steam}

    name_to_proc: dict[str, str] = {}
    norm_to_proc: dict[str, str] = {}
    gdb_norms: list[tuple[str, str]] = []
    for e in gdb:
        n = (e.get("Name") or "").strip()
        p = (e.get("processName") or "").strip()
        if n and p:
            name_to_proc[n.lower()] = p
            nn = norm(n)
            norm_to_proc[nn] = p
            gdb_norms.append((nn, p))

    def match_exact(title: str | None) -> str | None:
        if not title:
            return None
        return name_to_proc.get(title.lower()) or norm_to_proc.get(norm(title))

    def match_substring(title: str | None) -> str | None:
        if not title:
            return None
        nt = norm(title)
        if len(nt) < 4:
            return None
        best: str | None = None
        best_len = 0
        for gn, proc in gdb_norms:
            if gn in nt or nt in gn:
                overlap = min(len(gn), len(nt))
                if overlap > best_len and overlap >= max(6, int(len(nt) * 0.6)):
                    best_len = overlap
                    best = proc
        return best

    def steam_appid(g: dict) -> str | None:
        m = re.search(r"/app/(\d+)", g.get("steamUrl") or "")
        return m.group(1) if m else None

    def resolve(g: dict) -> tuple[str | None, str]:
        proc = match_exact(g["title"])
        if proc:
            return proc, "gfn_title"
        aid = steam_appid(g)
        if aid:
            proc = match_exact(appid_name.get(aid))
            if proc:
                return proc, "steam_name"
        proc = match_substring(g["title"])
        if proc:
            return proc, "substring"
        if aid:
            proc = match_substring(appid_name.get(aid, ""))
            if proc:
                return proc, "steam_substring"
        return None, "none"

    base = GAMES_YAML.read_text(encoding="utf-8")
    if GFN_MARKER in base:
        base = base.split(GFN_MARKER)[0].rstrip() + "\n"

    existing: set[str] = set()
    for m in re.finditer(r"PROCESS-NAME,([^\n]+)", base):
        v = m.group(1).split("#", 1)[0].strip()  # отсечь инлайн-комментарий
        if v and not v.startswith("(?"):
            existing.add(v.lower())

    entries: list[tuple[str, str, str, str, list]] = []
    stats: dict[str, int] = {}
    for g in sorted(
        (x for x in gfn if x.get("status") == "AVAILABLE"),
        key=lambda x: x["title"].lower(),
    ):
        if not needs_online_gameplay(g):
            stats["skipped_offline"] = stats.get("skipped_offline", 0) + 1
            continue
        proc, via = resolve(g)
        if not proc:
            stats["skipped_no_exe"] = stats.get("skipped_no_exe", 0) + 1
            continue
        if is_launcher_process(proc):
            stats["skipped_launcher"] = stats.get("skipped_launcher", 0) + 1
            continue
        stats[via] = stats.get(via, 0) + 1
        pl = proc.lower()
        if pl in existing:
            continue
        existing.add(pl)
        entries.append(
            (g["title"], proc, via, g.get("store", ""), g.get("genres") or [])
        )

    lines = [
        GFN_MARKER + " (только онлайн/MP по жанрам GFN) ---",
        "  # JSON: " + GFN_URL,
        "  # exe:  " + "https://gist.github.com/Gr3gorywolf/1757c79ce1152966bf77bf8c6d069161",
        "  # Steam appid→name: https://github.com/jsnli/steamappidlist (games_appid.json)",
        f"  # добавлено {len(entries)} PROCESS-NAME",
        f"  # пропущено офлайн (нет MP/MMO/F2P online в жанрах): {stats.get('skipped_offline', 0)}",
        f"  # пропущено без exe в gamedatabase: {stats.get('skipped_no_exe', 0)}",
        "",
    ]
    for title, proc, via, store, genres in entries:
        g = ", ".join(genres[:3]) if genres else ""
        meta = f"{store} | {g}" if g else store
        lines.append(f"  # {title} ({meta}; match={via})")
        lines.append(f"  - PROCESS-NAME,{proc}")
        lines.append("")

    manual = (
        "\n  # --- Добавленно вручную (нет в GFN / не попали в фильтр жанров) ---\n"
        "  # R.E.P.O. — co-op онлайн, Steam 3241660; в gfnpc-en-US.json отсутствует\n"
        "  # (REPO/Overwolf/Tanki — только Windows; на Linux Proton видит REPO.exe)\n"
        "  - PROCESS-NAME,REPO.exe\n"
        "  - PROCESS-NAME,REPO-Win64-Shipping.exe\n"
        "  - PROCESS-NAME,Raft.exe\n"
        "  - PROCESS-NAME,Raft                  # Raft — нативный macOS\n"
        "  - PROCESS-NAME,Tanki.exe\n"
        "  - PROCESS-NAME,Overwolf.exe\n"
    )
    out = base + "\n".join(lines) + manual
    GAMES_YAML.write_text(out, encoding="utf-8")
    print(f"Wrote {GAMES_YAML} (+{len(entries)} rules, stats={stats})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
