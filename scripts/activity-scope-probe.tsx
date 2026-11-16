// Development-only mounting point for the real conversation activity component.
import React from 'react';
import {createRoot, type Root} from 'react-dom/client';
import {ConversationActivities} from '../src/components/ConversationActivity';

let root: Root | undefined;
export function mountActivity(conversationId?:string,workspace=false) {
  if(!root) {
    const host=document.createElement('div');host.id='activity-scope-probe';document.body.append(host);
    document.getElementById('root')!.style.display='none';root=createRoot(host);
  }
  root.render(<ConversationActivities conversationId={conversationId} workspace={workspace}/>);
}
