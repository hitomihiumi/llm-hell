# Multi-stage so the runtime image carries no build toolchain and no dev
# dependencies. pnpm because package.json pins it as the packageManager.
FROM node:22-alpine AS deps
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
# --frozen-lockfile so a build fails loudly rather than silently resolving
# different versions than the ones that were tested.
RUN pnpm install --frozen-lockfile

FROM node:22-alpine AS builder
WORKDIR /app
RUN corepack enable
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# `next build` resolves next.config.ts's `rewrites()` and freezes the result
# into .next/routes-manifest.json - `next start` never calls it again. So
# BACKEND_URL has to be present HERE, at build time, or the proxy target
# bakes in as next.config.ts's fallback (http://localhost:8000) regardless of
# whatever docker-compose sets on the running container, which is too late.
ARG BACKEND_URL=http://api:8000
ENV BACKEND_URL=$BACKEND_URL
ENV NEXT_TELEMETRY_DISABLED=1
ENV CI=true
RUN pnpm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
# Set CI to true to suppress all interactive pnpm prompts
ENV CI=true

# Create the user/group AND change ownership of the /app directory
RUN corepack enable && addgroup -S app && adduser -S app -G app \
    && chown -R app:app /app

# Ensure all copied files are owned by the 'app' user
COPY --chown=app:app --from=deps /app/node_modules ./node_modules
COPY --chown=app:app --from=builder /app/.next ./.next
COPY --chown=app:app --from=builder /app/public ./public
COPY --chown=app:app --from=builder /app/package.json ./package.json
COPY --chown=app:app --from=builder /app/next.config.ts ./next.config.ts

USER app
EXPOSE 3000
CMD ["pnpm", "run", "start"]