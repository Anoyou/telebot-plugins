# 更新日志

## 1.0.23 (2026-06-28)
- 按 TelePilot 0.36 最新开发指南收束交互插件主动发送通道，移除 `result_contract.send_via` 中已废弃的 `bbot_notice`。
- 保留 `interaction_bot` 与 `userbot_reply` 双通道声明，避免插件中心提示 `result_contract.send_via` 含有未支持值。


## 1.0.22 (2026-06-27)
- 按最新 TelePilot 插件开发文档补充 `config_schema["x-usage-guide"]`，让插件中心和通用配置页展示明确使用说明。
- 同步更新 `plugin.json` 与 `manifest.py` 版本，避免触发“未声明详细使用说明”的高级规范警告。

## 1.0.21 (2026-06-27)
- 按 TelePilot 0.33 交互框架文档补齐 `dispatch_modes`、`message_channels`、`money_channel` 与 `participant_policy`，明确交互 Bot、UserBot 和资金动作边界。
- 将最低 TelePilot 版本提升到 `0.33.0`，并同步 `plugin.json` 与 `manifest.py` 的版本、分类和交互入口声明。
- 交互 Bot 回调删除旧按钮消息时改为检测 `ctx.client` 是否可用，兼容受限测试桩和 0.33 facade 注入场景。

## 1.0.20 (2026-06-26)
- 点击按钮（要牌/停牌/加倍）或发送文字指令时，自动删除旧 Bot 消息，避免聊天记录堆积。
- 交互 Bot 回调路径（callback_query / message）删除按钮来源消息，新消息不再 reply_to 已删除消息。
- 文字指令路径（`_player_action`）通过 `gs.message_id` 追踪并删除上一条 Bot 回复。
- 结算消息改为独立发送（不 reply_to 已删除的按钮消息）。

## 1.0.19 (2026-06-26)
- 确认无扣款功能，移除输了/bust 的 `-{bet}` 消息，只在赢时发 `+{amount}` 奖励。
- 庄家盖牌展示优化为 `🂠 ♦5（暗牌 ♦）`，显示暗牌花色。

## 1.0.18 (2026-06-26)
- 输牌/爆牌时发送扣款消息（`-{bet}`）通过 userbot_reply，与赢牌奖励对称。
- duplicate_start 从 reject 改为 allow，确保多人同时开局不互相干扰。
- 庄家盖牌展示优化：`🂠 ♦5  [?]`，暗牌更明显。

## 1.0.17 (2026-06-26)
- 修复下注金额取值优先级：优先取玩家实际转账金额（amount），而非规则配置的固定值（prize）。
- 修复玩家爆牌后庄家不补牌的问题：爆牌时也执行 _dealer_finish，展示庄家完整手牌。
- 玩家抓牌无上限限制，可以一直要牌直到停牌或爆牌。

## 1.0.16 (2026-06-26)
- 修复 payment_confirmed 触发时 trigger_message_id 取错消息的问题：payment_confirmed 的 message_id 是转账通知（bot发的），参与者的"+数字"消息在 reply_to.message_id 里。

## 1.0.15 (2026-06-26)
- session_scope 从 chat 改为 user，支持同一群内多名玩家同时开局互不干扰。
- 每个玩家独立会话，A 开局的同时 B 可以正常转账开局和交互。

## 1.0.14 (2026-06-26)
- 回退 trigger_message_id 逻辑：keyword 和 payment_confirmed 触发均存储玩家消息 ID，payment_confirmed 的参与者回复 "+数字" 本身就是玩家消息。

## 1.0.13 (2026-06-26)

## 1.0.12 (2026-06-26)
- GameState 新增 `trigger_message_id` 字段，记录玩家触发消息 ID。
- 奖励 `+{amount}` 消息改为回复玩家原始触发消息（`send_via: userbot_reply`），而非操作按钮消息。
- 全局文案「筹码」统一替换为「蝌蚪」。
- 「你的牌」统一改为显示玩家名称（如「张三的牌」），结算界面同步生效。
- 按钮提示文案改为「请 @{玩家名} 点击下方按钮进行操作」，去除旧 `🔔` mention 格式。

## 1.0.11 (2026-06-26)
- 修复通过转账通知触发交互 Bot 开局时，21 点误把通知 Bot 当作玩家，导致真正付款人点击按钮后被玩家校验拦截的问题。
- 补充交互 Bot 回归测试，覆盖付款人开局按钮归属与非本人按钮拦截。
- 全交互流程增加详细日志（开局、操作、回调、结算），便于线上排查。
- 按钮消息末尾增加玩家 @mention 提醒，点击可跳转用户资料。
- 修正 `_interaction_action` 与 `_interaction_callback_action` 方法签名，统一接收 `ctx` 参数。
- 结算奖励消息 `send_via: userbot_reply` 保持不变，结算日志明确记录每条动作的 send_via。

## 1.0.10 (2026-06-26)
- 交互 Bot 发牌文案去除旧文字指令提示，改为按钮操作引导。

## 1.0.9 (2026-06-25)
- 交互 Bot 路径改用 inline keyboard 按钮（要牌/停牌/加倍），玩家点击按钮即可操作，无需输入文字。
- 新增 `callback_query` 事件处理：解析 `bj:{action}:{player_id}` 回调数据，校验发送者与游戏归属。
- 非终局操作（要牌未爆/未21点）返回带按钮的消息；终局结果（爆牌/赢/输/Blackjack）不返回按钮。
- 加倍按钮仅在前两张牌时可用，超过两张返回纯文本错误提示。
- 保留原有文字命令（要牌/停牌/加倍）作为 fallback，两种方式均可使用。

## 1.0.8 (2026-06-25)
- 支持同一群聊内多名玩家同时开局，各自独立进行。游戏状态键改为 `(chat_id, player_id)` 元组，消除玩家之间互相覆盖游戏的问题。
- 移除「这不是你的牌局哦」拦截，改为按玩家 ID 精确查找游戏。
- `session_close` 仅清除对应玩家的游戏，不再清空整个聊天的所有牌局。

## 1.0.7 (2026-06-25)
- 修复交互 Bot 模式下奖励由 Bot 发放的问题，奖励消息改用 `send_via: userbot_reply` 由管理员账号发放。

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
