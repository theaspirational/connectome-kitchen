#!/usr/bin/env python3
"""What the TS cook adapter puts into a build, held under the name of its contract.

A kitchen names a build by `ADAPTER_CONTRACT` (src/cook.ts), not by the path of
its adapter (ruling D5). So the number in that name is all that tells a build
made by one adapter from a build made by another, and it has to move when the
adapter's output does. This holds the two together.

    adapter_contract.py check  [--node NODE] [--case NAME] [--seam FILE]
    adapter_contract.py record [--node NODE] [--replace]

For each public case of the deploy corpus the adapter is started by itself, as
the engine starts it: in a fresh `ROOT/builds/REQUEST` that holds
`deployment.json` and, where the deployment supplies a recipe, the staged
`cook-recipe.json`. Cook is the stand-in of the corpus, or the replay of a kept
build where the case has one, and `verify_cook` is recorded and not performed
(adapter_seam.mjs). What is held: the exit status, what it printed, how it
called cook (arguments, working directory, environment names), and every file,
link and directory that is there afterwards and was not before, with mode and
text. That is the recipe written or staged, the layout, the framework record,
the lock after the adapter's post-processing, and what cook's stand-in left
beside them. It is taken at two roots of different length, as a golden of the
corpus is, so it does not depend on where it was made.

The record is `adapter-contract/<name with - for />.golden`. `check` fails when
there is none under the present name, or when the output is not the one
recorded, and says where. A record under a name is not rewritten for a change
of the adapter: `record` refuses, and the way on is to raise the number in
src/cook.ts and record under the new name; the earlier record stays. One thing
else can change the output, the cases and stand-ins themselves. The record
keeps the digests of those files, `check` fails when they are not the files in
the tree, and `record --replace` rewrites a record only then.
"""
import argparse, hashlib, json, os, re, subprocess, sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import corpus
from corpus import ROOT, CorpusError

RECORDS = HERE / 'adapter-contract'
# The files that make the input of the adapter and stand around it. A change of one of them can change the output with the adapter as it was.
FIXTURES = ('test/corpus/adapter_contract.py', 'test/corpus/adapter_seam.mjs', 'test/corpus/corpus.py', 'test/corpus/replay_cook.py',
            'test/corpus/stand_in_cook.py', 'test/corpus/synthetic.py')
NAME = re.compile(r'([a-z][a-z-]*)/([1-9][0-9]*)')
ABOUT = ('What the TS cook adapter puts into a build for each public case of the deploy corpus, under the name the engine hashes for it '
         '(src/cook.ts ADAPTER_CONTRACT, ruling D5). Written by test/corpus/adapter_contract.py record and held by check. It is not rewritten '
         'for a change of the adapter: such a change raises the number and gets a record of its own.')


def contract(node):
    """The name as the built engine has it."""
    done = subprocess.run([node, '--input-type=module', '-e', "import { ADAPTER_CONTRACT } from './dist/cook.js'; process.stdout.write(ADAPTER_CONTRACT);"],
                          cwd=ROOT, capture_output=True, text=True, timeout=60)
    if done.returncode or not NAME.fullmatch(done.stdout):
        raise CorpusError('the built engine does not state the name of its adapter contract: ' + done.stderr[-400:])
    return done.stdout


def record_of(name):
    return RECORDS / (name.replace('/', '-') + '.golden')


def fixtures():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FIXTURES}


def observe(case, sandbox, node, seam):
    """One run of the adapter on one case at one root. Returns the record as text, located nowhere."""
    import connectome_deploy, connectome_inputs
    world = corpus.World(case, sandbox)
    world.build()
    with corpus.host_model(world.sandbox, world.account):
        deployment = connectome_deploy.Deployment(world.root, world.unit_dir, corpus.copy.deepcopy(world.effective), world.versions, world.shim_command(), check=True, base=world.base)
    # What the engine has made when it starts the adapter (deploy.py:781-798): the directories, the request, the staged recipe, the state directory.
    build = Path(deployment.build)
    for directory in (Path(world.root), build.parent, build, Path(world.effective['state_dir'])):
        corpus.make_directory(directory.parent, private_under=world.home)
        directory.mkdir(exist_ok=True)
        directory.chmod(0o700)
    corpus.write(build / 'deployment.json', connectome_deploy.canonical(deployment.spec), 0o600)
    with corpus.host_model(world.sandbox, world.account):
        staged = connectome_inputs.payloads(world.effective, world.operator_files())
    for name, content in staged.items():
        corpus.write(build / name, content, 0o600)
    before = corpus.snapshot(world.sandbox)
    tools = world.tools_moved
    argv = ['--spec', str(build / 'deployment.json'), '--out', str(build), '--state', world.effective['state_dir'], '--runtime', tools['runtime'],
            '--entrypoint', tools['entrypoint'], '--bun-path', tools['bun'], '--node-path', tools['node']]
    done = subprocess.run([node, str(seam), *argv], cwd=build, capture_output=True, text=True, timeout=300,
                          env={'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(world.home), 'LANG': 'C.UTF-8'})
    after = corpus.snapshot(world.sandbox)
    root = Path(world.root)
    record = {
        'given': {'argv': argv, 'build': {path: row for path, row in before.items() if Path(path).parent == build}},
        'status': done.returncode, 'stdout': done.stdout, 'stderr': done.stderr,
        'cook_calls': corpus.lines(root / 'cook-calls.jsonl'), 'verify_cook_calls': corpus.lines(root / 'verify-cook-calls.jsonl'),
        'left': {path: row for path, row in after.items() if before.get(path) != row and Path(path).name not in corpus.LOGS},
        'removed': sorted(path for path in before if path not in after),
    }
    return corpus.located_nowhere(record, world)


def observed(node, seam, only=None):
    corpus.ts_port.initialize()
    scratch = corpus.scratch_of(corpus.SYNTHETIC, None) / 'adapter-contract'
    cases = {}
    try:
        for case in corpus.cases(corpus.located(corpus.SYNTHETIC, scratch), only):
            texts = [observe(case, scratch / label / case.name, node, seam) for label in ('a', 'bb')]
            cases[case.name] = json.loads(corpus.normalize(*texts)[0])
    finally:
        corpus.ts_port.remove(scratch)
    return cases


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('check', 'record'))
    parser.add_argument('--node', default='node')
    parser.add_argument('--case', action='append', help='check: this case only')
    parser.add_argument('--seam', help='check: start this file in the adapter\'s place (a test of the check itself)')
    parser.add_argument('--replace', action='store_true', help='record: rewrite the record under the present name, after a change of the cases or stand-ins')
    args = parser.parse_args()
    os.umask(0o022)  # the modes of what the adapter and the stand-in write are recorded; they must not depend on the caller's umask
    name = contract(args.node)
    path, files = record_of(name), fixtures()
    kept = json.loads(path.read_text()) if path.is_file() else None
    cases = observed(args.node, Path(args.seam).resolve() if args.seam else HERE / 'adapter_seam.mjs', args.case if args.command == 'check' else None)
    if args.command == 'record':
        if args.case:
            raise CorpusError('a record is made of every case')
        if kept is not None and kept['cases'] != cases:
            if kept['fixtures'] == files:
                print('REFUSED: the adapter\'s output is not the one recorded under %s, with the cases and stand-ins as they were. Raise the number of ADAPTER_CONTRACT in src/cook.ts, build, and record under the new name.' % name)
                return 1
            if not args.replace:
                print('REFUSED: the output is not the one recorded under %s, and the cases or stand-ins changed since. If the adapter did not change, record --replace; if it did, raise the number.' % name)
                return 1
        if args.seam:  # a test of the refusals above: nothing is ever recorded of anything but the adapter itself
            raise CorpusError('a record is made with the adapter itself')
        RECORDS.mkdir(exist_ok=True)
        path.write_text(json.dumps({'about': ABOUT, 'contract': name, 'fixtures': files, 'cases': cases}, indent=1, sort_keys=True, ensure_ascii=False) + '\n')
        print('recorded %s: %s' % (name, ', '.join(sorted(cases))))
        return 0
    failed = False
    later = sorted(found.name for found in RECORDS.glob('*.golden') if (match := NAME.fullmatch(json.loads(found.read_text())['contract'])) and
                   match[1] == NAME.fullmatch(name)[1] and int(match[2]) > int(NAME.fullmatch(name)[2]))
    if later:
        failed = True
        print('FAIL: a record is kept under a later number than %s (%s): the number does not go back' % (name, ', '.join(later)))
    if kept is None:
        print('FAIL: no record of the adapter\'s output under %s: test/corpus/adapter_contract.py record' % name)
        return 1
    if kept['contract'] != name:
        failed = True
        print('FAIL: %s is not the record of %s' % (path.name, name))
    if kept['fixtures'] != files and not args.seam:
        failed = True
        print('FAIL: the cases or stand-ins are not the files the record under %s was made with (%s): test/corpus/adapter_contract.py record' % (
            name, ', '.join(sorted(file for file in files if kept['fixtures'].get(file) != files[file]))))
    for case in sorted(cases):
        parted = corpus.differences(kept['cases'].get(case), cases[case])
        for where in parted:
            print('  ADAPTER %s differs at %s' % (case, where))
        print('%s %s: the adapter\'s output %s under %s' % ('PASS' if not parted else 'FAIL', case, 'is the one recorded' if not parted else 'is NOT the one recorded', name))
        failed = failed or bool(parted)
    if not args.case and sorted(kept['cases']) != sorted(cases):
        failed = True
        print('FAIL: the record holds other cases than the corpus has')
    if failed:
        print('If the adapter puts something else into a build, on purpose: raise the number of ADAPTER_CONTRACT in src/cook.ts and record under the new name. Every agent is then built anew once.')
    return int(failed)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except CorpusError as error:
        print('adapter_contract: ' + str(error), file=sys.stderr)
        sys.exit(2)
