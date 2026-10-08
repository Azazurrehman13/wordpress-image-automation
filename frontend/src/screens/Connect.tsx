import { useState } from "react";
import { api, ApiError } from "../api";
import { Button, ErrorBanner, Field, Panel, Toggle } from "../components/ui";

export default function Connect({ onConnected, claudeOk }: { onConnected: (site: string) => void; claudeOk: boolean }) {
  const [loginUrl, setLoginUrl] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [headless, setHeadless] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await api.connect({ login_url: loginUrl, username, password, headless });
      setPassword("");
      onConnected(r.site);
    } catch (x) {
      setErr(x as ApiError);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Connect to your WordPress site">
      <p className="mb-4 max-w-prose text-sm text-muted">
        Finished product images are uploaded to this site. Your password stays in the backend's memory for this session and is never saved or logged.
      </p>
      {!claudeOk && <div className="mb-4"><ErrorBanner message="ANTHROPIC_API_KEY is empty. Add it to backend/.env and restart the backend." code="claude_not_configured" /></div>}
      <form onSubmit={submit} className="max-w-lg space-y-4">
        <Field label="Login URL" placeholder="https://example.com/wp-login.php" value={loginUrl} onChange={(e) => setLoginUrl(e.target.value)} required type="url" />
        <Field label="Username" value={username} onChange={(e) => setUsername(e.target.value)} required autoComplete="username" />
        <Field label="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password" />
        <Toggle label="Headless browser" checked={headless} onChange={setHeadless} hint="Turn off to watch the browser, or to finish a 2FA or captcha step." />
        {err && <ErrorBanner code={err.code} message={err.message} />}
        <Button type="submit" disabled={busy}>{busy ? "Connecting…" : "Connect"}</Button>
      </form>
    </Panel>
  );
}
