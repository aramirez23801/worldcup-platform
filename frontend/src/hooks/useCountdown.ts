import { useEffect, useMemo, useState } from 'react';
import { formatCountdown } from '../lib/format';

/** Live countdown string to an ISO kickoff, ticking once a second. '' when no target. */
export function useCountdown(targetIso?: string): string {
  const target = useMemo(() => (targetIso ? Date.parse(targetIso) : NaN), [targetIso]);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!targetIso) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [targetIso]);

  return formatCountdown(target, now);
}
