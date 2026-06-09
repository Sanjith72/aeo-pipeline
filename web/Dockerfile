# syntax=docker/dockerfile:1
#
# AEO Studio web (Next.js) — slim standalone runtime image.
#   docker build -t aeo-web --build-arg NEXT_PUBLIC_API_BASE=http://localhost:8000 .
#
# NEXT_PUBLIC_API_BASE is baked at BUILD time (it ships to the browser), so it must be the
# URL the *browser* uses to reach the API — for local compose that's the host-published
# http://localhost:8000, not the in-network http://api:8000.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
RUN addgroup -S app && adduser -S app -G app
COPY --from=build /app/public ./public
COPY --from=build --chown=app:app /app/.next/standalone ./
COPY --from=build --chown=app:app /app/.next/static ./.next/static
USER app
EXPOSE 3000
ENV PORT=3000 HOSTNAME=0.0.0.0
CMD ["node", "server.js"]
