import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { GraphConnections } from "./GraphConnections";
import type { GraphNode } from "./AgentGraph";

const callbacks = { onSelect:()=>undefined, hoverable:()=>({}), onAnchors:()=>undefined };

it("shows honest empty states for the three groups inside one box", () => {
  const html = renderToStaticMarkup(<GraphConnections models={[]} nodes={[]} width={228} selection={null} lit={null} {...callbacks}/>);
  expect(html.match(/class="graph-connections"/g)).toHaveLength(1);
  expect(html.match(/class="connection-group"/g)).toHaveLength(3);
  for (const text of ['No saved model routes.','No connected resources.','No enabled actions.']) expect(html).toContain(text);
  expect(html).not.toContain('connection-row');
});

it("keeps denied entries inspectable and does not imply granted access", () => {
  const nodes = [{key:'send',kind:'action',label:'Send mail',icon:'mail',allowed:false,users:['crew-a']}] as GraphNode[];
  const html = renderToStaticMarkup(<GraphConnections models={[]} nodes={nodes} width={228}
    selection={{kind:'action',key:'send'}} lit={{agents:new Set(['crew-a']),keys:new Set(['send'])}} {...callbacks}/>);
  expect(html).toContain('data-allowed="false"');
  expect(html).toContain('aria-pressed="true"');
  expect(html).toContain('Needs access');
  expect(html).toContain('data-connection-key="send"');
  expect(html).not.toContain('disabled');
});

it('shows pending users even when another agent has already authorized the shared resource',()=>{
  const nodes:GraphNode[]=[{key:'google.gmail',kind:'resource',label:'Gmail',icon:'mail',allowed:true,
    users:['allowed','pending'],pendingUsers:['pending']}];
  const html=renderToStaticMarkup(<GraphConnections models={[]} nodes={nodes} width={228}
    selection={null} lit={null} {...callbacks}/>);
  expect(html).toContain('1 pending');
  expect(html).toContain('1 need access');
});

it('shows link counts and different endpoint shapes without implying unused resources are assigned',()=>{
  const html=renderToStaticMarkup(<GraphConnections models={[
    {key:'route:main',route:{id:'main',name:'Main',provider:'airouter',model:'fixture'},users:['a','b']},
  ]} nodes={[
    {key:'weather',kind:'resource',label:'Weather',icon:'cloud',allowed:true,users:[]},
    {key:'notify',kind:'action',label:'Desktop alerts',icon:'bell',allowed:true,users:['a']},
  ]} width={228} selection={null} lit={null} {...callbacks}/>);
  expect(html).toContain('class="connection-port" data-kind="route" data-linked="true" title="2 linked Crew"');
  expect(html).toContain('class="connection-port" data-kind="resource" data-linked="false" title="0 linked Crew"');
  expect(html).toContain('class="connection-port" data-kind="action" data-linked="true" title="1 linked Crew"');
  expect(html.match(/class="connection-line-key"/g)).toHaveLength(3);
  expect(html).toContain('Unassigned connections have no lines.');
});
