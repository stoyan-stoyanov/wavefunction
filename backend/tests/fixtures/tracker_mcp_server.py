"""An MCP server over a remote transport, so the URL connector is tested against a real one.

Speaks streamable HTTP or SSE depending on `TRACKER_TRANSPORT`. Both are real remote transports
that published servers use, and the older SSE one is not simulated here — the same FastMCP is
asked to serve it.

Port is read from the environment because the test picks a free one — a fixed port makes a test
that passes alone and fails beside anything else listening.
"""

import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("tracker", host="127.0.0.1", port=int(os.environ.get("TRACKER_PORT", "8791")))


@mcp.tool()
def create_issue(title: str) -> str:
    """Open an issue in the tracker."""
    return f"Created: {title}"


if __name__ == "__main__":
    mcp.run(transport=os.environ.get("TRACKER_TRANSPORT", "streamable-http"))  # type: ignore[arg-type]
