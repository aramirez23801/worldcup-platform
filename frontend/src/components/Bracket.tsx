import { MatchLockup } from './MatchLockup';
import { stageLabel } from '../lib/format';
import type { Match, Stage } from '../lib/types';
import styles from './Bracket.module.css';

const ROUND_ORDER: Stage[] = ['R32', 'R16', 'QF', 'SF', '3P', 'F'];

const byKickoff = (a: Match, b: Match) => a.kickoff.localeCompare(b.kickoff);

/**
 * Knockout bracket as stacked round sections (no connector-line SVG, per the brief). Each node is
 * the quiet MatchLockup — the live-flag surface: when a slot resolves, the team name fills in and
 * its flag follows. Live scores arrive over WebSocket via the shared matches state.
 */
export function Bracket({ matches }: { matches: Match[] }) {
  const byStage = new Map<Stage, Match[]>();
  for (const m of matches) {
    if (!ROUND_ORDER.includes(m.stage)) continue;
    const list = byStage.get(m.stage) ?? [];
    list.push(m);
    byStage.set(m.stage, list);
  }

  const rounds = ROUND_ORDER.filter((s) => byStage.has(s));

  if (rounds.length === 0) {
    return <p className={styles.empty}>The bracket opens once the group stage resolves.</p>;
  }

  return (
    <div className={styles.bracket}>
      {rounds.map((stage) => (
        <section key={stage} className={styles.round}>
          <h3 className={styles.roundTitle}>{stageLabel(stage)}</h3>
          <div className={styles.nodes}>
            {(byStage.get(stage) ?? []).sort(byKickoff).map((m) => (
              <MatchLockup key={m.matchId} match={m} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
