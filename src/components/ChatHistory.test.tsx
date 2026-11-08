import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ChatDialog, ChatHistory } from "./ChatHistory";

const chat={id:"chat",title:"My conversation",messages:[],created_at:"now",updated_at:"now"};
const noop=()=>{};
describe("chat history controls",()=>{
  it("shows New chat, history, selection and compact rename/delete controls",()=>{
    const html=renderToStaticMarkup(<ChatHistory chats={[chat]} activeId="chat" pending={{}} disabled={false} onNew={noop} onSelect={noop} onRename={noop} onDelete={noop}/>);
    expect(html).toContain("New chat");expect(html).toContain("My conversation");
    expect(html).toContain('aria-current="page"');expect(html).toContain('aria-label="Rename My conversation"');
    expect(html).toContain('aria-label="Delete My conversation"');expect(html).not.toMatch(/<select|AIRouter|Minimax/);
  });
  it("shows a per-chat loading indicator and disables deletion while sending",()=>{
    const html=renderToStaticMarkup(<ChatHistory chats={[chat]} activeId="chat" pending={{chat:"Hello"}} disabled={false} onNew={noop} onSelect={noop} onRename={noop} onDelete={noop}/>);
    expect(html).toContain("animate-spin");
    expect(html).toMatch(/aria-label="Delete My conversation" disabled/);
  });
  it("requires explicit deletion and explains that Crew are preserved",()=>{
    const html=renderToStaticMarkup(<ChatDialog mode="delete" chat={chat} title={chat.title} busy={false} error={null} onTitle={noop} onCancel={noop} onConfirm={noop}/>);
    expect(html).toContain('role="dialog"');expect(html).toContain("cannot be undone");
    expect(html).toContain("Crew and their permissions will not be deleted");expect(html).toContain("Cancel");
  });
});
