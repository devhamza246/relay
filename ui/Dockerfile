FROM nginxinc/nginx-unprivileged:1.30-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html demo.css demo.js widget.js tour.js /usr/share/nginx/html/
EXPOSE 8080
