import { useEffect, useRef, useState, useCallback } from 'react';
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

  // Guards against out-of-order responses — e.g. double-clicking the
  // pager can fire page-2's request just before page-3's, and if page-2's
  // slower response resolves after page-3's, it would otherwise overwrite
  // the table with stale rows. Only the most recently *started* request is
  // allowed to commit state.
  const requestIdRef = useRef(0);

  const refetch = useCallback(() => {
    const requestId = ++requestIdRef.current;
    setLoading(true);
    setError(null);
    getRecords({ ...filters, page, pageSize, sortBy, sortDir })
      .then(({ records: r, total: t }) => {
        if (requestIdRef.current !== requestId) return;
        setRecords(r);
        setTotal(t);
      })
      .catch((err) => {
        if (requestIdRef.current !== requestId) return;
        console.error('Failed to load records:', err);
        setRecords([]);
        setTotal(0);
        setError(err.message || 'Unable to load records');
      })
      .finally(() => {
        if (requestIdRef.current !== requestId) return;
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
