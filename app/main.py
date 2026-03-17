"""Entry point for the Codex OAuth API Server."""

import uvicorn
from config import DEFAULT_HOST, DEFAULT_PORT

if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        reload=True,
        log_level="info",
    )
