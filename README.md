# TelePilot Plugins

TelePilot 远程插件仓库。

## 插件列表

| 插件 | 指令 | 说明 |
|------|------|------|
| [blackjack](./blackjack/) | `{prefix}bj` | 经典21点纸牌游戏 |
| [guess_number](./guess_number/) | `{prefix}guess 金额` | 群内猜数字 |
| [dice_battle](./dice_battle/) | `{prefix}dice` | 骰子比大小，支持对战 |
| [idiom_chain](./idiom_chain/) | `{prefix}cy 金额` | 成语接龙，支持禁词规则 |
| [poetry_blank](./poetry_blank/) | `{prefix}poetry 金额` | 古诗词填空抢答 |
| [chatter_challenge](./chatter_challenge/) | `{prefix}chat` | 话痨挑战，违反规则自动扣分 |
| [dice_grid_hunt](./dice_grid_hunt/) | `{prefix}dicegrid 金额` | 九宫格骰子图片竞猜 |
| [lottery_plus](./lottery_plus/) | `{prefix}lotto` | 群内彩票下注与开奖 |
| [mindreader_survival](./mindreader_survival/) | `{prefix}mind` | 多人读心生存赛 |
| [ten_half](./ten_half/) | 交互规则关键词 | 多人十点半纸牌游戏 |
| [auto_reply](./auto_reply/) | 规则配置 | 插件库推荐自动回复，按规则匹配关键词或正则后自动回复 |
| [autorepeat](./autorepeat/) | 规则配置 | 插件库推荐自动复读，多人发送相同内容时自动复读 |
| [game24](./game24/) | `{prefix}24d` | 插件库维护 24 点竞速答题，支持交互 Bot 调度 |
| [math10](./math10/) | 交互规则启动 | 插件库维护 10 以内算数题，支持交互 Bot 调度 |
| [chatgpt_image](./chatgpt_image/) | 插件配置 | 插件库维护 ChatGPT2API 图片生成/编辑与 token 池 |
| [codex_image](./codex_image/) | 插件配置 | 插件库维护 Codex 图片生成 |
| [AI-Chat](./ai-chat/) | `{prefix}ask` | AI-Chat 聊天与消息解释助手，调用 TelePilot 已配置的 AI Provider |
| [AI 答疑（交互 Bot）](./interaction-ai-chat/) | @交互 Bot / 回复 Bot | 交互 Bot 被 @提及或回复（含访客模式 Guest Mode）时调用 TelePilot 已配置的 AI Provider 回答：先回“🤔”占位再编辑为 Telegram 富文本，可选联网搜索与识图，文案模板可自定义，带短期记忆、白名单与每日限额；可选 NexusPHP PT 站点只读数据源（Cookie 自填，只搜种子列表、双重脱敏，结果表默认用 Bot API 10.3 紧凑表格）；内置 group-helper / qingwa-wiki skills，可选用已安装 Agent Skills 与站点 Wiki 只读检索（附页面链接）；模型调用失败时附「重试」（提问者/管理员）与「换模型重试」（管理员）按钮，也可回复“重试”按原问题重放 |
| [bot_mute_guard](./bot_mute_guard/) | 无 | 指定群组非白名单 @bot、inline Bot 与 Bot 发言广告消息删除 |
| [private_chat_assistant](./private_chat_assistant/) | 无 | 监听账号收到的私聊，通过插件专用 Bot 向指定账号发送 60 秒聚合提醒 |
| [todo_reminder](./todo_reminder/) | `{prefix}todo`、`{prefix}undo ID` | 支持自然语言单次提醒和 `todo 数字 提醒内容` 重复简写；含完成反馈与命令自动删除 |
| [sum](./sum/) | `{prefix}sum [数量]` | AI 群消息总结，支持快捷总结与定时任务 |
| [dead_revolver](./dead_revolver/) | `dr 金额` | 死亡左轮，群聊俄罗斯轮盘赌局 |
| [quick_qa](./quick_qa/) | `{prefix}quickqa` | 快问快答积分淘汰赛，支持 URL + AI 生成题库 |
| [lucky_redpack](./lucky_redpack/) | `{prefix}rp 发财 88888 10` | 拼手气口令红包，财富密码每次领取后随机刷新 |
| [ai_redpacket](./ai_redpacket/) | `{prefix}airp` | 基于 URL + AI 题库的三选一答题红包，支持默认配置或 `create` 参数创建，Interaction Bot 答题、UserBot 发奖 |
| [reply_anchor_test](./reply_anchor_test/) | `{prefix}send 用户ID 金额` | 测试 userbot 搜索目标用户近期发言并回复 `+金额` |
| [payment-source-cn-transfer](./payment-source-cn-transfer/) | 无（交互 Bot 转账通知） | TelePilot 0.111.0+ 转账通知解析扩展点（payment_sources）官方实现：识别中文转账 / 扣减通知的付款人、收款人、金额；规则与宿主内置解析器逐字一致，只返回解析结果、不结算；双跑对照期默认只做对照 |
| [random_benefit](./random_benefit/) | `{prefix}随机福利 on/off` | 随机引用指定群组发言回复自定义福利语 |

## byRBQ 迁移插件（Pagermaid → TelePilot）

以下插件已按最新 TelePilot 远程插件结构迁移并重命名为 `原名-byRBQ`：

- [ais-byRBQ](./ais-byRBQ/)
- [cai-byRBQ](./cai-byRBQ/)
- [get_reactions-byRBQ](./get_reactions-byRBQ/)
- [gi2-byRBQ](./gi2-byRBQ/)
- [jpm-byRBQ](./jpm-byRBQ/)
- [jpmai-byRBQ](./jpmai-byRBQ/)
- [luckydraw-byRBQ](./luckydraw-byRBQ/)
- [pixivshow-byRBQ](./pixivshow-byRBQ/)
- [redpack-byRBQ](./redpack-byRBQ/)
- [sar-byRBQ](./sar-byRBQ/)
- [sfl-byRBQ](./sfl-byRBQ/)
- [share_plugins-byRBQ](./share_plugins-byRBQ/)

每个迁移插件目录都包含：
- `plugin.json`（安装阶段元数据）
- `manifest.py`（运行期 Manifest）
- `plugin.py`（TelePilot 插件入口）
- `__init__.py`（导出 `PLUGIN_CLASS` / `MANIFEST`）
- `legacy_main.py`（保留原 Pagermaid 实现供后续功能深度迁移）

迁移插件测试工具：
- 冒烟脚本：`scripts/smoke_check_byrbq.py`
- 手工清单：`docs/BYRBQ-MANUAL-TEST-CHECKLIST.md`

## 安装方式

1. 在 TelePilot 前端 → 插件中心 → 插件仓库
2. 添加仓库地址：`https://github.com/Anoyou/telebot-plugins.git`
3. 浏览插件列表，点击安装

## 开发新插件

每个插件一个目录，包含：
- `plugin.json` — 元数据（必填）
- `manifest.py` — 运行期 Manifest（必填）
- `plugin.py` — 入口文件
- `__init__.py` — 导出 `PLUGIN_CLASS` 和 `MANIFEST`（必填）

开发前请以 Beta 分支的最新文档为准：

- [插件开发指南](https://github.com/Anoyou/Telebot/blob/Beta/docs/PLUGIN-DEV-GUIDE.md)
- [插件 API 完整参考](https://github.com/Anoyou/Telebot/blob/Beta/docs/PLUGIN-API-REFERENCE.md)

### 新平台能力：先探测、再回退（本仓库约定）

插件库约定**不为了用新能力抬高 `min_telepilot_version`**：新能力先探测 `ctx.capabilities`，旧平台自动走旧实现；这层兼容写法保留到 Plugin API 2.0 再删。各能力的写法与门槛见主仓库 [PLUGIN-API-REFERENCE.md「版本与兼容」](https://github.com/Anoyou/Telebot/blob/Beta/docs/PLUGIN-API-REFERENCE.md#版本与兼容)，本仓库已有的参考实现：

| 能力 | 参考插件 | 旧平台表现 |
| --- | --- | --- |
| 通用回调路由 `messages.callback_routing`（按钮写 `{"callback": {"data": ...}}`） | interaction-ai-chat `retry.button()` / `callback_routing_supported()` | 照旧写 `callback_data`；订阅不改（不要同时订阅 `own_buttons` 与 `all_allowed_chats`，同一点击会投递两次） |
| 宿主只读查询 `host.read.account.admins@1` | interaction-ai-chat `_host_admin_ids()` | 读 payload 的 `owner_user_ids` / `admin_user_ids` |
| 声明式卡片 `ui.cards` + `on_ui_card` | sum「定时总结任务」 | 旧宿主忽略 `ui`，不调用钩子 |
| 保存校验 `on_config_validate` | interaction-ai-chat、todo_reminder、private_chat_assistant、chatgpt_image、codex_image | 旧宿主不调用，保存行为不变 |
| 表单契约 2：`x-ui-visible-if`、数组 `textarea` 每行一项、`x-rule-schema`、`dry_run_rule` | interaction-ai-chat / ai-chat / todo_reminder / bot_mute_guard、auto_reply、autorepeat | 旧客户端忽略声明，字段照常全部显示 |
| 密钥列表 `x-secret-list` | chatgpt_image | 旧宿主经 plugin_compat 兼容 |

```python
def callback_routing_supported(ctx) -> bool:
    caps = getattr(ctx, "capabilities", None)   # 0.107 之前的宿主没有
    if caps is None:
        return False
    try:
        return bool(caps.has("messages.callback_routing"))
    except Exception:  # 探测失败按旧写法
        return False
```

- `on_ui_card` / `on_ui_action` / `on_config_validate` 在**主进程**的受控上下文里调用：有 `ctx.config`、`ctx.data_dir`、`ctx.http`、`ctx.ai`、`ctx.account_id`（全局作用域为 0），**没有** `ctx.storage`、`ctx.messages`、Telegram client。数据放在 `ctx.storage` 里的插件暂时做不了数据卡片。
- `x-ui-visible-if` 推荐写 `not_in`（只在开关被明确关掉时隐藏）：Web / iOS 按表单当前值判断，值缺省或控制字段在另一作用域时 `equals: true` 会把字段误藏起来。
- 涉及资金（红包、抽奖、下注、结算、转账）的插件只做纯展示类适配，不改结算与金额逻辑。

### 版本要求与兼容性检查

- `plugin.json` 与 `manifest.py` 的 `version`、`min_telepilot_version`、`permissions`、`config_schema` 等字段必须一致。
- `min_telepilot_version` 要覆盖插件实际用到的能力。`manifest.py` 向 `Manifest(...)` 传 `requires_platform_capabilities`（新装/升级必填）时，最低版本不得低于 `0.72.0-beta.2`——更早的 TelePilot 导入 manifest 会直接 `TypeError`。用到 `send_rich`、`ctx.skills`、识图、`compact_tables` 等新能力时，要么提高最低版本，要么先用能力标记（如 `getattr(ctx.messages, "rich_table_compact", False)`）探测再调用。版本比较：TelePilot 0.107.0-beta.1 起声明带 beta 号（如 `0.106.0-beta.12`）时按完整 SemVer 比较，不带 beta 号时仍只比 `x.y.z`；更早的宿主一律只比 `x.y.z`，所以依赖某个 beta 才有的能力仍要同时做能力探测。
- 用 TelePilot 主仓库的真实运行时检查全部插件（安装期校验、加载、manifest 一致性、权限、Host API 方法与参数、最低版本推断、`on_startup` 初始化）：

  ```bash
  git clone -b Beta https://github.com/Anoyou/Telebot.git ../Telebot
  python -m pip install -e "../Telebot/backend[dev]"
  TELEPILOT_REPO=../Telebot python scripts/check_host_compat.py          # 全部插件
  TELEPILOT_REPO=../Telebot python scripts/check_host_compat.py --plugin sum --strict
  TELEPILOT_REPO=../Telebot python -m pytest -q                           # 含 tests/test_host_compat.py
  TELEPILOT_REPO=../Telebot python scripts/check_host_compat.py --plugins-root ../Telebot/examples/plugins  # 检查主仓库示例
  ```

  也可以把 `TELEPILOT_REPO` 指向主仓库任意历史版本的检出，验证插件在旧版 TelePilot 上能否加载。
- CI（`.github/workflows/ci.yml`）在每次推送 / PR 和每天定时检出 `Anoyou/Telebot` 的 `Beta` 分支运行上述检查与全部 pytest，不需要任何密钥；手动运行时可指定其它分支或提交，报告作为 `host-compat-report` 工件上传。

