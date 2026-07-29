# BYRBQ 插件手工测试清单（高风险优先）

适用范围：
- `ais-byRBQ`
- `cai-byRBQ`
- `get_reactions-byRBQ`
- `gi2-byRBQ`
- `jpm-byRBQ`
- `jpmai-byRBQ`
- `luckydraw-byRBQ`
- `pixivshow-byRBQ`
- `redpack-byRBQ`
- `sar-byRBQ`
- `sfl-byRBQ`
- `share_plugins-byRBQ`

## 0. 通用前置检查（每个插件都做）

1. 在 Telebot 插件管理中安装插件，确认安装成功。
2. 全局启用插件，并为目标账号启用插件。
3. 打开插件日志，确认 `on_startup` 无异常堆栈。
4. 执行主命令（见各插件章节），确认能正常响应。
5. 重载插件，确认 `on_shutdown -> on_startup` 无异常，命令仍可用。

## 1. 高风险能力矩阵

- 网络调用：`ais / gi2 / jpmai / pixivshow / luckydraw / redpack`
- 媒体发送：`pixivshow / gi2 / share_plugins / redpack / sar / sfl`
- 表情反应：`cai / get_reactions`
- 按钮点击：`redpack`（自动确认场景）
- 群聊监听：`jpm / jpmai / luckydraw / sar / sfl / redpack`

## 2. 插件逐项用例

## ais-byRBQ

1. `{prefix}ais help` 显示帮助。
2. `{prefix}ais` 纯文本提问，返回 AI 内容。
3. 配置 API 后测试联网搜索路径（提问实时问题）。
4. 模型切换命令验证（如有）。
5. MCP 子命令（如配置了 MCP）验证 list/add/remove。

验收：
- 超时、失败提示友好。
- 不出现重复回复或重复删消息。

## cai-byRBQ

1. `,cai on` 启用。
2. `,cai set <user_id> <chat_id> <cooldown>` 添加目标。
3. 被监控用户发言后自动反应。
4. `,cai emoji 👎`、多 emoji（Premium）验证。

验收：
- 冷却时间生效。
- 非目标用户不触发。

## get_reactions-byRBQ

1. 回复带反应消息执行 `,get_reactions`。
2. 回复任意消息执行 `,test_react 👎`。

验收：
- 能正确读取反应信息。
- 不支持反应时返回可理解错误。

## gi2-byRBQ

1. `,gi2 <prompt>` 生图。
2. 回复图片执行 `,gi2 <prompt>` 改图。
3. 失败分支（错误 key/超时）提示验证。

验收：
- 发送图片成功。
- 回复链路正确。

## jpm-byRBQ

1. 配置关键词规则（目标群/用户）。
2. 触发关键词后自动回复。
3. 频率限制与开关命令验证。

验收：
- 只在命中条件时触发。
- 频控生效。

## jpmai-byRBQ

1. `,jpmai api ...`、`model`、`on/off` 基础命令。
2. 关键词触发 AI 回复。
3. 单人/双人模式验证。

验收：
- 权限控制（owner）生效。
- API 失败时错误信息可读。

## luckydraw-byRBQ

1. `,ldraw on` 启用群。
2. 白名单 bot 消息口令识别。
3. 自动发送口令。
4. 中奖贴纸（如配置）验证。

验收：
- 非白名单 bot 不触发。
- 同口令不会重复刷。

## pixivshow-byRBQ

1. `,pixiv` 拉普通图。
2. `,pixivr18` 拉 R18 图并遮罩。
3. 指定数量参数验证。

验收：
- 单图/多图发送都正常。
- R18 标记逻辑正确。

## redpack-byRBQ

1. `{prefix}redpack` 基础发包。
2. `{prefix}redpack img` 数学题红包流程。
3. 领取、结算消息、榜单流程。
4. 转账确认自动点击（如果场景可复现）。

验收：
- 关键状态机不丢状态。
- 并发领取不重复记账。

## sar-byRBQ

1. `,sar on` 启用当前群。
2. 他人用贴纸回复你的消息，自动回同贴纸。
3. `,sar off` 关闭验证。

验收：
- 仅在启用群触发。
- 非回复场景不触发。

## sfl-byRBQ

1. `,sfl on`。
2. 回复贴纸执行 `,sfl set` 绑定。
3. 群里发送目标贴纸触发跟随。

验收：
- 群级配置隔离。
- 未配置贴纸时不误触发。

## share_plugins-byRBQ

1. `,share_plugins` 查看列表。
2. `,share_plugins <序号>` 发送插件文件。

验收：
- 序号越界报错正常。
- 文件发送成功。

## 3. 回归与稳定性

1. 连续重载 3 次，重复执行关键命令。
2. 每个插件至少执行 1 次命令 + 1 次消息监听路径。
3. 观察日志是否出现以下关键错误：
   - `AttributeError`（兼容对象缺失字段）
   - `TypeError`（函数参数个数不匹配）
   - 网络超时后未捕获异常

## 4. 结果记录模板

- 插件名：
- 版本：
- 测试账号：
- 通过用例：
- 失败用例：
- 错误日志摘录：
- 是否可上线：是 / 否
