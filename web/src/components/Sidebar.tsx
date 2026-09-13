"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import CurrencySwitch from "./CurrencySwitch";
import ThemeSwitch from "./ThemeSwitch";
import styles from "./Sidebar.module.css";

type IconKind = "star" | "daily" | "rank" | "about";

interface NavItem {
  href: string;
  label: string;
  icon: IconKind;
}

const GROUPS: { title: string; items: NavItem[] }[] = [
  {
    title: "Content",
    items: [
      { href: "/", label: "Featured", icon: "star" },
      { href: "/daily", label: "Daily Digest", icon: "daily" },
      { href: "/ranking", label: "Trending", icon: "rank" },
    ],
  },
  {
    title: "More",
    items: [{ href: "/about", label: "About", icon: "about" }],
  },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className={styles.sidebar}>
      <Link href="/" className={styles.brand}>
        <span className={styles.mark} aria-hidden="true">
          <svg viewBox="0 0 24 24" width="18" height="18">
            <path
              d="M3.4 14.2l1.3-4.1A2.6 2.6 0 0 1 7.2 8.3h9.6a2.6 2.6 0 0 1 2.5 1.8l1.3 4.1"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
            />
            <rect
              x="2.6"
              y="13.8"
              width="18.8"
              height="5"
              rx="1.8"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
            />
            <circle cx="7" cy="18.8" r="1.5" fill="currentColor" />
            <circle cx="17" cy="18.8" r="1.5" fill="currentColor" />
          </svg>
        </span>
        <span className={styles.brandText}>AUTOHOT</span>
      </Link>

      <nav className={styles.nav}>
        {GROUPS.map((group) => (
          <div key={group.title} className={styles.group}>
            <p className={styles.groupTitle}>{group.title}</p>
            {group.items.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={styles.link}
                data-active={pathname === item.href}
              >
                <NavIcon kind={item.icon} />
                {item.label}
              </Link>
            ))}
          </div>
        ))}
      </nav>

      <div className={styles.foot}>
        <CurrencySwitch />
        <ThemeSwitch />
      </div>
    </aside>
  );
}

function NavIcon({ kind }: { kind: IconKind }) {
  const common = {
    viewBox: "0 0 24 24",
    width: 16,
    height: 16,
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.7,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };

  if (kind === "star") {
    return (
      <svg {...common}>
        <path d="M13 2.6L5.4 13.4h5.2l-.6 8 7.6-10.8h-5.2z" />
      </svg>
    );
  }
  if (kind === "daily") {
    return (
      <svg {...common}>
        <rect x="3.6" y="4.6" width="16.8" height="15.4" rx="2.2" />
        <path d="M3.6 9.4h16.8M8.2 3.2v3M15.8 3.2v3M7.6 13.4h6M7.6 16.6h4" />
      </svg>
    );
  }
  if (kind === "rank") {
    return (
      <svg {...common}>
        <path d="M4.6 20V13M12 20V4.8M19.4 20v-9.4" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <circle cx="12" cy="12" r="8.6" />
      <path d="M12 16.4v-4.8M12 8.2h.01" />
    </svg>
  );
}
