#!/usr/bin/env python3
"""standalone Loop Engineeringの信頼済みホスト起動器。"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source_root = root / "src"
sys.path.insert(0, str(source_root))


def _source_environment(values: dict[str, str]) -> dict[str, str]:
    environment = dict(values)
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = (
        f"{source_root}{os.pathsep}{existing}" if existing else str(source_root)
    )
    return environment


def main() -> int:
    from loop_engineering.host_launcher import (
        EnvironmentSecretProvider,
        GitHubCredentialUnavailable,
        LaunchEnvironment,
        build_launch_environment,
        launch_vscode,
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    try:
        launch_environment = build_launch_environment(
            root,
            EnvironmentSecretProvider(os.environ),
            os.environ,
        )
    except GitHubCredentialUnavailable:
        print('{"reason":"GITHUB_CREDENTIAL_UNAVAILABLE","review_status":"NOT_RUN"}')
        return 0

    environment = _source_environment(dict(launch_environment.values))
    if args.preflight:
        return subprocess.run(
            (sys.executable, "-m", "loop_engineering.preflight"),
            cwd=root,
            env=environment,
            check=False,
        ).returncode

    launch_vscode(root, LaunchEnvironment(environment))
    return 0


raise SystemExit(main())
