# 更新日志

## 1.0.17 (2026-08-15)
- 新增标准 Event Bus 主入口；奖励与题面编辑统一改走 MessageOps。

## 1.0.16 (2026-08-15)
- 补充 TelePilot 0.97.0 平台能力声明，并同步 plugin.json 与 manifest.py。

## 1.0.15 (2026-07-30)
- 显式声明 `payout` 高风险权限并开启资金动作严格 Trace，兼容最新 TelePilot 插件开发指南与运行时权限校验。

## 1.0.14 (2026-07-10)
- 按最新插件开发指南对齐：`on_interaction` 主路径改用标准事件信封 `event_from_interaction_payload(payload)` 读取 `event.type`、`event.message.text/chat_id/message_id`、`event.sender/actor.user_id`、`event.payment.amount`；旧平铺 payload helper（`_payload_event` / `_interaction_*`）保留为字段取不到时的 fallback。
- 局内 start/answer 语义路由仍以旧 trigger 分类为准（框架层两者同为 `type=message`），取不到时回退 `event.type`，确保行为零变化。
- 元数据同步 `plugin.json`↔`manifest.py` 版本到 1.0.14。
- Tier 3（session.data 镜像）本轮跳过，保留现有进程内状态机 `self._games`（同进程续局零回归，收益有限）。
- 保持原有成语接龙规则、禁词逻辑、每轮/终局奖励与文案语义不变。

## 1.0.13 (2026-07-04)
- 适配 TelePilot 0.49 交互契约：奖励发放改用平台 `payout` 动作。
- 移除交互入口里的旧发奖通道声明，避免已是最新版本但实际仍不发奖。


## 1.0.13 (2026-07-04)
- 移除旧 `result_contract.send_via` 样板，普通回复改为继承当前会话通道。
- 接龙奖励改为 `payout` action，由 userbot 执行。

## 1.0.12 (2026-06-30)
- 修复未填写奖励金额时的用法提示仍写死 `,cy 100` 的问题，现在运行时会读取 TelePilot 当前命令前缀。

## 1.0.11 (2026-06-29)
- 按 TelePilot 0.41 最新插件开发指南补充顶层 `usage`、`event_subscriptions` 与 `capabilities` 元数据，插件中心可直接展示使用说明、事件订阅和能力声明。
- 同步 `plugin.json` 与 `manifest.py` 版本和 Event Bus 元数据，保留旧交互入口作为迁移兼容声明。

## 1.0.10 (2026-06-28)
- 按 TelePilot 0.36 最新开发指南收束交互插件主动发送通道，移除 `result_contract.send_via` 中已废弃的 旧 notice 通道值。
- 保留 `interaction_bot` 与 `平台资金通道` 双通道声明，避免插件中心提示 `result_contract.send_via` 含有未支持值。


## 1.0.9 (2026-06-27)
- 按最新 TelePilot 插件开发文档补充 `config_schema["x-usage-guide"]`，让插件中心和通用配置页展示明确使用说明。
- 同步更新 `plugin.json` 与 `manifest.py` 版本，避免触发“未声明详细使用说明”的高级规范警告。

## 1.0.8 (2026-06-27)
- 按 TelePilot 0.33 交互框架文档补齐 `dispatch_modes`、`message_channels`、`money_channel` 与 `participant_policy`，明确交互 Bot、UserBot 和资金动作边界。
- 将最低 TelePilot 版本提升到 `0.33.0`，并同步 `plugin.json` 与 `manifest.py` 的版本、分类和交互入口声明。

## 1.0.7 (2026-06-25)
- 修复交互 Bot 模式下奖励由 Bot 发放的问题，奖励消息改用 平台资金通道 由管理员账号发放。
- 交互发起的游戏 on_message 不再重复响应，避免双重提示。

## 1.0.6 (2026-06-19)
- 按 TelePilot 最新交互 Bot 入口规范补齐 `launch_mode`、事件白名单、会话策略、payload/result contract 和结算声明。
- 保留原有 UserBot 命令触发，交互 Bot 入口只负责触发和高频互动承接，不改变插件本体配置。
- 按最新插件开发指南补充 `min_telepilot_version`，并将配置页模式从旧兼容别名 `schema` 更新为推荐的 `single`。
- 交互 Bot 入口现在可以直接开局并处理后续消息，中奖/胜负结果返回独立的 `result` 与 `settlement` 字段。

## 1.0.5 (2026-06-19)
- 修复接龙赢家展示名可能读取账号本地联系人备注的问题；保存为联系人时优先展示公开 username（不带 @），没有 username 时回退用户 ID。
- 兼容新版 TelePilot 的统一公开展示名 helper，并保留旧环境兜底逻辑。

## 1.0.4 (2026-05-20)
- 补充交互 Bot 运行入口：实现 `on_interaction(ctx, entry_key, payload)` 最小 hook。
- 保持原有指令与消息监听主逻辑不变，仅新增交互入口声明对应的标准动作返回。
- 同步更新版本号与远程元数据一致性。

## 1.0.3 (2026-05-20)
- 新增模块分类声明：`category = "interactive"`（互动娱乐）。
- 声明交互 Bot 启动入口（interaction_entries）。
- 同步更新远程元数据 `plugin.json`，保证 manifest 与 metadata 一致。
