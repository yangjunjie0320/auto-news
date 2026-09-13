import type { NewsItem } from "@/lib/news";
import HeatBadge from "./HeatBadge";
import Money from "./Money";
import styles from "./NewsCard.module.css";

interface Props {
  item: NewsItem;
  /** 时间轴里时间已在左侧列显示，卡片内不再重复。 */
  showTime?: boolean;
}

export default function NewsCard({ item, showTime = true }: Props) {
  return (
    <div className={styles.card} data-accent={item.accent}>
      <div className={styles.meta}>
        {showTime && (
          <time className={styles.time} dateTime={item.publishedAt}>
            {item.time}
          </time>
        )}
        <span className={styles.source}>{item.sourceSite}</span>
        <span className={styles.label}>{item.label}</span>
        {item.featured && (
          <span className={styles.featured}>
            <svg viewBox="0 0 24 24" width="10" height="10" aria-hidden="true">
              <path
                d="M12 2.4l2.5 6.3 6.7.4-5.2 4.3 1.7 6.6L12 16.4 6.3 20l1.7-6.6L2.8 9.1l6.7-.4z"
                fill="currentColor"
              />
            </svg>
            Featured
          </span>
        )}
        <HeatBadge value={item.heat} />
      </div>

      <h3 className={styles.title}>
        <a href={item.url} target="_blank" rel="noopener noreferrer">
          <Money>{item.title}</Money>
        </a>
      </h3>

      <ul className={styles.points}>
        {item.points.map((p, i) => (
          <li key={i} className={styles.point}>
            <Money>{p}</Money>
          </li>
        ))}
      </ul>
    </div>
  );
}
