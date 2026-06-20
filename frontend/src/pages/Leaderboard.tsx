import { useMemo, useState } from 'react';
import { useAuth } from 'react-oidc-context';
import { useLeaderboard } from '../hooks/useLeaderboard';
import { useApi } from '../hooks/useApi';
import { Notice } from '../components/Notice';
import { errorMessage } from '../lib/api';
import { formatSignedCoins } from '../lib/format';
import type { LeaderboardRow } from '../lib/types';
import styles from './Leaderboard.module.css';

const NAME_MAX = 20;

/**
 * Row label: the user's chosen display name if set, else a short stable label from the sub (never
 * the raw UUID). The caller's own row keeps the "You" badge regardless (added by the renderer).
 */
function playerLabel(row: LeaderboardRow, mySub?: string): { name: string; isYou: boolean } {
  const isYou = !!mySub && row.userId === mySub;
  const display = row.displayName?.trim();
  const name = display || (isYou ? 'You' : `Player ${row.userId.slice(-6)}`);
  return { name, isYou };
}

const profitClass = (p: number) =>
  p > 0 ? styles.pos : p < 0 ? styles.neg : styles.zero;

export default function Leaderboard() {
  const { standings, loading, error, reload } = useLeaderboard();
  const auth = useAuth();
  const mySub = auth.user?.profile.sub;

  // Backend returns it ordered; sort defensively anyway.
  const ranked = useMemo(
    () => (standings ? [...standings].sort((a, b) => b.profit - a.profit) : null),
    [standings],
  );

  // Prefill from the caller's own row, if they're on the board with a name set.
  const currentName = useMemo(
    () => (mySub && standings ? standings.find((r) => r.userId === mySub)?.displayName ?? '' : ''),
    [standings, mySub],
  );

  return (
    <div className="page">
      <h1 className="page-title">Leaderboard</h1>
      <p className="page-lead">Tournament standings by realized profit, updating live.</p>

      <NameForm currentName={currentName} onSaved={reload} />

      <div className={styles.board}>
        {loading ? (
          <div className={`skeleton ${styles.skeleton}`} role="status" aria-label="Loading standings" />
        ) : error ? (
          <Notice
            tone="error"
            title="Couldn't load the leaderboard"
            message={error}
            action={{ label: 'Try again', onClick: () => void reload() }}
          />
        ) : !ranked || ranked.length === 0 ? (
          <Notice
            title="No standings yet"
            message="Rankings appear once bets settle — after matches finish and results come in."
          />
        ) : (
          <Board rows={ranked} mySub={mySub} />
        )}
      </div>
    </div>
  );
}

function Board({ rows, mySub }: { rows: LeaderboardRow[]; mySub?: string }) {
  return (
    <table className={styles.table}>
      <thead>
        <tr>
          <th className={styles.rankCol} scope="col">
            #
          </th>
          <th scope="col">Player</th>
          <th className={styles.wlCol} scope="col" title="Wins–Losses">
            W–L
          </th>
          <th className={styles.profitCol} scope="col">
            Profit
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => {
          const rank = i + 1;
          const { name, isYou } = playerLabel(row, mySub);
          const rowClass = `${rank === 1 ? styles.top : ''} ${isYou ? styles.youRow : ''}`;
          return (
            <tr key={row.userId} className={rowClass}>
              <td className={styles.rank}>
                {rank === 1 ? <Trophy /> : null}
                <span className="num">{rank}</span>
              </td>
              <td>
                {/* flex lives on an inner div, not the <td>: display:flex on a table cell drops it
                    from table-cell layout, so the row highlight wouldn't fill the cell. */}
                <div className={styles.player}>
                  <span className={styles.playerName}>{name}</span>
                  {isYou && <span className={styles.youTag}>You</span>}
                </div>
              </td>
              <td className={`num ${styles.wl}`}>
                {row.wins}–{row.losses}
              </td>
              <td className={`num ${styles.profit} ${profitClass(row.profit)}`}>
                {formatSignedCoins(row.profit)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** Set / edit the caller's own display name (PUT /leaderboard/name), then refresh the board. */
function NameForm({ currentName, onSaved }: { currentName: string; onSaved: () => void }) {
  const api = useApi();
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // null draft = untouched → mirror the server value; once edited, the draft wins.
  const value = draft ?? currentName;
  const trimmed = value.trim();
  const valid = trimmed.length > 0 && trimmed.length <= NAME_MAX;

  async function save() {
    if (!valid || saving) return;
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      const res = await api.setDisplayName(trimmed);
      setDraft(res.displayName); // reflect the server-canonical value
      setSaved(true);
      onSaved(); // refresh the board so the new name shows on the user's row
    } catch (e) {
      setSaveError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      className={styles.nameForm}
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <label className={styles.nameField}>
        <span className={styles.nameLabel}>Your display name</span>
        <input
          className={styles.nameInput}
          type="text"
          maxLength={NAME_MAX}
          placeholder="Set a display name"
          value={value}
          onChange={(e) => {
            setDraft(e.target.value);
            setSaved(false);
            setSaveError(null);
          }}
        />
      </label>
      <button className="btn btn--accent" type="submit" disabled={!valid || saving}>
        {saving ? 'Saving…' : 'Save'}
      </button>
      <span className={styles.nameMeta} aria-live="polite">
        {saveError ? (
          <span className={styles.nameError}>{saveError}</span>
        ) : saved ? (
          <span className={styles.nameSaved}>Saved.</span>
        ) : (
          <span className={styles.nameCount}>
            {trimmed.length}/{NAME_MAX}
          </span>
        )}
      </span>
    </form>
  );
}

function Trophy() {
  return (
    <svg className={styles.trophy} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M6 4h12v2h3v2a4 4 0 0 1-4 4h-.3A6 6 0 0 1 13 15.9V18h3v2H8v-2h3v-2.1A6 6 0 0 1 7.3 12H7a4 4 0 0 1-4-4V6h3V4Zm0 4H5a2 2 0 0 0 1 1.7V8Zm12 0v1.7A2 2 0 0 0 19 8h-1Z" />
    </svg>
  );
}
