import { useEffect, useRef, useState } from 'react';
import { FlagChip } from './FlagChip';
import { useCountdown } from '../hooks/useCountdown';
import { finalWinner, formatKickoff, penaltyNote, sideLabel, stageLabel } from '../lib/format';
import type { Match } from '../lib/types';
import styles from './ScoreBug.module.css';

/**
 * THE signature element: a broadcast "score bug". One component, three first-class states —
 *   SCHEDULED → kickoff countdown in the seam, no score
 *   LIVE      → pulsing LIVE chip, running score, a single goal-flip when a number changes
 *   FINAL     → "Full time", final score, a gold winner ring (none on a draw)
 * Recessed LED score inset, a central gold seam, a key-light top edge. Everything else in the app
 * stays quiet so this is what the product is remembered by. Motion is plain CSS, reduced-motion-aware.
 */
export function ScoreBug({ match }: { match: Match }) {
  const { status } = match;
  const isLive = status === 'LIVE';
  const isFinal = status === 'FINAL';
  const hasScore = isLive || isFinal;
  const winner = finalWinner(match);
  const pens = penaltyNote(match);

  const countdown = useCountdown(!hasScore ? match.kickoff : undefined);
  const flip = useGoalFlip(match);

  const home = sideLabel(match.teamHome, match.sourceHome);
  const away = sideLabel(match.teamAway, match.sourceAway);

  return (
    <article className={styles.bug} data-status={status} aria-label={`${home} versus ${away}`}>
      <span className={styles.keylight} aria-hidden="true" />

      <div className={styles.side}>
        <FlagChip team={match.teamHome} size={72} ring={winner === 'home' ? 'win' : 'none'} />
        <span className={styles.team}>{home}</span>
      </div>

      <div className={styles.center}>
        <StatePill match={match} />

        {hasScore ? (
          <div className={styles.display}>
            <span className={`num ${styles.score} ${flip.home ? styles.flip : ''}`}>
              {match.scoreHome ?? 0}
            </span>
            <span className={styles.seam} aria-hidden="true">
              <i className={styles.notch} />
            </span>
            <span className={`num ${styles.score} ${flip.away ? styles.flip : ''}`}>
              {match.scoreAway ?? 0}
            </span>
          </div>
        ) : (
          <div className={`${styles.display} ${styles.displayCountdown}`}>
            <span className={`num ${styles.countdown}`}>{countdown || '—'}</span>
            <i className={styles.notch} aria-hidden="true" />
            <span className={styles.kickoffLabel}>Kickoff {formatKickoff(match.kickoff)}</span>
          </div>
        )}

        <span className={styles.meta}>
          {hasScore ? formatKickoff(match.kickoff) : stageLabel(match.stage, match.group)}
          {match.venue ? ` · ${match.venue}` : ''}
          {pens ? ` · ${pens}` : ''}
        </span>
      </div>

      <div className={styles.side}>
        <FlagChip team={match.teamAway} size={72} ring={winner === 'away' ? 'win' : 'none'} />
        <span className={styles.team}>{away}</span>
      </div>
    </article>
  );
}

function StatePill({ match }: { match: Match }) {
  if (match.status === 'LIVE') {
    return (
      <span className={`${styles.pill} ${styles.pillLive}`}>
        <i className={styles.liveDot} aria-hidden="true" />
        Live
      </span>
    );
  }
  if (match.status === 'FINAL') {
    return <span className={styles.pill}>Full time</span>;
  }
  return <span className={styles.pill}>{stageLabel(match.stage, match.group)}</span>;
}

/** Flag which numeral changed since the last LIVE score, briefly, to drive the goal-flip. */
function useGoalFlip(match: Match) {
  const prev = useRef<{ h?: number; a?: number }>({});
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const [flip, setFlip] = useState<{ home: boolean; away: boolean }>({ home: false, away: false });

  useEffect(() => {
    const ph = prev.current.h;
    const pa = prev.current.a;
    prev.current = { h: match.scoreHome, a: match.scoreAway };
    if (match.status !== 'LIVE') return;

    const home = ph !== undefined && match.scoreHome !== undefined && match.scoreHome !== ph;
    const away = pa !== undefined && match.scoreAway !== undefined && match.scoreAway !== pa;
    if (!home && !away) return;

    setFlip({ home, away });
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setFlip({ home: false, away: false }), 650);
    return () => clearTimeout(timer.current);
  }, [match.scoreHome, match.scoreAway, match.status]);

  return flip;
}
