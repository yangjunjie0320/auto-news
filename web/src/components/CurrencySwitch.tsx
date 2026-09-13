"use client";

import { useCurrency, type CurrencyMode } from "./CurrencyProvider";
import styles from "./CurrencySwitch.module.css";

const OPTIONS: { value: CurrencyMode; label: string; hint: string }[] = [
  { value: "cny", label: "¥", hint: "Yuan only" },
  { value: "both", label: "¥$", hint: "Yuan with USD" },
  { value: "usd", label: "$", hint: "USD only" },
];

export default function CurrencySwitch() {
  const { mode, setMode, rate } = useCurrency();
  const index = OPTIONS.findIndex((o) => o.value === mode);

  const rateHint = rate.stale
    ? "Rate unavailable at build time; using fallback"
    : `1 CNY = ${rate.cnyToUsd} USD · ${rate.fetchedAt || "build time"}`;

  return (
    <div className={styles.switch} role="radiogroup" aria-label="Currency">
      <span
        className={styles.thumb}
        style={{ transform: `translateX(${index * 100}%)` }}
        aria-hidden="true"
      />
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          role="radio"
          aria-checked={mode === o.value}
          aria-label={o.hint}
          title={`${o.hint} — ${rateHint}`}
          className={styles.option}
          data-active={mode === o.value}
          onClick={() => setMode(o.value)}
        >
          {o.label}
          {o.value === "cny" && rate.stale && (
            <span className={styles.staleDot} aria-hidden="true" />
          )}
        </button>
      ))}
    </div>
  );
}
