FROM python:3.12.5-slim-bookworm

LABEL org.opencontainers.image.title="AlphaLab Python Lab Runtime"
LABEL org.opencontainers.image.description="Disposable no-network runtime for bounded AlphaLab experiments"

RUN python -m pip install --no-cache-dir \
    numpy==2.0.1 \
    pandas==2.2.2 \
    scipy==1.14.0

WORKDIR /tmp
