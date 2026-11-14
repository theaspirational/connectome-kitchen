/** Contract draft identifier; no engine commands are implemented in phases 0–2. */
export const CONTRACT = 'connectome.kitchen/v1';
export const VERSION = '0.0.0-incubation';
/** Draft section 2.3 command names. Types only: no command is implemented or frozen. */
export type Command = 'plan' | 'apply' | 'verify' | 'recover' | 'spawn' | 'cutover';
/** Draft section 2.4 error core. The closed code table belongs to the later `errors` port. */
export interface ErrorCore { readonly error_code: string; readonly msg: string; }
/** Draft section 2.2 receipt envelope. `core` is the differential surface; `meta` is never compared. */
export interface Receipt<Core, Name extends Command | null = Command> {
  readonly contract: typeof CONTRACT;
  readonly command: Name;
  readonly core: Core;
  readonly meta: { readonly kitchen: string; readonly artifact_sha256: string | null; };
}
