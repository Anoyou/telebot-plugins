# 更新日志

## 1.1.33 (2026-08-15)
- 补充实际延迟清理题图所需的 `delete_message` 权限声明，使平台审计权限与 MessageOps 删除动作一致。

## 1.1.32 (2026-08-15)
- 新增标准 Event Bus 主入口；图片发送、题面编辑、奖励与延迟删除统一改走 MessageOps。

## 1.1.31 (2026-08-15)
- 补充 TelePilot 0.97.0 平台能力声明，并同步 plugin.json 与 manifest.py。

## 1.1.30 (2026-07-30)
- 显式声明 `payout` 高风险权限并开启资金动作严格 Trace，兼容最新 TelePilot 插件开发指南与运行时权限校验。

## 1.1.29 (2026-07-14)
- 移除题图发送与 caption 编辑动作中硬编码的 `send_via` / `send_via_options`，普通会话消息现由 TelePilot 按 `session.channel` 自动路由，保持 `payout` 固定走 userbot。

## 1.1.28 (2026-07-10)
- 修复交互 Bot 开局和答题仍依赖插件进程内存的问题：开局写入平台 session，后续答题从 `payload.session.data` 恢复局面。
- 猜错时返回 `update_session` 保存用户答题冷却，避免进程重载或会话续投递后同一用户限制丢失。
- 补齐 `callback_query`、`session_expired`、`update_session` 和 `valid_seconds` 交互契约声明，保留原图 `edit_caption` 和自动 `payout` 发奖链路。

## 1.1.27 (2026-07-10)
- 按最新插件开发指南对齐：`on_interaction` 读取改用标准事件信封 `event_from_interaction_payload(payload)` 作为主路径（事件类型、聊天、消息文本/ID、发起人 ID），旧平铺 payload helper 全部保留为 fallback，标准字段取不到时自动回退。
- 事件类型路由仍保持旧平铺优先、信封兜底，keyword/付款开局与答题分发语义不变；九宫格题图、答对 `edit_caption`、`payout` 发奖与每回合全量状态重发流程完全保留，玩法与文案零改动。
- 同步 `plugin.json` 与 `manifest.py` 版本号。

## 1.1.26 (2026-07-06)
- 修复交互 Bot 会话答题时未读取 TelePilot 当前标准 payload 的 `message.text`，导致答对后返回空动作、无法触发 `edit_caption` 的问题。
- 答对后的 caption 编辑统一依赖开局题图保存键定位原图片，避免玩家回复其他消息时误把 `reply_to_message_id` 当作图片消息 ID。
- 交互链路发奖 action 补充 `reply_to_user_id`，方便平台在消息 ID 缺失时使用近期发言兜底，并提升结算日志可排查性。

## 1.1.25 (2026-07-06)
- 九宫格开局题图 action 显式携带 `chat_id`，避免不同交互入口下仅依赖事件兜底导致保存原图消息 ID 不稳定。
- 答对编辑 caption 同时携带 `message_id` / `edit_message_id` 和保存键，并声明优先交互 Bot、可回退 UserBot 的发送通道。
- 兼容 TelePilot payload 中 `message.reply_to.message_id` 形态的原图回复 ID。

## 1.1.24 (2026-07-06)
- 答对后编辑原图 caption 时优先使用玩家回复的原图 `message_id`，保留保存键兜底，避免题图消息 ID 未命中时只发奖不编辑。
- 补充交互 Bot 回归测试，覆盖回复原图编辑和保存键兜底两种路径。

## 1.1.23 (2026-07-06)
- 九宫格题图发送和答对 caption 编辑显式走 `interaction_bot`，避免会话通道路由不一致导致原图无法编辑。
- 兼容已保存的旧开局模板：运行时自动为“九宫格竞猜”标题补上版本号。

## 1.1.22 (2026-07-06)
- 答对后编辑原图 caption 时不再重复追加奖金行，保留开局题面里的奖励展示。

## 1.1.21 (2026-07-06)
- 答对后编辑原图 caption 的结果文案改为 24 点同款结构，并同时携带 `caption` / `text` 字段适配 TelePilot 执行器。

## 1.1.20 (2026-07-06)
- 交互 Bot 开局题图保存消息 ID，答对后改为 `edit_caption` 原地更新原图片 caption，不再额外发送答对公告。
- 答对结果复用成功模板并默认显示“图 N”，发奖仍保持原 `payout` action 链路。
- 修正首次答题在极短运行时间环境下可能被冷却判断误拦的问题。

## 1.1.19 (2026-07-05)
- 答对公告移除发奖状态承诺文案；实际发奖仍继续返回 `payout` action 交给 TelePilot 执行。

## 1.1.18 (2026-07-04)
- 适配 TelePilot 0.49 交互契约：奖励发放改用平台 `payout` 动作。
- 移除交互入口里的旧发奖通道声明，避免已是最新版本但实际仍不发奖。


## 1.1.18 (2026-07-04)
- 移除旧 `result_contract.send_via` 样板，普通回复改为继承当前会话通道。
- 答对后新增 `payout` action，修复只公告结算但不触发 userbot 发奖的问题。

## 1.1.17 (2026-06-29)
- 按 TelePilot 0.41 最新插件开发指南补充顶层 `usage`、`event_subscriptions` 与 `capabilities` 元数据，插件中心可直接展示使用说明、事件订阅和能力声明。
- 同步 `plugin.json` 与 `manifest.py` 版本和 Event Bus 元数据，保留旧交互入口作为迁移兼容声明。

## 1.1.16 (2026-06-28)
- 按 TelePilot 0.36 最新开发指南收束交互插件主动发送通道，移除 `result_contract.send_via` 中已废弃的 旧 notice 通道值。
- 保留 `interaction_bot` 与 `平台资金通道` 双通道声明，避免插件中心提示 `result_contract.send_via` 含有未支持值。


## 1.1.15 (2026-06-27)
- 按最新 TelePilot 插件开发文档补充 `config_schema["x-usage-guide"]`，让插件中心和通用配置页展示明确使用说明。
- 同步更新 `plugin.json` 与 `manifest.py` 版本，避免触发“未声明详细使用说明”的高级规范警告。

## 1.1.14 (2026-06-27)
- 按 TelePilot 0.33 交互框架文档补齐 `dispatch_modes`、`message_channels`、`money_channel` 与 `participant_policy`，明确交互 Bot、UserBot 和资金动作边界。
- 将最低 TelePilot 版本提升到 `0.33.0`，并同步 `plugin.json` 与 `manifest.py` 的版本、分类和交互入口声明。

## 1.1.13 (2026-06-19)
- 按 TelePilot 最新交互 Bot 入口规范补齐 `launch_mode`、事件白名单、会话策略、payload/result contract 和结算声明。
- 保留原有 UserBot 命令触发，交互 Bot 入口只负责触发和高频互动承接，不改变插件本体配置。
- 按最新插件开发指南补充 `min_telepilot_version`，并将配置页模式从旧兼容别名 `schema` 更新为推荐的 `single`。

## 1.1.12 (2026-06-19)
- 修复原生命令答题路径中赢家展示名可能读取账号本地联系人备注的问题；保存为联系人时优先展示公开 username（不带 @），没有 username 时回退用户 ID。
- 交互 Bot 入口仍使用平台信封中的公开展示名，结构化赢家结果与结算字段保持隔离。

## 1.1.11 (2026-05-28)
- 修复交互 Bot 入口只提示再次发送指令的问题；入口触发后会直接发出九宫格题图并进入答题。
- 交互 Bot 后续消息现在会直接判定 1-9 答案，答对后发送可被自动发奖监听识别的中奖公告并结束会话。
- 参与有效期会作为本轮答题限时传入，不再需要在模块入口参数里重复配置 `prize` 或 `timeout`。

## 1.1.10 (2026-05-26)
- 按最新模块规范清理静态元数据中的疑似前缀误报文案。
- 配置页模式从旧兼容别名 `schema` 调整为推荐的 `single`。
- 同步更新模板预览默认文案和运行时内置默认文案。

## 1.1.9 (2026-05-21)
- 按最新模板预览规范接入 `{prefix}` 系统级占位符。
- 进行中提示、奖励参数错误提示和交互 Bot 入口文案不再硬编码英文逗号前缀，会随系统命令前缀变化。
- 兼容已保存的旧版默认模板，运行时会把旧的逗号前缀默认文案升级为 `{prefix}` 模板。
- 同步更新配置说明和远程元数据，避免消息预览中出现与实际设置不一致的命令前缀。

## 1.1.8 (2026-05-20)
- 补充交互 Bot 运行入口：实现 `on_interaction(ctx, entry_key, payload)` 最小 hook。
- 保持原有指令与消息监听主逻辑不变，仅新增交互入口声明对应的标准动作返回。
- 同步更新版本号与远程元数据一致性。

## 1.1.7 (2026-05-20)
- 新增模块分类声明：`category = "interactive"`（互动娱乐）。
- 声明交互 Bot 启动入口（interaction_entries）。
- 同步更新远程元数据 `plugin.json`，保证 manifest 与 metadata 一致。

## 1.1.6 - 2026-05-17

- 补充旧版开局模板占位符说明，明确 `{title}`、`{target_line}`、`{guide_line}`、`{reward_line}` 的实际展开内容。
- 更新开局模板描述，提示新模板应直接写完整文案，旧占位符仅作为兼容保留。
- 补齐所有消息模板的只读预览字段，配合配置弹窗的预览分组展示。

## 1.1.5 - 2026-05-17

- 精简九宫格骰子竞猜的默认开局、进行中、答对、超时和结束文案。
- 收敛配置弹窗里的消息模板字段，移除分段式标题、目标、引导、奖励模板和多余结果预览。
- 保留旧版分段模板配置的运行时兼容，避免已有自定义文案在更新后直接失效。
- 启动日志增加插件版本号，方便远程更新后确认运行版本。
