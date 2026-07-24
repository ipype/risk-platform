import { useEffect, useState } from "react";
import type { HistoryEntry } from "../types";
import { getActivity } from "../api";
import { ChangeList, fmtTime } from "../history-util";

const ACTION_LABEL: Record<string, string> = {
  created: "created",
  updated: "edited",
  deleted: "deleted",
};

export default function ActivityView() {
  const [entries, setEntries] = useState<HistoryEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getActivity(200)
      .then(setEntries)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  if (error) return <div className="error">{error}</div>;

  return (
    <div className="activity">
      <header className="topbar">
        <h1>Activity</h1>
      </header>
      {loading ? (
        <div className="muted">Loading…</div>
      ) : entries.length === 0 ? (
        <div className="empty">No changes recorded yet.</div>
      ) : (
        <ul className="feed">
          {entries.map((e) => (
            <li key={e.id} className={`feed-item action-${e.action}`}>
              <div className="feed-head">
                <strong>{e.actor}</strong> {ACTION_LABEL[e.action] ?? e.action}{" "}
                <span className="feed-code">{e.risk_code}</span>
                <span className="feed-time">{fmtTime(e.created_at)}</span>
              </div>
              <ChangeList changes={e.changes} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
