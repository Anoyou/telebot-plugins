# TelePilot Plugins

TelePilot 远程插件仓库。

## 插件列表

| 插件 | 指令 | 说明 |
|------|------|------|
| [blackjack](./blackjack/) | `{prefix}bj` | 经典21点纸牌游戏 |
| [guess_number](./guess_number/) | `{prefix}guess 金额` | 群内猜数字 |
| [dice_battle](./dice_battle/) | `{prefix}dice` | 骰子比大小，支持对战 |
| [idiom_chain](./idiom_chain/) | `{prefix}cy 金额` | 成语接龙，支持禁词规则 |
| [poetry_blank](./poetry_blank/) | `{prefix}poetry 金额` | 古诗词填空抢答 |
| [chatter_challenge](./chatter_challenge/) | `{prefix}chat` | 话痨挑战，违反规则自动扣分 |
| [dice_grid_hunt](./dice_grid_hunt/) | `{prefix}dicegrid 金额` | 九宫格骰子图片竞猜 |
| [lottery_plus](./lottery_plus/) | `{prefix}lotto` | 群内彩票下注与开奖 |
| [mindreader_survival](./mindreader_survival/) | `{prefix}mind` | 多人读心生存赛 |
| [ten_half](./ten_half/) | 交互规则关键词 | 多人十点半纸牌游戏 |
| [auto_reply](./auto_reply/) | 规则配置 | 插件库推荐自动回复，按规则匹配关键词或正则后自动回复 |
| [autorepeat](./autorepeat/) | 规则配置 | 插件库推荐自动复读，多人发送相同内容时自动复读 |
| [game24](./game24/) | `{prefix}24d` | 插件库维护 24 点竞速答题，支持交互 Bot 调度 |
| [math10](./math10/) | 交互规则启动 | 插件库维护 10 以内算数题，支持交互 Bot 调度 |
| [chatgpt_image](./chatgpt_image/) | 插件配置 | 插件库维护 ChatGPT2API 图片生成/编辑与 token 池 |
| [codex_image](./codex_image/) | 插件配置 | 插件库维护 Codex 图片生成 |
| [AI-Chat](./ai-chat/) | `{prefix}ask` | AI-Chat 聊天与消息解释助手，调用 TelePilot 已配置的 AI Provider |
| [bot_mute_guard](./bot_mute_guard/) | 无 | 指定群组非白名单 @bot、inline Bot 与 Bot 发言广告消息删除 |
| [sum](./sum/) | `{prefix}sum [数量]` | AI 群消息总结，支持快捷总结与定时任务 |
| [dead_revolver](./dead_revolver/) | `dr 金额` | 死亡左轮，群聊俄罗斯轮盘赌局 |
| [quick_qa](./quick_qa/) | `{prefix}quickqa` | 快问快答积分淘汰赛，支持 URL + AI 生成题库 |
| [lucky_redpack](./lucky_redpack/) | `{prefix}rp 发财 88888 10` | 拼手气口令红包，财富密码每次领取后随机刷新 |
| [ai_redpacket](./ai_redpacket/) | `{prefix}airp create 总金额 [题目数]` | 基于 URL + AI 题库的三选一答题红包，Interaction Bot 答题、UserBot 发奖 |
| [reply_anchor_test](./reply_anchor_test/) | `{prefix}send 用户ID 金额` | 测试 userbot 搜索目标用户近期发言并回复 `+金额` |
| [random_benefit](./random_benefit/) | `{prefix}随机福利 on/off` | 随机引用指定群组发言回复自定义福利语 |

## byRBQ 迁移插件（Pagermaid → TelePilot）

以下插件已按最新 TelePilot 远程插件结构迁移并重命名为 `原名-byRBQ`：

- [ais-byRBQ](./ais-byRBQ/)
- [cai-byRBQ](./cai-byRBQ/)
- [get_reactions-byRBQ](./get_reactions-byRBQ/)
- [gi2-byRBQ](./gi2-byRBQ/)
- [jpm-byRBQ](./jpm-byRBQ/)
- [jpmai-byRBQ](./jpmai-byRBQ/)
- [luckydraw-byRBQ](./luckydraw-byRBQ/)
- [pixivshow-byRBQ](./pixivshow-byRBQ/)
- [redpack-byRBQ](./redpack-byRBQ/)
- [sar-byRBQ](./sar-byRBQ/)
- [sfl-byRBQ](./sfl-byRBQ/)
- [share_plugins-byRBQ](./share_plugins-byRBQ/)

每个迁移插件目录都包含：
- `plugin.json`（安装阶段元数据）
- `manifest.py`（运行期 Manifest）
- `plugin.py`（TelePilot 插件入口）
- `__init__.py`（导出 `PLUGIN_CLASS` / `MANIFEST`）
- `legacy_main.py`（保留原 Pagermaid 实现供后续功能深度迁移）

迁移插件测试工具：
- 冒烟脚本：`scripts/smoke_check_byrbq.py`
- 手工清单：`docs/BYRBQ-MANUAL-TEST-CHECKLIST.md`

## 安装方式

1. 在 TelePilot 前端 → 插件中心 → 插件仓库
2. 添加仓库地址：`https://github.com/Anoyou/telebot-plugins.git`
3. 浏览插件列表，点击安装

## 开发新插件

每个插件一个目录，包含：
- `plugin.json` — 元数据（必填）
- `manifest.py` — 运行期 Manifest（必填）
- `plugin.py` — 入口文件
- `__init__.py` — 导出 `PLUGIN_CLASS` 和 `MANIFEST`（必填）

参考 [TelePilot 0.33 插件开发文档](https://github.com/Anoyou/Telebot/tree/codex/0.33-interaction-framework/docs)
