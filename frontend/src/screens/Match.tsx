import { useState } from "react";
import { api, ApiError } from "../api";
import type { Inspection } from "../types";
import { Button, ErrorBanner, Field, Panel } from "../components/ui";

interface Props {
  name: string;
  mode: "update" | "create";
  inspection: Inspection | null;
  onStarted: (jobId: string) => void;
}

export default function Match({ name, mode, inspection, onStarted }: Props) {
  const cands = inspection?.candidates.filter((c) => c.score >= 30) ?? [];
  const [choice, setChoice] = useState<string>(inspection?.selected_url ?? "");
  const [manual, setManual] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);

  const url = (manual.trim() || choice).trim();

  async function start() {
    setBusy(true);
    setErr(null);
    try {
      const j = await api.createJob({
        product_name: inspection?.product.name ?? name,
        product_id: inspection?.product.id ?? null,
        product_url: inspection?.product.url ?? null,
        mode,
        original_source_url: url || null,
      });
      onStarted(j.id);
    } catch (x) {
      setErr(x as ApiError);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <Panel title={mode === "create" ? "New listing" : "Product found"}>
        {inspection ? (
          <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[8rem_1fr]">
            <dt className="text-muted">Product</dt><dd className="font-medium">{inspection.product.name}</dd>
            <dt className="text-muted">Product URL</dt><dd className="break-all">{inspection.product.url}</dd>
            <dt className="text-muted">Status</dt><dd>{inspection.product.status}</dd>
            <dt className="text-muted">Description</dt><dd className="text-muted">{inspection.description_preview || "—"}</dd>
          </dl>
        ) : (
          <p className="text-sm text-muted">A new draft product named <strong className="text-ink">{name}</strong> will be created when you publish.</p>
        )}
      </Panel>

      <Panel title="Description source URL">
        {cands.length > 0 ? (
          <>
            <p className="mb-3 text-sm text-muted">
              {inspection?.ambiguous ? "Several links look like the original source. Pick the right one." : "This link in the description looks like the original image source."}
            </p>
            <ul className="space-y-2">
              {cands.map((c) => (
                <li key={c.url}>
                  <label className="flex cursor-pointer items-start gap-3 rounded border border-line p-3 has-[:checked]:border-accent has-[:checked]:bg-accent-soft">
                    <input type="radio" name="src" className="mt-1 accent-[#0E5A63]" checked={!manual && choice === c.url} onChange={() => { setChoice(c.url); setManual(""); }} />
                    <span className="min-w-0">
                      <span className="block break-all text-sm font-medium">{c.url}</span>
                      <span className="block text-xs text-muted">Confidence {Math.round(c.score)} · {c.reasons.join(", ") || "weak signals"}</span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </>
        ) : (
          mode === "update" && <ErrorBanner code="source_url_not_found" message="Source URL not found in the product description. Paste the original source URL below to continue." />
        )}
        <div className="mt-4 max-w-xl">
          <Field label={cands.length ? "Or use a different source URL" : "Source URL"} placeholder="https://original-source.com/product/…" value={manual} onChange={(e) => setManual(e.target.value)} />
        </div>
        {err && <div className="mt-4"><ErrorBanner code={err.code} message={err.message} /></div>}
        <div className="mt-5">
          <Button onClick={start} disabled={busy || !url}>{busy ? "Starting…" : "Open source"}</Button>
        </div>
      </Panel>
    </div>
  );
}
