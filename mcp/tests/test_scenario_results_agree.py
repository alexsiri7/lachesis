"""Lachesis reads the scenario results CI publishes, so both must name scenarios and results alike.

CI's delivery checks live outside the deployed app, so neither side imports the other.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

import pytest

from tools.specs import SCENARIO_RESULTS, scenario_titles

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_DIR = REPO_ROOT / "ci"


@pytest.fixture()
def ci(monkeypatch: pytest.MonkeyPatch) -> dict[str, ModuleType]:
    monkeypatch.syspath_prepend(str(CI_DIR))
    return {name: importlib.import_module(name) for name in ("acceptance", "scenario_results")}


@pytest.mark.parametrize(
    "spec",
    [
        "#### Scenario: Ordered\n- GIVEN x\n#### Scenario:Parked  \n- GIVEN y",
        "#### Scenario:\n- GIVEN an untitled scenario\n#### Scenario: Next one\n- GIVEN x\n",
        "#### Scenario: \t\n#### Scenario:\t Tabbed\t\n ### Scenario: Not one\n",
        "##### Scenario: Too deep\n#### Scenarios: Plural\n#### Scenario: Last",
    ],
)
def test_lachesis_and_ci_name_the_same_scenarios(ci: dict[str, ModuleType], spec: str) -> None:
    assert scenario_titles(spec) == ci["acceptance"].scenario_titles(spec)


def test_lachesis_and_ci_name_the_same_scenarios_in_every_spec(
    ci: dict[str, ModuleType],
) -> None:
    specs = sorted((REPO_ROOT / "openspec" / "specs").glob("*/spec.md"))

    assert specs
    for spec in specs:
        text = spec.read_text()
        assert scenario_titles(text) == ci["acceptance"].scenario_titles(text), spec


def test_lachesis_knows_every_result_ci_publishes(ci: dict[str, ModuleType]) -> None:
    assert set(SCENARIO_RESULTS) == set(ci["scenario_results"].RESULTS)
