# 更新日志

## 1.1.2 (2026-07-05)
- 交互答对后的 `payout` action 增加玩家 user id 兜底，提升 userbot 发奖定位稳定性。
- userbot 命令开局答对后的奖励改走 TelePilot 标准 `payout` 链路，避免插件直接发奖绕过平台动作记录。

## 1.1.1 (2026-07-04)
- 移除旧 `result_contract.send_via` 样板，普通回复改为继承当前会话通道。
- 交互答对后新增 `payout` action，由 userbot 执行发奖。
