"""Evaluate the assistant on assistant/evals/questions.jsonl (50 questions).

Offline (no API key, runs in CI):
  * docs questions: is the expected doc among the top-3 search results?  (retrieval hit rate)
  * sql questions: does the reference query, run through the SQL guard, give the expected answer?
Online (ANTHROPIC_API_KEY set, --online):
  * every question: does Claude's answer contain every expected fact?    (answer accuracy, target 85%)
Writes assistant/evals/results.md.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from card_analytics.assistant import agent, tools
from card_analytics.assistant.knowledge import ROOT

QUESTIONS = ROOT / "assistant" / "evals" / "questions.jsonl"
RESULTS = ROOT / "assistant" / "evals" / "results.md"
TARGET = 0.85


def load(path: Path = QUESTIONS) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def normalise(s: str) -> str:
    s = s.lower().replace(" percent", "%").replace(" ", " ").replace(" ", " ")
    s = re.sub(r"\s+%", "%", s)
    return re.sub(r"\s+", " ", s)


def has_fact(answer: str, fact: str) -> bool:
    """A fact is 'a|b|c': any alternative may appear. Numbers match with or without thousands separators."""
    a = normalise(answer)
    a_nocomma = a.replace(",", "")
    return any(normalise(alt) in a or normalise(alt).replace(",", "") in a_nocomma for alt in fact.split("|"))


def offline(questions: list[dict], k: int = 3) -> list[dict]:
    out = []
    for q in questions:
        if q["type"] == "docs":
            sources = [h["source"].split("#")[0] for h in tools.search_docs(q["question"], k)]
            ok = any(s in sources for s in q["source"].split("|"))  # some facts live in more than one doc
            out.append({**q, "passed": ok, "got": ", ".join(dict.fromkeys(sources))})
        else:
            res = tools.run_sql(q["reference_sql"])
            got = "" if "error" in res else str(res["rows"][0][0] if res["rows"] else "")
            out.append({**q, "passed": "error" not in res and all(has_fact(got, f) for f in q["expected"]), "got": res.get("error", got)})
    return out


def online(questions: list[dict], client=None) -> list[dict]:
    out = []
    for q in questions:
        ans = agent.ask(q["question"], client=client)
        out.append({**q, "passed": all(has_fact(ans.text, f) for f in q["expected"]), "got": " ".join(ans.text.split())[:300],
                    "tools": [c["tool"] for c in ans.tool_calls]})
    return out


def summary(results: list[dict]) -> dict:
    by_type = {}
    for t in ("docs", "sql"):
        rs = [r for r in results if r["type"] == t]
        by_type[t] = (sum(r["passed"] for r in rs), len(rs))
    passed = sum(r["passed"] for r in results)
    return {"passed": passed, "total": len(results), "rate": passed / max(len(results), 1), "by_type": by_type}


def report(results: list[dict], mode: str) -> str:
    s = summary(results)
    status = "PASS" if s["rate"] >= TARGET else "BELOW TARGET"
    what = (
        "retrieval hit rate (expected doc in the top 3) for docs questions; reference SQL correctness for sql questions"
        if mode == "offline" else "answers containing every expected fact"
    )
    lines = [
        "# Assistant evaluation",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by `python -m card_analytics.assistant --eval{' --online' if mode == 'online' else ''}`.",
        "",
        f"**Mode: {mode}.** Measures {what}.",
        "",
        f"**Overall: {s['passed']}/{s['total']} ({s['rate']:.0%}), target {TARGET:.0%}: {status}.** "
        f"Docs {s['by_type']['docs'][0]}/{s['by_type']['docs'][1]}, SQL {s['by_type']['sql'][0]}/{s['by_type']['sql'][1]}.",
        "",
        "| ID | Question | Pass | Got |",
        "| --- | --- | :---: | --- |",
    ]
    for r in results:
        got = str(r["got"]).replace("|", "/")
        lines.append(f"| {r['id']} | {r['question']} | {'✓' if r['passed'] else '✗'} | {got[:160]} |")
    return "\n".join(lines) + "\n"


def run(mode: str = "offline", write: bool = True) -> dict:
    qs = load()
    results = offline(qs) if mode == "offline" else online(qs)
    if write:
        RESULTS.write_text(report(results, mode), encoding="utf-8")
    return summary(results) | {"results": results}
