"use client";

import { useEffect, useState } from "react";
import styles from "./ThemeSwitch.module.css";

type Pref = "dark" | "system" | "light";

const OPTIONS: { value: Pref; label: string }[] = [
  { value: "dark", label: "Dark" },
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
];

/** 把偏好解析成实际主题并写到 <html data-theme>，CSS 只需认 dark/light 两种值。 */
function apply(pref: Pref) {
  const actual =
    pref === "system"
      ? window.matchMedia("(prefers-color-scheme: light)").matches
        ? "light"
        : "dark"
      : pref;
  document.documentElement.setAttribute("data-theme", actual);
}

export default function ThemeSwitch() {
  const [pref, setPref] = useState<Pref>("system");

  useEffect(() => {
    let stored: string | null = null;
    try {
      stored = localStorage.getItem("theme");
    } catch {
      // 隐私模式下读不到，按 system 处理
    }
    if (stored === "dark" || stored === "light" || stored === "system") {
      setPref(stored);
    }
  }, []);

  // 只有跟随系统时才需要响应系统主题变化
  useEffect(() => {
    if (pref !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => apply("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [pref]);

  function choose(next: Pref) {
    setPref(next);
    apply(next);
    try {
      localStorage.setItem("theme", next);
    } catch {
      // 写不进去也不影响当前会话
    }
  }

  const index = OPTIONS.findIndex((o) => o.value === pref);

  return (
    <div className={styles.switch} role="radiogroup" aria-label="Theme">
      <span
        className={styles.thumb}
        style={{ transform: `translateX(${index * 100}%)` }}
        aria-hidden="true"
      />
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          role="radio"
          aria-checked={pref === o.value}
          aria-label={o.label}
          title={o.label}
          className={styles.option}
          data-active={pref === o.value}
          onClick={() => choose(o.value)}
        >
          <Icon kind={o.value} />
        </button>
      ))}
    </div>
  );
}

function Icon({ kind }: { kind: Pref }) {
  if (kind === "dark") {
    return (
      <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
        <path
          d="M20 14.2A8.4 8.4 0 1 1 9.8 4a6.8 6.8 0 0 0 10.2 10.2z"
          fill="currentColor"
        />
      </svg>
    );
  }
  if (kind === "light") {
    return (
      <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
        <circle cx="12" cy="12" r="4" fill="currentColor" />
        <g stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
          <path d="M12 2.8v2.3M12 18.9v2.3M21.2 12H18.9M5.1 12H2.8" />
          <path d="M18.5 5.5l-1.6 1.6M7.1 16.9l-1.6 1.6M18.5 18.5l-1.6-1.6M7.1 7.1L5.5 5.5" />
        </g>
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
      <rect
        x="3.2"
        y="5"
        width="17.6"
        height="12"
        rx="2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
      />
      <path
        d="M9 20h6"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
    </svg>
  );
}
