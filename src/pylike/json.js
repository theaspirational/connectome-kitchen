/** Python-compatible typed JSON. JSON.parse must not erase numeric kind. */
export class Int {
  /** @param {string|bigint} value */
  constructor(value) { this.value = BigInt(value); Object.freeze(this); }
}
export class Float {
  /** @param {number} value */
  constructor(value) {
    if (!Number.isFinite(value)) throw new Error('non-finite number');
    this.value = value; Object.freeze(this);
  }
}
/** @param {string} source @returns {unknown} */
export function loads(source) {
  let i = 0, depth = 0;
  const bad = () => { throw new Error('invalid JSON'); };
  const ws = () => { while (/[\x20\t\r\n]/u.test(source[i] ?? '\0')) i++; };
  function string() {
    const start = i++;
    while (i < source.length) {
      const c = source[i++];
      if (c === '"') return JSON.parse(source.slice(start, i));
      if (c === '\\') { if (source[i] === 'u') { if (!/^[0-9a-fA-F]{4}$/u.test(source.slice(i+1,i+5))) bad(); i+=5; }
        else { if (!'"\\/bfnrt'.includes(source[i] ?? '\0')) bad(); i++; } }
      else if (c.charCodeAt(0) < 32) bad();
    }
    return bad();
  }
  function value() {
    ws(); if (++depth > 512) bad();
    let result;
    if (source[i] === '"') result = string();
    else if (source[i] === '[') {
      result = []; i++; ws();
      if (source[i] !== ']') while (true) { result.push(value()); ws(); if (source[i] !== ',') break; i++; }
      if (source[i++] !== ']') bad();
    } else if (source[i] === '{') {
      result = Object.create(null); i++; ws();
      if (source[i] !== '}') while (true) {
        ws(); if (source[i] !== '"') bad(); const key = string();
        if (Object.hasOwn(result, key)) bad(); ws(); if (source[i++] !== ':') bad();
        result[key] = value(); ws(); if (source[i] !== ',') break; i++;
      }
      if (source[i++] !== '}') bad();
    } else {
      const token = /^(?:true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)/u.exec(source.slice(i))?.[0];
      if (!token) bad(); i += token.length;
      result = token === 'true' ? true : token === 'false' ? false : token === 'null' ? null
        : /[.eE]/u.test(token) ? new Float(Number(token)) : new Int(token);
    }
    depth--; return result;
  }
  const result = value(); ws(); if (i !== source.length) bad(); return result;
}
/** Unicode code point ordering, not UTF-16 ordering. */
export function compare(a, b) {
  const x = Array.from(a, c=>c.codePointAt(0)), y=Array.from(b,c=>c.codePointAt(0));
  for (let n=0;n<Math.min(x.length,y.length);n++) if(x[n]!==y[n]) return x[n]-y[n];
  return x.length-y.length;
}
function floatText(value) {
  if (!Number.isFinite(value)) throw new Error('non-finite number');
  if (Object.is(value,-0)) return '-0.0';
  const text=value.toString();
  const [mantissa, exp] = text.toLowerCase().split('e');
  const exponent = exp === undefined ? Math.floor(Math.log10(Math.abs(value))) : Number(exp);
  if (value !== 0 && (exponent < -4 || exponent >= 16)) {
    const scientific = value.toExponential();
    const [m,e] = scientific.split('e'); const n=Number(e);
    return m+'e'+(n<0?'-':'+')+Math.abs(n).toString().padStart(2,'0');
  }
  return text.includes('.') ? text : text+'.0';
}
/** Three encoders: compact ASCII canonical; compact Unicode; Python default spaces. */
export function dumps(value, {ascii=true, compact=true, sort=true}={}) {
  const seen = new Set();
  const quote = s => {
    let out='"';
    for(let i=0;i<s.length;i++) {
      const c=s.charCodeAt(i), ch=s[i];
      if(ch==='"'||ch==='\\') out+='\\'+ch;
      else if(c===8) out+='\\b'; else if(c===9) out+='\\t'; else if(c===10) out+='\\n';
      else if(c===12) out+='\\f'; else if(c===13) out+='\\r';
      else if(c<32||(ascii&&c>=127)) out+='\\u'+c.toString(16).padStart(4,'0');
      else out+=ch;
    }
    return out+'"';
  };
  function emit(v) {
    if(v===null) return 'null';
    if(v instanceof Int) return v.value.toString();
    if(v instanceof Float) return floatText(v.value);
    if(typeof v==='bigint') return v.toString();
    if(typeof v==='number') throw new TypeError('number kind required');
    if(typeof v==='boolean') return String(v);
    if(typeof v==='string') return quote(v);
    if(typeof v!=='object'||seen.has(v)) throw new TypeError('not JSON');
    seen.add(v); let out;
    const comma=compact?',':', ', colon=compact?':':': ';
    if(Array.isArray(v)) out='['+v.map(emit).join(comma)+']';
    else {
      const keys=Object.keys(v); if(sort) keys.sort(compare);
      out='{'+keys.map(k=>quote(k)+colon+emit(v[k])).join(comma)+'}';
    }
    seen.delete(v); return out;
  }
  return emit(value);
}
export const canonical = value => dumps(value);
export const unicode = value => dumps(value,{ascii:false});
export const spaced = value => dumps(value,{compact:false});
