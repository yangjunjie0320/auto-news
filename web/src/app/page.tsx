import HotBoard from "@/components/HotBoard";
import Feed from "@/components/Feed";
import {
  allItems,
  brands,
  formatDateLabel,
  itemsByDate,
  labels,
  latestDate,
  topByHeat,
} from "@/lib/news";
import styles from "./page.module.css";

export default function HomePage() {
  const today = itemsByDate(latestDate);
  // 今日条目不足时用全量补齐，保证榜单始终有 5 条
  const hot = topByHeat(today.length >= 5 ? today : allItems, 5);

  return (
    <div className={`container ${styles.page}`}>
      <div className={styles.hero}>
        <h1 className={styles.h1}>Featured</h1>
        <p className={styles.sub}>
          {formatDateLabel(latestDate)} · Today&rsquo;s highlights
        </p>
      </div>

      <Feed items={allItems} labels={labels} brands={brands}>
        <div className={styles.board}>
          <HotBoard items={hot} />
        </div>
      </Feed>
    </div>
  );
}
