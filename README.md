# Telebot Plugins

Telebot 远程插件仓库。

## 插件列表

| 插件 | 指令 | 说明 |
|------|------|------|
| [blackjack](./blackjack/) | `,bj` | 经典21点纸牌游戏 |
| [guess_number](./guess_number/) | `,guess` | 群内猜数字 |
| [dice_battle](./dice_battle/) | `,dice` | 骰子比大小，支持对战 |
| [idiom_chain](./idiom_chain/) | `,cy` | 成语接龙，支持禁词规则 |
| [poetry_blank](./poetry_blank/) | `,poetry` | 古诗词填空抢答 |
| [chatter_challenge](./chatter_challenge/) | `,chat` | 话痨挑战，违反规则自动扣分 |
| [dice_grid_hunt](./dice_grid_hunt/) | `,dicegrid` | 九宫格骰子竞猜 |

## 安装方式

1. 在 Telebot 前端 → 插件中心 → 插件仓库
2. 添加仓库地址：`https://github.com/Anoyou/telebot-plugins.git`
3. 浏览插件列表，点击安装

## 开发新插件

每个插件一个目录，包含：
- `plugin.json` — 元数据（必填）
- `plugin.py` — 入口文件

参考 [插件开发指南](https://github.com/Anoyou/telebot/blob/main/docs/PLUGIN-DEV-GUIDE.md)
