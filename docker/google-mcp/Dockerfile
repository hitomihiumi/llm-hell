# The Google Workspace MCP server, bridged from stdio to streamable HTTP.
#
# Two packages:
#
#   @aaronsb/google-workspace-mcp  the MCP server itself. stdio only - it has
#                                  no HTTP transport of any kind.
#   supergateway                   wraps a stdio MCP server and serves it over
#                                  streamable HTTP, so the API talks to this
#                                  the same way it talks to gitlab-mcp and
#                                  there is no subprocess inside the Python
#                                  image.
#
# NOTE it does NOT need `gws`, Google's Workspace CLI, despite what a lot of
# writing about this package says (including an earlier version of this file).
# Verified against v4.2.1: `build/google/client.js` calls Google's REST APIs
# directly with `fetch` and `Authorization: Bearer <token>`, and manages its
# own OAuth in `build/accounts/`. The only `gws` strings in the package are a
# `gws://` URI scheme for MCP resources and temp-file name prefixes, and the
# only child process it ever spawns is a browser during consent. Installing
# @googleworkspace/cli here added a Rust binary download for nothing.
FROM node:22-bookworm-slim

# ca-certificates for TLS to Google's APIs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g --omit=dev \
      @aaronsb/google-workspace-mcp@4.2.1 \
      supergateway@3.4.3 \
    && npm cache clean --force

# Where the server keeps its account registry and OAuth tokens. It resolves
# both from XDG variables (build/executor/paths.js), so pointing those at
# /data puts them on the mounted volumes.
#
# accounts.json    -> $XDG_CONFIG_HOME/google-workspace-mcp/accounts.json
# refresh tokens   -> $XDG_DATA_HOME/google-workspace-mcp/credentials/<slug>.json
ENV XDG_CONFIG_HOME=/data/config \
    XDG_DATA_HOME=/data/share
RUN mkdir -p /data/config /data/share

EXPOSE 3003

# --outputTransport streamableHttp serves /mcp, matching the
# GOOGLE_MCP_URL default in the application settings.
#
# --stateful keeps ONE MCP server process alive across requests. Without it
# supergateway defaults to stateless and respawns node per request, which cost
# ~5s per Drive search - and since the federated fan-out waits for its slowest
# source, that was ~5s on every search in the whole application.
CMD ["supergateway", \
     "--stdio", "google-workspace-mcp", \
     "--outputTransport", "streamableHttp", \
     "--stateful", \
     "--port", "3003"]
