# 更新日志

## 1.0.15 (2026-10-30)
- 入口 `start_math_game` 增加 `legacy_entry_keys`，把旧入口 key `start_math10` 的推断（按群会话）写进插件自己的声明。TelePilot 0.112.0-beta.3+ 按声明处理、不再查宿主为本插件写死的兜底入口表；结论与原来完全相同（旧启动词、奖金与派奖不变）。旧宿主（含 0.110.0-beta.2 / 0.112.0-beta.2）忽略该字段、照旧走兜底表。未写 `entry_policy`（0.112.0-beta.2 见到它会整表跳过兜底），`min_telepilot_version` 不变。

## 1.0.14 (2026-10-28)
- 兼容性：`min_telepilot_version` 提高到 `0.72.0-beta.2`。`manifest.py` 向 `Manifest` 传入了 `requires_platform_capabilities`，该参数 TelePilot 0.72.0 起才支持，更早的版本导入插件时直接报 `TypeError`（宿主兼容性检查在 0.59.1 / 0.71.3 上实测）；提高后旧版本会在加载前给出“请先更新 TelePilot”的明确提示。功能无变化。
- `manifest.py` 的配置使用说明（`x-usage-guide`）与 `plugin.json` 不一致，统一为 `plugin.json` 的文案。

## 1.0.13 (2026-08-15)
- 新增标准 Event Bus 主入口，按 loader 注入入口键处理交互消息与结算事件。

## 1.0.12 (2026-08-15)
- 补充 TelePilot 0.97.0 平台能力声明，并同步 plugin.json 与 manifest.py。


## 1.0.11 (2026-07-30)
- 显式声明 `payout` 高风险权限并开启资金动作严格 Trace，兼容最新 TelePilot 插件开发指南与运行时权限校验。

## 1.0.10 (2026-07-12)
- 抢答结算改由 TelePilot `ctx.messages` 受控接口读取平台保存的题面消息 ID，installed 模式下可继续编辑原题面。

## 1.0.9 (2026-07-10)
- 按最新插件开发指南对齐：`on_interaction` 主路径改用标准事件信封（`event_from_interaction_payload`）读取事件类型、聊天/消息 ID、抢答文本和赢家 actor，旧平铺 payload helper 保留为兜底。
- 元数据同步：补充 `min_telepilot_version="0.33.0"`（plugin.json 与 manifest 一致），递增 patch 版本到 1.0.9。
- 保持原有算数题规则、抢答/结算行为与文案语义不变。

## 1.0.8 (2026-07-06)
- 答对后编辑原题面时不再重复追加奖金行，保留开局题面里的奖金展示。

## 1.0.7 (2026-07-06)
- 答对后编辑原题面的结算文案改为 24 点同款结构，避免玩家昵称和固定文案重复显示。

## 1.0.6 (2026-07-06)
- 开局题面标题按“名称 + 版本号 + 开始”格式展示，并精简抢答提示文案。

## 1.0.5 (2026-07-05)
- 开局题面标题追加插件版本号，便于远程实测确认当前 worker 实际加载的插件版本。

## 1.0.4 (2026-07-05)
- 答对后优先编辑原题面为结算结果，不再额外发送“答对了”公告；发奖仍保持原 `payout` action 链路。
- 结算题面中的赢家昵称限制为前 10 个字符，避免消息过长。

## 1.0.3 (2026-07-05)
- 答对公告移除发奖状态承诺文案；实际发奖仍继续返回 `payout` action 交给 TelePilot 执行。

## 1.0.2 (2026-07-05)
- 答对后的 `payout` action 增加玩家 user id 兜底，避免仅靠消息 ID 时 userbot 无法定位发奖回复目标。

## 1.0.1 (2026-07-04)
- 移除旧 `result_contract.send_via` 样板，普通回复改为继承当前会话通道。
- 答对后新增 `payout` action，由 userbot 执行发奖。
