import { useEffect, useState, useCallback } from 'react';
import { getVerificationStats, subscribe } from '../services/verificationService';

export default function useVerificationStats() {
  const [stats, setStats] = useState(null);

  const refetch = useCallback(() => {
    getVerificationStats().then(setStats);
  }, []);

  useEffect(() => {
    refetch();
    const unsubscribe = subscribe(refetch);
    return unsubscribe;
  }, [refetch]);

  return stats;
}
