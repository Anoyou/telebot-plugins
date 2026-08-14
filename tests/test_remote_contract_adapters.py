from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
_BRIDGE_SPEC = importlib.util.spec_from_file_location("_ais_http_bridge", ROOT / "ais-byRBQ" / "http_bridge.py")
assert _BRIDGE_SPEC is not None and _BRIDGE_SPEC.loader is not None
_BRIDGE = importlib.util.module_from_spec(_BRIDGE_SPEC)
_BRIDGE_SPEC.loader.exec_module(_BRIDGE)
Session = _BRIDGE.Session
BYRBQ = (
    "ais-byRBQ", "cai-byRBQ", "get_reactions-byRBQ", "gi2-byRBQ", "jpm-byRBQ",
    "jpmai-byRBQ", "luckydraw-byRBQ", "pixivshow-byRBQ", "sar-byRBQ", "sfl-byRBQ",
    "share_plugins-byRBQ",
)


class _HTTP:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    async def get(self, url: str, **kwargs):
        self.calls.append(("get", url, kwargs))
        return SimpleNamespace(
            status_code=200, headers={"x-test": "1"}, url=url,
            content=b'{"ok": true}', text='{"ok": true}', json=lambda: {"ok": True},
        )

    async def post(self, url: str, **kwargs):
        self.calls.append(("post", url, kwargs))
        return SimpleNamespace(
            status_code=201, headers={}, url=url, content=b"done", text="done",
            json=lambda: {"done": True},
        )


@pytest.mark.asyncio
async def test_http_bridge_uses_facade_and_filters_legacy_transport_options() -> None:
    http = _HTTP()
    async with Session(http) as session:
        async with session.get(
            "https://api.openai.com/v1/models",
            timeout=30,
            allow_redirects=True,
            headers={"Authorization": "Bearer test"},
        ) as response:
            assert response.status == 200
            assert await response.json() == {"ok": True}
    assert http.calls == [(
        "get",
        "https://api.openai.com/v1/models",
        {"headers": {"Authorization": "Bearer test"}},
    )]


def test_byrbq_compat_writes_are_routed_or_fail_closed() -> None:
    for key in BYRBQ:
        source = (ROOT / key / "plugin.py").read_text(encoding="utf-8")
        assert "self._c.send" not in source, key
        assert "self._c.edit" not in source, key
        assert "self._c.delete" not in source, key
        assert "self._e.reply(" not in source, key
        assert "self._e.edit(" not in source, key
        assert "self._e.delete(" not in source, key
        assert 'await self._message_op(' in source, key
        assert "MessageOps 暂不支持发送表情反应" in source, key
        assert "MessageOps 不支持 sticker" in source, key


def test_luckydraw_callback_is_bound_to_expected_bot_and_button_text() -> None:
    source = (ROOT / "luckydraw-byRBQ" / "plugin.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "click_callback_button"
    ]
    assert len(calls) == 1
    keywords = {item.arg for item in calls[0].keywords}
    assert {"expected_bot_id", "expected_button_text"} <= keywords


def test_gi2_multipart_is_forwarded_as_facade_data_and_files() -> None:
    source = (ROOT / "gi2-byRBQ" / "legacy_main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "post"
    ]
    assert any({item.arg for item in call.keywords} >= {"data", "files"} for call in calls)
    assert "request_edit_with_curl_sync" not in source
