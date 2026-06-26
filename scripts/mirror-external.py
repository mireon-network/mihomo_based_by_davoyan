#!/usr/bin/env python3
"""Mirror external rule-sets referenced by the Remnawave template into this repo.

Downloads every upstream source listed in SOURCES into the ``rules/`` directory,
so the template can reference our own repository instead of third-party hosts.
Run by the ``mirror-external`` workflow on a schedule.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

# (upstream url, destination path relative to repo root)
SOURCES: list[tuple[str, str]] = [
    # MetaCubeX / meta-rules-dat
    ("https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/category-ai-!cn.mrs",
     "rules/metacubex/geosite/category-ai-!cn.mrs"),
    ("https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/category-porn.mrs",
     "rules/metacubex/geosite/category-porn.mrs"),
    ("https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/private.mrs",
     "rules/metacubex/geosite/private.mrs"),
    ("https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/discord.mrs",
     "rules/metacubex/geosite/discord.mrs"),
    ("https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/speedtest.mrs",
     "rules/metacubex/geosite/speedtest.mrs"),
    ("https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/category-remote-control.mrs",
     "rules/metacubex/geosite/category-remote-control.mrs"),
    ("https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/youtube.mrs",
     "rules/metacubex/geosite/youtube.mrs"),
    ("https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/google-deepmind.mrs",
     "rules/metacubex/geosite/google-deepmind.mrs"),
    ("https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/telegram.mrs",
     "rules/metacubex/geosite/telegram.mrs"),
    ("https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geoip/telegram.mrs",
     "rules/metacubex/geoip/telegram.mrs"),
    ("https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geoip/cloudflare.mrs",
     "rules/metacubex/geoip/cloudflare.mrs"),

    # legiz-ru / mihomo-rule-sets
    ("https://raw.githubusercontent.com/legiz-ru/mihomo-rule-sets/main/other/discord-voice-ip-list.mrs",
     "rules/legiz-ru/other/discord-voice-ip-list.mrs"),
    ("https://raw.githubusercontent.com/legiz-ru/mihomo-rule-sets/main/re-filter/domain-rule.mrs",
     "rules/legiz-ru/re-filter/domain-rule.mrs"),
    ("https://raw.githubusercontent.com/legiz-ru/mihomo-rule-sets/main/oisd/big.mrs",
     "rules/legiz-ru/oisd/big.mrs"),
    ("https://raw.githubusercontent.com/legiz-ru/mihomo-rule-sets/main/other/torrent-trackers.mrs",
     "rules/legiz-ru/other/torrent-trackers.mrs"),
    ("https://raw.githubusercontent.com/legiz-ru/mihomo-rule-sets/main/other/torrent-clients.yaml",
     "rules/legiz-ru/other/torrent-clients.yaml"),
    ("https://raw.githubusercontent.com/legiz-ru/mihomo-rule-sets/main/other/ru-app-list.yaml",
     "rules/legiz-ru/other/ru-app-list.yaml"),

    # itdoginfo / allow-domains
    ("https://raw.githubusercontent.com/itdoginfo/allow-domains/main/Russia/inside-clashx.lst",
     "rules/itdoginfo/inside-clashx.lst"),

    # roscomvpn / custom-category
    ("https://raw.githubusercontent.com/roscomvpn/custom-category/release/mihomo/games.yaml",
     "rules/roscomvpn/games.yaml"),
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


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    failures: list[str] = []

    for url, rel_path in SOURCES:
        dest = repo_root / rel_path
        print(f"-> {url}")
        try:
            data = download(url)
        except Exception as e:  # noqa: BLE001 - report and continue
            print(f"   [ERROR] {e}")
            failures.append(url)
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        print(f"   saved {len(data)} bytes -> {rel_path}")

    if failures:
        print(f"\nFailed to download {len(failures)} source(s):", file=sys.stderr)
        for url in failures:
            print(f"  - {url}", file=sys.stderr)
        return 1

    print(f"\nDone. Mirrored {len(SOURCES)} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
