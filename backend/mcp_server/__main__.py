"""Run the MCP server: `python -m mcp_server` (Streamable HTTP on MCP_HOST:MCP_PORT/mcp)."""

import uvicorn

from app.core.config import get_settings
from app.core.logging import configure_logging
from mcp_server.app import build_app, read_only_sessionmaker
from mcp_server.server import build_server


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.mcp_api_key is None:
        raise SystemExit("MCP_API_KEY must be set (min 16 chars) to start the MCP server.")
    server = build_server(
        read_only_sessionmaker(settings.database_url), max_results=settings.mcp_max_results
    )
    app = build_app(
        server,
        token=settings.mcp_api_key.get_secret_value(),
        extra_allowed_hosts=settings.mcp_extra_allowed_hosts,
    )
    uvicorn.run(app, host=settings.mcp_host, port=settings.mcp_port, log_config=None)


if __name__ == "__main__":
    main()
