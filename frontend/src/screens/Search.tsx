import { useState } from "react";
import { api, ApiError } from "../api";
import type { Inspection, Match } from "../types";
import { Button, ErrorBanner, Field, Panel } from "../components/ui";
import BatchPanel from "./BatchPanel";

// Same rule as the backend (is_copy_product): a trailing "(Copy)", "- Copy", "Copy 2"...
const COPY_RE = /(?:\(|\[|-|–|—|\s)\s*copy(?:\s*\d+)?\s*[)\]]?$/i;
const isCopy = (name: string) => COPY_RE.test(name.trim());
const skipReason = (m: Match): string | null =>
  isCopy(m.name) ? "copy" : m.id === null ? "no product ID" : m.status.toLowerCase() !== "draft" ? "not a draft" : null;

interface Props {
  site: string;
  query: string;
  setQuery: (q: string) => void;
  onInspected: (name: string, i: Inspection, mode: "update" | "create") => void;
  onNewListing: (name: string) => void;
  onLogout: () => void;
  batchId: string | null;
  onBatch: (id: string | null) => void;
}

export default function Search({ site, query, setQuery, onInspected, onNewListing, onLogout, batchId, onBatch }: Props) {
  const [matches, setMatches] = useState<Match[] | null>(null);
  const [threshold, setThreshold] = useState(85);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const [batchRunning, setBatchRunning] = useState(false);
  const locked = busy || batchRunning;
  const eligible = (matches ?? []).filter((m) => skipReason(m) === null);

  async function startAll() {
    if (!matches || !eligible.length) return;
    const skipped = matches.length - eligible.length;
    const ok = window.confirm(
      `Process and publish ${eligible.length} draft product${eligible.length === 1 ? "" : "s"} one by one, with no review step?` +
      (skipped ? `\n\n${skipped} other result${skipped === 1 ? " is" : "s are"} copies or not drafts and will be skipped.` : "")
    );
    if (!ok) return;
    setBusy(true);
    setErr(null);
    try {
      onBatch((await api.startBatch(matches)).id);
    } catch (x) {
      setErr(x as ApiError);
    } finally {
      setBusy(false);
    }
  }

  async function inspect(m: { id: number | null; permalink: string; name: string }) {
    setBusy(true);
    setErr(null);
    try {
      onInspected(m.name, await api.inspect(m.id, m.permalink), "update");
    } catch (x) {
      setErr(x as ApiError);
    } finally {
      setBusy(false);
    }
  }

  async function find(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    setMatches(null);
    try {
      const r = await api.search(query);
      setThreshold(r.threshold);
      const auto = r.auto_selected_url ? r.matches.find((m) => m.permalink === r.auto_selected_url) : null;
      if (auto) {
        await inspect(auto);
        return;
      }
      setMatches(r.matches);
    } catch (x) {
      setErr(x as ApiError);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <Panel
        title="Find the product"
        actions={<span className="text-xs text-muted">Connected to {site} · <button className="underline" onClick={onLogout}>Disconnect</button></span>}
      >
        <form onSubmit={find} className="flex max-w-xl items-end gap-3">
          <div className="flex-1"><Field label="Product name" placeholder="Samsung Galaxy A15" value={query} onChange={(e) => setQuery(e.target.value)} required minLength={2} /></div>
          <Button type="submit" disabled={locked}>{busy ? "Working…" : "Find product"}</Button>
        </form>
        {err && <div className="mt-4"><ErrorBanner code={err.code} message={err.message} /></div>}
        {err?.code === "product_not_found" && query && (
          <p className="mt-3 text-sm text-muted">Not on the site yet? <button className="font-medium text-accent underline" onClick={() => onNewListing(query)}>Create a new listing</button> from a source URL instead.</p>
        )}
      </Panel>

      {batchId && <BatchPanel id={batchId} onClose={() => { onBatch(null); setBatchRunning(false); }} onRunning={setBatchRunning} />}

      {matches && (
        <Panel
          title="Product matches"
          actions={
            <Button onClick={startAll} disabled={locked || eligible.length === 0}>
              Use all One by One ({eligible.length})
            </Button>
          }
        >
          <p className="mb-3 text-sm text-muted">No single match reached {threshold}% confidence with a clear lead, so choose the right one.</p>
          <ul className="divide-y divide-line">
            {matches.map((m) => (
              <li key={m.permalink} className="flex items-center justify-between gap-4 py-3">
                <div className="min-w-0">
                  <p className="truncate font-medium">{m.name}</p>
                  <p className="truncate text-xs text-muted">
                    {m.permalink} · {m.status}
                    {skipReason(m) && <span className="ml-2 font-semibold text-danger">Skipped by "Use all": {skipReason(m)}</span>}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <span className={`text-sm font-semibold ${m.score >= threshold ? "text-ok" : "text-muted"}`}>{Math.round(m.score)}% match</span>
                  <Button variant="secondary" disabled={locked} onClick={() => inspect(m)}>Use this</Button>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}