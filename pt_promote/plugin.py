"""青娃PT (qingwapt.com) 置顶促销模块。

功能：
  - 在青娃PT上为种子设置置顶促销
  - 支持查询促销历史
  - 自动处理表单和确认流程

用法：
  {prefix}pt <种子ID> [天数] [类型]
  {prefix}pt 12345           # 默认 7 天置顶促销
  {prefix}pt 12345 30        # 30 天置顶促销
  {prefix}ptinfo <种子ID>    # 查询促销信息

注意：
  - 需要站点账号权限（消耗蝌蚪）
  - Cookie 需要在配置中设置
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlencode

from app.worker.plugins.base import Plugin, PluginContext, register


def _extract_form_fields(html: str) -> dict[str, str]:
    """从促销表单 HTML 中提取字段和默认值。"""
    fields = {}
    # 提取 input/select 字段
    for match in re.finditer(
        r'<(?:input|select)[^>]*name="([^"]+)"[^>]*(?:value="([^"]*)")?',
        html,
        re.DOTALL,
    ):
        name = match.group(1)
        value = match.group(2) or ""
        if name and name not in ('torrent_id',):
            fields[name] = value

    # 提取 select 的 selected option
    for match in re.finditer(
        r'<select[^>]*name="([^"]+)"[^>]*>.*?<option[^>]*value="([^"]*)"[^>]*selected',
        html,
        re.DOTALL,
    ):
        fields[match.group(1)] = match.group(2)

    return fields


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
                f"用法：{prefix} <种子ID> [天数]\n"
                f"示例：{prefix} 12345\n"
                f"      {prefix} 12345 30"
            )
            return

        torrent_id = args[0]
        site_url = ctx.config.get("site_url", "").rstrip("/")
        cookie = ctx.config.get("cookie", "")
        timeout = ctx.config.get("timeout", 30)

        if not site_url or not cookie:
            await event.edit("❌ 请先配置站点地址和 Cookie")
            return

        if ctx.http is None:
            await event.edit("❌ 缺少 external_http 权限，请检查模块配置")
            return

        await event.edit(f"⏳ 正在获取种子 {torrent_id} 的促销信息...")

        try:
            # Step 1: 获取促销信息和表单
            info_result = await self._get_promotion_info(
                ctx=ctx,
                site_url=site_url,
                cookie=cookie,
                torrent_id=torrent_id,
                timeout=timeout,
            )

            if not info_result["success"]:
                await event.edit(f"❌ {info_result['error']}")
                return

            form_fields = info_result["form_fields"]
            is_exists = info_result["is_exists"]

            # 如果用户指定了天数，覆盖表单默认值
            if len(args) > 1:
                days = args[1]
                # 尝试设置天数相关字段
                for key in form_fields:
                    if 'day' in key.lower() or 'period' in key.lower() or 'duration' in key.lower():
                        form_fields[key] = days

            # Step 2: 预计算消耗
            await event.edit(f"⏳ 正在计算消耗...")

            calc_result = await self._calculate_cost(
                ctx=ctx,
                site_url=site_url,
                cookie=cookie,
                torrent_id=torrent_id,
                form_fields=form_fields,
                is_exists=is_exists,
                timeout=timeout,
            )

            if not calc_result["success"]:
                await event.edit(f"❌ {calc_result['error']}")
                return

            cost = calc_result["cost_bonus"]
            expression = calc_result["expression"]

            # Step 3: 确认促销
            await event.edit(
                f"💰 预计消耗：{cost} 蝌蚪\n"
                f"📝 {expression}\n"
                f"⏳ 正在确认..."
            )

            confirm_result = await self._confirm_promotion(
                ctx=ctx,
                site_url=site_url,
                cookie=cookie,
                torrent_id=torrent_id,
                form_fields=form_fields,
                is_exists=is_exists,
                timeout=timeout,
            )

            if confirm_result["success"]:
                await event.edit(
                    f"✅ 置顶促销设置成功！\n"
                    f"种子：{torrent_id}\n"
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
        """查询种子促销信息。"""
        if not args:
            await event.edit("用法：ptinfo <种子ID>")
            return

        torrent_id = args[0]
        site_url = ctx.config.get("site_url", "").rstrip("/")
        cookie = ctx.config.get("cookie", "")
        timeout = ctx.config.get("timeout", 30)

        if not site_url or not cookie:
            await event.edit("❌ 请先配置站点地址和 Cookie")
            return

        if ctx.http is None:
            await event.edit("❌ 缺少 external_http 权限，请检查模块配置")
            return

        await event.edit(f"⏳ 正在查询种子 {torrent_id} 的促销信息...")

        try:
            # 获取促销信息页面
            url = f"{site_url}/plugin/sticky-promotion-history?torrent_id={torrent_id}"
            headers = {
                "Cookie": cookie,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }

            response = await ctx.http.get(url, headers=headers, timeout=timeout)

            if response.status_code == 200:
                # 简单提取信息
                html = response.text
                if "暂无记录" in html or "没有记录" in html:
                    await event.edit(f"📋 种子 {torrent_id} 暂无促销记录")
                else:
                    # 尝试提取促销历史
                    await event.edit(f"📋 种子 {torrent_id} 有促销记录，详情请访问：\n{site_url}/details.php?id={torrent_id}")
            else:
                await event.edit(f"❌ 查询失败：HTTP {response.status_code}")

        except Exception as e:
            await event.edit(f"❌ 查询失败：{str(e)[:200]}")

    async def _get_promotion_info(
        self,
        ctx: PluginContext,
        site_url: str,
        cookie: str,
        torrent_id: str,
        timeout: int,
    ) -> dict[str, Any]:
        """获取促销信息和表单。"""
        url = f"{site_url}/plugin/sticky-promotion-info"
        params = {"torrent_id": torrent_id}
        headers = {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        response = await ctx.http.get(
            url,
            params=params,
            headers=headers,
            timeout=timeout,
        )

        if response.status_code != 200:
            return {"success": False, "error": f"获取促销信息失败：HTTP {response.status_code}"}

        try:
            data = response.json()
        except Exception:
            return {"success": False, "error": "响应格式错误"}

        if data.get("ret") != 0:
            return {"success": False, "error": data.get("msg", "未知错误")}

        content = data.get("data", {}).get("content", "")
        is_exists = data.get("data", {}).get("is_exists", 0)

        # 从表单 HTML 中提取字段
        form_fields = _extract_form_fields(content)

        return {
            "success": True,
            "form_fields": form_fields,
            "is_exists": is_exists,
        }

    async def _calculate_cost(
        self,
        ctx: PluginContext,
        site_url: str,
        cookie: str,
        torrent_id: str,
        form_fields: dict[str, str],
        is_exists: int,
        timeout: int,
    ) -> dict[str, Any]:
        """预计算消耗。"""
        # 构建查询参数
        params = {**form_fields, "torrent_id": torrent_id, "__just_calculate": "1"}

        # 选择 URL
        if is_exists == 1:
            url = f"{site_url}/plugin/sticky-promotion-append"
        else:
            url = f"{site_url}/plugin/sticky-promotion"

        headers = {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        response = await ctx.http.post(
            url,
            params=params,
            headers=headers,
            timeout=timeout,
        )

        if response.status_code != 200:
            return {"success": False, "error": f"计算消耗失败：HTTP {response.status_code}"}

        try:
            data = response.json()
        except Exception:
            return {"success": False, "error": "响应格式错误"}

        if data.get("ret") != 0:
            return {"success": False, "error": data.get("msg", "未知错误")}

        return {
            "success": True,
            "cost_bonus": data.get("data", {}).get("cost_bonus", "未知"),
            "expression": data.get("data", {}).get("expression", ""),
        }

    async def _confirm_promotion(
        self,
        ctx: PluginContext,
        site_url: str,
        cookie: str,
        torrent_id: str,
        form_fields: dict[str, str],
        is_exists: int,
        timeout: int,
    ) -> dict[str, Any]:
        """确认促销。"""
        # 构建查询参数
        params = {**form_fields, "torrent_id": torrent_id}

        # 选择 URL
        if is_exists == 1:
            url = f"{site_url}/plugin/sticky-promotion-append"
        else:
            url = f"{site_url}/plugin/sticky-promotion"

        headers = {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        response = await ctx.http.post(
            url,
            params=params,
            headers=headers,
            timeout=timeout,
        )

        if response.status_code != 200:
            return {"success": False, "error": f"确认失败：HTTP {response.status_code}"}

        try:
            data = response.json()
        except Exception:
            return {"success": False, "error": "响应格式错误"}

        if data.get("ret") != 0:
            return {"success": False, "error": data.get("msg", "未知错误")}

        return {"success": True}


__all__ = ["PTPromotePlugin"]
