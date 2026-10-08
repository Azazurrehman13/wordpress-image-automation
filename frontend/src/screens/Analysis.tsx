import type { Job, JobImage } from "../types";
import { BUSY } from "../types";
import { Button, ErrorBanner, Panel, ProgressList, Star, Warnings } from "../components/ui";

const SCORES: [keyof NonNullable<JobImage["analysis"]>, string][] = [
  ["product_visibility_score", "Visibility"], ["quality_score", "Quality"], ["composition_score", "Composition"],
  ["feature_visibility_score", "Features"], ["ecommerce_score", "E-commerce"],
];

export default function Analysis({ job, onContinue, onRetry }: { job: Job; onContinue: () => void; onRetry: () => void }) {
  const busy = BUSY.includes(job.status);
  const analysed = job.images.filter((i) => i.analysis);
  const ranked = [...analysed].sort((a, b) => (b.ai_score ?? 0) - (a.ai_score ?? 0));
  const ready = job.status === "ready_for_review" || ["publishing", "completed", "verification_failed"].includes(job.status);
  return (
    <div className="space-y-4">
      <Panel title={busy ? "Working on it…" : "Job progress"}>
        {job.original_source_url && <p className="mb-3 break-all text-xs text-muted">Source: {job.original_source_url}</p>}
        <ProgressList steps={job.progress} />
        {job.status === "failed" && job.error_message && (
          <div className="mt-4 space-y-3">
            <ErrorBanner code={job.error_code} message={job.error_message} />
            <Button variant="secondary" onClick={onRetry}>Back to source URL</Button>
          </div>
        )}
        <div className="mt-4"><Warnings items={job.warnings} /></div>
      </Panel>

      {ranked.length > 0 && (
        <Panel title={`Candidate images (${ranked.length})`} actions={ready ? <Button onClick={onContinue}>Continue to selection</Button> : undefined}>
          <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {ranked.map((img) => (
              <li key={img.id} className={`overflow-hidden rounded border ${img.selected ? "border-accent" : "border-line"} ${img.state === "rejected" ? "opacity-60" : ""}`}>
                <div className="relative aspect-square bg-canvas">
                  <img src={img.source_preview} alt="" loading="lazy" className="h-full w-full object-contain" />
                  {img.is_featured && <span className="absolute left-2 top-2 rounded bg-surface px-2 py-0.5 text-xs font-semibold"><Star /> Best</span>}
                </div>
                <div className="space-y-1 p-3 text-xs">
                  <p className="flex justify-between font-medium">
                    <span>{img.analysis?.image_type.replace(/_/g, " ")}</span>
                    <span>{Math.round(img.ai_score ?? 0)} / 100</span>
                  </p>
                  <p className="text-muted">{img.width}×{img.height}{img.rejected_reason ? ` · ${img.rejected_reason}` : ""}</p>
                  <div className="grid grid-cols-5 gap-1 pt-1">
                    {SCORES.map(([k, l]) => (
                      <div key={k} title={`${l}: ${Math.round(Number(img.analysis?.[k] ?? 0))}`}>
                        <div className="h-1.5 rounded bg-canvas"><div className="h-full rounded bg-accent" style={{ width: `${Number(img.analysis?.[k] ?? 0)}%` }} /></div>
                        <span className="text-[10px] text-muted">{l.slice(0, 4)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}
