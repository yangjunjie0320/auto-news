import styles from "./HeatBadge.module.css";

/** 关注度徽标。数值来自 scripts/build-data.py 的占位算法，接入真实互动数据后替换。 */
export default function HeatBadge({ value }: { value: number }) {
  return (
    <span className={styles.badge} title={`Heat score ${value}`}>
      <svg viewBox="0 0 24 24" width="11" height="11" aria-hidden="true">
        <path
          d="M12 2.6s5.6 4.3 5.6 9.4a5.6 5.6 0 1 1-11.2 0c0-2 .9-3.8 1.9-5.2.3 1 1 1.9 1.9 2.3-.3-2.4.6-4.8 1.8-6.5z"
          fill="currentColor"
        />
      </svg>
      {value}
    </span>
  );
}
