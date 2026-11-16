import { useEffect, useState } from "react";

/** Text that types itself out once, quickly (about a second, however long), for a reply that just
 *  arrived. Replies already there when a chat opens show at once. Still for reduced motion. */
export function TypedText({ text, animate }: { text: string; animate: boolean }) {
  const reduced = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const [shown, setShown] = useState(animate && !reduced ? 0 : text.length);
  useEffect(() => {
    if (!animate || reduced) { setShown(text.length); return; }
    let frame = 0;
    const started = performance.now();
    const total = Math.min(1100, 250 + text.length * 6);  // short replies quickly, long ones capped
    const step = (now: number) => {
      const done = Math.min(1, (now - started) / total);
      const eased = 1 - Math.pow(1 - done, 2);
      setShown(Math.round(text.length * eased));
      if (done < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [text, animate, reduced]);
  const typing = shown < text.length;
  return <>{text.slice(0, shown)}{typing && <span className="type-caret" aria-hidden="true" />}{typing && <span className="sr-only">{text}</span>}</>;
}
