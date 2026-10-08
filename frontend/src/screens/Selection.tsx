import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import type { Job } from "../types";
import { BUSY } from "../types";
import { Button, ErrorBanner, Panel, ProgressList, Star, Warnings } from "../components/ui";

export default function Selection({ job, onJob, onContinue }: { job: Job; onJob: (j: Job) => void; onContinue: () => void }) {
  const serverOrder = useMemo(
    () => job.images.filter((i) => i.selected).sort((a, b) => (a.position ?? 0) - (b.position ?? 0)).map((i) => i.id),
    [job.images]
  );
  const [order, setOrder] = useState<number[]>(serverOrder);
  const [best, setBest] = useState<number>(serverOrder[0]);
  const [err, setErr] = useState<ApiError | null>(null);
  const busy = BUSY.includes(job.status);
  const dirty = JSON.stringify(order) !== JSON.stringify(serverOrder) || best !== serverOrder[0];

  useEffect(() => { if (!busy) { setOrder(serverOrder); setBest(serverOrder[0]); } }, [serverOrder, busy]);

  const finalOrder = [best, ...order.filter((i) => i !== best)];
  const byId = new Map(job.images.map((i) => [i.id, i]));
  const others = job.images.filter((i) => i.state === "valid" && !order.includes(i.id));

  function move(id: number, d: -1 | 1) {
    const idx = finalOrder.indexOf(id);
    const j = idx + d;
    if (j < 1 || j >= finalOrder.length) return; // position 0 is always the best image
    const next = [...finalOrder];
    [next[idx], next[j]] = [next[j], next[idx]];
    setOrder(next);
  }
  function remove(id: number) {
    if (id === best) return;
    setOrder(finalOrder.filter((i) => i !== id));
  }
  function add(id: number) {
    if (finalOrder.length < 5) setOrder([...finalOrder, id]);
  }
  function makeBest(id: number) {
    setBest(id);
    setOrder([id, ...finalOrder.filter((i) => i !== id)]);
  }

  async function apply() {
    setErr(null);
    try {
      onJob(await api.updateSelection(job.id, finalOrder, best));
    } catch (x) {
      setErr(x as ApiError);
    }
  }

  const card = (id: number, pos: number) => {
    const img = byId.get(id);
    if (!img) return null;
    const isBest = id === best;
    return (
      <li key={id} className={`overflow-hidden rounded border bg-surface ${isBest ? "border-best ring-2 ring-best/40" : "border-line"}`}>
        <div className="relative aspect-square bg-canvas">
          <img src={img.processed_preview && !dirty ? img.processed_preview : img.source_preview} alt={img.alt_text ?? ""} className="h-full w-full object-contain" />
          <span className="absolute left-2 top-2 rounded bg-surface px-2 py-0.5 text-xs font-semibold">{isBest ? <><Star /> Main image</> : `Gallery ${pos}`}</span>
        </div>
        <div className="space-y-2 p-3">
          <p className="text-xs text-muted">{img.analysis?.image_type.replace(/_/g, " ")} · score {Math.round(img.ai_score ?? 0)}</p>
          <div className="flex flex-wrap gap-1.5">
            {!isBest && <Button variant="secondary" className="!px-2 !py-1 text-xs" onClick={() => makeBest(id)} disabled={busy}>Make main</Button>}
            {!isBest && <Button variant="secondary" className="!px-2 !py-1 text-xs" onClick={() => move(id, -1)} disabled={busy || pos <= 1} aria-label="Move earlier">↑</Button>}
            {!isBest && <Button variant="secondary" className="!px-2 !py-1 text-xs" onClick={() => move(id, 1)} disabled={busy || pos >= finalOrder.length - 1} aria-label="Move later">↓</Button>}
            {!isBest && <Button variant="danger" className="!px-2 !py-1 text-xs" onClick={() => remove(id)} disabled={busy}>Remove</Button>}
          </div>
        </div>
      </li>
    );
  };

  return (
    <div className="space-y-4">
      {busy && <Panel title="Updating images…"><ProgressList steps={job.progress.filter((s) => ["images_cropped", "images_optimized", "metadata_generated", "images_uploaded", "ready"].includes(s.key))} /></Panel>}
      {job.error_message && !busy && <ErrorBanner code={job.error_code} message={job.error_message} />}
      <Warnings items={job.warnings} />
      <Panel
        title="Selected images"
        actions={
          <div className="flex gap-2">
            {dirty && <Button onClick={apply} disabled={busy}>Apply changes</Button>}
            <Button variant={dirty ? "secondary" : "primary"} onClick={onContinue} disabled={busy || dirty}>Continue to review</Button>
          </div>
        }
      >
        <p className="mb-4 max-w-prose text-sm text-muted">
          The main image is shown first on your product page; the others follow it as the gallery, in this order. Changes are processed and uploaded when you apply them.
        </p>
        {err && <div className="mb-4"><ErrorBanner code={err.code} message={err.message} /></div>}
        <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{finalOrder.map((id, i) => card(id, i))}</ul>
      </Panel>

      {others.length > 0 && (
        <Panel title="Other candidates">
          <ul className="grid grid-cols-3 gap-3 sm:grid-cols-4 xl:grid-cols-6">
            {others.map((img) => (
              <li key={img.id} className="space-y-1">
                <img src={img.source_preview} alt="" loading="lazy" className="aspect-square w-full rounded border border-line bg-canvas object-contain" />
                <Button variant="secondary" className="w-full !px-1 !py-1 text-xs" disabled={busy || finalOrder.length >= 5} onClick={() => add(img.id)}>Add</Button>
              </li>
            ))}
          </ul>
          {finalOrder.length >= 5 && <p className="mt-2 text-xs text-muted">Five images is the maximum. Remove one to add another.</p>}
        </Panel>
      )}
    </div>
  );
}
