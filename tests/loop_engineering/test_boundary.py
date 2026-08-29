from pathlib import Path


def test_src_package_is_the_only_loop_engine_implementation() -> None:
    root = Path(__file__).resolve().parents[2]
    source = root / "src" / "loop_engineering"

    assert (source / "supervisor.py").is_file()
    assert (source / "host_entrypoint.py").is_file()
    assert (source / "trusted_worktree.py").is_file()
    assert not (root / "tools" / "loop_engine").exists()


def test_platform_package_does_not_depend_on_legacy_tools_namespace() -> None:
    root = Path(__file__).resolve().parents[2]
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "src" / "loop_engineering").rglob("*.py")
    )
    assert "from tools.loop_engine" not in source
    assert "import tools.loop_engine" not in source
