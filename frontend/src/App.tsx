import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { BUSY, type Inspection, type Job, type Settings } from "./types";
import { ErrorBanner, Rail, Toggle } from "./components/ui";
import Connect from "./screens/Connect";
import Search from "./screens/Search";
import Match from "./screens/Match";
import Analysis from "./screens/Analysis";
import Selection from "./screens/Selection";
import Review from "./screens/Review";
import Publish from "./screens/Publish";
import Result from "./screens/Result";

export default function App() {
  const [screen, setScreen] = useState(1);
  const [reachable, setReachable] = useState(1);
  const [site, setSite] = useState<string | null>(null);
  const [claudeOk, setClaudeOk] = useState(true);
  const [query, setQuery] = useState("");
  const [name, setName] = useState("");
  const [mode, setMode] = useState<"update" | "create">("update");
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);

  const go = useCallback((n: number) => {
    setScreen(n);
    setReachable((r) => Math.max(r, n));
  }, []);

  useEffect(() => {
    api.settings().then(setSettings).catch(() => undefined);
    api.status().then((s) => {
      setClaudeOk(s.claude_configured);
      if (s.connected && s.site) { setSite(s.site); go(2); }
    }).catch((e) => setFatal(e.message));
  }, [go]);

  // Poll the job while the backend is working on it.
  useEffect(() => {
    if (!job || !BUSY.includes(job.status)) return;
    const t = setInterval(() => api.job(job.id).then(setJob).catch(() => undefined), 1000);
    return () => clearInterval(t);
  }, [job]);

  // Move forward automatically at the natural hand-over points.
  useEffect(() => {
    if (!job) return;
    if (job.status === "completed" && screen === 7) go(8);
  }, [job, screen, go]);

  function reset() {
    setJob(null); setInspection(null); setQuery(""); setReachable(2); setScreen(2);
  }

  async function toggleAuto(v: boolean) {
    setSettings(await api.saveSettings({ auto_publish: v }));
  }

  async function logout() {
    await api.logout();
    setSite(null); setJob(null); setBatchId(null); setReachable(1); setScreen(1);
  }

  async function cancel() {
    if (!job) return;
    if (!window.confirm("Cancel this job? Uploaded images will be removed from your media library.")) return;
    await api.cancel(job.id);
    reset();
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-6">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl">Product Image Automation</h1>
          <p className="text-sm text-muted">Source images from the description link, selected and prepared by Claude, published to your WordPress site.</p>
        </div>
        {settings && (
          <Toggle label="Auto publish" checked={settings.auto_publish} onChange={toggleAuto} hint="Skip the approval step when no warnings were raised. Off is safer." />
        )}
      </header>
      {fatal && <div className="mb-4"><ErrorBanner code="backend_unreachable" message={fatal} /></div>}
      <div className="grid gap-6 lg:grid-cols-[13rem_1fr]">
        <Rail screen={screen} reachable={reachable} onGo={setScreen} />
        <main className="min-w-0">
          {screen === 1 && <Connect claudeOk={claudeOk} onConnected={(s) => { setSite(s); go(2); }} />}
          {screen === 2 && site && (
            <Search
              site={site} query={query} setQuery={setQuery} onLogout={logout} batchId={batchId} onBatch={setBatchId}
              onInspected={(n, i, m) => { setName(n); setInspection(i); setMode(m); go(3); }}
              onNewListing={(n) => { setName(n); setInspection(null); setMode("create"); go(3); }}
            />
          )}
          {screen === 3 && (
            <Match key={inspection?.product.url ?? name} name={name} mode={mode} inspection={inspection} onStarted={async (id) => { setJob(await api.job(id)); go(4); }} />
          )}
          {screen === 4 && job && <Analysis job={job} onContinue={() => go(5)} onRetry={() => setScreen(3)} />}
          {screen === 5 && job && <Selection job={job} onJob={setJob} onContinue={() => go(6)} />}
          {screen === 6 && job && (
            <Review job={job} strip={settings?.strip_source_from_description ?? true}
              onApprove={() => go(7)} onEdit={() => setScreen(5)} onCancel={cancel} />
          )}
          {screen === 7 && job && <Publish job={job} onJob={setJob} />}
          {screen === 8 && job && <Result job={job} onAgain={reset} />}
        </main>
      </div>
    </div>
  );
}