"""interaction-ai-chat：内置 skill（ctx.skills.bundled 优先、本地兜底）与站点 Wiki 只读检索。

全部使用假的 ctx.skills / ctx.http / ctx.ai，不发任何真实网络请求。
"""

from __future__ import annotations

import json
import re
import types
import unittest
from pathlib import Path

import test_interaction_ai_chat as base

PACKAGE = base.PACKAGE
PLUGIN = base.PLUGIN
WIKI = __import__(f"{PLUGIN.__name__.rsplit('.', 1)[0]}.wiki_source", fromlist=["wiki_source"])

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "interaction_ai_chat"
WIKI_HTML = (FIXTURES / "wiki_download_rules.html").read_text(encoding="utf-8")
BASE = "https://wiki.qingwapt.org/docs"
PAGE = "/docs/rules/download-rules"
SKILL_DIR = base.PLUGIN_DIR / "skills"


def assert_no_secret(test: unittest.TestCase, text: str) -> None:
    lower = text.lower()
    for word in ("passkey", "torrentrss", "rss", "secretauthvalue", "authkey"):
        test.assertNotIn(word, lower, word)
    test.assertIsNone(re.search(r"[0-9a-f]{32,}", lower))


class FakeResponse:
    def __init__(self, status: int = 200, text: str = "", headers: dict | None = None) -> None:
        self.status_code = status
        self.text = text
        self.headers = headers or {}


class FakeHTTP:
    """只实现 get；插件若尝试其它方法会 AttributeError。"""

    def __init__(self, responses: list[FakeResponse] | None = None) -> None:
        self.responses = list(responses or [])
        self.requests: list[str] = []

    async def get(self, url, **kwargs):
        self.requests.append(url)
        if self.responses:
            return self.responses.pop(0)
        return FakeResponse(200, WIKI_HTML)


class FakeSkills:
    def __init__(self, bundled=None, *, error: Exception | None = None, installed=None) -> None:
        self._bundled = bundled
        self._error = error
        self.installed = installed or {}
        self.calls: list[tuple] = []

    async def bundled(self, *, max_chars=None):
        self.calls.append(("bundled", max_chars))
        if self._error is not None:
            raise self._error
        return list(self._bundled or [])

    async def list(self, names=None):
        self.calls.append(("list", names))
        return [{"name": name, "description": desc} for name, (desc, _body) in self.installed.items() if not names or name in names]

    async def load(self, name, *, max_chars=None):
        self.calls.append(("load", name))
        return {"name": name, "content": self.installed[name][1]}


class RoutingAI:
    """按系统提示区分：Wiki 页面选择 / skill 选择 / 最终回答。"""

    def __init__(self, pages=None, skills=None, answer: str = "按 Wiki：新种发布 2 天内为 Free。") -> None:
        self.pages = pages if pages is not None else [PAGE]
        self.skills = skills or []
        self.answer = answer
        self.calls: list[dict] = []

    async def complete(self, system, user, **kwargs):
        self.calls.append({"system": system, "user": user, **kwargs})
        if "Wiki 页面选择器" in system:
            return types.SimpleNamespace(text=json.dumps({"pages": self.pages}))
        if "skill 选择器" in system:
            return types.SimpleNamespace(text=json.dumps({"skills": self.skills}))
        return types.SimpleNamespace(text=self.answer)

    @property
    def answer_calls(self) -> list[dict]:
        return [c for c in self.calls if "选择器，不直接回答" not in c["system"]]

    @property
    def wiki_router_calls(self) -> list[dict]:
        return [c for c in self.calls if "Wiki 页面选择器" in c["system"]]


def bundled_items(group_helper: str | None = "【已编辑】群聊答疑助手（管理员改过）", wiki: bool = True) -> list[dict]:
    items = []
    if group_helper is not None:
        items.append({"name": "group-helper", "content": group_helper})
    if wiki:
        body = PLUGIN.split_front_matter((SKILL_DIR / "qingwa-wiki" / "SKILL.md").read_text("utf-8"))
        items.append({"name": "qingwa-wiki", "content": body})
    return items


def make_ctx(config: dict | None = None, *, ai=None, skills=None, http=None, with_http: bool = True):
    cfg = {"per_user_daily_limit": 0, "global_daily_limit": 0, "thinking_placeholder": False, **(config or {})}
    ctx = base.Ctx(config=cfg, ai=ai or RoutingAI())
    if skills is not None:
        ctx.skills = skills
    if with_http:
        ctx.http = http if http is not None else FakeHTTP()
    return ctx


def private(text: str, user_id: int = 601):
    return base.message_payload(text, chat_id=user_id, chat_type="private", user_id=user_id)


def reply_text(actions: list[dict]) -> str:
    return "\n".join(str(action.get("text") or "") for action in actions)


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        base._install_stubs()


class ManifestSkillsTest(_Case):
    def test_fields_permissions_and_bundled_skills(self) -> None:
        schema = PACKAGE.MANIFEST.config_schema
        props = schema["properties"]
        self.assertNotIn("skill_builtin_enabled", props)  # 内置 skill 由平台统一启停
        self.assertEqual(props["skill_names"]["x-ui-widget"], "agent-skill-multi-select")
        self.assertEqual(props["wiki_base_url"]["default"], "")
        self.assertNotIn("wiki_base_url", schema["required"])
        self.assertNotIn("skill_names", schema["required"])
        self.assertIn("agent_skills", PACKAGE.MANIFEST.permissions)
        self.assertIn("**.org", PACKAGE.MANIFEST.allowed_hosts)
        for name in ("group-helper", "qingwa-wiki"):
            text = (SKILL_DIR / name / "SKILL.md").read_text("utf-8")
            self.assertTrue(text.startswith(f"---\nname: {name}\n"), name)
        self.assertIn("优先查站点 Wiki", (SKILL_DIR / "group-helper" / "SKILL.md").read_text("utf-8"))

    def test_wiki_catalog_covers_docs_pages(self) -> None:
        body = (SKILL_DIR / "qingwa-wiki" / "SKILL.md").read_text("utf-8")
        catalog = WIKI.parse_catalog(body)
        paths = [path for path, _ in catalog]
        self.assertGreaterEqual(len(paths), 35)
        self.assertEqual(len(paths), len(set(paths)))
        for path in ("/docs/getting-started/newbie", "/docs/rules/download-rules", "/docs/tools/bt-clients/qbittorrent", "/docs/faqs"):
            self.assertIn(path, paths)
        self.assertTrue(all(WIKI.clean_path(path) == path for path in paths))
        self.assertNotIn("/docs/", WIKI.skill_guidance(body))


class WikiPolicyTest(_Case):
    def test_normalize_base(self) -> None:
        self.assertEqual(WIKI.normalize_base("https://wiki.qingwapt.org/docs/"), BASE)
        self.assertEqual(WIKI.normalize_base("wiki.qingwapt.org"), BASE)
        self.assertEqual(WIKI.normalize_base("https://Wiki.QingwaPT.org:8443/docs"), "https://wiki.qingwapt.org:8443/docs")
        for bad in ("", "http://wiki.qingwapt.org/docs", "https://wiki.qingwapt.org/admin", "https://u:p@wiki.qingwapt.org/docs", "https://localhost/docs"):
            self.assertEqual(WIKI.normalize_base(bad), "", bad)

    def test_whitelist(self) -> None:
        ok = WIKI.page_url(BASE, PAGE)
        self.assertEqual(ok, "https://wiki.qingwapt.org/docs/rules/download-rules")
        WIKI.assert_request_allowed("GET", ok, BASE)
        bad = [
            ("POST", ok),
            ("GET", "https://evil.example.org/docs/rules"),
            ("GET", "http://wiki.qingwapt.org/docs/rules"),
            ("GET", "https://wiki.qingwapt.org/api/users"),
            ("GET", "https://wiki.qingwapt.org/docs/rules?passkey=1"),
            ("GET", "https://wiki.qingwapt.org/docs/../admin"),
            ("GET", "https://wiki.qingwapt.org/docsx"),
        ]
        for method, url in bad:
            with self.assertRaises(WIKI.WikiPolicyError, msg=url):
                WIKI.assert_request_allowed(method, url, BASE)
        self.assertEqual(WIKI.clean_path("/docs/../etc"), "")
        self.assertEqual(WIKI.page_url(BASE, "/admin"), "")

    def test_cross_host_redirect_is_refused(self) -> None:
        http = FakeHTTP([FakeResponse(302, headers={"Location": "https://evil.example.org/docs/x"})])
        with self.assertRaises(WIKI.WikiPolicyError):
            base.run(WIKI.read_page(http, BASE, PAGE, cache=WIKI.WikiCache()))
        self.assertEqual(len(http.requests), 1)

    def test_same_host_docs_redirect_is_followed(self) -> None:
        http = FakeHTTP([FakeResponse(301, headers={"location": "/docs/rules/download-rules/"}), FakeResponse(200, WIKI_HTML)])
        page = base.run(WIKI.read_page(http, BASE, PAGE, cache=WIKI.WikiCache()))
        self.assertEqual(page["title"], "下载规则")
        self.assertEqual(len(http.requests), 2)


class WikiExtractTest(_Case):
    def test_extracts_h1_to_toc_and_strips_secrets(self) -> None:
        title, body = WIKI.extract_article(WIKI_HTML)
        self.assertEqual(title, "下载规则")
        self.assertIn("## 分享率", body)
        self.assertIn("H&R", body)
        self.assertIn("新种在发布后 2 天之内为 Free", body)
        self.assertNotIn("本页内容", body)
        self.assertNotIn("Copyright", body)
        self.assertNotIn("svg 文字", body)
        self.assertNotIn("首页", body)
        cleaned, hits = WIKI.sanitize(body)
        assert_no_secret(self, cleaned)
        self.assertGreaterEqual(hits, 3)
        self.assertIn("分享率为上传量和下载量的比值", cleaned)

    def test_truncate_and_cache(self) -> None:
        text, cut = WIKI.truncate("一行内容\n" * 2000, 300)
        self.assertTrue(cut)
        self.assertLess(len(text), 340)
        http = FakeHTTP()
        cache = WIKI.WikiCache()
        first = base.run(WIKI.read_page(http, BASE, PAGE, cache=cache))
        second = base.run(WIKI.read_page(http, BASE, PAGE, cache=cache))
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(len(http.requests), 1)
        self.assertEqual(WIKI.CACHE_TTL_SECONDS, 6 * 3600)
        assert_no_secret(self, json.dumps(second, ensure_ascii=False))


class BundledSkillTest(_Case):
    def test_platform_bundled_skill_with_user_edit_is_used(self) -> None:
        skills = FakeSkills(bundled_items(wiki=False))
        ctx = make_ctx(skills=skills)
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("你好")))
        system = ctx.ai.answer_calls[0]["system"]
        self.assertIn("【已编辑】群聊答疑助手（管理员改过）", system)
        self.assertNotIn("群聊礼仪", system)  # 本地文件版本没有被注入
        self.assertEqual(skills.calls[0][0], "bundled")

    def test_platform_disabled_bundled_skill_is_not_injected(self) -> None:
        ctx = make_ctx(skills=FakeSkills([]))
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("你好")))
        self.assertNotIn("内置 skill：group-helper", ctx.ai.answer_calls[0]["system"])

    def test_falls_back_to_local_files_without_ctx_skills(self) -> None:
        ctx = make_ctx()
        plugin = PLUGIN.InteractionAIChatPlugin()
        base.run(plugin.on_event(ctx, private("你好")))
        base.run(plugin.on_event(ctx, private("再问一次")))
        system = ctx.ai.answer_calls[0]["system"]
        self.assertIn("内置 skill：group-helper", system)
        self.assertIn("群聊礼仪", system)
        self.assertNotIn("页面目录", system)  # qingwa-wiki 只作目录，不整篇注入
        missing = [log for log in ctx.logs if "平台未提供 ctx.skills" in log[1]]
        self.assertEqual(len(missing), 1)  # 只记一次

    def test_falls_back_to_local_files_when_platform_errors(self) -> None:
        ctx = make_ctx(skills=FakeSkills(error=RuntimeError("db down")))
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("你好")))
        self.assertIn("群聊礼仪", ctx.ai.answer_calls[0]["system"])
        self.assertTrue(any("改用插件自带版本" in log[1] for log in ctx.logs))

    def test_installed_skill_names_exclude_bundled(self) -> None:
        cfg = PLUGIN._cfg(types.SimpleNamespace(config={"skill_names": ["group-helper", "qingwa-wiki", "pdf-helper", "Bad Name"]}))
        self.assertEqual(cfg["skills"]["names"], ["pdf-helper"])

    def test_installed_skill_is_loaded_when_router_picks_it(self) -> None:
        skills = FakeSkills(bundled_items(), installed={"pdf-helper": ("处理 PDF", "PDF 处理说明正文")})
        ai = RoutingAI(skills=["pdf-helper"])
        ctx = make_ctx({"skill_names": ["pdf-helper"]}, ai=ai, skills=skills)
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("这个 PDF 怎么拆分")))
        self.assertIn("PDF 处理说明正文", ai.answer_calls[0]["system"])


class WikiFlowTest(_Case):
    def test_wiki_pages_are_fetched_cleaned_and_linked(self) -> None:
        ai = RoutingAI()
        http = FakeHTTP()
        ctx = make_ctx({"wiki_base_url": BASE}, ai=ai, skills=FakeSkills(bundled_items()), http=http)
        actions = base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("新种多久是 Free？H&R 考核吗")))
        self.assertEqual(http.requests, ["https://wiki.qingwapt.org/docs/rules/download-rules"])
        self.assertEqual(len(ai.wiki_router_calls), 1)
        self.assertIn(PAGE, ai.wiki_router_calls[0]["system"])
        answer_call = ai.answer_calls[0]
        self.assertIn("站点 Wiki 资料", answer_call["user"])
        self.assertIn("新种在发布后 2 天之内为 Free", answer_call["user"])
        self.assertIn("【站点 Wiki】", answer_call["system"])
        assert_no_secret(self, answer_call["user"])
        text = reply_text(actions)
        self.assertIn("https://wiki.qingwapt.org/docs/rules/download-rules", text)
        self.assertIn("Wiki：下载规则", text)
        self.assertTrue(any(log[1] == "已读取站点 Wiki" for log in ctx.logs))

    def test_guest_mode_can_use_wiki(self) -> None:
        http = FakeHTTP()
        ctx = make_ctx({"wiki_base_url": BASE}, skills=FakeSkills(bundled_items()), http=http)
        actions = base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, base.guest_payload("保种有什么要求")))
        self.assertEqual(len(http.requests), 1)
        self.assertEqual(actions[0]["type"], "answer_guest_query")
        self.assertIn("docs/rules/download-rules", actions[0]["text"])

    def test_wiki_works_with_local_catalog_on_old_platform(self) -> None:
        http = FakeHTTP()
        ctx = make_ctx({"wiki_base_url": BASE}, http=http)
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("H&R 考核吗")))
        self.assertEqual(len(http.requests), 1)

    def test_skips_are_logged_with_reason(self) -> None:
        # 未填写地址：不请求，只记一次原因
        ctx = make_ctx(skills=FakeSkills(bundled_items()))
        plugin = PLUGIN.InteractionAIChatPlugin()
        base.run(plugin.on_event(ctx, private("H&R 考核吗")))
        base.run(plugin.on_event(ctx, private("H&R 考核吗")))
        self.assertEqual(ctx.http.requests, [])
        self.assertEqual(len([log for log in ctx.logs if "未填写「站点 Wiki 地址」" in log[1]]), 1)
        self.assertEqual(ctx.ai.wiki_router_calls, [])
        # qingwa-wiki 被管理员停用
        ctx = make_ctx({"wiki_base_url": BASE}, skills=FakeSkills(bundled_items(wiki=False)))
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("H&R 考核吗")))
        self.assertEqual(ctx.http.requests, [])
        self.assertTrue(any("qingwa-wiki 已停用" in log[1] for log in ctx.logs))
        # 与 Wiki 无关
        ctx = make_ctx({"wiki_base_url": BASE}, ai=RoutingAI(pages=[]), skills=FakeSkills(bundled_items()))
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("今天天气")))
        self.assertEqual(ctx.http.requests, [])
        self.assertTrue(any("与 Wiki 无关" in log[1] for log in ctx.logs))

    def test_router_paths_outside_catalog_are_ignored(self) -> None:
        ai = RoutingAI(pages=["/docs/secret-admin", "https://evil.example.org/docs/x", "/api/users"])
        ctx = make_ctx({"wiki_base_url": BASE}, ai=ai, skills=FakeSkills(bundled_items()))
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("规则")))
        self.assertEqual(ctx.http.requests, [])

    def test_fetch_failure_does_not_break_answer(self) -> None:
        http = FakeHTTP([FakeResponse(500, "")])
        ctx = make_ctx({"wiki_base_url": BASE}, skills=FakeSkills(bundled_items()), http=http)
        actions = base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("H&R 考核吗")))
        self.assertIn("按 Wiki", reply_text(actions))
        self.assertNotIn("站点 Wiki 资料", ctx.ai.answer_calls[0]["user"])
        self.assertTrue(any("读取 Wiki 页面失败" in log[1] for log in ctx.logs))

    def test_parse_wiki_route(self) -> None:
        allowed = {PAGE, "/docs/faqs"}
        self.assertEqual(PLUGIN.parse_wiki_route('{"pages": ["/docs/faqs", "/docs/x", "/docs/faqs"]}', allowed), ["/docs/faqs"])
        self.assertEqual(PLUGIN.parse_wiki_route("<think>x</think>不需要", allowed), [])
        self.assertEqual(len(PLUGIN.parse_wiki_route(json.dumps({"pages": [PAGE, "/docs/faqs", PAGE]}), allowed)), 2)


if __name__ == "__main__":
    unittest.main()
