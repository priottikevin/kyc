FROM nginx:1.27-alpine

# Upstream API service (docker compose service name)
ENV API_UPSTREAM=kyc-api:8000

COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY index.html app.js admin.html admin.js styles.css /usr/share/nginx/html/

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
