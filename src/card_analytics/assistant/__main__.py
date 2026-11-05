"""Ask the colleague assistant a question, or run its evaluation.

Usage:
    python -m card_analytics.assistant "Why is card ownership left out of the default model?"
    python -m card_analytics.assistant --eval            # offline: retrieval + reference SQL (no API key needed)
    python -m card_analytics.assistant --eval --online   # Claude answers all 50 questions (needs ANTHROPIC_API_KEY)

Needs the pipeline to have run (docs/ and the rpt_ tables). Database: CARDRISK_DB or
DATABASE_URL, default sqlite:///cardrisk.db. Model: ANTHROPIC_MODEL, default claude-sonnet-5-5.
"""
from __future__ import annotations

import argparse
import os
import sys

from card_analytics.assistant import agent, evaluate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="*", help="your question")
    parser.add_argument("--eval", action="store_true", help="run the 50-question evaluation")
    parser.add_argument("--online", action="store_true", help="with --eval: have Claude answer (needs ANTHROPIC_API_KEY)")
    args = parser.parse_args(argv)

    if args.eval:
        mode = "online" if args.online else "offline"
        if mode == "online" and not os.environ.get("ANTHROPIC_API_KEY"):
            print("--online needs ANTHROPIC_API_KEY")
            return 2
        s = evaluate.run(mode)
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
    ans = agent.answer(" ".join(args.question))
    print(ans.text)
    if ans.tool_calls and not ans.offline:
        print("\nTools used: " + ", ".join(c["tool"] for c in ans.tool_calls))
    return 0


if __name__ == "__main__":
    sys.exit(main())
