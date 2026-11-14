import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
// Archives of 4 GiB and more against the live oracle (test/differential/port_large.py): a valid one is read as the
// oracle reads it, one with a wrong CRC-32 trailer is refused, and the port's peak memory stays under the bound.
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
test('the port does not hold the decompressed stream',()=>{
 for(const [name,entry] of Object.entries(report.measured??{}))assert.ok(entry.port_peak_rss_mib<=report.port_rss_limit_mib,name+': '+entry.port_peak_rss_mib+' MiB');
 assert.equal(Object.keys(report.measured??{}).length,2);
 console.log('peak resident memory in MiB: '+JSON.stringify(report.measured));
});
