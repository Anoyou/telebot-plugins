"""青娃PT (qingwapt.com) 置顶促销模块。

功能：
  - 在青娃PT上为种子设置置顶促销
  - 支持自定义促销类型、时长、竞价和奖励
  - 支持查询促销历史

用法：
  {prefix}pt <种子ID> [选项]

选项（可选，用空格分隔）：
  free        促销类型：free=Free, 2x=2X Free（默认 free）
  1d/2d/3d/7d 时长：1天/2天/3天/7天（默认 1天）
  bid=100     竞价蝌蚪（越高排名越靠前）
  reward=50   奖励蝌蚪（吸引下载者）
  users=10    奖励人数

示例：
  {prefix}pt 12345                    # 默认：Free 1天
  {prefix}pt 12345 free 7d            # Free 7天
  {prefix}pt 12345 2x 3d bid=200      # 2X Free 3天 竞价200
  {prefix}pt 12345 free 7d bid=100 reward=50 users=10

查询：
  {prefix}ptinfo <种子ID>

注意：
  - 需要站点账号权限（消耗蝌蚪）
  - Cookie 需要在配置中设置
"""
from __future__ import annotations

import re
from typing import Any

from app.worker.plugins.base import Plugin, PluginContext, register

# 促销类型映射
PROMO_TYPES = {
    "free": "2",
    "2x": "4",
    "2xfree": "4",
}

# 时长映射（天 -> 小时）
DURATION_MAP = {
    "1d": "24",
    "2d": "48",
    "3d": "72",
    "7d": "168",
    "1": "24",
    "2": "48",
    "3": "72",
    "7": "168",
}


def _parse_args(args: list[str]) -> dict[str, str]:
    """解析命令参数。"""
    result = {
        "promotion_type": "2",  # 默认 Free
        "duration": "24",       # 默认 1天
        "competitive_bonus": "",
        "reward_bonus": "",
        "reward_user_num": "",
    }

    for arg in args:
        arg_lower = arg.lower()

        # 促销类型
        if arg_lower in PROMO_TYPES:
            result["promotion_type"] = PROMO_TYPES[arg_lower]

        # 时长
        elif arg_lower in DURATION_MAP:
            result["duration"] = DURATION_MAP[arg_lower]

        # 键值对参数
        elif "=" in arg:
            key, _, value = arg.partition("=")
            key = key.lower().strip()
            value = value.strip()

            if key == "bid":
                result["competitive_bonus"] = value
            elif key == "reward":
                result["reward_bonus"] = value
            elif key == "users":
                result["reward_user_num"] = value

    return result


def _format_params(params: dict[str, str]) -> str:
    """格式化参数为可读文本。"""
    lines = []

    # 促销类型
    promo_name = "Free" if params["promotion_type"] == "2" else "2X Free"
    lines.append(f"类型：{promo_name}")

    # 时长
    hours = int(params["duration"])
    days = hours // 24
    lines.append(f"时长：{days}天")

    # 竞价
    if params["competitive_bonus"]:
        lines.append(f"竞价：{params['competitive_bonus']} 蝌蚪")

    # 奖励
    if params["reward_bonus"]:
        lines.append(f"奖励：{params['reward_bonus']} 蝌蚪")

    # 奖励人数
    if params["reward_user_num"]:
        lines.append(f"奖励人数：{params['reward_user_num']}人")

    return "\n".join(lines)


@register
class PTPromotePlugin(Plugin):
    key = "pt_promote"
    display_name = "PT 种子促销"
    message_channels = {"outgoing"}
    owner_only = True

    def __init__(self) -> None:
        self.commands = {
            "pt": self._handle_promote,
            "促销": self._handle_promote,
            "ptinfo": self._handle_info,
        }

    async def _handle_promote(
        self,
        client: Any,
        event: Any,
        args: list[str],
        account_id: int,
        ctx: PluginContext,
    ) -> None:
        """处理置顶促销命令。"""
        if not args:
            prefix = ctx.config.get("command", "pt")
            await event.edit(
                f"用法：{prefix} <种子ID> [选项]\n\n"
                f"选项：\n"
                f"  free/2x — 促销类型（默认 free）\n"
                f"  1d/2d/3d/7d — 时长（默认 1天）\n"
                f"  bid=100 — 竞价蝌蚪\n"
                f"  reward=50 — 奖励蝌蚪\n"
                f"  users=10 — 奖励人数\n\n"
                f"示例：{prefix} 12345 free 7d bid=100"
            )
            return

        torrent_id = args[0]
        site_url = ctx.config.get("site_url", "https://www.qingwapt.com").rstrip("/")
        cookie = ctx.config.get("cookie", "")

        if not cookie:
            await event.edit("❌ 请先配置 Cookie")
            return

        if ctx.http is None:
            await event.edit("❌ 缺少 external_http 权限")
            return

        # 解析参数
        params = _parse_args(args[1:])
        params_desc = _format_params(params)

        await event.edit(f"⏳ 正在获取种子 {torrent_id} 的促销信息...")

        try:
            # Step 1: 获取促销信息
            info_result = await self._get_promotion_info(ctx, site_url, cookie, torrent_id)
            if not info_result["success"]:
                await event.edit(f"❌ {info_result['error']}")
                return

            is_exists = info_result["is_exists"]

            # Step 2: 预计算消耗
            await event.edit(f"📋 {params_desc}\n\n⏳ 正在计算消耗...")
            calc_result = await self._calculate_cost(ctx, site_url, cookie, torrent_id, params, is_exists)
            if not calc_result["success"]:
                await event.edit(f"❌ {calc_result['error']}")
                return

            cost = calc_result["cost_bonus"]
            expression = calc_result["expression"]

            # Step 3: 确认促销
            await event.edit(
                f"📋 {params_desc}\n\n"
                f"💰 预计消耗：{cost} 蝌蚪\n"
                f"📝 {expression}\n\n"
                f"⏳ 正在确认..."
            )
            confirm_result = await self._confirm_promotion(ctx, site_url, cookie, torrent_id, params, is_exists)

            if confirm_result["success"]:
                await event.edit(
                    f"✅ 置顶促销成功！\n\n"
                    f"种子：{torrent_id}\n"
                    f"{params_desc}\n"
                    f"消耗：{cost} 蝌蚪"
                )
            else:
                await event.edit(f"❌ {confirm_result['error']}")

        except Exception as e:
            await event.edit(f"❌ 发生错误：{str(e)[:200]}")

    async def _handle_info(
        self,
        client: Any,
        event: Any,
        args: list[str],
        account_id: int,
        ctx: PluginContext,
    ) -> None:
        """查询种子促销历史。"""
        if not args:
            await event.edit("用法：ptinfo <种子ID>")
            return

        torrent_id = args[0]
        site_url = ctx.config.get("site_url", "https://www.qingwapt.com").rstrip("/")
        cookie = ctx.config.get("cookie", "")

        if not cookie:
            await event.edit("❌ 请先配置 Cookie")
            return

        if ctx.http is None:
            await event.edit("❌ 缺少 external_http 权限")
            return

        await event.edit(f"⏳ 正在查询种子 {torrent_id} 的促销历史...")

        try:
            url = f"{site_url}/plugin/sticky-promotion-history?torrent_id={torrent_id}"
            headers = {
                "Cookie": cookie,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }

            response = await ctx.http.get(url, headers=headers)

            if response.status_code == 200:
                html = response.text
                if "暂无记录" in html or "没有记录" in html:
                    await event.edit(f"📋 种子 {torrent_id} 暂无促销记录")
                else:
                    await event.edit(f"📋 种子 {torrent_id} 有促销记录\n{site_url}/details.php?id={torrent_id}")
            else:
                await event.edit(f"❌ 查询失败：HTTP {response.status_code}")

        except Exception as e:
            await event.edit(f"❌ 查询失败：{str(e)[:200]}")

    async def _get_promotion_info(
        self, ctx: PluginContext, site_url: str, cookie: str, torrent_id: str,
    ) -> dict[str, Any]:
        """获取促销信息。"""
        url = f"{site_url}/plugin/sticky-promotion-info"
        headers = {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        response = await ctx.http.get(url, params={"torrent_id": torrent_id}, headers=headers)

        if response.status_code != 200:
            return {"success": False, "error": f"HTTP {response.status_code}"}

        data = response.json()
        if data.get("ret") != 0:
            return {"success": False, "error": data.get("msg", "未知错误")}

        is_exists = data.get("data", {}).get("is_exists", 0)
        return {"success": True, "is_exists": is_exists}

    async def _calculate_cost(
        self, ctx: PluginContext, site_url: str, cookie: str,
        torrent_id: str, params: dict[str, str], is_exists: int,
    ) -> dict[str, Any]:
        """预计算消耗。"""
        request_params = {
            "torrent_id": torrent_id,
            "promotion_type": params["promotion_type"],
            "duration": params["duration"],
            "competitive_bonus": params["competitive_bonus"] or "0",
            "reward_bonus": params["reward_bonus"] or "0",
            "reward_user_num": params["reward_user_num"] or "0",
            "__just_calculate": "1",
        }

        url = f"{site_url}/plugin/sticky-promotion-append" if is_exists == 1 else f"{site_url}/plugin/sticky-promotion"
        headers = {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        response = await ctx.http.post(url, params=request_params, headers=headers)

        if response.status_code != 200:
            return {"success": False, "error": f"HTTP {response.status_code}"}

        data = response.json()
        if data.get("ret") != 0:
            return {"success": False, "error": data.get("msg", "未知错误")}

        return {
            "success": True,
            "cost_bonus": data.get("data", {}).get("cost_bonus", "未知"),
            "expression": data.get("data", {}).get("expression", ""),
        }

    async def _confirm_promotion(
        self, ctx: PluginContext, site_url: str, cookie: str,
        torrent_id: str, params: dict[str, str], is_exists: int,
    ) -> dict[str, Any]:
        """确认促销。"""
        request_params = {
            "torrent_id": torrent_id,
            "promotion_type": params["promotion_type"],
            "duration": params["duration"],
            "competitive_bonus": params["competitive_bonus"] or "0",
            "reward_bonus": params["reward_bonus"] or "0",
            "reward_user_num": params["reward_user_num"] or "0",
        }

        url = f"{site_url}/plugin/sticky-promotion-append" if is_exists == 1 else f"{site_url}/plugin/sticky-promotion"
        headers = {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        response = await ctx.http.post(url, params=request_params, headers=headers)

        if response.status_code != 200:
            return {"success": False, "error": f"HTTP {response.status_code}"}

        data = response.json()
        if data.get("ret") != 0:
            return {"success": False, "error": data.get("msg", "未知错误")}

        return {"success": True}


__all__ = ["PTPromotePlugin"]
