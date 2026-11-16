/** A deliberately small, non-executable LaTeX prose adapter. Math stays with KaTeX. */
export function normalizeReportMarkup(input: string): string {
  // A model that writes LaTeX inside JSON loses backslashes to escapes: "\textbf" arrives as a
  // tab and "extbf", "\begin" as a backspace and "egin". Put them back.
  input = input.replace(/\t(?=ext(?:bf|it|tt|sc)?\{)/g, '\\t').replace(/\x08(?=egin\{)/g, '\\b').replace(/\f(?=rac\{)/g, '\\f');
  const items = /\\item\b/.test(input);
  const lists: { ordered: boolean; count: number }[] = [];
  const latex = /\\(?:section|subsection|textbf|emph|href|begin\{(?:document|itemize|enumerate)\})/.test(input);
  function prose(value: string): string {
    value = value.replace(/\\?(?:<br\s*\/?>|&lt;br\s*\/?&gt;)/gi, '\n');
    // No general HTML parser, DOM insertion, TeX interpreter, macros, or file I/O.
    value = value.replace(/\\(?:documentclass|usepackage)(?:\[[^\]\n]*\])?\{[^}\n]*\}/g, '')
      .replace(/\\(?:begin|end)\{document\}/g, '\n')
      .replace(/[ \t]*\\(begin|end)\{(itemize|enumerate)\}|[ \t]*\\item(?=\s|\\|$)/g, (_, boundary: string | undefined, kind: string) => {
        if (boundary === 'begin') { lists.push({ordered: kind === 'enumerate', count: 0}); return '\n'; }
        if (boundary === 'end') { lists.pop(); return '\n'; }
        const list = lists[lists.length - 1];
        return `\n${list?.ordered ? ++list.count + '.' : '-'} `;
      });
    const formatted = inlineProse(value).replace(/\\par\b/g, '\n\n');
    return latex ? formatted.replace(/\\\\(?:\[[^\]\n]*\])?[ \t]*\n?/g, '\n') : formatted;
  }
  // Protect code, equations and table environments before interpreting prose commands.
  const protectedPart = /```[\s\S]*?(?:```|$)|`[^`\n]*`|\$\$[\s\S]*?(?:\$\$|$)|\\\[[\s\S]*?(?:\\\]|$)|\\\([\s\S]*?\\\)|\$(?!\s)[^$\n]*?[\\^_{][^$\n]*?\$|\\begin\{(equation|align|gather|multline|tabular|array|matrix|pmatrix|bmatrix|cases)\*?\}[\s\S]*?\\end\{\1\*?\}/g;
  let result = '', at = 0;
  for (const match of input.matchAll(protectedPart)) {
    result += prose(input.slice(at, match.index)) + match[0];
    at = match.index! + match[0].length;
  }
  result += prose(input.slice(at));
  if (!items) return result;
  // The lines written under an \item belong to it: indented, so they stay in its list item
  // (each "From: / Subject: / Date:" ran together into one paragraph).
  let inItem = false;
  return result.split('\n').map(line => {
    if (/^\s*(?:-|\d+\.)\s/.test(line)) { inItem = true; return line; }
    if (!line.trim() || line.startsWith('#')) { inItem = false; return line; }
    return inItem && !/^\s/.test(line) ? '  ' + line : line;
  }).join('\n');
}

function group(text: string, start: number): { value: string; end: number } | null {
  if (text[start] !== '{') return null;
  let depth = 1;
  for (let i = start + 1; i < text.length; i++) {
    if (text[i] === '\\') { i++; continue; }
    if (text[i] === '{') depth++;
    if (text[i] === '}' && --depth === 0) return {value: text.slice(start + 1, i), end: i + 1};
  }
  return null;
}

function inlineProse(text: string, depth = 0): string {
  if (depth > 12) return text;
  let result = '', i = 0;
  while (i < text.length) {
    const command = /^\\(section|subsection|subsubsection|paragraph|textbf|textit|emph|texttt|href|url)\*?/.exec(text.slice(i));
    if (command) {
      const first = group(text, i + command[0].length);
      if (first) {
        const name = command[1], content = inlineProse(first.value, depth + 1);
        if (name === 'href') {
          const second = group(text, first.end);
          if (second) { result += `[${inlineProse(second.value, depth + 1)}](${first.value.replace(/\\([%&#_])/g, '$1')})`; i = second.end; continue; }
        } else {
          if (name === 'url') result += `[${content}](${content})`;
          else if (name === 'textbf') result += `**${content}**`;
          else if (name === 'textit' || name === 'emph') result += `*${content}*`;
          else if (name === 'texttt') result += '`' + content + '`';
          else result += `\n\n${'#'.repeat(name === 'section' ? 1 : name === 'subsection' ? 2 : 3)} ${content}\n\n`;
          i = first.end; continue;
        }
      }
    }
    if (text[i] === '\\' && /[&%_#$\{\}]/.test(text[i + 1] ?? '')) { result += text[i + 1]; i += 2; continue; }
    result += text[i++];
  }
  return result;
}

/** Escape prose, including executable-looking TeX, when exporting a report. */
export function escapeLatex(text: string): string {
  const escapes: Record<string, string> = {
    '\\': '\\textbackslash{}', '{': '\\{', '}': '\\}', '&': '\\&', '%': '\\%',
    '$': '\\$', '#': '\\#', '_': '\\_', '~': '\\textasciitilde{}', '^': '\\textasciicircum{}',
  };
  return text.replace(/[\\{}&%$#_~^]/g, char => escapes[char]);
}

const HIDDEN_KEY = /(^|_)(id|ids|etag|thread_id|in_reply_to|message_id)$/i;

/** Where a JSON array or object that opens `text` ends, or -1. Strings are skipped whole. */
function jsonEnd(text: string): number {
  let depth = 0, quoted = false, escaped = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) { if (escaped) escaped = false; else if (c === "\\") escaped = true; else if (c === '"') quoted = false; continue; }
    if (c === '"') quoted = true;
    else if (c === "[" || c === "{") depth++;
    else if (c === "]" || c === "}") { depth--; if (depth === 0) return i + 1; }
  }
  return -1;
}

/** Line breaks written inside JSON strings (an agent's raw output) escaped, so it parses. */
function breaksEscaped(raw: string): string {
  let out = "", quoted = false, escaped = false;
  for (const c of raw) {
    if (quoted && !escaped && (c === "\n" || c === "\r")) { out += c === "\n" ? "\\n" : ""; continue; }
    if (quoted) { if (escaped) escaped = false; else if (c === "\\") escaped = true; else if (c === '"') quoted = false; }
    else if (c === '"') quoted = true;
    out += c;
  }
  return out;
}

function label(key: string): string { const words = key.replace(/_/g, " ").trim(); return words.charAt(0).toUpperCase() + words.slice(1); }

/** A report that opens with JSON (an agent's details for an action) as a person reads it: each
 *  item as labelled lines ("To:", "Subject:", the message under it), ids left out. Four replies
 *  were shown as raw JSON above "Done: Create event". Anything after the JSON is kept. */
export function jsonAsReadable(text: string): string {
  const start = text.search(/\S/);
  if (start < 0 || !"[{".includes(text[start])) return text;
  const end = jsonEnd(text.slice(start));
  if (end < 0) return text;
  let value: unknown;
  const raw = text.slice(start, start + end);
  try { value = JSON.parse(raw); } catch {
    try { value = JSON.parse(breaksEscaped(raw)); } catch { return text; }
  }
  const rows = Array.isArray(value) ? value : [value];
  const blocks = rows.map(row => {
    if (!row || typeof row !== "object") return String(row ?? "");
    return Object.entries(row as Record<string, unknown>)
      .filter(([key, v]) => !HIDDEN_KEY.test(key) && v !== null && v !== "" && !(Array.isArray(v) && !v.length))
      .map(([key, v]) => {
        const shown = typeof v === "string" ? v.trim() : JSON.stringify(v);
        return shown.includes("\n") ? `**${label(key)}:**\n${shown}` : `**${label(key)}:** ${shown}`;
      }).join("\n");
  }).filter(Boolean);
  const rest = text.slice(start + end).trim();
  return [rows.length ? blocks.join("\n\n") : "Nothing to report.", rest].filter(Boolean).join("\n\n");
}
