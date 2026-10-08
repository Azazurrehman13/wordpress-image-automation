import type { Job } from "../types";
import { Button, Panel, Star, Warnings } from "../components/ui";

export default function Review({ job, strip, onApprove, onEdit, onCancel }: { job: Job; strip: boolean; onApprove: () => void; onEdit: () => void; onCancel: () => void }) {
  const sel = job.images.filter((i) => i.selected).sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
  const [main, ...gallery] = sel;
  const complete = sel.length > 0 && sel.every((i) => i.title && i.alt_text && i.description);
  return (
    <div className="space-y-4">
      <Warnings items={job.warnings} />
      <Panel title={job.product_name}>
        <div className="grid gap-6 lg:grid-cols-[minmax(0,26rem)_1fr]">
          <div>
            {main && (
              <div className="relative">
                <img src={main.processed_preview ?? main.source_preview} alt={main.alt_text ?? ""} className="aspect-square w-full rounded border border-line bg-white object-contain" />
                <span className="absolute left-2 top-2 rounded bg-surface px-2 py-0.5 text-xs font-semibold"><Star /> Main product image</span>
              </div>
            )}
            <ul className="mt-3 grid grid-cols-4 gap-2">
              {gallery.map((g) => (
                <li key={g.id}><img src={g.processed_preview ?? g.source_preview} alt={g.alt_text ?? ""} className="aspect-square w-full rounded border border-line bg-white object-contain" /></li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-muted">Preview of your processed files. The main image comes first, then the gallery in order.</p>
          </div>
          <div className="space-y-4 text-sm">
            <ul className="space-y-1.5">
              <li>{complete ? "✓" : "✗"} Titles, alt text and descriptions</li>
              <li>✓ Source URL removed from image metadata</li>
              <li>{strip ? "✓ Source links will be removed from the product description" : "• Product description left untouched"}</li>
              <li>✓ Price, SKU, stock, categories and attributes are not touched</li>
              <li>• Destination: <span className="break-all font-medium">{job.destination_site}</span></li>
            </ul>
            <details className="rounded border border-line">
              <summary className="cursor-pointer px-3 py-2 font-medium">Image metadata</summary>
              <ul className="divide-y divide-line">
                {sel.map((i) => (
                  <li key={i.id} className="space-y-0.5 px-3 py-2">
                    <p className="font-medium">{i.is_featured && <Star />} {i.title}</p>
                    <p className="text-xs text-muted">Alt: {i.alt_text}</p>
                    {i.caption && <p className="text-xs text-muted">Caption: {i.caption}</p>}
                    <p className="text-xs text-muted">{i.description}</p>
                  </li>
                ))}
              </ul>
            </details>
          </div>
        </div>
        <div className="mt-6 flex flex-wrap gap-2 border-t border-line pt-4">
          <Button onClick={onApprove} disabled={!complete}>Approve &amp; publish</Button>
          <Button variant="secondary" onClick={onEdit}>Edit images</Button>
          <Button variant="danger" onClick={onCancel}>Cancel</Button>
        </div>
      </Panel>
    </div>
  );
}
