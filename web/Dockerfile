# syntax=docker/dockerfile:1
#
# AEO Studio web (Next.js) — slim standalone runtime image.
#   docker build -t aeo-web .
#
# Two different lifetimes, and mixing them up is the classic failure here:
#
#   RUNTIME  API_BASE_URL, API_KEY — the API is reached ONLY through the server-side proxy
#            (app/api/[...path]/route.ts), which injects the key server-side, so nothing
#            secret ships to the browser. Pass these as `environment:`; they are read on
#            every request, so changing one needs a restart, not a rebuild.
#
#   BUILD    NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY — `next build` INLINES
#            NEXT_PUBLIC_* into the bundle, so these must be present as build args below.
#            Passing them as `environment:` does nothing at all (see the ARG block).
#
# So build args ARE needed — for the Supabase pair only. docker-compose.yml passes each
# variable in the right place.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
# v5 CH-07: NEXT_PUBLIC_* are inlined by `next build`, NOT read at runtime — so they must be
# present HERE or the image ships with `authEnabled` false: no Sign-in button, no Google
# button, and every pack gate permanently open to an anonymous tier. Passing them only as
# runtime `environment:` (as docker-compose used to) has no effect whatsoever.
# Both values are public by design; nothing secret belongs in a NEXT_PUBLIC_ var.
ARG NEXT_PUBLIC_SUPABASE_URL=""
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY=""
ENV NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL
ENV NEXT_PUBLIC_SUPABASE_ANON_KEY=$NEXT_PUBLIC_SUPABASE_ANON_KEY
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
