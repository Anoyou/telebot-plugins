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
TH = base.TH

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
        self.assertEqual(raw["version"], "0.4.3")
        self.assertEqual(manifest.version, "0.4.3")
        self.assertIn("test_pt", [action["key"] for action in manifest.config_actions])


class PTAccessDiagnosticsTest(_Case):
    """0.3.0 实测问题：访客模式里问“在我配置的站点里搜索…”时 PT 被静默跳过，模型回答“没有访问站点的能力”。"""

    QUESTION = "在我配置的站点里搜索『青蛙』有哪些资源"

    def test_guest_without_permission_explains_reason_instead_of_denying_capability(self) -> None:
        ai = RouterAI(answer="当前不能查站。")
        ctx = make_ctx({"pt_access_scope": "所有可用答疑的人"}, ai=ai)
        actions = base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, base.guest_payload(self.QUESTION, user_id=601)))
        self.assertEqual(ctx.http.requests, [])  # 不允许就不请求站点
        system = ai.answer_calls[0]["system"]
        self.assertIn("管理员已配置 PT 站点数据源，但当前因访客模式下未开启 PT 查询", system)
        self.assertIn("不要说自己没有这个能力", system)
        self.assertNotIn("PT 站点只读搜索结果", ai.answer_calls[0]["user"])
        text = reply_text(actions)
        self.assertIn("PT 站点暂时无法查询（访客模式下未开启 PT 查询", text)
        logs = json.dumps(ctx.logs, ensure_ascii=False, default=str)
        self.assertIn("PT 查询已跳过：访客模式下未开启 PT 查询", logs)
        self.assertNotIn("CookieSecretValue9876", logs)
        self.assertNotIn(SITE, logs)

    def test_reasons_distinguish_whitelists(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        cases = [
            ({"allowed_user_ids": "", "pt_allowed_user_ids": ""}, 601, "都为空"),
            ({"pt_allowed_user_ids": "999"}, 601, "提问者不在PT 白名单中"),
            ({}, 999, "提问者不在答疑白名单（PT 白名单留空时沿用）中"),
            ({"pt_enabled": False}, 601, "PT 站点数据源未启用"),
            ({"pt_cookie": ""}, 601, "未填写站点 Cookie"),
            ({"pt_site_url": "http://pt.example.com"}, 601, "站点地址未填写或不合法"),
        ]
        for extra, user_id, reason in cases:
            cfg = PLUGIN._cfg(make_ctx(extra))
            trigger = PLUGIN._Trigger(kind="private", user_id=user_id)
            allowed, why = plugin._pt_permitted(cfg, trigger)
            self.assertFalse(allowed, extra)
            self.assertIn(reason, why, extra)
        cfg = PLUGIN._cfg(make_ctx())
        self.assertEqual(plugin._pt_permitted(cfg, PLUGIN._Trigger(kind="private", user_id=601)), (True, ""))
        # 开关关闭且没填站点：PT 根本没在用，不记录原因，避免日志刷屏。
        cfg = PLUGIN._cfg(make_ctx({"pt_enabled": False, "pt_site_url": ""}))
        self.assertEqual(plugin._pt_permitted(cfg, PLUGIN._Trigger(kind="private", user_id=601)), (False, ""))

    def test_blocked_non_pt_question_runs_router_but_adds_nothing(self) -> None:
        ai = RouterAI(route={"pt_search": False})
        ctx = make_ctx({"allowed_user_ids": "", "pt_allowed_user_ids": "", "access_mode": "所有人"}, ai=ai)
        actions = base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("1+1 等于几")))
        self.assertEqual(len(ai.router_calls), 1)  # ready 时路由照常跑
        self.assertNotIn("【PT 站点数据源】", ai.answer_calls[0]["system"])
        self.assertNotIn("PT 站点暂时无法查询", reply_text(actions))
        self.assertTrue(any("都为空" in item[1] for item in ctx.logs))

    def test_blocked_router_positive_injects_reason(self) -> None:
        ai = RouterAI(route={"pt_search": True, "keyword": "Dune"})
        ctx = make_ctx({"pt_allowed_user_ids": "999"}, ai=ai)
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("沙丘第二部怎么样")))
        self.assertEqual(ctx.http.requests, [])
        self.assertIn("当前因提问者不在PT 白名单中", ai.answer_calls[0]["system"])

    def test_allowed_prompt_states_capability_and_zero_results(self) -> None:
        ai = RouterAI(route={"pt_search": True, "keyword": "不存在的作品"})
        ctx = make_ctx(ai=ai, http=FakeHTTP([FakeResponse(200, EMPTY_HTML)]))
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("不存在的作品有种吗")))
        call = ai.answer_calls[0]
        self.assertIn("你可以访问管理员配置的 PT 站点", call["system"])
        self.assertIn("不要说自己没有访问该站点的能力", call["system"])
        self.assertIn("0 条时明确告诉用户站内没有搜到", call["system"])
        self.assertIn("没有找到种子", call["user"])

    def test_site_alias_is_recognised_and_not_used_as_keyword(self) -> None:
        ai = RouterAI(route={"pt_search": True, "keyword": "青蛙"})
        ctx = make_ctx({"pt_site_name": "青蛙, 蛙站"}, ai=ai)
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private(self.QUESTION)))
        router_system = ai.router_calls[0]["system"]
        self.assertIn("“青蛙”", router_system)
        self.assertIn("“蛙站”", router_system)
        self.assertIn("站点名本身不是搜索关键词", router_system)
        url = ctx.http.requests[0][0]
        self.assertTrue(url.startswith(f"{SITE}/torrents.php?search=&"), url)  # 站点名不当关键词，返回最新列表
        self.assertIn("最新种子列表", ai.answer_calls[0]["user"])
        self.assertIn("（青蛙、蛙站", ai.answer_calls[0]["system"])

    def test_site_reference_without_router_keyword_searches_latest(self) -> None:
        ai = RouterAI(route={"pt_search": False})
        ctx = make_ctx({"pt_site_name": "青蛙"}, ai=ai)
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("青蛙站里有哪些资源")))
        self.assertEqual(len(ctx.http.requests), 1)
        ai = RouterAI(route={"pt_search": False})
        ctx = make_ctx(ai=ai)
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("我配置的 PT 站点最近有什么")))
        self.assertEqual(len(ctx.http.requests), 1)

    def test_site_names_from_domain_and_title(self) -> None:
        self.assertEqual(PLUGIN._pt_site_names("", "https://pt.kamept.com"), ["kamept"])
        self.assertEqual(PLUGIN._pt_site_names("青蛙，蛙站", "https://www.example.org"), ["青蛙", "蛙站", "example"])
        self.assertEqual(PLUGIN.pt_site_title("<title>青蛙 :: 种子 - Powered by NexusPHP</title>"), "青蛙")
        self.assertEqual(PLUGIN.pt_site_title("<title>NexusPHP</title>"), "")
        self.assertTrue(PLUGIN.refers_to_configured_site("青蛙站里有哪些资源", ["青蛙"]))
        self.assertTrue(PLUGIN.refers_to_configured_site("在我配置的站点里搜索", []))
        self.assertFalse(PLUGIN.refers_to_configured_site("今天天气", ["青蛙"]))
        self.assertTrue(PLUGIN.looks_like_pt_question("PT 上有沙丘吗"))
        self.assertFalse(PLUGIN.looks_like_pt_question("帮我写首诗"))
        self.assertEqual(PLUGIN.parse_pt_route('{"pt_search": true, "keyword": "青蛙站"}', site_names=["青蛙"]), ("", "", False))

    def test_generic_site_terms_recognised_without_config(self) -> None:
        # 群里用“站点 / 站里 / 本站 / PT 站”等泛称指代配置的站点（青蛙），不需要填写 pt_site_name。
        for term in ("站点", "站里", "站内", "站上", "本站", "咱站", "我们站", "PT站", "PT 站", "种子站"):
            self.assertIn(term, PLUGIN.PT_GENERIC_SITE_TERMS)
            question = f"{term}有没有 沙丘3"
            self.assertTrue(PLUGIN.looks_like_pt_question(question), question)
            self.assertTrue(PLUGIN.refers_to_configured_site(question), question)
        self.assertTrue(PLUGIN.refers_to_configured_site("站里最近有啥新资源"))
        self.assertTrue(PLUGIN.refers_to_configured_site("站上有沙丘吗"))
        self.assertFalse(PLUGIN.refers_to_configured_site("本站的 logo 挺好看"))
        self.assertFalse(PLUGIN.looks_like_pt_question("帮我写首诗"))

    def test_generic_terms_never_become_keyword(self) -> None:
        cases = {
            "站里 沙丘3": "沙丘3",
            "站里有没有 沙丘3": "沙丘3",
            "站里有没有沙丘3的资源吗": "沙丘3",
            "本站的沙丘3": "沙丘3",
            "PT 站 Dune 2021": "Dune 2021",
            "种子站": "",
            "站里": "",
            "咱站": "",
            "我配置的站点": "",
            "蛙站": "",
            "青蛙 PT": "",
            "在路上": "在路上",
            "热恋中": "热恋中",
        }
        for raw, expected in cases.items():
            route = json.dumps({"pt_search": True, "keyword": raw}, ensure_ascii=False)
            self.assertEqual(PLUGIN.parse_pt_route(route, site_names=["青蛙", "蛙站"]), (expected, "", False), raw)

    def test_router_prompt_lists_generic_terms_and_wiki_exclusion(self) -> None:
        rule = PLUGIN.pt_router_site_rule([])
        for term in ("“站点”", "“站里”", "“站内”", "“本站”", "“咱站”", "“PT站”", "“种子站”"):
            self.assertIn(term, rule)
        self.assertIn("keyword 是“沙丘3”", rule)
        self.assertIn("keyword 留空", rule)
        self.assertIn("站务问题时不要调用", rule)
        self.assertIn("由 PT 搜索处理，不选", PLUGIN.WIKI_ROUTER_PROMPT)

    def test_generic_term_search_end_to_end(self) -> None:
        ai = RouterAI(route={"pt_search": True, "keyword": "站里 沙丘3"})
        ctx = make_ctx(ai=ai)
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("站里有没有 沙丘3")))
        self.assertIn("“站里”", ai.router_calls[0]["system"])
        url = ctx.http.requests[0][0]
        self.assertIn("search=%E6%B2%99%E4%B8%983&", url)  # 只搜“沙丘3”
        # 判断器漏判时，“站里最近有啥新资源”按最新列表兜底查一次。
        ai = RouterAI(route={"pt_search": False})
        ctx = make_ctx(ai=ai)
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("站里最近有啥新资源")))
        self.assertEqual(len(ctx.http.requests), 1)
        self.assertTrue(ctx.http.requests[0][0].startswith(f"{SITE}/torrents.php?search=&"))

    def test_site_rule_questions_go_to_wiki_not_pt(self) -> None:
        for question in ("站点规则是什么", "站里规定能不能用盒子", "本站魔力怎么算", "咱站保种区怎么进", "站内新手要注意什么", "站里 H&R 考核怎么算", "PT 站怎么发种"):
            self.assertTrue(PLUGIN.is_site_wiki_question(question), question)
            self.assertFalse(PLUGIN.looks_like_pt_question(question), question)
            self.assertFalse(PLUGIN.refers_to_configured_site(question), question)
            # 判断器即使误判为要查，也不请求站点、不调用判断器。
            ai = RouterAI(route={"pt_search": True, "keyword": "规则"})
            ctx = make_ctx(ai=ai)
            base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private(question)))
            self.assertEqual(ai.router_calls, [], question)
            self.assertEqual(ctx.http.requests, [], question)
        # 带明确资源意图的仍走 PT。
        for question in ("站里有什么保种的资源", "站里有没有 沙丘3", "本站最新的 4K 电影", "种子站有没有新番"):
            self.assertFalse(PLUGIN.is_site_wiki_question(question), question)
            self.assertTrue(PLUGIN.looks_like_pt_question(question), question)

    def test_site_name_field_wording(self) -> None:
        props = PACKAGE.MANIFEST.config_schema["properties"]
        field = props["pt_site_name"]
        self.assertEqual(field["title"], "站点叫法（可选）")
        self.assertIn("青蛙, 蛙站", field["description"])
        self.assertIn("已默认识别", field["description"])
        self.assertIn("无需填写", field["description"])
        self.assertNotIn("空枪", field["description"])
        raw = json.loads((base.PLUGIN_DIR / "plugin.json").read_text("utf-8"))
        self.assertEqual(raw["config_schema"]["properties"]["pt_site_name"], {k: v for k, v in field.items()})

    def test_config_action_shows_scope_diagnostics(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = make_ctx()
        result = base.run(plugin.on_config_action(ctx, "test_pt", {"config": {}, "input": {}}))
        self.assertIn("访客模式：不允许查站", result["message"])
        self.assertIn("答疑白名单（PT 白名单留空时沿用）（1 人：601）", result["message"])
        scope = result["result"]["scope"]
        self.assertEqual(scope["whitelist"], [601])
        self.assertFalse(scope["allow_guest"])
        assert_clean(self, json.dumps(result, ensure_ascii=False))
        ctx = make_ctx({"allowed_user_ids": "", "pt_allow_guest": True, "access_mode": "所有人"})
        result = base.run(plugin.on_config_action(ctx, "test_pt", {"config": {}, "input": {}}))
        self.assertIn("都为空——目前没有任何人能触发查站", result["message"])
        self.assertIn("访客模式：允许查站", result["message"])
        ctx = make_ctx(http=FakeHTTP([FakeResponse(200, LOGIN_HTML)]))
        result = base.run(plugin.on_config_action(ctx, "test_pt", {"config": {}, "input": {}}))
        self.assertIn("可用范围", result["message"])

    def test_site_name_field_is_optional(self) -> None:
        for schema in (PACKAGE.MANIFEST.config_schema, json.loads((base.PLUGIN_DIR / "plugin.json").read_text("utf-8"))["config_schema"]):
            self.assertIn("pt_site_name", schema["properties"])
            self.assertNotIn("pt_site_name", schema.get("required", []))
            self.assertEqual(schema["properties"]["pt_site_name"]["x-ui-section"], "PT 站点数据源")


class PTChatScopeAndFormatTest(_Case):
    def test_allowed_chat_ids_limit_search_to_specific_groups(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        cfg = PLUGIN._cfg(make_ctx({"pt_allowed_chat_ids": "-100777\n-100888", "pt_access_scope": "所有可用答疑的人"}))
        self.assertEqual(plugin._pt_permitted(cfg, PLUGIN._Trigger(kind="group", chat_id=-100777, user_id=1)), (True, ""))
        allowed, why = plugin._pt_permitted(cfg, PLUGIN._Trigger(kind="group", chat_id=-100999, user_id=1))
        self.assertFalse(allowed)
        self.assertIn("当前群未开启 PT 搜索", why)
        allowed, why = plugin._pt_permitted(cfg, PLUGIN._Trigger(kind="private", chat_id=601, user_id=601))
        self.assertIn("当前私聊未开启 PT 搜索", why)
        # 留空 = 不限群。
        cfg = PLUGIN._cfg(make_ctx({"pt_access_scope": "所有可用答疑的人"}))
        self.assertTrue(plugin._pt_permitted(cfg, PLUGIN._Trigger(kind="group", chat_id=-100999, user_id=1))[0])

    def test_group_not_in_list_does_not_request_and_explains(self) -> None:
        ai = RouterAI()
        ctx = make_ctx({"pt_allowed_chat_ids": "-100888", "bot_username": "helper_bot"}, ai=ai)
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, base.message_payload("@helper_bot 沙丘有种吗", user_id=601)))
        self.assertEqual(ctx.http.requests, [])
        self.assertIn("当前因当前群未开启 PT 搜索", ai.answer_calls[0]["system"])
        self.assertTrue(any("PT 查询已跳过：当前群未开启 PT 搜索" in item[1] for item in ctx.logs))
        ai = RouterAI()
        ctx = make_ctx({"pt_allowed_chat_ids": "-100777", "bot_username": "helper_bot"}, ai=ai)
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, base.message_payload("@helper_bot 沙丘有种吗", user_id=601)))
        self.assertEqual(len(ctx.http.requests), 1)

    def test_results_sorted_by_seeders_with_table_format_hint(self) -> None:
        swapped = LIST_HTML
        ai = RouterAI()
        ctx = make_ctx(ai=ai, http=FakeHTTP([FakeResponse(200, swapped)]))
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("沙丘有种吗")))
        user = ai.answer_calls[0]["user"]
        self.assertIn("已按做种数从高到低排序", user)
        self.assertIn("列依次为：标题、大小、做种/下载、优惠、发布时间。", user)
        self.assertIn(f"标题链接：**[Dune Part Two 2024 2160p UHD BluRay HEVC-]({SITE}/details.php?id=1001)**", user)
        self.assertNotIn("详情页：", user)
        self.assertNotIn("[详情](链接)；", user)
        self.assertLess(user.index("details.php?id=1001"), user.index("details.php?id=1002"))  # 128 > 45
        items = [{"id": 1, "seeders": 3}, {"id": 2, "seeders": None}, {"id": 3, "seeders": 10}]
        self.assertEqual([item["id"] for item in PT.sort_by_seeders(items)], [3, 1, 2])

    def test_zero_results_have_no_table_hint(self) -> None:
        ai = RouterAI()
        ctx = make_ctx(ai=ai, http=FakeHTTP([FakeResponse(200, EMPTY_HTML)]))
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("沙丘有种吗")))
        self.assertNotIn("建议回答格式", ai.answer_calls[0]["user"])

    def test_hide_categories_filters_items(self) -> None:
        ai = RouterAI()
        ctx = make_ctx({"pt_hide_categories": "电影, 成人"}, ai=ai)
        base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("沙丘有种吗")))
        user = ai.answer_calls[0]["user"]
        self.assertNotIn("details.php?id=1001", user)  # 分类“电影/Movies”被隐藏
        self.assertTrue(any("隐藏分类" in item[1] for item in ctx.logs))
        self.assertEqual(PLUGIN.pt_filter_hidden([{"category": "成人/XXX"}, {"category": "剧集"}], ["成人"]), [{"category": "剧集"}])
        self.assertEqual(PLUGIN._cfg(make_ctx())["pt"]["hide_categories"], [])

    def test_diagnostics_include_chats_and_hidden_categories(self) -> None:
        plugin = PLUGIN.InteractionAIChatPlugin()
        ctx = make_ctx({"pt_allowed_chat_ids": "-100777", "pt_hide_categories": "成人"})
        result = base.run(plugin.on_config_action(ctx, "test_pt", {"config": {}, "input": {}}))
        self.assertIn("允许的群：1 个（-100777）", result["message"])
        self.assertIn("隐藏分类：成人", result["message"])
        self.assertEqual(result["result"]["scope"]["allowed_chat_ids"], [-100777])

    def test_new_fields_are_optional_and_grouped(self) -> None:
        for schema in (PACKAGE.MANIFEST.config_schema, json.loads((base.PLUGIN_DIR / "plugin.json").read_text("utf-8"))["config_schema"]):
            for key in ("pt_allowed_chat_ids", "pt_hide_categories"):
                self.assertEqual(schema["properties"][key]["x-ui-section"], "PT 站点数据源")
                self.assertEqual(schema["properties"][key]["default"], "")
                self.assertNotIn(key, schema.get("required", []))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class BoldTitleLinkTest(_Case):
    """0.4.3：PT 结果标题加粗且本身就是详情页链接，不再单独放“详情页”列。"""

    def test_title_link_markdown_escapes_breaking_characters(self) -> None:
        out = PT.title_link_markdown("[Kong Qiang] 2026 | 2160p `WEB-DL` *HDR*", f"{SITE}/details.php?id=123")
        self.assertEqual(out, f"**[（Kong Qiang） 2026 / 2160p 'WEB-DL' ＊HDR＊]({SITE}/details.php?id=123)**")

    def test_bold_title_link_renders_in_html_fallback_and_rich(self) -> None:
        md = (
            "站里有 1 条。\n\n| 标题 | 大小 | 做种/下载 | 优惠 | 发布时间 |\n|---|---|---|---|---|\n"
            f"| **[Kong Qiang 2026 2160p WEB-DL `x264`]({SITE}/details.php?id=123)** | 20 GB | 50/2 | Free | 2026-10-20 |\n"
        )
        html_out = TH.markdown_to_telegram_html(md)
        self.assertIn(f'• <b><a href="{SITE}/details.php?id=123">Kong Qiang 2026 2160p WEB-DL x264</a></b> — 大小：20 GB', html_out)
        self.assertNotIn("<b><b>", html_out)
        self.assertNotIn("<code>", html_out)  # 链接文字里的行内代码降级为普通文字（code 与链接互不包含）
        rich = TH.markdown_to_rich_markdown(md)
        self.assertIn(f"| **[Kong Qiang 2026 2160p WEB-DL `x264`]({SITE}/details.php?id=123)** | 20 GB |", rich)
        para = TH.markdown_to_telegram_html(f"推荐 **[沙丘2]({SITE}/details.php?id=1001)**")
        self.assertIn(f'<b><a href="{SITE}/details.php?id=1001">沙丘2</a></b>', para)

    def test_output_sanitizer_keeps_only_details_links_in_bold_titles(self) -> None:
        answer = (
            "| 标题 | 大小 |\n|---|---|\n"
            f"| **[沙丘2]({SITE}/details.php?id=1001)** | 61 GB |\n"
            f"| **[沙丘1]({SITE}/download.php?id=1000&passkey=0123456789abcdef0123456789abcdef)** | 40 GB |\n"
            f"| **[沙丘列表]({SITE}/torrents.php?search=dune)** | - |\n"
        )
        ai = RouterAI(answer=answer)
        ctx = make_ctx(ai=ai, config={"rich_text": True})
        actions = base.run(PLUGIN.InteractionAIChatPlugin().on_event(ctx, private("沙丘有种吗")))
        text = reply_text(actions)
        self.assertIn(f'<b><a href="{SITE}/details.php?id=1001">沙丘2</a></b>', text)
        self.assertNotIn("download.php", text)
        self.assertNotIn("passkey", text)
        self.assertNotIn("torrents.php", text)
        self.assertIn("<b>沙丘1</b>", text)  # 非详情页链接被去掉，只留加粗标题文字

