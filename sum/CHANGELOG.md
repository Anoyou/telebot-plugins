# 更新日志

## 1.1.29 (2026-06-27)
- 按 TelePilot 0.33 插件开发文档更新远程元数据，将最低 TelePilot 版本提升到 `0.33.0`。
- 同步 `plugin.json` 与 `manifest.py` 版本，保持配置页模式和插件分类声明一致。

## 1.1.28 (2026-06-19)
- 按最新插件开发指南补充 `min_telepilot_version`，并将配置页模式从旧兼容别名 `schema` 更新为推荐的 `single`。


## 1.1.27 (2026-06-19)
- 修复总结素材中的发送者展示名可能读取账号本地联系人备注的问题；保存为联系人时优先使用公开 username（不带 @），没有 username 时回退用户 ID。
- 兼容新版 TelePilot 的统一公开展示名 helper，并保留旧环境兜底逻辑，避免备注进入 AI 总结上下文。

## 1.1.26 (2026-05-28)
- 重做词云中文取词逻辑：不再直接使用 2~5 字滑窗硬切整句，改为先按常见功能词/语气词切分候选短语，再保留更完整的关键词。
- 增加中文噪声词过滤，抑制“答案了的”“里已经有”这类跨助词拼出来的怪词。
- 词云候选词允许更长短语进入排序，避免画面里几乎只剩 2~4 字碎片。

## 1.1.25 (2026-05-28)
- 补回 `sum/assets/font.otf` 随包 Noto Sans CJK SC Regular 开源常规中文字体，VPS/Docker 未安装系统中文字体时也能直接生成词云。
- 保留 `sum/assets/font.ttc` / 系统 Noto Sans CJK 等字体兜底，允许部署方替换为自己的开源中文字体。

## 1.1.24 (2026-05-27)
- 词云字体改为随包字体优先：优先加载 `sum/assets/font.ttc` / `sum/assets/font.ttf`，用于 VPS/Docker 环境稳定渲染中文。
- 增加 Linux 常见开源中文字体路径兜底（Noto Sans CJK、思源黑体、文泉驿）。
- 找不到中文字体时不再生成方块图，改为提示安装 `fonts-noto-cjk` 或放置随包字体。
- 去掉词云图片左下角 footer 水印，仅保留纯词云内容。

## 1.1.23 (2026-05-27)
- 词云布局按 `cy.ts` 的 `layoutWords` 逻辑重写：使用 `angle = t * 0.38`、`radius = 5.2 * sqrt(t)` 的中心螺旋排布，高频词从中心开始尝试落位。
- 字号映射按 `cy.ts` 的 `buildWordItems` 逻辑调整为 `12 + ratio^0.7 * 68`，避免最大词过度膨胀、低频词过度拥挤。
- 词云画布调整为 `900x640`，边距、底部标题区和碰撞判断对齐原 `cy.ts` 实现。
- 保留包含关系去重，避免中文滑窗把同一短语的子串重复铺满画面。

## 1.1.22 (2026-05-27)
- 修正词云高频词判断：过滤系统提示/命令文案（如“正在获取消息”“条有效消息”“群组总结”等）避免污染热词统计。
- 增加热词去重规则：抑制包含关系且频次接近的滑窗碎片，减少同一句话重复占据高位。
- 词云字体切换为常规系统中文字体链，不再优先使用 redpack 装饰字体。

## 1.1.21 (2026-05-27)
- 修复 `--cy` 仍出现两条消息的问题：命令消息删除升级为“双通道强删”（`event.delete` + `client.delete_messages` 兜底）。
- 增加命令消息删除失败日志，便于定位权限或客户端能力差异导致的残留消息。

## 1.1.20 (2026-05-27)
- 参考 `cy.ts` 重做词云布局：高频词优先中心螺旋排布，中心词最大，低频词逐步外扩，整体观感更接近“中心热点”样式。
- 词云分词与权重策略对齐 `cy.ts`：英文词、中文 2~5 字滑窗、边缘加权与停用词过滤，减少杂词与噪声。
- `--cy` 发送改为空 caption 图片，避免额外文本气泡；发送成功后尝试删除原命令消息，仅保留词云图。
- 增加 `delete_message` 权限声明，用于清理命令消息。

## 1.1.19 (2026-05-27)
- 增强 `--cy` 识别兼容性：支持 `--cy`、`-cy`、`cy`、`词云`，并兼容全角/长横线输入。
- 新增原始命令文本兜底识别：即使上层参数解析未透传 `--cy`，也会按原始文本强制进入词云模式。
- 增加快捷总结参数解析日志，便于定位“明明写了 `--cy` 但未进入词云模式”的问题。

## 1.1.18 (2026-05-27)
- 新增只读 `usage_preview` 说明源，按 `{prefix}`/`{command}` 规则提供统一“使用说明”文本。
- 配合 TelePilot 通用配置页规范，`usage_preview`/`ai_usage_guide` 只用于顶部“使用说明”卡片，不再在配置字段区重复渲染。

## 1.1.17 (2026-05-27)
- 去掉配置区里的 `ai_usage_guide` 字段，避免与页面顶部“使用说明”卡片重复显示。
- 配置页恢复为单一顶部说明 + 业务配置字段，减少视觉冗余。

## 1.1.16 (2026-05-27)
- 修复快捷总结消息回退行为：`sum` 快捷路径改为仅编辑原命令消息，不再自动 reply 到新消息。
- 调整 `--cy` 语义为词云优先模式：`sum ... --cy` 只生成并发送热词云，不再继续输出 AI 文本总结。
- 快捷帮助文案同步更新 `--cy` 描述，避免误解为“词云 + 文本总结”。

## 1.1.15 (2026-05-27)
- 配置页字段顺序调整：将“使用说明（ai_usage_guide）”移动到配置 schema 顶部，符合 TelePilot 统一规范的顶部展示顺序。
- 其余功能保持不变。

## 1.1.14 (2026-05-27)
- 配置页“使用说明”文案同步到最新命令写法，补充时间段与词云参数示例：
  - `{prefix}sum 1h`
  - `{prefix}sum --time 90m`
  - `{prefix}sum 500 --cy`
  - `{prefix}sum 100 1h --cy`
- 将说明字段标题从“AI 配置说明”调整为“使用说明”，避免与 Provider 选择区混淆。

## 1.1.13 (2026-05-27)
- 新增快捷参数 `--cy`：支持在快捷总结时同时生成并发送热词云图片（例如 `sum 2000 --cy`）。
- 词云生成复用现有图文能力，优先使用 `redpack-byRBQ/assets/font.ttf` 渲染中文，避免 VPS 缺字问题。
- 新增时间段与词云组合用法支持（如 `sum 100 1h --cy`、`sum --time 90m --cy`）。
- Manifest / plugin.json 增加 `send_file` 权限声明，用于发送词云 PNG。

## 1.1.12 (2026-05-27)
- 快捷总结新增按时间段触发：支持 `sum 1h`、`sum 1d`、`sum --time 30m` 等语法。
- 支持组合范围：`sum 100 1h` 表示“最近 1 小时内最多读取 100 条消息”。
- 快捷总结按时间段执行时，输出中的 `{message_count}` 将显示本次实际参与总结的消息条数。
- 帮助文案补充时间段用法示例，避免只看到“按条数”模式。

## 1.1.11 (2026-05-27)
- 改为通过 TelePilot `ctx.ai.complete()` 调用平台 LLM，不再直接 import 后端数据库和 LLM 私有服务。
- Manifest / plugin.json 声明 `ai_text` 权限，配合 TelePilot 0.25 插件 AI facade 运行。
- `sum config providers` 改为读取 `ctx.ai.list_providers()`，避免触碰平台内部 Provider 表。

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
