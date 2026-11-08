import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { GraphConnections } from "./GraphConnections";
import type { GraphNode } from "./AgentGraph";

const callbacks = { onSelect:()=>undefined, hoverable:()=>({}), onAnchors:()=>undefined };

it("shows honest empty states for the three groups inside one box", () => {
  const html = renderToStaticMarkup(<GraphConnections models={[]} nodes={[]} width={228} selection={null} lit={null} {...callbacks}/>);
  expect(html.match(/class="graph-connections"/g)).toHaveLength(1);
  expect(html.match(/class="connection-group"/g)).toHaveLength(3);
  for (const text of ['No model routes in use.','No resources in use.','No actions in use.']) expect(html).toContain(text);
  expect(html).not.toContain('connection-row');
});

it("keeps denied entries inspectable and does not imply granted access", () => {
  const nodes = [{key:'send',kind:'action',label:'Send mail',icon:'mail',allowed:false,users:['crew-a']}] as GraphNode[];
  const html = renderToStaticMarkup(<GraphConnections models={[]} nodes={nodes} width={228}
    selection={{kind:'action',key:'send'}} lit={{agents:new Set(['crew-a']),keys:new Set(['send'])}} {...callbacks}/>);
  expect(html).toContain('data-allowed="false"');
  expect(html).toContain('aria-pressed="true"');
  expect(html).toContain('Not allowed');
  expect(html).toContain('data-connection-key="send"');
  expect(html).not.toContain('disabled');
});
