/** Build a Discovery library URL that opens a specific indexed asset. */
export function discoveryLibraryHref(relpath?: string | null): string {
  const norm = (relpath || "").trim().replace(/^\/+/, "").replace(/\\/g, "/");
  if (!norm) return "/discovery";
  return `/discovery?relpath=${encodeURIComponent(norm)}`;
}

/** Read `?relpath=` from a search string (defaults to current location). */
export function parseDiscoveryDeepLinkRelpath(search: string = window.location.search): string | null {
  const sp = new URLSearchParams(search);
  const rel = sp.get("relpath");
  if (!rel || !rel.trim()) return null;
  return rel.trim().replace(/^\/+/, "").replace(/\\/g, "/");
}
