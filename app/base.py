#!/usr/bin/env python3
"""Feature ID: 1.1. Executable entrypoint for BaseApp."""

import os
import sys


# Ensure project root is importable when running from app/.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.baseutils import *
from utils.apputils import *
from utils.testutils import TestManager, build_test_config


# Feature 1.1.1
class App(AppManager):
    """Feature ID: 1.1.1. Concrete application entrypoint."""

    def __init__(self, config=None, logger=None, results=None):
        super().__init__(config=config, logger=logger, results=results)


# Feature 1.1.2
@trackit
def execute(app):
    """Feature ID: 1.1.2. Execute app lifecycle and return process exit code."""
    return app.run()


# Feature 1.1.3
def run(args=sys.argv[1:]):
    """Feature ID: 1.1.3. Run the app with explicit config/logger initialization and tracked execution."""
    
    base_config_path = "../config/base.json"
    config = Config(
        args=args,
        base_config_path=base_config_path,
    ).config
    logger_settings = config.LOG.get_dict() if getattr(config, "LOG", None) else {}
    logger_settings["base_dir"] = config.base_dir
    logger = create_logger(logger_settings)
    logger.log(message_code="BASE002", data={"config": config.get_dict()})

    app = App(config=config, logger=logger)

    test_config = build_test_config(runtime_config=config,
                                    test_config_path="../test/config/base.json")
    logger.log(message_code="TEST001", data={"test_config": test_config})
    test_manager = TestManager(config=test_config, logger=logger)
    test_manager.mark_app_state("initialized", run_id=app.RESULTS.run_id)

    tracking = None
    runtime_error = None
    try:
        test_manager.run_phase("prep")
        test_manager.start_live()
        test_manager.mark_app_state("running")
        tracking = execute(app)
        app.RESULTS.runtime_tracking = tracking
        track_data = {'function': tracking.get('function')} | tracking.get('metrics', {})
        logger.log(message_code="BASE005", data=track_data)
        
    except Exception as ex:
        runtime_error = {"type": ex.__class__.__name__, "message": str(ex)}
        app.RESULTS.runtime_error = runtime_error
        logger.log(message_code="BASEE13", message_type="ERROR", data=runtime_error)

    
    app.LOGS = app.logger.logs
    app.store_outputs()

    test_manager.mark_app_state(
        "failed" if runtime_error else "finished",
        runtime_error=runtime_error,
        elapsed_seconds=getattr(app.results, "elapsed_seconds", None),
    )    
    test_manager.stop_live()
    test_manager.run_phase("post")
    test_manager.finalize()
    test_manager.store_outputs(output_config="CONFIG.OUTPUT")
    
    exit_code = 1 if runtime_error or test_manager.RESULTS.report.n_failures > 0 else 0
    return exit_code

if __name__ == "__main__":
    raise SystemExit(run())
