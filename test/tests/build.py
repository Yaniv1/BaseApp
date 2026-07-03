#!/usr/bin/env python3
"""Feature ID: 5.3.2. Standalone build-phase test runner for deployment validation."""

import os
import sys

# Ensure project root is importable when running from test/tests/.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.baseutils import Config, create_logger, get_config
from utils.testutils import TestManager, build_test_config


# Feature 5.3.2.1
def run(args=sys.argv[1:]):
    """Feature ID: 5.3.2.1. Load config, execute the build test phase, store outputs, and return exit code."""
    base_config_path = os.path.join(PROJECT_ROOT, "config", "base.json")
    config_loader = Config(args=args, base_config_path=base_config_path)
    config = config_loader.config

    logger_settings = config.LOG.get_dict() if getattr(config, "LOG", None) else {}
    logger_settings["base_dir"] = config.base_dir
    logger = create_logger(logger_settings)
    config_loader.log_warnings(logger)

    test_config_path = os.path.join(PROJECT_ROOT, "test", "config", "base.json")
    test_config = get_config(config_path=test_config_path)

    build_config = build_test_config(base_config=config.get_dict(), test_config=test_config)
    test_manager = TestManager(config=build_config, logger=logger)

    build_summary = test_manager.run_phase("build")

    n_failures = build_summary.get("n_failures", 0)
    tests_run = build_summary.get("tests_run", 0)
    print(f"\nBuild phase: {tests_run} test(s) run, {n_failures} failure(s).")

    test_manager.store_outputs(output_config="CONFIG.OUTPUT")

    return 1 if n_failures > 0 else 0


if __name__ == "__main__":
    raise SystemExit(run())
