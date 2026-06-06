# Sandbox install site — a self-contained nginx image with the one-line
# installer + release tarball baked in. Run it anywhere (xCloud "Custom Docker
# Compose", any host) and it serves:
#   https://<host>/install.sh            (curl -fsSL https://<host>/install.sh | sh)
#   https://<host>/sandbox-latest.tar.gz
#
# Rebuild + push on each release (see build-and-push.sh).
FROM nginx:1.27-alpine

# Serve the landing page at /, correct content types, no-cache on the installer.
RUN printf '%s\n' \
  'server {' \
  '    listen 80 default_server;' \
  '    root /usr/share/nginx/html;' \
  '    index index.html;' \
  '    location = /install.sh { default_type text/x-shellscript; add_header Cache-Control "no-cache"; }' \
  '    location ~ \.tar\.gz$ { default_type application/gzip; add_header Cache-Control "no-cache"; }' \
  '    location / { try_files $uri $uri/ =404; }' \
  '}' > /etc/nginx/conf.d/default.conf

COPY index.html             /usr/share/nginx/html/index.html
COPY install.sh             /usr/share/nginx/html/install.sh
COPY sandbox-latest.tar.gz  /usr/share/nginx/html/sandbox-latest.tar.gz

EXPOSE 80
