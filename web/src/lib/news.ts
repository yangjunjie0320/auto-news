import raw from "@/data/news.json";

export type Accent = "cyan" | "emerald" | "rose" | "amber";

export interface NewsItem {
  id: string;
  /** 来源类型：新闻站文章还是微博观点。旧数据没有这个字段时按 "web" 处理 */
  kind: "web" | "weibo";
  title: string;
  points: string[];
  label: string;
  labelSlug: string;
  accent: Accent;
  source: string;
  sourceSite: string;
  url: string;
  publishedAt: string;
  date: string;
  time: string;
  heat: number;
  featured: boolean;
  /** false 表示没有英文翻译、回退了中文原文 */
  translated: boolean;
  /** 命中的品牌 slug，已按 exportRank 排序 */
  brands: string[];
}

export interface Brand {
  slug: string;
  name: string;
  origin: "CN" | "INTL";
  /** 出海销量粗排（估计值，见 src/data/brands.json 的说明） */
  exportRank: number;
  /** 当前快照里命中的条目数 */
  count: number;
}

export interface Rate {
  /** 1 CNY 折合多少 USD */
  cnyToUsd: number;
  fetchedAt: string;
  source: string;
  /** true 表示构建时拉取失败、用了兜底值 */
  stale: boolean;
}

export interface LabelMeta {
  name: string;
  slug: string;
  accent: Accent;
}

interface NewsData {
  generatedAt: string;
  rate: Rate;
  labels: LabelMeta[];
  brands: Brand[];
  sources: string[];
  items: NewsItem[];
}

const data = raw as NewsData;

/** 全部条目，已按发布时间倒序。 */
export const allItems: NewsItem[] = data.items;
export const labels: LabelMeta[] = data.labels;
export const sources: string[] = data.sources;
export const generatedAt: string = data.generatedAt;
export const rate: Rate = data.rate;
/** 品牌，已按 exportRank 升序（出海销量粗排）。 */
export const brands: Brand[] = data.brands;

/** 数据快照里最新的一天，用作“今日”。 */
export const latestDate: string = allItems[0]?.date ?? "";

export function itemsByDate(date: string): NewsItem[] {
  return allItems.filter((it) => it.date === date);
}

export function itemsByLabel(slug: string | null): NewsItem[] {
  if (!slug || slug === "all") return allItems;
  return allItems.filter((it) => it.labelSlug === slug);
}

export function itemsByBrand(slug: string): NewsItem[] {
  return allItems.filter((it) => it.brands.includes(slug));
}

export function brandBySlug(slug: string): Brand | undefined {
  return brands.find((b) => b.slug === slug);
}

/** 按关注度取榜单；同分时较新的排前面（allItems 已按时间倒序）。 */
export function topByHeat(items: NewsItem[], limit: number): NewsItem[] {
  return [...items].sort((a, b) => b.heat - a.heat).slice(0, limit);
}

/** 把条目按日期分组，返回 [日期, 条目[]][]，日期倒序。 */
export function groupByDate(items: NewsItem[]): [string, NewsItem[]][] {
  const map = new Map<string, NewsItem[]>();
  for (const it of items) {
    const bucket = map.get(it.date);
    if (bucket) bucket.push(it);
    else map.set(it.date, [it]);
  }
  return [...map.entries()].sort((a, b) => (a[0] < b[0] ? 1 : -1));
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/** "2026-08-01" -> "Aug 1" */
export function formatDateShort(date: string): string {
  const [, m, d] = date.split("-").map(Number);
  return `${MONTHS[m - 1]} ${d}`;
}

/** "2026-08-01" -> "Sat" */
export function formatWeekday(date: string): string {
  const [y, m, d] = date.split("-").map(Number);
  return WEEKDAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
}

/** "2026-08-01" -> "Aug 1 · Sat" */
export function formatDateLabel(date: string): string {
  return `${formatDateShort(date)} · ${formatWeekday(date)}`;
}

/** 相对最新快照日期的自然语言天数。 */
export function relativeDay(date: string): string {
  const diff = Math.round(
    (Date.parse(`${latestDate}T00:00:00Z`) - Date.parse(`${date}T00:00:00Z`)) /
      86_400_000,
  );
  if (diff <= 0) return "Today";
  if (diff === 1) return "Yesterday";
  return `${diff} days ago`;
}
