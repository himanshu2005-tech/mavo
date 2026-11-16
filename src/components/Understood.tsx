import { useState } from "react";

/** What Mavo Core understood the person to want: its brief for the crew, every rule and default
 *  it chose, shown first on a draft so a wrong guess (which days, how long) is corrected before
 *  the team is tested. Three lines, the rest on "More"; no label: it reads as the job itself. */
export function Understood({ text, onChange }: { text?: string; onChange?: () => void }) {
  const [open, setOpen] = useState(false);
  const brief = (text ?? "").trim();
  if (!brief) return null;
  const long = brief.length > 220 || brief.split("\n").length > 3;
  return <section className="understood" aria-label="What I understood">
    <p className={"understood-text" + (open || !long ? "" : " clamped")}>{brief}</p>
    <p className="understood-tools">
      {long && <button type="button" className="link-quiet" onClick={() => setOpen(!open)}>{open ? "Less" : "More"}</button>}
      {onChange && <button type="button" className="link-quiet" onClick={onChange}>Change</button>}
    </p>
  </section>;
}
