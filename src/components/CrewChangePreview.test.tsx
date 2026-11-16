import {renderToStaticMarkup} from 'react-dom/server';
import {expect,it} from 'vitest';
import {CrewChangePreview} from './CrewChangePreview';

it('shows exact proposed calendar changes without claiming that they were executed',()=>{
  const html=renderToStaticMarkup(<CrewChangePreview result={{status:'passed',kind:'action_preview',message:'Preview',confirmed:false,proposed_actions:[{operation:'calendar.create',arguments:{summary:'Focus block',start:'2026-11-10T10:00:00+05:30',end:'2026-11-10T11:00:00+05:30'}}]}}/>);
  expect(html).toContain('Nothing was changed yet.');
  expect(html).toContain('Focus block');expect(html).toContain('Add to your calendar');
  // A person reads it: a day and a time, never an ISO timestamp or JSON.
  expect(html).not.toContain('2026-11-10T10:00:00+05:30');expect(html).not.toContain('{&quot;');
  expect(html).toContain('In the test, it would:');
});

it('does not show an activation preview for a failed test',()=>{
  expect(renderToStaticMarkup(<CrewChangePreview result={{status:'failed',message:'Missing access'}}/>)).toBe('');
});

it('shows mail targets and command/script limits without claiming real execution',()=>{
  const html=renderToStaticMarkup(<CrewChangePreview result={{status:'passed',kind:'action_preview',message:'Preview',confirmed:false,
    proposed_actions:[{operation:'gmail.mark_read',label:'Mark read',arguments:{message_id:'observed-mail'},not_verified:['execution_result']},
      {operation:'project.run',arguments:{project:'Sample',script:'check.py'},not_verified:['script_approval']}],
    unexercised_actions:['gmail.trash','windows.shutdown']}}/>);
  expect(html).toContain('Mark read');
  expect(html).toContain('asks you before running a script');
  expect(html).toContain('Also allowed to trash, shutdown.');
  expect(html).not.toContain('successfully executed');
});

it('states explicitly when the input did not exercise any action',()=>{
  const html=renderToStaticMarkup(<CrewChangePreview result={{status:'passed',kind:'action_preview',message:'Preview',proposed_actions:[],unexercised_actions:['gmail.mark_read']}}/>);
  expect(html).toContain('Nothing needed doing this time.');
  expect(html).toContain('Also allowed to mark read.');
});

it('shows an email as who it goes to, its subject and the message, never ids',()=>{
  const html=renderToStaticMarkup(<CrewChangePreview result={{status:'passed',kind:'action_preview',message:'Preview',confirmed:false,proposed_actions:[{operation:'gmail.send',arguments:{to:'hr@example.com',subject:'Re: Interview',body:'Hello,\nMonday works.',thread_id:'1a1d4bedc6a0f594',in_reply_to:'1a1d4bedc6a0f594'}}]}}/>);
  expect(html).toContain('Send this email');expect(html).toContain('hr@example.com');expect(html).toContain('Re: Interview');expect(html).toContain('Monday works.');
  expect(html).not.toContain('1a1d4bedc6a0f594');
});
