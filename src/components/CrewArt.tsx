import type { CSSProperties, ReactNode } from "react";

/* One small living scene per kind of Crew. Colours come from the card (--hue, --art, --art-2); the
   shapes only say what is drawn. Motion lives in art.css (classes a-*), and pauses while stopped. */

/** The hue (0-360) each kind of Crew wears. */
export const HUES: Record<string, number> = {
  schedule: 32, communication: 214, computer: 176, coding: 268, health: 350, fitness: 18, learning: 236, research: 196,
  project: 148, files: 46, operator: 286, travel: 202, home: 158, wellbeing: 322, "personal-admin": 224,
};

export const artStyle = (template: string): CSSProperties => ({ ["--hue" as string]: HUES[template] ?? 250 });

const clock = { transformBox: "view-box", transformOrigin: "146px 84px" } as CSSProperties;
const spine = { transformBox: "view-box", transformOrigin: "100px 60px" } as CSSProperties;
const path = (d: string): CSSProperties => ({ offsetPath: `path('${d}')` } as CSSProperties);
const delay = (s: number): CSSProperties => ({ ["--dl" as string]: `${s}s` });

const ART: Record<string, ReactNode> = {
  schedule: <>
    <g className="a-float">
      <rect className="pp" x="54" y="20" width="84" height="76" rx="14" />
      <path className="ac" d="M54 34a14 14 0 0 1 14-14h56a14 14 0 0 1 14 14v8H54z" />
      <rect className="pp" x="76" y="13" width="5" height="13" rx="2.5" /><rect className="pp" x="111" y="13" width="5" height="13" rx="2.5" />
      {[0, 1, 2].flatMap(r => [0, 1, 2].map(c => r === 2 && c === 1
        ? <circle key={`${r}${c}`} className="ac a-pulse" cx={75 + c * 21} cy={58 + r * 15} r="5" />
        : <circle key={`${r}${c}`} className="is" cx={75 + c * 21} cy={58 + r * 15} r="3.2" />))}
    </g>
    <circle className="pp" cx="146" cy="84" r="19" /><circle className="il" cx="146" cy="84" r="14" style={{ opacity: .45 }} />
    <line className="al a-spin" x1="146" y1="84" x2="146" y2="73" style={clock} /><circle className="ac" cx="146" cy="84" r="2.6" />
  </>,
  communication: <>
    <g className="a-float">
      <rect className="pp" x="46" y="32" width="108" height="64" rx="14" />
      <path className="il" d="M54 42l46 33 46-33" style={{ opacity: .8 }} />
      <rect className="is" x="62" y="80" width="30" height="4.5" rx="2.2" /><rect className="is" x="62" y="88" width="18" height="4.5" rx="2.2" />
      <circle className="ac a-pop" cx="152" cy="32" r="11" /><circle fill="#fff" cx="152" cy="32" r="3.6" />
    </g>
  </>,
  computer: <>
    <rect className="pp" x="42" y="16" width="116" height="70" rx="14" />
    <path className="il" d="M58 70h84" />
    {[[62, 24, 0], [84, 38, .4], [106, 18, .8], [128, 30, 1.2]].map(([x, h, d], i) =>
      <rect key={x} className={(i === 1 ? "ac2" : "ac") + " a-grow"} style={delay(d)} x={x} y={70 - h} width="13" height={h} rx="4" />)}
    <path className="il" d="M100 86v9M82 97h36" />
  </>,
  coding: <>
    <g className="a-float">
      <rect className="pp" x="36" y="20" width="128" height="76" rx="14" />
      <circle className="ac" cx="52" cy="33" r="3" /><circle className="ac2" cx="63" cy="33" r="3" /><circle className="is" cx="74" cy="33" r="3" />
      <path className="al" d="M80 56 66 68l14 12M120 56l14 12-14 12" />
      <path className="il" d="m107 52-8 32" />
      <rect className="ac a-blink" x="142" y="62" width="3.4" height="18" rx="1.7" />
    </g>
  </>,
  health: <>
    <path className="as" d="M100 90C58 66 52 42 73 33c11-5 22 1 27 11 5-10 16-16 27-11 21 9 15 33-27 57z" style={{ transform: "translateY(6px) scale(1.08)", opacity: .7 }} />
    <path className="ac a-beat" d="M100 88C60 64 54 42 74 34c11-4 22 2 26 11 4-9 15-15 26-11 20 8 14 30-26 54z" />
    <path className="il" d="M26 92h44l9-17 12 32 11-24 8 9h60" style={{ opacity: .4 }} />
    <path className="al a-ecg" pathLength={200} d="M26 92h44l9-17 12 32 11-24 8 9h60" style={{ stroke: "var(--art-2)", strokeWidth: 3 }} />
  </>,
  fitness: <>
    <ellipse className="is" cx="100" cy="100" rx="42" ry="4.5" />
    <g className="a-lift">
      <rect className="ac" x="40" y="42" width="14" height="36" rx="5" /><rect className="ac2" x="56" y="32" width="15" height="56" rx="5.5" />
      <rect className="pp" x="70" y="56" width="60" height="8" rx="4" />
      <rect className="ac2" x="129" y="32" width="15" height="56" rx="5.5" /><rect className="ac" x="146" y="42" width="14" height="36" rx="5" />
    </g>
  </>,
  learning: <>
    <g className="a-float">
      <path className="pp" d="M100 38C84 28 62 28 44 34v52c18-6 40-6 56 4z" />
      <path className="pp" d="M100 38c16-10 38-10 56-4v52c-18-6-40-6-56 4z" />
      <path className="is" d="M56 48c9-2 18-1 30 3M56 60c9-2 18-1 30 3M56 72c9-2 18-1 30 3M114 51c10-3 19-4 30-3M114 63c10-3 19-4 30-3" />
      <path className="as a-flip" d="M100 38c16-10 38-10 56-4v52c-18-6-40-6-56 4z" style={spine} />
      <path className="ac a-pop" d="M100 12l2.8 6 6.2.8-4.6 4.3 1.3 6.2-5.7-3.2-5.7 3.2 1.3-6.2L91 18.8l6.2-.8z" />
    </g>
  </>,
  research: <>
    <rect className="pp" x="52" y="18" width="72" height="82" rx="12" />
    <rect className="is" x="64" y="32" width="46" height="5" rx="2.5" /><rect className="is" x="64" y="46" width="34" height="5" rx="2.5" />
    <rect className="is" x="64" y="60" width="44" height="5" rx="2.5" /><rect className="is" x="64" y="74" width="28" height="5" rx="2.5" />
    <g className="a-scan">
      <circle cx="118" cy="62" r="23" fill="color-mix(in srgb, var(--surface) 55%, transparent)" stroke="var(--art)" strokeWidth="3.4" />
      <path className="al" d="m134 79 17 17" style={{ strokeWidth: 6 }} />
      <path className="il" d="M105 53a16 16 0 0 1 12-6" style={{ stroke: "var(--art)", opacity: .6 }} />
    </g>
  </>,
  project: <>
    {[44, 84, 124].map((x, i) => <rect key={x} className="pp" x={x} y="22" width="34" height="74" rx="10" style={{ opacity: i === 2 ? .94 : 1 }} />)}
    <rect className="is" x="50" y="32" width="22" height="11" rx="4" /><rect className="is" x="50" y="48" width="22" height="11" rx="4" />
    <rect className="is" x="90" y="32" width="22" height="11" rx="4" />
    <rect className="ac2" x="130" y="32" width="22" height="11" rx="4" />
    <rect className="ac a-move" x="50" y="64" width="22" height="11" rx="4" />
  </>,
  files: <>
    <path className="as" d="M44 38a8 8 0 0 1 8-8h26l9 10h61a8 8 0 0 1 8 8v8H44z" />
    <rect className="pp a-slide" x="62" y="38" width="66" height="50" rx="8" /><path className="il a-slide" d="M74 52h38M74 62h26" />
    <path className="pp" d="M44 50a8 8 0 0 1 8-8h96a8 8 0 0 1 8 8v40a8 8 0 0 1-8 8H52a8 8 0 0 1-8-8z" />
    <path className="ac" d="M44 50a8 8 0 0 1 8-8h96a8 8 0 0 1 8 8v6H44z" style={{ opacity: .9 }} />
  </>,
  operator: <>
    <path className="il" d="M58 58 100 34M58 58l42 24M100 34l44 24M100 82l44-24M100 34v48" />
    {[[58, 58, 13], [100, 34, 11], [100, 82, 11], [144, 58, 13]].map(([x, y, r], i) => <circle key={i} className={i === 3 ? "ac" : "pp"} cx={x} cy={y} r={r} />)}
    <circle className="ac2" cx="58" cy="58" r="5" />
    <circle className="ac a-travel" r="3.6" cx="0" cy="0" style={{ ...path("M58 58 100 34 144 58"), ...delay(0) }} />
    <circle className="ac2 a-travel" r="3.6" cx="0" cy="0" style={{ ...path("M58 58 100 82 144 58"), ...delay(1.8) }} />
  </>,
  travel: <>
    <path className="il" d="M36 92C64 28 136 28 164 92" strokeDasharray="3 6" />
    <circle className="pp" cx="36" cy="92" r="6" /><circle className="ac" cx="164" cy="92" r="6" />
    <g className="a-travel" style={{ ...path("M36 92C64 28 136 28 164 92"), animationDuration: "6s" }}>
      <path className="ac" d="M-22 0 10 -5 22 0 10 5zM-6 0-17-17 2-17 12 0zM-6 0-17 17 2 17 12 0z" />
    </g>
    <path className="is a-drift" d="M52 40a9 9 0 0 1 17-3 8 8 0 0 1 11 8H52z" style={delay(0)} />
    <path className="is a-drift" d="M124 46a8 8 0 0 1 15-3 7 7 0 0 1 9 7h-24z" style={delay(3)} />
  </>,
  home: <>
    <path className="ac" d="M42 58 100 16l58 42z" style={{ strokeLinejoin: "round" }} />
    <rect className="pp" x="54" y="56" width="92" height="42" rx="6" />
    <rect className="ac2 a-flicker" x="64" y="64" width="20" height="18" rx="4" /><rect className="ac2 a-flicker" x="116" y="64" width="20" height="18" rx="4" style={delay(1.4)} />
    <rect className="is" x="91" y="68" width="18" height="30" rx="5" />
    <rect className="pp" x="126" y="18" width="10" height="20" rx="2" />
    <circle className="is a-smoke" cx="131" cy="14" r="4" style={delay(0)} /><circle className="is a-smoke" cx="131" cy="14" r="4" style={delay(1.8)} />
  </>,
  wellbeing: <>
    {[0, 1, 2].map(i => <circle key={i} cx="100" cy="62" r="30" fill="none" stroke="var(--art)" strokeWidth="1.6" className="a-ripple" style={delay(i * 1.6)} />)}
    <g className="a-breathe">
      <path className="as" d="M100 90C74 82 68 58 82 40c10 8 14 20 18 34 4-14 8-26 18-34 14 18 8 42-18 50z" style={{ transform: "scale(1.16)" }} />
      <path className="ac" d="M100 88C84 80 82 62 100 38c18 24 16 42 0 50z" />
      <path className="ac2" d="M100 88C80 86 66 70 70 52c14 4 26 16 30 36zM100 88c20-2 34-18 30-36-14 4-26 16-30 36z" style={{ opacity: .85 }} />
    </g>
  </>,
  "personal-admin": <>
    <g className="a-float">
      <rect className="pp" x="52" y="18" width="96" height="82" rx="15" />
      {[0, 1, 2].map(i => <g key={i}>
        <circle className="is" cx="72" cy={42 + i * 22} r="8" />
        <path className="al a-tick" pathLength={1} d={`m67.5 ${42 + i * 22} 3.5 3.5 6.5-7.5`} style={delay(i * .7)} />
        <rect className="is" x="90" y={39 + i * 22} width={44 - i * 8} height="6" rx="3" />
      </g>)}
    </g>
  </>,
};

const FALLBACK = <>
  <ellipse className="il" cx="100" cy="60" rx="52" ry="18" transform="rotate(-22 100 60)" />
  <circle className="pp" cx="100" cy="60" r="26" /><circle className="ac" cx="100" cy="60" r="10" />
  <circle className="ac2 a-orbit" r="5" cx="0" cy="0" style={path("M48 60a52 18 0 1 0 104 0a52 18 0 1 0 -104 0")} />
</>;

export function CrewArt({ template, className }: { template: string; className?: string }) {
  return <svg className={"crew-art-svg " + (className ?? "")} viewBox="0 0 200 116" fill="none" aria-hidden="true" focusable="false">{ART[template] ?? FALLBACK}</svg>;
}
