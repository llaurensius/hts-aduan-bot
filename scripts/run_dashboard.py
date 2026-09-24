#!/usr/bin/env python3
"""Run script for HTS Web Dashboard server."""

import os
import sys
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.config import load_config
import uvicorn


def main():
    config = load_config()
    host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.getenv("DASHBOARD_PORT", "8888"))

    print("=" * 60)
    print("           HTS TICKET MONITOR — WEB DASHBOARD")
    print("=" * 60)
    print(f"Server starting on http://{host}:{port}")
    print(f"Access the dashboard in your browser: http://localhost:{port}/dashboard")
    print("=" * 60)

    uvicorn.run(
        "app.dashboard.server:app",
        host=host,
        port=port,
        log_level=config.log_level.lower(),
        access_log=True,
    )


if __name__ == "__main__":
    main()
