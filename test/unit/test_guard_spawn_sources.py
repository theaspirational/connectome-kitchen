"""Purpose-built killing vectors for the spawn catalog, source readers and planner guards."""
import collections, contextlib, hashlib, importlib.util, io, json, os, re, subprocess, sys, tempfile, unittest, zlib
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'oracle/module_utils'),str(ROOT/'oracle/tests')]
import yaml
import test_spawn
from connectome_spawn import SpawnError, load_catalog, make_plan, require_bound
from connectome_spawn import catalog as spawn_catalog, planner, sources
from connectome_spawn.catalog import RoleCatalog, descriptor_digest

# The frozen contract adapter is the boundary that turns engine exceptions into closed codes.
_spec=importlib.util.spec_from_file_location('guard_spawn_shim',ROOT/'oracle/shim/kitchen_v1.py')
shim=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(shim)

STAMP='2001-02-03T04:05:06+00:00'
GIT_ENV={'PATH':os.defpath,'LANG':'C','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null',
         'GIT_AUTHOR_NAME':'Fixture Author','GIT_AUTHOR_EMAIL':'author@example.invalid','GIT_AUTHOR_DATE':STAMP,
         'GIT_COMMITTER_NAME':'Fixture Committer','GIT_COMMITTER_EMAIL':'committer@example.invalid',
         'GIT_COMMITTER_DATE':STAMP}
AUTHOR=b'author Fixture Author <author@example.invalid> 981173106 +0000\n'
COMMITTER=b'committer Fixture Committer <committer@example.invalid> 981173106 +0000\n'
REFUSED=(1,{'error':'spawn input refused','filesWritten':False,'error_code':'E_SPAWN_REFUSED'})
FAKE_COMMIT='1'*40


def git(repo,*args):
    done=subprocess.run(['git','-C',str(repo),*args],env=GIT_ENV,check=True,stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL)
    return done.stdout.decode().strip()


def loose_commit(repo,body):
    """Store a hand-written commit object, as any writer of the object store could."""
    data=b'commit '+str(len(body)).encode()+b'\0'+body
    oid=hashlib.sha1(data).hexdigest()
    target=Path(repo)/'.git/objects'/oid[:2]/oid[2:]
    target.parent.mkdir(exist_ok=True);target.write_bytes(zlib.compress(data))
    return oid


class Guards(unittest.TestCase):
    def setUp(self):
        (ROOT/'.cache').mkdir(parents=True,exist_ok=True)
        temp=tempfile.TemporaryDirectory(dir=ROOT/'.cache');self.addCleanup(temp.cleanup)
        self.work=Path(temp.name)

    # ---- fixtures -------------------------------------------------------
    def refused(self,message):
        return self.assertRaisesRegex(SpawnError,'^'+re.escape(message)+'$')

    def write_catalog(self,name,descriptors,versions,files=None):
        root=self.work/name;(root/'connectors').mkdir(parents=True)
        for key,value in descriptors.items():
            (root/'connectors'/((files or {}).get(key,key)+'.yml')).write_text(yaml.safe_dump({key:value}))
        (root/'versions.yml').write_text(yaml.safe_dump(versions))
        return root

    def world(self,repository=True):
        """The frozen spawn fixture shape: one legacy connector, one archive runtime."""
        w=SimpleNamespace(spec=test_spawn.example_template(),declaration=test_spawn.example_instance())
        w.repo=self.work/'repo';w.commit=w.link_commit=FAKE_COMMIT
        w.template_bytes=yaml.safe_dump(w.spec).encode();w.payload_bytes=b'local fixture payload\x00\xff'
        if repository:
            w.repo.mkdir();git(w.repo,'-c','init.defaultBranch=main','init','-q')
            (w.repo/'template.yml').write_bytes(w.template_bytes)
            (w.repo/'payload.bin').write_bytes(w.payload_bytes)
            (w.repo/'link.yml').symlink_to('template.yml')
            git(w.repo,'add','.');git(w.repo,'commit','-qm','Offline fixture')
            w.link_commit=git(w.repo,'rev-parse','HEAD')
            (w.repo/'link.yml').unlink()
            git(w.repo,'add','.');git(w.repo,'commit','-qm','Regular files only')
            w.commit=git(w.repo,'rev-parse','HEAD');w.tree=git(w.repo,'rev-parse',w.commit+'^{tree}')
        w.payload_root=self.work/'payloads';(w.payload_root/'inputs').mkdir(parents=True)
        w.archive=w.payload_root/'inputs/node.tar.gz';w.archive.write_bytes(b'offline fixture archive, not an executable')
        w.connector={'repo':'https://example.invalid/connector.git','pin_ref':'connectors.example-connector',
                     'enabled':True,'build':[['npm','run','build']],'required_secrets':['EXAMPLE_TOKEN'],
                     'settings':{'command':'node','args':['index.js'],'env':{'EXAMPLE_TOKEN':'${EXAMPLE_TOKEN}'}}}
        w.node={'kind':'runtime','pin_ref':'artifacts.node','enabled':False,'exports':{'node':'bin/node'},'requires':[]}
        w.versions={'runtime':{n:w.commit for n in ('cook','connectome-host','agent-framework')},
                    'connectors':{'example-connector':w.commit},
                    'artifacts':{'node':{'archive':'inputs/node.tar.gz','version':'1.0.0',
                                         'sha256':hashlib.sha256(w.archive.read_bytes()).hexdigest(),
                                         'descriptor_sha256':descriptor_digest(w.node)}}}
        w.catalog_root=self.write_catalog('catalog',{'example-connector':w.connector,'node':w.node},w.versions)
        w.catalog=load_catalog(w.catalog_root)
        w.stores={'runtime.'+n:str(w.repo) for n in w.versions['runtime']}
        w.stores.update({'connectors.example-connector':str(w.repo),'artifacts.node':str(w.payload_root)})
        return w

    def plan(self,w,spec=None,declaration=None,catalog=None,stores=None,**kwargs):
        return make_plan(spec or w.spec,declaration or w.declaration,catalog or w.catalog,
                         stores if stores is not None else w.stores,**kwargs)

    def with_settings(self,w,**settings):
        value=deepcopy(w.catalog);value.descriptors['example-connector']['settings'].update(settings)
        return value

    def archive_connector(self,w,settings):
        """The connector as a staged archive (pin object), as in the frozen archive test."""
        entry=dict(w.connector,requires=['node'],settings=settings)
        versions=deepcopy(w.versions)
        versions['connectors']['example-connector']=dict(versions['artifacts']['node'],
            descriptor_sha256=descriptor_digest(entry),commit=w.commit)
        stores=dict(w.stores,**{'connectors.example-connector':str(w.payload_root)})
        return entry,versions,stores

    def request(self,w,**changes):
        return dict({'action':'plan','template':w.spec,'instance':w.declaration,
                     'catalog':str(w.catalog_root),'stores':w.stores},**changes)

    def contract(self,request):
        path=self.work/'request.json'
        path.write_text(json.dumps({'contract':'connectome.kitchen/v1','command':'spawn','request':request}))
        out=io.TextIOWrapper(io.BytesIO())
        with contextlib.redirect_stdout(out):status=shim.main(['spawn','--input',str(path)])
        return status,json.loads(out.buffer.getvalue())['core']

    def memory_catalog(self):
        w=self.world(repository=False)
        return w,deepcopy(w.catalog)

    # ---- catalog.py: descriptors and versions ---------------------------
    def test_catalog_L48_4(self):
        w=self.world(repository=False)
        (w.catalog_root/'connectors/broken.yml').write_text('broken: [1]\n')
        # A list descriptor is a spawn refusal at the contract boundary, not a contract fault.
        self.assertEqual(self.contract(self.request(w)),REFUSED)
        with self.refused('catalog descriptor must be an object'):load_catalog(w.catalog_root)

    def test_named_catalog_descriptor_1(self):
        w=self.world(repository=False);nested='leaf'
        for _ in range(40):nested=[nested]
        entry=dict(w.connector,settings={'command':'node','args':nested})
        with self.refused('document nesting limit exceeded'):
            spawn_catalog.descriptor('example-connector',entry)

    def test_catalog_L74_4(self):
        w=self.world(repository=False)
        node=dict(w.node,exports=['bin/node'])
        (w.catalog_root/'connectors/node.yml').write_text(yaml.safe_dump({'node':node}))
        self.assertEqual(self.contract(self.request(w)),REFUSED)
        with self.refused('invalid exports'):load_catalog(w.catalog_root)

    def test_catalog_L81_4(self):
        # non-JSON input
        w,catalog=self.memory_catalog()
        lookalike=SimpleNamespace(descriptors=catalog.descriptors,versions=catalog.versions)
        with self.refused('load_catalog result required'):spawn_catalog.validate_catalog(lookalike)

    def test_named_catalog_versions_1(self):
        w,catalog=self.memory_catalog()
        catalog.versions['extra']={}
        with self.refused('missing or unknown fields'):spawn_catalog.validate_catalog(catalog)

    def test_catalog_L84_8(self):
        w,catalog=self.memory_catalog()
        catalog.versions['platform']=[]
        with self.refused('invalid version section'):spawn_catalog.validate_catalog(catalog)

    def test_catalog_L85_4(self):
        # non-JSON input
        w,catalog=self.memory_catalog()
        catalog.descriptors=collections.OrderedDict(catalog.descriptors)
        with self.refused('invalid descriptors'):spawn_catalog.validate_catalog(catalog)

    def test_catalog_L99_4(self):
        root=self.work/'rootless';root.mkdir()
        (root/'versions.yml').write_text(yaml.safe_dump({'runtime':{},'connectors':{}}))
        with self.refused('catalog root required'):load_catalog(root)

    def test_catalog_L103_8(self):
        w=self.world(repository=False)
        other=dict(w.connector,pin_ref='connectors.other')
        root=self.write_catalog('misnamed',{'other':other},{'runtime':{},'connectors':{}},files={'other':'alias'})
        with self.refused('one matching name per catalog descriptor required'):load_catalog(root)

    # ---- catalog.py: pins -----------------------------------------------
    def test_catalog_L114_4(self):
        w,catalog=self.memory_catalog()
        del catalog.versions['artifacts']['node']
        with self.refused('catalog pin missing'):spawn_catalog.pin(catalog,'node')

    def test_catalog_L116_8(self):
        w,catalog=self.memory_catalog()
        catalog.versions['connectors']['example-connector']='main'
        with self.refused('full nonzero connector commit required'):
            spawn_catalog.pin(catalog,'example-connector')

    def test_named_catalog_pin_3(self):
        w,catalog=self.memory_catalog()
        catalog.versions['artifacts']['node']['publisher']='unlisted'
        with self.refused('missing or unknown fields'):spawn_catalog.pin(catalog,'node')

    def test_named_catalog_pin_4(self):
        w,catalog=self.memory_catalog()
        catalog.versions['artifacts']['node']['archive']='elsewhere/node.tar.gz'
        with self.refused('archive must be under inputs'):spawn_catalog.pin(catalog,'node')

    def test_catalog_L125_12(self):
        w,catalog=self.memory_catalog()
        catalog.versions['artifacts']['node']['sha256']='0'*64
        with self.refused('nonzero artifact digest required'):spawn_catalog.pin(catalog,'node')

    def test_named_catalog_pin_6(self):
        w,catalog=self.memory_catalog()
        catalog.versions['artifacts']['node']['descriptor_sha256']='b'*64
        with self.refused('descriptor digest mismatch'):spawn_catalog.pin(catalog,'node')

    def test_catalog_L128_12(self):
        w,catalog=self.memory_catalog()
        entry=catalog.descriptors['example-connector']
        catalog.versions['connectors']['example-connector']=dict(catalog.versions['artifacts']['node'],
            descriptor_sha256=descriptor_digest(entry),commit='0'*40)
        with self.refused('full nonzero connector commit required'):
            spawn_catalog.pin(catalog,'example-connector')

    # ---- catalog.py: selection ------------------------------------------
    def test_catalog_L136_8(self):
        w=self.world(repository=False)
        node=dict(w.node,requires=['ghost']);versions=deepcopy(w.versions)
        versions['artifacts']['node']['descriptor_sha256']=descriptor_digest(node)
        root=self.write_catalog('dangling',{'example-connector':w.connector,'node':node},versions)
        self.assertEqual(self.contract(self.request(w,catalog=str(root))),REFUSED)
        with self.refused('unknown catalog selection'):
            spawn_catalog.select(load_catalog(root),['example-connector'],['node'],[])

    def test_catalog_L137_8(self):
        # non-JSON input
        # A one-shot dependency listing lets the walk end; a JSON list re-enters the cycle forever.
        top={'kind':'runtime','pin_ref':'artifacts.top','requires':['loop']}
        loop={'kind':'runtime','pin_ref':'artifacts.loop','requires':iter(['loop','ghost'])}
        catalog=RoleCatalog({'top':top,'loop':loop},{'runtime':{},'connectors':{}})
        with self.refused('catalog dependency cycle'):spawn_catalog.select(catalog,[],['top'],[])

    def test_catalog_L154_12(self):
        w,catalog=self.memory_catalog()
        with self.refused('catalog selection kind mismatch'):spawn_catalog.select(catalog,['node'],[],[])

    # ---- catalog.py: local stores and payloads --------------------------
    def test_catalog_L161_4(self):
        w=self.world(repository=False)
        self.assertEqual(self.contract(self.request(w,stores=[])),REFUSED)
        with self.refused('local object-store map required'):spawn_catalog.local_roots([])

    def test_catalog_L163_8(self):
        with self.refused('invalid local-store pin_ref'):
            spawn_catalog.local_roots({'elsewhere.node':'/fixture/store'})

    def test_named_artifact_payload_path_1(self):
        w=self.world(repository=False)
        self.assertEqual(spawn_catalog.payload(str(w.payload_root),'inputs/node.tar.gz'),w.archive.read_bytes())
        with self.refused('absolute literal path required'):
            spawn_catalog.payload(str(w.payload_root)+'/','inputs/node.tar.gz')

    def test_named_artifact_payload_path_2(self):
        w=self.world(repository=False)
        (w.payload_root/'inputs/node archive.bin').write_bytes(b'fixture')
        with self.refused('literal contained artifact path required'):
            spawn_catalog.payload(str(w.payload_root),'inputs/node archive.bin')

    def test_catalog_L174_8(self):
        w=self.world(repository=False)
        (w.payload_root/'inputs/alias.tar.gz').symlink_to('node.tar.gz')
        with self.refused('symlinked payload path refused'):
            spawn_catalog.payload(str(w.payload_root),'inputs/alias.tar.gz')

    def test_catalog_L175_4(self):
        w=self.world(repository=False)
        outside=self.work/'outside';outside.mkdir();(outside/'node.tar.gz').write_bytes(b'outside the root')
        real=Path.is_symlink;swapped=[]
        def racing(path):
            # Every component is honestly link-free when inspected; the directory is
            # replaced by a link only after the last inspection (the root directory).
            result=real(path)
            if path==Path('/') and not swapped:
                swapped.append(str(path))
                w.archive.unlink();w.archive.parent.rmdir();w.archive.parent.symlink_to(outside)
            return result
        with patch.object(Path,'is_symlink',racing):
            with self.refused('payload escapes root'):
                spawn_catalog.payload(str(w.payload_root),'inputs/node.tar.gz')
        self.assertEqual(swapped,['/'])

    def test_catalog_L183_4(self):
        w=self.world(repository=False)
        stores={k:v for k,v in w.stores.items() if k!='connectors.example-connector'}
        self.assertEqual(self.contract(self.request(w,stores=stores)),REFUSED)
        with self.refused('selected artifact needs an explicit local store'):
            spawn_catalog.resolve(w.catalog,'example-connector',stores)

    def test_catalog_L204_4(self):
        w=self.world();catalog=deepcopy(w.catalog)
        del catalog.versions['runtime']['cook']
        with self.refused('role runtime pins missing'):spawn_catalog.resolve_runtime(catalog,w.stores)

    def test_catalog_L208_8(self):
        w=self.world();catalog=deepcopy(w.catalog)
        catalog.versions['runtime']['Bad.Name']=w.commit
        stores=dict(w.stores,**{'runtime.Bad.Name':str(w.repo)})
        with self.refused('invalid runtime name'):spawn_catalog.resolve_runtime(catalog,stores)

    def test_catalog_L210_8(self):
        w=self.world()
        stores={k:v for k,v in w.stores.items() if k!='runtime.cook'}
        self.assertEqual(self.contract(self.request(w,stores=stores)),REFUSED)
        with self.refused('runtime pin needs an explicit local object store'):
            spawn_catalog.resolve_runtime(w.catalog,stores)

    # ---- planner.py ------------------------------------------------------
    def test_planner_L42_12(self):
        with self.refused('unbound build directory in apply input'):
            require_bound({'paths':{'__CONNECTOME_BUILD_DIR__/catalog/node':1}})

    def test_planner_L58_8(self):
        with self.refused('catalog must use artifact references, not build tokens'):
            planner.expand({'args':['__CONNECTOME_BUILD_DIR__/catalog/node/index.js']},{'node'})

    def test_planner_L61_12(self):
        with self.refused('artifact reference is not selected or required'):
            planner.expand({'args':['{artifact:bun}/index.js']},{'node'})

    def test_named_catalog_reference_2(self):
        self.assertEqual(planner.expand({'command':'{artifact:node}/bin/node','args':['{artifact:tool}/index.js']},
                                        {'node','tool'}),
                         {'command':'__CONNECTOME_BUILD_DIR__/catalog/node/bin/node',
                          'args':['__CONNECTOME_BUILD_DIR__/catalog/tool/index.js']})

    def test_planner_L64_8(self):
        with self.refused('malformed artifact reference'):
            planner.expand({'args':['{artifact:Node}/index.js']},{'node'})

    def test_named_catalog_reference_4(self):
        with self.refused('artifact path escape'):
            planner.expand({'args':['{artifact:node}/../escape']},{'node'})

    def test_named_catalog_reference_5(self):
        with self.refused('artifact path escape'):
            planner.expand({'args':['{artifact:node}\\escape']},{'node'})

    def test_planner_L80_4(self):
        w=self.world()
        with self.refused('connector requires supported native launch metadata'):
            self.plan(w,catalog=self.with_settings(w,workingDirectory='elsewhere'))

    def test_planner_L83_4(self):
        w=self.world()
        with self.refused('invalid connector args'):
            self.plan(w,catalog=self.with_settings(w,args='index.js'))

    def test_planner_L87_8(self):
        w=self.world()
        with self.refused('connector token must reference a declared secret name'):
            self.plan(w,catalog=self.with_settings(w,token='literal-credential-value'))

    def test_planner_L90_4(self):
        w=self.world()
        with self.refused('invalid connector env'):
            self.plan(w,catalog=self.with_settings(w,env={'lower-case':'value'}))

    def test_planner_L94_12(self):
        w=self.world()
        with self.refused('secret values forbidden in recipe'):
            self.plan(w,catalog=self.with_settings(w,env={'EXAMPLE_TOKEN':'literal-credential-value'}))

    def test_planner_L99_12(self):
        w=self.world()
        entry,versions,stores=self.archive_connector(w,{'command':'{artifact:node}/bin/node',
            'args':['{artifact:example-connector}/index.js'],'data_args':['--data-dir']})
        root=self.write_catalog('staged',{'example-connector':entry,'node':w.node},versions)
        self.assertEqual(self.contract(self.request(w,catalog=str(root),stores=stores)),REFUSED)
        with self.refused('data_args must be a mapping'):
            self.plan(w,catalog=load_catalog(root),stores=stores)

    def test_planner_L108_8(self):
        w=self.world()
        with self.refused('legacy source connector cannot reference staged artifacts'):
            self.plan(w,catalog=self.with_settings(w,args=['{artifact:node}/index.js']))

    def test_planner_L132_4(self):
        w=self.world()
        with self.refused('invalid observed deployment hash'):
            self.plan(w,observed={'deploymentHash':'not-a-digest'})

    def test_planner_L134_4(self):
        w=self.world();declaration=deepcopy(w.declaration)
        declaration['home']['transport']='node'
        with self.refused('home transport not selected'):self.plan(w,declaration=declaration)

    def test_planner_L148_8(self):
        w=self.world();catalog=deepcopy(w.catalog)
        catalog.versions['platform']={'/usr/bin/fixture-tool':'0'*64}
        with self.refused('invalid platform pin'):self.plan(w,catalog=catalog)

    # ---- sources.py: strict local reader --------------------------------
    def test_sources_L41_4(self):
        (self.work/'sub').mkdir();(self.work/'input.txt').write_bytes(b'fixture')
        with self.refused('path traversal refused'):
            sources.read_file(str(self.work)+'/sub/../input.txt',root=self.work)

    def test_sources_L42_4(self):
        target=self.work/'input.txt';target.write_bytes(b'fixture')
        with self.refused('input escapes root'):sources.read_file(target,root=target)

    def test_sources_L55_12(self):
        pipe=self.work/'pipe';os.mkfifo(pipe)
        with self.refused('regular input file required'):sources.read_file(pipe)

    def test_sources_L56_12(self):
        target=self.work/'input.txt';target.write_bytes(b'fixture');os.link(target,self.work/'second-name.txt')
        with self.refused('single-link input file required'):sources.read_file(target)

    def test_sources_L58_12(self):
        target=self.work/'input.txt';target.write_bytes(b'fixture')
        real=os.fstat;removed=[]
        def racing(descriptor):
            # The opened file loses its name right after its metadata was taken.
            result=real(descriptor)
            if not removed:removed.append(result.st_nlink);target.unlink()
            return result
        with patch.object(sources.os,'fstat',side_effect=racing):
            with self.refused('input escapes root'):sources.read_file(target)
        self.assertEqual(removed,[1])

    def test_sources_L61_12(self):
        target=self.work/'input.txt';target.write_bytes(b'0123456789')
        self.assertEqual(sources.read_file(target,10),b'0123456789')
        with self.refused('input size limit exceeded'):sources.read_file(target,4)

    # ---- sources.py: strict git reader and commit pins ------------------
    def test_sources_L110_12(self):
        w=self.world()
        with self.refused('missing or oversized local git object'):
            sources.git(str(w.repo),'cat-file','blob','f'*40)

    def test_sources_L118_8(self):
        with self.refused('unreadable local repository or git unavailable'):
            sources.git(str(self.work/'absent'),'rev-parse','--is-bare-repository')

    def test_sources_L125_4(self):
        w=self.world()
        with self.refused('full nonzero commit required'):sources.commit_tree(str(w.repo),'HEAD')

    def test_sources_L130_4(self):
        w=self.world()
        git(w.repo,'tag','-a','fixture-tag','-m','Fixture tag',w.commit)
        tag=git(w.repo,'rev-parse','refs/tags/fixture-tag')
        self.assertNotEqual(tag,w.commit)
        with self.refused('exact commit object required'):sources.commit_tree(str(w.repo),tag)

    def test_sources_L131_4(self):
        w=self.world();real=subprocess.Popen;asked=[]
        def popen(argv,**options):
            # The object store answers the type query for the pin with another object's type.
            if argv[-3:-1]==['cat-file','-t']:
                asked.append(argv[-1]);argv=argv[:-1]+[w.tree]
            return real(argv,**options)
        with patch.object(sources.subprocess,'Popen',side_effect=popen):
            with self.refused('commit object required'):sources.commit_tree(str(w.repo),w.commit)
        self.assertEqual(asked,[w.commit])

    def test_sources_L134_4(self):
        w=self.world()
        odd=loose_commit(w.repo,b'tree '+w.tree.upper().encode()+b'\n'+AUTHOR+COMMITTER+b'\nUppercase tree id\n')
        self.assertEqual(git(w.repo,'rev-parse',odd+'^{tree}'),w.tree)
        with self.refused('invalid commit tree'):sources.commit_tree(str(w.repo),odd)

    def test_sources_L162_4(self):
        w=self.world()
        self.assertEqual(sources.blob(str(w.repo),w.link_commit,'template.yml'),w.template_bytes)
        with self.refused('pinned regular blob required (no symlinks or submodules)'):
            sources.blob(str(w.repo),w.link_commit,'link.yml')

    def test_sources_L175_8(self):
        w=self.world()
        twin=loose_commit(w.repo,b'tree '+w.tree.encode()+b'\n'+AUTHOR+AUTHOR.replace(b'Fixture Author',b'Second Author')
                          +COMMITTER+b'\nTwo authors\n')
        with self.refused('commit author and committer required'):
            sources.pinned_blob(str(w.repo),twin,'template.yml')

    def test_sources_L179_12(self):
        w=self.world()
        odd=loose_commit(w.repo,b'tree '+w.tree.encode()+b'\n'+AUTHOR.replace(b'Fixture Author',b'Fixture \xff Author')
                         +COMMITTER+b'\nUndecodable author\n')
        with self.refused('invalid commit provenance'):sources.pinned_blob(str(w.repo),odd,'template.yml')

    # ---- sources.py: artifact records ------------------------------------
    def test_named_artifact_provenance_1(self):
        w=self.world()
        with self.refused('missing or unknown fields'):
            sources.resolve_artifact({'repo':str(w.repo),'commit':w.commit,'sha256':'a'*64})

    def test_named_artifact_provenance_2(self):
        w=self.world()
        entry={'repo':str(w.repo),'commit':w.commit,'path':'payload.bin'}
        self.assertEqual(sources.resolve_artifact(entry),dict(entry,kind='blob',size=len(w.payload_bytes),
                         sha256=hashlib.sha256(w.payload_bytes).hexdigest()))

    def test_named_artifact_provenance_3(self):
        w=self.world()
        entry={'repo':str(w.repo),'commit':w.commit}
        files=[{'path':path,'mode':'100644','size':len(data),'sha256':hashlib.sha256(data).hexdigest()}
               for path,data in (('payload.bin',w.payload_bytes),('template.yml',w.template_bytes))]
        # Independent canonical encoding of the file inventory.
        expected=hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(sources.resolve_artifact(entry),dict(entry,kind='git-tree',tree=w.tree,
                         size=len(w.payload_bytes)+len(w.template_bytes),files=files,sha256=expected))

    def test_sources_L220_8(self):
        w=self.world();real=sources.git;bounds=[]
        def overflowing(repo,*args,limit=sources.LIMIT):
            # A blob reader that hands back one byte more than the bound it was given.
            if args[:2]==('cat-file','blob'):
                bounds.append(limit);return b'\0'*(limit+1)
            return real(repo,*args,limit=limit)
        with patch.object(sources,'git',side_effect=overflowing):
            with self.refused('artifact size limit exceeded'):
                sources.resolve_artifact({'repo':str(w.repo),'commit':w.commit})
        self.assertEqual(bounds,[sources.ARTIFACT_LIMIT])


if __name__=='__main__':unittest.main()
