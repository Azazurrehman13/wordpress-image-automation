import { useState } from "react";
import { api, ApiError } from "../api";
import type { Job } from "../types";
import { BUSY } from "../types";
import { Button, ErrorBanner, Panel, ProgressList } from "../components/ui";

export default function Publish({ job, onJob }: { job: Job; onJob: (j: Job) => void }) {
  const [err, setErr] = useState<ApiError | null>(null);
  const running = job.status === "publishing";
  const started = job.progress.some((s) => ["main_assigned", "gallery_assigned", "published", "verified"].includes(s.key) && s.status !== "pending");
  const sel = job.images.filter((i) => i.selected);

  async function go() {
    setErr(null);
    try { onJob(await api.publish(job.id)); } catch (x) { setErr(x as ApiError); }
  }

  return (
    <Panel title="Publish to your site">
      {!started && !running && (
        <p className="mb-4 max-w-prose text-sm text-muted">
          This writes the {sel.length} approved images to <strong className="text-ink">{job.mode === "create" ? "a new product" : job.product_name}</strong> on {job.destination_site}:
          the first becomes the Product Image and the other {Math.max(0, sel.length - 1)} become the Product Gallery.
        </p>
      )}
      {started && <div className="mb-4"><ProgressList steps={job.progress.filter((s) => ["main_assigned", "gallery_assigned", "published", "verified"].includes(s.key))} /></div>}
      {err && <div className="mb-3"><ErrorBanner code={err.code} message={err.message} /></div>}
      {job.error_message && !running && <div className="mb-3"><ErrorBanner code={job.error_code} message={job.error_message} /></div>}
      {job.verification.some((c) => !c.ok) && (
        <ul className="mb-3 space-y-1 text-sm">{job.verification.map((c) => <li key={c.name} className={c.ok ? "text-ok" : "text-danger"}>{c.ok ? "✓" : "✗"} {c.name}{!c.ok && c.detail ? ` — ${c.detail}` : ""}</li>)}</ul>
      )}
      <Button onClick={go} disabled={running || BUSY.includes(job.status)}>{running ? "Publishing…" : job.error_message ? "Publish again" : "Publish"}</Button>
    </Panel>
  );
}
