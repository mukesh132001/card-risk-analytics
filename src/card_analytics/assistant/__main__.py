"""Ask the colleague assistant a question, or run its evaluation.

Usage:
    python -m card_analytics.assistant "Why is card ownership left out of the default model?"
    python -m card_analytics.assistant --eval            # offline: retrieval + reference SQL (no API key needed)
    python -m card_analytics.assistant --platform "What did I spend last month?"   # + platform tools, you confirm each call
    python -m card_analytics.assistant --eval --online   # Claude answers all 50 questions (needs ANTHROPIC_API_KEY)

Needs the pipeline to have run (docs/ and the rpt_ tables). Database: CARDRISK_DB or
DATABASE_URL, default sqlite:///cardrisk.db. Model: ANTHROPIC_MODEL, default claude-sonnet-5-5.
"""
from __future__ import annotations

import argparse
import os
import sys

from card_analytics.assistant import agent, evaluate, platform_api

HINTS = {
    "AuthenticationError": "Anthropic rejected the API key (401). Check ANTHROPIC_API_KEY: a real key starts with "
    "'sk-ant-api' and is about 100 characters long. Create one at console.anthropic.com > API keys.",
    "PermissionDeniedError": "This API key isn't allowed to use that model (403). Check the key's workspace or set ANTHROPIC_MODEL.",
    "NotFoundError": "Model not found (404). Set ANTHROPIC_MODEL to a current model ID, e.g. claude-sonnet-5-5.",
    "RateLimitError": "Rate limited (429). Wait a minute and try again.",
    "APIConnectionError": "Couldn't reach api.anthropic.com. Check your internet connection or proxy.",
}


def explain_api_error(e: Exception) -> str | None:
    """A one-line fix for Anthropic API errors, or None if it isn't one."""
    if not type(e).__module__.startswith("anthropic"):
        return None
    name, message = type(e).__name__, str(e)
    if "credit balance" in message.lower():
        return "Your Anthropic account has no API credit. Add some at console.anthropic.com > Billing."
    return HINTS.get(name, f"Anthropic API error ({name}): {message[:300]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="*", help="your question")
    parser.add_argument("--eval", action="store_true", help="run the 50-question evaluation")
    parser.add_argument("--online", action="store_true", help="with --eval: have Claude answer (needs ANTHROPIC_API_KEY)")
    parser.add_argument("--platform", action="store_true",
                        help="also let Claude read card-banking-platform accounts/transactions (asks you to confirm each call)")
    args = parser.parse_args(argv)

    if args.eval:
        mode = "online" if args.online else "offline"
        if mode == "online" and not os.environ.get("ANTHROPIC_API_KEY"):
            print("--online needs ANTHROPIC_API_KEY")
            return 2
        try:
            s = evaluate.run(mode)
        except Exception as e:
            hint = explain_api_error(e)
            if hint is None:
                raise
            print(hint)
            return 1
        for r in s["results"]:
            if not r["passed"]:
                print(f"  [FAIL] {r['id']} {r['question']}  (got: {str(r['got'])[:100]})")
        d, q = s["by_type"]["docs"], s["by_type"]["sql"]
        print(f"{mode} eval: {s['passed']}/{s['total']} ({s['rate']:.0%})  docs {d[0]}/{d[1]}  sql {q[0]}/{q[1]}  target {evaluate.TARGET:.0%}")
        print(f"Report: {evaluate.RESULTS.relative_to(evaluate.ROOT)}")
        return 0 if s["rate"] >= evaluate.TARGET else 1

    if not args.question:
        parser.print_help()
        return 2
    try:
        question = " ".join(args.question)
        if args.platform:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                print("--platform needs ANTHROPIC_API_KEY")
                return 2
            ans = agent.ask(question, platform=platform_api.PlatformTools(confirm=platform_api.cli_confirm))
        else:
            ans = agent.answer(question)
    except Exception as e:
        hint = explain_api_error(e)
        if hint is None:
            raise
        print(hint)
        return 1
    print(ans.text)
    if ans.tool_calls and not ans.offline:
        print("\nTools used: " + ", ".join(c["tool"] for c in ans.tool_calls))
    return 0


if __name__ == "__main__":
    sys.exit(main())
