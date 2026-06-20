import { Link } from 'react-router-dom';
import { useMatches } from '../hooks/useMatches';
import { ScoreBug } from '../components/ScoreBug';
import { MatchLockup } from '../components/MatchLockup';
import { Notice } from '../components/Notice';
import type { Match } from '../lib/types';
import styles from './Home.module.css';

const byKickoff = (a: Match, b: Match) => a.kickoff.localeCompare(b.kickoff);
const playable = (m: Match) => Boolean(m.teamHome && m.teamAway);

/** The single match to feature: a live one if any, else the next scheduled, else the latest final. */
function pickHero(matches: Match[]): Match | null {
  const withTeams = matches.filter(playable);
  const live = withTeams.filter((m) => m.status === 'LIVE').sort(byKickoff);
  if (live.length) return live[0];
  const scheduled = withTeams.filter((m) => m.status === 'SCHEDULED').sort(byKickoff);
  if (scheduled.length) return scheduled[0];
  const finals = withTeams.filter((m) => m.status === 'FINAL').sort(byKickoff);
  if (finals.length) return finals[finals.length - 1];
  return matches[0] ?? null;
}

const heroKicker = (m: Match) =>
  m.status === 'LIVE' ? 'Live now' : m.status === 'FINAL' ? 'Latest result' : 'Next match';

const betLink = (m: Match) => `/bet?match=${encodeURIComponent(m.matchId)}`;

export default function Home() {
  const { matches, loading, error, reload } = useMatches();

  if (loading) return <HomeSkeleton />;
  if (error) {
    return (
      <div className="page">
        <Notice
          tone="error"
          title="Couldn't load matches"
          message={error}
          action={{ label: 'Try again', onClick: () => void reload() }}
        />
      </div>
    );
  }
  if (!matches || matches.length === 0) {
    return (
      <div className="page">
        <Notice title="No matches yet" message="Fixtures will appear here once the schedule is live." />
      </div>
    );
  }

  const hero = pickHero(matches);
  const upcoming = matches
    .filter(
      (m) =>
        hero &&
        m.matchId !== hero.matchId &&
        (m.status === 'SCHEDULED' || m.status === 'LIVE') &&
        playable(m),
    )
    .sort(byKickoff)
    .slice(0, 6);

  return (
    <div className="page">
      {hero && (
        <section className={styles.hero}>
          <h1 className="page-title">{heroKicker(hero)}</h1>
          <ScoreBug match={hero} />
          <HeroActions match={hero} />
        </section>
      )}

      {upcoming.length > 0 && (
        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>Upcoming</h2>
          <div className={styles.grid}>
            {upcoming.map((m) => (
              <MatchLockup
                key={m.matchId}
                match={m}
                to={m.status === 'SCHEDULED' ? betLink(m) : '/matches'}
              />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function HeroActions({ match }: { match: Match }) {
  if (match.status === 'SCHEDULED') {
    return (
      <div className={styles.actions}>
        <Link to={betLink(match)} className="btn btn--accent">
          Place bet
        </Link>
        <Link to={betLink(match)} className="btn btn--ghost">
          Forecast
        </Link>
      </div>
    );
  }
  if (match.status === 'LIVE') {
    return (
      <div className={styles.actions}>
        <span className={styles.note}>Betting is closed — match in play.</span>
        <Link to="/matches" className="btn btn--ghost">
          Watch live
        </Link>
      </div>
    );
  }
  return (
    <div className={styles.actions}>
      <span className={styles.note}>Full time.</span>
      <Link to="/matches" className="btn btn--ghost">
        See matches
      </Link>
    </div>
  );
}

function HomeSkeleton() {
  return (
    <div className="page">
      <section className={styles.hero}>
        <span className={`skeleton ${styles.titleSkeleton}`} aria-hidden="true" />
        <div className={`skeleton ${styles.heroSkeleton}`} role="status" aria-label="Loading matches" />
      </section>
      <section className={styles.section}>
        <span className={`skeleton ${styles.titleSkeleton}`} aria-hidden="true" />
        <div className={styles.grid}>
          {Array.from({ length: 6 }).map((_, i) => (
            <span key={i} className={`skeleton ${styles.cardSkeleton}`} aria-hidden="true" />
          ))}
        </div>
      </section>
    </div>
  );
}
