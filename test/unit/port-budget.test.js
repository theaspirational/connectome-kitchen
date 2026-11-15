import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
// The admission budgets of the tar reader (divergence D4) at their production values, against the live oracle
// (test/differential/port_budget.py): archives that really hold one unit less than a budget, exactly a budget and one
// unit more, for each of the four budgets, the four sparse forms, and two archives close to all budgets at once. The
// port reads every one of them under exactly the heap limit the budgets are sized for.
const done=spawnSync('python3',['test/differential/port_budget.py','--check','--node',process.execPath],{encoding:'utf8',timeout:1800000});
let report={};
try{report=JSON.parse(done.stdout.trim().split('\n').pop()??'');}catch{/* reported by the first test */}
test('the budget differential ran, under the heap limit the reader requires',()=>{
 assert.equal(done.stderr,'');
 assert.deepEqual(report.problems,[]);
 assert.equal(done.status,0);
 assert.deepEqual(report.budgets,{metadataBytes:2**28,paxRecords:2**24,sparseNumbers:2**20,members:2**17});
 assert.equal(report.heap_limit_required_mib,3072);
 // Exactly the limit the reader requires, not a larger one: the flag is chosen so that heap_size_limit comes out at it.
 assert.equal(report.port_heap_size_limit_mib,3072);
 assert.ok(report.port_max_old_space_size_mib<3072);
});
for(const [kind,what] of [['members','members'],['records','pax records'],['names','many large names'],['sparse-global','a sparse map in a global header, expanded again and again']]){
 test(what+': one under the budget and at it both engines accept; one over it the oracle accepts and the port refuses (D4)',()=>{
  assert.deepEqual([report.cases?.[kind+'.below'],report.cases?.[kind+'.at'],report.cases?.[kind+'.above']],['equal','equal','D4']);
 });
}
test('the four sparse forms count alike: 2**19 regions are read in each, one region or number more is refused in each',()=>{
 for(const form of ['0.1','0.0','1.0','old'])assert.deepEqual([report.cases?.['sparse-'+form+'.at'],report.cases?.['sparse-'+form+'.above']],['equal','D4'],form);
});
test('close to every budget at once, the archive is read as the oracle reads it',()=>{
 assert.deepEqual([report.cases?.['combined.all-below'],report.cases?.['combined.heap-worst']],['equal','equal']);
});
test('a refusal is cheap, and no archive within the budgets costs more than the bound',()=>{
 const measured=Object.entries(report.measured??{});
 assert.equal(measured.length,22);
 for(const [name,entry] of measured){
  assert.ok(entry.port_peak_rss_mib<=report.port_rss_limit_mib,name+': '+entry.port_peak_rss_mib+' MiB');
  if(name.endsWith('.above')&&name!=='names.above')assert.ok(entry.port_peak_rss_mib<=report.refusal_rss_limit_mib,name+': '+entry.port_peak_rss_mib+' MiB');
 }
 console.log('peak resident memory in MiB, port: '+JSON.stringify(Object.fromEntries(measured.map(([name,entry])=>[name,entry.port_peak_rss_mib]))));
});
