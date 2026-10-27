"""A tiny stdio MCP server, so the connector path is tested against a real one.

Mocking the client would test our own mock. This is a genuine MCP server over stdio, which is
the transport a user is most likely to add first.
"""

import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("weather")


@mcp.tool(annotations={"readOnlyHint": True})
def forecast(city: str) -> str:
    """The weather where you asked."""
    return f"{city}: 18{os.environ.get('WEATHER_UNITS') or 'C'}, light rain."


@mcp.tool()
def warnings(city: str) -> str:
    """Any severe weather warnings in force."""
    return f"No warnings for {city}."


if __name__ == "__main__":
    mcp.run(transport="stdio")
