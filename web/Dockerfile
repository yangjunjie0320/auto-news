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

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ENV NODE_ENV=production

# 预建属主正确的挂载点，理由同 scraper/Dockerfile。
# /app 也要给写权限：build-data.py 写 src/data/news.json 和 public/*.xml，
# next build 写 .next/ 和 out/。
RUN groupadd -g 10001 app \
    && useradd -u 10001 -g 10001 -m -s /usr/sbin/nologin app \
    && mkdir -p /data/www \
    && chown -R 10001:10001 /data /app

# 循环体在 deploy/rebuild-loop.sh 里，由 compose 挂进来并作为 command
CMD ["node", "--version"]
