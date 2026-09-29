# syntax=docker/dockerfile:1

# ---- 运行镜像（页面 + API）----
FROM python:3.11-slim AS app

ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    AUDIT_DB=/data/audit.json

WORKDIR /app
COPY app/ /app/
RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=5s --timeout=3s --start-period=3s --retries=12 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status == 200 else 1)"

CMD ["python3", "server.py"]

# ---- 一次性核验镜像：构建成功即证明 Dockerfile 可用；入口再跑测试与冒烟 ----
FROM app AS verify
CMD ["sh", "/app/tests/verify_entrypoint.sh"]
