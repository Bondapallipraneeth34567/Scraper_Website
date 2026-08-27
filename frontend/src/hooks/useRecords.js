import { useEffect, useState, useCallback } from 'react';
import { getRecords, subscribe } from '../services/verificationService';

/** Filtered, server-paginated record list that re-fetches whenever filters,
 * page, or sort change, or the service notifies a write (verify/import).
 * `total` is the real count matching the filters in the database —
 * records.length only reflects how many rows this one page fetched (capped
 * at pageSize), since fetching every matching row up front isn't feasible
 * when a filter (e.g. a single state) still matches thousands of rows. */
export default function useRecords(filters, { page = 1, pageSize = 50, sortBy, sortDir } = {}) {
  const [records, setRecords] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const refetch = useCallback(() => {
    setLoading(true);
    setError(null);
    getRecords({ ...filters, page, pageSize, sortBy, sortDir })
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
  }, [JSON.stringify(filters), page, pageSize, sortBy, sortDir]);

  useEffect(() => {
    refetch();
    const unsubscribe = subscribe(refetch);
    return unsubscribe;
  }, [refetch]);

  return { records, total, loading, error, refetch };
}
