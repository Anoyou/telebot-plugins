"""interaction-ai-chat 0.4.11：管理员名单优先读 ctx.host.read（TelePilot 0.108.0+），旧平台回退 payload。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import test_interaction_ai_chat as base


def test_host_admin_ids_reads_declared_resource() -> None:
    helper = base.PLUGIN._host_admin_ids

    class _Caps:
        def __init__(self, ok: bool) -> None:
            self.ok = ok

        def has(self, name: str) -> bool:
            return self.ok and name == "host.read.account.admins@1"

    class _Host:
        async def read(self, ref: str):
            assert ref == "account.admins@1"
            return {"owner_user_ids": [1], "admin_user_ids": [1, "2"]}

    assert asyncio.run(helper(SimpleNamespace(capabilities=_Caps(True), host=_Host()))) == {1, 2}
    assert asyncio.run(helper(SimpleNamespace(capabilities=_Caps(False), host=_Host()))) == set()
    assert asyncio.run(helper(SimpleNamespace())) == set()

    class _Broken:
        async def read(self, ref: str):
            raise PermissionError("未声明")

    assert asyncio.run(helper(SimpleNamespace(capabilities=_Caps(True), host=_Broken()))) == set()
