from __future__ import annotations

import gzip
import json

import httpx
import pytest

from scenario_forge_bridge import api


class Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, content, headers, timeout):
        self.calls.append(headers.get("Content-Encoding"))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        status, payload = item
        return httpx.Response(status, json=payload, request=httpx.Request("POST", url))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(api.time, "sleep", lambda seconds: None)


def test_sends_gzip_and_retries_transient_failures(monkeypatch) -> None:
    recorder = Recorder([httpx.ReadError("Connection reset by peer"), (503, {"detail": "restarting"}),
                         (200, {"snapshot_id": 7, "map_name": "Town02", "created": True})])
    monkeypatch.setattr(api.httpx, "post", recorder)
    retries = []
    stored = api.upload_catalog("http://x/api/v1", "tok", "CONN-1", {"map_name": "Town02"}, on_retry=lambda n, wait, why: retries.append((n, why)))
    assert stored["snapshot_id"] == 7
    assert recorder.calls == ["gzip", "gzip", "gzip"]
    assert [n for n, _ in retries] == [2, 3] and "Connection reset" in retries[0][1]


def test_falls_back_to_plain_json_for_an_older_backend(monkeypatch) -> None:
    recorder = Recorder([(422, {"error": {"message": "Invalid catalog upload"}}), (201, {"snapshot_id": 1, "map_name": "Town01", "created": False})])
    monkeypatch.setattr(api.httpx, "post", recorder)
    assert api.upload_catalog("http://x/api/v1", "tok", "CONN-1", {})["snapshot_id"] == 1
    assert recorder.calls == ["gzip", None]


def test_gives_up_after_the_last_retry_and_does_not_retry_client_errors(monkeypatch) -> None:
    monkeypatch.setattr(api.httpx, "post", Recorder([httpx.ConnectError("down")] * 4))
    with pytest.raises(api.BridgeApiError, match="sau 4 lần"):
        api.upload_catalog("http://x/api/v1", "tok", "CONN-1", {})
    recorder = Recorder([(404, {"error": {"message": "Bridge connection not found"}})])
    monkeypatch.setattr(api.httpx, "post", recorder)
    with pytest.raises(api.BridgeApiError, match="not found"):
        api.upload_catalog("http://x/api/v1", "tok", "CONN-1", {})
    assert len(recorder.calls) == 1


def test_body_round_trips() -> None:
    body = {"connection_uid": "CONN-1", "request_id": None, "catalog": {"waypoints": [{"x": 1}] * 10}}
    raw = json.dumps(body, separators=(",", ":")).encode()
    assert json.loads(gzip.decompress(gzip.compress(raw))) == body
