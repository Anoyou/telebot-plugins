#!/usr/bin/env python3
"""生成 / 校验插件市场索引 ``registry.json``。

TelePilot（0.113.0+）的“插件市场”从这里读取官方插件的元数据：浏览、搜索、更新角标都只看这份
索引，不需要把整个仓库克隆下来；真正安装 / 更新时宿主仍然克隆本仓库、读取插件自己的
``plugin.json`` 并做完整校验，索引里的字段只用于展示和“是否兼容”的预判。

用法::

    python scripts/build_registry.py            # 重新生成 registry.json
    python scripts/build_registry.py --check    # CI：registry.json 与各插件 plugin.json 不一致时失败

规则（全部从 ``plugin.json`` 推导，不手写）：

- ``key`` = ``plugin.json`` 的 ``name``，必须与目录名一致；
- ``kind``：声明了 ``payment_sources`` → ``payment_source``；声明了 ``middleware`` → ``middleware``；
  否则用 ``category``（interactive / automation / utility），缺省 ``utility``；
- ``icon``：``plugin.json`` 里有 ``icon`` 用它，否则按 ``kind`` 给一个默认图标；
- ``summary``：``description`` 截到 200 字；``name.en`` 用 ``display_name_en``，没有就用 ``key``；
- ``min_telepilot_version`` / ``plugin_api`` / ``permissions`` / ``reads`` / ``tags`` 原样复制；
- ``channels.stable`` = ``main``（本仓库 main 分支即稳定通道）。

索引里不写生成时间，保证同样的插件内容总是生成同样的文件（``--check`` 才能稳定）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "registry.json"
SCHEMA_VERSION = 1
REPOSITORY = "https://github.com/Anoyou/telebot-plugins.git"
HOMEPAGE = "https://github.com/Anoyou/telebot-plugins"
SUMMARY_MAX = 200
KINDS = ("interactive", "automation", "utility", "middleware", "payment_source", "ui")
DEFAULT_ICONS = {
    "interactive": "🎮",
    "automation": "⚙️",
    "utility": "🧰",
    "middleware": "🧩",
    "payment_source": "💱",
    "ui": "🖼️",
}
_KEY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.\-]+)?$")


class RegistryError(Exception):
    pass


def _kind(meta: dict) -> str:
    if meta.get("payment_sources"):
        return "payment_source"
    if meta.get("middleware"):
        return "middleware"
    category = str(meta.get("category") or "").strip()
    return category if category in KINDS else "utility"


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


def _summary(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= SUMMARY_MAX else text[: SUMMARY_MAX - 1] + "…"


def entry_for(plugin_dir: Path) -> dict:
    meta = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
    key = str(meta.get("name") or "")
    if key != plugin_dir.name:
        raise RegistryError(f"{plugin_dir.name}: plugin.json name={key!r} 与目录名不一致")
    if not _KEY_RE.match(key):
        raise RegistryError(f"{plugin_dir.name}: 插件 key {key!r} 不合法")
    version = str(meta.get("version") or "")
    if not _VERSION_RE.match(version):
        raise RegistryError(f"{key}: version {version!r} 不是 SemVer")
    kind = _kind(meta)
    entry: dict = {
        "key": key,
        "name": {
            "zh": str(meta.get("display_name") or key),
            "en": str(meta.get("display_name_en") or key),
        },
        "kind": kind,
        "icon": str(meta.get("icon") or DEFAULT_ICONS[kind]),
        "summary": _summary(str(meta.get("description") or "")),
        "version": version,
        "author": str(meta.get("author") or ""),
        "min_telepilot_version": meta.get("min_telepilot_version") or None,
        "plugin_api": meta.get("plugin_api") or None,
        "permissions": _str_list(meta.get("permissions")),
        "reads": _str_list(meta.get("reads")),
        "tags": _str_list(meta.get("tags")),
        "path": key,
        "channels": {"stable": "main"},
    }
    return entry


def build() -> dict:
    plugins = [
        entry_for(child)
        for child in sorted(ROOT.iterdir(), key=lambda p: p.name)
        if child.is_dir() and not child.name.startswith(".") and (child / "plugin.json").is_file()
    ]
    keys = [item["key"] for item in plugins]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise RegistryError(f"插件 key 重复: {', '.join(duplicates)}")
    return {
        "schema_version": SCHEMA_VERSION,
        "name": "TelePilot 官方插件库",
        "trust": "official",
        "repository": REPOSITORY,
        "homepage": HOMEPAGE,
        "plugins": plugins,
    }


def render(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = parser.parse_args(argv)
    try:
        expected = render(build())
    except RegistryError as exc:
        print(f"registry.json 生成失败：{exc}", file=sys.stderr)
        return 1
    if args.check:
        current = REGISTRY_PATH.read_text(encoding="utf-8") if REGISTRY_PATH.exists() else ""
        if current != expected:
            print(
                "registry.json 与各插件的 plugin.json 不一致；请运行 python scripts/build_registry.py 后提交。",
                file=sys.stderr,
            )
            return 1
        print(f"registry.json 校验通过（{len(json.loads(expected)['plugins'])} 个插件）")
        return 0
    REGISTRY_PATH.write_text(expected, encoding="utf-8")
    print(f"已生成 registry.json（{len(json.loads(expected)['plugins'])} 个插件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
