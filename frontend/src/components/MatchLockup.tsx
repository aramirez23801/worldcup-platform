import { Link } from 'react-router-dom';
import { FlagChip } from './FlagChip';
import { finalWinner, formatKickoffShort, sideLabel, stageLabel } from '../lib/format';
import type { Match } from '../lib/types';
import styles from './MatchLockup.module.css';

/**
 * Quiet match lockup: flag chips + names + score-or-kickoff and a small status tag. Deliberately
 * NOT the score bug — no LED inset, key-light, or pulse — so a dense surface (the upcoming row, the
 * bracket, the bet picker) stays clean and cheap. Winner ring on FINAL, derived from the score.
 *
 * Renders as a link (to), a selectable button (onSelect), or a static card.
 */
export function MatchLockup({
  match,
  to,
  onSelect,
  selected,
}: {
  match: Match;
  to?: string;
  onSelect?: () => void;
  selected?: boolean;
}) {
  const isLive = match.status === 'LIVE';
  const isFinal = match.status === 'FINAL';
  const hasScore = isLive || isFinal;
  const winner = finalWinner(match);

  const inner = (
    <>
      <div className={styles.top}>
        <span className={styles.tag}>{stageLabel(match.stage, match.group)}</span>
        <span className={isLive ? `${styles.state} ${styles.stateLive}` : styles.state}>
          {isLive ? 'Live' : isFinal ? 'Full time' : formatKickoffShort(match.kickoff)}
        </span>
      </div>

      <div className={styles.row}>
        <div className={styles.team}>
          <FlagChip team={match.teamHome} size={34} ring={winner === 'home' ? 'win' : 'none'} />
          <span className={styles.name}>{sideLabel(match.teamHome, match.sourceHome)}</span>
        </div>

        <div className={styles.mid}>
          {hasScore ? (
            <span className={`num ${styles.score}`}>
              {match.scoreHome ?? 0}
              <span className={styles.dash}>–</span>
              {match.scoreAway ?? 0}
            </span>
          ) : (
            <span className={styles.vs}>vs</span>
          )}
        </div>

        <div className={`${styles.team} ${styles.teamAway}`}>
          <span className={styles.name}>{sideLabel(match.teamAway, match.sourceAway)}</span>
          <FlagChip team={match.teamAway} size={34} ring={winner === 'away' ? 'win' : 'none'} />
        </div>
      </div>
    </>
  );

  if (onSelect) {
    return (
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        className={`${styles.card} ${styles.linkCard} ${selected ? styles.selected : ''}`}
      >
        {inner}
      </button>
    );
  }
  if (to) {
    return (
      <Link to={to} className={`${styles.card} ${styles.linkCard}`}>
        {inner}
      </Link>
    );
  }
  return <div className={styles.card}>{inner}</div>;
}
