FROM nginxinc/nginx-unprivileged:1.27.5-alpine

USER root
RUN printf '%s\n' 'devboxes custom image e2e' > /usr/share/nginx/html/index.html
USER 101
