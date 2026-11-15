import json
import logging

import httpx2
import pytest
from stage.store import Store, StoreError


def answering(status: int, body: bytes) -> Store:
    transport = httpx2.MockTransport(
        lambda request: httpx2.Response(status, content=body)
    )
    return Store(
        "https://store.invalid", "token", http=httpx2.Client(transport=transport)
    )


def test_a_command_is_a_json_array_posted_with_the_token(store) -> None:
    sent = []
    store.on_command = sent.append

    assert store.client().command("SET", "karaoke:x", "1") == "OK"

    assert sent == [["SET", "karaoke:x", "1"]]


def test_the_result_of_a_command_comes_back(store) -> None:
    store.values["karaoke:x"] = "value"

    assert store.client().command("GET", "karaoke:x") == "value"
    assert store.client().command("GET", "karaoke:none") is None


def test_set_json_stores_compact_json_with_an_expiry(store) -> None:
    store.client().set_json("karaoke:answer:1", {"ok": True, "título": "Canção"}, 120)

    assert store.values["karaoke:answer:1"] == '{"ok":true,"título":"Canção"}'
    assert store.expiry["karaoke:answer:1"] == 120


def test_set_json_without_an_expiry_keeps_the_key(store) -> None:
    store.client().set_json("karaoke:snapshot", {"version": 1})

    assert store.commands == [["SET", "karaoke:snapshot", '{"version":1}']]


def test_pop_takes_from_the_head(store) -> None:
    store.lists["karaoke:requests"] = ["a", "b"]
    client = store.client()

    assert [client.pop("karaoke:requests") for _ in range(3)] == ["a", "b", None]


def test_delete(store) -> None:
    store.lists["karaoke:requests"] = ["a"]

    store.client().delete("karaoke:requests")

    assert "karaoke:requests" not in store.lists


def test_time_is_the_store_clock_in_milliseconds(store) -> None:
    store.now_ms = 1_700_000_000_123.456

    assert store.client().time_ms() == pytest.approx(1_700_000_000_123.456)


def test_an_error_answer_raises_with_its_message() -> None:
    client = answering(400, json.dumps({"error": "ERR unknown command"}).encode())

    with pytest.raises(StoreError, match="the store answered 400: ERR unknown command"):
        client.command("NOPE")


def test_a_wrong_token_raises(store) -> None:
    client = store.client(token="wrong-token")

    with pytest.raises(StoreError, match="the store answered 401: Unauthorized"):
        client.command("GET", "karaoke:x")


def test_an_answer_that_is_not_json_raises() -> None:
    with pytest.raises(StoreError, match="the store answered 502"):
        answering(502, b"<html>bad gateway</html>").command("GET", "karaoke:x")
    with pytest.raises(StoreError, match="the store sent an answer it cannot read"):
        answering(200, b"not json").command("GET", "karaoke:x")


def test_an_unreachable_store_raises(store) -> None:
    store.down = True

    with pytest.raises(StoreError, match="could not reach the store"):
        store.client().command("GET", "karaoke:x")


def test_a_failing_store_is_logged_once_until_it_answers_again(store, caplog) -> None:
    client = store.client()
    store.down = True

    with caplog.at_level(logging.WARNING, logger="stage"):
        for _ in range(3):
            with pytest.raises(StoreError):
                client.command("GET", "karaoke:x")
        store.down = False
        client.command("GET", "karaoke:x")
        client.command("GET", "karaoke:x")

    assert caplog.messages == [
        (
            "the public store failed (could not reach the store (the store is down)); "
            "trying again on every cycle"
        ),
        "the public store answers again",
    ]
