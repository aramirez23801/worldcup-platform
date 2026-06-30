import type { Match, Stage } from './types';

const pad2 = (n: number) => String(n).padStart(2, '0');

/** Thousands-separated coin balance, e.g. 1250 → "1,250". */
export function formatCoins(n: number): string {
  return new Intl.NumberFormat('en-US').format(n);
}

/** Signed coins for profit columns, e.g. -340 → "−340", 120 → "+120". */
export function formatSignedCoins(n: number): string {
  const sign = n > 0 ? '+' : n < 0 ? '−' : '';
  return `${sign}${formatCoins(Math.abs(n))}`;
}

/** Local kickoff, e.g. "Thu 18 Jun, 19:00". */
export function formatKickoff(iso: string): string {
  return new Intl.DateTimeFormat(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(iso));
}

/** Compact local kickoff for dense cards, e.g. "Thu 19:00". */
export function formatKickoffShort(iso: string): string {
  return new Intl.DateTimeFormat(undefined, {
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(iso));
}

/** Time-to-kickoff as a scoreboard countdown: "3d 04h", "5h 12m", "12m 30s", "8s", or "Kicking off". */
export function formatCountdown(targetMs: number, nowMs: number): string {
  if (Number.isNaN(targetMs)) return '';
  const diff = targetMs - nowMs;
  if (diff <= 0) return 'Kicking off';
  const total = Math.floor(diff / 1000);
  const d = Math.floor(total / 86400);
  const h = Math.floor((total % 86400) / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (d > 0) return `${d}d ${pad2(h)}h`;
  if (h > 0) return `${h}h ${pad2(m)}m`;
  if (m > 0) return `${m}m ${pad2(s)}s`;
  return `${s}s`;
}

const STAGE_LABELS: Record<Stage, string> = {
  GROUP: 'Group',
  R32: 'Round of 32',
  R16: 'Round of 16',
  QF: 'Quarter-final',
  SF: 'Semi-final',
  '3P': 'Third place',
  F: 'Final',
};

/** Human round label, e.g. GROUP+"A" → "Group A", "QF" → "Quarter-final". */
export function stageLabel(stage: Stage, group?: string): string {
  if (stage === 'GROUP') return group ? `Group ${group}` : 'Group';
  return STAGE_LABELS[stage] ?? stage;
}

/**
 * Winner of a FINAL match: 'home' | 'away' | 'draw', or null when not final / no score.
 * Normally derived purely from the score; a knockout decided on penalties carries a level
 * score but still has a winner (penaltyWinner), so that side wins here. A drawn group match
 * has no winner and returns 'draw'.
 */
export function finalWinner(m: Match): 'home' | 'away' | 'draw' | null {
  if (m.status !== 'FINAL' || m.scoreHome == null || m.scoreAway == null) return null;
  if (m.scoreHome > m.scoreAway) return 'home';
  if (m.scoreAway > m.scoreHome) return 'away';
  if (m.decidedBy === 'PENALTIES' && m.penaltyWinner) {
    if (m.penaltyWinner === m.teamHome) return 'home';
    if (m.penaltyWinner === m.teamAway) return 'away';
  }
  return 'draw';
}

/** Caption for a knockout decided on penalties, e.g. "Paraguay won on penalties", else null. */
export function penaltyNote(m: Match): string | null {
  if (m.status !== 'FINAL' || m.decidedBy !== 'PENALTIES') return null;
  return m.penaltyWinner ? `${m.penaltyWinner} won on penalties` : 'Decided on penalties';
}

/** The label to show for a side: the resolved team, else the bracket source placeholder, else "TBD". */
export function sideLabel(team: string | undefined, source: string | undefined): string {
  return team || source || 'TBD';
}

/** Human label for a bet selection, using team names when available. */
export function betSelectionLabel(
  betType: string,
  selection: string,
  home?: string,
  away?: string,
): string {
  if (betType === '1X2') {
    if (selection === 'HOME') return home ? `${home} to win` : 'Home win';
    if (selection === 'AWAY') return away ? `${away} to win` : 'Away win';
    return 'Draw';
  }
  if (betType === 'OU25') {
    return selection === 'OVER' ? 'Over 2.5 goals' : 'Under 2.5 goals';
  }
  return selection;
}
