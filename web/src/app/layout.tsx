import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import CurrencyProvider from "@/components/CurrencyProvider";
import Sidebar from "@/components/Sidebar";
import { rate } from "@/lib/news";
import "./globals.css";

/**
 * IBM Plex 是为技术文档设计的，这个站的内容就是技术文档：交付量、价格、续航、尺寸。
 * 选它有三条具体理由，不是审美偏好：
 * 1. Mono 的等宽数字让卡片之间的数字竖向对齐，扫读时成列 —— 这正是读者要做的事
 * 2. Sans 的字形在小字号下区分度高（1/l/I、0/O），数据密集的界面里这很关键
 * 3. 同家族有 Plex Sans SC，中文品牌名漏出来时不会跳字体
 *
 * next/font 会自托管字体文件，静态导出后不依赖任何外部 CDN。
 */
const plexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-plex-sans",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "AUTOHOT — China auto industry news, in English",
  description:
    "Deliveries, launches, prices and policy from China's auto industry, translated into English every day.",
  // 两个 feed 由 scripts/build-data.py 写进 public/，这里只是发现入口。
  // 网站本身只有英文；中文 feed 是给中文读者的另一条出口，且不经过翻译链路。
  alternates: {
    types: {
      "application/rss+xml": [
        { url: "/rss.xml", title: "AUTOHOT — English" },
        { url: "/rss.zh.xml", title: "AUTOHOT — 中文" },
      ],
    },
  },
};

/**
 * 在首帧前把主题偏好解析成具体的 dark/light 写入 <html>，避免闪烁。
 * 与 ThemeSwitch 的 apply() 保持同一套规则：system 读 prefers-color-scheme。
 */
const THEME_SCRIPT = `(function(){try{var p=localStorage.getItem("theme")||"system";var t=p==="system"?(window.matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"):p;document.documentElement.setAttribute("data-theme",t);}catch(e){}})();`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      data-theme="dark"
      suppressHydrationWarning
      className={`${plexSans.variable} ${plexMono.variable}`}
    >
      <body>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
        <CurrencyProvider rate={rate}>
          <div className="shell">
            <Sidebar />
            <div className="content">
              <main>{children}</main>
              <footer className="container siteFooter">
                <p>
                  Data collected by auto-news-monitor from Yiche, Sina Auto and
                  Autohome. Prototype for demonstration only.
                </p>
              </footer>
            </div>
          </div>
        </CurrencyProvider>
      </body>
    </html>
  );
}
