import {renderToStaticMarkup} from 'react-dom/server';
import {describe,it,expect,vi} from 'vitest';
import {ChatFailure} from './ChatFailure';

describe('clear chat failures',()=>{
  it('explains publisher credit failures without offering an unhelpful retry',()=>{
    const html=renderToStaticMarkup(<ChatFailure error="Old generic error" requestId="reference" onRetry={vi.fn()} onSettings={vi.fn()} problem={{code:'core_provider_credits',message:'Core has insufficient credits.',action:'The publisher must top up Core, not your Crew route.',retryable:false,request_id:null}}/>);
    expect(html).toContain('role="alert"');
    expect(html).toContain('insufficient credits');
    expect(html).toContain('not your Crew route');
    expect(html).toContain('Reference: reference');
    expect(html).not.toContain('Send again');
    expect(html).not.toContain('Old generic error');
  });
  it('offers a deliberate retry for recoverable failures',()=>{
    const html=renderToStaticMarkup(<ChatFailure error="Timeout" requestId="reference" onRetry={vi.fn()} onSettings={vi.fn()}/>);
    expect(html).toContain('Timeout');expect(html).toContain('Send again');
  });
});
