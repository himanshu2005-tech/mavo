export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="brand" aria-label="Mavo">
      <span className="brand-tile" aria-hidden="true"><svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M4.5 19V6.2c0-.5.6-.7.9-.3L12 14.4l6.6-8.5c.3-.4.9-.2.9.3V19" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" /></svg></span>
      {!compact && <span className="brand-word">Mavo</span>}
    </div>
  );
}
