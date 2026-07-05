# 更新日志

## 1.1.4 (2026-07-05)
- 答对公告移除发奖状态承诺文案；实际发奖仍继续返回 `payout` action 交给 TelePilot 执行。

## 1.1.3 (2026-07-05)
- 修复 24 点表达式校验在 Python 3.9 测试环境下使用 `int | float` 作为 `isinstance` 参数导致合同测试失败的问题。

## 1.1.2 (2026-07-05)
- 交互答对后的 `payout` action 增加玩家 user id 兜底，提升 userbot 发奖定位稳定性。
- userbot 命令开局答对后的奖励改走 TelePilot 标准 `payout` 链路，避免插件直接发奖绕过平台动作记录。

## 1.1.1 (2026-07-04)
- 移除旧 `result_contract.send_via` 样板，普通回复改为继承当前会话通道。
- 交互答对后新增 `payout` action，由 userbot 执行发奖。
