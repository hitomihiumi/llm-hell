# The Google Workspace MCP server, bridged from stdio to streamable HTTP.
#
# Three packages, and the reason for each:
#
#   @aaronsb/google-workspace-mcp  the MCP server itself. stdio only - it has
#                                  no HTTP transport of any kind.
#   @googleworkspace/cli           provides `gws`, Google's Workspace CLI.
#                                  The MCP server shells out to it and does
#                                  NOT depend on it, so installing the server
#                                  alone yields a server that cannot do
#                                  anything. It is a Rust binary fetched by
#                                  the package's postinstall; glibc is used
#                                  rather than Alpine/musl because that is the
#                                  better-tested target.
#   supergateway                   wraps a stdio MCP server and serves it over
#                                  streamable HTTP, so the API talks to this
#                                  the same way it talks to gitlab-mcp and
#                                  there is no subprocess inside the Python
#                                  image.
FROM node:22-bookworm-slim

# ca-certificates for the gws postinstall download and for Google's APIs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g --omit=dev \
      @aaronsb/google-workspace-mcp@4.2.1 \
      @googleworkspace/cli \
      supergateway@3.4.3 \
    && npm cache clean --force

# Fail the build rather than the first search if `gws` did not land on PATH -
# the MCP server's failure mode without it is an empty result set, which
# looks like "no matching documents".
RUN command -v gws >/dev/null || (echo "gws is not on PATH - @googleworkspace/cli postinstall failed" && exit 1)

# Where the server keeps its account registry and OAuth tokens. Mounted from
# ./secrets/google-workspace so they survive a rebuild, and read-write
# because the server refreshes tokens in place.
ENV XDG_CONFIG_HOME=/data/config \
    XDG_DATA_HOME=/data/share
RUN mkdir -p /data/config /data/share

EXPOSE 3003

# --outputTransport streamableHttp serves /mcp, matching the
# GOOGLE_MCP_URL default in the application settings.
CMD ["supergateway", \
     "--stdio", "google-workspace-mcp", \
     "--outputTransport", "streamableHttp", \
     "--port", "3003"]
