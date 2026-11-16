"""How every agent writes what the person reads: a small, fixed LaTeX that the app turns into a
styled report (src/lib/reportFormat.ts and Report.tsx draw each piece). Nothing outside this set:
a model that writes it wrong still shows as clean text, never as broken markup."""

LATEX_STYLE = (
    "Write it in this small LaTeX, which Mavo turns into a styled report. The answer first, on "
    "its own line: when it is one value or status, as 'Label: \\textbf{value}' (for example "
    "'Credits left: \\textbf{\\$0.50}'). Then only what is worth knowing: \\section{...} for a "
    "heading; \\begin{itemize} \\item ... \\end{itemize} for points (\\begin{enumerate} when "
    "order matters); \\begin{tabular}{ll} a & b \\\\ ... \\end{tabular} for a table; \\textbf{} "
    "and \\emph{} for emphasis; \\href{url}{words} for a link; $...$ for maths. Write money as "
    "\\$5. No \\documentclass, packages or other commands. Leave out how you found it, that a "
    "page loaded, that nothing needs doing, and when the next check is: Mavo shows those."
)
