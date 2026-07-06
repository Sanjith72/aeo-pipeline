# syntax=docker/dockerfile:1
#
# AEO Studio web (Next.js) — slim standalone runtime image.
#   docker build -t aeo-web .
#
# The API is reached ONLY through the server-side proxy (app/api/[...path]/route.ts),
# configured at RUNTIME with API_BASE_URL (+ API_KEY, injected server-side — nothing
# secret ships to the browser). No build args needed.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
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
