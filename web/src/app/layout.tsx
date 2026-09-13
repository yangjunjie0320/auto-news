import type { Metadata } from "next";
import CurrencyProvider from "@/components/CurrencyProvider";
import Sidebar from "@/components/Sidebar";
import { rate } from "@/lib/news";
import "./globals.css";

export const metadata: Metadata = {
  title: "AUTOHOT — China Auto Industry News · Daily Picks and Launch Digest",
  description:
    "AUTOHOT curates daily news from China's auto industry: new car launches, sales data and industry buzz. Everything worth knowing today, in one place.",
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
    <html lang="en" data-theme="dark" suppressHydrationWarning>
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
