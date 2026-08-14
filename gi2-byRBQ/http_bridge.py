from __future__ import annotations
import json
from typing import Any
class Response:
    def __init__(self, raw: Any):
        self._raw = raw; self.status = int(getattr(raw, "status_code", 0)); self.status_code = self.status
        self.headers = getattr(raw, "headers", {}); self.url = getattr(raw, "url", "")
    async def read(self) -> bytes: return bytes(getattr(self._raw, "content", b""))
    async def text(self, *args: Any, **kwargs: Any) -> str: return str(getattr(self._raw, "text", ""))
    async def json(self, *args: Any, **kwargs: Any) -> Any:
        try: return self._raw.json()
        except Exception: return json.loads((await self.read()).decode("utf-8", "replace"))
    async def __aenter__(self): return self
    async def __aexit__(self, *exc: Any): return None
class Request:
    def __init__(self, awaitable: Any): self._awaitable = awaitable
    async def __aenter__(self): return Response(await self._awaitable)
    async def __aexit__(self, *exc: Any): return None
class Session:
    def __init__(self, http: Any):
        if http is None: raise RuntimeError("TelePilot 未注入 ctx.http，拒绝直接网络请求")
        self.http = http
    async def __aenter__(self): return self
    async def __aexit__(self, *exc: Any): return None
    def get(self, url: str, **kwargs: Any):
        kwargs.pop("allow_redirects", None); kwargs.pop("timeout", None); return Request(self.http.get(url, **kwargs))
    def post(self, url: str, **kwargs: Any):
        kwargs.pop("allow_redirects", None); kwargs.pop("timeout", None); return Request(self.http.post(url, **kwargs))
