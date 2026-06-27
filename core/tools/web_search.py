"""联网搜索工具 — Tavily 后端

支持多 API Key 池：注册 N 个 Key，自动轮换和限流降级。
"""

import logging
import random
import time
from typing import Any

import httpx

from core.tools.base import BaseTool

logger = logging.getLogger(__name__)


class WebSearchTool(BaseTool):
    """联网搜索工具，Tavily 后端"""

    name = "web_search"
    description = (
        "联网搜索实时信息。返回完整文章内容、标题、URL。\n"
        "支持获取 AI 摘要、相关图片，可控制搜索深度。"
    )
    output_type = "json"

    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索查询关键词",
            },
            "max_results": {
                "type": "integer",
                "description": "返回结果数量（1-10），默认 5",
                "default": 5,
            },
            "include_answer": {
                "type": "boolean",
                "description": "是否生成 AI 摘要答案，默认 false。设为 true 可获得搜索结果的总结",
                "default": False,
            },
            "include_images": {
                "type": "boolean",
                "description": "是否返回搜索结果中的相关图片，默认 false",
                "default": False,
            },
            "search_depth": {
                "type": "string",
                "enum": ["basic", "advanced"],
                "description": "搜索深度：basic=快速粗略 / advanced=深度全面，默认 basic",
                "default": "basic",
            },
        },
        "required": ["query"],
    }

    def __init__(self, backend: str = "tavily", api_key: str = "",
                 api_keys: list[str] | None = None,
                 max_results: int = 5, timeout: int = 10) -> None:
        self._backend = backend
        # 支持多 Key 池：优先用 api_keys 列表，回退到单个 api_key
        self._api_keys = api_keys if api_keys else ([api_key] if api_key else [])
        self._default_max_results = max_results
        self._timeout = timeout
        # Key 状态追踪：{key: cooldown_until_timestamp}
        self._key_cooldowns: dict[str, float] = {}

    def _get_available_key(self) -> str | None:
        """从 Key 池中随机选择一个可用 Key（跳过冷却中的）"""
        now = time.time()
        available = [k for k in self._api_keys if self._key_cooldowns.get(k, 0) <= now]
        if not available:
            # 全都在冷却 → 清空冷却，防止死锁
            logger.warning("All API keys in cooldown, resetting")
            self._key_cooldowns.clear()
            available = self._api_keys.copy()
        return random.choice(available) if available else None

    def _mark_cooldown(self, key: str, seconds: int = 60):
        """标记 Key 进入冷却（限流时调用）"""
        self._key_cooldowns[key] = time.time() + seconds

    async def execute(self, query: str, max_results: int = 5, **kwargs: Any) -> dict:
        """执行搜索，返回结构化结果"""
        include_answer = kwargs.get("include_answer", False)
        include_images = kwargs.get("include_images", False)
        search_depth = kwargs.get("search_depth", "basic")
        return await self._search_tavily(
            query, max_results, include_answer, include_images, search_depth,
        )

    async def _search_tavily(
        self, query: str, max_results: int,
        include_answer: bool = False, include_images: bool = False,
        search_depth: str = "basic",
    ) -> dict:
        """Tavily API 后端 — 多 Key 池自动轮换"""
        if not self._api_keys:
            return {
                "status": "error",
                "error": "Tavily API Key 未配置，请在 .AIGEME/local.yaml 中设置 web_search.api_key 或 api_keys",
            }

        url = "https://api.tavily.com/search"
        max_attempts = min(len(self._api_keys), 5)  # 最多试 5 次

        for attempt in range(max_attempts):
            api_key = self._get_available_key()
            if not api_key:
                break

            payload = {
                "api_key": api_key,
                "query": query,
                "max_results": min(max_results, 10),
                "include_answer": include_answer,
                "include_images": include_images,
                "search_depth": search_depth,
            }

            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(url, json=payload)

                    if resp.status_code == 429:
                        # 限流 → 标记冷却，换 Key 重试
                        logger.warning(f"Tavily key rate limited, trying next key (attempt {attempt + 1})")
                        self._mark_cooldown(api_key, seconds=60)
                        continue

                    resp.raise_for_status()
                    data = resp.json()
                    results = []
                    for item in data.get("results", []):
                        results.append({
                            "title": item.get("title", ""),
                            "content": item.get("content", ""),
                            "url": item.get("url", ""),
                            "score": item.get("score", 0.0),
                        })
                    ret = {
                        "status": "ok",
                        "result": {
                            "query": query,
                            "results": results,
                        },
                        "output_type": "json",
                    }
                    if include_answer and data.get("answer"):
                        ret["result"]["answer"] = data["answer"]
                    if include_images and data.get("images"):
                        ret["result"]["images"] = data["images"]
                    logger.info(f"Tavily search OK (key={api_key[:8]}..., {len(results)} results)")
                    return ret

            except httpx.TimeoutException:
                return {
                    "status": "error",
                    "error": "搜索服务超时，请稍后重试",
                }
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    logger.warning(f"Tavily key rate limited (httpx), trying next key (attempt {attempt + 1})")
                    self._mark_cooldown(api_key, seconds=60)
                    continue
                return {
                    "status": "error",
                    "error": f"搜索服务错误: HTTP {e.response.status_code}",
                }
            except Exception as e:
                return {
                    "status": "error",
                    "error": f"搜索服务不可用: {e!s}",
                }

        return {
            "status": "error",
            "error": "所有 Tavily API Key 均已达到速率限制，请稍后重试",
        }


