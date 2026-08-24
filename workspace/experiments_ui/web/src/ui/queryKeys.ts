export const queryKeys = {
  shapeFactory: {
    root: ["shapeFactory"] as const,
    workProductsRoot: ["shapeFactory", "workProducts"] as const,
    workProducts: (opts: { limit: number; hourlyOnly: boolean; family?: string | null }) =>
      ["shapeFactory", "workProducts", opts] as const,
  },
  queue: {
    root: ["queue"] as const,
    snapshot: ["queue", "snapshot"] as const,
    history: ["queue", "history"] as const,
    ledgerRoot: ["queue", "ledger"] as const,
  },
};
