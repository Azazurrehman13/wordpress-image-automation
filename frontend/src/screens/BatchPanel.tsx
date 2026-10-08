import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { Batch, BatchItem, BatchStatus } from "../types";
import { Button, ErrorBanner, Panel } from "../components/ui";

const LABEL: Record<BatchStatus, string> = {
  queued: "Waiting", running: "Working…", published: "Published", needs_review: "Left as draft",
  failed: "Failed", skipped: "Skipped", cancelled: "Stopped",
};
const TONE: Record<BatchStatus, string> = {
  queued: "text-muted", running: "text-accent", published: "text-ok", needs_review: "text-ink",
  failed: "text-danger", skipped: "text-muted", cancelled: "text-muted",
};

function Row({ it }: { it: BatchItem }) {
  return (
    <li className="flex items-start justify-between gap-4 py-3">
      <div className="min-w-0">
        <p className="truncate font-medium">{it.name || "Untitled product"}</p>
        {it.status === "running" ? (
          <p className="text-xs text-muted">
            {it.step ? `${it.step} · ` : ""}{it.steps_done}/{it.steps_total} steps
          </p>
        ) : (
          it.message && <p className="break-words text-xs text-muted">{it.message}</p>
        )}
        {it.status === "published" && it.result_url && (
          <a href={it.result_url} target="_blank" rel="noreferrer" className="break-all text-xs font-medium text-accent underline">
            {it.result_url}
          </a>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2 text-sm font-semibold">
        {it.status === "running" && <span className="spin inline-block h-4 w-4 rounded-full border-2 border-accent border-t-transparent" aria-hidden />}
        <span className={TONE[it.status]}>{it.status === "published" ? "✓ " : ""}{LABEL[it.status]}</span>
      </div>
    </li>
  );
}

export default function BatchPanel({ id, onClose, onRunning }: { id: string; onClose: () => void; onRunning: (r: boolean) => void }) {
  const [batch, setBatch] = useState<Batch | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [stopping, setStopping] = useState(false);
  const running = batch?.state === "running";
  const finished = batch ? batch.state !== "running" : false;

  // Poll while the backend is working; one last read when it finishes.
  useEffect(() => {
    let alive = true;
    const load = () =>
      api.batch(id)
        .then((b) => { if (alive) { setBatch(b); setErr(null); } })
        .catch((x) => { if (alive) setErr(x as ApiError); });
    load();
    if (finished) return () => { alive = false; };
    const t = setInterval(load, 1500);
    return () => { alive = false; clearInterval(t); };
  }, [id, finished]);

  useEffect(() => { onRunning(!!running); }, [running, onRunning]);

  async function stop() {
    setStopping(true);
    try { setBatch(await api.cancelBatch(id)); } catch (x) { setErr(x as ApiError); setStopping(false); }
  }

  const c = batch?.counts ?? {};
  const total = batch?.total ?? 0;
  const handled = total - (c.queued ?? 0) - (c.running ?? 0);
  const pct = total ? Math.round((handled / total) * 100) : 0;

  return (
    <Panel
      title="Use all One by One"
      actions={
        running
          ? <Button variant="danger" onClick={stop} disabled={stopping}>{stopping ? "Stopping after this product…" : "Stop"}</Button>
          : <Button variant="secondary" onClick={onClose}>Close</Button>
      }
    >
      {err && <div className="mb-4"><ErrorBanner code={err.code} message={err.message} /></div>}
      {batch && (
        <>
          <p className="text-sm">
            <strong>{handled}</strong> of {total} handled · <span className="text-ok">{c.published ?? 0} published</span>
            {(c.failed ?? 0) > 0 && <> · <span className="text-danger">{c.failed} failed</span></>}
            {(c.needs_review ?? 0) > 0 && <> · {c.needs_review} left as draft</>}
            {(c.skipped ?? 0) > 0 && <> · {c.skipped} skipped</>}
            {(c.cancelled ?? 0) > 0 && <> · {c.cancelled} not started</>}
          </p>
          <div className="mt-2 h-2 overflow-hidden rounded bg-canvas" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
            <div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} />
          </div>
          <p className="mt-2 text-xs text-muted">
            Products are processed one at a time and published automatically. Copies and non-draft products are skipped.
            {batch.state === "done" && " Finished."}{batch.state === "cancelled" && " Stopped."}
          </p>
          <ul className="mt-3 divide-y divide-line">
            {batch.items.map((it, i) => <Row key={`${it.product_id ?? "x"}-${i}`} it={it} />)}
          </ul>
        </>
      )}
      {!batch && !err && <p className="text-sm text-muted">Starting…</p>}
    </Panel>
  );
}