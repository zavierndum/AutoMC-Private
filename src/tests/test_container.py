"""
Deployment (WD-5, devops/AutoMC_Workspace_Plan.md): OLLAMA_HOST points every local provider at another Ollama (the host's, from a
container); python.approval: auto lets Python run without asking only inside a container (W2), the run still shown and saved; the image
check finds a restricted pack, devops/, a workspace, keys and personal identifiers; .dockerignore lets in only what the image may hold.
Nothing here needs Docker.
"""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import automc_paths
import automc_settings

HAVE_YAML = importlib.util.find_spec("yaml") is not None
HAVE_KERNEL = all(importlib.util.find_spec(m) is not None for m in ("jupyter_client", "ipykernel", "langgraph"))


def check_image_module():
    spec = importlib.util.spec_from_file_location("check_image", automc_paths.AUTOMC_ROOT / "docker" / "check_image.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(HAVE_YAML, "needs PyYAML")
class Ollama(unittest.TestCase):
    def test_ollama_host_moves_every_local_provider_and_no_cloud_one(self):
        from agent import llm
        with mock.patch.dict(os.environ, {"OLLAMA_HOST": "host.docker.internal:11434"}):
            self.assertEqual(llm.provider("local", require_ready=False).base_url, "http://host.docker.internal:11434")
            self.assertEqual(llm.provider("local-128k", require_ready=False).base_url, "http://host.docker.internal:11434")
            self.assertEqual(llm.provider("openai-portkey", require_ready=False).base_url, "https://api.portkey.ai/v1")
        with mock.patch.dict(os.environ, {"OLLAMA_HOST": "https://ollama.example:8443/"}):
            self.assertEqual(llm.provider("local", require_ready=False).base_url, "https://ollama.example:8443")
        with mock.patch.dict(os.environ):
            os.environ.pop("OLLAMA_HOST", None)
            self.assertEqual(llm.provider("local", require_ready=False).base_url, "http://127.0.0.1:11434")


@unittest.skipUnless(HAVE_YAML, "needs PyYAML")
class PythonPolicy(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="automc_container_")).resolve()
        self.patches = [mock.patch.object(automc_paths, "WORKSPACE", self.ws), mock.patch.dict(os.environ),
                        mock.patch.object(automc_settings, "in_container",                    # the variable alone decides here, even
                                          side_effect=lambda: os.environ.get(automc_settings.CONTAINER_ENV) == "1")]   # in the image
        for p in self.patches:
            p.start()
        os.environ[automc_settings.USER_ENV] = str(self.ws / "none.yaml")
        os.environ.pop(automc_settings.CONTAINER_ENV, None)
        automc_settings._CACHE.clear()

    def tearDown(self):
        from tools.python import run
        run.shutdown()
        for p in reversed(self.patches):
            p.stop()
        automc_settings._CACHE.clear()
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_auto_counts_only_inside_a_container(self):
        self.assertFalse(automc_settings.python_runs_without_asking())                       # ask, outside
        automc_settings.write({"python": {"approval": "auto"}})
        self.assertFalse(automc_settings.python_runs_without_asking())                       # auto, outside: still asks
        rows = {k: v for k, v, _ in automc_settings.effective()[0]}
        self.assertIn("not in a container", rows["python.approval"])
        os.environ[automc_settings.CONTAINER_ENV] = "1"
        self.assertTrue(automc_settings.python_runs_without_asking())
        automc_settings.write({"python": {"approval": "ask"}})
        self.assertFalse(automc_settings.python_runs_without_asking())

    @unittest.skipUnless(HAVE_KERNEL, "needs jupyter_client, ipykernel and LangGraph")
    def test_in_a_container_with_auto_python_runs_and_says_so(self):
        from langchain_core.messages import AIMessage, ToolMessage

        import automc_core
        from tests.agent.test_core import LOCAL, Scripted, call
        automc_settings.write({"python": {"approval": "auto"}})
        os.environ[automc_settings.CONTAINER_ENV] = "1"
        model = Scripted(replies=[call("PythonRunTool", {"code": "print('sum', 2 + 3)"}), AIMessage(content="5")], seen=[])
        s = automc_core.Session(provider=LOCAL, model=model, audit=True)
        evs = list(s.send("add 2 and 3 in Python"))
        self.assertFalse([e for e in evs if e[0] == "approval"])                             # not asked
        self.assertTrue(any(e[0] == "notice" and "without asking" in e[1] for e in evs))
        result = json.loads([m for m in s.messages() if isinstance(m, ToolMessage)][-1].content)
        self.assertEqual((result["started"], result["output"].strip()), (True, "sum 5"))
        self.assertIn("standing approval", result["approved_by"])
        audit = [json.loads(l) for l in s.chat.path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(l["event"] == "approval" and l.get("automatic") for l in audit))


@unittest.skipUnless((automc_paths.AUTOMC_ROOT / "docker" / "check_image.py").is_file(),
                     "the build files are not in the image (.dockerignore keeps them out)")
class ImageCheck(unittest.TestCase):
    def test_what_must_never_be_in_the_image_is_found(self):
        mod = check_image_module()
        clean = ["/opt/automc/src/automc.py", "/opt/automc/src/facility_packs/netl_public/pack.yaml", "/etc/ssl/certs/ca.pem"]
        bad = {"/opt/AutoMC_restricted/facility_packs/x.yaml": "restricted", "/opt/automc/src/facility_packs/netl_restricted/a": "NETL",
               "/opt/automc/devops/COMMANDS.md": "devops", "/opt/automc/workspace/c0/result.json": "a workspace",
               "/home/automc/.config/automc/keys.env": "keys file", "/opt/automc/src/.env": ".env", "/opt/automc/src/key.pem": "key",
               "/opt/automc/src/x/.automc/chats/1.jsonl": ".automc"}
        with mock.patch.object(mod, "files", return_value=clean), mock.patch.object(mod, "identifiers", return_value=[]):
            self.assertEqual(mod.check("img")[1], [])
        for path, why in bad.items():
            with self.subTest(path=path), mock.patch.object(mod, "files", return_value=clean + [path]), \
                    mock.patch.object(mod, "identifiers", return_value=[]):
                self.assertTrue(mod.check("img")[1], why)
        with mock.patch.object(mod, "files", return_value=clean), mock.patch.object(mod, "identifiers", return_value=["/opt/automc/src/x.py"]):
            self.assertIn("personal identifier", mod.check("img")[1][0])
        import re
        rx = re.compile("|".join(mod.IDENTIFIERS))
        # made up at run time, so this file holds none of them for the image check to find
        slug, key, eid = "@" + "mech-ab1234-ope-b26a21", "sk-" + "proj-" + "a" * 24, "zn" + "2366"
        for text, hit in ((f"openai-portkey/{slug}/gpt-5.4", True), ("sk-test-NEVER-PRINT-123456789", False), (key, True),
                          (f"by {eid}", True), ("eval fixture", False)):
            with self.subTest(text=text):
                self.assertEqual(bool(rx.search(text)), hit)

    def test_the_build_context_lets_in_only_what_the_image_may_hold(self):
        lines = [l.strip() for l in (automc_paths.AUTOMC_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.startswith("#")]
        self.assertEqual(lines[0], "*")
        allowed = {l[1:] for l in lines if l.startswith("!")}
        self.assertEqual(allowed, {"src/", "library_snapshots/", "docker/requirements.txt", "CHANGELOG.md"})
        dockerfile = (automc_paths.AUTOMC_ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("USER automc", dockerfile)
        self.assertIn("AUTOMC_IN_CONTAINER=1", dockerfile)
        import automc_version
        self.assertIn(f'org.opencontainers.image.version="{automc_version.VERSION}"', dockerfile)


if __name__ == "__main__":
    unittest.main()
