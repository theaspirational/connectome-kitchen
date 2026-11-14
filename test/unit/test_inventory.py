import importlib.machinery,json,shutil,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
loader=importlib.machinery.SourceFileLoader('sites',str(ROOT/'scripts/inventory-sites'));sites=loader.load_module()
class InventoryTests(unittest.TestCase):
 def test_all_rows_and_valid_ranges(self):
  rows=sites.inventory(ROOT/'docs/approved-plan.md');self.assertEqual([r['row'] for r in rows],list(range(1,75)))
  self.assertEqual([r['row'] for r in rows if r['future']],[14,43,44,45,46])
 def test_globs_new_engine_and_boundary_modules(self):
  scratch=ROOT/'.cache/inventory';scratch.mkdir(parents=True,exist_ok=True)
  for prefix in ('module_utils/new_package','library','lookup_plugins','filter_plugins','scripts'):
   folder=scratch/prefix;folder.mkdir(parents=True,exist_ok=True);(folder/'new_module.py').write_text("raise ValueError('refused')\n")
  files=sites.files(scratch);self.assertEqual(len(files),5)
  for file in files.values():self.assertEqual(sites.sites(file.read_text()),[1])
if __name__=='__main__':unittest.main()
