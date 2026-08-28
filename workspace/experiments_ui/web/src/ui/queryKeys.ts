export const queryKeys = {
  shapeFactory: {
    root: ["shapeFactory"] as const,
    mapRoot: ["shapeFactory", "map"] as const,
    map: (opts: { membersLimit: number; jobsLimit: number; jobsPerFamily?: number }) =>
      ["shapeFactory", "map", opts] as const,
    quarantineRoot: ["shapeFactory", "quarantine"] as const,
    quarantine: (opts: { status?: string }) => ["shapeFactory", "quarantine", opts] as const,
    promotionsRoot: ["shapeFactory", "templatePromotions"] as const,
    promotions: (opts?: { includeExpired?: boolean }) =>
      ["shapeFactory", "templatePromotions", opts || { includeExpired: false }] as const,
    inputCurationRoot: ["shapeFactory", "inputCuration"] as const,
    inputCurationState: ["shapeFactory", "inputCuration", "state"] as const,
    inputCurationStills: (opts?: { q?: string; tag?: string; limit?: number }) =>
      ["shapeFactory", "inputCuration", "stills", opts || { q: "", tag: "", limit: 96 }] as const,
    inputCurationEffectiveSources: (familySlug: string) =>
      ["shapeFactory", "inputCuration", "effectiveSources", familySlug] as const,
    workProductsRoot: ["shapeFactory", "workProducts"] as const,
    workProducts: (opts: { limit: number; hourlyOnly: boolean; family?: string | null }) =>
      ["shapeFactory", "workProducts", opts] as const,
    submitAttemptsRoot: ["shapeFactory", "submitAttempts"] as const,
    submitAttempts: (opts?: { limit?: number; errorsOnly?: boolean; family?: string }) =>
      ["shapeFactory", "submitAttempts", opts || { limit: 12, errorsOnly: true }] as const,
  },
  queue: {
    root: ["queue"] as const,
    snapshot: ["queue", "snapshot"] as const,
    history: ["queue", "history"] as const,
    ledgerRoot: ["queue", "ledger"] as const,
    ledgerStatus: ["queue", "ledger", "status"] as const,
    ledgerEvents: (limit: number) => ["queue", "ledger", "events", limit] as const,
  },
};
