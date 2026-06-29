# 更新日志

## 1.0.8 (2026-06-29)
- 修复转账通知命中后无响应的问题：为 `payment_confirmed` Event Bus 订阅补充 `entry_key=join_paid_game`，避免事件被订阅匹配后因缺少入口而跳过插件调用。
- 兼容 TelePilot Event Bus 付款 payload，从 `payment` 与 `reply_to` 中提取付款金额、付款人和玩家消息 ID，防止付款事件被静默忽略或误把通知 Bot 当作玩家。

## 1.0.7 (2026-06-29)
- 调整死亡左轮大厅参与方式文案：只展示“转账门槛金额自动报名”和“庄家发送开局关键词开始”两条说明，不再提示 `+金额` 快速加入。
- 新增可配置开局关键词，默认使用“开始挑战”，并继续兼容 `dr_start` 老命令。
- 同步更新插件中心和配置页使用说明，让 TelePilot 展示的参与方式与游戏大厅一致。

## 1.0.6 (2026-06-29)
- 收紧付款报名校验：标准付款事件和文本通知都会确认收款人是当前 UserBot，无法确认或转给其他人时不会加入游戏。
- 同步更新大厅、插件中心和配置页文案，明确“只接受转给当前 UserBot 收款人”的门票规则。

## 1.0.5 (2026-06-29)
- 修复玩家超时自射导致结算流程取消自身定时任务，从而中断最终结算的问题；只剩一名存活玩家时会正常结算并发放奖励。
- 发送下一名玩家射击按钮前会清理上一轮按钮消息，减少群聊中已完成操作的旧按钮残留。
- 报名付款会校验收款人是否为当前 UserBot，并在通知缺少 reply_id 时回退到付款消息发送者，降低误报名和漏报名概率。
- 游戏取消或人数不足时自动退还已付门票，并更新大厅参与说明，隐藏内部游戏 ID。
- 同步声明 `开始挑战` 开局别名，保持插件元数据与运行时命令一致。

## 1.0.4 (2026-06-29)
- 按 TelePilot 0.41 最新插件开发指南补充顶层 `usage`、`event_subscriptions` 与 `capabilities` 元数据，插件中心可直接展示使用说明、事件订阅和能力声明。
- 同步 `plugin.json` 与 `manifest.py` 版本和 Event Bus 元数据，保留旧交互入口作为迁移兼容声明。

## 1.0.3 (2026-06-28)
- 按 TelePilot 0.36 最新开发指南收束交互插件主动发送通道，移除 `result_contract.send_via` 中已废弃的 `bbot_notice`。
- 保留 `interaction_bot` 与 `userbot_reply` 双通道声明，避免插件中心提示 `result_contract.send_via` 含有未支持值。


## 1.0.2 (2026-06-27)
- 按最新 TelePilot 插件开发文档补充 `config_schema["x-usage-guide"]`，让插件中心和通用配置页展示明确使用说明。
- 同步更新 `plugin.json` 与 `manifest.py` 版本，避免触发“未声明详细使用说明”的高级规范警告。

## 1.0.1 (2026-06-27)
- 按 TelePilot 0.33 交互框架文档补齐 `dispatch_modes`、`message_channels`、`money_channel` 与 `participant_policy`，明确交互 Bot、UserBot 和资金动作边界。
- 将最低 TelePilot 版本提升到 `0.33.0`，并同步 `plugin.json` 与 `manifest.py` 的版本、分类和交互入口声明。
