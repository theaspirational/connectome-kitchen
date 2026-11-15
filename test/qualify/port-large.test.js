import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
// Archives too large to commit, against the live oracle (test/differential/port_large.py). One of 4 GiB and more: a valid
// one is read as the oracle reads it, one with a wrong CRC-32 trailer is refused, and the port's peak memory stays under
// the bound. And the metadata limit (divergence D3, 2**28 bytes of one block) from both sides. It lies above the byte budget
// of D4 (2**24), so through archive_manifest a long name and a pax header of exactly the limit are refused under D4, and with
// one byte more under D3; the oracle accepts all four, and the port refuses each without reading the block. With the byte
// budget raised to the limit, a test option, the reader lists a block of exactly the limit and D3 alone refuses one byte more.
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
 test('a '+kind+' block of exactly the metadata limit is above the byte budget: refused, where the oracle accepts (D4)',()=>{assert.equal(report.cases?.['metadata.'+kind+'.at-limit'],'D4');});
 test('a '+kind+' block one byte above the metadata limit is refused, where the oracle accepts (D3)',()=>{assert.equal(report.cases?.['metadata.'+kind+'.above-limit'],'D3');});
}
test('above the limit with the data missing, both engines refuse alike',()=>{assert.equal(report.cases?.['metadata.longname.above-limit.cut'],'equal');});
test('the port does not hold the decompressed stream, and refuses a block at the limit or above it without reading it',()=>{
 assert.equal(report.metadata_limit_bytes,2**28);
 assert.equal(report.metadata_budget_bytes,2**24);
 const measured=Object.entries(report.measured??{});
 for(const [name,entry] of measured){
  assert.ok(entry.port_peak_rss_mib<=report.port_rss_limit_mib,name+': '+entry.port_peak_rss_mib+' MiB');
  // Under the raised byte budget the block at the limit is really read; above it D3 refuses before the read.
  const raised=entry.reader_under_a_byte_budget_of_the_limit_peak_rss_mib;
  if(name.endsWith('-limit'))assert.ok(raised<=(name.endsWith('.at-limit')?report.port_rss_limit_at_metadata_limit_mib:report.port_rss_limit_mib),name+': '+raised+' MiB under the raised budget');
 }
 assert.equal(measured.length,7);
 assert.equal(measured.filter(([name])=>name.endsWith('.at-limit')).length,2);
 console.log('peak resident memory in MiB: '+JSON.stringify(report.measured));
});
