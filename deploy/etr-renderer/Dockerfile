# Chromium is the point of this image: the ETR analysis page computes its report
# in the browser, so the only faithful way to read it is to run the browser.
FROM python:3.12-slim

# The deploy contract checks that every running image reports the commit it was
# built from. Without this the sidecar is unverifiable from `docker inspect`:
# a stale image looks identical to a fresh one, which is how PR #75's renderer
# half sat unshipped for nine days.
ARG GIT_SHA=unknown

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DEPLOY_GIT_SHA=${GIT_SHA}

LABEL org.opencontainers.image.revision=${GIT_SHA} \
      org.opencontainers.image.source="https://github.com/Trujillofa/manual-trading-agent"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY app.py .

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
