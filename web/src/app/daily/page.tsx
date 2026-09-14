import type { Metadata } from "next";
import Money from "@/components/Money";
import NewsCard from "@/components/NewsCard";
import {
  allItems,
  formatDateLabel,
  groupByDate,
  relativeDay,
  topByHeat,
} from "@/lib/news";
import styles from "./daily.module.css";

export const metadata: Metadata = {
  title: "Daily Digest — AUTOHOT",
  description:
    "A day-by-day recap of China's auto industry: launches, sales data and industry buzz.",
};

export default function DailyPage() {
  const days = groupByDate(allItems);

  return (
    <div className={`container ${styles.page}`}>
      <div className={styles.hero}>
        <h1 className={styles.h1}>Daily Digest</h1>
        <p className={styles.sub}>
          {allItems.length} items across {days.length} days
        </p>
      </div>

      {days.map(([date, items]) => {
        const lead = topByHeat(items, 1)[0];
        return (
          <section key={date} className={styles.day}>
            <header className={styles.dayHead}>
              <h2 className={styles.dayTitle}>{formatDateLabel(date)}</h2>
              <span className={styles.dayMeta}>
                {items.length === 1 ? "1 item" : `${items.length} items`},{" "}
                {relativeDay(date).toLowerCase()}
              </span>
            </header>

            {lead && (
              <p className={styles.lead}>
                <span className={styles.leadTag}>Top story</span>
                <Money>{lead.title}</Money>
              </p>
            )}

            <div className={styles.cards}>
              {items.map((it) => (
                <NewsCard key={it.id} item={it} />
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}
