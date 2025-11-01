FROM python:3.13-alpine as base

FROM base as builder

RUN apk add --no-cache clang bison flex

RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

WORKDIR /install
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

FROM base
COPY --from=builder /venv /venv

ENV PATH="/venv/bin:$PATH"

COPY src /app
WORKDIR /app

CMD ["python", "main.py"]
