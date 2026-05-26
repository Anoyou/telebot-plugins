# 更新日志

## 1.1.10 (2026-05-26)
- 修复模板热更新不及时：每次命令入口都同步 `ctx.config`，配置页修改 `message_template` 后立即生效。
- 调整快捷总结交互：`.sum` / `.sum 100` 的最终结果改为直接编辑原命令消息，不再回复“正在获取消息并总结...”这条消息。

## 1.1.9 (2026-05-26)
- 新增结果输出模板 `message_template` 配置项，可自定义总结结果消息格式。
- 新增 `template_placeholders` 与 `template_preview` 只读字段，按 TelePilot 通用模板预览规则展示占位符说明和渲染预览。
- 总结发送逻辑改为统一按模板渲染，支持 `{summary}`、`{chat_display}`、`{chat_id}`、`{time}`、`{message_count}` 占位符。
- TelePilot 模型下拉新增文本模型约束，避免在总结模块里误选 `gpt-image-*` 一类图像模型。
- 最低 TelePilot 版本提升到 `0.24.2`，确保插件独立配置页可正确加载 Provider/Model 下拉和模板预览示例。

## 1.1.8 (2026-05-26)
- 声明 TelePilot 新增的 `resolve_entity` 能力，配合平台沙箱按需放行 `client.get_entity`，修复 `.sum 100` 在真实事件流程中仍被权限拦截的问题。
- 配置页 Provider 和 Model 字段改为动态下拉选择，不再要求手动输入 Provider ID 或模型名。
- 固定 TelePilot Provider 与模型覆盖改为真正可选配置，默认留空走 TelePilot 自动路由。
- 最低 TelePilot 版本提升到 `0.24.1`，确保平台已支持 `resolve_entity` 和 AI 下拉控件。

## 1.1.7 (2026-05-26)
- 修复 Telethon 事件懒加载聊天属性内部触发 `client.get_entity` 导致 installed 插件沙箱报错的问题。
- 事件目标解析改为只读取已存在的原始属性，读取失败直接回退到当前 `chat_id`。
- 群名展示辅助不再调用 Telegram 客户端解析接口，避免影响 `.sum 100` 主流程。

## 1.1.6 (2026-05-26)
- 修复 installed 插件沙箱下调用 `client.get_entity` 被权限拦截的问题。
- 移除定时任务兜底解析里的 `iter_dialogs/get_dialogs` 调用，严格只使用 Manifest 已声明的 `read_chat` 能力范围。
- 群名称和公开用户名解析改为可选辅助信息，无法读取时直接回退显示群 ID，不影响总结流程。

## 1.1.5 (2026-05-26)
- 移除模块内 OpenAI/Gemini API Key、Base URL、Model 和自定义 AI Provider 配置，只保留调用 TelePilot 已配置的 AI。
- 新增配置页定时总结任务 `scheduled_tasks_json`，支持通过插件配置维护 `chatId`、`interval`、消息数、推送目标、提示词、折叠和禁用状态。
- 定时任务执行时会在当前账号 dialogs 中补充解析 `-100...` 群 ID，减少无事件上下文时找不到实体的问题。
- 帮助文案和 `sum config` 命令同步调整为 TelePilot AI 自动路由/固定 Provider 逻辑。

## 1.1.4 (2026-05-26)
- 修复快捷总结当前群时 Telethon 偶发无法用裸 `-100...` 群 ID 找到实体的问题，优先复用命令事件自带的 InputPeer/Chat。
- `sum debug` 与总结结果回发同样使用当前事件实体候选，减少 `Cannot find any entity` 报错。
- 当定时任务仍无法解析目标群时，错误提示改为说明账号入群状态和公开用户名/链接要求。

## 1.1.3 (2026-05-26)
- 在配置页补充模块内 OpenAI/Gemini 的 API Key、Base URL、Model 字段，选择对应调用方式后无需再猜命令。
- 增加 AI 配置说明，明确 TelePilot 自动路由不需要选择具体 Provider，固定 Provider 属于高级用法。
- 新增 `sum config providers` 查看 TelePilot 已配置 LLM Provider，并支持 `sum config set telepilot provider auto` 恢复自动路由。

## 1.1.2 (2026-05-26)
- 修复当前群 ID 被当作字符串用户名解析的问题，避免 `.sum 100` 在 `-100...` 群组里报 `Cannot find any entity`。
- TelePilot 内置 AI 默认改为调用平台官方 AI 路由，根据 Provider 标签、API Key 状态和成本档自动选择模型。
- 将配置页文案调整为“AI 调用方式 / TelePilot 自动路由”，避免误解为在模块里手动选择具体 Provider。

## 1.1.1 (2026-05-26)
- 将通用配置页里的默认 AI 来源改为下拉选择，默认推荐 TelePilot 内置 AI，避免误以为必须手动输入配置名。
- 明确高级 AI 配置 JSON 只用于外部兼容接口预置，普通使用无需填写。
- 修正快捷总结数量边界，`.sum 100` 表示本次总结最近 100 条消息，数量会限制在 1 到单次最多读取消息数之间。

## 1.1.0 (2026-05-26)
- 新增 `telepilot` 内置 AI 配置，默认优先调用 TelePilot 已配置的 LLM Provider，无需在模块内重复填写 API Key。
- 支持通过 `sum config set telepilot provider <Provider ID或名称>` 指定平台内置 Provider，并可用 `model` 覆盖本次总结模型。
- 保留原 OpenAI/Gemini 兼容配置作为回退路径，旧配置和任务可继续使用。

## 1.0.0 (2026-05-22)
- 新增群消息总结远程模块，兼容迁移 TeleBox `sum.ts` 的核心能力。
- 支持快捷总结当前聊天最近消息、AI 服务商配置、推荐提示词、折叠输出、最大输出长度和调试预览。
- 支持定时总结任务的新增、列表、删除、立即执行、启用禁用、排序和局部编辑。
- 远程模块运行时仅使用 TelePilot 公开 `PluginContext`、`ctx.client` 和 `ctx.scheduler` 能力，不依赖 TeleBox 私有全局客户端。
- 外部 OpenAI/Gemini 兼容接口调用统一设置超时，错误提示不会输出 API Key。
