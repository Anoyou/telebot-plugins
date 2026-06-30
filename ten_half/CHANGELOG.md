# 更新日志

## 0.2.10 (2026-06-30)
- 按最新 TelePilot 插件开发指南重构交互 Bot 流程，选庄、庄家行动、玩家行动和结算优先编辑同一条主消息，减少群内消息刷屏。
- 调整十点半交互规则：庄家先行动并通过按钮弹窗查看庄家牌；玩家起手 1 张明牌，再从第二张开始逐张要牌，爆牌后自动结束当前玩家回合。
- 优化转账加入体验：开局文案展示底注、收款 userbot 和牌桌 ID；玩家付款加入后回复付款消息，滚动更新加入通知并删除上一条加入通知。
- 将错人点击、未轮到你、加倍条件不满足等短提示改为 `answer_callback`，避免提示消息长期留在群里。
- 同步声明 `edit_message`、`delete_message`、`answer_callback` 交互动作和 `delete_message` 权限，消除新版 Contract Guard 告警。

## 0.2.9 (2026-06-30)
- 修复交互 Bot 选庄超时后调用不存在的 `_begin_game_ix` 导致后台任务崩溃的问题。
- 超时自动选择机器人当庄时复用交互动作发送逻辑，避免游戏卡在选庄阶段。

## 0.2.8 (2026-06-29)
- 修复交互 Bot 超时/后台结算路径引用未定义变量导致奖励流程中断的问题。
- 修复十点半奖励消息格式，统一由 userbot 回复玩家付款/触发消息发送 `+金额`，便于转账发奖链路识别。
- 修复关键词开局后首位玩家付款加入会重复发送大厅公告、重复启动大厅定时器的问题。
- 补充 `no_session` 到交互结果契约，避免会话已结束后普通消息触发规范告警。

## 0.2.7 (2026-06-29)
- 按 TelePilot 0.41 最新插件开发指南补充顶层 `usage`、`event_subscriptions` 与 `capabilities` 元数据，插件中心可直接展示使用说明、事件订阅和能力声明。
- 同步 `plugin.json` 与 `manifest.py` 版本和 Event Bus 元数据，保留旧交互入口作为迁移兼容声明。

## 0.2.6 (2026-06-28)
- 按 TelePilot 0.36 最新开发指南收束交互插件主动发送通道，移除 `result_contract.send_via` 中已废弃的 `bbot_notice`。
- 保留 `interaction_bot` 与 `userbot_reply` 双通道声明，避免插件中心提示 `result_contract.send_via` 含有未支持值。


## 0.2.5 (2026-06-27)
- 按最新 TelePilot 插件开发文档补充 `config_schema["x-usage-guide"]`，让插件中心和通用配置页展示明确使用说明。
- 同步更新 `plugin.json` 与 `manifest.py` 版本，避免触发“未声明详细使用说明”的高级规范警告。

## 0.2.4 (2026-06-27)
- 按 TelePilot 0.33 交互框架文档补齐 `dispatch_modes`、`message_channels`、`money_channel` 与 `participant_policy`，明确交互 Bot、UserBot 和资金动作边界。
- 将最低 TelePilot 版本提升到 `0.33.0`，并同步 `plugin.json` 与 `manifest.py` 的版本、分类和交互入口声明。
