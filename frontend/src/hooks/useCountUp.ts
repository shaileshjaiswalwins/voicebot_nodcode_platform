import { useState, useEffect, useRef } from 'react';

// Counts a number up from 0 to `target` over `duration`ms.
// Returns the current display value and a ref to attach to the element
// (adds/removes .counting CSS class so the shimmer animation fires).
export function useCountUp(target: number, duration = 600) {
  const [value, setValue] = useState(0);
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (target === 0) { setValue(0); return; }
    const start = performance.now();
    ref.current?.classList.add('counting');
    const tick = (now: number) => {
      const progress = Math.min((now - start) / duration, 1);
      // ease-out cubic
      const eased = 1 - Math.pow(1 - progress, 3);
      setValue(Math.round(target * eased));
      if (progress < 1) requestAnimationFrame(tick);
      else ref.current?.classList.remove('counting');
    };
    requestAnimationFrame(tick);
  }, [target, duration]);
  return { value, ref };
}
