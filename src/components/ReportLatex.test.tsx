import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { Report } from "./Report";

const html = (text: string) => renderToStaticMarkup(<Report text={text} />);

it("renders an agent's LaTeX report styled: the answer big, sections, lists, a table, links", () => {
  const out = html(String.raw`Credits left: \textbf{\$0.50}
\section{Recent activity}
\begin{itemize}
\item Last top-up: Nov 14 (\$1.00)
\item Rate: \$1 = ₹94.25
\end{itemize}
\begin{tabular}{l r}
Day & Spent \\
Nov 14 & \$0.30 \\
Nov 15 & \$0.20 \\
\end{tabular}
See \href{https://airouter.in/settings/credits}{the credits page}.`);
  expect(out).toContain('<div class="report-stat"><span>Credits left</span><b>$0.50</b></div>');
  expect(out).toContain("Recent activity</h4>");
  expect(out).toContain("<li>Last top-up: Nov 14 ($1.00)</li>");
  expect(out).toContain("<th>Day</th>");
  expect(out).toContain("<td>$0.30</td>");
  expect(out).toContain("the credits page</a>");
  expect(out).not.toContain("\\textbf");
  expect(out).not.toContain("\\item");
});

it("broken LaTeX still reads as clean text", () => {
  const out = html(String.raw`Status: \textbf{All good
\begin{itemize}
\item One check \emph{passed}`);
  expect(out).not.toContain("\\begin");
  expect(out).not.toContain("\\item");
  expect(out).toContain("One check");
});
