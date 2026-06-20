import { useMatches } from '../hooks/useMatches';
import { useStandings } from '../hooks/useStandings';
import { StandingsTable } from '../components/StandingsTable';
import { Bracket } from '../components/Bracket';
import { Notice } from '../components/Notice';
import type { Standings } from '../lib/types';
import styles from './Matches.module.css';

export default function Matches() {
  const { matches, loading: mLoading, error: mError, reload: mReload } = useMatches();
  const { standings, loading: sLoading, error: sError, reload: sReload } = useStandings();

  return (
    <div className="page">
      <h1 className="page-title">Matches</h1>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Group standings</h2>
        {sLoading ? (
          <GridSkeleton count={12} className={styles.tableSkeleton} />
        ) : sError ? (
          <Notice
            tone="error"
            title="Couldn't load standings"
            message={sError}
            action={{ label: 'Try again', onClick: () => void sReload() }}
          />
        ) : standings ? (
          <GroupGrid standings={standings} />
        ) : null}
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Knockout bracket</h2>
        {mLoading ? (
          <GridSkeleton count={8} className={styles.nodeSkeleton} />
        ) : mError ? (
          <Notice
            tone="error"
            title="Couldn't load the bracket"
            message={mError}
            action={{ label: 'Try again', onClick: () => void mReload() }}
          />
        ) : (
          <Bracket matches={matches ?? []} />
        )}
      </section>
    </div>
  );
}

function GroupGrid({ standings }: { standings: Standings }) {
  const groups = Object.keys(standings).sort();
  if (groups.length === 0) {
    return <Notice title="No groups yet" message="Group tables will appear once fixtures are seeded." />;
  }
  return (
    <div className={styles.groupGrid}>
      {groups.map((g) => (
        <StandingsTable key={g} group={g} rows={standings[g]} />
      ))}
    </div>
  );
}

function GridSkeleton({ count, className }: { count: number; className: string }) {
  return (
    <div className={styles.groupGrid} role="status" aria-label="Loading">
      {Array.from({ length: count }).map((_, i) => (
        <span key={i} className={`skeleton ${className}`} aria-hidden="true" />
      ))}
    </div>
  );
}
