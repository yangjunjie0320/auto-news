import Link from "next/link";
import type { NewsItem } from "@/lib/news";
import Money from "./Money";
import styles from "./HotBoard.module.css";

export default function HotBoard({ items }: { items: NewsItem[] }) {
  return (
    <section className={styles.board}>
      <header className={styles.head}>
        <h2 className={styles.heading}>Trending Today</h2>
        <Link href="/ranking" className={styles.more}>
          View all ›
        </Link>
      </header>

      <ol className={styles.list}>
        {items.map((item, i) => (
          <li key={item.id} className={styles.row}>
            <span className={styles.rank} data-rank={i + 1}>
              {i + 1}
            </span>
            <a
              className={styles.link}
              href={item.url}
              target="_blank"
              rel="noopener noreferrer"
            >
              <Money>{item.title}</Money>
            </a>
            <span className={styles.heat}>
              {item.heat}
              <span className={styles.heatUnit}>heat</span>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
