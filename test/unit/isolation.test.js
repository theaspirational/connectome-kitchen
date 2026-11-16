import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {CACHE_ENV,PASS_ENV,cache_dir,git_environment,git_misdirected,remove,shared,sweep} from '../../dist/isolation.js';
import {local_sources,refers_to_recipes} from '../../dist/cook.js';
import {loads} from '../../dist/pylike/pyjson.js';
// src/isolation.ts: the Bun cache of a build's own, and the count of a build's files that have a name outside it.
const scratch=path.join(path.resolve('.'),'.cache','unit-isolation-'+process.pid);
test.before(()=>fs.mkdirSync(scratch,{recursive:true}));
test.after(()=>fs.rmSync(scratch,{recursive:true,force:true}));
const file=(name,text='x')=>{fs.mkdirSync(path.dirname(name),{recursive:true});fs.writeFileSync(name,text);return name;};

test('the names are Bun\'s and cook\'s, and the cache lies beside the builds under the name of the build',()=>{
 assert.deepEqual([CACHE_ENV,PASS_ENV],['BUN_INSTALL_CACHE_DIR','COOK_PASS_ENV']);
 assert.equal(cache_dir('/deployment/root','0123'),'/deployment/root/.bun-cache-0123');
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

test('a sweep removes the cache of every request under the root and nothing else',()=>{
 const root=path.join(scratch,'swept');
 for(const name of ['.bun-cache-own','.bun-cache-'+'f'.repeat(64)])file(path.join(root,name,'pkg@1','index.js'));
 const kept=file(path.join(root,'builds','old','.bun-cache-inside')),beside=file(path.join(root,'bun-cache-not-ours'));
 fs.symlinkSync(path.join(root,'builds'),path.join(root,'.bun-cache-link'));  // removed, not followed
 assert.deepEqual(sweep(root,'own'),['.bun-cache-'+'f'.repeat(64),'.bun-cache-link','.bun-cache-own']);
 assert.deepEqual(fs.readdirSync(root).sort(),['builds','bun-cache-not-ours']);
 assert.deepEqual([fs.existsSync(kept),fs.existsSync(beside)],[true,true]);
 assert.deepEqual(sweep(root,'own'),[]);
});

test('the source repositories a recipe names by a local path, and no other',()=>{
 const recipe=loads(JSON.stringify({mcpServers:{a:{source:{url:'/repos/b',ref:'main'}},b:{source:{url:'https://example.invalid/x.git'}},c:{source:{external:true,url:'/repos/external'}},
  d:{source:{url:'/repos/a'}},e:{url:'/not/a/source'},f:{source:'/text'}},extensions:[{source:{url:'/repos/a'}},{nested:{source:{url:'/repos/c'}}}]}));
 assert.deepEqual(local_sources(recipe),['/repos/a','/repos/b','/repos/c']);
 assert.deepEqual(local_sources(loads('{"mcpServers":{}}')),[]);
 assert.deepEqual(local_sources(null),[]);
});

test('Git is told to take a local repository over its transport: counted entries, none for no path',()=>{
 assert.deepEqual(git_environment([]),{});
 assert.deepEqual(git_environment(['https://example.invalid/x.git','relative','/with\nbreak','/with\rreturn','/with\0nul']),{});
 assert.deepEqual(git_environment(['/repos/b','/repos/a','/repos/b','/with\nbreak']),{GIT_CONFIG_COUNT:'2',
  GIT_CONFIG_KEY_0:'url.file:///repos/a.insteadOf',GIT_CONFIG_VALUE_0:'/repos/a',GIT_CONFIG_KEY_1:'url.file:///repos/b.insteadOf',GIT_CONFIG_VALUE_1:'/repos/b'});
});

test('a % of a path is written %25 in the URL, which Git decodes, and the path that Git is to match stays as it is',()=>{
 assert.deepEqual(git_environment(['/repos/a%20b%']),{GIT_CONFIG_COUNT:'1',GIT_CONFIG_KEY_0:'url.file:///repos/a%2520b%25.insteadOf',GIT_CONFIG_VALUE_0:'/repos/a%20b%'});
});

test('a repository without an entry that a rewrite would send elsewhere is named: a rewritten beginning and a % after it',()=>{
 assert.deepEqual(git_misdirected([],['/repos/a%20b']),[]);
 assert.deepEqual(git_misdirected(['/repos/a','/repos/a%20b'],['/repos/a','/repos/a%20b']),[]);  // each has an entry of its own
 assert.deepEqual(git_misdirected(['/repos/a'],['/repos/a%20b','/repos/a-b','/repos/a/b','/repos/b%20','/other%20','https://example.invalid/repos/a%20','relative%20','/repos/a%20b']),['/repos/a%20b']);
 assert.deepEqual(git_misdirected(['/repos/a','/repos/a%20b'],['/repos/a%20bc','/repos/a%20b%41','/repos/a%20b\n%41']),['/repos/a%20b\n%41','/repos/a%20b%41']);  // the longest beginning is the one Git takes
 assert.deepEqual(git_misdirected(['/repos/a\nb'],['/repos/a\nb%20']),[]);  // a path with a line break has no rewrite
});

test('a recipe that refers to further recipes is known as one',()=>{
 for(const text of ['{}','{"modules":{}}','{"modules":{"fleet":{}}}','{"modules":{"fleet":{"children":[]}}}','{"modules":{"fleet":true}}','{"fleet":{"children":[{"recipe":"x"}]}}','null'])assert.equal(refers_to_recipes(loads(text)),false,text);
 for(const text of ['{"modules":{"fleet":{"children":[{"name":"a","recipe":"child.json"}]}}}','{"modules":{"fleet":{"children":{}}}}','{"modules":{"fleet":{"children":null}}}'])assert.equal(refers_to_recipes(loads(text)),true,text);
});

test('with that environment a clone of a local repository shares no object file with it, and names the path as its origin',{skip:spawnSync('git',['--version']).status!==0},()=>{
 const home=path.join(scratch,'git-home'),source=path.join(scratch,'git source'),base={HOME:home,PATH:process.env.PATH,LANG:'C.UTF-8',GIT_CONFIG_NOSYSTEM:'1'};
 fs.mkdirSync(home,{recursive:true});
 const git=(args,env=base)=>{const done=spawnSync('git',args,{env,encoding:'utf8'});assert.equal(done.status,0,done.stderr);return done.stdout.trim();};
 git(['init','-q',source]);
 file(path.join(source,'fixture.txt'),'public fixture\n');
 git(['-C',source,'add','.']);
 git(['-C',source,'-c','user.name=Public Fixture','-c','user.email=fixture@example.invalid','commit','-q','-m','fixture']);
 const linked=directory=>fs.readdirSync(path.join(directory,'.git','objects'),{recursive:true,withFileTypes:true}).filter(entry=>entry.isFile()&&fs.statSync(path.join(entry.parentPath,entry.name)).nlink>1).length;
 const plain=path.join(scratch,'clone-plain'),own=path.join(scratch,'clone-own');
 git(['clone','-q',source,plain]);
 assert.ok(linked(plain)>0);  // the premise: Git links the objects of a repository at a local path
 git(['clone','-q',source,own],{...base,...git_environment([source])});
 assert.equal(linked(own),0);
 assert.equal(linked(source),linked(plain));  // and the repository's own files got no further name
 assert.equal(git(['-C',own,'config','remote.origin.url']),source);
 assert.equal(fs.readFileSync(path.join(own,'fixture.txt'),'utf8'),'public fixture\n');
 assert.equal(git(['-C',own,'rev-parse','HEAD']),git(['-C',source,'rev-parse','HEAD']));
});

test('a repository whose path begins with another one is cloned from itself, also with a % that Git would decode',{skip:spawnSync('git',['--version']).status!==0},()=>{
 // Git holds `insteadOf` against the beginning of a URL: the rewrite of `repo` alone sends `repo%20other` to `repo other`.
 const home=path.join(scratch,'prefix-home'),base={HOME:home,PATH:process.env.PATH,LANG:'C.UTF-8',GIT_CONFIG_NOSYSTEM:'1'};
 fs.mkdirSync(home,{recursive:true});
 const git=(args,env=base)=>{const done=spawnSync('git',args,{env,encoding:'utf8'});assert.equal(done.status,0,done.stderr);return done.stdout.trim();};
 const make=(name,word)=>{const source=path.join(scratch,'prefix',name);git(['init','-q','-b','main',source]);file(path.join(source,'fixture.txt'),word+'\n');
  git(['-C',source,'add','.']);git(['-C',source,'-c','user.name=Public Fixture','-c','user.email=fixture@example.invalid','commit','-q','-m',word]);return source;};
 const prefix=make('repo','prefix'),literal=make('repo%20other','literal'),decoded=make('repo other','decoded');
 const linked=directory=>fs.readdirSync(path.join(directory,'.git','objects'),{recursive:true,withFileTypes:true}).filter(entry=>entry.isFile()&&fs.statSync(path.join(entry.parentPath,entry.name)).nlink>1).length;
 const clone=(source,name,env)=>{const target=path.join(scratch,'prefix-clones',name);git(['clone','-q',source,target],{...base,...env});
  return [fs.readFileSync(path.join(target,'fixture.txt'),'utf8').trim(),git(['-C',target,'config','remote.origin.url']),linked(target)];};
 assert.deepEqual(clone(literal,'premise',git_environment([prefix])),['decoded',literal,0]);  // the premise: what an entry for `repo` alone does
 const sources=local_sources(loads(JSON.stringify({mcpServers:{one:{source:{url:prefix}},two:{source:{url:literal}}}})));
 assert.deepEqual(git_misdirected(sources,sources),[]);
 const env=git_environment(sources);
 assert.deepEqual(clone(literal,'literal',env),['literal',literal,0]);
 assert.deepEqual(clone(prefix,'prefix',env),['prefix',prefix,0]);
 assert.deepEqual(clone(decoded,'decoded',env),['decoded',decoded,0]);  // not named; begins with `repo`, has no %: itself, over the transport
 assert.deepEqual(clone(literal,'alone',git_environment([literal])),['literal',literal,0]);
 assert.deepEqual(git_misdirected([prefix],[literal]),[literal]);  // and where it has no entry, the adapter is told
});
