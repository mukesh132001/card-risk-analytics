"""Platform tools: confirm gate, read-only, JWT handling, masking, and the platform eval."""
import json
from types import SimpleNamespace

import pytest

from card_analytics.assistant import agent, platform_api, platform_eval
from card_analytics.assistant.platform_eval import FULL_NUMBER, FakePlatform, make_tools


def test_no_confirm_callback_means_no_call():
    pt, fake = make_tools(None)
    assert "declined" in pt.call("platform_list_accounts", {})["error"]
    assert fake.requests == []  # not even a login


def test_declined_confirm_makes_no_request():
    asked = []
    pt, fake = make_tools(lambda p: asked.append(p) or False)
    assert "declined" in pt.call("platform_get_transactions", {"account_id": "1"})["error"]
    assert fake.requests == [] and "/api/accounts/1/transactions" in asked[0]


def test_approved_call_logs_in_once_then_gets_with_bearer_token():
    pt, fake = make_tools(lambda p: True)
    assert pt.call("platform_list_accounts", {})["data"]["count"] == 2
    pt.call("platform_get_transactions", {"account_id": "1"})
    assert fake.requests == [("POST", "/api/auth/login"), ("GET", "/api/accounts"), ("GET", "/api/accounts/1/transactions")]


def test_only_get_requests_for_data_and_no_change_tools():
    pt, fake = make_tools(lambda p: True)
    pt.call("platform_list_accounts", {})
    assert {m for m, _ in fake.requests} == {"POST", "GET"} and [r for r in fake.requests if r[0] == "POST"] == [("POST", "/api/auth/login")]
    assert {s["name"] for s in platform_api.PlatformTools.SPECS} == {"platform_list_accounts", "platform_get_transactions"}
    assert "error" in pt.call("platform_freeze_card", {"card_id": "1"})


@pytest.mark.parametrize("bad", ["../admin", "1/../../x", "1?x=1", "", "a b", "x" * 65])
def test_account_id_path_tricks_are_rejected_before_any_request(bad):
    pt, fake = make_tools(lambda p: True)
    assert "error" in pt.call("platform_get_transactions", {"account_id": bad})
    assert fake.requests == []


def test_account_numbers_are_masked_and_token_never_returned():
    pt, _ = make_tools(lambda p: True)
    blob = json.dumps(pt.call("platform_list_accounts", {})) + json.dumps(pt.call("platform_get_transactions", {"account_id": "1"}))
    # the fake leaks a full card number inside a transaction description: it must be masked
    assert FULL_NUMBER not in blob and "************1234" in blob and "fake.jwt.token" not in blob and "demo-pass" not in blob


def test_login_sends_email_and_reads_access_token():
    sent = []

    def transport(method, url, headers, body):
        sent.append((method, url, json.loads(body) if body else None))
        return FakePlatform()(method, url, headers, body)

    client = platform_api.PlatformClient("http://platform.test", "me@example.com", "pw", transport=transport)
    client.get("/api/accounts")
    assert sent[0] == ("POST", "http://platform.test/api/auth/login", {"email": "me@example.com", "password": "pw"})
    assert client._token == "fake.jwt.token"


def test_account_shape_and_paged_transactions_keep_content_and_paging():
    pt, _ = make_tools(lambda p: True)
    acct = pt.call("platform_list_accounts", {})["data"]["items"][0]
    assert set(acct) == {"id", "maskedNumber", "type", "currency", "balance", "status", "openedAt"}
    data = pt.call("platform_get_transactions", {"account_id": "1", "size": 2})["data"]
    assert len(data["content"]) == 2 and (data["page"], data["size"], data["totalElements"], data["totalPages"]) == (0, 2, 5, 3)
    assert {"postedAt", "direction", "signedAmount", "balanceAfter"} <= set(data["content"][0])


def test_transaction_filters_become_query_parameters():
    pt, fake = make_tools(lambda p: True)
    res = pt.call("platform_get_transactions", {"account_id": "1", "direction": "DEBIT", "from": "2026-10-01", "to": "2026-10-31", "page": 0, "size": 10, "q": "fuel"})
    assert "error" not in res
    path = fake.gets[-1][1]
    assert path.startswith("/api/accounts/1/transactions?") and "direction=DEBIT" in path and "from=2026-10-01" in path and "q=fuel" in path
    assert all(t["direction"] == "DEBIT" for t in res["data"]["content"])


@pytest.mark.parametrize("args", [{"direction": "debit"}, {"from": "01/10/2026"}, {"size": 101}, {"size": 0}, {"page": -1}, {"q": "x" * 101}, {"size": "abc"}])
def test_invalid_filters_are_rejected_before_any_request(args):
    pt, fake = make_tools(lambda p: True)
    assert "error" in pt.call("platform_get_transactions", {"account_id": "1", **args})
    assert fake.requests == []


def test_foreign_account_404_is_reported_not_hidden():
    pt, _ = make_tools(lambda p: True)
    assert "404" in pt.call("platform_get_transactions", {"account_id": "999"})["error"]


def test_expired_token_is_refreshed_once():
    fake = FakePlatform()
    client = platform_api.PlatformClient("http://platform.test", "demo", "demo-pass", transport=fake)
    client._token = "stale"
    assert len(client.get("/api/accounts")) == 2
    assert [r[0] for r in fake.requests] == ["GET", "POST", "GET"]


def test_login_failure_does_not_leak_credentials():
    def deny(method, url, headers, body):
        return 403, "bad password demo-pass"
    client = platform_api.PlatformClient("http://platform.test", "demo", "demo-pass", transport=deny)
    res = platform_api.PlatformTools(client, lambda p: True).call("platform_list_accounts", {})
    assert "error" in res and "demo-pass" not in json.dumps(res)


def test_missing_credentials_fail_cleanly(monkeypatch):
    monkeypatch.delenv("PLATFORM_USERNAME", raising=False)
    monkeypatch.delenv("PLATFORM_PASSWORD", raising=False)
    res = platform_api.PlatformTools(platform_api.PlatformClient(transport=FakePlatform()), lambda p: True).call("platform_list_accounts", {})
    assert "not set" in res["error"]


def test_large_lists_are_capped():
    big = [{"id": str(i)} for i in range(120)]
    out = platform_api._items(big)
    assert len(out["items"]) == platform_api.MAX_ITEMS and out["count"] == 120 and out["truncated"]


def test_agent_exposes_and_routes_platform_tools():
    pt, fake = make_tools(lambda p: True)

    class Client:
        def __init__(self):
            self.sent, self.n = [], 0
            self.messages = SimpleNamespace(create=self.create)

        def create(self, **kw):
            self.sent.append(kw)
            self.n += 1
            if self.n == 1:
                return SimpleNamespace(stop_reason="tool_use", content=[SimpleNamespace(type="tool_use", id="t1", name="platform_list_accounts", input={})])
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="You have 2 accounts.")])

    c = Client()
    ans = agent.ask("How many accounts?", client=c, platform=pt)
    assert "platform_list_accounts" in [t["name"] for t in c.sent[0]["tools"]]
    assert ans.tool_calls[0]["error"] is None and ("GET", "/api/accounts") in fake.requests
    plain = Client()
    agent.ask("x", client=plain)
    assert not any(t["name"].startswith("platform_") for t in plain.sent[0]["tools"])


def test_cli_confirm_defaults_to_no(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert platform_api.cli_confirm("ok?") is False
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert platform_api.cli_confirm("ok?") is True


def test_platform_eval_set_and_offline_result():
    qs = platform_eval.load()
    assert len(qs) == 15 and {q["type"] for q in qs} == {"call", "declined", "refuse"}
    res = platform_eval.run("offline", write=False)
    assert res["passed"] == res["total"], [r["id"] for r in res["results"] if not r["passed"]]
