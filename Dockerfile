FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home repere
# Code de secours, utilisé si GitHub est injoignable au tout premier démarrage.
COPY app ./app
COPY config ./config
# Démarrage : récupère la dernière version validée sur GitHub, puis lance l'application (docker/entrypoint.py).
COPY Dockerfile docker/entrypoint.py /opt/repere/
COPY docker/repere /usr/local/bin/repere
RUN chmod 755 /usr/local/bin/repere && mkdir -p /app/data && chown -R repere:repere /app/data
USER repere
EXPOSE 8765
ENTRYPOINT ["python", "/opt/repere/entrypoint.py"]
