"""interaction-ai-chat：PT 站点（NexusPHP）只读数据源。

全部用本地 HTML fixture 与假的 ctx.http / ctx.ai，不发任何真实网络请求。
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
PT = __import__(f"{PLUGIN.__name__.rsplit('.', 1)[0]}.pt_source", fromlist=["pt_source"])

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "interaction_ai_chat"
LIST_HTML = (FIXTURES / "nexusphp_torrents.html").read_text(encoding="utf-8")
LOGIN_HTML = (FIXTURES / "nexusphp_login.html").read_text(encoding="utf-8")
EMPTY_HTML = (FIXTURES / "nexusphp_empty.html").read_text(encoding="utf-8")

SITE = "https://pt.example.com"
COOKIE = "c_secure_uid=424242; c_secure_pass=CookieSecretValue9876; c_secure_login=bm9wZQ%3D%3D"
USERNAME = "SecretUserZed"
UID = "424242"
FORBIDDEN = (
    "passkey",
    "magnet:",
    "download.php",
    "torrentrss",
    "thanks.php",
    "0123456789abcdef0123456789abcdef",
    "deadbeefdeadbeef",
    "CookieSecretValue9876",
    "c_secure_pass",
    USERNAME,
    UID,
)


def assert_clean(test: unittest.TestCase, text: str) -> None:
    for word in FORBIDDEN:
        test.assertNotIn(word.lower(), text.lower(), word)
    test.assertIsNone(re.search(r"(?i)[0-9a-f]{32,}", text))


class FakeResponse:
    def __init__(self, status: int = 200, text: str = "", headers: dict | None = None, url: str = "") -> None:
        self.status_code = status
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = headers or {}
        self.url = url


class FakeHTTP:
    """只实现 get：插件若尝试 post 会直接 AttributeError。"""

    def __init__(self, responses: list[FakeResponse] | None = None) -> None:
        self.responses = list(responses or [])
        self.requests: list[tuple[str, dict]] = []

    async def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        if not self.responses:
            return FakeResponse(200, LIST_HTML, url=url)
        return self.responses.pop(0)


class RouterAI:
    """第一次（判断步骤）返回 pt_search JSON，之后返回最终回答。"""

    def __init__(self, route: dict | str | None = None, answer: str = "站里有两个版本。") -> None:
        self.route = route if route is not None else {"pt_search": True, "keyword": "Dune", "category": "", "area": "normal"}
        self.answer = answer
        self.calls: list[dict] = []

    async def complete(self, system, user, **kwargs):
        self.calls.append({"system": system, "user": user, **kwargs})
        if "查询判断器" in system:
            text = self.route if isinstance(self.route, str) else json.dumps(self.route, ensure_ascii=False)
            return types.SimpleNamespace(text=text)
        return types.SimpleNamespace(text=self.answer)

    @property
    def answer_calls(self) -> list[dict]:
        return [call for call in self.calls if "查询判断器" not in call["system"]]

    @property
    def router_calls(self) -> list[dict]:
        return [call for call in self.calls if "查询判断器" in call["system"]]


def pt_config(**extra) -> dict:
    config = {
        "pt_enabled": True,
        "pt_site_url": SITE,
        "pt_cookie": COOKIE,
        "allowed_user_ids": "601",
        "per_user_daily_limit": 0,
        "global_daily_limit": 0,
        "thinking_placeholder": False,
    }
    config.update(extra)
    return config


def make_ctx(config: dict | None = None, ai=None, http: FakeHTTP | None = None):
    ctx = base.Ctx(config=pt_config(**(config or {})), ai=ai or RouterAI())
    ctx.http = http if http is not None else FakeHTTP()
    return ctx


def private(text: str, user_id: int = 601):
    return base.message_payload(text, chat_id=user_id, chat_type="private", user_id=user_id)


def reply_text(actions: list[dict]) -> str:
    return "\n".join(str(action.get("text") or "") for action in actions)


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        base._install_stubs()


class ParseTest(_Case):
    def test_parses_nexusphp_list_and_rebuilds_details_links(self) -> None:
        items, user = PT.parse_torrent_list(LIST_HTML, SITE, limit=10)
        self.assertEqual(user, {"name": USERNAME, "uid": UID})
        self.assertEqual([item["id"] for item in items], [1001, 1002])
        first, second = items
        self.assertEqual(first["url"], f"{SITE}/details.php?id=1001")
        self.assertEqual(second["url"], f"{SITE}/details.php?id=1002")
        self.assertEqual(first["category"], "电影/Movies")
        self.assertEqual(first["size"], "61.25 GB")
        self.assertEqual((first["seeders"], first["leechers"], first["completed"]), (128, 7, 1024))
        self.assertEqual(first["promotion"], "Free")
        self.assertEqual(first["added"], "2026-10-20 08:30:00")
        self.assertTrue(first["subtitle"].startswith("沙丘2"))
        self.assertEqual(second["promotion"], "50%")
        self.assertEqual((second["seeders"], second["leechers"], second["completed"]), (45, 0, 300))
        # 只有白名单字段，没有页面原始 href。
        self.assertEqual(
            set(first),
            {"id", "title", "subtitle", "category", "size", "seeders", "leechers", "completed", "promotion", "added", "url"},
        )

    def test_limit_and_empty_page(self) -> None:
        items, _ = PT.parse_torrent_list(LIST_HTML, SITE, limit=1)
        self.assertEqual(len(items), 1)
        items, user = PT.parse_torrent_list(EMPTY_HTML, SITE)
        self.assertEqual(items, [])
        self.assertEqual(user["uid"], UID)

    def test_sanitizer_removes_every_secret_before_model(self) -> None:
        items, user = PT.parse_torrent_list(LIST_HTML, SITE, limit=10)
        sanitizer = PT.Sanitizer(SITE, PT.build_blockwords(COOKIE, user["name"], user["uid"]))
        cleaned = [sanitizer.clean_item(item) for item in items]
        text = PT.format_items_for_model(cleaned, keyword="Dune", area="")
        assert_clean(self, json.dumps(cleaned, ensure_ascii=False))
        assert_clean(self, text)
        self.assertIn(f"{SITE}/details.php?id=1001", text)
        self.assertIn("61.25 GB", text)
        self.assertGreater(sanitizer.hit_count, 0)
        self.assertTrue({"magnet", "secret_param", "username"} <= set(sanitizer.hits))

    def test_sanitizer_link_rules(self) -> None:
        sanitizer = PT.Sanitizer(SITE, PT.build_blockwords(COOKIE, USERNAME, UID))
        text = (
            f"详情 [点我]({SITE}/details.php?id=12&hit=1) 下载 [种子]({SITE}/download.php?id=12&passkey=abc) "
            f"另见 {SITE}/torrents.php?search=x 和 https://pt.example.com/details.php?id=5 "
            "磁力 magnet:?xt=urn:btih:ABCDEF 以及 authkey=zzz torrent_pass=yyy downhash: qqq "
            "hash 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08 "
            f"用户 {USERNAME} uid={UID} 外链 https://example.org/page"
        )
        out = sanitizer.clean_text(text)
        assert_clean(self, out)
        self.assertIn(f"[点我]({SITE}/details.php?id=12)", out)
        self.assertIn("种子", out)  # 链接被删，文字保留
        self.assertNotIn("torrents.php", out)  # 站内非详情页链接也删除
        self.assertIn(f"{SITE}/details.php?id=5", out)
        self.assertIn("https://example.org/page", out)
        for word in ("authkey", "torrent_pass", "downhash"):
            self.assertNotIn(word, out)


class RequestPolicyTest(_Case):
    def test_normalize_site(self) -> None:
        self.assertEqual(PT.normalize_site("https://PT.Example.com/index.php?x=1"), SITE)
        self.assertEqual(PT.normalize_site("pt.example.com"), SITE)
        self.assertEqual(PT.normalize_site("https://pt.example.com:8443/"), "https://pt.example.com:8443")
        for bad in ("http://pt.example.com", "ftp://pt.example.com", "https://user:pw@pt.example.com", "https://localhost", ""):
            self.assertEqual(PT.normalize_site(bad), "", bad)

    def test_whitelist_rejects_methods_paths_hosts_and_params(self) -> None:
        ok = PT.build_search_url(SITE, "Dune 2024", category="401")
        PT.assert_request_allowed("GET", ok, SITE)
        self.assertIn("search=Dune%202024", ok)
        rejected = [
            ("POST", ok),
            ("PUT", ok),
            ("GET", f"{SITE}/details.php?id=1"),
            ("GET", f"{SITE}/download.php?id=1"),
            ("GET", f"{SITE}/torrentrss.php"),
            ("GET", f"{SITE}/thanks.php?id=1"),
            ("GET", f"{SITE}/takelogin.php"),
            ("GET", f"{SITE}/bookmark.php?torrentid=1"),
            ("GET", f"{SITE}/comment.php?action=add"),
            ("GET", f"{SITE}/special.php?search=x"),
            ("GET", f"{SITE}/torrents.php?search=x&passkey=abc"),
            ("GET", "https://evil.example.net/torrents.php?search=x"),
            ("GET", "https://sub.pt.example.com/torrents.php?search=x"),
            ("GET", "http://pt.example.com/torrents.php?search=x"),
            ("GET", "https://pt.example.com:444/torrents.php?search=x"),
        ]
        for method, url in rejected:
            with self.assertRaises(PT.PTPolicyError, msg=(method, url)):
                PT.assert_request_allowed(method, url, SITE)
        # 特别区只在启用时放行。
        PT.assert_request_allowed("GET", PT.build_search_url(SITE, "x", special=True), SITE, allow_special=True)

    def test_fetch_sends_cookie_only_via_get_and_detects_login_redirect(self) -> None:
        http = FakeHTTP([FakeResponse(302, headers={"Location": "login.php?returnto=torrents.php"})])
        with self.assertRaises(PT.PTLoginExpired) as caught:
            base.run(PT.fetch_list(http, SITE, PT.build_search_url(SITE, "x"), cookie=COOKIE))
        self.assertEqual(caught.exception.reason, "站点登录已失效")
        self.assertEqual(len(http.requests), 1)
        self.assertEqual(http.requests[0][1]["headers"]["Cookie"], COOKIE)
        self.assertNotIn("follow_redirects", http.requests[0][1])

    def test_fetch_login_page_and_takelogin_redirect(self) -> None:
        for response in (
            FakeResponse(200, LOGIN_HTML),
            FakeResponse(302, headers={"location": f"{SITE}/takelogin.php"}),
            FakeResponse(403, LOGIN_HTML),
        ):
            with self.assertRaises(PT.PTLoginExpired):
                base.run(PT.fetch_list(FakeHTTP([response]), SITE, PT.build_search_url(SITE, "x"), cookie=COOKIE))

    def test_fetch_refuses_cross_origin_and_off_whitelist_redirects(self) -> None:
        http = FakeHTTP([FakeResponse(301, headers={"Location": "https://mirror.example.org/torrents.php?search=x"})])
        with self.assertRaises(PT.PTPolicyError):
            base.run(PT.fetch_list(http, SITE, PT.build_search_url(SITE, "x"), cookie=COOKIE))
        self.assertEqual(len(http.requests), 1)  # 没有跟随到其它域名
        http = FakeHTTP([FakeResponse(302, headers={"Location": "/details.php?id=1"})])
        with self.assertRaises(PT.PTPolicyError):
            base.run(PT.fetch_list(http, SITE, PT.build_search_url(SITE, "x"), cookie=COOKIE))
        self.assertEqual(len(http.requests), 1)

    def test_fetch_follows_same_origin_whitelisted_redirect(self) -> None:
        http = FakeHTTP([FakeResponse(302, headers={"Location": "/torrents.php?search=x&incldead=0"}), FakeResponse(200, LIST_HTML)])
        html = base.run(PT.fetch_list(http, SITE, PT.build_search_url(SITE, "x"), cookie=COOKIE))
        self.assertIn("torrents", html)
        self.assertEqual(len(http.requests), 2)

    def test_errors_never_contain_cookie(self) -> None:
        class Boom:
            async def get(self, url, **kwargs):
                raise RuntimeError(f"connect failed headers={kwargs}")

        with self.assertRaises(PT.PTError) as caught:
            base.run(PT.fetch_list(Boom(), SITE, PT.build_search_url(SITE, "x"), cookie=COOKIE))
        self.assertNotIn("CookieSecretValue9876", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)


class PluginFlowTest(_Case):
    def test_whitelisted_user_gets_pt_data_and_output_is_sanitized(self) -> None:
        leaky = (
            f"有的：**Dune Part Two**（61.25 GB，做种 128）[详情]({SITE}/details.php?id=1001)\n"
            f"下载：{SITE}/download.php?id=1001&passkey=0123456789abcdef0123456789abcdef\n"
            f"磁力 magnet:?xt=urn:btih:deadbeef 由 {USERNAME}（uid={UID}）登录查询，Cookie {COOKIE}"
        )
        ai = RouterAI(answer=leaky)
        ctx = make_ctx(ai=ai)
        plugin = PLUGIN.InteractionAIChatPlugin()
        actions = base.run(plugin.on_event(ctx, private("沙丘2 有种吗？多大？")))
        self.assertEqual(len(ai.router_calls), 1)
        self.assertEqual(len(ai.answer_calls), 1)
        answer_call = ai.answer_calls[0]
        self.assertIn("PT 站点只读搜索结果", answer_call["user"])
        self.assertIn(f"{SITE}/details.php?id=1001", answer_call["user"])
        self.assertIn("【PT 站点数据】", answer_call["system"])
        assert_clean(self, answer_call["user"])  # 第一重：送给模型前
        text = reply_text(actions)
        assert_clean(self, text)  # 第二重：发给用户前
        self.assertIn("details.php?id=1001", text)
        self.assertIn("61.25 GB", text)
        # 只发了 GET torrents.php，带 Cookie。
        self.assertEqual(len(ctx.http.requests), 1)
        url, kwargs = ctx.http.requests[0]
        self.assertTrue(url.startswith(f"{SITE}/torrents.php?search=Dune"))
        self.assertEqual(kwargs["headers"]["Cookie"], COOKIE)
        # 记忆里也不会存敏感内容。
        assert_clean(self, json.dumps(ctx.storage.data, ensure_ascii=False))
        # 日志不含 Cookie / 用户名，但记录了脱敏事件。
        logs = json.dumps(ctx.logs, ensure_ascii=False, default=str)
        self.assertNotIn("CookieSecretValue9876", logs)
        self.assertNotIn(USERNAME, logs)
        self.assertIn("PT 数据脱敏", logs)

    def test_router_decides_no_search(self) -> None:
        ai = RouterAI(route={"pt_search": False, "keyword": ""})
        ctx = make_ctx(ai=ai)
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("1+1 等于几")))
        self.assertEqual(ctx.http.requests, [])
        self.assertNotIn("PT 站点只读搜索结果", ai.answer_calls[0]["user"])

    def test_router_garbage_is_treated_as_no_search(self) -> None:
        ai = RouterAI(route="我觉得应该搜一下")
        ctx = make_ctx(ai=ai)
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("沙丘有种吗")))
        self.assertEqual(ctx.http.requests, [])
        self.assertEqual(len(ai.answer_calls), 1)

    def test_scope_whitelist_by_default(self) -> None:
        ai = RouterAI()
        ctx = make_ctx(ai=ai)
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("沙丘有种吗", user_id=999)))
        self.assertEqual(ai.router_calls, [])
        self.assertEqual(ctx.http.requests, [])
        # PT 专用白名单优先于答疑白名单。
        ai = RouterAI()
        ctx = make_ctx({"pt_allowed_user_ids": "999"}, ai=ai)
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("沙丘有种吗", user_id=999)))
        self.assertEqual(len(ctx.http.requests), 1)
        ai = RouterAI()
        ctx = make_ctx({"pt_allowed_user_ids": "999"}, ai=ai)
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("沙丘有种吗", user_id=601)))
        self.assertEqual(ctx.http.requests, [])

    def test_scope_all_and_guest_default_off(self) -> None:
        ctx = make_ctx({"pt_access_scope": "所有可用答疑的人"})
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("沙丘有种吗", user_id=999)))
        self.assertEqual(len(ctx.http.requests), 1)
        ctx = make_ctx({"pt_access_scope": "所有可用答疑的人"})
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, base.guest_payload("沙丘有种吗", user_id=601)))
        self.assertEqual(ctx.http.requests, [])
        ctx = make_ctx({"pt_access_scope": "所有可用答疑的人", "pt_allow_guest": True})
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, base.guest_payload("沙丘有种吗", user_id=601)))
        self.assertEqual(len(ctx.http.requests), 1)

    def test_disabled_or_incomplete_config_never_requests(self) -> None:
        for extra in ({"pt_enabled": False}, {"pt_cookie": ""}, {"pt_site_url": "http://pt.example.com"}):
            ai = RouterAI()
            ctx = make_ctx(extra, ai=ai)
            base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("沙丘有种吗")))
            self.assertEqual(ctx.http.requests, [], extra)
            self.assertEqual(ai.router_calls, [], extra)

    def test_login_expired_notice_and_no_auto_login(self) -> None:
        http = FakeHTTP([FakeResponse(302, headers={"Location": "login.php"})])
        ai = RouterAI(answer="不确定。")
        ctx = make_ctx(ai=ai, http=http)
        actions = base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("沙丘有种吗")))
        text = reply_text(actions)
        self.assertIn("PT 站点登录已失效", text)
        self.assertIn("不确定。", text)
        self.assertEqual(len(http.requests), 1)  # 没有再去请求登录页或 takelogin
        logs = json.dumps(ctx.logs, ensure_ascii=False, default=str)
        self.assertIn("站点登录已失效", logs)
        self.assertNotIn("CookieSecretValue9876", logs)

    def test_rate_limit_and_cache(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = make_ctx({"pt_rate_limit_per_minute": 1, "pt_cache_seconds": 0})
        base.run(plugin.on_event(ctx, private("沙丘有种吗")))
        actions = base.run(plugin.on_event(ctx, private("沙丘还有种吗")))
        self.assertEqual(len(ctx.http.requests), 1)
        self.assertIn("PT 查询太频繁了（每分钟最多 1 次）", reply_text(actions))

        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = make_ctx({"pt_rate_limit_per_minute": 1, "pt_cache_seconds": 300})
        base.run(plugin.on_event(ctx, private("沙丘有种吗")))
        actions = base.run(plugin.on_event(ctx, private("沙丘还有种吗")))
        self.assertEqual(len(ctx.http.requests), 1)  # 第二次命中缓存，不计限速
        self.assertNotIn("太频繁", reply_text(actions))
        self.assertIn("PT 站点只读搜索结果", ctx.ai.answer_calls[-1]["user"])
        cached = [value for key, value in ctx.storage.data.items() if key.startswith("pt:cache:")]
        self.assertEqual(len(cached), 1)
        self.assertEqual(ctx.storage.ttls[next(k for k in ctx.storage.data if k.startswith("pt:cache:"))], 300)
        assert_clean(self, json.dumps(cached, ensure_ascii=False))

    def test_site_failure_notice(self) -> None:
        http = FakeHTTP([FakeResponse(503, "cloudflare")])
        ctx = make_ctx(http=http)
        actions = base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("沙丘有种吗")))
        self.assertIn("PT 站点暂时无法查询（站点暂时无法访问）", reply_text(actions))

    def test_answer_sanitized_even_without_search(self) -> None:
        ai = RouterAI(route={"pt_search": False}, answer=f"试试 {SITE}/download.php?id=9&passkey=abc 或 magnet:?xt=urn:btih:abc")
        ctx = make_ctx(ai=ai)
        actions = base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("怎么下载")))
        assert_clean(self, reply_text(actions))

    def test_config_action_test_pt(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = make_ctx()
        result = base.run(plugin.on_config_action(ctx, "test_pt", {"config": {"pt_cookie": "***"}, "input": {"keyword": "Dune"}}))
        self.assertTrue(result["result"]["ok"], result)
        self.assertEqual(result["result"]["count"], 2)
        self.assertIn("连接正常", result["message"])
        assert_clean(self, json.dumps(result, ensure_ascii=False))
        self.assertEqual(ctx.http.requests[0][1]["headers"]["Cookie"], COOKIE)  # 遮罩值不会覆盖已保存 Cookie

        ctx = make_ctx(http=FakeHTTP([FakeResponse(200, LOGIN_HTML)]))
        result = base.run(plugin.on_config_action(ctx, "test_pt", {"config": {}, "input": {}}))
        self.assertFalse(result["result"]["ok"])
        self.assertIn("站点登录已失效", result["message"])

        ctx = make_ctx({"pt_enabled": False})
        result = base.run(plugin.on_config_action(ctx, "test_pt", {"config": {}, "input": {}}))
        self.assertEqual(result["result"]["error"], "disabled")


class ManifestPTTest(_Case):
    def test_pt_fields_are_optional_and_grouped(self) -> None:
        for schema in (PACKAGE.MANIFEST.config_schema, json.loads((base.PLUGIN_DIR / "plugin.json").read_text("utf-8"))["config_schema"]):
            props = schema["properties"]
            pt_keys = [key for key in props if key.startswith("pt_")]
            self.assertTrue(
                {
                    "pt_enabled",
                    "pt_site_url",
                    "pt_cookie",
                    "pt_user_agent",
                    "pt_access_scope",
                    "pt_allow_guest",
                    "pt_rate_limit_per_minute",
                    "pt_cache_seconds",
                    "pt_max_results",
                }
                <= set(pt_keys)
            )
            self.assertFalse(set(pt_keys) & set(schema["required"]))
            for key in pt_keys:
                if not key.endswith("_template"):
                    self.assertEqual(props[key]["x-ui-section"], "PT 站点数据源", key)
            self.assertIs(props["pt_enabled"]["default"], False)
            self.assertEqual(props["pt_cookie"]["format"], "password")
            self.assertIs(props["pt_cookie"]["x-sensitive"], True)
            self.assertEqual(props["pt_access_scope"]["default"], "仅白名单用户")
            self.assertIs(props["pt_allow_guest"]["default"], False)

    def test_permissions_and_version(self) -> None:
        manifest = PACKAGE.MANIFEST
        raw = json.loads((base.PLUGIN_DIR / "plugin.json").read_text("utf-8"))
        self.assertIn("external_http", manifest.permissions)
        self.assertEqual(raw["allowed_hosts"], manifest.allowed_hosts)
        self.assertEqual(raw["version"], "0.3.1")
        self.assertEqual(manifest.version, "0.3.1")
        self.assertIn("test_pt", [action["key"] for action in manifest.config_actions])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
