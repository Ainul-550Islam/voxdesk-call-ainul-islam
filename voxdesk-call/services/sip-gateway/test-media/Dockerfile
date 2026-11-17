FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/media-test
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt
COPY server.py ./server.py

USER 10001:10001
EXPOSE 8765/tcp 8766/tcp
ENTRYPOINT ["python3", "/opt/media-test/server.py"]
