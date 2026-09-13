import type { NewsItem } from "@/lib/news";
import { formatDateShort, formatWeekday, relativeDay } from "@/lib/news";
import NewsCard from "./NewsCard";
import styles from "./Timeline.module.css";

/** 按日期分好组的条目，[日期, 条目[]][]，调用方保证日期倒序。 */
export default function Timeline({
  groups,
}: {
  groups: [string, NewsItem[]][];
}) {
  return (
    <div className={styles.timeline}>
      {groups.map(([date, items]) => (
        <section key={date} className={styles.day}>
          <header className={styles.dayHead}>
            <h3 className={styles.date}>{formatDateShort(date)}</h3>
            <span className={styles.railCell} aria-hidden="true" />
            <p className={styles.dayMeta}>
              {formatWeekday(date)} · {relativeDay(date)} ·{" "}
              {items.length === 1 ? "1 item" : `${items.length} items`}
            </p>
          </header>

          <div className={styles.items}>
            {items.map((item) => (
              <article
                key={item.id}
                className={styles.item}
                data-accent={item.accent}
              >
                <time className={styles.time} dateTime={item.publishedAt}>
                  {item.time}
                </time>
                <span className={styles.railCell} aria-hidden="true">
                  <span className={styles.dot} />
                </span>
                <NewsCard item={item} showTime={false} />
              </article>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
