import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { Inspection, Match } from "../types";
import { Button, ErrorBanner, Field, Panel } from "../components/ui";
import BatchPanel from "./BatchPanel";

// Same rule as the backend (is_copy_product): a trailing "(Copy)", "- Copy", "Copy 2"...
const COPY_RE = /(?:\(|\[|-|–|—|\s)\s*copy(?:\s*\d+)?\s*[)\]]?$/i;
const isCopy = (name: string) => COPY_RE.test(name.trim());
const skipReason = (m: Match): string | null =>
  isCopy(m.name) ? "copy" : m.id === null ? "no product ID" : m.status.toLowerCase() !== "draft" ? "not a draft" : null;
const categoriesOf = (m: Match): string[] => (m as Match & { categories?: string[] }).categories ?? [];

interface Category { name: string; count: number }

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
  const [searched, setSearched] = useState(""); // the text the list on screen was filtered by ("" = all drafts)
  const [categories, setCategories] = useState<Category[]>([]); // categories that contain drafts (quick filters)
  const [threshold, setThreshold] = useState(85);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const [batchRunning, setBatchRunning] = useState(false);
  const locked = busy || batchRunning;
  const eligible = (matches ?? []).filter((m) => skipReason(m) === null);

  // Load the list: every draft product when text is empty, otherwise drafts whose name or category matches.
  async function load(text: string) {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.search(text);
      setThreshold(r.threshold);
      setMatches(r.matches);
      const cats = (r as unknown as { categories?: Category[] | null }).categories;
      if (cats) setCategories(cats); // absent on a normal filtered result: keep the chips we already have
    } catch (x) {
      setMatches([]);
      setErr(x as ApiError);
    } finally {
      setSearched(text);
      setBusy(false);
    }
  }

  // All draft products appear as soon as the user is connected (this page opens right after login).
  useEffect(() => {
    void load(query.trim());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function startAll() {
    if (!matches || !eligible.length) return;
    const skipped = matches.length - eligible.length;
    const ok = window.confirm(
      `Process and publish ${eligible.length} draft product${eligible.length === 1 ? "" : "s"} shown in the list, one by one, with no review step?` +
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

  function find(e: React.FormEvent) {
    e.preventDefault();
    void load(query.trim());
  }

  function showAll() {
    setQuery("");
    void load("");
  }

  function pickCategory(name: string) {
    setQuery(name);
    void load(name);
  }

  const empty = matches !== null && matches.length === 0 && !err;

  return (
    <div className="space-y-4">
      <Panel
        title="Search (optional)"
        actions={<span className="text-xs text-muted">Connected to {site} · <button className="underline" onClick={onLogout}>Disconnect</button></span>}
      >
        <form onSubmit={find} className="flex max-w-xl items-end gap-3">
          <div className="flex-1">
            <Field
              label="Product name or category"
              placeholder="Leave empty to see all drafts, or type e.g. pouch, sandals"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <Button type="submit" disabled={locked}>{busy ? "Working…" : "Search"}</Button>
          {searched && <Button type="button" variant="secondary" disabled={locked} onClick={showAll}>Show all drafts</Button>}
        </form>

        {categories.length > 0 && (
          <div className="mt-4">
            <p className="mb-2 text-xs font-medium text-muted">Categories with draft products</p>
            <div className="flex flex-wrap gap-2">
              {categories.map((c) => (
                <button
                  key={c.name}
                  type="button"
                  disabled={locked}
                  onClick={() => pickCategory(c.name)}
                  className={`rounded-full border px-3 py-1 text-xs font-medium ${
                    searched.toLowerCase() === c.name.toLowerCase() ? "border-accent bg-accent text-white" : "border-line text-ink hover:border-accent"
                  }`}
                >
                  {c.name} ({c.count})
                </button>
              ))}
            </div>
          </div>
        )}

        {err && <div className="mt-4"><ErrorBanner code={err.code} message={err.message} /></div>}
      </Panel>

      {batchId && <BatchPanel id={batchId} onClose={() => { onBatch(null); setBatchRunning(false); }} onRunning={setBatchRunning} />}

      {matches === null && busy && <p className="px-1 text-sm text-muted">Loading draft products…</p>}

      {empty && (
        <Panel title="No draft products found">
          <p className="text-sm text-muted">
            {searched
              ? `No draft product has “${searched}” in its name or category. Pick one of the categories above, or search another word.`
              : "There are no draft products on the connected site."}
          </p>
          {searched && (
            <p className="mt-3 text-sm text-muted">Not on the site yet? <button className="font-medium text-accent underline" onClick={() => onNewListing(searched)}>Create a new listing</button> from a source URL instead.</p>
          )}
        </Panel>
      )}

      {matches && matches.length > 0 && (
        <Panel
          title={`Draft products${searched ? ` matching “${searched}”` : ""} (${matches.length})`}
          actions={
            <Button onClick={startAll} disabled={locked || eligible.length === 0}>
              Use all One by One ({eligible.length})
            </Button>
          }
        >
          <p className="mb-3 text-sm text-muted">
            {searched
              ? `All ${matches.length} draft product${matches.length === 1 ? "" : "s"} with “${searched}” in the name or category. Scroll for more.`
              : `All ${matches.length} draft product${matches.length === 1 ? "" : "s"} on the site. Scroll for more, or search / pick a category above to narrow the list.`}
          </p>
          <ul className="divide-y divide-line overflow-y-auto pr-2" style={{ maxHeight: "65vh" }}>
            {matches.map((m) => (
              <li key={`${m.id ?? m.permalink}`} className="flex items-center justify-between gap-4 py-3">
                <div className="min-w-0">
                  <p className="truncate font-medium">{m.name}</p>
                  <p className="truncate text-xs text-muted">
                    {m.permalink} · {m.status}
                    {categoriesOf(m).length > 0 && <> · {categoriesOf(m).join(", ")}</>}
                    {skipReason(m) && <span className="ml-2 font-semibold text-danger">Skipped by "Use all": {skipReason(m)}</span>}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  {searched && m.score >= 50 && (
                    <span className={`text-sm font-semibold ${m.score >= threshold ? "text-ok" : "text-muted"}`}>{Math.round(m.score)}% match</span>
                  )}
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