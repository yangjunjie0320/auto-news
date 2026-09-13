import type { Metadata } from "next";
import { allItems, generatedAt, labels, rate, sources } from "@/lib/news";
import styles from "./about.module.css";

export const metadata: Metadata = {
  title: "About — AUTOHOT",
  description: "Where AUTOHOT's data comes from and how it is produced.",
};

export default function AboutPage() {
  const dates = new Set(allItems.map((it) => it.date));
  const untranslated = allItems.filter((it) => !it.translated).length;

  return (
    <div className={`container ${styles.page}`}>
      <h1 className={styles.h1}>About</h1>

      <p className={styles.lead}>
        AUTOHOT aggregates auto industry news from Yiche, Sina Auto and
        Autohome, organized into a daily featured stream, a digest and a
        trending board. This is a front-end prototype; the data is a build-time
        snapshot.
      </p>

      <dl className={styles.facts}>
        <div className={styles.fact}>
          <dt>Items</dt>
          <dd>{allItems.length}</dd>
        </div>
        <div className={styles.fact}>
          <dt>Days covered</dt>
          <dd>{dates.size}</dd>
        </div>
        <div className={styles.fact}>
          <dt>Sources</dt>
          <dd>{sources.length}</dd>
        </div>
        <div className={styles.fact}>
          <dt>Categories</dt>
          <dd>{labels.length}</dd>
        </div>
      </dl>

      <h2 className={styles.h2}>Sources</h2>
      <ul className={styles.list}>
        {sources.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ul>

      <h2 className={styles.h2}>Currency conversion</h2>
      <p className={styles.note}>
        Prices are published in Chinese yuan. USD figures are converted at{" "}
        <strong>1 CNY = {rate.cnyToUsd} USD</strong>
        {rate.stale ? (
          <>
            {" "}
            — a <strong>fallback rate</strong>, because the live rate could not
            be fetched at build time.
          </>
        ) : (
          <>
            , fetched from {rate.source} at build time
            {rate.fetchedAt ? ` (${rate.fetchedAt})` : ""}.
          </>
        )}{" "}
        Converted values are rounded to three significant figures and are
        indicative only. Use the ¥ / ¥$ / $ switch in the sidebar to change how
        prices are shown.
      </p>

      <h2 className={styles.h2}>Heat score</h2>
      <p className={styles.note}>
        The heat score on each item is a <strong>placeholder</strong>, derived
        from category weight, source weight and title keywords. It is not real
        readership or share data, and will be replaced wholesale once real
        engagement metrics are available. The daily <strong>Featured</strong>{" "}
        badge marks the highest-scoring item of each day, so it inherits the
        same caveat.
      </p>

      <h2 className={styles.h2}>Translation</h2>
      <p className={styles.note}>
        Source articles are published in Chinese. Titles and summaries are
        translated and stored in a per-item cache keyed by article id.
        {untranslated > 0 ? (
          <>
            {" "}
            <strong>{untranslated}</strong> item
            {untranslated === 1 ? "" : "s"} currently{" "}
            {untranslated === 1 ? "has" : "have"} no translation and{" "}
            {untranslated === 1 ? "falls" : "fall"} back to the Chinese
            original.
          </>
        ) : (
          " All items in this snapshot are translated."
        )}
      </p>

      <p className={styles.stamp}>Snapshot generated {generatedAt}</p>
    </div>
  );
}
