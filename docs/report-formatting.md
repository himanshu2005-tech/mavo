# Crew report formatting

Every shared `Report` view uses LaTeX-style document typography on Mavo's existing
dark surface. This includes saved Crew reports, work results and expanded desktop
updates. Formatting happens locally; it does not need model calls, change source
evidence, rewrite stored reports or change grants.

- Legacy `<br>`, `<br/>`, escaped `\<br>` and `&lt;br&gt;` separators become real
  line breaks. Other source HTML is never inserted into the page.
- Supported LaTeX prose: section/subsection headings, bold and italic text,
  itemize/enumerate lists, `\href` and `\url`, and escaped special characters.
  Code spans/fences and existing math/table environments are protected from prose
  conversion. This is a limited display adapter, not a full TeX interpreter.
- Equations continue to use KaTeX with `trust: false`. News prose is not treated
  as an equation, so it wraps and scrolls normally.
- Formatting is automatic: no LaTeX label, formatting toolbar or export button
  appears above reports. The internal document-export utility remains tested but
  is not exposed in the report UI. No TeX compiler is installed or run by Mavo;
  complex layouts and arbitrary LaTeX packages are unsupported.
- Explicit HTTP(S) source links open in the default browser only after a click.
  Native validation rejects executable/file schemes, credentials, local targets
  and nonstandard ports. It does not grant any browser action to a Crew or make
  retrieved instructions trusted. Source opening is currently Windows-only.

The existing full-screen/restore and accessible scroll region remain available on
desktop updates. Closing or reading a report does not claim that its facts have
been independently verified.

## Verification

`src/components/Report.test.tsx` covers the seven-item broken news example,
LaTeX prose/math/table rendering, escaped source markup, exports and link policy.
Rust report URL tests validate native source opening without launching a browser.
`scripts/check-desktop-alerts.py --base-url http://127.0.0.1:1432` exercises the real
React overlay in Chromium with API, clipboard and native IPC fixtures: eight
items, document fonts, absence of formatting controls, click-only source links, keyboard scrolling,
full-screen restoration and queued update isolation. It uses no model credits,
real account. Requires a Vite server on that port.
