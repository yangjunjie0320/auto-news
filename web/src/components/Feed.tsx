"use client";

import { useMemo, useState } from "react";
import type { Brand, LabelMeta, NewsItem } from "@/lib/news";
import BrandBar from "./BrandBar";
import Timeline from "./Timeline";
import styles from "./Feed.module.css";

interface Props {
  items: NewsItem[];
  labels: LabelMeta[];
  brands: Brand[];
  /** 渲染在筛选区和时间轴之间（热点榜）。筛选生效时隐藏。 */
  children?: React.ReactNode;
}

/** 精选流：品牌栏 + 分类 tab + 搜索，三者联合筛选后交给 Timeline 渲染。 */
export default function Feed({ items, labels, brands, children }: Props) {
  const [label, setLabel] = useState<string>("all");
  const [brand, setBrand] = useState<string | null>(null);
  const [query, setQuery] = useState<string>("");

  const keyword = query.trim().toLowerCase();
  const filtering = keyword !== "" || brand !== null || label !== "all";

  // 分类计数跟随品牌筛选，否则选了 BYD 还显示全站的 66 条 Product Launch。
  // 反过来品牌计数不跟随分类，避免品牌栏的数字来回跳。
  const brandScope = useMemo(
    () => (brand ? items.filter((it) => it.brands.includes(brand)) : items),
    [items, brand],
  );

  const visible = useMemo(() => {
    return items.filter((it) => {
      if (label !== "all" && it.labelSlug !== label) return false;
      if (brand && !it.brands.includes(brand)) return false;
      if (!keyword) return true;
      return (
        it.title.toLowerCase().includes(keyword) ||
        it.points.some((p) => p.toLowerCase().includes(keyword))
      );
    });
  }, [items, label, brand, keyword]);

  const groups = useMemo(() => {
    const map = new Map<string, NewsItem[]>();
    for (const it of visible) {
      const bucket = map.get(it.date);
      if (bucket) bucket.push(it);
      else map.set(it.date, [it]);
    }
    return [...map.entries()];
  }, [visible]);

  const tabs = [{ name: "All", slug: "all" }, ...labels];
  const activeBrand = brand ? brands.find((b) => b.slug === brand) : null;

  return (
    <>
      <BrandBar
        brands={brands}
        active={brand}
        onSelect={(next) => {
          setBrand(next);
          // 新品牌下当前分类可能一条都没有，回到 All 免得显示空列表
          if (next && label !== "all") {
            const scope = items.filter((it) => it.brands.includes(next));
            if (!scope.some((it) => it.labelSlug === label)) setLabel("all");
          }
        }}
        total={items.length}
      />

      <div className={styles.bar}>
        <div className={styles.tabs} role="tablist">
          {tabs.map((t) => {
            const count =
              t.slug === "all"
                ? brandScope.length
                : brandScope.filter((i) => i.labelSlug === t.slug).length;
            return (
              <button
                key={t.slug}
                role="tab"
                aria-selected={label === t.slug}
                className={styles.tab}
                data-active={label === t.slug}
                onClick={() => setLabel(t.slug)}
              >
                {t.name}
                <span className={styles.count}>{count}</span>
              </button>
            );
          })}
        </div>

        <div className={styles.search}>
          <svg
            className={styles.searchIcon}
            viewBox="0 0 24 24"
            width="15"
            height="15"
            aria-hidden="true"
          >
            <circle
              cx="10.8"
              cy="10.8"
              r="6.4"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
            />
            <path
              d="M15.6 15.6l4 4"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
          </svg>
          <input
            className={styles.input}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search…"
            aria-label="Search"
          />
          {query && (
            <button
              className={styles.clear}
              onClick={() => setQuery("")}
              aria-label="Clear search"
            >
              ×
            </button>
          )}
        </div>
      </div>

      {!filtering && children}

      {filtering && (
        <p className={styles.result}>
          <strong>{visible.length}</strong>{" "}
          {visible.length === 1 ? "item" : "items"}
          {activeBrand ? ` from ${activeBrand.name}` : ""}
          {keyword ? ` matching “${query.trim()}”` : ""}
          {(activeBrand || keyword) && (
            <button
              className={styles.reset}
              onClick={() => {
                setBrand(null);
                setQuery("");
                setLabel("all");
              }}
            >
              Reset
            </button>
          )}
        </p>
      )}

      {groups.length === 0 ? (
        <p className={styles.empty}>No items match the current filters.</p>
      ) : (
        <Timeline groups={groups} />
      )}
    </>
  );
}
