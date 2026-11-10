"""The Claude tool-use loop, plus an offline fallback when no API key is set."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from card_analytics.assistant import tools

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TURNS = 8
MAX_TOOL_RESULT_CHARS = 20_000

SYSTEM = """You are the colleague assistant for the Card Risk Analytics platform: analytics on anonymised
retail bank data (the Berka dataset, a Czech bank, 1993-1998; amounts in CZK).

How to answer:
- Ground every answer in tool results. Use search_docs for definitions, methodology, controls,
  assumptions and recommendations. For numbers the docs don't state, call list_tables, then run_sql.
- Cite inline in square brackets: the doc `source` for doc facts, e.g. [docs/model-card.md#threshold],
  and [sql] for numbers you computed with run_sql.
- If the tools don't contain the answer, say you don't know. Never guess a number.
- Be brief: one to four sentences, or a small table. Lead with the answer.
- If platform_* tools are available they read the signed-in customer's live accounts and transactions
  (cite as [platform]); the person approves each call. If a call is declined, say no call was made and
  don't retry. Account numbers come back masked; never ask for or reveal full numbers or credentials.
- You can only read data. If asked to change, delete or approve anything, say you can't and who would.
- Never try to identify a real person; the data is masked and must stay that way."""


@dataclass
class Answer:
    text: str
    tool_calls: list[dict] = field(default_factory=list)
    model: str = ""
    offline: bool = False


def _block_get(block, key, default=None):
    return block.get(key, default) if isinstance(block, dict) else getattr(block, key, default)


def make_client():
    import anthropic  # imported lazily so offline mode and tests don't need the package

    return anthropic.Anthropic()


def ask(question: str, client=None, model: str | None = None, max_turns: int = MAX_TURNS, platform=None) -> Answer:
    """Answer a question with Claude, letting it call the tools until it has what it needs.

    `platform` (a platform_api.PlatformTools) adds the card-banking-platform read-only tools; each
    call is confirmed with a person first. Without it only the three local tools exist.
    """
    client = client or make_client()
    specs = tools.TOOL_SPECS + (platform.SPECS if platform else [])
    model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
    messages = [{"role": "user", "content": question}]
    calls: list[dict] = []
    for _ in range(max_turns):
        resp = client.messages.create(model=model, max_tokens=1024, system=SYSTEM, tools=specs, messages=messages)
        content = list(resp.content)
        if resp.stop_reason != "tool_use":
            text = "".join(_block_get(b, "text", "") for b in content if _block_get(b, "type") == "text").strip()
            return Answer(text, calls, model)
        messages.append({"role": "assistant", "content": content})
        results = []
        for block in content:
            if _block_get(block, "type") != "tool_use":
                continue
            name, args = _block_get(block, "name"), _block_get(block, "input") or {}
            result = platform.call(name, args) if platform and name in platform.NAMES else tools.call_tool(name, args)
            calls.append({"tool": name, "input": args, "error": result.get("error") if isinstance(result, dict) else None})
            payload = json.dumps(result, default=str)[:MAX_TOOL_RESULT_CHARS]
            results.append({"type": "tool_result", "tool_use_id": _block_get(block, "id"), "content": payload})
        messages.append({"role": "user", "content": results})
    return Answer("I couldn't finish within the tool-call limit. Try a narrower question.", calls, model)


def offline_answer(question: str, k: int = 3) -> Answer:
    """No API key: return the most relevant doc sections with their citations."""
    hits = tools.search_docs(question, k)
    if not hits:
        return Answer("Nothing in the docs matches that question.", offline=True)
    parts = ["(Offline mode: set ANTHROPIC_API_KEY for full answers. Most relevant sections:)", ""]
    for h in hits:
        snippet = " ".join(h["text"].split())[:400]
        parts.append(f"[{h['source']}] {h['title']}\n  {snippet}...")
    return Answer("\n".join(parts), [{"tool": "search_docs", "input": {"query": question}}], offline=True)


def answer(question: str) -> Answer:
    return ask(question) if os.environ.get("ANTHROPIC_API_KEY") else offline_answer(question)
