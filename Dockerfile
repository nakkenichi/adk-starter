# syntax=docker/dockerfile:1
# Runtime image for both deployment targets: Vertex AI Agent Runtime builds this Dockerfile
# remotely from the source upload, and Cloud Run runs the image pushed by deploy.yml.

FROM python:3.12-slim

LABEL description="ADK Starter — Google ADK agent served by FastAPI"

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    GOOGLE_GENAI_USE_VERTEXAI="TRUE" \
    GOOGLE_CLOUD_LOCATION="global"

WORKDIR /code

COPY ./pyproject.toml ./README.md ./uv.lock ./
COPY ./app ./app

RUN uv sync --frozen --no-dev --default-index https://pypi.org/simple

ARG AGENT_VERSION=0.1.0
ENV AGENT_VERSION=${AGENT_VERSION}

EXPOSE 8080

CMD ["uv", "run", "--no-sync", "uvicorn", "app.fast_api_app:app", "--host", "0.0.0.0", "--port", "8080"]
