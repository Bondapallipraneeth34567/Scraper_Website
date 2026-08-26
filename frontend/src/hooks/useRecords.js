import { useEffect, useState, useCallback } from 'react';
import { getRecords, subscribe } from '../services/verificationService';

/** Filtered record list that re-fetches whenever filters change or the
 * service notifies a write (verify/import). `total` is the real count
 * matching the filters in the database — records.length only reflects
 * how many rows this page actually fetched (capped at pageSize). */
export default function useRecords(filters) {
  const [records, setRecords] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const refetch = useCallback(() => {
    setLoading(true);
    setError(null);
    getRecords(filters)
      .then(({ records: r, total: t }) => {
        setRecords(r);
        setTotal(t);
      })
      .catch((err) => {
        console.error('Failed to load records:', err);
        setRecords([]);
        setTotal(0);
        setError(err.message || 'Unable to load records');
      })
      .finally(() => {
        setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(filters)]);

  useEffect(() => {
    refetch();
    const unsubscribe = subscribe(refetch);
    return unsubscribe;
  }, [refetch]);

  return { records, total, loading, error, refetch };
}
