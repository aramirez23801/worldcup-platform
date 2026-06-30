/** Shapes mirrored from the backend handlers (assets/_lambda + assets/domain). Read-only contract. */

export type MatchStatus = 'TBD' | 'SCHEDULED' | 'LIVE' | 'FINAL';
export type Stage = 'GROUP' | 'R32' | 'R16' | 'QF' | 'SF' | '3P' | 'F';

export interface Match {
  matchId: string;
  stage: Stage;
  group?: string;
  matchday?: number;
  teamHome?: string;
  teamAway?: string;
  sourceHome?: string;
  sourceAway?: string;
  kickoff: string; // UTC ISO, e.g. "2026-06-11T19:00:00Z"
  venue?: string;
  status: MatchStatus;
  scoreHome?: number;
  scoreAway?: number;
  decidedBy?: 'PENALTIES'; // a knockout settled by a shootout; the score stays level
  penaltyWinner?: string; // team that advanced on penalties (matches teamHome or teamAway)
  externalId?: string;
}

export interface Wallet {
  userId: string;
  balance: number;
}

export interface StandingsRow {
  team: string;
  played: number;
  won: number;
  drawn: number;
  lost: number;
  goalsFor: number;
  goalsAgainst: number;
  goalDifference: number;
  points: number;
}

/** Group letter (A–L) → ordered rows, best first. */
export type Standings = Record<string, StandingsRow[]>;

export interface LeaderboardRow {
  userId: string;
  profit: number;
  wins: number;
  losses: number;
  displayName?: string; // present only if the user has set one
}

export type BetType = '1X2' | 'OU25';
export type Selection = 'HOME' | 'DRAW' | 'AWAY' | 'OVER' | 'UNDER';
export type BetStatus = 'PENDING' | 'WON' | 'LOST';

export interface Forecast {
  matchId: string;
  teamA: string; // home
  teamB: string; // away
  ratings: { teamA: number; teamB: number };
  expectedGoals: { teamA: number; teamB: number };
  probabilities: { teamA: number; draw: number; teamB: number; over25: number; under25: number };
  odds: {
    teamA: number | null;
    draw: number | null;
    teamB: number | null;
    over25: number | null;
    under25: number | null;
  };
}

export interface Bet {
  userId: string;
  betId: string;
  matchId: string;
  betType: BetType;
  selection: Selection;
  stake: number;
  oddsSnapshot: number;
  status: BetStatus;
  placedAt: string;
  settledAt?: string;
  payout?: number;
}

export interface PlaceBetBody {
  matchId: string;
  betType: BetType;
  selection: Selection;
  stake: number;
}

export interface AgentReply {
  sessionId: string;
  specialist: string | null;
  response: string | null;
}

export interface SettledBet {
  betId: string;
  betType: string;
  selection: string;
  status: 'WON' | 'LOST';
  payout: number;
}

/** Messages pushed over the WebSocket (func_results, func_leaderboard_agg, func_notify). */
export type WsMessage =
  | { type: 'match.live'; matchId: string; scoreHome: number; scoreAway: number }
  | { type: 'match.final'; matchId: string; scoreHome: number; scoreAway: number }
  | { type: 'leaderboard'; standings: LeaderboardRow[] }
  | {
      type: 'bets.settled';
      matchId: string;
      score: string;
      balance: number | null;
      bets: SettledBet[];
    };
