import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
// Archives too large to commit, against the live oracle (test/differential/port_large.py). One of 4 GiB and more: a valid
// one is read as the oracle reads it, one with a wrong CRC-32 trailer is refused, and the port's peak memory stays under
// the bound. And the metadata limit (divergence D3) from both sides: a long name and a pax header of exactly 2**28 bytes
// are read by both engines; with one byte more the oracle accepts the archive and the port refuses it without reading the block.
const done=spawnSync('python3',['test/differential/port_large.py','--check','--node',process.execPath],{encoding:'utf8',timeout:900000});
let report={};
try{report=JSON.parse(done.stdout.trim().split('\n').pop()??'');}catch{/* reported by the first test */}
test('the large-archive differential ran',()=>{
 assert.equal(done.stderr,'');
 assert.deepEqual(report.problems,[]);
 assert.equal(done.status,0);
 assert.ok(report.decompressed_bytes>=2**32);
});
test('a valid archive of 4 GiB is read as the oracle reads it',()=>{assert.equal(report.cases?.['large.valid'],'equal');});
test('a wrong CRC-32 over 4 GiB is refused (D1)',()=>{assert.equal(report.cases?.['large.crc-trailer'],'D1');});
for(const kind of ['longname','pax']){
 test('a '+kind+' block of exactly the metadata limit is read as the oracle reads it',()=>{assert.equal(report.cases?.['metadata.'+kind+'.at-limit'],'equal');});
 test('a '+kind+' block one byte above the metadata limit is refused, where the oracle accepts (D3)',()=>{assert.equal(report.cases?.['metadata.'+kind+'.above-limit'],'D3');});
}
test('above the limit with the data missing, both engines refuse alike',()=>{assert.equal(report.cases?.['metadata.longname.above-limit.cut'],'equal');});
test('the port does not hold the decompressed stream, and refuses a block above the limit without reading it',()=>{
 assert.equal(report.metadata_limit_bytes,2**28);
 const measured=Object.entries(report.measured??{});
 for(const [name,entry] of measured){
  const bound=name.endsWith('.at-limit')?report.port_rss_limit_at_metadata_limit_mib:report.port_rss_limit_mib;
  assert.ok(entry.port_peak_rss_mib<=bound,name+': '+entry.port_peak_rss_mib+' MiB');
 }
 assert.equal(measured.length,7);
 assert.equal(measured.filter(([name])=>name.endsWith('.at-limit')).length,2);
 console.log('peak resident memory in MiB: '+JSON.stringify(report.measured));
});
