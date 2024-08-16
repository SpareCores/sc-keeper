FROM python:3.11

WORKDIR /pkg
COPY . /pkg
RUN pip install --no-cache-dir .[apm]

ARG SENTRY_RELEASE
ENV SENTRY_RELEASE $SENTRY_RELEASE

CMD ["uvicorn", "sc_keeper.api:app", "--host", "0.0.0.0", "--port", "80", "--log-config", "/pkg/docker/logconfig.ini", "--no-access-log"]
