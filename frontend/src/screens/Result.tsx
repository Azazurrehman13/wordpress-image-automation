import type { Job } from "../types";
import { Button, Panel } from "../components/ui";

export default function Result({ job, onAgain }: { job: Job; onAgain: () => void }) {
  return (
    <Panel title="Published">
      <p className="mb-1 text-sm font-semibold text-ok">✓ Successfully published</p>
      <p className="mb-4 text-sm">Product URL:{" "}
        <a href={job.result_url ?? "#"} target="_blank" rel="noreferrer" className="break-all font-medium text-accent underline">{job.result_url}</a>
      </p>
      <ul className="mb-5 space-y-1 text-sm">
        {job.verification.map((c) => <li key={c.name} className={c.ok ? "" : "text-danger"}>{c.ok ? "✓" : "✗"} {c.name}</li>)}
      </ul>
      <Button onClick={onAgain}>Process another product</Button>
    </Panel>
  );
}
