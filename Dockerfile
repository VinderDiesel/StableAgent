# StableAgent API image（P7 M6）：需 server extra；非 root。
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY stableagent ./stableagent
RUN pip install --no-cache-dir -e ".[server]"

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 appuser
WORKDIR /app
COPY --from=build /usr/local /usr/local
COPY --from=build /src /app
USER appuser
ENV STABLEAGENT_STATE_DIR=/data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')" || exit 1
# 启动命令由编排注入 create_app；示意：
# CMD ["uvicorn", "examples.api_app:app", "--host", "0.0.0.0", "--port", "8080"]
CMD ["python", "-c", "print('set CMD to uvicorn app factory')"]
