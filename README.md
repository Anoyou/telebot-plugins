# TelePilot Plugins

TelePilot 远程插件仓库。

## 插件列表

| 插件 | 指令 | 说明 |
|------|------|------|
| [blackjack](./blackjack/) | `,bj` | 经典21点纸牌游戏 |
| [guess_number](./guess_number/) | `,guess 金额` | 群内猜数字 |
| [dice_battle](./dice_battle/) | `,dice` | 骰子比大小，支持对战 |
| [idiom_chain](./idiom_chain/) | `,cy 金额` | 成语接龙，支持禁词规则 |
| [poetry_blank](./poetry_blank/) | `,poetry 金额` | 古诗词填空抢答 |
| [chatter_challenge](./chatter_challenge/) | `,chat` | 话痨挑战，违反规则自动扣分 |
| [dice_grid_hunt](./dice_grid_hunt/) | `,dicegrid 金额` | 九宫格骰子图片竞猜 |
| [lottery_plus](./lottery_plus/) | `,lotto` | 群内彩票下注与开奖 |
| [mindreader_survival](./mindreader_survival/) | `,mind` | 多人读心生存赛 |
| [ten_half](./ten_half/) | `,10d` | 多人十点半纸牌游戏 |
| [game24](./game24/) | `,24d` | 官方可选 24 点竞速答题，支持交互 Bot 调度 |
| [math10](./math10/) | 交互规则启动 | 官方可选 10 以内算数题，支持交互 Bot 调度 |
| [chatgpt_image](./chatgpt_image/) | 插件配置 | 官方可选 ChatGPT2API 图片生成/编辑与 token 池 |
| [codex_image](./codex_image/) | 插件配置 | 官方可选 Codex 图片生成 |
| [bot_mute_guard](./bot_mute_guard/) | 无 | 指定群组非白名单 @bot、inline Bot 与 Bot 发言广告消息删除 |
| [sum](./sum/) | `,sum [数量]` | AI 群消息总结，支持快捷总结与定时任务 |
| [dead_revolver](./dead_revolver/) | `dr 金额` | 死亡左轮，群聊俄罗斯轮盘赌局 |

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
