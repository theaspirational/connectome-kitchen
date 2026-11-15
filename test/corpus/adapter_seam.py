#!/usr/bin/env python3
"""The oracle's cook adapter with one check taken out. Test only.

`connectome_cook.verify_cook` asks git whether the cook checkout is the pinned
commit with the reviewed tree. No sandbox can hold a checkout with that commit
id, so the corpus records that the check was asked for, and with what, and lets
the adapter go on. Everything else is the oracle's own `main`, argument for
argument (oracle/scripts/cook-adapter.py).
"""
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'oracle/module_utils'))
import connectome_cook


def recorded(entrypoint, pin, env):
    build = Path(sys.argv[sys.argv.index('--out') + 1])
    with (build.parent.parent / 'verify-cook-calls.jsonl').open('a') as stream:
        stream.write(json.dumps({'entrypoint': entrypoint, 'pin': pin, 'environment_names': sorted(env)}, sort_keys=True) + '\n')


connectome_cook.verify_cook = recorded

if __name__ == '__main__':
    connectome_cook.main()
