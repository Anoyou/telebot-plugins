"""
自动点踩插件 (CAI - Auto Click Icon)
功能描述: 自动对目标群组中目标用户的发言进行点踩（添加表情反应）
文件名: cai.py
"""

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from typing import List, Dict, Optional, Union
from contextlib import suppress

from pagermaid.listener import listener
from pagermaid.hook import Hook
from pagermaid.enums import Message
from pagermaid.utils import logs

# 尝试导入自定义表情类型
try:
    from pyrogram.types import ReactionTypeEmoji, ReactionTypeCustomEmoji

    HAS_CUSTOM_EMOJI = True
except ImportError:
    HAS_CUSTOM_EMOJI = False
    logs.warning("[CAI] 当前环境不支持自定义表情类型，将使用标准表情")


# 配置文件路径；由 plugin.py 在账号启动时绑定到 ctx.data_dir。
LEGACY_CONFIG_FILE = Path(__file__).parent / "cai_config.json"
config_file: Path | None = None


def _copy_legacy_file_once(source: Path, target: Path) -> bool:
    """原子复制旧文件；目标已存在时保持原样。"""
    if target.exists() or not source.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as temp_file:
            temp_file.write(source.read_bytes())
            temp_file.flush()
            os.fsync(temp_file.fileno())
        try:
            os.link(temp_name, target)
        except FileExistsError:
            return False
        return True
    finally:
        Path(temp_name).unlink(missing_ok=True)


class CAIConfig:
    """自动点踩配置管理类"""

    def __init__(self):
        self.enabled: bool = False
        self.emojis: List[str] = ["👎"]  # 默认点踩表情列表
        self.is_premium: bool = False  # 是否为 Telegram Premium 会员
        self.targets: List[Dict] = []  # 目标列表
        self.stats: Dict = {"total_reacts": 0}  # 统计信息
        self.load()

    def load(self) -> None:
        """从文件加载配置"""
        if config_file is not None and config_file.exists():
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.enabled = data.get("enabled", False)
                    self.is_premium = data.get("is_premium", False)

                    # 兼容旧配置：如果只有一个 emoji 字符串，转换为列表
                    if "emoji" in data and "emojis" not in data:
                        self.emojis = [data.get("emoji", "👎")]
                    else:
                        self.emojis = data.get("emojis", ["👎"])

                    self.targets = data.get("targets", [])
                    self.stats = data.get("stats", {"total_reacts": 0})
                logs.info(
                    f"[CAI] 配置已加载，共 {len(self.targets)} 个目标，总点踩 {self.stats['total_reacts']} 次"
                )
            except Exception as e:
                logs.error(f"[CAI] 加载配置失败: {e}")
                self.enabled = False
                self.is_premium = False
                self.emojis = ["👎"]
                self.targets = []
                self.stats = {"total_reacts": 0}
        else:
            logs.info("[CAI] 配置文件不存在，使用默认配置")
            # 等 plugin.py 绑定 ctx.data_dir 后再保存，导入阶段不得写源码目录。

    def save(self) -> bool:
        """保存配置到文件"""
        try:
            if config_file is None:
                raise RuntimeError("TelePilot 未提供 ctx.data_dir")
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "enabled": self.enabled,
                        "is_premium": self.is_premium,
                        "emojis": self.emojis,
                        "targets": self.targets,
                        "stats": self.stats,
                    },
                    f,
                    indent=4,
                    ensure_ascii=False,
                )
            return True
        except Exception as e:
            logs.error(f"[CAI] 保存配置失败: {e}")
            return False

    def add_target(self, user_id: int, chat_id: int, rate_limit: int) -> str:
        """添加或更新目标配置"""
        # 检查是否已存在相同的配置
        for i, target in enumerate(self.targets):
            if target["user_id"] == user_id and target["chat_id"] == chat_id:
                # 更新现有配置
                self.targets[i]["rate_limit"] = rate_limit
                self.targets[i]["last_react_time"] = 0
                self.save()
                return f"✅ 已更新配置 #{i + 1}"

        # 添加新配置
        self.targets.append(
            {
                "user_id": user_id,
                "chat_id": chat_id,
                "rate_limit": rate_limit,
                "last_react_time": 0,
            }
        )
        self.save()
        return f"✅ 已添加配置 #{len(self.targets)}"

    def remove_target(self, index: int) -> str:
        """删除指定序号的目标配置"""
        if 1 <= index <= len(self.targets):
            removed = self.targets.pop(index - 1)
            self.save()
            return f"✅ 已删除配置 #{index}\n用户ID: `{removed['user_id']}`\n群组ID: `{removed['chat_id']}`"
        return "❌ 序号无效"

    def get_target(self, user_id: int, chat_id: int) -> Optional[Dict]:
        """获取指定用户和群组的配置"""
        for target in self.targets:
            if target["user_id"] == user_id and target["chat_id"] == chat_id:
                return target
        return None

    def update_last_react(self, user_id: int, chat_id: int) -> None:
        """更新最后点踩时间"""
        target = self.get_target(user_id, chat_id)
        if target:
            target["last_react_time"] = int(time.time())
            self.stats["total_reacts"] += 1
            self.save()

    def can_react(self, user_id: int, chat_id: int) -> bool:
        """检查是否可以点踩（冷却时间检查）"""
        target = self.get_target(user_id, chat_id)
        if not target:
            return False

        current_time = int(time.time())
        elapsed = current_time - target["last_react_time"]
        return elapsed >= target["rate_limit"]

    def list_targets(self) -> str:
        """列出所有目标配置"""
        if not self.targets:
            return "📋 **当前没有配置任何目标**"

        output = "📋 **目标配置列表：**\n\n"
        for i, target in enumerate(self.targets, 1):
            rate_limit_minutes = target["rate_limit"] // 60
            last_react = target["last_react_time"]
            if last_react == 0:
                time_info = "从未点踩"
            else:
                elapsed = int(time.time()) - last_react
                elapsed_minutes = elapsed // 60
                time_info = f"{elapsed_minutes} 分钟前"

            output += f"**#{i}**\n"
            output += f"   用户ID: `{target['user_id']}`\n"
            output += f"   群组ID: `{target['chat_id']}`\n"
            output += f"   间隔: {rate_limit_minutes} 分钟\n"
            output += f"   上次点踩: {time_info}\n\n"

        return output

    def get_stats(self) -> str:
        """获取统计信息"""
        output = "📊 **统计信息：**\n\n"
        output += f"功能状态: {'✅ 已启用' if self.enabled else '❌ 已禁用'}\n"
        output += f"会员状态: {'🌟 Premium' if self.is_premium else '👤 普通用户'}\n"

        emoji_display = " ".join(self.emojis)
        output += f"点踩表情: `{emoji_display}` ({len(self.emojis)}/{self.max_emojis()})\n"
        output += f"目标数量: `{len(self.targets)}`\n"
        output += f"累计点踩: `{self.stats['total_reacts']}` 次\n"
        return output

    def max_emojis(self) -> int:
        """获取可设置的最大表情数量"""
        return 3 if self.is_premium else 1

    def set_emojis(self, emojis: List[str]) -> str:
        """设置表情列表"""
        max_count = self.max_emojis()

        if len(emojis) > max_count:
            return f"❌ **表情数量超限！**\n\n当前为{'Premium' if self.is_premium else '普通'}用户，最多只能设置 {max_count} 个表情"

        self.emojis = emojis
        self.save()

        emoji_display = " ".join(emojis)
        return f"✅ **点踩表情已设置**\n\n当前表情: `{emoji_display}` ({len(emojis)}/{max_count})"


# 全局配置实例
config = CAIConfig()


def configure_data_dir(data_dir: Path | None, account_id: int) -> None:
    """绑定当前账号的持久化目录并重载状态。"""
    global config_file, config
    if data_dir is None:
        raise RuntimeError("TelePilot 未提供 ctx.data_dir，无法保存 cai 配置")
    account_dir = Path(data_dir) / str(int(account_id))
    account_dir.mkdir(parents=True, exist_ok=True)
    config_file = account_dir / "cai_config.json"
    _copy_legacy_file_once(LEGACY_CONFIG_FILE, config_file)
    config = CAIConfig()

# Premium 状态检测标记
_premium_checked = False


async def check_premium_status(bot) -> bool:
    """
    检测 bot 主人是否为 Telegram Premium 会员

    Args:
        bot: Pyrogram client 实例

    Returns:
        是否为 Premium 用户
    """
    global _premium_checked

    try:
        me = await bot.get_me()
        is_premium = getattr(me, "is_premium", False)

        # 更新配置
        if config.is_premium != is_premium:
            config.is_premium = is_premium
            config.save()

        _premium_checked = True

        status = "Premium 用户" if is_premium else "普通用户"
        logs.info(f"[CAI] 账户类型: {status}，最多可设置 {config.max_emojis()} 个表情")

        return is_premium

    except Exception as e:
        logs.error(f"[CAI] 检测 Premium 状态失败: {e}")
        return False


async def ensure_premium_checked(bot):
    """确保已检测 Premium 状态"""
    global _premium_checked
    if not _premium_checked:
        await check_premium_status(bot)


def get_reaction(emoji: str) -> Union[str, list]:
    """
    将表情字符串转换为正确的反应类型

    Args:
        emoji: 表情（可以是标准 emoji 或自定义表情 ID）

    Returns:
        如果支持自定义表情，返回 [ReactionTypeEmoji] 或 [ReactionTypeCustomEmoji]
        否则返回字符串 emoji
    """
    if not HAS_CUSTOM_EMOJI:
        return emoji

    # 判断是自定义表情 ID（纯数字）还是标准表情
    if emoji.isdigit():
        # 自定义表情 ID
        return [ReactionTypeCustomEmoji(custom_emoji_id=str(emoji))]
    else:
        # 标准表情
        return [ReactionTypeEmoji(emoji=emoji)]


def get_reactions(emojis: List[str]) -> list:
    """
    将表情列表转换为正确的反应类型列表

    Args:
        emojis: 表情列表

    Returns:
        反应类型列表
    """
    if not HAS_CUSTOM_EMOJI:
        return emojis

    reactions = []
    for emoji in emojis:
        if emoji.isdigit():
            reactions.append(ReactionTypeCustomEmoji(custom_emoji_id=str(emoji)))
        else:
            reactions.append(ReactionTypeEmoji(emoji=emoji))
    return reactions


# ==================== 生命周期钩子 ====================


@Hook.on_startup()
async def cai_startup():
    """插件启动时执行"""
    logs.info("[CAI] 自动点踩插件已加载")
    # 检测 Premium 状态将在首次调用命令时进行


@Hook.on_shutdown()
async def cai_shutdown():
    """插件关闭时执行"""
    logs.info("[CAI] 自动点踩插件已卸载")


# ==================== 管理命令 ====================


@listener(
    command="cai",
    description="自动点踩管理命令",
    parameters="<on|off|set|remove|list|emoji|stats>",
    is_plugin=True,
)
async def cai_command(message: Message):
    """处理 CAI 管理命令"""
    # 确保 Premium 状态已检测
    bot = getattr(message, "_client", None)
    if bot:
        await ensure_premium_checked(bot)

    # 获取命令参数
    text = message.arguments or ""

    # 如果没有参数，返回提示信息并在3秒后撤回
    if not text or text.strip() == "":
        await message.edit("请输入文本")
        await asyncio.sleep(3)
        await message.delete()
        return

    # 检查是否是帮助命令
    if text.strip().lower() == "help":
        await show_help(message)
        return

    cmd = text.lower().split()[0]

    # 启用功能
    if cmd == "on":
        config.enabled = True
        config.save()
        await message.edit("✅ **自动点踩功能已开启**\n\n已开始监听目标用户的发言")

    # 禁用功能
    elif cmd == "off":
        config.enabled = False
        config.save()
        await message.edit("❌ **自动点踩功能已关闭**")

    # 添加配置
    elif cmd == "set":
        params = text.split()
        if len(params) < 4:
            await message.edit(
                "❌ **参数错误！**\n\n"
                "使用方法: `,cai set <用户ID> <群组ID> <频率(秒)>`\n\n"
                "示例: `,cai set 123456789 -1001234567890 3600`"
            )
            return

        try:
            user_id = int(params[1])
            chat_id = int(params[2])
            rate_limit = int(params[3])

            if rate_limit < 60:
                await message.edit("❌ **频率限制不能小于 60 秒**")
                return

            result = config.add_target(user_id, chat_id, rate_limit)
            rate_limit_minutes = rate_limit // 60
            await message.edit(
                f"{result}\n\n"
                f"用户ID: `{user_id}`\n"
                f"群组ID: `{chat_id}`\n"
                f"频率限制: {rate_limit_minutes} 分钟"
            )
        except ValueError:
            await message.edit("❌ **ID格式错误！**\n\n请输入有效的数字ID")

    # 删除配置
    elif cmd == "remove":
        params = text.split()
        if len(params) < 2:
            await message.edit(
                "❌ **参数错误！**\n\n使用方法: `,cai remove <序号>`\n\n示例: `,cai remove 1`"
            )
            return

        try:
            index = int(params[1])
            result = config.remove_target(index)
            await message.edit(result)
        except ValueError:
            await message.edit("❌ **序号格式错误！**\n\n请输入有效的数字序号")

    # 查看配置
    elif cmd == "list":
        await message.edit(config.list_targets())

    # 设置表情
    elif cmd == "emoji":
        params = text.split(maxsplit=1)
        if len(params) < 2:
            # 确保 Premium 状态已检测
            bot = getattr(message, "_client", None)
            if bot:
                await ensure_premium_checked(bot)

            max_count = config.max_emojis()
            support_info = ""
            if HAS_CUSTOM_EMOJI:
                support_info = "\n**支持自定义表情：** ✅ 是\n   使用 `,get_reactions` 获取自定义表情ID\n"

            premium_note = ""
            if config.is_premium:
                premium_note = f"\n**Premium 用户最多可设置 {max_count} 个表情，用空格分隔**\n"

            await message.edit(
                f"❌ **参数错误！**\n\n"
                f"使用方法: `,cai emoji <表情>` {'或多个表情（空格分隔）' if config.is_premium else ''}\n\n"
                f"示例:\n"
                f" ` ,cai emoji 👎`  - 标准表情\n"
                f" ` ,cai emoji 5352930934257484526`  - 自定义表情ID{support_info}"
                f"{premium_note}"
                f"**当前最多可设置: {max_count} 个表情**\n"
                f"**提示：** 自定义表情ID是纯数字"
            )
            return

        # 解析表情列表
        emoji_params = params[1].strip()
        emojis = emoji_params.split()

        # 确保 Premium 状态已检测
        bot = getattr(message, "_client", None)
        if bot:
            await ensure_premium_checked(bot)

        # 设置表情
        result = config.set_emojis(emojis)
        await message.edit(result)

    # 统计信息
    elif cmd == "stats":
        await message.edit(config.get_stats())

    else:
        await show_help(message)


async def show_help(message: Message):
    """显示帮助信息"""
    support_status = "✅ 支持自定义表情" if HAS_CUSTOM_EMOJI else "❌ 不支持自定义表情"

    # 确保 Premium 状态已检测
    bot = getattr(message, "_client", None)
    if bot:
        await ensure_premium_checked(bot)

    max_emojis = config.max_emojis()
    premium_status = "🌟 Premium 用户" if config.is_premium else "👤 普通用户"

    # 根据 Premium 状态生成不同的表情说明
    if config.is_premium:
        emoji_examples = f"""**,cai emoji <表情1> [表情2] [表情3>]** - 设置点踩表情（最多{max_emojis}个）
  • 单个标准表情: `,cai emoji 👎`
  • 单个自定义表情ID: `,cai emoji 5352930934257484526`
  • 多个表情（空格分隔）: `,cai emoji 👎 😆 🤔`
  • 混合使用: `,cai emoji 👎 5352930934257484526 😆`
  • 自定义表情ID是纯数字"""
    else:
        emoji_examples = f"""**,cai emoji <表情>** - 设置点踩表情（最多{max_emojis}个）
  • 标准表情: `,cai emoji 👎`
  • 自定义表情ID: `,cai emoji 5352930934257484526`
  • 自定义表情ID是纯数字"""

    help_text = f"""**📖 自动点踩插件使用说明**

**当前状态：** {premium_status}，最多可设置 {max_emojis} 个表情

**管理命令：**

**,cai on** - 开启自动点踩功能
**,cai off** - 关闭自动点踩功能

**,cai set <用户ID> <群组ID> <频率(秒)>** - 添加目标配置
  • 频率单位为秒，3600 = 1小时
  • 示例: `,cai set 123456789 -1001234567890 3600`

**,cai remove <序号>** - 删除指定配置
  • 先用 ,cai list 查看序号
  • 示例: `,cai remove 1`

**,cai list** - 查看所有目标配置

{emoji_examples}

**,cai stats** - 查看统计信息

---

**💡 提示：**
• 使用 `,get_reactions` 回复带表情的消息获取自定义表情ID
• 使用 `,test_react <表情ID>` 测试发送表情反应
• 频率限制建议至少 60 秒（1分钟）
• 当前环境{support_status}
• Telegram Premium 用户可以同时添加多个反应"""

    await message.edit(help_text)


# ==================== 自动点踩监听器 ====================


@listener(is_plugin=True, incoming=True, outgoing=True, ignore_edited=True)
async def auto_react_handler(message: Message, bot):
    """
    自动点踩消息处理器

    检测目标用户的发言，在冷却时间过后自动点踩
    """
    # 检查功能是否启用
    if not config.enabled:
        return

    # 检查是否有发送者
    if not message.from_user:
        return

    # 检查是否在配置的目标列表中
    target = config.get_target(message.from_user.id, message.chat.id)
    if not target:
        return

    # 检查冷却时间
    if not config.can_react(message.from_user.id, message.chat.id):
        logs.info(f"[CAI] 用户 {message.from_user.id} 在冷却期内，跳过点踩")
        return

    # 确保 Premium 状态已检测
    await ensure_premium_checked(bot)

    # 执行点踩
    try:
        # 获取正确的反应类型列表
        reactions = get_reactions(config.emojis)

        # 使用 Message.react() 方法
        await message.react(reactions)

        # 更新最后点踩时间
        config.update_last_react(message.from_user.id, message.chat.id)

        # 获取用户信息用于日志
        user_name = (
            message.from_user.username
            or message.from_user.first_name
            or str(message.from_user.id)
        )
        emoji_display = " ".join(config.emojis)
        logs.info(
            f"[CAI] 已对用户 {user_name}({message.from_user.id}) 在群组 {message.chat.id} 进行点踩 [{emoji_display}]"
        )

    except AttributeError:
        # 如果 react 方法不存在，尝试使用 send_reaction（仅支持单个表情）
        try:
            # 如果配置了多个表情，只使用第一个
            emoji_to_use = config.emojis[0] if config.emojis else "👎"
            await bot.send_reaction(
                chat_id=message.chat.id, message_id=message.id, emoji=emoji_to_use
            )

            # 更新最后点踩时间
            config.update_last_react(message.from_user.id, message.chat.id)

            user_name = (
                message.from_user.username
                or message.from_user.first_name
                or str(message.from_user.id)
            )
            logs.info(
                f"[CAI] 已对用户 {user_name}({message.from_user.id}) 在群组 {message.chat.id} 进行点踩 [{emoji_to_use}]"
            )

        except Exception as e:
            logs.error(f"[CAI] 点踩失败: {e}")

    except Exception as e:
        logs.error(f"[CAI] 点踩失败: {e}")
