"""Build the ``scenario-results`` check run: every spec scenario's outcome for one commit.

Reads the outcomes the acceptance run wrote with ``--scenario-results`` and that run's exit
code, and prints the check-run payload for ``POST /repos/{owner}/{repo}/check-runs``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from acceptance import spec_scenarios

UNTESTED = "untested"


def scenario_results(
    scenarios: dict[str, list[str]], outcomes: dict[str, dict[str, str]]
) -> dict[str, dict[str, str]]:
    """Map capability -> scenario title -> passed, failed, xfail or untested."""
    return {
        capability: {title: outcomes.get(capability, {}).get(title, UNTESTED) for title in titles}
        for capability, titles in scenarios.items()
    }


def check_run(sha: str, results: dict[str, dict[str, str]], exit_code: int) -> dict[str, object]:
    """Fail whenever the acceptance run did, which includes the harness's own tests failing."""
    counts: dict[str, int] = {}
    for titles in results.values():
        for outcome in titles.values():
            counts[outcome] = counts.get(outcome, 0) + 1
    summary = ", ".join(f"{n} {outcome}" for outcome, n in sorted(counts.items())) or "No scenarios"
    if exit_code:
        summary += f"; the acceptance run failed (exit code {exit_code})"
    return {
        "name": "scenario-results",
        "head_sha": sha,
        "status": "completed",
        "conclusion": "failure" if exit_code else "success",
        "output": {
            "title": "Scenario results",
            "summary": summary,
            "text": json.dumps(results, indent=2),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--outcomes", required=True, type=Path)
    parser.add_argument("--exit-code", required=True, type=int, help="of the acceptance run")
    args = parser.parse_args()
    outcomes = json.loads(args.outcomes.read_text())
    results = scenario_results(spec_scenarios(), outcomes)
    print(json.dumps(check_run(args.sha, results, args.exit_code)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
