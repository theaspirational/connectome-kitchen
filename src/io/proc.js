import { spawnSync } from 'node:child_process';
/** Child output is never forwarded, nor included in failures. */
export function run(argv,{env={},cwd,timeout=30000,maxBuffer=1024*1024,log=()=>{}}={}) {
  if(!Array.isArray(argv)||!argv.length||typeof argv[0]!=='string'||!argv[0].startsWith('/')||argv.some(x=>typeof x!=='string'||x.includes('\0')))throw new Error('invalid child argv');
  const result=spawnSync(argv[0],argv.slice(1),{env,cwd,timeout,maxBuffer,stdio:['ignore','pipe','pipe']});
  // Logs contain only structural properties, never argv, environment values or output.
  log({op:'child',stdin:'null',stdout:'discard',stderr:'discard',envNames:Object.keys(env).sort(),status:result.status});
  if(result.error||result.status!==0)throw new Error('child refused');
  return {status:0};
}
