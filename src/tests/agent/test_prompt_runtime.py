"""GT-6: the system prompt's session block names the workspace, AutoMC's folder, the folders the file tools may read and the knowledge
bases; a cloud model is told only the public knowledge bases (and how many others there are), never a restricted one's name."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import automc_paths
from agent import prompt
from agent.llm import Provider
from tools.documents import parse
from tools.openmc import jobs

LOCAL = Provider(name="test", kind="local", backend="ollama", base_url="x", model="m")
CLOUD = Provider(name="test-cloud", kind="cloud", account="personal", backend="openai_compatible", base_url="x", model="m")


class RuntimeBlock(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="automc_prompt_")).resolve()
        self.p = mock.patch.object(automc_paths, "WORKSPACE", self.ws)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        shutil.rmtree(self.ws, ignore_errors=True)

    def kb(self, name, label):
        folder = self.ws / parse.DOCS / name
        folder.mkdir(parents=True)
        jobs.atomic_write_json(folder / parse.RECORD, {"kb": name, "label": label, "files": {"a.pdf": {}}, "updated_at": jobs.now()})

    def test_it_names_the_folders(self):
        block = prompt.runtime_block(LOCAL)
        self.assertIn(f"Workspace: {self.ws}", block)
        self.assertIn(f"AutoMC: {automc_paths.AUTOMC_ROOT.resolve()}", block)
        self.assertIn("Folders the file tools may read, with their labels: ", block)
        self.assertIn("Knowledge bases: none yet", block)

    def test_a_cloud_model_is_told_only_the_public_knowledge_bases(self):
        self.kb("osti-reports", "public")
        self.kb("netl-sar-secret", "restricted")
        local, cloud = prompt.runtime_block(LOCAL), prompt.runtime_block(CLOUD)
        self.assertIn("netl-sar-secret (restricted, 1 files)", local)
        self.assertIn("osti-reports (public, 1 files)", cloud)
        self.assertNotIn("netl-sar-secret", cloud)
        self.assertIn("1 more that a cloud model may not read", cloud)


if __name__ == "__main__":
    unittest.main()
