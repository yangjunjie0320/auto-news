import type { Metadata } from "next";
import HeatBadge from "@/components/HeatBadge";
import Money from "@/components/Money";
import { allItems, formatDateLabel, topByHeat } from "@/lib/news";
import styles from "./ranking.module.css";

export const metadata: Metadata = {
  title: "Trending — AUTOHOT",
  description: "China auto industry stories ranked by heat score.",
};

export default function RankingPage() {
  const ranked = topByHeat(allItems, 30);

  return (
    <div className={`container ${styles.page}`}>
      <div className={styles.hero}>
        <h1 className={styles.h1}>Trending</h1>
        <p className={styles.sub}>Top {ranked.length} by heat score</p>
      </div>

      <ol className={styles.list}>
        {ranked.map((item, i) => (
          <li key={item.id} className={styles.row} data-accent={item.accent}>
            <span className={styles.rank} data-rank={i + 1}>
              {i + 1}
            </span>
            <div className={styles.body}>
              <a
                className={styles.title}
                href={item.url}
                target="_blank"
                rel="noopener noreferrer"
              >
                <Money>{item.title}</Money>
              </a>
              <div className={styles.meta}>
                <span className={styles.label}>{item.label}</span>
                <span>{item.sourceSite}</span>
                <span>{formatDateLabel(item.date)}</span>
                <HeatBadge value={item.heat} />
              </div>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
