import asyncio
import json
import os


def _int(name, default):
    try:
        return max(1, int(os.environ.get(name) or default))
    except ValueError:
        return default


HTTP_CONCURRENCY = _int("HOPLITA_HTTP_CONCURRENCY", 12)
HTTP_QUEUE_S = _int("HOPLITA_HTTP_QUEUE_S", 30)


class HttpConcurrencyLimit:

    def __init__(self, app, limit=None, queue_s=None):
        self.app = app
        self.limit = limit or HTTP_CONCURRENCY
        self.queue_s = queue_s or HTTP_QUEUE_S
        self._sems = {}
        self.in_flight = 0

    def _sem(self):
        loop = asyncio.get_running_loop()
        sem = self._sems.get(loop)
        if sem is None:
            sem = self._sems[loop] = asyncio.Semaphore(self.limit)
        return sem

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        sem = self._sem()
        try:
            await asyncio.wait_for(sem.acquire(), timeout=self.queue_s)
        except asyncio.TimeoutError:
            body = json.dumps({"error": "busy", "retry_after": 5,
                               "message": "The server is at capacity; "
                                          "retry shortly."}).encode()
            await send({"type": "http.response.start", "status": 503,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"retry-after", b"5"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        self.in_flight += 1
        try:
            return await self.app(scope, receive, send)
        finally:
            self.in_flight -= 1
            sem.release()
