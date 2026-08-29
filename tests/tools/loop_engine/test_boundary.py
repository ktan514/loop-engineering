from pathlib import Path


def test_src_package_is_the_only_full_loop_engine_implementation() -> None:
    root = Path(__file__).resolve().parents[3]
    source = root / "src" / "loop_engineering"
    compatibility = root / "tools" / "loop_engine"

    assert (source / "supervisor.py").is_file()
    assert (source / "host_entrypoint.py").is_file()
    assert (source / "trusted_worktree.py").is_file()
    assert (compatibility / "supervisor.py").is_file()

    compatibility_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in compatibility.glob("*.py")
    )
    assert "from loop_engineering" in compatibility_sources
    assert "class MissionSupervisor" not in compatibility_sources
    assert "class TrustedWorktree" not in compatibility_sources


def test_platform_package_does_not_depend_on_legacy_tools_namespace() -> None:
    root = Path(__file__).resolve().parents[3]
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "src" / "loop_engineering").rglob("*.py")
    )
    assert "from tools.loop_engine" not in source
    assert "import tools.loop_engine" not in source
