import { useEffect } from 'react';

const EDITABLE_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT']);

/**
 * Registers global single-key shortcuts, e.g. { m: markMatch, x: markMismatch }.
 * Ignored while focus is in a form field, and disabled entirely via `enabled`
 * (e.g. while a modal with its own text inputs is open).
 */
export default function useKeyboardShortcuts(map, enabled = true) {
  useEffect(() => {
    if (!enabled) return undefined;

    function handleKeyDown(e) {
      if (EDITABLE_TAGS.has(document.activeElement?.tagName) || e.metaKey || e.ctrlKey || e.altKey) {
        return;
      }
      const handler = map[e.key.toLowerCase()];
      if (handler) {
        e.preventDefault();
        handler(e);
      }
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, map]);
}
