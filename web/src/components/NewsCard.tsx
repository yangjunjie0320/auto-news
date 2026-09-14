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
    <article className={styles.card} data-accent={item.accent}>
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

      {/* 元信息下沉：来源和分类是读过标题之后才需要的，不该抢在前面 */}
      <div className={styles.meta}>
        {showTime && (
          <time className={styles.time} dateTime={item.publishedAt}>
            {item.time}
          </time>
        )}
        <span className={styles.source}>{item.sourceSite}</span>
        <span className={styles.label}>{item.label}</span>
        {item.featured && <span className={styles.featured}>Featured</span>}
        <HeatBadge value={item.heat} />
      </div>
    </article>
  );
}
