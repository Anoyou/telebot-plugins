"""
分享插件插件 - 将现有插件以文件形式分享
使用方式: ,share_plugins 或 /share_plugins (sudo)
"""

import os
from pathlib import Path
from typing import List, Dict, Optional

from pagermaid.listener import listener
from pagermaid.hook import Hook
from pagermaid.enums import Message, Client
from pagermaid.utils import logs


# 插件目录路径
PLUGIN_DIR = Path(__file__).parent


class PluginManager:
    """插件管理类"""

    def __init__(self):
        self.plugin_list: List[str] = []
        self.refresh_plugin_list()

    def refresh_plugin_list(self):
        """刷新插件列表"""
        try:
            # 获取所有 .py 文件（排除 __ 开头的文件）
            self.plugin_list = [
                f.name
                for f in PLUGIN_DIR.iterdir()
                if f.is_file() and f.suffix == ".py" and not f.name.startswith("__")
            ]
            logs.info(
                f"[SharePlugins] 已加载 {len(self.plugin_list)} 个插件: {self.plugin_list}"
            )
        except Exception as e:
            logs.error(f"[SharePlugins] 刷新插件列表失败: {e}")
            self.plugin_list = []

    def get_plugin_list(self) -> List[str]:
        """获取插件列表"""
        return self.plugin_list

    def get_plugin_file_path(self, index: int) -> Optional[Path]:
        """根据索引获取插件文件路径（索引从1开始）"""
        if 1 <= index <= len(self.plugin_list):
            plugin_name = self.plugin_list[index - 1]
            return PLUGIN_DIR / plugin_name
        return None

    def format_plugin_list(self) -> str:
        """格式化插件列表为可读文本"""
        if not self.plugin_list:
            return "❌ 未找到任何插件文件"

        lines = ["**📋 可用插件列表：**\n"]
        for i, plugin_name in enumerate(self.plugin_list, 1):
            lines.append(f"**{i}.** `{plugin_name}`")
        lines.append(f"\n💡 请回复对应的数字序号选择要分享的插件")
        lines.append(f"\n⚠️ 注意：操作消息会被撤回，插件文件会直接发送到群组")

        return "\n".join(lines)


# 全局实例
plugin_manager = PluginManager()


@listener(
    command="share_plugins",
    description="分享插件 - 列出插件或直接分享指定插件",
    parameters="[序号]",
    is_plugin=True,
)
async def share_plugins_command(message: Message, bot: Client):
    """处理 share_plugins 命令"""
    # 刷新插件列表
    plugin_manager.refresh_plugin_list()

    # 检查是否有插件
    if not plugin_manager.get_plugin_list():
        await message.edit("❌ 未找到任何插件文件")
        return

    # 检查参数
    args = message.arguments.strip() if message.arguments else ""

    if args:
        # 如果有参数，尝试解析为插件序号
        await handle_plugin_selection(message, bot, args)
    else:
        # 没有参数，显示插件列表
        list_text = plugin_manager.format_plugin_list()
        await message.edit(list_text)


async def handle_plugin_selection(message: Message, bot: Client, args: str):
    """处理插件选择"""
    # 尝试解析参数为数字
    try:
        plugin_index = int(args)
    except ValueError:
        await message.edit(f"❌ 无效的序号：`{args}`\n请输入纯数字序号")
        return

    # 获取插件文件路径
    plugin_file = plugin_manager.get_plugin_file_path(plugin_index)

    if not plugin_file:
        total_count = len(plugin_manager.get_plugin_list())
        await message.edit(f"❌ 序号超出范围\n请输入 1-{total_count} 之间的数字")
        return

    # 撤回操作消息
    try:
        await message.delete()
        logs.info(f"[SharePlugins] 撤回操作消息: {message.id}")
    except Exception as e:
        logs.error(f"[SharePlugins] 撤回消息失败: {e}")
        # 如果撤回失败，编辑消息告知用户
        await message.edit(f"⚠️ 撤回消息失败: {e}")
        return

    # 发送插件文件
    try:
        # 使用 bot 实例发送文件到当前聊天（直接传递文件路径）
        await bot.send_document(
            chat_id=message.chat.id,
            document=str(plugin_file),
            caption=f"📦 分享插件: `{plugin_file.name}`",
        )
        logs.info(f"[SharePlugins] 成功分享插件: {plugin_file.name}")

    except FileNotFoundError:
        # 尝试发送消息告知用户（因为之前的消息已被删除）
        await bot.send_message(
            chat_id=message.chat.id, text=f"❌ 插件文件不存在: `{plugin_file.name}`"
        )
        logs.error(f"[SharePlugins] 文件不存在: {plugin_file}")
    except Exception as e:
        # 尝试发送消息告知用户
        await bot.send_message(chat_id=message.chat.id, text=f"❌ 发送文件失败: {e}")
        logs.error(f"[SharePlugins] 发送文件失败: {e}")


@Hook.on_startup()
async def plugin_startup():
    """插件初始化"""
    plugin_manager.refresh_plugin_list()
    logs.info("分享插件插件已加载")


@Hook.on_shutdown()
async def plugin_shutdown():
    """插件关闭"""
    logs.info("分享插件插件已卸载")
