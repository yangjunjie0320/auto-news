import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // 全站构建时预渲染，服务器只需 nginx 发静态文件，不跑 Node。
  // 没有 API routes / server actions / 动态路由，可以安全静态导出。
  output: "export",
};

export default nextConfig;
