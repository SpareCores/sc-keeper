FROM python:3.13

WORKDIR /pkg
COPY . /pkg
ARG KEEPER_VERSION
RUN if [ -n "$KEEPER_VERSION" ]; then \
        sed -i "s/^version = .*/version = \"$KEEPER_VERSION\"/" pyproject.toml; \
    fi
RUN pip install --no-cache-dir .[apm,security]
# use the inspector rules from the sc-crawler GitHub repo (branch or commit) when
# compatible with the installed sc-crawler version (set to empty to disable);
# note that this layer must not be cached when using a branch name
ARG INSPECTOR_RULES_REF=main
RUN python docker/overlay_inspector_rules.py "$INSPECTOR_RULES_REF"

ARG SENTRY_RELEASE
ENV SENTRY_RELEASE=$SENTRY_RELEASE

CMD ["uvicorn", "sc_keeper.api:app", "--host", "0.0.0.0", "--port", "80", "--log-config", "/pkg/docker/logconfig.ini", "--no-access-log"]
