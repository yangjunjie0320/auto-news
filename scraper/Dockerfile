# 抓取端：常驻轮询 -> DeepSeek 分类翻译 -> 写 state/digest/*.jsonl
FROM python:3.12-slim-bookworm

# uv 装依赖，uv.lock 已入库，跨平台可复现
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

# 先只拷依赖清单，源码改动不会让依赖层失效
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# 预建属主正确的挂载点。Docker 首次创建空命名卷时会继承镜像中该路径的属主，
# 不这么做的话卷属 root，而容器以非 root 跑，一写就 Permission denied。
RUN groupadd -g 10001 app \
    && useradd -u 10001 -g 10001 -m -s /usr/sbin/nologin app \
    && mkdir -p /data/state/digest /data/logs \
    && chown -R 10001:10001 /data

# 所有路径字段都是相对 CWD 解析的，WORKDIR 必须是项目目录。
# 具体的 state/log 位置由 compose 用 AUTO_NEWS_MONITOR_* 环境变量指到挂载卷。
CMD ["python", "-u", "main.py", "--config", "/config/config.yaml"]
