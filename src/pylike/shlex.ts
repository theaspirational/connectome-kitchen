export function split(text: string): string[] {
  const result: string[]=[]; let word='', active=false, quote: string | null=null;
  for(let i=0;i<text.length;i++) {
    const c=text[i]!;
    if(quote==='\'') { if(c==='\'')quote=null;else word+=c; continue; }
    if(quote==='"') { if(c==='"')quote=null;else if(c==='\\'&&(['"','\\'] as (string | undefined)[]).includes(text[i+1]))word+=text[++i];else word+=c;continue; }
    if(c==='\''||c==='"') {quote=c;active=true;}
    else if(c==='\\') {if(++i===text.length)throw new Error('No escaped character');word+=text[i];active=true;}
    else if(' \t\r\n'.includes(c)) {if(active){result.push(word);word='';active=false;}}
    else {word+=c;active=true;}
  }
  if(quote)throw new Error('No closing quotation'); if(active)result.push(word);return result;
}
export function quote(text: string): string { return text===''?"''":/^[\w@%+=:,./-]+$/u.test(text)?text:"'"+text.replaceAll("'", "'\"'\"'")+"'"; }
export const join = (words: readonly string[]) => words.map(quote).join(' ');
