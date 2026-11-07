# 只跑网页服务的镜像。引擎/数据层是纯标准库，所以镜像能压到 200MB 以内。
# 本机开发请继续用 .venv，不用这个。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    WEB_HOST=0.0.0.0 \
    PYTHONIOENCODING=utf-8

WORKDIR /app

COPY requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements-server.txt

COPY . .

# 容器里**必须**监听 0.0.0.0，否则宿主机连不进来
ENV WEB_HOST=0.0.0.0

EXPOSE 8000

# 健康检查：能应答就算健康（设了口令时 /api/health 会回 401，那是正常的，见脚本注释）
HEALTHCHECK --interval=30s --timeout=6s --start-period=10s --retries=3 \
    CMD ["python", "deploy/healthcheck.py"]

CMD ["python", "-m", "api.server"]
