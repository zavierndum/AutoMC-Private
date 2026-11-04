"""
GT-5 (devops/AutoMC_General_Tools_Plan.md): PythonRunTool with a real Jupyter kernel. The tool only plans; a run needs the user's
approval through a surface and the unchanged code; variables persist between runs of one conversation and not across conversations; the
kernel has no keys; errors, figures, written files and the whole output are kept; a long output is shortened at both ends to a share
of the model's window; a run past its time limit is interrupted and the kernel kept; the output carries no label unless the user typed
'yes, send'. Needs jupyter_client and ipykernel.
"""
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import automc_paths
from tools import conversation, registry
from tools.files.places import Refused
from tools.python import run

HAVE_KERNEL = all(importlib.util.find_spec(m) is not None for m in ("jupyter_client", "ipykernel"))
HAVE_MPL = importlib.util.find_spec("matplotlib") is not None
FAKE_KEY = "sk-test-not-a-real-key-0123456789"


class Plans(unittest.TestCase):
    def test_the_tool_only_plans_and_warns(self):
        out = registry.call("PythonRunTool", {"code": "import openmc\nopenmc.run()\nimport subprocess, requests, shutil\nshutil.rmtree('x')"})
        self.assertTrue(out["ok"], out)
        self.assertEqual((out["started"], out["needs_approval"], out["facility_data"]), (False, True, "public"))
        warnings = " ".join(out["plan"]["warnings"])
        for phrase in ("bypasses the job service", "starts other programs", "reach the network", "deletes files"):
            self.assertIn(phrase, warnings)
        self.assertIn("2 | openmc.run()", out["plan"]["text"])
        self.assertEqual(registry.call("PythonRunTool", {"code": "print(1)"})["plan"]["warnings"], [])
        for bad in ({"code": " "}, {"code": "x", "timeout_s": 0}, {"code": "x", "timeout_s": 99999}):
            self.assertFalse(registry.call("PythonRunTool", bad)["ok"])

    def test_typed_input_is_warned_about(self):
        for code in ("n = int(input('a number from 1 to 10: '))", "import getpass\nkey = getpass.getpass()"):
            warnings = " ".join(registry.call("PythonRunTool", {"code": code})["plan"]["warnings"])
            self.assertIn("typed input", warnings, code)
        for code in ("user_input(3)", "widget.input(3)", "inputs = [1, 2]"):
            self.assertEqual(registry.call("PythonRunTool", {"code": code})["plan"]["warnings"], [], code)

    def test_what_the_user_sees_of_a_withheld_output(self):
        result = {"run": 3, "status": "error", "seconds": 0.4, "output": "rows\n" + "x" * 9000, "error": "ZeroDivisionError: division by zero",
                  "output_file": f"{run.FOLDER}/t1/runs/run-003.txt", "figures": [f"{run.FOLDER}/t1/figure-003-1.png"]}
        text = run.for_the_user(result)
        self.assertTrue(text.startswith(f"run 3: error in 0.4 s; the whole output is in workspace/{run.FOLDER}/t1/runs/run-003.txt"))
        self.assertIn("rows", text)
        self.assertIn("characters left out here", text)                                    # long: its start and end only
        self.assertLess(len(text), run.SHOWN_CHARS + 600)
        self.assertIn("error: ZeroDivisionError", text)
        self.assertIn(f"figures: workspace/{run.FOLDER}/t1/figure-003-1.png", text)
        self.assertIn("(no output)", run.for_the_user({"run": 1, "status": "ok", "seconds": 0.1, "output": "", "output_file": "f"}))

    def test_the_output_limit_follows_the_window(self):
        self.assertEqual(run.output_limit(None), 10_000)
        self.assertTrue(10_000 <= run.output_limit(32_768) <= 10_500)
        self.assertEqual(run.output_limit(131_072), run.MAX_CHARS)
        self.assertEqual(run.output_limit(8_192), run.MIN_CHARS)
        text = "A" * 3000 + "M" * 20_000 + "Z" * 8000
        shown, left = run._shorten(text, 10_000)
        self.assertTrue(shown.startswith("A" * 2000) and shown.endswith("Z" * 8000))
        self.assertEqual(left, len(text) - 10_000)
        self.assertIn(f"{left:,} characters left out", shown)


@unittest.skipUnless(HAVE_KERNEL, "needs jupyter_client and ipykernel")
class Running(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="automc_py_")).resolve()
        self.patches = [mock.patch.object(automc_paths, "WORKSPACE", self.ws), mock.patch.dict(os.environ)]
        for p in self.patches:
            p.start()
        os.environ.update(OPENAI_API_KEY=FAKE_KEY, AUTOMC_ENV_FILE="/somewhere/keys.env", Host_Key="hunter2")

    def tearDown(self):
        run.shutdown()
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.ws, ignore_errors=True)

    def go(self, code, thread="t1", timeout=60, send=False, window=None):
        with conversation.inside(None, "public", thread, window):
            plan = run.plan_run(code, timeout)
        return run.start(plan, approved_by="test", send=send)

    def test_a_run_needs_an_approval_and_the_code_the_user_saw(self):
        with conversation.inside(None, "public", "t1"):
            plan = run.plan_run("print(1)")
        with self.assertRaises(Refused):
            run.start(plan, approved_by="")
        with self.assertRaises(Refused):
            run.start({**plan, "code": "print(2)"}, approved_by="test")

    def test_variables_persist_per_conversation_and_the_kernel_has_no_keys(self):
        self.go("x = 41")
        out = self.go("import os\nprint(x + 1)\nprint(sorted(k for k in os.environ if 'KEY' in k.upper() or k == 'AUTOMC_ENV_FILE'))\n"
                      "print(os.environ.get('MPLBACKEND'), os.getcwd())")
        self.assertEqual(out["status"], "ok", out)
        lines = out["output"].splitlines()
        self.assertEqual(lines[:2], ["42", "[]"])
        self.assertEqual(lines[2], f"Agg {self.ws / run.FOLDER / 't1'}")
        other = self.go("print(x)", thread="t2")
        self.assertEqual((other["status"], other["error"]), ("error", "NameError: name 'x' is not defined"))
        self.assertNotIn(FAKE_KEY, json.dumps(out))

    def test_errors_files_and_the_whole_output_are_kept(self):
        out = self.go("open('table.csv', 'w').write('a,b\\n1,2\\n')\nprint('rows')\n1 / 0")
        self.assertEqual((out["status"], out["error"]), ("error", "ZeroDivisionError: division by zero"))
        self.assertIn("ZeroDivisionError", out["output"])
        self.assertEqual(out["files_written"], [f"{run.FOLDER}/t1/table.csv"])
        self.assertTrue((self.ws / out["output_file"]).read_text().startswith("rows"))
        self.assertTrue((self.ws / out["code_file"]).read_text().startswith("open("))
        self.assertNotIn("facility_data", out)                                              # no label: the code can read anything
        self.assertEqual(self.go("print(2)", send=True)["facility_data"], "public")          # the user typed 'yes, send'

    def test_a_long_output_is_shortened_to_the_windows_share_and_kept_whole(self):
        out = self.go("print('HEAD'); print('x' * 50000); print('TAIL')", window=32_768)
        self.assertEqual(out["output_chars"], 50_011)                                       # HEAD\n, 50,000 x and \n, TAIL\n
        self.assertLess(len(out["output"]), 10_600)
        self.assertTrue(out["output"].startswith("HEAD") and out["output"].rstrip().endswith("TAIL"))
        self.assertIn("left out of the middle", out["cut"])
        self.assertEqual(len((self.ws / out["output_file"]).read_text()), 50_011)

    def test_a_run_past_its_limit_is_interrupted_and_the_kernel_kept(self):
        self.go("y = 7")
        out = self.go("import time\ntime.sleep(60)", timeout=2)
        self.assertEqual(out["status"], "timed_out")
        if "restarted" in out["timed_out"]:                  # Windows: a sleeping kernel may not take the interrupt, and is restarted
            self.assertNotEqual(os.name, "posix")
            self.assertIn("variables are gone", out["timed_out"])
            self.assertEqual(self.go("print(y)")["status"], "error")
        else:
            self.assertIn("variables are kept", out["timed_out"])
            self.assertEqual(self.go("print(y)")["output"].strip(), "7")

    @unittest.skipUnless(HAVE_MPL, "needs matplotlib")
    def test_a_figure_is_saved_as_a_png(self):
        out = self.go("import matplotlib.pyplot as plt\nplt.plot([1, 2, 3], [1, 4, 9])\nplt.title('k-eff')")
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["figures"], [f"{run.FOLDER}/t1/figure-{out['run']:03d}-1.png"])
        self.assertTrue((self.ws / out["figures"][0]).read_bytes().startswith(b"\x89PNG"))


@unittest.skipUnless(HAVE_KERNEL and importlib.util.find_spec("langgraph") is not None, "needs a kernel and LangGraph")
class Agent(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="automc_pyagent_")).resolve()
        self.p = mock.patch.object(automc_paths, "WORKSPACE", self.ws)
        self.p.start()

    def tearDown(self):
        run.shutdown()
        self.p.stop()
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_the_chat_asks_and_only_yes_runs(self):
        from langchain_core.messages import AIMessage, ToolMessage

        import automc_core
        from tests.agent.test_core import LOCAL, Scripted, call
        for yes in (False, True):
            model = Scripted(replies=[call("PythonRunTool", {"code": "print(6 * 7)"}), AIMessage(content="done")], seen=[])
            s = automc_core.Session(provider=LOCAL, model=model, audit=False)
            evs = list(s.send("what is six times seven? compute it"))
            self.assertEqual((evs[-1][0], evs[-1][1]["kind"], evs[-1][1]["send_option"]), ("approval", "python_approval", False))
            self.assertIn("print(6 * 7)", evs[-1][1]["plan_text"])
            list(s.answer(yes))
            result = json.loads([m for m in s.messages() if isinstance(m, ToolMessage)][-1].content)
            if yes:
                self.assertEqual((result["started"], result["output"].strip()), (True, "42"))
            else:
                self.assertTrue(result["declined"])
                self.assertFalse((self.ws / run.FOLDER).exists() and any((self.ws / run.FOLDER).rglob("run-*.txt")))

    def test_output_withheld_from_a_cloud_model_is_shown_to_the_user(self):
        from langchain_core.messages import AIMessage, ToolMessage

        import automc_core
        from agent import guard
        from agent.chat import ChatUI
        from tests.agent.test_core import CLOUD, Scripted, call
        model = Scripted(replies=[call("PythonRunTool", {"code": "print('product', 6 * 7)"}), AIMessage(content="shown to you")], seen=[])
        with mock.patch.object(guard, "restricted_pack_installed", return_value=True):
            s = automc_core.Session(provider=CLOUD, model=model, audit=False)
            evs = list(s.send("what is six times seven? compute it"))
            self.assertTrue(evs[-1][1]["send_option"])
            after = list(s.answer(True))                                               # 'y', not 'yes, send'
            shown = [e for e in after if e[0] == "user_output"]
            self.assertEqual(len(shown), 1)
            self.assertIn("\nproduct 42", shown[0][2])
            self.assertIn(f"workspace/{run.FOLDER}/", shown[0][2])
            msg = [m for m in s.messages() if isinstance(m, ToolMessage)][-1]
            seen = json.loads(msg.content)
            self.assertTrue(seen["withheld"])
            self.assertIn("The user has seen this output in the chat; you have not", seen["tell_the_user"])
            self.assertNotIn("product", msg.content)                                   # the model's copy holds no output
            sent = model.seen[-1][-1]                                                  # the result as the next request carried it
            self.assertIsInstance(sent, ToolMessage)
            self.assertNotIn("product", sent.content)
            out = io.StringIO()
            ChatUI(s, out=out).run_events(iter(after))
            self.assertIn("shown to you only (not to the cloud model)", out.getvalue())
            self.assertIn("    product 42", out.getvalue())


if __name__ == "__main__":
    unittest.main()
