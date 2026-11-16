import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { TypedText } from "./TypedText";

it("a reply already there shows whole; a new one starts typing, readable in full to a screen reader", () => {
  expect(renderToStaticMarkup(<TypedText text="I've drafted an inbox watcher." animate={false} />)).toBe("I&#x27;ve drafted an inbox watcher.");
  const typing = renderToStaticMarkup(<TypedText text="I've drafted an inbox watcher." animate />);
  expect(typing).toContain('class="type-caret"');
  expect(typing).toContain('<span class="sr-only">I&#x27;ve drafted an inbox watcher.</span>');
});
