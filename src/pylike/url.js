import { isIP } from 'node:net';
/** Restricted urllib-compatible split, without WHATWG normalization. */
export function urlsplit(value) {
  value=value.replace(/^[\x00-\x20]+/u,'').replace(/[\t\r\n]/gu,'');
  let scheme='',netloc='',path=value,query='',fragment='';
  const colon=/^([A-Za-z][A-Za-z0-9+.-]*):/u.exec(path); if(colon){scheme=colon[1].toLowerCase();path=path.slice(colon[0].length);}
  if(path.startsWith('//')){const end=path.slice(2).search(/[/?#]/u);const n=end<0?path.length:end+2;netloc=path.slice(2,n);path=path.slice(n);}
  const hash=path.indexOf('#');if(hash>=0){fragment=path.slice(hash+1);path=path.slice(0,hash);}
  const q=path.indexOf('?');if(q>=0){query=path.slice(q+1);path=path.slice(0,q);}
  if(netloc.includes('[')!==netloc.includes(']'))throw new Error('Invalid IPv6 URL');
  const checked=netloc.replace(/[@:#?]/gu,'').normalize('NFKC');
  if(/[/:#?@]/u.test(checked))throw new Error('invalid netloc normalization');
  if(netloc.includes('[')) {
    const part=netloc.split('@').at(-1);
    if(!part.startsWith('[')||!/^\[[^\]]+\](?::[^:]*)?$/u.test(part))throw new Error('invalid bracketed host');
    const host=part.slice(1,part.indexOf(']'));
    if(host.startsWith('v')) {if(!/^v[0-9a-fA-F]+\..+$/u.test(host))throw new Error('invalid future host');}
    else if(isIP(host)!==6)throw new Error('invalid IPv6 host');
  }
  const host=netloc.split('@').at(-1); let port=null;
  const portText=host.startsWith('[')?host.slice(host.indexOf(']')+1):host.includes(':')?host.slice(host.indexOf(':')):'';
  if(portText){if(!/^:[0-9]*$/u.test(portText))throw new Error('invalid port');if(portText!==':'){port=Number(portText.slice(1));if(port>65535)throw new Error('invalid port');}}
  return {scheme,netloc,path,query,fragment,port};
}
export function unquote(value) {
  // Decode each contiguous percent-byte run with UTF-8 replacement like urllib.
  return value.replace(/(?:%[0-9a-fA-F]{2})+/gu,s=>new TextDecoder().decode(Uint8Array.from(s.match(/%[0-9a-fA-F]{2}/g).map(x=>parseInt(x.slice(1),16)))));
}
