"""Stand-in for `systemd-analyze` in the deploy corpus. Test only; never talks to a user manager.

Under `activation: install_only` the engine looks for `systemd-analyze` on the search path and, when it finds one, runs
`<it> --user verify <installed unit>...` as the last step of health (deploy.py:907-913). corpus.py puts a script of that
name first on the search path of both engines; the script runs this file with two names set:

    CALLS    where each call is written down, one JSON line of its arguments;
    VERDICT  a file that, when present, holds the exit status to give instead of looking at the units.

Without a verdict it answers as the oracle's own double does (oracle/tests/fixtures/analyze.py): the call must be
`--user verify` with at least one unit, and each unit must be a readable file with a `[Service]` section.
"""
import json, sys
from pathlib import Path

with open(CALLS, 'a') as stream:  # noqa: F821 (set by the script that runs this file)
    stream.write(json.dumps(sys.argv[1:]) + '\n')
if Path(VERDICT).is_file():  # noqa: F821
    sys.exit(int(Path(VERDICT).read_text()))  # noqa: F821
if sys.argv[1:3] != ['--user', 'verify'] or not sys.argv[3:]:
    sys.exit(64)
for unit in sys.argv[3:]:
    try:
        if '[Service]' not in Path(unit).read_text():
            sys.exit(65)
    except OSError:
        sys.exit(66)
