"""Check that every spec scenario has exactly one acceptance test and every claim is real.

Exits non-zero on a gap only when ``enforce`` is true in ``tests/acceptance/coverage.yml``;
otherwise it reports the gaps and passes.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import yaml

from acceptance import ACCEPTANCE_DIR, Claim, scenario_claims, spec_scenarios


def coverage_gaps(scenarios: dict[str, list[str]], claims: list[Claim]) -> list[str]:
    tests_by_scenario: dict[tuple[str, str], list[str]] = defaultdict(list)
    for claim in claims:
        tests_by_scenario[(claim.capability, claim.title)].append(claim.test)

    gaps = []
    for capability, titles in scenarios.items():
        for title in titles:
            tests = tests_by_scenario.get((capability, title), [])
            if not tests:
                gaps.append(f"Scenario without a test: {capability} / {title}")
            elif len(tests) > 1:
                gaps.append(
                    f"Scenario claimed by {len(tests)} tests: {capability} / {title}: "
                    + ", ".join(tests)
                )
    for claim in claims:
        if claim.title not in scenarios.get(claim.capability, []):
            gaps.append(
                f"Test for a scenario the spec does not contain: {claim.test} "
                f"claims {claim.capability} / {claim.title}"
            )
    return gaps


def enforced(config: Path) -> bool:
    return bool((yaml.safe_load(config.read_text()) or {}).get("enforce", False))


def main() -> int:
    gaps = coverage_gaps(spec_scenarios(), scenario_claims())
    enforce = enforced(ACCEPTANCE_DIR / "coverage.yml")
    for gap in gaps:
        print(gap)
    if not gaps:
        print("Every scenario has exactly one acceptance test.")
        return 0
    if not enforce:
        print(f"\n{len(gaps)} gap(s); report-only while coverage.yml has enforce: false.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
