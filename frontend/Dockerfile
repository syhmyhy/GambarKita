# GambarKita website: build the React app, then serve it with Caddy
# (which also terminates HTTPS and proxies /api, /media and /ws to the API).

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM caddy:2-alpine
COPY --from=build /app/dist /srv
