"""The Stage's client of the public store: Upstash Redis over its REST API.

Each command is a JSON array posted to the store's URL with its token, and the
answer is {"result": ...} or {"error": ...} (docs/PLAN.md section 5.9). The
store is on the internet and the venue's network may be down, so every
failure is a StoreError the caller skips until its next cycle.
"""

import json
import logging
from typing import Any

import httpx2

log = logging.getLogger(__name__)

# Short: the publisher and the request reader wait on it, never playback.
_TIMEOUT = httpx2.Timeout(5.0)
_UNREACHABLE = (httpx2.HTTPError, httpx2.InvalidURL)


class StoreError(Exception):
    """The store could not be reached, or answered with an error."""


class Store:
    """One HTTP client shared by every thread that talks to the store.

    A failure is logged once, and again only after the store answered in
    between, so a night offline does not flood the console.
    """

    def __init__(self, url: str, token: str, http: httpx2.Client | None = None) -> None:
        self._url = url
        self._auth = {"Authorization": f"Bearer {token}"}
        self._owned = http is None
        self._http = httpx2.Client(timeout=_TIMEOUT) if http is None else http
        self._failing = False

    def close(self) -> None:
        if self._owned:
            self._http.close()

    def command(self, *args: str | int) -> Any:
        try:
            result = self._send(list(args))
        except StoreError as error:
            if not self._failing:
                self._failing = True
                log.warning(
                    "the public store failed (%s); trying again on every cycle", error
                )
            raise
        if self._failing:
            self._failing = False
            log.warning("the public store answers again")
        return result

    def set_json(self, key: str, value: object, expire_s: int | None = None) -> None:
        text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        if expire_s is None:
            self.command("SET", key, text)
        else:
            self.command("SET", key, text, "EX", expire_s)

    def pop(self, key: str) -> str | None:
        """The head of the list at key, or None when it is empty."""
        return self.command("LPOP", key)

    def delete(self, key: str) -> None:
        self.command("DEL", key)

    def time_ms(self) -> float:
        """The store's clock: Redis TIME, in milliseconds."""
        seconds, microseconds = self.command("TIME")
        return int(seconds) * 1000 + int(microseconds) / 1000

    def _send(self, command: list) -> Any:
        try:
            response = self._http.post(self._url, headers=self._auth, json=command)
        except _UNREACHABLE as error:
            raise StoreError(f"could not reach the store ({error})") from None
        try:
            answer = response.json()
        except ValueError:
            answer = None
        if not response.is_success:
            error = answer.get("error") if isinstance(answer, dict) else None
            raise StoreError(
                f"the store answered {response.status_code}: "
                f"{error or response.reason_phrase}"
            )
        if not isinstance(answer, dict) or "result" not in answer:
            raise StoreError("the store sent an answer it cannot read")
        return answer["result"]
