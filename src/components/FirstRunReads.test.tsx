import {renderToStaticMarkup} from 'react-dom/server';
import {expect,it} from 'vitest';
import {FirstRunReads} from './FirstRunReads';

it('only shows resource reads backed by explicit receipts',()=>{
  expect(renderToStaticMarkup(<FirstRunReads result={{status:'passed',message:'Nothing new'}}/>)).toBe('');
  const html=renderToStaticMarkup(<FirstRunReads result={{status:'passed',message:'Checked',resource_checks:{reader:[{resource:'gmail',item_count:12,supplied_item_count:10,message_bodies_read:3}]}}}/>);
  expect(html).toContain('Looked at Gmail (12 emails).');
});

it('does not label a failed test or malformed resource receipt as verified',()=>{
  expect(renderToStaticMarkup(<FirstRunReads result={{status:'failed',message:'Blocked',resource_checks:{reader:[{resource:'gmail',item_count:2}]}}}/>)).toBe('');
  expect(renderToStaticMarkup(<FirstRunReads result={{status:'passed',message:'Checked',resource_checks:{reader:null}} as never}/>)).toBe('');
});
