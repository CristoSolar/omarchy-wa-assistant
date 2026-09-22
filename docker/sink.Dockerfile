# docker/sink.Dockerfile
FROM python:3.12-alpine
WORKDIR /app
COPY sink/ /app/sink/
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "sink.server"]
