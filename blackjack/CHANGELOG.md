# 更新日志
## 1.0.5 (2026-06-19)
- 按 TelePilot 最新交互 Bot 入口规范补齐 `launch_mode`、事件白名单、会话策略、payload/result contract 和结算声明。
- 保留原有 UserBot 命令触发，交互 Bot 入口只负责触发和高频互动承接，不改变插件本体配置。
- 按最新插件开发指南补充 `min_telepilot_version`，并将配置页模式从旧兼容别名 `schema` 更新为推荐的 `single`。
- 交互 Bot 入口现在可以直接开局并处理后续消息，中奖/胜负结果返回独立的 `result` 与 `settlement` 字段。

## 1.0.4 (2026-06-19)
- 修复玩家展示名可能读取账号本地联系人备注的问题；保存为联系人时优先展示公开 username（不带 @），没有 username 时回退用户 ID。
- 兼容新版 TelePilot 的统一公开展示名 helper，并保留旧环境兜底逻辑。

## 1.0.3 (2026-05-20)
- 补充交互 Bot 运行入口：实现 `on_interaction(ctx, entry_key, payload)` 最小 hook。
- 保持原有指令与消息监听主逻辑不变，仅新增交互入口声明对应的标准动作返回。
- 同步更新版本号与远程元数据一致性。

## 1.0.2 (2026-05-20)
- 新增模块分类声明：`category = "interactive"`（互动娱乐）。
- 声明交互 Bot 启动入口（interaction_entries）。
- 同步更新远程元数据 `plugin.json`，保证 manifest 与 metadata 一致。
