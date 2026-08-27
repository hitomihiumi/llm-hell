"""Constructs the connector set from configuration.

Separate from `registry.py` purely to keep that module free of imports of
every concrete connector - `get_mcp_registry()` imports this lazily, which
also keeps the MCP SDK off the import path of anything that only needs the
registry type.
"""

from app.core.config import Settings
from app.models.source import (
    SOURCE_GITLAB,
    SOURCE_GOOGLE_DRIVE,
    SOURCE_GOOGLE_MAIL,
    SOURCE_POSTGRES_KB,
)
from app.services.mcp.connector import Connector
from app.services.mcp.gitlab import GitLabConnector
from app.services.mcp.google import GoogleWorkspaceConnector
from app.services.mcp.postgres import PostgresKbConnector
from app.services.mcp.semantic import SemanticConnector


def build_connectors(settings: Settings) -> list[Connector]:
    """One connector per searchable surface.

    Built unconditionally, without consulting credentials: a source that is
    unconfigured should report *why* through `health()` and through a
    per-source error on the search response, which is far more useful than
    silently not existing. Whether a source is searched at all is decided by
    `Source.enabled` in the database.

    Drive and Gmail are two connectors over one MCP server, because they have
    different result shapes and different usefulness - a tester should be
    able to switch off email search without losing Drive.
    """
    return [
        GitLabConnector(None, settings, key=SOURCE_GITLAB),
        PostgresKbConnector(None, settings, key=SOURCE_POSTGRES_KB),
        GoogleWorkspaceConnector(None, settings, key=SOURCE_GOOGLE_DRIVE),
        GoogleWorkspaceConnector(None, settings, key=SOURCE_GOOGLE_MAIL),
        # The semantic index. No MCP server behind it - it reads the chunks
        # this deployment indexed itself - but it is a connector so that it
        # goes through the same fusion, the same per-source cap and the same
        # status reporting as everything else, rather than being spliced in
        # ahead of them on a rule written for the occasion.
        SemanticConnector(settings),
    ]
