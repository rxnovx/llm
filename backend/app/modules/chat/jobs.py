"""Run chat generation for the lifetime of the API process, independent of SSE."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing

logger = logging.getLogger(__name__)


class Subscription:
    def __init__(self):
        self.queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=256)
        self.listening = True

    def publish(self, event: str | None):
        if not self.listening:
            return
        if self.queue.full():
            # Slow clients can reload the checkpointed conversation. Keep the
            # model job moving and bound the memory used by the connection.
            self.listening = False
            while not self.queue.empty():
                self.queue.get_nowait()
            event = None
        self.queue.put_nowait(event)

    async def stream(self):
        try:
            while True:
                try:
                    event = await asyncio.wait_for(self.queue.get(), timeout=15)
                except TimeoutError:
                    yield ': keepalive\n\n'
                    continue
                if event is None:
                    return
                yield event
        finally:
            self.listening = False


class GenerationJobs:
    def __init__(self):
        self.tasks: set[asyncio.Task] = set()

    def start(self, events: AsyncIterator[str]) -> Subscription:
        subscription = Subscription()

        async def run():
            try:
                async with aclosing(events):
                    async for event in events:
                        subscription.publish(event)
                        # Give the SSE reader a chance to consume bursts of
                        # events from a completed tool/model response.
                        await asyncio.sleep(0)
            except Exception as error:
                # 예외 원문에는 모델 응답·명령 출력·인증 값이 포함될 수 있다.
                # 사용자에게는 연결 종료와 저장된 체크포인트로 복구를 안내한다.
                logger.error('Chat generation job failed (%s)', type(error).__name__)
            finally:
                subscription.publish(None)

        task = asyncio.create_task(run())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return subscription

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
