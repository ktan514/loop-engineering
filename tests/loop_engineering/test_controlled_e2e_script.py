from __future__ import annotations

import subprocess
from pathlib import Path


def test_controlled_e2e_script_has_valid_bash_syntax() -> None:
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "controlled-e2e.sh"

    result = subprocess.run(
        ("bash", "-n", str(script)),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_controlled_e2e_does_not_delete_or_force_push() -> None:
    root = Path(__file__).resolve().parents[2]
    content = (root / "scripts" / "controlled-e2e.sh").read_text(encoding="utf-8")

    assert "gh repo delete" not in content
    assert "gh project delete" not in content
    assert "push --force" not in content
    assert ".loop-controlled-e2e" in content
    assert "reset --hard origin/main" in content
    assert "\\${" not in content
    assert "historical/hold" in content
    assert "restart)" in content
