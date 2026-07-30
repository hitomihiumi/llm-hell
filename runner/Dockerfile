# Sandbox image the agent loop executes commands in (services/sandbox).
# Language runtimes are copied in from their official images rather than
# installed via curl|sh installers or NodeSource-style repo scripts - fewer
# moving parts at build time, and no dependency on those endpoints staying
# reachable.

FROM node:22-bookworm-slim AS node-src
FROM golang:1.23-bookworm AS go-src
FROM rust:1.82-slim-bookworm AS rust-src

FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ripgrep \
        ca-certificates \
        build-essential \
        pkg-config \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip

# Node.js + npm/npx
COPY --from=node-src /usr/local/bin/node /usr/local/bin/node
COPY --from=node-src /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# Go toolchain
COPY --from=go-src /usr/local/go /usr/local/go
ENV PATH="/usr/local/go/bin:${PATH}"

# Rust toolchain (rustc, cargo, standard library - installed by the
# upstream image via rustup into these two directories)
COPY --from=rust-src /usr/local/cargo /usr/local/cargo
COPY --from=rust-src /usr/local/rustup /usr/local/rustup
ENV CARGO_HOME=/usr/local/cargo \
    RUSTUP_HOME=/usr/local/rustup \
    PATH="/usr/local/cargo/bin:${PATH}"

# Non-root: the sandbox backend also runs the container with a read-only
# root filesystem and no network by default, but the user boundary matters
# independently of those (e.g. if a project's own build tooling drops
# privileges expecting not to already be root).
RUN useradd --create-home --uid 10001 --shell /bin/bash runner

WORKDIR /workspace
USER runner

CMD ["sleep", "infinity"]
