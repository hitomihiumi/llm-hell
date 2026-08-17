"""Constructs the connector set from configuration.

Separate from `registry.py` purely to keep that module free of imports of
every concrete connector - `get_mcp_registry()` imports this lazily, which
also keeps `transport.py` (and therefore the MCP SDK) off the import path of
anything that only needs the registry type.
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
from app.services.mcp.postgres import PostgresKbConnector


def build_connectors(settings: Settings) -> list[Connector]:
    """One connector per searchable surface.

    Built unconditionally, without consulting credentials: a source that is
    unconfigured should report *why* through `health()` and through a
    per-source error on the search response, which is far more useful than
    silently not existing. Whether a source is searched at all is decided by
    `Source.enabled` in the database.
    """
    return [
        GitLabConnector(None, settings, key=SOURCE_GITLAB),
        PostgresKbConnector(None, settings, key=SOURCE_POSTGRES_KB),
    ]


# Google is added in the next phase; these are referenced here so the import
# is not flagged as unused and the intended key set is visible in one place.
ALL_SOURCE_KEYS = (SOURCE_GITLAB, SOURCE_POSTGRES_KB, SOURCE_GOOGLE_DRIVE, SOURCE_GOOGLE_MAIL)
