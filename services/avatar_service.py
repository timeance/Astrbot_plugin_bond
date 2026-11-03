"""榜单头像的有界下载与内存缓存；调用方须先完成查询授权。"""

from __future__ import annotations

import asyncio
import re
import time
from collections import OrderedDict

import aiohttp

MAX_AVATAR_BYTES = 128 * 1024
REQUEST_TIMEOUT = 2.0
BATCH_TIMEOUT = 3.0
CACHE_ITEMS = 128
CACHE_BYTES = 16 * 1024 * 1024
SUCCESS_TTL = 600.0
FAILURE_TTL = 30.0
_QQ_ID = re.compile(r"[0-9]{1,20}\Z")


class RankingAvatars:
    """每实例共享三个下载槽；只拥有请求内任务，不需后台清理循环。"""

    def __init__(self) -> None:
        self._semaphore = asyncio.Semaphore(3)
        self._cache: OrderedDict[str, tuple[float, bytes | None]] = OrderedDict()
        self._cache_bytes = 0

    def _discard(self, user_id: str) -> None:
        entry = self._cache.pop(user_id, None)
        if entry is not None:
            self._cache_bytes -= len(entry[1]) if entry[1] is not None else 0

    def _remember(self, user_id: str, data: bytes | None) -> None:
        self._discard(user_id)
        ttl = SUCCESS_TTL if data is not None else FAILURE_TTL
        self._cache[user_id] = (time.monotonic() + ttl, data)
        self._cache_bytes += len(data) if data is not None else 0
        while len(self._cache) > CACHE_ITEMS or self._cache_bytes > CACHE_BYTES:
            self._discard(next(iter(self._cache)))

    async def _read(self, session: aiohttp.ClientSession, user_id: str) -> bytes | None:
        url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=100"
        async with session.get(url, allow_redirects=False) as response:
            if response.status != 200:
                return None
            media_type = response.headers.get("Content-Type", "").split(";", 1)[0]
            if not media_type.strip().lower().startswith("image/"):
                return None
            if (
                response.content_length is not None
                and response.content_length > MAX_AVATAR_BYTES
            ):
                return None
            data = bytearray()
            async for chunk in response.content.iter_chunked(16 * 1024):
                if len(data) + len(chunk) > MAX_AVATAR_BYTES:
                    return None
                data.extend(chunk)
            return bytes(data) if data else None

    async def _fetch(self, session, user_id: str) -> bytes | None:
        async with self._semaphore:
            try:
                data = await asyncio.wait_for(
                    self._read(session, user_id), timeout=REQUEST_TIMEOUT
                )
            except Exception:
                data = None
            # CancelledError 不在 Exception 内，卸载/整批取消会正常传播并清理连接。
            self._remember(user_id, data)
            return data

    async def load(self, user_ids: list[str]) -> list[bytes | None]:
        """按前十个 ID 的原顺序返回；非法 ID、失败或超时位置保留 None。"""
        selected = user_ids[:10]
        results: list[bytes | None] = [None] * len(selected)
        pending: dict[str, list[int]] = {}
        now = time.monotonic()
        for index, user_id in enumerate(selected):
            if not isinstance(user_id, str) or not _QQ_ID.fullmatch(user_id):
                continue
            entry = self._cache.get(user_id)
            if entry is not None and entry[0] > now:
                self._cache.move_to_end(user_id)
                results[index] = entry[1]
            else:
                self._discard(user_id)
                pending.setdefault(user_id, []).append(index)
        if not pending:
            return results

        async def batch():
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                trust_env=False,
                auto_decompress=False,
                cookie_jar=aiohttp.DummyCookieJar(),
            ) as session:

                async def download(user_id, positions):
                    data = await self._fetch(session, user_id)
                    for position in positions:
                        results[position] = data

                # 所有并发都由当前请求 await；取消时 gather 会取消并等待子请求。
                await asyncio.gather(
                    *(
                        download(user_id, positions)
                        for user_id, positions in pending.items()
                    ),
                    return_exceptions=True,
                )

        try:
            await asyncio.wait_for(batch(), timeout=BATCH_TIMEOUT)
        except Exception:
            # 整批超时或会话建立失败也短暂缓存；保留并发请求已成功写入的缓存。
            now = time.monotonic()
            for user_id in pending:
                entry = self._cache.get(user_id)
                if entry is None or entry[0] <= now:
                    self._remember(user_id, None)
        return results
