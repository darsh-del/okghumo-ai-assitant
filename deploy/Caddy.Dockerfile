# Caddy image that also carries the built frontend (static files) at /srv/ui.
FROM node:20-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
# VITE_API_URL unset => the page calls the API on its own origin (Caddy proxies it)
RUN npm run build

FROM caddy:2
COPY --from=ui /ui/dist /srv/ui
