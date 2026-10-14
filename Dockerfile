FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
# Couche la plus lourde en premier : réutilisée par le cache de GitHub Actions tant que requirements.txt ne change pas.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --uid 10001 --create-home repere \
 && mkdir -p /app/data && chown repere:repere /app/data
COPY app ./app
COPY config ./config
COPY --chmod=755 docker/repere /usr/local/bin/repere
# Commit d'origine, renseigné par GitHub Actions ; affiché par « repere version » et dans Paramètres.
ARG REPERE_COMMIT=""
ENV REPERE_COMMIT=${REPERE_COMMIT}
USER repere
EXPOSE 8765
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765"]
