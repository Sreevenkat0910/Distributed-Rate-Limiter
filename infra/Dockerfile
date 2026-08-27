# Single-stage build: none of this project's dependencies (fastapi,
# uvicorn[standard], pydantic-settings, redis, pybreaker) need compiling
# from source on linux/amd64 or linux/arm64 -- they all ship manylinux
# wheels -- so a multi-stage build wouldn't meaningfully shrink the image,
# just add complexity.
FROM python:3.12-slim

WORKDIR /srv/app

COPY pyproject.toml ./
COPY app ./app

RUN pip install --no-cache-dir .

RUN useradd --create-home --shell /usr/sbin/nologin appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
