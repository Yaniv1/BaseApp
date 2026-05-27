#!/usr/bin/env python3
"""Executable entrypoint for BaseApp."""

import os
import sys


# Ensure project root is importable when running from app/.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.baseutils import *
from utils.apputils import *
from utils.testutils import TestManager, build_test_config


class App(Main):
    """Concrete application entrypoint."""

    def __init__(self, config=None, logger=None, results=None):
        super().__init__(config=config, logger=logger, results=results)


@trackit
def execute(app):
    """Execute app lifecycle and return process exit code."""
    return app.run()


def run(args=sys.argv[1:]):
    """Run the app with explicit config/logger initialization and tracked execution."""
    
    base_config_path = "../config/base.json"
    config = Config(
        args=args,
        base_config_path=base_config_path,
    ).config
    logger_settings = config.log.get_dict() if config.log else {}
    logger_settings["base_dir"] = config.base_dir
    logger = create_logger(logger_settings)
    logger.log(message_code="BASE002", data={"config": config.get_dict()})

    app = App(config=config, logger=logger)

    test_config = build_test_config(runtime_config=config,
                                    test_config_path="../test/config/base.json")
    logger.log(message_code="TEST001", data={"test_config": test_config})
    test_manager = TestManager(config=test_config, logger=logger)
    test_manager.mark_app_state("initialized", run_id=app.results.run_id)

    tracking = None
    runtime_error = None
    try:
        test_manager.run_phase("prep")
        test_manager.start_live()
        test_manager.mark_app_state("running")
        tracking = execute(app)
        app.results.runtime_tracking = tracking
        track_data = {'function': tracking.get('function')} | tracking.get('metrics', {})
        logger.log(message_code="BASE005", data=track_data)
        
    except Exception as ex:
        runtime_error = {"type": ex.__class__.__name__, "message": str(ex)}
        app.results.runtime_error = runtime_error
        logger.log(message_code="BASEE13", message_type="ERROR", data=runtime_error)

    
    app.logs = app.logger.logs
    app.store_outputs()

    test_manager.mark_app_state(
        "failed" if runtime_error else "finished",
        runtime_error=runtime_error,
        elapsed_seconds=getattr(app.results, "elapsed_seconds", None),
    )    
    test_manager.stop_live()
    test_manager.run_phase("post")
    test_manager.finalize()
    test_manager.store_outputs(output_config="config.output")
    
    exit_code = 1 if runtime_error or test_manager.results.report.n_failures > 0 else 0
    return exit_code

if __name__ == "__main__":
    raise SystemExit(run())
