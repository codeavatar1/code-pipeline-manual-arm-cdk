# Use multi-arch nginx, explicitly targeting arm64
FROM --platform=linux/arm64 public.ecr.aws/nginx/nginx:stable-alpine

# Copy your local site assets into the container
COPY ./site-assets/ /usr/share/nginx/html

# Expose port 80 for the web server
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]