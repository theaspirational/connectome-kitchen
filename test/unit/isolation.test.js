import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {CACHE_ENV,PASS_ENV,cache_dir,remove,shared} from '../../dist/isolation.js';
// src/isolation.ts: the Bun cache of a build's own, and the count of a build's files that have a name outside it.
const scratch=path.join(path.resolve('.'),'.cache','unit-isolation-'+process.pid);
test.before(()=>fs.mkdirSync(scratch,{recursive:true}));
test.after(()=>fs.rmSync(scratch,{recursive:true,force:true}));
const file=(name,text='x')=>{fs.mkdirSync(path.dirname(name),{recursive:true});fs.writeFileSync(name,text);return name;};

test('the names are Bun\'s and cook\'s, and the cache lies beside the builds under the name of the build',()=>{
 assert.deepEqual([CACHE_ENV,PASS_ENV],['BUN_INSTALL_CACHE_DIR','COOK_PASS_ENV']);
 assert.equal(cache_dir('/home/a/root','0123'),'/home/a/root/.bun-cache-0123');
});

test('removing a cache removes a tree, a link without what it names, and is quiet where there is nothing',()=>{
 const kept=file(path.join(scratch,'kept','file')),cache=path.join(scratch,'root','.bun-cache-1');
 file(path.join(cache,'pkg@1','index.js'));
 remove(cache);
 assert.equal(fs.existsSync(cache),false);
 fs.symlinkSync(path.dirname(kept),cache);
 remove(cache);
 assert.deepEqual([fs.existsSync(cache),fs.existsSync(kept)],[false,true]);
 remove(cache);
});

test('a file with a name outside the build is counted, and one that is a file of another build is named',()=>{
 const builds=path.join(scratch,'root','builds'),own=path.join(builds,'new'),other=path.join(builds,'old'),cache=path.join(scratch,'cache');
 file(path.join(own,'alone.js'));
 fs.linkSync(file(path.join(own,'app','a.js')),path.join(own,'app','b.js'));  // two names inside: no share
 file(path.join(other,'theirs.js'));
 fs.symlinkSync(path.join(other,'theirs.js'),path.join(own,'link'));  // not followed
 assert.deepEqual(shared(builds,'new'),{files:3,outside:0,with_other_builds:0,examples:[]});
 fs.linkSync(file(path.join(cache,'pkg','index.js')),path.join(own,'app','cached.js'));  // a name in a cache that no build shares
 assert.deepEqual(shared(builds,'new'),{files:4,outside:1,with_other_builds:0,examples:[]});
 fs.linkSync(path.join(cache,'pkg','index.js'),path.join(other,'cached.js'));  // and now another build has it too
 fs.linkSync(path.join(other,'theirs.js'),path.join(own,'app','z.js'));
 assert.deepEqual(shared(builds,'new'),{files:5,outside:2,with_other_builds:2,examples:[path.join(own,'app','cached.js'),path.join(own,'app','z.js')]});
 fs.rmSync(other,{recursive:true});
 assert.deepEqual(shared(builds,'new'),{files:5,outside:1,with_other_builds:0,examples:[]});  // the cache's name is left
});
