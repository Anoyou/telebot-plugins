#!/usr/bin/env python3
"""TelePilot 宿主兼容性检查：用主仓库（Anoyou/Telebot）真实的插件运行时校验本仓库全部插件。

用法::

    # 指向 TelePilot 主仓库（含 backend/ 的检出目录），用主仓库 backend 的虚拟环境运行
    TELEPILOT_REPO=/path/to/Telebot /path/to/Telebot/backend/.venv/bin/python scripts/check_host_compat.py
    # 只查部分插件 / 输出 JSON 报告 / 警告也算失败
    ... scripts/check_host_compat.py --plugin sum --plugin game24 --json report.json --strict

检查内容（全部直接调用主仓库代码，不复制规则）：

1. ``plugin.json``：``remote_plugin_service._read_plugin_metadata``（与安装/升级同一套 Pydantic +
   JSON Schema 校验，要求显式 ``requires_platform_capabilities``）、``_validate_runtime_plugin_shape``、
   ``lint_plugin_metadata_files``（安装时给出的规范警告）。
2. 加载：``loader._load_dir(path, "installed")``——与 worker 加载第三方插件同一路径（静态预检、
   ``min_telepilot_version`` 对比、``PLUGIN_CLASS``/``MANIFEST`` 身份一致性）。
3. ``manifest.py`` 与 ``plugin.json`` 一致：版本、最低版本、权限、分类、平台能力、config_schema 等。
4. 权限：只能是宿主认识的权限；用到的 facade / Telethon 方法必须有对应权限
   （``ctx.ai`` → ``ai_text``/``ai_agent``、``ctx.http`` → ``external_http``+``allowed_hosts``、
   ``ctx.skills`` → ``agent_skills``、识图 → ``ai_vision``、``client.X`` → ``sandbox.ALLOWED_API``）。
5. Host API：``ctx.messages/ai/http/storage/skills/scheduler`` 上调用的方法必须存在，关键字参数必须被
   宿主签名接受；返回的标准动作 ``{"type": ...}`` 必须是 ``action_core.CANONICAL_ACTION_TYPES``。
6. ``min_telepilot_version``：按用到的能力推断所需最低版本，声明偏低时报警告；做了能力探测
   （``ctx.capabilities.has("X")``、``getattr``/``hasattr`` 能力标记或 ``except TypeError`` 回退）的用法不计入。
   宿主有能力注册表（TelePilot 0.107.0+ ``app.worker.plugins.capabilities.CAPABILITIES``）时，注册表里
   登记过的能力一律以注册表的 ``since`` 为准（``FEATURE_CAPABILITY`` 映射），``FEATURE_MIN_VERSIONS``
   只保留注册表出现之前的历史门槛；同时校验 ``plugin_api`` 范围、对弃用接口（``DEPRECATIONS``）给警告。
7. 初始化：实例化插件类，用平台 ``PluginContext`` + 内存版 storage/scheduler 跑一次 ``on_startup`` /
   ``on_shutdown``（超时 5 秒；失败记为警告，因为真实环境还会注入 Telegram client）。

退出码：存在错误为 1；``--strict`` 时警告也算失败。
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import contextlib
import inspect
import json
import logging
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# 能力 → 首个提供它的 TelePilot 版本（依据主仓库 CHANGELOG）。只列插件可能用到、
# 且在旧版本上会直接失败（方法/参数/权限不存在）的能力；宿主比较版本时只看 x.y.z。
FEATURE_MIN_VERSIONS: dict[str, tuple[str, str]] = {
    # key: (版本, 说明)
    "messages.send": ("0.33.0", "ctx.messages"),
    "event_subscriptions": ("0.40.0", "Event Bus 订阅 / on_event"),
    "config_actions": ("0.45.0", "配置页动作 on_config_action"),
    "direct_passthrough": ("0.46.0", "on_direct_message 直通入口"),
    "messages.send_photo": ("0.49.13", "ctx.messages.send_photo"),
    "messages.send_file": ("0.49.13", "ctx.messages.send_file"),
    "messages.edit_caption": ("0.49.13", "ctx.messages.edit_caption"),
    "ai.stream_complete": ("0.51.0", "ctx.ai.stream_complete"),
    "storage": ("0.54.0", "ctx.storage"),
    "ai_components": ("0.54.0", "QuizMaker / AnswerJudge / PersonaChat"),
    "ai.run_agent": ("0.56.0", "ctx.ai.run_agent"),
    "data_dir": ("0.59.1", "ctx.data_dir"),
    "messages.send_rich": ("0.60.0", "ctx.messages.send_rich / send_rich_message"),
    "identities": ("0.60.6", "ctx.identities"),
    "messages.edit_rich": ("0.67.1", "ctx.messages.edit_rich"),
    "manifest.requires_platform_capabilities": ("0.72.0-beta.2", "manifest.py 传入 requires_platform_capabilities（旧宿主 Manifest 不接受该参数，导入即失败）"),
    "messages.click_callback_button": ("0.90.0", "ctx.messages.click_callback_button"),
    "external_http_bypass_proxy": ("0.97.0", "external_http_bypass_proxy / http.allow_direct"),
    "skills": ("0.106.0-beta.1", "ctx.skills / agent_skills"),
    "ai.fetch_bot_image": ("0.106.0-beta.1", "ctx.ai.fetch_bot_image"),
    "ai.complete.images": ("0.106.0-beta.1", "ctx.ai.complete(images=...) 识图"),
    "ai.complete.web_search": ("0.106.0-beta.1", "ctx.ai.complete(web_search=...)"),
    "messages.answer_guest_query": ("0.106.0-beta.1", "访客模式 answer_guest_query"),
    "guest_message": ("0.106.0-beta.1", "guest_message 事件 / guest_all"),
    "ai.complete.thinking": ("0.106.0-beta.2", "ctx.ai.complete(thinking=...)"),
    "compact_tables": ("0.106.0-beta.12", "compact_tables 紧凑表格"),
}

# 本脚本的能力键 → 宿主能力注册表名（0.107.0+）。宿主有注册表时，门槛版本与说明一律读注册表。
FEATURE_CAPABILITY: dict[str, str] = {
    "compact_tables": "messages.rich_table_compact",
    "guest_reply_markup": "messages.guest_reply_markup",
    "messages.stream_editor": "messages.streaming_edit",
    "messages.stream_edit": "messages.streaming_edit",
    "callback_routing": "messages.callback_routing",
    "bot_api_extra": "messages.bot_api_extra",
    "ai.complete.extra_body": "ai.extra_body",
    "host.read": "host.read",
    "capabilities": "runtime.capabilities",
}

# 插件做了能力探测时，用这些标记判断“已兼容旧平台”。
FEATURE_GUARDS: dict[str, tuple[str, ...]] = {
    "compact_tables": ("rich_table_compact",),
    "messages.edit_rich": ("edit_rich",),
    "messages.send_rich": ("send_rich",),
    "messages.answer_guest_query": ("answer_guest_query",),
    "ai.fetch_bot_image": ("fetch_bot_image", "vision_enabled"),
    "ai.complete.images": ("vision_enabled", "AICapabilityError", "TypeError"),
    "ai.complete.web_search": ("AICapabilityError", "TypeError"),
    "ai.complete.thinking": ("TypeError",),
    "skills": ("skills",),
    "storage": ("storage",),
    "data_dir": ("data_dir",),
    "identities": ("identities",),
    "messages.click_callback_button": ("click_callback_button",),
    "messages.send_photo": ("send_photo", "_message_op"),
    "messages.send_file": ("send_file", "_message_op"),
    "messages.edit_caption": ("edit_caption", "_message_op"),
}

# 只会在 guest_message 事件处理中用到的能力：旧平台不投递该事件，不构成最低版本要求。
GUEST_GATED = {"guest_message", "messages.answer_guest_query"}

FACADE_NAMES = ("messages", "ai", "http", "storage", "skills", "scheduler", "identities")
CTX_NAMES = {"ctx", "context", "plugin_ctx", "call_ctx", "runtime_ctx"}
CLIENT_NAMES = {"client", "_client"}
# 字典里同时出现 type 与这些键时才视为标准动作（避免误判 OpenAI 请求体等普通 dict）
ACTION_HINT_KEYS = {
    "chat_id",
    "rich_message",
    "message_id",
    "inline_message_id",
    "callback_query_id",
    "inline_query_id",
    "guest_query_id",
    "reply_to_user_id",
    "save_message_id_key",
}


@dataclass
class PluginReport:
    key: str
    version: str = ""
    min_version: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    lint: list[str] = field(default_factory=list)
    features: dict[str, list[str]] = field(default_factory=dict)
    inferred_min: str = ""

    @property
    def status(self) -> str:
        if self.errors:
            return "ERROR"
        if self.warnings:
            return "WARN"
        return "OK"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "version": self.version,
            "min_telepilot_version": self.min_version,
            "inferred_min_version": self.inferred_min,
            "status": self.status,
            "errors": self.errors,
            "warnings": self.warnings,
            "lint": self.lint,
            "features": self.features,
        }


# ─────────────────────────────────────────────────────
# 宿主环境
# ─────────────────────────────────────────────────────
def locate_host_repo(explicit: str | None) -> Path:
    candidates = [explicit, os.environ.get("TELEPILOT_REPO")]
    # 用主仓库 backend/.venv 运行时，sys.prefix 的上一级就是 backend/
    candidates.append(str(Path(sys.prefix).resolve().parent.parent))
    for raw in candidates:
        if not raw:
            continue
        path = Path(raw).expanduser().resolve()
        if (path / "backend" / "app" / "worker" / "plugins" / "loader.py").is_file():
            return path
    raise SystemExit(
        "找不到 TelePilot 主仓库：请设置 TELEPILOT_REPO=/path/to/Telebot（需包含 backend/app），"
        "或用 --host-repo 指定。"
    )


def prepare_host(repo: Path, installed_dir: Path) -> dict[str, Any]:
    backend = repo / "backend"
    for path in (str(backend), str(repo)):
        if path not in sys.path:
            sys.path.insert(0, path)
    # 只用于让配置模型能实例化；检查过程不连接数据库 / Redis，也不读写任何凭据。
    os.environ.setdefault("MASTER_KEY", "4QAy7kFzv2mkHRXTHwL_FEZABz0R3sqat5rBl4vDLXk=")
    os.environ.setdefault("JWT_SECRET", "telepilot-host-compat-check-only")
    os.environ.setdefault("PLUGINS_INSTALLED_DIR", str(installed_dir))

    logging.basicConfig(level=logging.CRITICAL)

    import importlib

    def _optional(module: str, name: str) -> Any:
        """旧宿主可能没有某些 facade；缺失时返回 None，相关检查自动跳过。"""
        try:
            return getattr(importlib.import_module(module), name)
        except (ImportError, AttributeError):
            return None

    from app import __version__ as host_version
    from app.services import remote_plugin_service as rps
    from app.worker.plugins import loader, sandbox
    from app.worker.plugins.base import Plugin, PluginContext
    from app.worker.plugins.manifest import Manifest

    with_property_order = _optional("app.worker.plugins.manifest", "with_property_order") or (lambda x: x)
    canonical = _optional("app.services.interaction.action_core", "CANONICAL_ACTION_TYPES")
    BufferedMessageOps = _optional("app.worker.plugins.message_ops", "BufferedMessageOps")
    LiveMessageOps = getattr(loader, "_LiveMessageOps", None)
    PluginAI = _optional("app.worker.plugins.ai_facade", "PluginAI")
    PluginHTTP = _optional("app.worker.plugins.http_facade", "PluginHTTP")
    PluginSkills = _optional("app.worker.plugins.skills_facade", "PluginSkills")
    PluginStorage = _optional("app.worker.plugins.storage", "PluginStorage")
    SchedulerFacade = _optional("app.worker.scheduler_runtime", "SchedulerFacade")
    PluginIdentityFacade = _optional("app.worker.plugins.base", "PluginIdentityFacade")
    capabilities = _optional("app.worker.plugins.capabilities", "CAPABILITIES") or ()
    deprecations = _optional("app.worker.plugins.deprecations", "DEPRECATION_INDEX") or {}
    plugin_api_version = _optional("app.worker.plugins.versioning", "PLUGIN_API_VERSION")
    plugin_api_satisfies = _optional("app.worker.plugins.versioning", "plugin_api_satisfies")
    compare_versions = _optional("app.worker.plugins.versioning", "compare_versions")
    known_permissions = set(sandbox.ALLOWED_API) | set(getattr(sandbox, "_NON_CLIENT_PERMISSIONS", ()))
    known_permissions |= set(getattr(loader, "_SUPPORTED_FACADE_PERMISSIONS", ()))
    return {
        "version": host_version,
        "rps": rps,
        "loader": loader,
        "sandbox": sandbox,
        "Plugin": Plugin,
        "PluginContext": PluginContext,
        "Manifest": Manifest,
        "with_property_order": with_property_order,
        "canonical_actions": set(canonical) if canonical else None,
        "event_types": set(getattr(rps, "VALID_EVENT_TYPES", ()) or ()),
        "known_permissions": known_permissions,
        "reserved_permissions": set(getattr(loader, "_RESERVED_UNSUPPORTED_FACADE_PERMISSIONS", set())),
        "facades": {
            "messages": [c for c in (BufferedMessageOps, LiveMessageOps) if c is not None],
            "ai": [c for c in (PluginAI,) if c is not None],
            "http": [c for c in (PluginHTTP,) if c is not None],
            "storage": [c for c in (PluginStorage,) if c is not None],
            # 旧宿主没有 ctx.skills：保留空类占位，使调用被判为“不存在”
            "skills": [PluginSkills or type("MissingPluginSkills", (), {})],
            "scheduler": [c for c in (SchedulerFacade,) if c is not None],
            "identities": [c for c in (PluginIdentityFacade,) if c is not None],
        },
        "live_message_ops": LiveMessageOps,
        # 能力注册表（0.107.0+）：{name: (since, summary)}；旧宿主为空，回退手写门槛表。
        "capabilities": {str(c.name): (str(c.since), str(c.summary)) for c in capabilities},
        "deprecations": dict(deprecations),
        "plugin_api_version": plugin_api_version,
        "plugin_api_satisfies": plugin_api_satisfies,
        "compare_versions": compare_versions,
    }


def feature_min_versions(host: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """门槛表：注册表里有的能力以注册表为准，其余沿用历史手写表。"""

    table = dict(FEATURE_MIN_VERSIONS)
    registry = host.get("capabilities") or {}
    for feature, cap_name in FEATURE_CAPABILITY.items():
        if cap_name in registry:
            since, summary = registry[cap_name]
            table[feature] = (since, f"{cap_name}：{summary}"[:120])
    return table


def _probed_capabilities(plugin_dir: Path) -> set[str]:
    """插件里 ``capabilities.has("X")`` / ``"X" in ctx.capabilities`` 探测过的能力名。"""

    names: set[str] = set()
    pattern = re.compile(r"""\.has\(\s*["']([\w.@-]+)["']|["']([\w.@-]+)["']\s+in\s+[\w.]*capabilities""")
    for path in plugin_dir.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        for match in pattern.finditer(_read_text(path)):
            names.add(match.group(1) or match.group(2))
    return names


def _capability_probed(cap_name: str | None, probed: set[str]) -> bool:
    """探测名等于能力名，或以“能力名.”/“能力名@”开头（如 ``host.read.account.admins@1`` 探测 ``host.read``）。"""

    if not cap_name:
        return False
    return any(name == cap_name or name.startswith((cap_name + ".", cap_name + "@")) for name in probed)


# ─────────────────────────────────────────────────────
# 静态分析
# ─────────────────────────────────────────────────────
def _version_tuple(raw: str | None) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", str(raw or ""))[:3])


def _full_version_key(raw: str) -> tuple[int, int, int, int]:
    """比 x.y.z 更细的比较键，正式版排在同基础版本 beta 之后。"""
    base = list(_version_tuple(raw)) + [0, 0, 0]
    match = re.search(r"beta\.(\d+)", raw)
    pre = int(match.group(1)) if match else 10_000
    return (base[0], base[1], base[2], pre)


def _chain(node: ast.AST) -> list[str]:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    else:
        parts.append("?")
    return list(reversed(parts))


def _is_ctx_base(parts: list[str]) -> bool:
    """``ctx`` / ``self.ctx`` / ``self._ctx`` / ``state.ctx`` 之类。"""
    if not parts:
        return False
    last = parts[-1]
    return last in CTX_NAMES or last.lstrip("_") in CTX_NAMES


@dataclass
class SourceFacts:
    facade_calls: list[tuple[str, str, list[str], int, str]] = field(default_factory=list)
    facade_attrs: set[str] = field(default_factory=set)
    client_calls: list[tuple[str, int, str]] = field(default_factory=list)
    event_calls: list[tuple[str, int, str]] = field(default_factory=list)
    action_types: list[tuple[str, int, str]] = field(default_factory=list)
    guard_tokens: set[str] = field(default_factory=set)
    imports_ai_components: bool = False
    has_type_error_fallback: bool = False


def scan_source(plugin_dir: Path) -> SourceFacts:
    facts = SourceFacts()
    for path in sorted(plugin_dir.rglob("*.py")):
        rel = str(path.relative_to(plugin_dir))
        if "__pycache__" in rel or rel.startswith("tests"):
            continue
        # 保留的 Pagermaid 原实现不会被加载
        if path.name == "legacy_main.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            facts.action_types.append(("<syntax-error>", exc.lineno or 0, rel))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is not None:
                names = [_chain(n)[-1] for n in (node.type.elts if isinstance(node.type, ast.Tuple) else [node.type])]
                if "TypeError" in names:
                    facts.has_type_error_fallback = True
                    facts.guard_tokens.add("TypeError")
                facts.guard_tokens.update(names)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"getattr", "hasattr"}:
                for arg in node.args[1:2]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        facts.guard_tokens.add(arg.value)
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("ai_components"):
                facts.imports_ai_components = True
            if isinstance(node, ast.Attribute):
                parts = _chain(node)
                # ctx.<facade>（含 self.ctx.<facade>）
                for idx in range(1, len(parts)):
                    if parts[idx] in FACADE_NAMES and _is_ctx_base(parts[:idx]):
                        facts.facade_attrs.add(parts[idx])
                    if parts[idx] == "data_dir" and _is_ctx_base(parts[:idx]):
                        facts.facade_attrs.add("data_dir")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                parts = _chain(node.func)
                method = parts[-1]
                kwargs = [kw.arg for kw in node.keywords if kw.arg]
                if any(kw.arg is None for kw in node.keywords):
                    kwargs.append("**")
                if len(parts) >= 3 and parts[-2] in FACADE_NAMES and _is_ctx_base(parts[:-2]):
                    facts.facade_calls.append((parts[-2], method, kwargs, node.lineno, rel))
                elif len(parts) >= 2 and (
                    parts[-2] in CLIENT_NAMES
                    or (parts[-2] == "client" and _is_ctx_base(parts[:-2]))
                ):
                    facts.client_calls.append((method, node.lineno, rel))
                elif len(parts) == 2 and parts[0] == "event":
                    facts.event_calls.append((method, node.lineno, rel))
            if isinstance(node, ast.Dict):
                keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
                if "type" in keys and (ACTION_HINT_KEYS & set(keys)):
                    value = node.values[keys.index("type")] if None not in node.keys else None
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        facts.action_types.append((value.value, node.lineno, rel))
    return facts


def _signature_accepts(cls: type, method: str, kwargs: list[str]) -> list[str]:
    fn = getattr(cls, method, None)
    if fn is None:
        return kwargs
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return []
    params = sig.parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return []
    return [kw for kw in kwargs if kw != "**" and kw not in params]


def _delegate_cls(host: dict[str, Any], facade: str, method: str) -> list[type]:
    classes = [c for c in host["facades"].get(facade, []) if c is not None]
    if facade == "messages":
        # _LiveMessageOps 以 **kwargs 委托给 BufferedMessageOps，参数以后者为准
        return classes[:1]
    return classes


# ─────────────────────────────────────────────────────
# 单插件检查
# ─────────────────────────────────────────────────────
def check_plugin(plugin_dir: Path, host: dict[str, Any], *, run_startup: bool = True) -> PluginReport:
    rps = host["rps"]
    loader = host["loader"]
    sandbox = host["sandbox"]
    report = PluginReport(key=plugin_dir.name)

    raw: dict[str, Any] = {}
    try:
        raw = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"plugin.json 无法读取: {exc}")
        return report
    report.version = str(raw.get("version") or "")
    report.min_version = str(raw.get("min_telepilot_version") or raw.get("min_telebot_version") or "")

    # 1. 安装期元数据校验（与安装 / 升级同一套）
    meta = None
    try:
        try:
            meta = rps._read_plugin_metadata(
                plugin_dir, fallback_name=plugin_dir.name, require_platform_capabilities=True
            )
        except TypeError:  # 旧宿主没有该参数
            meta = rps._read_plugin_metadata(plugin_dir, fallback_name=plugin_dir.name)
    except rps.InvalidPluginMetadata as exc:
        report.errors.append(f"安装校验失败 {getattr(exc, 'code', '')}: {exc}")
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"安装校验异常: {type(exc).__name__}: {exc}")
    if meta is not None:
        if meta.name != plugin_dir.name:
            report.errors.append(f"plugin.json name={meta.name!r} 与目录名不一致")
        try:
            if hasattr(rps, "_validate_runtime_plugin_shape"):
                rps._validate_runtime_plugin_shape(plugin_dir, meta)
        except rps.InvalidPluginMetadata as exc:
            report.errors.append(f"运行期结构不完整: {exc}")
    try:
        report.lint = list(rps.lint_plugin_metadata_files(plugin_dir))
    except Exception as exc:  # noqa: BLE001
        report.warnings.append(f"lint 执行异常: {exc}")
    for item in report.lint:
        report.warnings.append(f"安装规范警告: {item}")

    # 2. 权限
    permissions = [str(p) for p in raw.get("permissions") or []]
    perm_set = set(permissions)
    unknown = sorted(perm_set - host["known_permissions"])
    if unknown:
        report.errors.append(f"未知权限（宿主不认识，将不会生效）: {', '.join(unknown)}")
    reserved = sorted(perm_set & host["reserved_permissions"])
    if reserved:
        report.warnings.append(f"保留但未实现的权限（宿主会忽略）: {', '.join(reserved)}")
    if "ai_vision" in perm_set and not perm_set & {"ai_text", "ai_agent"}:
        report.errors.append("ai_vision 必须与 ai_text 同时声明")
    if "external_http" in perm_set and not raw.get("allowed_hosts") and not _manifest_attr_hint(plugin_dir, "allowed_hosts"):
        report.warnings.append("声明了 external_http 但未声明 allowed_hosts，ctx.http 不会注入")

    # 3. config_schema 默认值必须符合字段 schema
    schema = raw.get("config_schema")
    if isinstance(schema, dict):
        report.errors.extend(_check_schema_defaults(schema))
        report.warnings.extend(_check_schema_ui(schema))

    # 4. 运行期加载（与 worker 加载第三方插件同一路径）
    cls = None
    manifest = None
    load_errors = getattr(loader, "_PLUGIN_LOAD_ERRORS", {})
    load_errors.pop(plugin_dir.name, None)
    capture = _LogCapture()
    loader_logger = getattr(loader, "log", None) or logging.getLogger(loader.__name__)
    loader_logger.addHandler(capture)
    previous_level, previous_disabled = loader_logger.level, loader_logger.disabled
    loader_logger.setLevel(logging.WARNING)
    loader_logger.disabled = False
    previous_propagate = loader_logger.propagate
    loader_logger.propagate = False
    try:
        loaded = loader._load_dir(plugin_dir, "installed")
    except Exception as exc:  # noqa: BLE001
        loaded = {}
        report.errors.append(f"加载异常: {type(exc).__name__}: {exc}")
    finally:
        loader_logger.removeHandler(capture)
        loader_logger.setLevel(previous_level)
        loader_logger.disabled = previous_disabled
        loader_logger.propagate = previous_propagate
    if loaded:
        cls = next(iter(loaded.values()))
        manifest = getattr(cls, "_manifest", None)
    else:
        reason = load_errors.get(plugin_dir.name) or capture.summary()
        report.errors.append(f"宿主拒绝加载: {reason or '导入失败或缺少 PLUGIN_CLASS/MANIFEST'}")

    # 5. manifest.py 与 plugin.json 一致
    if manifest is not None:
        report.errors.extend(_check_parity(raw, manifest, host))

    # 6. 插件类与 hook 签名
    if cls is not None:
        report.errors.extend(_check_hooks(cls, host))

    # 7. Host API 静态检查
    facts = scan_source(plugin_dir)
    features: dict[str, list[str]] = {}

    def use(feature: str, where: str) -> None:
        features.setdefault(feature, [])
        if len(features[feature]) < 3:
            features[feature].append(where)

    for facade, method, kwargs, lineno, rel in facts.facade_calls:
        where = f"{rel}:{lineno}"
        classes = _delegate_cls(host, facade, method)
        all_classes = [c for c in host["facades"].get(facade, []) if c is not None]
        if not all_classes:
            continue
        if not any(hasattr(c, method) for c in all_classes):
            if method in facts.guard_tokens:
                report.warnings.append(f"{where} ctx.{facade}.{method} 宿主不存在（已做能力探测）")
            else:
                report.errors.append(f"{where} ctx.{facade}.{method} 在当前宿主不存在")
            continue
        for c in classes:
            if not hasattr(c, method):
                continue
            bad = _signature_accepts(c, method, kwargs)
            if bad:
                msg = f"{where} ctx.{facade}.{method}() 不接受参数 {', '.join(bad)}"
                if facts.has_type_error_fallback:
                    report.warnings.append(msg + "（有 TypeError 回退）")
                else:
                    report.errors.append(msg)
        if facade == "messages":
            live = host["live_message_ops"]
            if live is not None and not hasattr(live, method) and method not in {"apply"}:
                use(f"live_missing:{method}", where)
            key = f"messages.{method}"
            if key in FEATURE_MIN_VERSIONS:
                use(key, where)
            if "compact_tables" in kwargs:
                use("compact_tables", where)
            if "bot_api_extra" in kwargs:
                use("bot_api_extra", where)
            if method in {"stream_editor", "stream_edit"}:
                use(f"messages.{method}", where)
        elif facade == "ai":
            key = f"ai.{method}"
            if key in FEATURE_MIN_VERSIONS:
                use(key, where)
            if method == "complete":
                for kw in ("images", "web_search", "thinking", "extra_body"):
                    if kw in kwargs:
                        use(f"ai.complete.{kw}", where)
        elif facade in {"storage", "skills", "identities"}:
            use(facade, where)
    for facade in facts.facade_attrs:
        if facade in {"storage", "skills", "identities", "data_dir"}:
            use(facade, "ctx." + facade)
    if facts.imports_ai_components:
        use("ai_components", "import ai_components")

    # 权限 ↔ facade
    if "ai" in facts.facade_attrs and not perm_set & {"ai_text", "ai_agent"}:
        report.errors.append("使用了 ctx.ai 但未声明 ai_text / ai_agent 权限（ctx.ai 不会注入）")
    if "http" in facts.facade_attrs and "external_http" not in perm_set:
        report.errors.append("使用了 ctx.http 但未声明 external_http 权限（ctx.http 不会注入）")
    if "skills" in facts.facade_attrs and "agent_skills" not in perm_set:
        report.errors.append("使用了 ctx.skills 但未声明 agent_skills 权限")
    if ("ai.complete.images" in features or "ai.fetch_bot_image" in features) and "ai_vision" not in perm_set:
        report.errors.append("使用识图（images= / fetch_bot_image）但未声明 ai_vision 权限")
    if any(m == "payout" for f, m, *_ in facts.facade_calls if f == "messages") and "payout" not in perm_set:
        report.errors.append("调用 ctx.messages.payout 但未声明 payout 权限")
    if "payout" in {t for t, *_ in facts.action_types} and "payout" not in perm_set:
        report.errors.append("返回 payout 动作但未声明 payout 权限")

    # Telethon client 方法 → sandbox 权限
    try:
        from telethon import TelegramClient as telegram_client_cls
    except ImportError:  # pragma: no cover
        telegram_client_cls = object
    allowed_methods = set(sandbox.resolve_permissions(permissions)) | set(sandbox._ALWAYS_ALLOWED)
    method_to_perm = {m: p for p, ms in sandbox.ALLOWED_API.items() for m in ms}
    for method, lineno, rel in facts.client_calls:
        if method in allowed_methods:
            continue
        where = f"{rel}:{lineno}"
        if method in method_to_perm:
            report.errors.append(f"{where} client.{method} 需要权限 {method_to_perm[method]}（未声明，会被沙箱拒绝）")
        elif not method.startswith("_") and hasattr(telegram_client_cls, method):
            report.warnings.append(f"{where} client.{method} 不在沙箱白名单内，第三方插件调用会被拒绝")
    event_map = getattr(sandbox, "_EVENT_METHOD_TO_CLIENT_METHOD", {})
    for method, lineno, rel in facts.event_calls:
        target = event_map.get(method)
        if target and target not in allowed_methods:
            report.errors.append(
                f"{rel}:{lineno} event.{method}() 需要权限 {method_to_perm.get(target, target)}（未声明，会被沙箱拒绝）"
            )

    # 动作类型
    for action_type, lineno, rel in facts.action_types:
        if action_type == "<syntax-error>":
            report.errors.append(f"{rel}:{lineno} 语法错误")
        elif action_type in host["event_types"]:
            continue  # 事件信封里的 type（如 payment_confirmed），不是动作
        elif host["canonical_actions"] is not None and action_type not in host["canonical_actions"]:
            report.warnings.append(f"{rel}:{lineno} 动作类型 {action_type!r} 不是标准动作")
        elif action_type == "send_rich_message":
            use("messages.send_rich", f"{rel}:{lineno}")
        elif action_type == "answer_guest_query":
            use("messages.answer_guest_query", f"{rel}:{lineno}")

    # 声明级能力
    if raw.get("config_actions") or _has_override(cls, "on_config_action", host):
        use("config_actions", "config_actions / on_config_action")
    if raw.get("event_subscriptions"):
        use("event_subscriptions", "plugin.json event_subscriptions")
        for sub in raw.get("event_subscriptions") or []:
            if isinstance(sub, dict) and (
                "guest_message" in (sub.get("events") or []) or sub.get("scope") == "guest_all"
            ):
                use("guest_message", "event_subscriptions guest_message/guest_all")
    for sub in raw.get("event_subscriptions") or []:
        if isinstance(sub, dict) and sub.get("scope") == "own_buttons":
            use("callback_routing", "event_subscriptions scope own_buttons")
    if raw.get("reads"):
        use("host.read", "plugin.json reads")
    source_text = "\n".join(_read_text(p) for p in plugin_dir.rglob("*.py") if "__pycache__" not in str(p))
    if re.search(r"""["']callback["']\s*:\s*\{""", source_text):
        use("callback_routing", "reply_markup callback 按钮")
    if ".host.read(" in source_text:
        use("host.read", "ctx.host.read")
    # Plugin API 范围（0.107.0+ 宿主）
    spec = raw.get("plugin_api")
    satisfies = host.get("plugin_api_satisfies")
    if isinstance(spec, str) and spec.strip() and satisfies is not None:
        try:
            if not satisfies(spec, host.get("plugin_api_version")):
                report.errors.append(f"plugin_api={spec} 与宿主 Plugin API {host.get('plugin_api_version')} 不兼容")
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"plugin_api={spec!r} 写法无法识别：{exc}")
    # 弃用接口（宿主 DEPRECATIONS 登记，0.107.0+）
    deprecations = host.get("deprecations") or {}

    def deprecated(dep_id: str, where: str) -> None:
        item = deprecations.get(dep_id)
        if item is not None:
            report.warnings.append(
                f"{where} 使用了已弃用接口 {dep_id}：{item.summary}；请改用 {item.replacement}（计划移除：{item.removal}）"
            )

    if raw.get("min_telebot_version") is not None or "min_telebot_version" in _read_text(plugin_dir / "manifest.py"):
        deprecated("manifest.min_telebot_version", "plugin.json / manifest.py")
    if cls is not None and _has_override(cls, "on_interaction", host) and not _has_override(cls, "on_event", host):
        deprecated("hook.on_interaction", "插件类")
    if "requires_platform_capabilities" in _read_text(plugin_dir / "manifest.py"):
        use("manifest.requires_platform_capabilities", "manifest.py")
    if _has_override(cls, "on_direct_message", host):
        use("direct_passthrough", "on_direct_message")
    if "external_http_bypass_proxy" in perm_set or (isinstance(raw.get("http"), dict) and raw["http"].get("allow_direct")):
        use("external_http_bypass_proxy", "permissions/http")
    if "agent_skills" in perm_set:
        use("skills", "permissions agent_skills")
    for entry in list(raw.get("interaction_entries") or []) + list(raw.get("event_subscriptions") or []):
        if isinstance(entry, dict) and entry.get("timeout_seconds") is not None:
            try:
                if float(entry["timeout_seconds"]) > 90:
                    report.warnings.append(f"timeout_seconds={entry['timeout_seconds']} 超过平台上限 90 秒，会被截断")
            except (TypeError, ValueError):
                report.errors.append(f"timeout_seconds 不是数字: {entry['timeout_seconds']!r}")

    # 实时上下文缺少的方法：只在交互入口（BufferedMessageOps）里可用
    for feature, wheres in list(features.items()):
        if feature.startswith("live_missing:"):
            method = feature.split(":", 1)[1]
            features.pop(feature)
            if method in facts.guard_tokens or "_message_op" in facts.guard_tokens:
                continue
            report.warnings.append(
                f"ctx.messages.{method} 只在交互入口 / Event Bus 回调的缓冲 MessageOps 中存在，"
                f"后台任务与命令里的实时 ctx.messages 没有该方法（{', '.join(wheres)}）；需确认调用场景"
            )

    # 8. 最低版本推断
    required: list[tuple[str, str, str]] = []
    subscribes_guest = "guest_message" in features
    min_versions = feature_min_versions(host)
    probed = _probed_capabilities(plugin_dir)
    for feature, wheres in features.items():
        if feature not in min_versions:
            continue
        version, label = min_versions[feature]
        guards = FEATURE_GUARDS.get(feature, ())
        if any(g in facts.guard_tokens for g in guards):
            # 能力探测：旧平台不会因此失败
            continue
        if _capability_probed(FEATURE_CAPABILITY.get(feature), probed):
            # ctx.capabilities.has(...) 探测过：旧平台走回退分支，不抬高最低版本
            continue
        if feature in GUEST_GATED and subscribes_guest:
            # 只在 guest_message 事件里走到；旧平台不会投递该事件
            continue
        required.append((version, label, ", ".join(wheres)))
    report.features = {k: v for k, v in features.items()}
    if required:
        top = max(required, key=lambda item: _full_version_key(item[0]))
        report.inferred_min = top[0]
        declared = report.min_version or "0.0.0"
        compare = host.get("compare_versions")
        if compare is not None:
            try:
                too_low = compare(declared, top[0]) < 0
            except Exception:  # noqa: BLE001
                too_low = _version_tuple(declared) < _version_tuple(top[0])
        else:
            too_low = _version_tuple(declared) < _version_tuple(top[0])
        if too_low:
            report.warnings.append(
                f"min_telepilot_version={declared} 偏低：用到 {top[1]}（{top[2]}）需要 ≥ {top[0]}"
            )
    host_version = host["version"]
    if report.min_version and _version_tuple(report.min_version) > _version_tuple(host_version):
        report.errors.append(f"min_telepilot_version={report.min_version} 高于当前宿主 {host_version}")

    # 9. 初始化
    if cls is not None and run_startup:
        report.warnings.extend(_run_startup(cls, raw, host, plugin_dir))
    return report


class _LogCapture(logging.Handler):
    """收集 loader 加载插件时的警告 / 异常，作为拒绝加载的原因。"""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.items: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            text = f"{type(exc).__name__}: {exc}"
        self.items.append(text)

    def summary(self) -> str:
        return "；".join(self.items[-2:])[:400]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _manifest_attr_hint(plugin_dir: Path, attr: str) -> bool:
    try:
        return attr in (plugin_dir / "manifest.py").read_text(encoding="utf-8")
    except OSError:
        return False


def _has_override(cls: Any, method: str, host: dict[str, Any]) -> bool:
    if cls is None:
        return False
    base = host["Plugin"]
    return getattr(cls, method, None) is not getattr(base, method, None)


def _check_schema_defaults(schema: dict[str, Any]) -> list[str]:
    from jsonschema import Draft7Validator

    problems: list[str] = []
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    for name, prop in props.items():
        if not isinstance(prop, dict) or "default" not in prop:
            continue
        if name in (schema.get("required") or []):
            # 必填字段的默认值只是表单占位（如空 Token），由用户填写
            continue
        sub = {k: v for k, v in prop.items() if not k.startswith("x-")}
        try:
            errors = list(Draft7Validator(sub).iter_errors(prop["default"]))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"config_schema.{name} 无法校验默认值: {exc}")
            continue
        if errors:
            problems.append(f"config_schema.{name} 默认值不符合自身 schema: {errors[0].message}")
    try:
        defaults = {k: v["default"] for k, v in props.items() if isinstance(v, dict) and "default" in v}
        required = [k for k in schema.get("required") or [] if k not in defaults]
        if not required:
            errors = list(Draft7Validator(schema).iter_errors(defaults))
            if errors:
                problems.append(f"config_schema 默认配置整体校验失败: {errors[0].message}")
    except Exception:  # noqa: BLE001
        pass
    return problems


KNOWN_UI_WIDGETS = {
    "textarea",
    "multi-select",
    "list-select",
    "config-list",
    "dynamic-select",
    "llm-provider-select",
    "llm-model-select",
    "allowed-peer-multi-select",
    "agent-skill-multi-select",
    "password",
    "secret",
    "chat-select",
    "peer-select",
}


def _check_schema_ui(schema: dict[str, Any]) -> list[str]:
    warnings: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            widget = node.get("x-ui-widget")
            if isinstance(widget, str) and widget not in KNOWN_UI_WIDGETS:
                warnings.append(f"config_schema{path} x-ui-widget={widget!r} 不是已知控件，App/Web 会按普通字段渲染")
            for key, value in node.items():
                if key in {"properties", "items"} or isinstance(value, dict | list):
                    walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for idx, item in enumerate(node):
                walk(item, f"{path}[{idx}]")

    walk(schema, "")
    return warnings


def _check_parity(raw: dict[str, Any], manifest: Any, host: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    with_order = host["with_property_order"]

    def norm_min(value: Any) -> str:
        return str(value or "").strip()

    pairs = {
        "version": (raw.get("version"), manifest.version),
        "min_telepilot_version": (
            norm_min(raw.get("min_telepilot_version") or raw.get("min_telebot_version")),
            norm_min(manifest.min_telepilot_version or manifest.min_telebot_version),
        ),
        "permissions": (sorted(raw.get("permissions") or []), sorted(manifest.permissions or [])),
        "category": (raw.get("category") or "utility", manifest.category or "utility"),
        "requires_platform_capabilities": (
            sorted(raw.get("requires_platform_capabilities") or []),
            sorted(manifest.requires_platform_capabilities or []),
        ),
    }
    for name, (left, right) in pairs.items():
        if left != right:
            problems.append(f"plugin.json 与 manifest.py 的 {name} 不一致: {left!r} != {right!r}")
    raw_schema = raw.get("config_schema")
    if raw_schema is not None or manifest.config_schema is not None:
        if with_order(raw_schema) != manifest.config_schema:
            problems.append("plugin.json 与 manifest.py 的 config_schema 不一致")
    for name in ("event_subscriptions", "interaction_entries"):
        if (raw.get(name) or []) != list(getattr(manifest, name, None) or []):
            problems.append(f"plugin.json 与 manifest.py 的 {name} 不一致")
    raw_hosts = raw.get("allowed_hosts")
    if raw_hosts is not None and sorted(raw_hosts) != sorted(manifest.allowed_hosts or []):
        problems.append("plugin.json 与 manifest.py 的 allowed_hosts 不一致")
    return problems


def _check_hooks(cls: Any, host: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    base = host["Plugin"]
    for hook in (
        "on_startup",
        "on_shutdown",
        "on_message",
        "on_message_edited",
        "on_direct_message",
        "on_command",
        "on_interaction",
        "on_event",
        "on_config_action",
    ):
        impl = getattr(cls, hook, None)
        ref = getattr(base, hook, None)
        if impl is None or ref is None or impl is ref:
            continue
        if not inspect.iscoroutinefunction(impl):
            problems.append(f"{cls.__name__}.{hook} 必须是 async def")
            continue
        try:
            ref_params = [p for p in inspect.signature(ref).parameters.values()]
            impl_sig = inspect.signature(impl)
            dummy = [object()] * len(ref_params)
            impl_sig.bind(*dummy)
        except TypeError as exc:
            problems.append(f"{cls.__name__}.{hook} 签名与宿主调用方式不兼容: {exc}")
        except ValueError:
            pass
    channels = getattr(cls, "message_channels", {"incoming"})
    if not set(channels) <= {"incoming", "outgoing"}:
        problems.append(f"message_channels 含未知方向: {sorted(set(channels) - {'incoming', 'outgoing'})}")
    return problems


class _MemoryStorage:
    """与 PluginStorage 同签名的内存实现，供初始化检查使用。"""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    @property
    def available(self) -> bool:
        return True

    async def get(self, key: Any, default: Any = None) -> Any:
        return self.data.get(str(key), default)

    async def set(self, key: Any, value: Any, *, ttl: int | float | None = None) -> bool:
        json.dumps(value, default=str)
        self.data[str(key)] = value
        return True

    async def delete(self, *keys: Any) -> int:
        return sum(1 for k in keys if self.data.pop(str(k), None) is not None)

    async def incr(self, key: Any, amount: int = 1, *, ttl: int | float | None = None) -> int:
        value = int(self.data.get(str(key), 0)) + int(amount)
        self.data[str(key)] = value
        return value

    async def get_all(self) -> dict[str, Any]:
        return dict(self.data)

    async def items(self) -> dict[str, Any]:
        return dict(self.data)


class _RecordingScheduler:
    """按真实 SchedulerFacade 签名绑定参数，只记录不执行。"""

    def __init__(self, facade_cls: type) -> None:
        self.facade_cls = facade_cls
        self.jobs: dict[str, Any] = {}

    def register(self, *args: Any, **kwargs: Any) -> None:
        inspect.signature(self.facade_cls.register).bind(self, *args, **kwargs)
        job_id = kwargs.get("job_id") or (args[0] if args else "")
        schedule = kwargs.get("schedule") or (args[1] if len(args) > 1 else None)
        if not isinstance(schedule, dict):
            raise TypeError("schedule 必须是 dict")
        self.jobs[str(job_id)] = schedule

    def unregister(self, job_id: str) -> bool:
        return self.jobs.pop(job_id, None) is not None

    def unregister_all(self) -> int:
        count = len(self.jobs)
        self.jobs.clear()
        return count

    def list_jobs(self) -> list[dict[str, Any]]:
        return [{"job_id": k, "schedule": v} for k, v in self.jobs.items()]


def _schema_defaults(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    props = schema.get("properties")
    if not isinstance(props, dict):
        return {}
    return {k: v["default"] for k, v in props.items() if isinstance(v, dict) and "default" in v}


def _run_startup(cls: Any, raw: dict[str, Any], host: dict[str, Any], plugin_dir: Path) -> list[str]:
    warnings: list[str] = []
    try:
        inst = cls()
    except Exception as exc:  # noqa: BLE001
        return [f"实例化失败: {type(exc).__name__}: {exc}"]

    try:
        from app.worker.plugins.message_ops import BufferedMessageOps
    except ImportError:  # pragma: no cover - 旧宿主
        BufferedMessageOps = None  # type: ignore[assignment]

    async def _log(level: str, message: str, **detail: Any) -> None:
        return None

    scheduler_cls = (host["facades"]["scheduler"] or [_RecordingScheduler])[0]
    with tempfile.TemporaryDirectory() as tmp:
        ctx = host["PluginContext"](
            account_id=0,
            feature_key=plugin_dir.name,
            config=_schema_defaults(raw.get("config_schema")),
            account_config={},
            storage=_MemoryStorage(),
            data_dir=Path(tmp),
            log=_log,
            scheduler=_RecordingScheduler(scheduler_cls),
            messages=BufferedMessageOps() if BufferedMessageOps else None,
        )

        async def _go() -> None:
            before = set(asyncio.all_tasks())
            try:
                await asyncio.wait_for(inst.on_startup(ctx), timeout=5)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"on_startup 在测试上下文中失败（无 Telegram client）: {type(exc).__name__}: {exc}"[:300])
            with contextlib.suppress(Exception):
                await asyncio.wait_for(inst.on_shutdown(ctx), timeout=5)
            for task in set(asyncio.all_tasks()) - before:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task

        try:
            asyncio.run(_go())
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"初始化检查异常: {type(exc).__name__}: {exc}")
    return warnings


# ─────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────
def discover_plugins(root: Path) -> list[Path]:
    return sorted(p.parent for p in root.glob("*/plugin.json"))


def run(
    *,
    host_repo: str | None = None,
    only: list[str] | None = None,
    run_startup: bool = True,
    plugins_root: Path | None = None,
) -> tuple[dict[str, Any], list[PluginReport]]:
    repo = locate_host_repo(host_repo)
    with tempfile.TemporaryDirectory() as installed:
        host = prepare_host(repo, Path(installed))
        reports = []
        for plugin_dir in discover_plugins(plugins_root or ROOT):
            if only and plugin_dir.name not in only:
                continue
            reports.append(check_plugin(plugin_dir, host, run_startup=run_startup))
    return {"host_repo": str(repo), "host_version": host["version"]}, reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--host-repo", help="TelePilot 主仓库路径（默认读 TELEPILOT_REPO）")
    parser.add_argument("--plugin", action="append", help="只检查指定插件，可重复")
    parser.add_argument("--json", help="把完整报告写到 JSON 文件")
    parser.add_argument("--strict", action="store_true", help="警告也视为失败")
    parser.add_argument("--no-startup", action="store_true", help="跳过 on_startup 初始化检查")
    parser.add_argument("--quiet", action="store_true", help="只输出有问题的插件")
    parser.add_argument(
        "--plugins-root",
        help="插件目录的上级目录（默认本仓库根目录；可指向主仓库 examples/plugins 校验示例）",
    )
    args = parser.parse_args(argv)

    info, reports = run(
        host_repo=args.host_repo,
        only=args.plugin,
        run_startup=not args.no_startup,
        plugins_root=Path(args.plugins_root).resolve() if args.plugins_root else None,
    )
    print(f"TelePilot 宿主: {info['host_version']}（{info['host_repo']}）")
    counts = {"OK": 0, "WARN": 0, "ERROR": 0}
    for rep in reports:
        counts[rep.status] += 1
        if args.quiet and rep.status == "OK":
            continue
        print(f"[{rep.status:5}] {rep.key} {rep.version} (min {rep.min_version or '-'}"
              f"{', 推断 ' + rep.inferred_min if rep.inferred_min else ''})")
        for item in rep.errors:
            print(f"    ✗ {item}")
        for item in rep.warnings:
            print(f"    ! {item}")
    print(f"共 {len(reports)} 个插件：OK {counts['OK']} / 警告 {counts['WARN']} / 错误 {counts['ERROR']}")
    if args.json:
        Path(args.json).write_text(
            json.dumps({**info, "plugins": [r.to_dict() for r in reports]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if counts["ERROR"] or (args.strict and counts["WARN"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
