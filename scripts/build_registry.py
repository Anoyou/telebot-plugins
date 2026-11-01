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

目录版本 1.1 追加字段（TelePilot 0.117.0+ 的插件市场详情页读取；全部可选，旧服务器忽略不认识的字段，
``summary`` 仍是字符串）：

- ``summary_i18n``：``{"zh": summary, "en": description_en 截断}``（没有英文简介就只有 ``zh``）；
- ``description``：完整简介（最多 1000 字）；``usage``：使用说明（最多 1500 字，保留 ``{prefix}`` 占位）；
- ``commands``：``plugin.json`` 的 ``commands``（指令名，最多 24 个）；
- ``release_notes`` / ``released_at``：插件自己 ``CHANGELOG.md`` 第一段（标题版本必须等于 ``version``，否则不写），最多 1500 字；
- ``homepage``：插件在本仓库里的目录链接；``license``：``plugin.json`` 的 ``license``；
- ``icon_url`` / ``screenshots``：``plugin.json`` 的同名字段；相对路径必须是插件目录里真实存在的文件，转成 raw 链接；
- ``channel_versions``：``{"stable": version}``（以后有测试通道分支时再加 ``beta``）。

空值不写，保证索引体积小、同样的插件内容总是生成同样的文件。

索引里不写生成时间，保证同样的插件内容总是生成同样的文件（``--check`` 才能稳定）。

**目录版本 1 已定死**（TelePilot Plugin API 1.0 冻结范围，第 22 轮）：``schema_version`` 固定为 1，顶层与条目只能
使用 ``REGISTRY_V1_TOP_FIELDS`` / ``REGISTRY_V1_ENTRY_FIELDS`` 里的字段（与主仓库
``app.worker.plugins.stability.REGISTRY_V1_*`` 一致）；``kind`` 只能是 ``KINDS``、``trust`` 只能是 ``TRUST_LEVELS``。
生成与 ``--check`` 都会校验，越界即失败。要加字段或改格式，先在主仓库更新目录版本（加字段属于 1.x 追加，
改格式发目录版本 2，宿主两版都读），再改这里。
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
# registry.json 目录版本 1 的字段（定死，只增不减；与主仓库 stability.REGISTRY_V1_* 一致）。
REGISTRY_V1_TOP_FIELDS = ("schema_version", "name", "trust", "repository", "homepage", "plugins")
REGISTRY_V1_ENTRY_FIELDS = (
    "key",
    "name",
    "kind",
    "icon",
    "summary",
    "version",
    "author",
    "min_telepilot_version",
    "plugin_api",
    "permissions",
    "reads",
    "tags",
    "path",
    "channels",
    "repository",
    "trust",
)
# 目录版本 1.1 追加字段（可选，只增不减；与主仓库 stability.REGISTRY_V1_ADDITIVE_ENTRY_FIELDS 一致）。
REGISTRY_V1_ADDITIVE_ENTRY_FIELDS = (
    "summary_i18n",
    "description",
    "usage",
    "commands",
    "release_notes",
    "released_at",
    "homepage",
    "license",
    "icon_url",
    "screenshots",
    "channel_versions",
)
TRUST_LEVELS = ("official", "community", "unreviewed")
REPOSITORY = "https://github.com/Anoyou/telebot-plugins.git"
HOMEPAGE = "https://github.com/Anoyou/telebot-plugins"
SUMMARY_MAX = 200
DESCRIPTION_MAX = 1000
USAGE_MAX = 1500
RELEASE_NOTES_MAX = 1500
MAX_COMMANDS = 24
MAX_SCREENSHOTS = 8
RAW_BASE = "https://raw.githubusercontent.com/Anoyou/telebot-plugins/main"
TREE_BASE = "https://github.com/Anoyou/telebot-plugins/tree/main"
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
_CHANGELOG_HEADING_RE = re.compile(
    r"^##\s+v?(?P<version>\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?)\s*(?:[（(]\s*(?P<date>\d{4}-\d{2}-\d{2})\s*[）)])?",
    re.M,
)


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


def _clip(text: str, limit: int) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _release_notes(plugin_dir: Path, version: str) -> tuple[str | None, str | None]:
    """插件 CHANGELOG.md 第一段：标题版本等于当前版本才返回（避免显示过期说明）。"""

    path = plugin_dir / "CHANGELOG.md"
    if not path.is_file():
        return None, None
    text = path.read_text(encoding="utf-8")
    match = _CHANGELOG_HEADING_RE.search(text)
    if not match or match.group("version") != version:
        return None, None
    body = text[match.end():]
    body = body.split("\n", 1)[1] if "\n" in body else ""
    following = re.search(r"^##\s", body, re.M)
    if following:
        body = body[: following.start()]
    notes = _clip(body.strip(), RELEASE_NOTES_MAX)
    return (notes or None), match.group("date")


def _asset_url(plugin_dir: Path, value: object) -> str | None:
    """https 链接原样保留；相对路径必须是插件目录里真实存在的文件，转成 raw 链接。"""

    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("https://"):
        return text
    if "://" in text or text.startswith("/") or ".." in text.split("/"):
        raise RegistryError(f"{plugin_dir.name}: 图片地址 {text!r} 必须是 https 链接或插件目录内的相对路径")
    if not (plugin_dir / text).is_file():
        raise RegistryError(f"{plugin_dir.name}: 图片文件 {text} 不存在")
    return f"{RAW_BASE}/{plugin_dir.name}/{text}"


def _additive_fields(plugin_dir: Path, meta: dict, summary: str, version: str) -> dict:
    """目录版本 1.1 追加字段；空值不写。"""

    out: dict = {}
    i18n = {"zh": summary}
    summary_en = _summary(str(meta.get("description_en") or ""))
    if summary_en:
        i18n["en"] = summary_en
    out["summary_i18n"] = i18n
    description = _clip(" ".join(str(meta.get("description") or "").split()), DESCRIPTION_MAX)
    if description and description != summary:
        out["description"] = description
    usage = _clip(str(meta.get("usage") or ""), USAGE_MAX)
    if usage:
        out["usage"] = usage
    commands = _str_list(meta.get("commands"))[:MAX_COMMANDS]
    if commands:
        out["commands"] = commands
    notes, released_at = _release_notes(plugin_dir, version)
    if notes:
        out["release_notes"] = notes
    if released_at:
        out["released_at"] = released_at
    out["homepage"] = f"{TREE_BASE}/{plugin_dir.name}"
    if isinstance(meta.get("license"), str) and meta["license"].strip():
        out["license"] = meta["license"].strip()[:40]
    icon_url = _asset_url(plugin_dir, meta.get("icon_url"))
    if icon_url:
        out["icon_url"] = icon_url
    screenshots = meta.get("screenshots")
    if isinstance(screenshots, list):
        urls = [url for url in (_asset_url(plugin_dir, item) for item in screenshots) if url]
        if urls:
            out["screenshots"] = urls[:MAX_SCREENSHOTS]
    out["channel_versions"] = {"stable": version}
    return out


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
    entry.update(_additive_fields(plugin_dir, meta, entry["summary"], version))
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


def validate_v1(data: dict) -> None:
    """确认索引符合目录版本 1：版本号、顶层字段、条目字段、kind / trust 取值都在冻结清单内。"""

    if data.get("schema_version") != SCHEMA_VERSION or SCHEMA_VERSION != 1:
        raise RegistryError(f"schema_version 必须是 1（目录版本 1 已定死），当前 {data.get('schema_version')!r}")
    extra_top = sorted(set(data) - set(REGISTRY_V1_TOP_FIELDS))
    if extra_top:
        raise RegistryError(f"顶层字段超出目录版本 1：{', '.join(extra_top)}")
    if data.get("trust") not in TRUST_LEVELS:
        raise RegistryError(f"trust={data.get('trust')!r} 不是目录版本 1 的信任等级")
    for entry in data.get("plugins") or []:
        extra = sorted(set(entry) - set(REGISTRY_V1_ENTRY_FIELDS) - set(REGISTRY_V1_ADDITIVE_ENTRY_FIELDS))
        if extra:
            raise RegistryError(f"{entry.get('key')}: 条目字段超出目录版本 1：{', '.join(extra)}")
        if entry.get("kind") not in KINDS:
            raise RegistryError(f"{entry.get('key')}: kind={entry.get('kind')!r} 不是目录版本 1 的类型")
        if "trust" in entry and entry["trust"] not in TRUST_LEVELS:
            raise RegistryError(f"{entry.get('key')}: trust={entry['trust']!r} 不是目录版本 1 的信任等级")


def render(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = parser.parse_args(argv)
    try:
        data = build()
        validate_v1(data)
        expected = render(data)
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
