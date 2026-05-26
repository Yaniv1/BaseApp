#!/usr/bin/env python3
"""Executable entrypoint for BaseApp."""

import os
import sys


# Ensure project root is importable when running from app/.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from utils.baseutils import *
from utils.apputils import *


class App(Main):
    """Concrete application entrypoint."""

    def __init__(self, config=None, logger=None, results=None):
        super().__init__(config=config, logger=logger, results=results)


@trackit
def execute(app):
    """Execute app lifecycle and return process exit code."""
    app.load_data()
    app.close()
    return app.results


def run(args=sys.argv[1:]):
    """Run the app with explicit config/logger initialization and tracked execution."""
    config = Config(args=args, base_config_path="../config/base.json").config
    logger_settings = config.log.get_dict() if config.log else {}
    logger_settings["base_dir"] = config.base_dir
    logger = create_logger(logger_settings)

    app = App(config=config, logger=logger)
    tracking = execute(app)
    data = {'function': tracking.get('function')} | tracking.get('metrics', {})
    logger.log(message_code="BASE005", data=data)
    app.store_outputs(outputs=["log_html"])
    return tracking

if __name__ == "__main__":
    raise SystemExit(run())
