# Waterflood Optimizer — UI image: static build served by nginx, /api proxied to the API service.
FROM node:22-bookworm-slim AS build
WORKDIR /ui
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . ./
RUN npx tsc -b && npx vite build

FROM nginx:1.27-alpine
COPY --from=build /ui/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD wget -qO- http://127.0.0.1/healthz || exit 1
EXPOSE 80
