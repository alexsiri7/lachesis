from __future__ import annotations

from pathlib import Path

from acceptance import Claim, scenario_claims, spec_scenarios
from scenario_coverage import coverage_gaps, enforced

SCENARIOS = {"backlog": ["Everything blocked", "Nothing blocked"]}


def test_a_scenario_without_a_test_names_capability_and_scenario() -> None:
    claims = [Claim("backlog", "Everything blocked", "t.py::test_a")]
    assert coverage_gaps(SCENARIOS, claims) == [
        "Scenario without a test: backlog / Nothing blocked"
    ]


def test_two_tests_for_one_scenario_names_both() -> None:
    claims = [
        Claim("backlog", "Everything blocked", "t.py::test_a"),
        Claim("backlog", "Everything blocked", "t.py::test_b"),
        Claim("backlog", "Nothing blocked", "t.py::test_c"),
    ]
    assert coverage_gaps(SCENARIOS, claims) == [
        "Scenario claimed by 2 tests: backlog / Everything blocked: t.py::test_a, t.py::test_b"
    ]


def test_a_test_for_a_deleted_scenario_names_the_test() -> None:
    claims = [
        Claim("backlog", "Everything blocked", "t.py::test_a"),
        Claim("backlog", "Nothing blocked", "t.py::test_b"),
        Claim("backlog", "Gone", "t.py::test_c"),
    ]
    assert coverage_gaps(SCENARIOS, claims) == [
        "Test for a scenario the spec does not contain: t.py::test_c claims backlog / Gone"
    ]


def test_full_coverage_has_no_gaps() -> None:
    claims = [
        Claim("backlog", "Everything blocked", "t.py::test_a"),
        Claim("backlog", "Nothing blocked", "t.py::test_b"),
    ]
    assert coverage_gaps(SCENARIOS, claims) == []


def test_spec_scenarios_are_read_per_capability(tmp_path: Path) -> None:
    spec = tmp_path / "backlog" / "spec.md"
    spec.parent.mkdir()
    spec.write_text(
        "### Requirement: R\n\n#### Scenario: Everything blocked\n- GIVEN x\n\n"
        "#### Scenario: Nothing blocked  \n- GIVEN y\n"
    )
    assert spec_scenarios(tmp_path) == SCENARIOS


def test_a_scenario_heading_without_a_title_names_no_scenario(tmp_path: Path) -> None:
    spec = tmp_path / "backlog" / "spec.md"
    spec.parent.mkdir()
    spec.write_text("#### Scenario:\n- GIVEN x\n\n#### Scenario: \t\n- GIVEN y\n")
    assert spec_scenarios(tmp_path) == {"backlog": []}


def test_claims_are_read_from_markers_outside_the_harness(tmp_path: Path) -> None:
    acceptance = tmp_path / "tests" / "acceptance"
    (acceptance / "backlog").mkdir(parents=True)
    (acceptance / "harness").mkdir()
    (acceptance / "backlog" / "test_backlog.py").write_text(
        "from harness import scenario\n\n"
        "@scenario('backlog', 'Everything blocked')\n"
        "async def test_a(lachesis): ...\n\n"
        "class TestB:\n"
        "    @scenario('backlog', 'Nothing blocked')\n"
        "    def test_b(self): ...\n"
    )
    (acceptance / "harness" / "test_harness.py").write_text(
        "@scenario('backlog', 'Everything blocked')\ndef test_h(): ...\n"
    )
    assert scenario_claims(acceptance, tmp_path) == [
        Claim("backlog", "Everything blocked", "tests/acceptance/backlog/test_backlog.py::test_a"),
        Claim(
            "backlog", "Nothing blocked", "tests/acceptance/backlog/test_backlog.py::TestB::test_b"
        ),
    ]


def test_enforcement_is_read_from_coverage_yml(tmp_path: Path) -> None:
    config = tmp_path / "coverage.yml"
    config.write_text("enforce: false\n")
    assert not enforced(config)
    config.write_text("enforce: true\n")
    assert enforced(config)
