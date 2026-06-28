#!/usr/bin/env python3
"""Mirror external rule-sets into this repo as *readable text*, then build .mrs locally.

Rationale: third-party ``.mrs`` files are opaque binaries — nobody can review what
domains/IPs they contain. Instead of mirroring those binaries verbatim, we keep a
human-readable text source in the repo and compile the ``.mrs`` ourselves with
``mihomo convert-ruleset``. The template keeps referencing the same ``.mrs`` paths,
so nothing downstream changes.

Three kinds of sources (see SETS / PLAIN below):

1. Upstream already publishes text (MetaCubeX ``.list``, legiz ``.yaml``):
   download the text, then compile ``.mrs`` from it.
2. Upstream publishes only ``.mrs`` (hydraponique, a couple of legiz sets):
   download the ``.mrs``, decompile it to text for transparency, then recompile.
3. Sources that are already plain text/yaml and used as-is (PLAIN): just download.

Run by the ``mirror-external`` workflow on a schedule. Needs the ``mihomo`` binary
(found on PATH, via $MIHOMO_BIN, or auto-downloaded into .tools/).
"""
from __future__ import annotations

import gzip
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Версия mihomo для скачивания. По умолчанию "latest" — берём последний релиз
# с GitHub (как и workflow-файлы). Можно запинить через $MIHOMO_VERSION=vX.Y.Z.
MIHOMO_VERSION = os.environ.get("MIHOMO_VERSION", "latest")

# Фолбэк, если GitHub API недоступен при разрешении "latest".
MIHOMO_VERSION_FALLBACK = "v1.19.25"

# Sets that produce a .mrs. Each entry:
#   mrs          — destination .mrs (relative to repo root); referenced by the template
#   behavior     — domain | ipcidr | classical (must match the rule-provider)
#   text         — destination text source (relative); committed for transparency
#   text_format  — "text" (.list) or "yaml" (.yaml); how to compile text -> mrs
# Exactly one source of truth:
#   text_url     — upstream text to download directly, OR
#   mrs_url      — upstream .mrs to download and decompile into `text`
SETS: list[dict[str, str]] = [
    # --- MetaCubeX / meta-rules-dat: upstream ships .list next to .mrs ---
    *[
        {
            "mrs": f"rules/metacubex/geosite/{name}.mrs",
            "text": f"rules/metacubex/geosite/{name}.list",
            "behavior": "domain",
            "text_format": "text",
            "text_url": f"https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/{name}.list",
        }
        for name in (
            "category-ai-!cn",
            "category-porn",
            "private",
            "discord",
            "category-remote-control",
            "youtube",
            "google-deepmind",
        )
    ],
    *[
        {
            "mrs": f"rules/metacubex/geoip/{name}.mrs",
            "text": f"rules/metacubex/geoip/{name}.list",
            "behavior": "ipcidr",
            "text_format": "text",
            "text_url": f"https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geoip/{name}.list",
        }
        for name in ("cloudflare", "private")
    ],

    # --- legiz-ru: upstream ships .yaml next to .mrs ---
    {
        "mrs": "rules/legiz-ru/re-filter/domain-rule.mrs",
        "text": "rules/legiz-ru/re-filter/domain-rule.yaml",
        "behavior": "domain",
        "text_format": "yaml",
        "text_url": "https://raw.githubusercontent.com/legiz-ru/mihomo-rule-sets/main/re-filter/domain-rule.yaml",
    },
    {
        "mrs": "rules/legiz-ru/oisd/big.mrs",
        "text": "rules/legiz-ru/oisd/big.yaml",
        "behavior": "domain",
        "text_format": "yaml",
        "text_url": "https://raw.githubusercontent.com/legiz-ru/mihomo-rule-sets/main/oisd/big.yaml",
    },

    # --- legiz-ru: upstream ships ONLY .mrs -> decompile to text, recompile ---
    {
        "mrs": "rules/legiz-ru/other/discord-voice-ip-list.mrs",
        "text": "rules/legiz-ru/other/discord-voice-ip-list.list",
        "behavior": "ipcidr",
        "text_format": "text",
        "mrs_url": "https://raw.githubusercontent.com/legiz-ru/mihomo-rule-sets/main/other/discord-voice-ip-list.mrs",
    },
    {
        "mrs": "rules/legiz-ru/other/torrent-trackers.mrs",
        "text": "rules/legiz-ru/other/torrent-trackers.list",
        "behavior": "domain",
        "text_format": "text",
        "mrs_url": "https://raw.githubusercontent.com/legiz-ru/mihomo-rule-sets/main/other/torrent-trackers.mrs",
    },

    # --- hydraponique / roscomvpn-geosite: upstream ships ONLY .mrs -> decompile ---
    *[
        {
            "mrs": f"rules/roscomvpn/mrs/{dest}.mrs",
            "text": f"rules/roscomvpn/mrs/{dest}.list",
            "behavior": "domain",
            "text_format": "text",
            "mrs_url": f"https://raw.githubusercontent.com/hydraponique/roscomvpn-geosite/release/mihomo/{src}.mrs",
        }
        for src, dest in (
            ("whitelist", "whitelist"),
            ("win-spy", "win-spy"),
            ("torrent", "torrent-domains"),
            ("epicgames", "epicgames"),
            ("escapefromtarkov", "escapefromtarkov"),
            ("faceit", "faceit"),
            ("origin", "origin"),
            ("riot", "riot"),
            ("steam", "steam"),
        )
    ],
]

# Sources in v2ray/xray geosite text format (``domain:``/``full:``/``host:``/
# ``keyword:`` prefixes). mihomo's ``convert-ruleset domain text`` does NOT accept
# that format, so we transform each entry into mihomo's domain .list syntax first
# (``domain:``/``host:`` -> ``+.X`` suffix, ``full:`` -> ``X`` exact), commit the
# readable .list for transparency, then compile the .mrs.
V2RAY_DOMAIN_SETS: list[dict[str, str]] = [
    {
        "mrs": "rules/davoyan/category-ip-geo-detect.mrs",
        "text": "rules/davoyan/category-ip-geo-detect.list",
        "src_url": "https://raw.githubusercontent.com/Davoyan/xray-routing/main/domains/category-ip-geo-detect.txt",
    },
]

# Sources that are already plain text/yaml and consumed as-is by the template.
# (url, destination path relative to repo root)
PLAIN: list[tuple[str, str]] = [
    ("https://raw.githubusercontent.com/legiz-ru/mihomo-rule-sets/main/other/torrent-clients.yaml",
     "rules/legiz-ru/other/torrent-clients.yaml"),
    ("https://raw.githubusercontent.com/legiz-ru/mihomo-rule-sets/main/other/ru-app-list.yaml",
     "rules/legiz-ru/other/ru-app-list.yaml"),
    ("https://raw.githubusercontent.com/itdoginfo/allow-domains/main/Russia/inside-clashx.lst",
     "rules/itdoginfo/inside-clashx.lst"),
    ("https://raw.githubusercontent.com/roscomvpn/custom-category/release/mihomo/games.yaml",
     "rules/roscomvpn/games.yaml"),
    # legacy-список RU-доменов из апстрима Davoyan (используется как источник в category-ru.py)
    ("https://raw.githubusercontent.com/Davoyan/mihomo-rule-sets/main/domains/category-ru-legacy.txt",
     "rules/domains/category-ru-legacy.txt"),
    # NB: кастомные YAML НЕ зеркалятся — они ведутся вручную прямо в этом репозитории
    # и нигде в апстриме отсутствуют (лежат в rules/custom/):
    #   rules/custom/ai-process.yaml, games-launchers.yaml, games-proxy-rules.yaml, wine.yaml
]


def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "mihomo-rule-sets-mirror"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        status = getattr(resp, "status", None) or resp.getcode()
        if status != 200:
            raise RuntimeError(f"HTTP {status}")
        data = resp.read()
    if not data:
        raise RuntimeError("empty response")
    return data


def resolve_mihomo_version() -> str:
    """Вернуть тег версии mihomo. "latest" -> последний релиз с GitHub API."""
    if MIHOMO_VERSION != "latest":
        return MIHOMO_VERSION
    try:
        data = json.loads(
            download("https://api.github.com/repos/MetaCubeX/mihomo/releases/latest")
        )
        tag = data.get("tag_name")
        if tag:
            return tag
    except Exception as e:  # noqa: BLE001 - сеть/лимиты API: падаем на фолбэк
        print(f"-> latest mihomo lookup failed ({e}); fallback {MIHOMO_VERSION_FALLBACK}")
    return MIHOMO_VERSION_FALLBACK


def ensure_mihomo() -> str:
    """Return a path to a usable mihomo binary, downloading it if needed."""
    env_bin = os.environ.get("MIHOMO_BIN")
    if env_bin and Path(env_bin).is_file() and os.access(env_bin, os.X_OK):
        return env_bin
    found = shutil.which("mihomo")
    if found:
        return found

    tools_dir = REPO_ROOT / ".tools"
    local_bin = tools_dir / "mihomo"
    if local_bin.is_file() and os.access(local_bin, os.X_OK):
        return str(local_bin)

    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    if arch is None:
        raise RuntimeError(f"unsupported architecture: {machine}")
    if system not in ("linux", "darwin"):
        raise RuntimeError(f"unsupported OS: {system}")

    version = resolve_mihomo_version()
    asset = f"mihomo-{system}-{arch}-{version}.gz"
    url = f"https://github.com/MetaCubeX/mihomo/releases/download/{version}/{asset}"
    print(f"-> downloading mihomo ({asset})")
    tools_dir.mkdir(parents=True, exist_ok=True)
    raw = download(url)
    local_bin.write_bytes(gzip.decompress(raw))
    local_bin.chmod(0o755)
    return str(local_bin)


def convert_ruleset(mihomo: str, behavior: str, fmt: str, src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [mihomo, "convert-ruleset", behavior, fmt, str(src), str(dst)],
        check=True,
        capture_output=True,
        text=True,
    )


def v2ray_domains_to_mihomo(text: str) -> list[str]:
    """v2ray/xray geosite text -> mihomo domain .list entries (dedup, stable order).

    ``domain:X`` / ``host:X`` -> ``+.X`` (X и все поддомены)
    ``full:X``                -> ``X``   (точное совпадение)
    ``keyword:X``             -> пропуск (в domain-behavior нет подстрочного match)
    """
    seen: set[str] = set()
    out: list[str] = []
    skipped_keywords = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # отрезаем возможный inline-комментарий/атрибут (v2ray: "domain:x @attr")
        line = line.split()[0]

        if line.startswith("keyword:"):
            skipped_keywords += 1
            continue

        if line.startswith("full:"):
            domain = line[len("full:"):].strip().lstrip(".")
            entry = domain
        else:
            for prefix in ("domain:", "host:"):
                if line.startswith(prefix):
                    line = line[len(prefix):]
                    break
            domain = line.strip().lstrip(".")
            if domain.startswith("+."):
                domain = domain[2:]
            entry = "+." + domain if domain else ""

        if not entry or entry in seen:
            continue
        seen.add(entry)
        out.append(entry)

    if skipped_keywords:
        print(f"   (пропущено keyword:-записей: {skipped_keywords})")
    return out


def process_v2ray_domain_set(mihomo: str, item: dict[str, str]) -> None:
    text_path = REPO_ROOT / item["text"]
    mrs_path = REPO_ROOT / item["mrs"]

    print(f"-> v2ray {item['src_url']}")
    raw = download(item["src_url"]).decode("utf-8")
    entries = v2ray_domains_to_mihomo(raw)
    if not entries:
        raise RuntimeError("после трансформации не осталось доменов")

    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text("\n".join(entries) + "\n", encoding="utf-8")
    convert_ruleset(mihomo, "domain", "text", text_path, mrs_path)
    print(f"   built {item['mrs']} ({len(entries)} доменов)")


def process_set(mihomo: str, item: dict[str, str]) -> None:
    text_path = REPO_ROOT / item["text"]
    mrs_path = REPO_ROOT / item["mrs"]
    behavior = item["behavior"]

    if "text_url" in item:
        print(f"-> text  {item['text_url']}")
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_bytes(download(item["text_url"]))
    else:
        # Decompile upstream .mrs into readable text, then recompile from it.
        print(f"-> mrs   {item['mrs_url']} (decompile)")
        with tempfile.NamedTemporaryFile(suffix=".mrs", delete=False) as tmp:
            tmp.write(download(item["mrs_url"]))
            tmp_path = Path(tmp.name)
        try:
            convert_ruleset(mihomo, behavior, "mrs", tmp_path, text_path)
        finally:
            tmp_path.unlink(missing_ok=True)

    convert_ruleset(mihomo, behavior, item["text_format"], text_path, mrs_path)
    print(f"   built {item['mrs']} ({text_path.stat().st_size} B text)")


def main() -> int:
    failures: list[str] = []

    mihomo = ensure_mihomo()
    print(f"mihomo: {mihomo}\n")

    for item in SETS:
        label = item.get("text_url") or item.get("mrs_url") or item["mrs"]
        try:
            process_set(mihomo, item)
        except subprocess.CalledProcessError as e:  # noqa: PERF203
            print(f"   [ERROR] convert-ruleset failed: {e.stderr or e}")
            failures.append(label)
        except Exception as e:  # noqa: BLE001 - report and continue
            print(f"   [ERROR] {e}")
            failures.append(label)

    for item in V2RAY_DOMAIN_SETS:
        label = item.get("src_url") or item["mrs"]
        try:
            process_v2ray_domain_set(mihomo, item)
        except subprocess.CalledProcessError as e:  # noqa: PERF203
            print(f"   [ERROR] convert-ruleset failed: {e.stderr or e}")
            failures.append(label)
        except Exception as e:  # noqa: BLE001 - report and continue
            print(f"   [ERROR] {e}")
            failures.append(label)

    for url, rel_path in PLAIN:
        dest = REPO_ROOT / rel_path
        print(f"-> plain {url}")
        try:
            data = download(url)
        except Exception as e:  # noqa: BLE001
            print(f"   [ERROR] {e}")
            failures.append(url)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        print(f"   saved {len(data)} bytes -> {rel_path}")

    total = len(SETS) + len(V2RAY_DOMAIN_SETS) + len(PLAIN)
    if failures:
        print(f"\nFailed on {len(failures)} source(s):", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print(
        f"\nDone. Processed {total} source(s) "
        f"({len(SETS)} built, {len(V2RAY_DOMAIN_SETS)} v2ray, {len(PLAIN)} plain)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
