import { FlagChip } from './FlagChip';
import type { StandingsRow } from '../lib/types';
import styles from './StandingsTable.module.css';

const gd = (n: number) => (n > 0 ? `+${n}` : String(n));

/** One group's standings as a compact, scannable table. Top two rows carry a gold qualify accent. */
export function StandingsTable({ group, rows }: { group: string; rows: StandingsRow[] }) {
  return (
    <div className={styles.card}>
      <h3 className={styles.head}>Group {group}</h3>
      <table className={styles.table}>
        <thead>
          <tr>
            <th className={styles.pos} scope="col">
              <span className="sr-only">Position</span>
            </th>
            <th className={styles.teamHead} scope="col">
              Team
            </th>
            <th scope="col" title="Played">P</th>
            <th scope="col" title="Won">W</th>
            <th scope="col" title="Drawn">D</th>
            <th scope="col" title="Lost">L</th>
            <th scope="col" title="Goal difference">GD</th>
            <th className={styles.ptsHead} scope="col" title="Points">Pts</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.team} className={i < 2 ? styles.qualify : ''}>
              <td className={styles.pos}>{i + 1}</td>
              <td className={styles.team}>
                <FlagChip team={r.team} size={22} />
                <span className={styles.name} title={r.team}>
                  {r.team}
                </span>
              </td>
              <td className={styles.n}>{r.played}</td>
              <td className={styles.n}>{r.won}</td>
              <td className={styles.n}>{r.drawn}</td>
              <td className={styles.n}>{r.lost}</td>
              <td className={styles.n}>{gd(r.goalDifference)}</td>
              <td className={`${styles.n} ${styles.pts}`}>{r.points}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
