# 更新日志

## 1.1.4 (2026-05-26)
- 兼容前缀后误加空格的写法，例如 `. sum`、`。 sum config providers`，避免命令没有任何反应。
- 兼容中文全角句号/逗号作为快捷触发前缀，方便移动端中文输入法场景。

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
