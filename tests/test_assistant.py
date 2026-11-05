"""Colleague assistant tests: SQL guardrails, retrieval, the Claude tool loop (with a fake client) and the eval set."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from card_analytics.assistant import agent, evaluate, tools
from card_analytics.assistant.knowledge import ROOT, Retriever, split_markdown

# ---------- SQL guardrails ----------


@pytest.mark.parametrize(
    "sql",
    [
        "delete from rpt_fact_loans",
        "select 1; drop table rpt_fact_loans",
        "update rpt_dim_client set gender = 'X'",
        "pragma table_info(dim_client)",
        "select * from dim_client",            # star schema with raw IDs: not allowed
        "select * from berka_client",          # clean source with birth dates: not allowed
        "select * from rpt_dim_client c join fact_loans l on 1 = 1",
        "attach database 'x.db' as x",
    ],
)
def test_unsafe_or_out_of_scope_sql_is_rejected(sql):
    with pytest.raises(tools.SQLRejected):
        tools.check_sql(sql)


def test_cte_over_allowed_tables_is_accepted():
    sql = "with m as (select year, count(*) n from dim_date group by year) select * from m join dim_date d on d.year = m.year"
    assert tools.check_sql(sql + ";").startswith("with")


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "rpt.db"
    conn = sqlite3.connect(path)
    conn.execute("create table dim_district (district_id int, region text)")
    conn.executemany("insert into dim_district values (?, ?)", [(i, "Prague" if i == 1 else "other") for i in range(1, 251)])
    conn.commit()
    conn.close()
    return f"sqlite:///{path}"


def test_run_sql_returns_rows_and_caps_them(db):
    small = tools.run_sql("select count(*) as n from dim_district", db)
    assert small == {"columns": ["n"], "rows": [[250]], "row_count": 1, "truncated": False}
    big = tools.run_sql("select * from dim_district", db)
    assert big["row_count"] == tools.MAX_ROWS and big["truncated"]


def test_rejected_and_broken_queries_come_back_as_errors(db):
    assert tools.run_sql("drop table dim_district", db)["error"].startswith("query rejected")
    assert tools.run_sql("select nope from dim_district", db)["error"].startswith("query failed")


def test_connection_is_read_only(db):
    conn = tools.connect_readonly(db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("insert into dim_district values (999, 'x')")


# ---------- retrieval ----------

DOC = """# Model card
Intro.
## Threshold
Picked so recall is at least 75%.
## Limitations
Small sample.
```mermaid
## not a heading inside code
```
"""


def test_markdown_is_split_into_cited_sections():
    chunks = split_markdown("docs/model-card.md", DOC)
    assert [c.source for c in chunks] == ["docs/model-card.md", "docs/model-card.md#threshold", "docs/model-card.md#limitations"]
    assert chunks[1].title == "Model card > Threshold"
    assert "not a heading" in chunks[2].text


def test_retriever_finds_the_right_section():
    r = Retriever(split_markdown("docs/model-card.md", DOC))
    assert tools.search_docs("how was the recall threshold picked", 1, r)[0]["source"] == "docs/model-card.md#threshold"


# ---------- Claude tool loop (fake client, no network) ----------


class FakeClient:
    """Plays back scripted responses and records what the assistant sent."""

    def __init__(self, responses):
        self.responses, self.sent = list(responses), []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.sent.append(json.loads(json.dumps(kwargs, default=lambda o: o.__dict__)))
        return self.responses.pop(0)


def block(**kw):
    return SimpleNamespace(**kw)


def test_tool_loop_runs_tools_and_returns_the_final_answer(monkeypatch):
    monkeypatch.setattr(tools, "call_tool", lambda name, args: {"columns": ["n"], "rows": [[77]]})
    client = FakeClient([
        SimpleNamespace(stop_reason="tool_use", content=[
            block(type="text", text="Let me check."),
            block(type="tool_use", id="tu_1", name="run_sql", input={"sql": "select count(*) n from dim_district"}),
        ]),
        SimpleNamespace(stop_reason="end_turn", content=[block(type="text", text="There are 77 districts [sql].")]),
    ])
    ans = agent.ask("How many districts?", client=client, model="test-model")
    assert ans.text == "There are 77 districts [sql]."
    assert ans.tool_calls == [{"tool": "run_sql", "input": {"sql": "select count(*) n from dim_district"}, "error": None}]
    second = client.sent[1]
    assert second["model"] == "test-model" and second["tools"] == tools.TOOL_SPECS
    result = second["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "tu_1" and "77" in result["content"]


def test_tool_loop_stops_at_the_turn_limit(monkeypatch):
    monkeypatch.setattr(tools, "call_tool", lambda name, args: {})
    looping = [SimpleNamespace(stop_reason="tool_use", content=[block(type="tool_use", id=f"t{i}", name="list_tables", input={})])
               for i in range(3)]
    ans = agent.ask("loop forever", client=FakeClient(looping), max_turns=3)
    assert "tool-call limit" in ans.text and len(ans.tool_calls) == 3


# ---------- eval set ----------


def test_fact_matching_ignores_separators_and_spacing():
    assert evaluate.has_fact("About 2,999 transactions", "2,999|2999")
    assert evaluate.has_fact("recall of 75 %", "75%")
    assert not evaluate.has_fact("recall of 57%", "75%")


def test_eval_set_is_well_formed_and_facts_exist_in_the_docs():
    qs = evaluate.load()
    assert len(qs) == 50 and len({q["id"] for q in qs}) == 50
    for q in qs:
        if q["type"] == "docs":
            text = " ".join((ROOT / s).read_text(encoding="utf-8") for s in q["source"].split("|"))
            assert all(evaluate.has_fact(text, f) for f in q["expected"]), q["id"]
        else:
            tools.check_sql(q["reference_sql"])  # every reference query passes the guard


def test_retrieval_meets_the_target_on_the_real_docs():
    docs_qs = [q for q in evaluate.load() if q["type"] == "docs"]
    s = evaluate.summary(evaluate.offline(docs_qs))
    assert s["by_type"]["docs"][0] / s["by_type"]["docs"][1] >= evaluate.TARGET


# ---------- friendly API errors ----------


def fake_anthropic_error(name, message="boom"):
    cls = type(name, (Exception,), {"__module__": "anthropic._exceptions"})
    return cls(message)


def test_bad_api_key_gives_a_one_line_fix(monkeypatch, capsys):
    from card_analytics.assistant import __main__ as cli

    def raise_401(question):
        raise fake_anthropic_error("AuthenticationError", "Error code: 401 invalid x-api-key")

    monkeypatch.setattr(agent, "answer", raise_401)
    assert cli.main(["hello"]) == 1
    out = capsys.readouterr().out
    assert "rejected the API key" in out and "Traceback" not in out


def test_no_credit_and_other_errors_are_explained():
    from card_analytics.assistant.__main__ import explain_api_error

    assert "no API credit" in explain_api_error(fake_anthropic_error("BadRequestError", "Your credit balance is too low"))
    assert "Rate limited" in explain_api_error(fake_anthropic_error("RateLimitError"))
    assert explain_api_error(ValueError("not from anthropic")) is None
