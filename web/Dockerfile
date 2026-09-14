# 构建端：读 digest -> build-data.py 出 news.json 与两个 RSS -> next build 出 out/
#
# 需要 Node 和 Python 两个运行时：next build 用 Node，build-data.py 用 Python。
# build-data.py 是纯标准库，装个 python3 就够，不需要 pip。
#
# 用 Debian 基底而不是 alpine：rebuild.sh 换 current 软链用 `mv -T`，busybox 不支持。
FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 先建用户和挂载点，再以该用户身份装依赖和拷源码。
# 顺序很重要：如果放到 COPY 之后再 chown -R /app，递归改属主会重写每个文件的
# 元数据，Docker 得把整个 /app（含 400MB+ node_modules）物化成又一层。
RUN groupadd -g 10001 app \
    && useradd -u 10001 -g 10001 -m -s /usr/sbin/nologin app \
    && mkdir -p /data/www /app \
    && chown -R 10001:10001 /data /app

USER 10001:10001
WORKDIR /app

COPY --chown=10001:10001 package.json package-lock.json ./
RUN npm ci

COPY --chown=10001:10001 . .

ENV NODE_ENV=production

# 循环体在 deploy/rebuild-loop.sh 里，由 compose 挂进来并作为 command
CMD ["node", "--version"]
