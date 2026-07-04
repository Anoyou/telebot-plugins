# 更新日志

## 1.0.2 (2026-07-05)
- 答对后的 `payout` action 增加玩家 user id 兜底，避免仅靠消息 ID 时 userbot 无法定位发奖回复目标。

## 1.0.1 (2026-07-04)
- 移除旧 `result_contract.send_via` 样板，普通回复改为继承当前会话通道。
- 答对后新增 `payout` action，由 userbot 执行发奖。
