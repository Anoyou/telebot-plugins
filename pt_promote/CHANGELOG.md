# 更新日志

## 1.0.20 (2026-06-30)
- 移除 PT 种子促销的付款确认触发声明：`interaction_entries` 与 `event_subscriptions` 不再包含 `payment_confirmed`。
- 删除交互入口中的 `money_channel` 元数据，明确该插件只通过管理员命令或交互 Bot 关键词/会话消息触发。
- 运行时不再接受 `payment_confirmed` 事件，避免无付款需求的促销插件被误配置成付款入口。

## 1.0.19 (2026-06-29)
- 按 TelePilot 0.41 最新插件开发指南补充顶层 `usage`、`event_subscriptions` 与 `capabilities` 元数据，插件中心可直接展示使用说明、事件订阅和能力声明。
- 同步 `plugin.json` 与 `manifest.py` 版本和 Event Bus 元数据，保留旧交互入口作为迁移兼容声明。

## 1.0.18 (2026-06-28)
- 按 TelePilot 0.36 最新开发指南收束交互插件主动发送通道，移除 `result_contract.send_via` 中已废弃的 `bbot_notice`。
- 保留 `interaction_bot` 与 `userbot_reply` 双通道声明，避免插件中心提示 `result_contract.send_via` 含有未支持值。



## 1.0.17 (2026-06-27)
- 按最新 TelePilot 插件开发文档补充 `config_schema["x-usage-guide"]`，让插件中心和通用配置页展示明确使用说明。
- 同步更新 `plugin.json` 与 `manifest.py` 版本，避免触发“未声明详细使用说明”的高级规范警告。

## 1.0.16 (2026-06-27)
- 按 TelePilot 0.33 交互框架文档补齐 `dispatch_modes`、`message_channels`、`money_channel` 与 `participant_policy`，明确交互 Bot、UserBot 和资金动作边界。
- 将最低 TelePilot 版本提升到 `0.33.0`，并同步 `plugin.json` 与 `manifest.py` 的版本、分类和交互入口声明。

## 1.0.15 (2026-06-26)
- 修复状态消息模板中 `{torrent_id}` 等占位符未被实际值替换的 bug。原因是 `_status_template` 把 message 当作一个值传给外层模板，`format_map` 只替换顶层占位符，message 内部的变量不会被递归处理。现在先用 payload 预格式化 message 再传入外层模板。

## 1.0.14 (2026-06-19)
## 1.0.14 (2026-06-19)
- 按 TelePilot 最新交互 Bot 入口规范补齐 `launch_mode`、事件白名单、会话策略、payload/result contract 和结算声明。
- 保留原有 UserBot 命令触发，交互 Bot 入口只负责触发和高频互动承接，不改变插件本体配置。
- 按最新插件开发指南补充 `min_telepilot_version`，并将配置页模式从旧兼容别名 `schema` 更新为推荐的 `single`。

## [1.0.9] - 2026-05-30

### Changed
- 置顶成功消息的折叠明细改为 `<code class="language-副标题与促销明细">` 结构，副标题、促销类型、促销时长和消耗全部放在同一个代码块内容里。
- 移除上一版误用的 `language-转账成功` 标识，避免成功结果被渲染成转账成功代码块。

## [1.0.8] - 2026-05-30

### Changed
- 折叠块标题“副标题与促销明细”改为带 `language-转账成功` 标识的 HTML code/pre 结构，方便交互 Bot 按 Telegram code language 标识识别。
- 折叠块里的副标题正文去掉 `副标题：` 前缀，减少长文本噪音。

## [1.0.7] - 2026-05-30

### Fixed
- 修复部分青娃 PT 详情页以表格展示副标题时，置顶成功消息缺少副标题的问题。
- 成功消息会清理标题里的站点状态尾巴，避免把 `[ 免费 ]`、剩余时间等页面状态混入种子标题。

### Changed
- 置顶成功消息改为只在外层展示成功状态、可点击种子标题和 ID，副标题、促销参数、消耗统一放入 Telegram 可展开引用块，减少群聊刷屏。

## [1.0.6] - 2026-05-29

### Added
- 增加交互 Bot 入口 `promote_torrent`，群友关键词触发后可直接调用置顶促销逻辑，不再需要自动回复拼接 userbot 指令。
- 成功消息补充可点击的种子标题、种子 ID 和副标题。
- 增加同一种子 ID 的处理中互斥和成功后 12 小时冷却，已处于置顶状态时直接提示不再处理，避免红包词/抽奖词等高频触发造成重复促销消耗。

### Fixed
- 站点返回 302 时提示可能已处于置顶状态或站点拒绝重复提交，而不是只显示 HTTP 状态码。
- 交互 Bot 入口会显式返回业务成功/失败状态，避免已处于置顶状态、查询失败等情况被规则层误计为成功调用。

## [1.0.5] - 2026-05-29

### Fixed
- 查询和置顶促销流程改为同一条消息逐步编辑，减少群内刷屏。
- 优化置顶流程文案，明确展示种子 ID、促销条件、预计消耗、计算方式和确认状态。
- 成功结果统一为“种子置顶促销成功”，并格式化蝌蚪消耗数字。
