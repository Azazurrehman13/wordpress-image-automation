"""Start the backend.  Do not use `uvicorn --reload` on Windows: Playwright needs the Proactor event loop."""
import asyncio
import sys

import uvicorn

from app.config import cfg

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    uvicorn.run("app.main:app", host=cfg.host, port=cfg.port, reload=False, log_level="info")
