import { useState } from "react";
import { KeyRound, Loader2, AlertCircle } from "lucide-react";

interface AuthGateProps {
  onLogin: (key: string) => Promise<boolean>;
}

export function AuthGate({ onLogin }: AuthGateProps) {
  const [key, setKey] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!key.trim()) return;
    setLoading(true);
    setError(false);
    const ok = await onLogin(key.trim());
    if (!ok) {
      setError(true);
    }
    setLoading(false);
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center space-y-2">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-accent/10">
            <KeyRound className="h-6 w-6 text-accent" />
          </div>
          <h1 className="text-xl font-semibold text-foreground">Sandcastle</h1>
          <p className="text-sm text-muted">Enter your API key to connect</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <input
              type="password"
              value={key}
              onChange={(e) => { setKey(e.target.value); setError(false); }}
              placeholder="sc_..."
              autoFocus
              className="h-10 w-full rounded-lg border border-border bg-surface px-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring/30 transition-colors"
            />
            {error && (
              <div className="mt-2 flex items-center gap-1.5 text-xs text-error">
                <AlertCircle className="h-3.5 w-3.5" />
                Invalid API key
              </div>
            )}
          </div>

          <button
            type="submit"
            disabled={!key.trim() || loading}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-accent-foreground hover:bg-accent-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Connect"}
          </button>
        </form>
      </div>
    </div>
  );
}
