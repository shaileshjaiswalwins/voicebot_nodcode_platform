import React, { useState, useEffect } from 'react';
import { formatDate, timeAgo } from '../utils/formatting';

export function TimeAgo({ value }: { value?: string }) {
  const [, forceUpdate] = useState(0);
  useEffect(() => {
    const id = setInterval(() => forceUpdate(n => n + 1), 60_000);
    return () => clearInterval(id);
  }, []);
  const abs = value ? formatDate(value) : '-';
  return <span title={abs}>{timeAgo(value)}</span>;
}
