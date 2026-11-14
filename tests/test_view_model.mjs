import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../views/settings/model.js',import.meta.url),'utf8');
const model=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
assert.equal(model.escapeHTML('<img src=x onerror="bad()">'), '&lt;img src=x onerror=&quot;bad()&quot;&gt;');
assert.deepEqual(model.splitNames(' 英语群，学院群\n英语群 '),['英语群','学院群']);
assert.equal(model.makeSource('qq-main','GroupMessage','123'),'qq-main:GroupMessage:123');
assert.throws(()=>model.makeSource('qq-main','GroupMessage','1 OR 1=1'));
const cfg={enabled:false,audit_enabled:true,instances:[{platform_id:'qq-main',bot_id:'999'}],readers:[],aliases:[],limits:{scan:500}};
cfg.readers.push(model.entry('readers','qq-main'));cfg.readers[0].user_id='111';cfg.readers[0].groups=['qq-main:GroupMessage:123'];
assert.equal(model.instanceReferences(cfg,'qq-main'),2);
assert.deepEqual(model.validate(cfg,{scan:[1,2000]}),[]);
model.setPath(cfg,'readers.0.user_id','222');assert.equal(cfg.readers[0].user_id,'222');
assert.throws(()=>model.setPath(cfg,'__proto__.injected','bad'));
assert.throws(()=>model.setPath(cfg,'readers.0.__proto__.injected','bad'));
const cloned=model.clone(cfg);cloned.readers[0].groups.push('qq-main:GroupMessage:456');assert.equal(cfg.readers[0].groups.length,1);
cfg.readers.push(model.clone(cfg.readers[0]));assert.ok(model.validate(cfg,{scan:[1,2000]}).some(v=>v.includes('重复')));
cfg.readers.pop();cfg.limits.scan=-1;assert.ok(model.validate(cfg,{scan:[1,2000]}).length);
cfg.limits.private_recent_scan=75;
assert.deepEqual(model.validate(cfg,{private_recent_scan:[1,2000]}),[]);
for (const value of [0,2001,1.5]) {
  cfg.limits.private_recent_scan=value;
  assert.ok(model.validate(cfg,{private_recent_scan:[1,2000]}).some(issue=>issue.includes('回看条数')));
}
console.log('View model checks passed: escaping, source validation, duplicate checks, independent drafts and safe field paths.');
