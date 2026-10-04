from __future__ import annotations

import pytest


def scenario(capability: str, title: str, /) -> pytest.MarkDecorator:
    """Declare the spec scenario a test covers, exactly as titled after ``#### Scenario:``."""
    return pytest.mark.scenario(capability, title)
