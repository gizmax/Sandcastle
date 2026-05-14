// Persistent localStorage helpers for dismissing provider/advisor recommendations.

const DISMISSED_REC_KEY = "sandcastle_dismissed_recommendation";
// Dismissals expire after 7 days so new recommendations surface automatically.
const DISMISSED_REC_TTL_MS = 7 * 24 * 60 * 60 * 1000;

export function isDismissed(title: string): boolean {
  try {
    const raw = localStorage.getItem(DISMISSED_REC_KEY);
    if (!raw) return false;
    const map: Record<string, number> = JSON.parse(raw);
    const ts = map[title];
    if (!ts) return false;
    return Date.now() - ts < DISMISSED_REC_TTL_MS;
  } catch {
    return false;
  }
}

export function dismissRec(title: string): void {
  try {
    const raw = localStorage.getItem(DISMISSED_REC_KEY);
    const map: Record<string, number> = raw ? JSON.parse(raw) : {};
    map[title] = Date.now();
    for (const key of Object.keys(map)) {
      if (Date.now() - map[key] >= DISMISSED_REC_TTL_MS) delete map[key];
    }
    localStorage.setItem(DISMISSED_REC_KEY, JSON.stringify(map));
  } catch { /* ignore */ }
}
