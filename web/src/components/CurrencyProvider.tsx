"use client";

import { createContext, useContext, useEffect, useState } from "react";
import type { Rate } from "@/lib/news";

export type CurrencyMode = "cny" | "usd" | "both";

interface CurrencyContextValue {
  mode: CurrencyMode;
  setMode: (m: CurrencyMode) => void;
  rate: Rate;
}

const CurrencyContext = createContext<CurrencyContextValue | null>(null);

export function useCurrency(): CurrencyContextValue {
  const ctx = useContext(CurrencyContext);
  if (!ctx) throw new Error("useCurrency must be used inside CurrencyProvider");
  return ctx;
}

const STORAGE_KEY = "currency";

export default function CurrencyProvider({
  rate,
  children,
}: {
  rate: Rate;
  children: React.ReactNode;
}) {
  // 默认只显示美元；想看原始人民币价可以用侧栏开关切回 ¥ 或 ¥$
  const [mode, setModeState] = useState<CurrencyMode>("usd");

  useEffect(() => {
    let stored: string | null = null;
    try {
      stored = localStorage.getItem(STORAGE_KEY);
    } catch {
      // 隐私模式下读不到，用默认值
    }
    if (stored === "cny" || stored === "usd" || stored === "both") {
      setModeState(stored);
    }
  }, []);

  function setMode(next: CurrencyMode) {
    setModeState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // 写不进去不影响当前会话
    }
  }

  return (
    <CurrencyContext.Provider value={{ mode, setMode, rate }}>
      {children}
    </CurrencyContext.Provider>
  );
}
