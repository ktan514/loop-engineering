from __future__ import annotations

import loop_engineering
import loop_engineering.core


def test_package_imports() -> None:
    assert loop_engineering.__name__ == "loop_engineering"
    assert loop_engineering.core.__name__ == "loop_engineering.core"
