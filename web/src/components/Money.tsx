"use client";

import { useCurrency } from "./CurrencyProvider";
import styles from "./Money.module.css";

/**
 * 把文本里的人民币金额按当前货币模式渲染。
 *
 * 只认 `¥` 开头的规范写法（如 `¥89,800`）——翻译阶段已经把「38万元」这类
 * 中文表述统一成了这种形式，并且把「120万台」这种数量排除在外，
 * 所以这里不需要再猜哪个数字是钱。
 */
const MONEY_RE = /¥[\d,]+(?:\.\d+)?/g;

/** 保留 3 位有效数字，避免出现 $13,311.85 这种伪精度。 */
function formatUsd(cny: number, cnyToUsd: number): string {
  const usd = cny * cnyToUsd;
  if (usd === 0) return "$0";
  const magnitude = Math.floor(Math.log10(Math.abs(usd)));
  const step = Math.pow(10, Math.max(0, magnitude - 2));
  const rounded = Math.round(usd / step) * step;
  return `$${rounded.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

export default function Money({ children }: { children: string }) {
  const { mode, rate } = useCurrency();

  if (mode === "cny") return <>{children}</>;

  const parts: React.ReactNode[] = [];
  let last = 0;
  let key = 0;

  for (const m of children.matchAll(MONEY_RE)) {
    const start = m.index ?? 0;
    if (start > last) parts.push(children.slice(last, start));

    const raw = m[0];
    const cny = Number(raw.slice(1).replace(/,/g, ""));
    const usd = formatUsd(cny, rate.cnyToUsd);
    const title = `${raw} at ${rate.cnyToUsd} USD/CNY${rate.stale ? " (rate may be outdated)" : ""}`;

    parts.push(
      mode === "usd" ? (
        <span key={key++} className={styles.converted} title={title}>
          {usd}
        </span>
      ) : (
        <span key={key++} className={styles.pair} title={title}>
          {raw}
          <span className={styles.approx}> ≈ {usd}</span>
        </span>
      ),
    );
    last = start + raw.length;
  }

  if (parts.length === 0) return <>{children}</>;
  if (last < children.length) parts.push(children.slice(last));

  return <>{parts}</>;
}
