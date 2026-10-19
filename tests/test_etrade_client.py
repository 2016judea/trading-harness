"""etrade.py against a fake session: a dead token re-auths exactly once on a
read and NEVER on a write, and a 204 is data, not a fault."""
from __future__ import annotations

import pytest

import etrade


class _Resp:
    def __init__(self, status, body=b""):
        self.status_code, self.content, self.text = status, body, body.decode()

    def json(self):
        import json
        return json.loads(self.content)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _fake_sessions(monkeypatch, responses):
    """Each session() call hands back the next canned response for any verb."""
    log = []

    class S:
        def __init__(self, r):
            self.r = r

        def get(self, url, params=None):
            log.append(("GET", url, params)); return self.r

        def request(self, method, url, json=None, headers=None):
            log.append((method, url, json)); return self.r

    it = iter(responses)
    monkeypatch.setattr(etrade, "session", lambda force_reauth=False: (log.append(("SESSION", force_reauth)), S(next(it)))[1])
    return log


def test_a_401_on_a_read_reauths_once_and_retries(monkeypatch):
    log = _fake_sessions(monkeypatch, [_Resp(401), _Resp(200, b'{"ok": 1}')])
    assert etrade.get("/v1/accounts/list") == {"ok": 1}
    assert [e for e in log if e[0] == "SESSION"] == [("SESSION", False), ("SESSION", True)]


def test_a_204_is_an_empty_answer_not_an_error(monkeypatch):
    _fake_sessions(monkeypatch, [_Resp(204)])
    assert etrade.get("/v1/accounts/K/orders") == {}


def test_a_401_on_a_write_is_never_retried(monkeypatch):
    log = _fake_sessions(monkeypatch, [_Resp(401), _Resp(200, b'{}')])
    with pytest.raises(RuntimeError, match="never retried"):
        etrade.post("/v1/accounts/K/orders/place", {"x": 1})
    assert [e for e in log if e[0] == "SESSION"] == [("SESSION", False)]
    assert sum(1 for e in log if e[0] == "POST") == 1


def test_a_rejected_write_raises_with_the_body(monkeypatch):
    _fake_sessions(monkeypatch, [_Resp(400, b'{"Error": {"message": "bad"}}')])
    with pytest.raises(RuntimeError, match="400"):
        etrade.put("/v1/accounts/K/orders/cancel", {})
