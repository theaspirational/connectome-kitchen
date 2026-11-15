#!/usr/bin/env python3
"""Where CPython's tarfile, listing an archive, passes an admission budget of the TS reader (divergence D4).

The TS reader counts four things over one listing and refuses when a count
would pass its budget (src/pylike/tarfile.ts, `Budgets`). The oracle has no
budgets. To say on which archives the two may differ, `crossing` walks an
archive with the oracle's own tarfile and counts the same four things at the
same points:

    metadataBytes   on entry to `_proc_gnulong` and `_proc_pax`, the size the
                    header states; 512 before each block `_proc_gnusparse_10`
                    reads and before each extension block `_proc_sparse` reads
    paxRecords      each time `_proc_pax` looks for the length prefix of a
                    record (its one use of `_header_length_prefix_re`)
    sparseNumbers   each integer tarfile converts for a sparse list: the parts
                    of a `GNU.sparse.map` (0.1), the offset and numbytes
                    records (0.0), the lines after the count (1.0); and two
                    for each struct of an old GNU sparse header
    members         each TarInfo `TarFile.next` appends to the listing

The walk stops at the first count that would pass its budget and names it.
A long-name or pax header that states more than METADATA_LIMIT bytes is
divergence D3 and is named `D3` instead. Nothing here decides an outcome: the
oracle's answer is taken from an untouched tarfile elsewhere. This walk has
frames and a recursion limit of its own, so it says nothing about where the
frame limit falls.

The three sparse methods that read are walked ahead of tarfile, on the same
file object, which is then put back: the lines below repeat what those
methods read and convert, no more.
"""
import io, sys, tarfile

METADATA_LIMIT = 2 ** 28
BUDGET_NAMES = ('metadataBytes', 'paxRecords', 'sparseNumbers', 'members')
BLOCK = tarfile.BLOCKSIZE


class Crossed(BaseException):
    """Not an Exception, so that no handler of tarfile takes it for a damaged archive."""

    def __init__(self, what):
        super().__init__(what)
        self.what = what


def crossing(source, budgets):
    """({'crossed': None | 'D3' | a budget name, 'used': counts when the walk ended}) for a path or the bytes of a gzip archive."""
    used = dict.fromkeys(BUDGET_NAMES, 0)

    def charge(name, amount):
        if amount > budgets[name] - used[name]:
            raise Crossed(name)
        used[name] += amount

    class LengthPrefix:
        def __init__(self, real):
            self.real = real

        def match(self, buf, pos):
            charge('paxRecords', 1)
            return self.real.match(buf, pos)

    class Probe(tarfile.TarInfo):
        def _metadata(self):
            if self.size > METADATA_LIMIT:
                raise Crossed('D3')
            if self.size >= 0:  # a negative size is refused by tarfile before anything is read
                charge('metadataBytes', self.size)

        def _proc_gnulong(self, archive):
            self._metadata()
            return super()._proc_gnulong(archive)

        def _proc_pax(self, archive):
            self._metadata()
            return super()._proc_pax(archive)

        def _proc_sparse(self, archive):
            structs, isextended, _origsize = self._sparse_structs
            charge('sparseNumbers', 2 * len(structs))
            position = archive.fileobj.tell()
            try:
                while isextended:
                    charge('metadataBytes', BLOCK)
                    buf = archive.fileobj.read(BLOCK)
                    pos = 0
                    for _ in range(21):
                        try:
                            offset = tarfile.nti(buf[pos:pos + 12])
                            numbytes = tarfile.nti(buf[pos + 12:pos + 24])
                        except ValueError:
                            break
                        if offset and numbytes:
                            charge('sparseNumbers', 2)
                        pos += 24
                    isextended = bool(buf[504])
            except Crossed:
                raise
            except Exception:
                pass  # tarfile fails at the same place, below
            archive.fileobj.seek(position)
            return super()._proc_sparse(archive)

        def _proc_gnusparse_00(self, next, raw_headers):
            for _length, keyword, value in raw_headers:
                if keyword in (b'GNU.sparse.offset', b'GNU.sparse.numbytes'):
                    try:
                        int(value.decode())
                    except ValueError:
                        break
                    charge('sparseNumbers', 1)
            return super()._proc_gnusparse_00(next, raw_headers)

        def _proc_gnusparse_01(self, next, pax_headers):
            for part in pax_headers['GNU.sparse.map'].split(','):
                try:
                    int(part)
                except ValueError:
                    break
                charge('sparseNumbers', 1)
            return super()._proc_gnusparse_01(next, pax_headers)

        def _proc_gnusparse_10(self, next, pax_headers, archive):
            position = archive.fileobj.tell()
            try:
                charge('metadataBytes', BLOCK)
                buf = archive.fileobj.read(BLOCK)
                fields, buf = buf.split(b'\n', 1)
                fields = int(fields)
                count = 0
                while count < fields * 2:
                    if b'\n' not in buf:
                        charge('metadataBytes', BLOCK)
                        buf += archive.fileobj.read(BLOCK)
                    number, buf = buf.split(b'\n', 1)
                    int(number)
                    charge('sparseNumbers', 1)
                    count += 1
            except Crossed:
                raise
            except Exception:
                pass  # tarfile fails at the same place, below
            archive.fileobj.seek(position)
            return super()._proc_gnusparse_10(next, pax_headers, archive)

    class Listing(tarfile.TarFile):
        def next(self):
            handed_over = self.firstmember is not None  # read and counted by __init__ already
            info = super().next()
            if info is not None and not handed_over:
                charge('members', 1)
            return info

    real, limit = tarfile._header_length_prefix_re, sys.getrecursionlimit()
    tarfile._header_length_prefix_re = LengthPrefix(real)
    sys.setrecursionlimit(max(limit, 20000))
    crossed = None
    try:
        where = {'name': source} if isinstance(source, str) else {'fileobj': io.BytesIO(source)}
        with Listing.open(mode='r:gz', tarinfo=Probe, **where) as archive:
            archive.getmembers()
    except Crossed as stop:
        crossed = stop.what
    except Exception:
        pass
    finally:
        tarfile._header_length_prefix_re = real
        sys.setrecursionlimit(limit)
    return {'crossed': crossed, 'used': used}


if __name__ == '__main__':
    import json
    print(json.dumps(crossing(sys.argv[1], json.loads(sys.argv[2]))))
