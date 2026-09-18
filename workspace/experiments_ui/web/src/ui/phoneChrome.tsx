import React, { createContext, useContext, useEffect, useMemo, useState } from "react";

export type PhoneOverflowItem = {
  id: string;
  label: string;
  hint?: string;
  onSelect: () => void;
};

type PhoneOverflowContextValue = {
  items: PhoneOverflowItem[];
  setItems: (items: PhoneOverflowItem[]) => void;
};

const PhoneOverflowContext = createContext<PhoneOverflowContextValue>({
  items: [],
  setItems: () => {},
});

export function PhoneOverflowProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<PhoneOverflowItem[]>([]);
  const value = useMemo(() => ({ items, setItems }), [items]);
  return <PhoneOverflowContext.Provider value={value}>{children}</PhoneOverflowContext.Provider>;
}

export function usePhoneOverflowItems(): PhoneOverflowItem[] {
  return useContext(PhoneOverflowContext).items;
}

/** Register hamburger actions for the current screen. Clears on unmount. */
export function useRegisterPhoneOverflow(items: PhoneOverflowItem[]) {
  const { setItems } = useContext(PhoneOverflowContext);
  useEffect(() => {
    setItems(items);
    return () => setItems([]);
  }, [items, setItems]);
}
