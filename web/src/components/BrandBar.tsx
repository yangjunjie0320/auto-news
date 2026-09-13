"use client";

import type { Brand } from "@/lib/news";
import styles from "./BrandBar.module.css";

interface Props {
  brands: Brand[];
  active: string | null;
  onSelect: (slug: string | null) => void;
  total: number;
}

/**
 * 顶部品牌栏。品牌直接平铺在这里而不是藏进二级菜单，
 * 顺序按 brands.json 的 exportRank（出海销量粗排）。
 */
export default function BrandBar({ brands, active, onSelect, total }: Props) {
  return (
    <div className={styles.wrap}>
      <div className={styles.track} role="tablist" aria-label="Brands">
        <button
          role="tab"
          aria-selected={active === null}
          className={styles.all}
          data-active={active === null}
          onClick={() => onSelect(null)}
        >
          All brands
          <span className={styles.count}>{total}</span>
        </button>

        <span className={styles.divider} aria-hidden="true" />

        {brands.map((b) => (
          <button
            key={b.slug}
            role="tab"
            aria-selected={active === b.slug}
            title={`${b.name} — ${b.count} item${b.count === 1 ? "" : "s"}`}
            className={styles.brand}
            data-active={active === b.slug}
            data-origin={b.origin}
            onClick={() => onSelect(active === b.slug ? null : b.slug)}
          >
            <BrandMark brand={b} />
            <span className={styles.count}>{b.count}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * 品牌标识。目前渲染文字 wordmark；把 SVG 放到 public/brands/<slug>.svg
 * 之后可以在这里换成 <img>，其余布局不用动。
 */
function BrandMark({ brand }: { brand: Brand }) {
  return <span className={styles.wordmark}>{brand.name}</span>;
}
