/**
 * Team name → `flag-icons` country code.
 *
 * The flag is a PURE FUNCTION of the team NAME currently in the data, never tied to a fixture
 * slot — so when a knockout slot resolves (teamHome/teamAway flips from empty to "Brazil"), the
 * right flag appears automatically on the next render. Keys are the EXACT seeded national-team
 * spellings from assets/seed/elo_ratings.json (read read-only). Aliases cover plausible feed
 * spelling variants so a resolved knockout team never falls back to a blank crest by accident.
 */

// Exact seed spellings (48 teams).
const BY_NAME: Record<string, string> = {
  Algeria: 'dz',
  Argentina: 'ar',
  Australia: 'au',
  Austria: 'at',
  Belgium: 'be',
  'Bosnia and Herzegovina': 'ba',
  Brazil: 'br',
  Canada: 'ca',
  'Cape Verde': 'cv',
  Colombia: 'co',
  Croatia: 'hr',
  'Curaçao': 'cw',
  'Czech Republic': 'cz',
  'DR Congo': 'cd',
  Ecuador: 'ec',
  Egypt: 'eg',
  England: 'gb-eng',
  France: 'fr',
  Germany: 'de',
  Ghana: 'gh',
  Haiti: 'ht',
  Iran: 'ir',
  Iraq: 'iq',
  'Ivory Coast': 'ci',
  Japan: 'jp',
  Jordan: 'jo',
  Mexico: 'mx',
  Morocco: 'ma',
  Netherlands: 'nl',
  'New Zealand': 'nz',
  Norway: 'no',
  Panama: 'pa',
  Paraguay: 'py',
  Portugal: 'pt',
  Qatar: 'qa',
  'Saudi Arabia': 'sa',
  Scotland: 'gb-sct',
  Senegal: 'sn',
  'South Africa': 'za',
  'South Korea': 'kr',
  Spain: 'es',
  Sweden: 'se',
  Switzerland: 'ch',
  Tunisia: 'tn',
  Turkey: 'tr',
  'United States': 'us',
  Uruguay: 'uy',
  Uzbekistan: 'uz',
};

function normalize(name: string): string {
  return name.trim().toLowerCase();
}

// Normalized lookup + defensive aliases for spelling variants a live feed might use.
const BY_NORM: Record<string, string> = {};
for (const [name, code] of Object.entries(BY_NAME)) {
  BY_NORM[normalize(name)] = code;
}
Object.assign(BY_NORM, {
  curacao: 'cw',
  usa: 'us',
  'united states of america': 'us',
  'korea republic': 'kr',
  'republic of korea': 'kr',
  'korea, republic of': 'kr',
  czechia: 'cz',
  'côte d’ivoire': 'ci',
  'côte d\'ivoire': 'ci',
  "cote d'ivoire": 'ci',
  'cabo verde': 'cv',
  'türkiye': 'tr',
  turkiye: 'tr',
  'democratic republic of the congo': 'cd',
  'congo dr': 'cd',
  'bosnia & herzegovina': 'ba',
  wales: 'gb-wls',
});

/** The `flag-icons` code for a team name (e.g. "Brazil" → "br"), or '' if unknown/empty. */
export function flagCode(teamName: string | undefined | null): string {
  if (!teamName) return '';
  return BY_NORM[normalize(teamName)] ?? '';
}
