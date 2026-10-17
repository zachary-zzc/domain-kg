# Resolve updates deliberately; this multi-platform digest was read from the official registry.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
FROM ${PYTHON_IMAGE} AS dependencies
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
COPY requirements.lock ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir --no-compile --only-binary=:all: --require-hashes -r requirements.lock
ARG APPLICATION_WHEEL=domain_kg-0.1.0-py3-none-any.whl
COPY dist/${APPLICATION_WHEEL} /wheels/
RUN /opt/venv/bin/python -m pip install --no-index --no-deps --no-compile /wheels/*.whl

FROM ${PYTHON_IMAGE} AS runtime
COPY --from=dependencies /opt/venv /opt/venv
RUN groupadd --gid 10001 domain-kg \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin domain-kg
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:8000/healthz', timeout=2).read()"]
CMD ["uvicorn", "domain_kg.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
