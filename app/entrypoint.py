"""
Entrypoint for the EnteBus service.

This module:
    - Determines the mode of operation based on the MODE constant.
    - Starts the FastAPI server if MODE is "API_SERVER".
    - Starts the job manager if MODE is "JOB_RUNNER".
    - Keeps the container alive if MODE is "INTERACTIVE".
"""

from app.src.constants import MODE

# ---------------------------------------------------------------------------
## Entrypoint
# ---------------------------------------------------------------------------
if MODE == "API_SERVER":
    import uvicorn
    from app.main import app

    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning", access_log=False)
elif MODE == "JOB_RUNNER":
    import sys
    from app.src.scheduler import start_job_runner

    start_job_runner()
    sys.exit(0)
elif MODE == "INTERACTIVE":
    # Keep container alive for debugging or migrations
    import time

    while True:
        time.sleep(3600)
