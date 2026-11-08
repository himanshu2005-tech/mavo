import type { CSSProperties } from "react";

export interface IdentityColor { hue: number; dark: string; light: string }
export type IdentityPalette = Record<string, IdentityColor>;
const STORAGE = "mavo-identity-colors-v1";
const hexPattern = /^#[0-9a-f]{6}$/;

function hash(key: string): number {
  let value = 2166136261;
  for (const ch of key) value = Math.imul(value ^ ch.charCodeAt(0),16777619);
  return value >>> 0;
}
function mix(value: number): number {
  value = Math.imul(value ^ value >>> 16,0x21f0aaad);
  value = Math.imul(value ^ value >>> 15,0x735a2d97);
  return (value ^ value >>> 15) >>> 0;
}
function rgb(hex: string): number[] { return [1,3,5].map(i => parseInt(hex.slice(i,i+2),16)/255); }
function linear(channel: number): number { return channel<=.04045?channel/12.92:((channel+.055)/1.055)**2.4; }
function luminance(hex: string): number {
  const [r,g,b]=rgb(hex).map(linear); return .2126*r+.7152*g+.0722*b;
}
export function contrast(a: string,b: string): number {
  const x=luminance(a),y=luminance(b); return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);
}
function hsl(h: number,s: number,l: number): string {
  s/=100; l/=100;
  const a=s*Math.min(l,1-l);
  const channel=(n:number) => {
    const k=(n+h/30)%12;
    return Math.round(255*(l-a*Math.max(-1,Math.min(k-3,9-k,1)))).toString(16).padStart(2,"0");
  };
  return `#${channel(0)}${channel(8)}${channel(4)}`;
}
/** OKLab distance separates identities by appearance, not just an integer hue. */
function lab(hex: string): number[] {
  const [r,g,b]=rgb(hex).map(linear);
  const l=Math.cbrt(.4122214708*r+.5363325363*g+.0514459929*b);
  const m=Math.cbrt(.2119034982*r+.6806995451*g+.1073969566*b);
  const s=Math.cbrt(.0883024619*r+.2817188376*g+.6299787005*b);
  return [.2104542553*l+.793617785*m-.0040720468*s,
    1.9779984951*l-2.428592205*m+.4505937099*s,
    .0259040371*l+.7827717662*m-.808675766*s];
}
function distance(a: number[],b: number[]): number { return a.reduce((sum,v,i)=>sum+(v-b[i])**2,0); }

/** Preserve existing assignments, checking actual RGB values in BOTH themes for collisions. */
export function allocateIdentityColors(keys: string[], previous: IdentityPalette = {}): IdentityPalette {
  const palette: IdentityPalette = {};
  const darkUsed=new Set<string>(),lightUsed=new Set<string>();
  const used: {dark:number[];light:number[]}[]=[];
  const keep=(key:string,color:IdentityColor) => {
    palette[key]=color; darkUsed.add(color.dark); lightUsed.add(color.light);
    used.push({dark:lab(color.dark),light:lab(color.light)});
  };
  for (const [key,color] of Object.entries(previous)) {
    if (/^(crew|route|resource|action):/.test(key) && color && Number.isFinite(color.hue) && color.hue>=0 && color.hue<360
      && hexPattern.test(color.dark) && hexPattern.test(color.light)
      && !darkUsed.has(color.dark) && !lightUsed.has(color.light)
      && contrast(color.dark,"#181818")>=3 && contrast(color.light,"#f5f5f5")>=3) keep(key,color);
  }
  for (const key of [...new Set(keys)].sort()) {
    if (palette[key]) continue;
    const seed=hash(key);
    let best:IdentityColor|undefined,bestScore=-1;
    for (let i=0;i<256;i++) {
      const value=mix(seed+Math.imul(i+1,0x9e3779b9));
      const hue=(value%3600)/10,sat=52+(value>>>12)%34;
      const candidate={hue,dark:hsl(hue,sat,60+(value>>>18)%18),light:hsl(hue,sat,33+(value>>>24)%15)};
      if (darkUsed.has(candidate.dark)||lightUsed.has(candidate.light)
        ||contrast(candidate.dark,"#181818")<3||contrast(candidate.light,"#f5f5f5")<3) continue;
      const dark=lab(candidate.dark),light=lab(candidate.light);
      const score=used.length?used.reduce((minimum,c)=>Math.min(minimum,distance(dark,c.dark),distance(light,c.light)),Infinity):1;
      if (score>bestScore) { best=candidate; bestScore=score; }
    }
    // Finite candidate pools must not introduce duplicates in a very large saved palette.
    if (!best) {
      for (let i=0;!best&&i<0x1000000;i++) {
        const dark=`#${((seed+i*104729)&0xffffff).toString(16).padStart(6,"0")}`;
        const light=`#${((seed+i*13007+0x315a37)&0xffffff).toString(16).padStart(6,"0")}`;
        if (!darkUsed.has(dark)&&!lightUsed.has(light)&&contrast(dark,"#181818")>=3&&contrast(light,"#f5f5f5")>=3)
          best={hue:seed%360,dark,light};
      }
    }
    if (!best) throw new Error("No unused accessible identity colors remain.");
    keep(key,best);
  }
  return palette;
}

let palette: IdentityPalette | undefined;
let warned=false;
let listening=false;
function preferenceWarning() {
  if (!warned) { warned=true; console.warn("Mavo could not store color preferences. Identity colors will remain available for this session."); }
}
function current(): IdentityPalette {
  if (!palette) {
    palette={};
    if (typeof window!=="undefined") {
      try {
        const saved=JSON.parse(window.localStorage.getItem(STORAGE)??"null");
        if (saved?.version===1&&saved.colors&&typeof saved.colors==="object") palette=allocateIdentityColors([],saved.colors);
      } catch { preferenceWarning(); }
      if (!listening) {
        listening=true;
        window.addEventListener("storage",event => {
          if (event.key!==STORAGE) return;
          try {
            const saved=JSON.parse(event.newValue??"null");
            if (saved?.version===1&&saved.colors&&typeof saved.colors==="object")
              palette=allocateIdentityColors(Object.keys(palette??{}),saved.colors);
            else if (!event.newValue) palette={};
          } catch { preferenceWarning(); }
        });
      }
    }
  }
  return palette;
}
/** An idempotent UI cache: no model calls, permissions or application records are changed. */
export function reserveIdentityColors(keys: string[]): IdentityPalette {
  const existing=current();
  if (keys.some(key=>!existing[key])) palette=allocateIdentityColors(keys,existing);
  return palette!;
}
export function identityColor(key: string): IdentityColor { return reserveIdentityColors([key])[key]; }
export function persistIdentityColors(): void {
  if (typeof window==="undefined") return;
  try { window.localStorage.setItem(STORAGE,JSON.stringify({version:1,colors:current()})); }
  catch { preferenceWarning(); }
}
export function identityStyle(color: IdentityColor): CSSProperties {
  return { ["--identity" as string]:`light-dark(${color.light}, ${color.dark})`,["--hue" as string]:color.hue };
}
