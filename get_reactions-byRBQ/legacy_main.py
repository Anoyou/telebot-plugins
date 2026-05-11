"""
表情获取辅助命令
用于测试环境是否支持自定义表情反应
文件名: get_reactions.py
"""

from pagermaid.listener import listener
from pagermaid.enums import Message
from pagermaid.utils import logs

# 检测是否支持自定义表情类型
try:
    from pyrogram.types import ReactionTypeEmoji, ReactionTypeCustomEmoji
    HAS_CUSTOM_EMOJI = True
    logs.info("[get_reactions] 检测到自定义表情支持")
except ImportError:
    HAS_CUSTOM_EMOJI = False
    logs.info("[get_reactions] 未检测到自定义表情支持，将使用标准表情")


@listener(
    command="get_reactions",
    description="获取消息的表情反应详情（用于获取自定义表情ID）",
    parameters="<回复一条带表情反应的消息>",
    is_plugin=True,
)
async def get_reactions(message: Message):
    """
    获取消息的表情反应详情

    使用方法：
    1. 在群组中找一条带表情反应的消息
    2. 回复该消息，发送 ,get_reactions
    3. 查看返回的表情信息
    """
    reply = message.reply_to_message

    if not reply:
        return await message.edit("❌ 请回复一条带表情反应的消息")

    # 获取消息的 reactions
    reactions = reply.reactions

    if not reactions:
        return await message.edit("❌ 这条消息没有表情反应")

    # 添加环境支持信息
    support_status = "✅ 支持自定义表情" if HAS_CUSTOM_EMOJI else "❌ 不支持自定义表情"
    output = f"📋 **表情反应信息：**\n环境支持: {support_status}\n\n"

    for reaction in reactions.reactions:
        count = reaction.count

        # 标准表情
        if hasattr(reaction, 'emoji') and reaction.emoji:
            emoji = reaction.emoji
            output += f"🔹 **标准表情**: `{emoji}`\n"
            output += f"   数量: `{count}`\n\n"

        # 自定义表情
        elif hasattr(reaction, 'custom_emoji_id') and reaction.custom_emoji_id:
            emoji_id = reaction.custom_emoji_id
            output += f"🔹 **自定义表情ID**: `{emoji_id}`\n"
            output += f"   数量: `{count}`\n\n"

        else:
            # 未知类型，打印所有属性帮助调试
            output += f"🔹 **未知表情类型**\n"
            output += f"   数量: `{count}`\n"
            output += f"   可用属性: `{dir(reaction)}`\n\n"

    output += "💡 **提示**:\n"
    output += "- 如果显示 `标准表情`，表示该表情是 Unicode 字符\n"
    output += "- 如果显示 `自定义表情ID`，表示支持自定义表情\n"
    output += "- 复制表情或ID用于设置点踩表情"

    await message.edit(output)


@listener(
    command="test_react",
    description="测试发送表情反应",
    parameters="[表情或自定义表情ID]",
    is_plugin=True,
)
async def test_react(message: Message):
    """
    测试发送表情反应

    用于测试当前环境是否支持 send_reaction API

    支持标准表情: 👎 👍 💀
    支持自定义表情ID: 5352930934257484526 (纯数字)
    """
    emoji_input = message.arguments if message.arguments else "👎"

    reply = message.reply_to_message
    if not reply:
        return await message.edit("❌ 请回复一条消息来测试表情反应")

    # 准备反应类型
    reaction = None
    emoji_type = "标准表情"

    if HAS_CUSTOM_EMOJI:
        # 判断是自定义表情 ID（纯数字）还是标准表情
        if emoji_input.strip().isdigit():
            reaction = [ReactionTypeCustomEmoji(custom_emoji_id=str(emoji_input.strip()))]
            emoji_type = "自定义表情ID"
        else:
            reaction = [ReactionTypeEmoji(emoji=emoji_input)]
    else:
        # 不支持自定义表情，使用字符串
        reaction = emoji_input

    try:
        # 尝试发送反应
        await reply.react(reaction)
        await message.edit(f"✅ 成功发送表情反应\n\n表情: `{emoji_input}`\n类型: {emoji_type}\n环境支持: {'✅ 自定义表情' if HAS_CUSTOM_EMOJI else '❌ 仅标准表情'}")

    except AttributeError:
        # 如果 react 方法不存在，尝试使用 send_reaction
        try:
            from pagermaid.enums import bot
            await bot.send_reaction(
                chat_id=reply.chat.id,
                message_id=reply.id,
                emoji=emoji_input
            )
            await message.edit(f"✅ 成功发送表情反应 (使用 send_reaction)\n\n表情: `{emoji_input}`")
        except Exception as e:
            await message.edit(f"❌ 发送表情反应失败: `{str(e)}`\n\n当前环境可能不支持表情反应功能")
    except Exception as e:
        await message.edit(f"❌ 发送表情反应失败: `{str(e)}`")
