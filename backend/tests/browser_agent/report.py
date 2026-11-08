# ruff: noqa: E501, E731
"""Run every scenario and print a results table:  python -m tests.browser_agent.report"""

import asyncio
import json
import sys
import traceback

from .scenarios import SCENARIOS, Result, hidden_menu_credits


async def run_one(scenario, *args):
    try:
        return await asyncio.wait_for(scenario(*args), timeout=180)
    except Exception as exc:  # a crash is a failed scenario, with the reason
        last = traceback.format_exc().strip().splitlines()[-1]
        return Result(
            scenario.__name__, False, f"{type(exc).__name__}: {str(exc)[:120]} ({last[:80]})"
        )


async def run_all():
    results = [await run_one(s) for s in SCENARIOS]
    results.append(await run_one(hidden_menu_credits, "$3.40", False))
    return results


def table(results):
    width = max(len(r.name) for r in results)
    lines = [
        f"{'PASS' if r.passed else 'FAIL'}  {r.name.ljust(width)}  {r.seconds:>5.1f}s  {r.calls:>3} calls  {r.reads:>2} reads  {r.detail}"
        for r in results
    ]
    lines.append(f"\n{sum(r.passed for r in results)}/{len(results)} passed")
    return "\n".join(lines)


if __name__ == "__main__":
    out = asyncio.run(run_all())
    print(table(out))
    if "--json" in sys.argv:
        print(json.dumps([r.__dict__ for r in out], indent=1))
    sys.exit(0 if all(r.passed for r in out) else 1)
