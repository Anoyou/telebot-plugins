from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_module():
    app_module = types.ModuleType("app")
    worker_module = types.ModuleType("app.worker")
    plugins_module = types.ModuleType("app.worker.plugins")
    base_module = types.ModuleType("app.worker.plugins.base")
    events_module = types.ModuleType("app.worker.plugins.events")

    class Plugin:
        pass

    class PluginContext:
        def __init__(self, account_id=1, feature_key="quick_qa", log=None, config=None, redis=None):
            self.account_id = account_id
            self.feature_key = feature_key
            self.log = log
            self.config = config or {}
            self.messages = None
            self.http = None
            self.ai = None
            self.redis = redis

    def register(cls):
        return cls

    def event_from_interaction_payload(payload):
        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        actor = payload.get("actor") if isinstance(payload.get("actor"), dict) else {}
        event_type = str(source.get("type") or event.get("type") or payload.get("event_type") or "")
        chat_id = message.get("chat_id") or source.get("chat_id") or event.get("chat_id") or payload.get("chat_id")
        message_id = message.get("message_id") or source.get("message_id") or event.get("message_id") or payload.get("message_id")
        text = message.get("text") or payload.get("message_text") or payload.get("text") or source.get("text") or event.get("text") or ""
        return types.SimpleNamespace(
            type=event_type,
            message=types.SimpleNamespace(chat_id=chat_id, message_id=message_id, text=text),
            sender=types.SimpleNamespace(
                user_id=actor.get("user_id") or payload.get("sender_user_id"),
                display_name=actor.get("display_name") or payload.get("sender_name") or "玩家",
            ),
            callback=types.SimpleNamespace(
                id=source.get("callback_query_id") or event.get("callback_query_id") or payload.get("callback_query_id"),
                data=source.get("callback_data") or event.get("callback_data") or payload.get("callback_data") or "",
            ),
        )

    base_module.Plugin = Plugin
    base_module.PluginContext = PluginContext
    base_module.register = register
    events_module.event_from_interaction_payload = event_from_interaction_payload
    sys.modules.setdefault("app", app_module)
    sys.modules.setdefault("app.worker", worker_module)
    sys.modules.setdefault("app.worker.plugins", plugins_module)
    sys.modules["app.worker.plugins.base"] = base_module
    sys.modules["app.worker.plugins.events"] = events_module

    spec = importlib.util.spec_from_file_location(
        "quick_qa_plugin_under_test",
        ROOT / "quick_qa" / "plugin.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, PluginContext


plugin_module, PluginContext = _load_plugin_module()


def payment_payload(user_id: int, name: str, amount: int = 100) -> dict:
    return {
        "event": {"type": "payment_confirmed", "chat_id": -100123, "message_id": user_id},
        "source": {"type": "payment_confirmed", "chat_id": -100123, "message_id": user_id},
        "actor": {"user_id": 999, "display_name": "通知Bot"},
        "payment": {"amount": amount, "payer": {"user_id": user_id, "display_name": name}},
        "payer_user_id": user_id,
        "payer_name": name,
        "amount": amount,
        "chat_id": -100123,
        "message_id": user_id,
    }


def callback_payload(data: str, user_id: int, name: str, message_id: int = 500) -> dict:
    return {
        "event": {
            "type": "callback_query",
            "chat_id": -100123,
            "message_id": message_id,
            "callback_query_id": f"cb-{user_id}-{message_id}",
            "callback_data": data,
        },
        "source": {
            "type": "callback_query",
            "chat_id": -100123,
            "message_id": message_id,
            "callback_query_id": f"cb-{user_id}-{message_id}",
            "callback_data": data,
        },
        "actor": {"user_id": user_id, "display_name": name},
        "callback_query_id": f"cb-{user_id}-{message_id}",
        "callback_data": data,
        "chat_id": -100123,
        "message_id": message_id,
    }


def message_payload(text: str, user_id: int, name: str, *, channel: str = "interaction_bot", message_id: int = 700) -> dict:
    return {
        "event": {"type": "message", "chat_id": -100123, "message_id": message_id},
        "source": {"type": "message", "channel": channel, "chat_id": -100123, "message_id": message_id},
        "message": {"chat_id": -100123, "message_id": message_id, "text": text},
        "actor": {"user_id": user_id, "display_name": name},
        "chat_id": -100123,
        "message_id": message_id,
    }


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)


class QuickQATest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_data_path = plugin_module.DATA_PATH
        plugin_module.DATA_PATH = Path(self._tmp.name) / "quickqa_data.json"
        self._old_choice = plugin_module.random.choice
        plugin_module.random.choice = lambda seq: list(seq)[0]

    def tearDown(self) -> None:
        plugin_module.random.choice = self._old_choice
        plugin_module.DATA_PATH = self._old_data_path
        self._tmp.cleanup()

    def _seed_kb(self) -> None:
        store = {
            "version": 1,
            "accounts": {
                "1": {
                    "drafts": {},
                    "knowledge_bases": [
                        {
                            "kb_id": "kb1",
                            "title": "测试题库",
                            "url": "https://example.com/a",
                            "summary": "测试",
                            "questions": [
                                {
                                    "question": "TelePilot 插件新主路径是什么？",
                                    "options": ["Event Bus", "直接 Bot API", "shell"],
                                    "answer_index": 0,
                                    "explanation": "新插件走 Event Bus + MessageOps。",
                                }
                            ],
                            "created_at": 1,
                        }
                    ],
                }
            },
        }
        plugin_module._save_store(store)

    def test_extract_json_and_validate_question(self) -> None:
        data = plugin_module._extract_json_object(
            "```json\n{\"questions\":[{\"question\":\"Q\",\"options\":[\"A\",\"B\",\"C\"],\"answer_index\":1}]}\n```"
        )
        question = plugin_module._question_from_dict(data["questions"][0])
        self.assertIsNotNone(question)
        self.assertEqual(question.answer_index, 1)

    def test_startup_uses_context_data_dir_and_copies_legacy_store(self) -> None:
        async def scenario() -> None:
            legacy_path = Path(self._tmp.name) / "legacy" / "quickqa_data.json"
            legacy_path.parent.mkdir(parents=True)
            legacy_path.write_text('{"version": 1, "accounts": {"1": {"drafts": {}, "knowledge_bases": []}}}', encoding="utf-8")
            data_dir = Path(self._tmp.name) / "persistent"
            ctx = PluginContext(account_id=1)
            ctx.data_dir = data_dir

            with patch.object(plugin_module, "LEGACY_DATA_PATH", legacy_path):
                plugin = plugin_module.QuickQAPlugin()
                await plugin.on_startup(ctx)

            target = data_dir / "quickqa_data.json"
            self.assertEqual(plugin._store_path(), target)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["version"], 1)

        asyncio.run(scenario())

    def test_draft_save_persists_knowledge_base(self) -> None:
        plugin = plugin_module.QuickQAPlugin()
        draft = {
            "draft_id": "d1",
            "kb_id": "kb-draft",
            "title": "草稿题库",
            "url": "https://example.com",
            "summary": "摘要",
            "questions": [
                {"question": "Q1", "options": ["A", "B", "C"], "answer_index": 0},
                {"question": "Q2", "options": ["A", "B", "C"], "answer_index": 1},
                {"question": "Q3", "options": ["A", "B", "C"], "answer_index": 2},
            ],
        }

        plugin._put_draft(1, draft)
        text = plugin._save_draft(1, "d1")
        kbs = plugin._available_kbs_for_account(1)

        self.assertIn("题库已保存", text)
        self.assertEqual(len(kbs), 1)
        self.assertEqual(kbs[0].kb_id, "kb-draft")

    def test_config_action_generates_knowledge_base_patch(self) -> None:
        class FakeHTTP:
            async def get(self, _url: str):
                return types.SimpleNamespace(
                    status_code=200,
                    text="<html><body><h1>TelePilot</h1>" + ("插件配置框架支持题库生成。" * 30) + "</body></html>",
                )

        class FakeAI:
            def __init__(self):
                self.calls = []

            async def complete(self, *_args, **_kwargs):
                self.calls.append(_kwargs)
                return types.SimpleNamespace(
                    text=(
                        '{"title":"配置框架","summary":"通用配置页动作",'
                        '"questions":['
                        '{"question":"配置页动作由谁声明？","options":["插件","数据库","主题"],"answer_index":0},'
                        '{"question":"题库来源是什么？","options":["URL","贴纸","头像"],"answer_index":0},'
                        '{"question":"答案按钮数量？","options":["三个","一个","五个"],"answer_index":0}'
                        ']}'
                    )
                )

        async def scenario() -> None:
            plugin = plugin_module.QuickQAPlugin()
            ctx = PluginContext(
                account_id=1,
                config={"allowed_source_hosts": "example.com"},
            )
            ctx.http = FakeHTTP()
            fake_ai = FakeAI()
            ctx.ai = fake_ai
            result = await plugin.on_config_action(
                ctx,
                "generate_knowledge_base",
                {
                    "input": {"url": "https://example.com/article", "title": "配置页"},
                    "config": {
                        "knowledge_bases": [
                            {
                                "kb_id": "old",
                                "title": "旧题库",
                                "enabled": False,
                                "questions": [
                                    {"question": "旧题", "options": ["A", "B", "C"], "answer_index": 0}
                                ],
                            }
                        ],
                        "ai_timeout_seconds": 90,
                    },
                },
            )
            items = result["config_patch"]["knowledge_bases"]
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0]["kb_id"], "old")
            self.assertFalse(items[0]["enabled"])
            self.assertEqual(items[1]["title"], "配置页")
            self.assertTrue(items[1]["enabled"])
            self.assertEqual(len(items[1]["questions"]), 3)
            self.assertEqual(fake_ai.calls[0]["timeout_seconds"], plugin_module.DEFAULT_AI_TIMEOUT_SECONDS)

        asyncio.run(scenario())

    def test_plugin_json_allows_legacy_ai_timeout_config(self) -> None:
        data = json.loads((ROOT / "quick_qa" / "plugin.json").read_text())
        properties = data["config_schema"]["properties"]
        timeout_schema = properties["ai_timeout_seconds"]
        entry_fee_schema = properties["entry_fee"]
        interaction_entry = data["interaction_entries"][0]

        self.assertLessEqual(timeout_schema["minimum"], 90)
        self.assertEqual(entry_fee_schema["minimum"], 0)
        self.assertTrue(properties["free_join_keyword"]["x-ui-hidden"])
        self.assertNotIn("message", interaction_entry["events"])
        self.assertIn("start_session", interaction_entry["result_contract"]["actions"])
        self.assertEqual(properties["reward_ratio"]["title"], "可发奖金比例")
        self.assertEqual(properties["settlement_base_amount"]["title"], "基础奖池单价 / 单人保底奖金")
        self.assertIn("单人奖金 =", properties["settlement_formula_preview"]["default"])
        self.assertEqual(
            plugin_module._ai_timeout_seconds({"ai_timeout_seconds": 90}),
            plugin_module.DEFAULT_AI_TIMEOUT_SECONDS,
        )

    def test_config_action_appends_and_deduplicates_existing_kb(self) -> None:
        class FakeHTTP:
            async def get(self, _url: str):
                return types.SimpleNamespace(
                    status_code=200,
                    text="<html><body>" + ("TelePilot 支持插件配置动作和题库生成。" * 40) + "</body></html>",
                )

        class FakeAI:
            async def complete(self, *_args, **_kwargs):
                return types.SimpleNamespace(
                    text=(
                        '{"title":"配置框架","summary":"增量题库",'
                        '"questions":['
                        '{"question":"旧题会重复吗？","options":["不会","会","不确定"],"answer_index":0},'
                        '{"question":"新增题来自哪里？","options":["URL正文","骰子","头像"],"answer_index":0},'
                        '{"question":"每题几个选项？","options":["三个","两个","四个"],"answer_index":0}'
                        ']}'
                    )
                )

        async def scenario() -> None:
            plugin = plugin_module.QuickQAPlugin()
            ctx = PluginContext(
                account_id=1,
                config={"allowed_source_hosts": "example.com", "ai_question_count": 10},
            )
            ctx.http = FakeHTTP()
            ctx.ai = FakeAI()
            result = await plugin.on_config_action(
                ctx,
                "generate_knowledge_base",
                {
                    "input": {"url": "https://example.com/article", "mode": "append", "target_total": 5},
                    "config": {
                        "knowledge_bases": [
                            {
                                "kb_id": "kb-existing",
                                "title": "配置框架",
                                "url": "https://example.com/article/",
                                "enabled": True,
                                "questions": [
                                    {"question": "旧题会重复吗？", "options": ["不会", "会", "不确定"], "answer_index": 0}
                                ],
                            }
                        ],
                    },
                },
            )
            items = result["config_patch"]["knowledge_bases"]
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["kb_id"], "kb-existing")
            self.assertEqual(len(items[0]["questions"]), 3)
            self.assertEqual(
                [item["question"] for item in items[0]["questions"]],
                ["旧题会重复吗？", "新增题来自哪里？", "每题几个选项？"],
            )

        asyncio.run(scenario())

    def test_interaction_bot_command_echo_is_ignored(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.QuickQAPlugin()
            ctx = PluginContext(account_id=1, config={"command": "qa"})
            await plugin.on_startup(ctx)
            try:
                actions = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    message_payload("qa 100 20", 111, "管理员"),
                )

                self.assertEqual(actions, [])
                self.assertNotIn(-100123, plugin._games)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_keyword_route_creates_lobby(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.QuickQAPlugin()
            ctx = PluginContext(account_id=1, config={"command": "qa", "entry_fee": 100, "start_keyword": "我要答题"})
            await plugin.on_startup(ctx)
            try:
                actions = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    message_payload("我要答题", 111, "玩家A"),
                )

                self.assertIn(-100123, plugin._games)
                self.assertTrue(any("快问快答报名中" in action.get("text", "") for action in actions))
                lobby_text = "\n".join(action.get("text", "") for action in actions)
                self.assertIn("三选一抢答", lobby_text)
                self.assertIn("答错扣分且本题不能再答", lobby_text)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_transfer_command_message_does_not_reopen_lobby(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.QuickQAPlugin()
            ctx = PluginContext(account_id=1, config={"command": "qa", "entry_fee": 100, "start_keyword": "我要答题"})
            await plugin.on_startup(ctx)
            try:
                await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    message_payload("我要答题", 111, "玩家A"),
                )

                actions = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    message_payload("+100", 111, "玩家A", message_id=701),
                )

                self.assertEqual(actions, [])
                self.assertIn(-100123, plugin._games)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_randomizes_options_and_preserves_correct_answer(self) -> None:
        original_shuffle = plugin_module.random.shuffle
        plugin_module.random.shuffle = lambda seq: seq.reverse()
        try:
            question = plugin_module.QAQuestion(
                question="正确答案原本在哪里？",
                options=["正确", "错误一", "错误二"],
                answer_index=0,
            )

            randomized = plugin_module._randomized_question_options(question)

            self.assertEqual(randomized.options, ["错误二", "错误一", "正确"])
            self.assertEqual(randomized.answer_index, 2)
        finally:
            plugin_module.random.shuffle = original_shuffle

    def test_dedupes_similar_questions_for_game_pool(self) -> None:
        questions = [
            plugin_module.QAQuestion(
                question="有疑问应先做什么？",
                options=["先查 Wiki", "直接私信管理", "随便猜"],
                answer_index=0,
            ),
            plugin_module.QAQuestion(
                question="有疑问时应先做什么？",
                options=["先查 Wiki", "直接私信管理", "随便猜"],
                answer_index=0,
            ),
            plugin_module.QAQuestion(
                question="每题有几个选项？",
                options=["三个", "两个", "四个"],
                answer_index=0,
            ),
        ]

        deduped = plugin_module._dedupe_questions(questions)

        self.assertEqual([item.question for item in deduped], ["有疑问应先做什么？", "每题有几个选项？"])

    def test_finish_message_explains_highest_score_settlement(self) -> None:
        plugin = plugin_module.QuickQAPlugin()
        winner = plugin_module.Player(user_id=111, name="玩家A", points=102, correct_count=28, wrong_count=1)
        game = plugin_module.QuickQAGame(
            game_id="g1",
            account_id=1,
            chat_id=-100123,
            entry_fee=100,
            initial_points=20,
            correct_points=3,
            wrong_points=5,
            reward_ratio=0.9,
            settlement_base_amount=1000,
            settlement_point_multiplier=666,
            settlement_base_points=4,
            cleanup_delay_seconds=120,
            payout_interval_seconds=2,
            min_players=2,
            max_players=30,
            max_questions=30,
            question_timeout_seconds=45,
            selection_timeout_seconds=120,
            host_user_id=111,
            host_name="玩家A",
            players={
                111: winner,
                222: plugin_module.Player(user_id=222, name="玩家B", points=26, correct_count=4, wrong_count=3),
            },
        )

        settlements = plugin._settlement_items(game)
        text = plugin._render_finish(game, settlements, "题库已用完，按积分余额结算", settlement_mode="auto")

        self.assertIn("本局题目已出完", text)
        self.assertIn("基础奖池：1000 × 2 = 2000", text)
        self.assertIn("可发奖金：2000 × 90% = 1800", text)
        self.assertIn("玩家A：102 分 → 66268", text)
        self.assertIn("玩家A：102 分（存活，答对 28，答错 1）", text)
        self.assertIn("玩家B：26 分（存活，答对 4，答错 3）", text)
        self.assertIn("发奖模式：自动发奖", text)

    def test_payment_without_lobby_is_ignored(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.QuickQAPlugin()
            ctx = PluginContext(account_id=1)
            await plugin.on_startup(ctx)
            try:
                actions = await plugin.on_interaction(ctx, "join_quick_qa", payment_payload(111, "玩家A"))

                self.assertEqual(actions, [])
                self.assertNotIn(-100123, plugin._games)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_free_join_button_edits_saved_lobby_and_syncs_participants(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.QuickQAPlugin()
            redis = FakeRedis()
            ctx = PluginContext(
                account_id=1,
                redis=redis,
                config={
                    "entry_fee": 0,
                    "min_players": 2,
                },
            )
            await plugin.on_startup(ctx)
            try:
                start_actions = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    message_payload("开始答题", 999, "主持人", message_id=700),
                )
                self.assertEqual(len(start_actions), 1)
                game = plugin._games[-100123]
                lobby_key = plugin._lobby_message_key(game)
                self.assertEqual(start_actions[0].get("save_message_id_key"), lobby_key)
                self.assertEqual(
                    start_actions[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"],
                    f"qqa:join:{game.game_id}",
                )
                ignored_text_join = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    message_payload("报名", 111, "玩家A", message_id=701),
                )
                self.assertEqual(ignored_text_join, [])
                self.assertEqual(game.players, {})
                redis.store[lobby_key] = "900"

                join_actions = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    callback_payload(f"qqa:join:{game.game_id}", 111, "玩家A", message_id=900),
                )

                self.assertEqual(
                    [action.get("type") for action in join_actions],
                    ["answer_callback", "start_session", "edit_message"],
                )
                self.assertEqual(join_actions[0].get("text"), "加入成功")
                self.assertEqual(join_actions[1].get("participant_user_ids"), [111])
                self.assertEqual(join_actions[1]["data"]["quick_qa"]["participant_user_ids"], [111])
                self.assertEqual(join_actions[2].get("message_id"), 900)
                self.assertEqual(join_actions[2].get("chat_id"), -100123)
                self.assertIn("玩家A：20 分", join_actions[2].get("text", ""))
                self.assertIsNone(game.players[111].join_message_id)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_free_join_button_uses_callback_lobby_message_without_redis_key(self) -> None:
        async def scenario() -> None:
            plugin = plugin_module.QuickQAPlugin()
            ctx = PluginContext(
                account_id=1,
                config={
                    "entry_fee": 0,
                    "min_players": 2,
                },
            )
            await plugin.on_startup(ctx)
            try:
                await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    message_payload("开始答题", 999, "主持人", message_id=700),
                )
                game = plugin._games[-100123]
                join_actions = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    callback_payload(f"qqa:join:{game.game_id}", 111, "玩家A", message_id=800),
                )

                self.assertEqual(
                    [action.get("type") for action in join_actions],
                    ["answer_callback", "start_session", "edit_message"],
                )
                self.assertEqual(join_actions[2].get("message_id"), 800)
                self.assertIn("快问快答报名中", join_actions[2].get("text", ""))
                self.assertIn("玩家A：20 分", join_actions[2].get("text", ""))
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_start_button_edits_lobby_and_ignores_repeated_clicks(self) -> None:
        self._seed_kb()

        async def scenario() -> None:
            plugin = plugin_module.QuickQAPlugin()
            ctx = PluginContext(
                account_id=1,
                config={
                    "entry_fee": 0,
                    "min_players": 2,
                    "selection_timeout_seconds": 300,
                },
            )
            await plugin.on_startup(ctx)
            try:
                await plugin.on_interaction(ctx, "join_quick_qa", message_payload("开始答题", 999, "主持人", message_id=700))
                game = plugin._games[-100123]
                await plugin.on_interaction(ctx, "join_quick_qa", callback_payload(f"qqa:join:{game.game_id}", 111, "玩家A", message_id=800))
                await plugin.on_interaction(ctx, "join_quick_qa", callback_payload(f"qqa:join:{game.game_id}", 222, "玩家B", message_id=800))

                first = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    callback_payload(f"qqa:start:{game.game_id}", 111, "玩家A", message_id=800),
                )
                self.assertEqual(game.phase, "selecting")
                self.assertEqual(game.selector_user_id, 111)
                self.assertTrue(any(action.get("type") == "edit_message" and "题库选择" in action.get("text", "") for action in first))
                self.assertTrue(any("【轮到 玩家A 选择题库】" in action.get("text", "") for action in first))
                self.assertFalse(any(action.get("type") == "send_message" and "题库选择" in action.get("text", "") for action in first))

                repeated = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    callback_payload(f"qqa:start:{game.game_id}", 222, "玩家B", message_id=801),
                )
                self.assertEqual(game.selector_user_id, 111)
                self.assertTrue(any("题库选择已经开始" in action.get("text", "") for action in repeated))
                self.assertFalse(any(action.get("type") in {"send_message", "edit_message"} for action in repeated))
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_free_game_closes_button_registration_after_questions_start(self) -> None:
        self._seed_kb()

        async def scenario() -> None:
            plugin = plugin_module.QuickQAPlugin()
            ctx = PluginContext(
                account_id=1,
                config={
                    "entry_fee": 0,
                    "min_players": 2,
                    "question_timeout_seconds": 300,
                    "selection_timeout_seconds": 300,
                    "max_questions_per_game": 1,
                    "payout_mode": "announce_only",
                },
            )
            await plugin.on_startup(ctx)
            try:
                await plugin.on_interaction(ctx, "join_quick_qa", message_payload("开始答题", 999, "主持人", message_id=700))
                game = plugin._games[-100123]
                await plugin.on_interaction(ctx, "join_quick_qa", callback_payload(f"qqa:join:{game.game_id}", 111, "玩家A", message_id=800))
                await plugin.on_interaction(ctx, "join_quick_qa", callback_payload(f"qqa:join:{game.game_id}", 222, "玩家B", message_id=800))
                self.assertEqual(game.entry_fee, 0)
                self.assertIsNone(game.players[111].join_message_id)

                await plugin.on_interaction(ctx, "join_quick_qa", callback_payload(f"qqa:start:{game.game_id}", 111, "玩家A"))
                await plugin.on_interaction(ctx, "join_quick_qa", callback_payload(f"qqa:go:{game.game_id}", 111, "玩家A"))
                late_join = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    callback_payload(f"qqa:join:{game.game_id}", 333, "玩家C", message_id=800),
                )

                self.assertNotIn(333, game.players)
                self.assertEqual(late_join[0].get("text"), "报名已经结束。")

                question_id = game.current_question.question_id
                blocked = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    callback_payload(f"qqa:ans:{game.game_id}:{question_id}:0", 444, "未报名", message_id=704),
                )
                self.assertTrue(any("还没有加入本局" in action.get("text", "") for action in blocked))
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())

    def test_auto_payout_uses_user_id_and_prefers_existing_message_anchor(self) -> None:
        class FakeMessages:
            def __init__(self):
                self.applied = []

            async def apply(self, actions, entry_key=None):
                self.applied.append({"actions": list(actions), "entry_key": entry_key})

        async def scenario() -> None:
            plugin = plugin_module.QuickQAPlugin()
            game = plugin_module.QuickQAGame(
                game_id="g1",
                account_id=1,
                chat_id=-100123,
                entry_fee=0,
                initial_points=20,
                correct_points=3,
                wrong_points=5,
                reward_ratio=0.9,
                settlement_base_amount=1000,
                settlement_point_multiplier=666,
                settlement_base_points=4,
                cleanup_delay_seconds=120,
                payout_interval_seconds=0,
                min_players=2,
                max_players=30,
                max_questions=30,
                question_timeout_seconds=45,
                selection_timeout_seconds=120,
                host_user_id=999,
                host_name="主持人",
                players={
                    111: plugin_module.Player(user_id=111, name="玩家A", points=5, join_message_id=701),
                    222: plugin_module.Player(user_id=222, name="玩家B", points=4, join_message_id=702),
                    333: plugin_module.Player(user_id=333, name="玩家C", points=0),
                    444: plugin_module.Player(user_id=444, name="玩家D", points=5),
                },
            )
            messages = FakeMessages()
            ctx = PluginContext(account_id=1)
            ctx.messages = messages

            await plugin._send_payouts(ctx, game, plugin._settlement_items(game))

            payout_actions = [
                action
                for batch in messages.applied
                for action in batch["actions"]
                if action.get("type") == "payout"
            ]
            by_user = {item["reply_to_user_id"]: item for item in payout_actions}
            self.assertEqual(set(by_user), {111, 222, 444})
            self.assertEqual(by_user[111]["reply_to_message_id"], 701)
            self.assertEqual(by_user[222]["reply_to_message_id"], 702)
            self.assertNotIn("reply_to_message_id", by_user[444])
            self.assertEqual(by_user[444]["reply_to_search_limit"], 200)

        asyncio.run(scenario())

    def test_paid_game_selects_kb_and_settles_after_answer(self) -> None:
        self._seed_kb()

        async def scenario() -> None:
            plugin = plugin_module.QuickQAPlugin()
            ctx = PluginContext(
                account_id=1,
                config={
                    "min_players": 2,
                    "question_timeout_seconds": 300,
                    "selection_timeout_seconds": 300,
                    "max_questions_per_game": 1,
                },
            )
            await plugin.on_startup(ctx)
            try:
                lobby_actions = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    message_payload("开始答题", 999, "主持人"),
                )
                self.assertFalse(
                    any(
                        button.get("callback_data", "").startswith("qqa:join:")
                        for row in lobby_actions[0]["reply_markup"]["inline_keyboard"]
                        for button in row
                    )
                )
                await plugin.on_interaction(ctx, "join_quick_qa", payment_payload(111, "玩家A"))
                await plugin.on_interaction(ctx, "join_quick_qa", payment_payload(222, "玩家B"))
                game = plugin._games[-100123]
                self.assertEqual(len(game.players), 2)

                actions = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    callback_payload(f"qqa:start:{game.game_id}", 111, "玩家A"),
                )
                self.assertTrue(any("题库选择" in action.get("text", "") for action in actions))
                self.assertEqual(game.selector_user_id, 111)

                actions = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    callback_payload(f"qqa:go:{game.game_id}", 111, "玩家A"),
                )
                self.assertTrue(any("TelePilot 插件新主路径" in action.get("text", "") for action in actions))

                question_id = game.current_question.question_id
                correct_index = game.current_question.question.answer_index
                wrong_index = (correct_index + 1) % 3
                wrong = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    callback_payload(f"qqa:ans:{game.game_id}:{question_id}:{wrong_index}", 222, "玩家B", message_id=600),
                )
                self.assertEqual(game.players[222].points, 15)
                self.assertEqual(game.players[222].wrong_count, 1)
                self.assertFalse(any(action.get("type") == "result" for action in wrong))

                right = await plugin.on_interaction(
                    ctx,
                    "join_quick_qa",
                    callback_payload(f"qqa:ans:{game.game_id}:{question_id}:{correct_index}", 111, "玩家A", message_id=601),
                )
                result = next(action for action in right if action.get("type") == "result")
                self.assertEqual(game.players[111].correct_count, 1)
                self.assertEqual(result["settlement"]["items"][0]["user_id"], 111)
                self.assertEqual(result["settlement"]["items"][0]["amount"], 13654)
                self.assertEqual(result["settlement"]["items"][0]["correct_count"], 1)
                self.assertEqual(result["settlement"]["items"][1]["wrong_count"], 1)
                self.assertEqual(result["settlement"]["items"][1]["user_id"], 222)
                self.assertEqual(result["settlement"]["amount"], 21980)
                self.assertNotIn(-100123, plugin._games)
            finally:
                await plugin.on_shutdown(ctx)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
