import { expect, it } from 'vitest';
import { firstRunLabel, firstRunLine, firstRunResult, setupBlockers } from './firstRun';

it('keeps failed and interrupted tests as retryable draft states', () => {
  expect(firstRunLabel()).toBe('Test it');
  expect(firstRunLabel({status:'running',message:''})).toContain('Testing');
  expect(firstRunLabel({status:'failed',message:'Missing access'})).toBe('Test again');
  expect(firstRunLabel({status:'interrupted',message:''})).toBe('Test again');
});

it('shows actionable missing settings instead of hiding them behind the verification gate',()=>{
  expect(setupBlockers(['Complete first-run verification before adding this Crew','Allow calendar.create','Allow tasks.create'])).toEqual(['Allow calendar.create','Allow tasks.create']);
  expect(firstRunLabel({status:'passed',kind:'action_preview',message:'Preview',confirmed:false})).toBe('Turn it on');
});

it('any passed test leads to one next step: turn it on', () => {
  expect(firstRunLabel({status:'passed',kind:'schedule_validation',message:''})).toBe('Turn it on');
  expect(firstRunLabel({status:'passed',kind:'read_only_execution',message:''})).toBe('Turn it on');
  expect(firstRunResult(null)).toBeUndefined();
  expect(firstRunResult({})).toBeUndefined();
  expect(firstRunResult({first_run:{status:'failed',message:'Denied'}})?.message).toBe('Denied');
});

it('a running test is just "Testing…", whatever it is doing behind it', () => {
  expect(firstRunLine({status:'running',message:'',phase:'starting'})).toBe('Testing…');
  expect(firstRunLine({status:'running',message:'Writing a sample email to test it with…',phase:'writing_sample'})).toBe('Testing…');
  expect(firstRunLine({status:'running',message:'',tested_with:'sample'})).toBe('Testing…');
});

it('says how far a workflow test has got', () => {
  expect(firstRunLine({status:'running',message:'',kind:'workflow',done:3,total:12})).toBe('Testing… 3 of 12');
});

it('says when Core is fixing what failed, and the tests that run again', () => {
  expect(firstRunLine({status:'running',message:'',kind:'workflow',phase:'fixing',round:1,rounds:2})).toBe('Fixing what failed, round 1 of 2…');
  expect(firstRunLine({status:'running',message:'',kind:'workflow',phase:'testing',round:1,rounds:2,done:4,total:12})).toBe('Testing again… 4 of 12');
});
