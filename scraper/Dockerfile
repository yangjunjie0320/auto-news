# 抓取端：常驻轮询 -> DeepSeek 分类翻译 -> 写 state/digest/*.jsonl
FROM python:3.12-slim-bookworm

# uv 装依赖，uv.lock 已入库，跨平台可复现
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

# 先建用户和挂载点，再以该用户身份装依赖和拷源码——放到 COPY 之后再递归 chown
# 会把整个 /app 物化成又一层。
RUN groupadd -g 10001 app \
    && useradd -u 10001 -g 10001 -m -s /usr/sbin/nologin app \
    && mkdir -p /data/state/digest /data/logs /app \
    && chown -R 10001:10001 /data /app

USER 10001:10001
WORKDIR /app

# 只拷依赖清单，源码改动不会让这一层失效。
# 前提是 .dockerignore 排除了宿主机的 .venv——否则下面的 COPY 会把 macOS 的
# venv 盖上来，这一层就白建了。
COPY --chown=10001:10001 pyproject.toml uv.lock ./
# 默认 30s 在网络较差时会下到一半超时（实测 pydantic-core 就卡在这里）
ENV UV_HTTP_TIMEOUT=120
RUN uv sync --frozen --no-dev

COPY --chown=10001:10001 . .

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# 所有路径字段都是相对 CWD 解析的，WORKDIR 必须是项目目录。
# 具体的 state/log 位置由 compose 用 AUTO_NEWS_MONITOR_* 环境变量指到挂载卷。
CMD ["python", "-u", "main.py", "--config", "/config/config.yaml"]
