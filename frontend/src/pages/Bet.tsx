import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useApi } from '../hooks/useApi';
import { useMatches } from '../hooks/useMatches';
import { useBets } from '../hooks/useBets';
import { useWallet } from '../state/WalletProvider';
import { ApiError, errorMessage } from '../lib/api';
import {
  betSelectionLabel,
  formatCoins,
  formatKickoff,
  sideLabel,
  stageLabel,
} from '../lib/format';
import { MatchLockup } from '../components/MatchLockup';
import { FlagChip } from '../components/FlagChip';
import { Notice } from '../components/Notice';
import type { Bet, BetType, Forecast, Match, Selection } from '../lib/types';
import styles from './Bet.module.css';

const byKickoff = (a: Match, b: Match) => a.kickoff.localeCompare(b.kickoff);
const playable = (m: Match) => Boolean(m.teamHome && m.teamAway);
const round2 = (n: number) => Math.round(n * 100) / 100;

export default function Bet() {
  const [params] = useSearchParams();
  const { matches, loading, error, reload: reloadMatches } = useMatches();
  const wallet = useWallet();
  const betsState = useBets();

  const [selectedId, setSelectedId] = useState<string | null>(params.get('match'));

  const matchById = useMemo(
    () => new Map((matches ?? []).map((m) => [m.matchId, m] as const)),
    [matches],
  );
  const scheduled = useMemo(
    () => (matches ?? []).filter((m) => m.status === 'SCHEDULED' && playable(m)).sort(byKickoff),
    [matches],
  );
  const selected = selectedId ? matchById.get(selectedId) ?? null : null;

  return (
    <div className="page">
      <h1 className="page-title">Bet</h1>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Pick a match</h2>
        {loading ? (
          <PickerSkeleton />
        ) : error ? (
          <Notice
            tone="error"
            title="Couldn't load matches"
            message={error}
            action={{ label: 'Try again', onClick: () => void reloadMatches() }}
          />
        ) : scheduled.length === 0 ? (
          <Notice
            title="No matches open for betting"
            message="Check back when the next fixtures are scheduled."
          />
        ) : (
          <div className={styles.pickerScroll}>
            <div className={styles.pickerGrid}>
              {scheduled.map((m) => (
                <MatchLockup
                  key={m.matchId}
                  match={m}
                  selected={m.matchId === selectedId}
                  onSelect={() => setSelectedId(m.matchId)}
                />
              ))}
            </div>
          </div>
        )}
      </section>

      {selected && (
        <section className={styles.section}>
          {selected.status === 'SCHEDULED' && playable(selected) ? (
            <BetPanel
              match={selected}
              onPlaced={() => {
                void betsState.reload();
                void wallet.refresh();
              }}
            />
          ) : (
            <ClosedNotice match={selected} />
          )}
        </section>
      )}

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Your bets</h2>
        <OpenBets state={betsState} matchById={matchById} />
      </section>
    </div>
  );
}

/* ---- Forecast + bet form ------------------------------------------------ */

type Outcome = {
  betType: BetType;
  selection: Selection;
  label: string;
  odds: number | null;
  prob: number;
};

function BetPanel({ match, onPlaced }: { match: Match; onPlaced: () => void }) {
  const api = useApi();
  const wallet = useWallet();

  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [fLoading, setFLoading] = useState(true);
  const [fError, setFError] = useState<string | null>(null);

  const [picked, setPicked] = useState<Outcome | null>(null);
  const [stake, setStake] = useState('');
  const [placing, setPlacing] = useState(false);
  const [placeError, setPlaceError] = useState<string | null>(null);
  const [placed, setPlaced] = useState<Bet | null>(null);
  const placingRef = useRef(false); // synchronous in-flight lock (see place())

  // Fetch the forecast whenever the match changes; reset the form.
  useEffect(() => {
    let cancelled = false;
    setFLoading(true);
    setFError(null);
    setForecast(null);
    setPicked(null);
    setStake('');
    setPlaceError(null);
    setPlaced(null);
    api
      .getForecast(match.matchId)
      .then((f) => !cancelled && setForecast(f))
      .catch((e) => !cancelled && setFError(errorMessage(e)))
      .finally(() => !cancelled && setFLoading(false));
    return () => {
      cancelled = true;
    };
  }, [api, match.matchId]);

  const result: Outcome[] = forecast
    ? [
        { betType: '1X2', selection: 'HOME', label: betSelectionLabel('1X2', 'HOME', match.teamHome, match.teamAway), odds: forecast.odds.teamA, prob: forecast.probabilities.teamA },
        { betType: '1X2', selection: 'DRAW', label: 'Draw', odds: forecast.odds.draw, prob: forecast.probabilities.draw },
        { betType: '1X2', selection: 'AWAY', label: betSelectionLabel('1X2', 'AWAY', match.teamHome, match.teamAway), odds: forecast.odds.teamB, prob: forecast.probabilities.teamB },
      ]
    : [];
  const goals: Outcome[] = forecast
    ? [
        { betType: 'OU25', selection: 'OVER', label: 'Over 2.5 goals', odds: forecast.odds.over25, prob: forecast.probabilities.over25 },
        { betType: 'OU25', selection: 'UNDER', label: 'Under 2.5 goals', odds: forecast.odds.under25, prob: forecast.probabilities.under25 },
      ]
    : [];

  const stakeValid = /^\d+$/.test(stake) && Number(stake) > 0;
  const stakeNum = stakeValid ? Number(stake) : 0;
  const balance = wallet.balance;
  const overBalance = stakeValid && balance != null && stakeNum > balance;
  const payout = picked?.odds != null && stakeValid ? round2(stakeNum * picked.odds) : null;
  const canPlace =
    !!picked &&
    picked.odds != null &&
    stakeValid &&
    !overBalance &&
    !placing &&
    match.status === 'SCHEDULED' &&
    Date.parse(match.kickoff) > Date.now(); // not bettable once kicked off (feed lag)

  async function place() {
    if (!canPlace || !picked) return;
    // Synchronous lock: the button's disabled state only updates after a re-render, so a fast
    // double-click could otherwise fire two requests — and the backend has no idempotency.
    if (placingRef.current) return;
    placingRef.current = true;
    setPlacing(true);
    setPlaceError(null);
    setPlaced(null);
    try {
      const bet = await api.placeBet({
        matchId: match.matchId,
        betType: picked.betType,
        selection: picked.selection,
        stake: stakeNum,
      });
      setPlaced(bet);
      setPicked(null);
      setStake('');
      onPlaced();
    } catch (e) {
      if (e instanceof ApiError && e.status === 402) {
        setPlaceError(`Not enough coins${balance != null ? ` — your balance is ${formatCoins(balance)}` : ''}.`);
      } else {
        setPlaceError(errorMessage(e));
      }
    } finally {
      placingRef.current = false;
      setPlacing(false);
    }
  }

  const home = sideLabel(match.teamHome, match.sourceHome);
  const away = sideLabel(match.teamAway, match.sourceAway);

  return (
    <div className={styles.panel}>
      <div className={styles.panelHead}>
        <div className={styles.matchup}>
          <FlagChip team={match.teamHome} size={28} />
          <span className={styles.matchupName}>
            {home} <span className={styles.v}>v</span> {away}
          </span>
          <FlagChip team={match.teamAway} size={28} />
        </div>
        <span className={styles.kickoff}>
          {stageLabel(match.stage, match.group)} · {formatKickoff(match.kickoff)}
        </span>
      </div>

      {fLoading ? (
        <div className={`skeleton ${styles.forecastSkeleton}`} role="status" aria-label="Loading forecast" />
      ) : fError ? (
        <Notice tone="error" title="Couldn't load the forecast" message={fError} />
      ) : (
        <>
          <Market title="Match result" outcomes={result} picked={picked} onPick={setPicked} />
          <Market title="Goals — Over / Under 2.5" outcomes={goals} picked={picked} onPick={setPicked} />

          <div className={styles.form}>
            <label className={styles.stakeField}>
              <span className={styles.stakeLabel}>Stake (coins)</span>
              <input
                className={styles.input}
                type="text"
                inputMode="numeric"
                placeholder="0"
                value={stake}
                onChange={(e) => setStake(e.target.value.replace(/[^\d]/g, ''))}
                aria-invalid={overBalance || (stake !== '' && !stakeValid)}
              />
              {balance != null && <span className={styles.balanceHint}>Balance {formatCoins(balance)}</span>}
            </label>

            <div className={styles.payoutRow}>
              <span className={styles.payoutLabel}>Potential payout</span>
              <span className={`num ${styles.payoutValue}`}>
                {payout != null ? formatCoins(payout) : '—'}
              </span>
            </div>

            <div aria-live="polite">
              {overBalance && <p className={styles.error}>That's more than your balance.</p>}
              {placeError && <p className={styles.error}>{placeError}</p>}
              {placed && (
                <p className={styles.success}>
                  Bet placed — {betSelectionLabel(placed.betType, placed.selection, match.teamHome, match.teamAway)} ·{' '}
                  {formatCoins(placed.stake)} at {placed.oddsSnapshot.toFixed(2)}.
                </p>
              )}
            </div>

            <button className="btn btn--accent" onClick={place} disabled={!canPlace}>
              {placing ? 'Placing…' : 'Place bet'}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function Market({
  title,
  outcomes,
  picked,
  onPick,
}: {
  title: string;
  outcomes: Outcome[];
  picked: Outcome | null;
  onPick: (o: Outcome) => void;
}) {
  return (
    <div className={styles.market}>
      <span className={styles.marketTitle}>{title}</span>
      <div className={styles.outcomes}>
        {outcomes.map((o) => {
          const isPicked =
            picked?.betType === o.betType && picked?.selection === o.selection;
          return (
            <button
              key={`${o.betType}-${o.selection}`}
              type="button"
              className={`${styles.outcome} ${isPicked ? styles.outcomePicked : ''}`}
              onClick={() => onPick(o)}
              disabled={o.odds == null}
              aria-pressed={isPicked}
            >
              <span className={styles.outcomeLabel}>{o.label}</span>
              <span className={`num ${styles.outcomeOdds}`}>{o.odds != null ? o.odds.toFixed(2) : '—'}</span>
              <span className={styles.outcomeProb}>{Math.round(o.prob * 100)}%</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function ClosedNotice({ match }: { match: Match }) {
  const home = sideLabel(match.teamHome, match.sourceHome);
  const away = sideLabel(match.teamAway, match.sourceAway);
  return (
    <Notice
      title="Betting is closed"
      message={`${home} v ${away} has kicked off — bets can only be placed on scheduled matches.`}
    />
  );
}

/* ---- Open bets ---------------------------------------------------------- */

function OpenBets({
  state,
  matchById,
}: {
  state: ReturnType<typeof useBets>;
  matchById: Map<string, Match>;
}) {
  const { bets, loading, error, reload } = state;

  if (loading) return <div className={`skeleton ${styles.betsSkeleton}`} role="status" aria-label="Loading bets" />;
  if (error) {
    return (
      <Notice
        tone="error"
        title="Couldn't load your bets"
        message={error}
        action={{ label: 'Try again', onClick: () => void reload() }}
      />
    );
  }
  if (!bets || bets.length === 0) {
    return <Notice title="No bets yet" message="Pick a match above and place your first bet." />;
  }

  return (
    <div className={styles.betList}>
      {bets.map((bet) => (
        <BetRow key={bet.betId} bet={bet} match={matchById.get(bet.matchId)} />
      ))}
    </div>
  );
}

function BetRow({ bet, match }: { bet: Bet; match?: Match }) {
  const matchup = match
    ? `${sideLabel(match.teamHome, match.sourceHome)} v ${sideLabel(match.teamAway, match.sourceAway)}`
    : bet.matchId;
  const label = betSelectionLabel(bet.betType, bet.selection, match?.teamHome, match?.teamAway);

  return (
    <div className={styles.betRow}>
      <div className={styles.betMain}>
        <span className={styles.betMatchup}>{matchup}</span>
        <span className={styles.betSelection}>{label}</span>
      </div>
      <div className={styles.betNums}>
        <span className={styles.betNum}>
          Stake <b className="num">{formatCoins(bet.stake)}</b>
        </span>
        <span className={styles.betNum}>
          Odds <b className="num">{bet.oddsSnapshot.toFixed(2)}</b>
        </span>
      </div>
      <BetStatusBadge bet={bet} />
    </div>
  );
}

function BetStatusBadge({ bet }: { bet: Bet }) {
  if (bet.status === 'WON') {
    return (
      <span className={`${styles.badge} ${styles.badgeWon}`}>
        Won {bet.payout != null ? `+${formatCoins(bet.payout)}` : ''}
      </span>
    );
  }
  if (bet.status === 'LOST') {
    return <span className={`${styles.badge} ${styles.badgeLost}`}>Lost</span>;
  }
  return <span className={`${styles.badge} ${styles.badgePending}`}>Pending</span>;
}

/* ---- Skeletons ---------------------------------------------------------- */

function PickerSkeleton() {
  return (
    <div className={styles.pickerGrid} role="status" aria-label="Loading matches">
      {Array.from({ length: 6 }).map((_, i) => (
        <span key={i} className={`skeleton ${styles.pickSkeleton}`} aria-hidden="true" />
      ))}
    </div>
  );
}
