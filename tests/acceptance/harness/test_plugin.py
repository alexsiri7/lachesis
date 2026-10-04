"""The scenario results the plugin writes, from what each test's phases report."""

from __future__ import annotations

import json
from textwrap import dedent

import pytest


def _scenario_results(pytester: pytest.Pytester, tests: str) -> dict[str, dict[str, str]]:
    pytester.makepyfile(
        dedent(
            """
            import pytest

            from harness import scenario
            """
        )
        + dedent(tests)
    )
    pytester.runpytest(
        "-p", "no:asyncio", "-p", "harness.plugin", "--scenario-results", "results.json"
    )
    return json.loads((pytester.path / "results.json").read_text())


def test_a_failed_call_stays_failed_whatever_teardown_reports(pytester: pytest.Pytester) -> None:
    results = _scenario_results(
        pytester,
        """
        @pytest.fixture
        def skips_in_teardown():
            yield
            pytest.skip("teardown")


        @scenario("backlog", "Fails")
        def test_fails(skips_in_teardown):
            assert False
        """,
    )

    assert results == {"backlog": {"Fails": "failed"}}


def test_a_skipped_scenario_is_left_out(pytester: pytest.Pytester) -> None:
    results = _scenario_results(
        pytester,
        """
        @scenario("backlog", "Skipped")
        @pytest.mark.skip
        def test_skipped():
            pass


        @scenario("backlog", "Expected to fail")
        @pytest.mark.xfail(reason="spec change #1")
        def test_expected_to_fail():
            assert False


        @scenario("backlog", "Passes")
        def test_passes():
            pass
        """,
    )

    assert results == {"backlog": {"Expected to fail": "xfail", "Passes": "passed"}}


def test_an_unexpectedly_passing_strict_xfail_is_reported_failed(pytester: pytest.Pytester) -> None:
    results = _scenario_results(
        pytester,
        """
        @scenario("backlog", "Now works")
        @pytest.mark.xfail(strict=True, reason="spec change #1")
        def test_now_works():
            pass
        """,
    )

    assert results == {"backlog": {"Now works": "failed"}}
