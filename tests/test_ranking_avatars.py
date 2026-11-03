"""头像网络边界与缓存回归；仅模拟 HTTP，不访问 QQ。"""

import asyncio
from collections import deque
from unittest.mock import patch

import aiohttp
import pytest

from astrbot_plugin_bond.services import avatar_service as avatars


class FakeResponse:
    def __init__(
        self,
        body=b"image",
        *,
        status=200,
        media="image/png",
        length=None,
        delay=0,
        chunks=None,
        error=None,
    ):
        self.body = body
        self.status = status
        self.headers = {"Content-Type": media}
        self.content_length = length
        self.delay = delay
        self.chunks = chunks
        self.error = error
        self.content = self
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def iter_chunked(self, size):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        for chunk in self.chunks if self.chunks is not None else [self.body]:
            yield chunk


class FakeHTTP:
    def __init__(self, responses=(), factory=None):
        self.responses = deque(responses)
        self.factory = factory
        self.calls = []
        self.sessions = []
        self.active = 0
        self.maximum = 0

    def session(self, **options):
        owner = self

        class Session:
            def __init__(self):
                self.closed = False
                self.options = options
                owner.sessions.append(self)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                self.closed = True

            def get(self, url, **kwargs):
                owner.calls.append((url, kwargs))
                response = (
                    owner.factory(url) if owner.factory else owner.responses.popleft()
                )

                class Request:
                    async def __aenter__(self):
                        owner.active += 1
                        owner.maximum = max(owner.maximum, owner.active)
                        return await response.__aenter__()

                    async def __aexit__(self, *args):
                        try:
                            await response.__aexit__(*args)
                        finally:
                            owner.active -= 1

                return Request()

        return Session()


@pytest.fixture
def http(monkeypatch):
    fake = FakeHTTP()
    monkeypatch.setattr(avatars.aiohttp, "ClientSession", fake.session)
    return fake


def test_fixed_url_order_duplicate_ids_and_first_ten_only(http):
    async def run():
        http.factory = lambda url: FakeResponse(url.encode())
        loader = avatars.RankingAvatars()
        ids = ["10001", "bad", "10002", "10001"] + [str(10010 + n) for n in range(8)]
        result = await loader.load(ids)
        assert len(result) == 10
        assert result[1] is None
        assert result[0] == result[3]
        assert result[2] == b"https://q1.qlogo.cn/g?b=qq&nk=10002&s=100"
        assert len(http.calls) == 8
        assert all(options == {"allow_redirects": False} for _, options in http.calls)
        assert all("10016" not in url and "10017" not in url for url, _ in http.calls)
        assert all(session.closed for session in http.sessions)
        options = http.sessions[0].options
        assert options["timeout"].total == 2
        assert options["trust_env"] is False
        assert options["auto_decompress"] is False
        assert http.active == 0

    asyncio.run(run())


@pytest.mark.parametrize(
    "user_id",
    [
        "",
        "not-qq",
        "１２３",
        "12 34",
        "12&x=1",
        "https://evil.test",
        "-12",
        "1" * 21,
        None,
        123,
    ],
)
def test_invalid_ids_never_open_session(http, user_id):
    assert asyncio.run(avatars.RankingAvatars().load([user_id])) == [None]
    assert http.calls == http.sessions == []


def test_empty_list_never_opens_session(http):
    assert asyncio.run(avatars.RankingAvatars().load([])) == []
    assert http.sessions == []


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(status=302),
        FakeResponse(status=404),
        FakeResponse(media="text/html"),
        FakeResponse(media=""),
        FakeResponse(body=b""),
        FakeResponse(length=128 * 1024 + 1),
        FakeResponse(chunks=[b"a" * (128 * 1024), b"b"]),
        FakeResponse(error=aiohttp.ClientConnectionError("synthetic")),
    ],
)
def test_rejected_responses_are_none_and_are_closed(http, response):
    http.responses.append(response)
    assert asyncio.run(avatars.RankingAvatars().load(["10001"])) == [None]
    assert response.closed
    assert http.active == 0
    assert http.sessions[0].closed
    assert len(http.calls) == 1


def test_exact_size_limit_and_case_insensitive_image_type(http):
    body = b"a" * (128 * 1024)
    http.responses.append(
        FakeResponse(body, media="IMAGE/PNG; charset=binary", length=len(body))
    )
    assert asyncio.run(avatars.RankingAvatars().load(["10001"])) == [body]


def test_success_and_failure_ttl_do_not_refresh_on_read(http):
    async def run():
        loader = avatars.RankingAvatars()
        http.responses.extend(
            [
                FakeResponse(b"first"),
                FakeResponse(status=503),
                FakeResponse(b"retry"),
                FakeResponse(b"new"),
            ]
        )
        clock = [0.0]
        with patch.object(avatars.time, "monotonic", side_effect=lambda: clock[0]):
            assert await loader.load(["10001", "10002"]) == [b"first", None]
            clock[0] = 29
            assert await loader.load(["10002", "10001"]) == [None, b"first"]
            assert len(http.calls) == 2
            clock[0] = 30
            assert await loader.load(["10002"]) == [b"retry"]
            clock[0] = 599
            assert await loader.load(["10001"]) == [b"first"]
            clock[0] = 600
            assert await loader.load(["10001"]) == [b"new"]
            assert len(http.calls) == 4

    asyncio.run(run())


def test_cache_item_limit_and_recent_access_eviction(http):
    async def run():
        loader = avatars.RankingAvatars()
        http.factory = lambda url: FakeResponse(b"x")
        for start in range(0, 128, 10):
            await loader.load(
                [str(10000 + n) for n in range(start, min(start + 10, 128))]
            )
        assert len(loader._cache) == 128
        assert loader._cache_bytes == 128
        await loader.load(["10000"])
        await loader.load(["20000"])
        assert len(loader._cache) == 128
        assert "10000" in loader._cache
        assert "10001" not in loader._cache
        assert loader._cache_bytes == 128

    asyncio.run(run())


def test_cache_byte_budget_is_enforced(http, monkeypatch):
    async def run():
        monkeypatch.setattr(avatars, "CACHE_BYTES", 8)
        loader = avatars.RankingAvatars()
        http.factory = lambda url: FakeResponse(b"12345")
        await loader.load(["10001", "10002"])
        assert len(loader._cache) == 1
        assert loader._cache_bytes == 5
        await loader.load(["10003"])
        assert len(loader._cache) == 1
        assert loader._cache_bytes == 5

    asyncio.run(run())


def test_request_timeout_is_bounded_and_cached_as_failure(http, monkeypatch):
    async def run():
        monkeypatch.setattr(avatars, "REQUEST_TIMEOUT", 0.01)
        http.factory = lambda url: FakeResponse(delay=10)
        loader = avatars.RankingAvatars()
        assert await loader.load(["10001"]) == [None]
        assert http.active == 0
        assert await loader.load(["10001"]) == [None]
        assert len(http.calls) == 1
        assert all(session.closed for session in http.sessions)

    asyncio.run(run())


def test_batch_timeout_keeps_finished_results_and_drains_requests(http, monkeypatch):
    async def run():
        monkeypatch.setattr(avatars, "BATCH_TIMEOUT", 0.02)
        http.factory = lambda url: FakeResponse(
            b"ok", delay=0 if "10001" in url else 10
        )
        loader = avatars.RankingAvatars()
        result = await loader.load([str(10001 + n) for n in range(10)])
        assert result == [b"ok"] + [None] * 9
        calls = len(http.calls)
        assert await loader.load([str(10001 + n) for n in range(10)]) == result
        assert len(http.calls) == calls
        assert http.active == 0
        assert http.maximum <= 3
        assert all(session.closed for session in http.sessions)
        assert not [
            task for task in asyncio.all_tasks() if task is not asyncio.current_task()
        ]

    asyncio.run(run())


def test_semaphore_is_shared_across_simultaneous_batches(http):
    async def run():
        http.factory = lambda url: FakeResponse(b"ok", delay=0.001)
        loader = avatars.RankingAvatars()
        first, second = await asyncio.gather(
            loader.load([str(10000 + n) for n in range(10)]),
            loader.load([str(20000 + n) for n in range(10)]),
        )
        assert first == second == [b"ok"] * 10
        assert http.maximum == 3
        assert http.active == 0
        assert all(session.closed for session in http.sessions)

    asyncio.run(run())


def test_parent_cancellation_propagates_and_closes_sessions(http):
    async def run():
        parent = asyncio.current_task()

        class CancellingResponse(FakeResponse):
            async def iter_chunked(self, size):
                asyncio.get_running_loop().call_soon(parent.cancel)
                await asyncio.sleep(10)
                yield b"unreachable"

        http.factory = lambda url: CancellingResponse()
        loader = avatars.RankingAvatars()
        with pytest.raises(asyncio.CancelledError):
            await loader.load(["10001"])
        assert http.active == 0
        assert all(session.closed for session in http.sessions)
        assert not [
            task for task in asyncio.all_tasks() if task is not asyncio.current_task()
        ]

    asyncio.run(run())


def test_session_setup_failure_does_not_break_ranking(http, monkeypatch):
    def failed_session(**options):
        raise OSError("synthetic session failure")

    monkeypatch.setattr(avatars.aiohttp, "ClientSession", failed_session)
    assert asyncio.run(avatars.RankingAvatars().load(["10001"])) == [None]
