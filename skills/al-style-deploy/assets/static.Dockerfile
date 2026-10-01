FROM python:3.12-alpine AS tests
WORKDIR /project
COPY . .
# The agent must add meaningful static-site tests here before publication.
RUN python -m unittest discover -s tests -p 'test_*.py'

FROM nginxinc/nginx-unprivileged:stable-alpine AS runtime
COPY --chown=101:101 . /usr/share/nginx/html
USER root
RUN rm -rf /usr/share/nginx/html/tests /usr/share/nginx/html/.alstyle /usr/share/nginx/html/Dockerfile
USER 101:101
EXPOSE 8080
