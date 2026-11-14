/**
 * `datetime.fromisoformat` of CPython 3.12, for the one shape the engine hands
 * it: `YYYY-MM-DDTHH:MM:SS[.digits]+00:00` in ASCII digits (lock.py:160-161
 * checks that shape first and only then calls it).
 */
import { PyValueError } from './py.js';

const SHAPE = /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.[0-9]+)?\+00:00$/u;

/** Returns normally where CPython returns a datetime; `ValueError` where it raises. Any number of fraction digits is accepted (the excess is truncated there). */
export function fromisoformat(text: string): true {
  const match = SHAPE.exec(text);
  if (!match) throw new Error('unsupported fromisoformat shape');
  const [year, month, day, hour, minute, second] = match.slice(1, 7).map(Number) as [number, number, number, number, number, number];
  if (year < 1) throw new PyValueError('year ' + year + ' is out of range');
  if (month < 1 || month > 12) throw new PyValueError('month must be in 1..12');
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]!;
  if (day < 1 || day > days) throw new PyValueError('day is out of range for month');
  if (hour > 23) throw new PyValueError('hour must be in 0..23');
  if (minute > 59) throw new PyValueError('minute must be in 0..59');
  if (second > 59) throw new PyValueError('second must be in 0..59');
  return true;
}
