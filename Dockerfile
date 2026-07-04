FROM python:3.13-alpine as base

FROM base as builder

RUN apk add --no-cache \
    build-base \
    clang \
    lld \
    python3-dev \
    bison \
    flex

RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

WORKDIR /install
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

FROM base
COPY --from=builder /venv /venv

ENV PATH="/venv/bin:$PATH"

WORKDIR /app

COPY src /app/src

COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic

CMD ["python", "src/main.py"]