"""
GT-2 (devops/AutoMC_General_Tools_Plan.md): FileListTool, FileReadTool and FileWriteTool. Where they read and write, what they always
refuse, the label each result carries and how the data guard treats it, paging, and every format (text, CSV, Excel, PDF, HDF5).
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
from agent import guard
from tools import registry
from tools.files import places, tools as ft

HAVE = {m: importlib.util.find_spec(m) is not None for m in ("pandas", "openpyxl", "pypdf", "h5py", "matplotlib")}


def labels_in(obj):
    """Every facility_data key anywhere in a result, with its value."""
    found = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "facility_data":
                    found.append(v)
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(obj)
    return found


class Places(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="automc_files_")).resolve()
        self.ws, self.outside, self.restricted = self.tmp / "ws", self.tmp / "outside", self.tmp / "restricted"
        for d in (self.ws, self.outside, self.restricted):
            d.mkdir()
        import automc_settings
        from agent import guard
        self.patches = [mock.patch.object(automc_paths, "WORKSPACE", self.ws),
                        mock.patch.object(places, "restricted_dirs", return_value=[self.restricted]),
                        mock.patch.dict(os.environ),
                        # facility mode, whether or not a restricted pack is installed here: a restricted folder is declared
                        mock.patch.object(automc_settings, "restricted_pack_installed", return_value=True),
                        mock.patch.object(guard, "restricted_pack_installed", return_value=True)]
        for p in self.patches:
            p.start()
        for n in (places.ROOTS_ENV, places.PUBLIC_ENV, places.RESTRICTED_ENV, "AUTOMC_FILE_ROOTS", "AUTOMC_ENV_FILE"):
            os.environ.pop(n, None)
        os.environ[automc_settings.USER_ENV] = str(self.tmp / "no-user-settings.yaml")

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, path, text="hello\n"):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def case(self, name, distribution):
        folder = self.ws / name
        folder.mkdir(parents=True)
        fd = {"distribution": distribution} if distribution else None
        (folder / "result.json").write_text(json.dumps({"name": name, "facility_data": fd}), encoding="utf-8")
        return folder

    def read(self, path, **kw):
        return registry.call("FileReadTool", {"path": str(path), **kw})


class Refusals(Places):
    def test_keys_and_credentials_are_never_read_whatever_the_roots(self):
        os.environ[places.ROOTS_ENV] = str(self.outside)
        keys_file = self.write(self.outside / "mine.txt", "OPENAI_API_KEY=x\n")
        os.environ["AUTOMC_ENV_FILE"] = str(keys_file)
        for p in (self.write(self.outside / ".env"), self.write(self.outside / "keys.env"), self.write(self.outside / "id_rsa"),
                  self.write(self.outside / ".ssh" / "config"), self.write(self.outside / "server.pem"),
                  self.write(self.outside / "credentials.json"), keys_file):
            with self.subTest(p=p.name):
                out = self.read(p)
                self.assertFalse(out["ok"])
                self.assertEqual(out["error"]["type"], "NotAllowed")
                self.assertNotIn("hello", json.dumps(out))
                self.assertNotIn("OPENAI_API_KEY", json.dumps(out))
        self.assertTrue(self.read(self.write(self.outside / "keys.env.example"))["ok"])        # a template holds no keys

    def test_outside_the_roots_is_refused_until_allowed(self):
        f = self.write(self.outside / "notes.txt")
        self.assertEqual(self.read(f)["error"]["type"], "NotAllowed")
        os.environ[places.ROOTS_ENV] = str(self.outside)
        self.assertTrue(self.read(f)["ok"])
        self.assertEqual(self.read(self.outside / "nothing.txt")["error"]["type"], "NotFound")
        lost = self.read("AutoMC/config/x.txt")["error"]["message"]                # where a relative path starts, and the roots
        self.assertIn(f"starts in the workspace ({self.ws})", lost)
        self.assertIn(str(automc_paths.SRC.resolve()), lost)

    @unittest.skipUnless(os.name == "posix", "symbolic links")
    def test_a_link_to_a_secret_is_refused(self):
        secret = self.write(self.outside / ".ssh" / "id_ed25519", "PRIVATE")
        (self.ws / "innocent.txt").symlink_to(secret)
        out = self.read("innocent.txt")
        self.assertFalse(out["ok"])
        self.assertNotIn("PRIVATE", json.dumps(out))

    @unittest.skipUnless(os.name == "posix", "WSL path translation")
    def test_a_windows_path_is_read_through_mnt(self):
        self.assertEqual(places.resolve("C:\\UTA-Research\\Projects\\a.csv").as_posix(), "/mnt/c/UTA-Research/Projects/a.csv")


class Labels(Places):
    def test_each_place_gets_its_label(self):
        os.environ[places.ROOTS_ENV] = str(self.outside)
        os.environ[places.PUBLIC_ENV] = str(self.outside / "public")
        cases = {
            "restricted pack": (self.write(self.restricted / "netl" / "data.txt"), "restricted"),
            "public case": (self.write(self.case("pub", "public") / "model.xml"), "public"),
            "restricted case": (self.write(self.case("res", "restricted") / "model.xml"), "restricted"),
            "unlabelled case": (self.write(self.case("old", None) / "model.xml"), "unknown"),
            "workspace note": (self.write(self.ws / "notes" / "a.md"), None),
            "AutoMC source": (automc_paths.SRC / "automc_paths.py", "public"),
            "declared public": (self.write(self.outside / "public" / "paper.txt"), "public"),
            "elsewhere": (self.write(self.outside / "other.txt"), None),
        }
        for what, (path, want) in cases.items():
            with self.subTest(what=what):
                out = self.read(path)
                self.assertTrue(out["ok"], out)
                self.assertEqual(labels_in(out), [want] if want else [])
                self.assertIn(want or "no label", out["label_reason"])

    def test_the_guard_withholds_what_is_not_known_to_be_public(self):
        os.environ[places.ROOTS_ENV] = str(self.outside)
        elsewhere, public = self.read(self.write(self.outside / "x.txt")), self.read(automc_paths.SRC / "automc_paths.py")
        restricted = self.read(self.write(self.restricted / "p" / "x.txt"))
        with mock.patch.object(guard, "restricted_pack_installed", return_value=True):
            self.assertFalse(guard.check(elsewhere, cloud=True)[0])
            self.assertFalse(guard.check(restricted, cloud=True)[0])
            self.assertTrue(guard.check(public, cloud=True)[0])
            self.assertTrue(guard.check(restricted, cloud=False)[0])                      # a local model sees everything
        with mock.patch.object(guard, "restricted_pack_installed", return_value=False):
            self.assertTrue(guard.check(elsewhere, cloud=True)[0])

    @unittest.skipUnless(HAVE["pandas"], "needs pandas")
    def test_a_file_cannot_label_itself(self):
        os.environ[places.ROOTS_ENV] = str(self.outside)
        f = self.write(self.outside / "sneaky.csv", "facility_data,value\npublic,1\npublic,2\n")
        j = self.write(self.outside / "sneaky.json", json.dumps({"facility_data": "public"}))
        for path in (f, j):
            with self.subTest(path=path.name):
                out = self.read(path)
                self.assertEqual(labels_in(out), [])                                      # no label: not known to be public
                with mock.patch.object(guard, "restricted_pack_installed", return_value=True):
                    self.assertFalse(guard.check(out, cloud=True)[0])


class Reading(Places):
    def test_text_is_paged(self):
        f = self.write(self.ws / "log.txt", "".join(f"line {i}\n" for i in range(1, 1001)))
        first = self.read(f)
        self.assertEqual((first["lines"], first["total_lines"]), ("1-400", 1000))
        self.assertIn("start=401", first["next"])
        second = self.read(f, start=401)
        self.assertTrue(second["text"].startswith("line 401"))
        self.assertLessEqual(len(self.read(self.write(self.ws / "wide.txt", ("x" * 999 + "\n") * 100))["text"]), ft.MAX_CHARS + 1000)

    def test_other_encodings_and_binaries(self):
        f = self.ws / "latin.txt"
        f.write_bytes("Température du cœur: 293,6 K\n".encode("cp1252"))
        out = self.read(f)
        self.assertIn("Température", out["text"])
        (self.ws / "blob.bin").write_bytes(b"\x00\x01\x02" * 100)
        self.assertEqual(self.read(self.ws / "blob.bin")["error"]["type"], "Unsupported")

    @unittest.skipUnless(HAVE["pandas"], "needs pandas")
    def test_csv_columns_rows_and_paging(self):
        f = self.write(self.ws / "tally.csv", "bin,flux,rel_err\n" + "".join(f"{i},{i * 1.5},0.01\n" for i in range(250)))
        out = self.read(f)
        self.assertEqual((out["format"], out["rows"], out["shown_rows"]), ("csv", 250, "1-100"))
        self.assertEqual(out["columns"][1], ["flux", "float64"])
        self.assertEqual(out["numeric_columns_are"], ["column", "mean", "min", "max"])
        self.assertIn("start=101", out["next"])
        self.assertTrue(self.read(f, start=241)["text"].splitlines()[1].startswith("240,"))

    @unittest.skipUnless(HAVE["pandas"] and HAVE["openpyxl"], "needs pandas and openpyxl")
    def test_excel_sheets(self):
        import pandas as pd
        f = self.ws / "book.xlsx"
        with pd.ExcelWriter(f, engine="openpyxl") as w:
            pd.DataFrame({"a": [1, 2]}).to_excel(w, sheet_name="first", index=False)
            pd.DataFrame({"b": [3, 4, 5]}).to_excel(w, sheet_name="second", index=False)
        self.assertEqual(self.read(f)["sheets"], ["first", "second"])
        self.assertEqual(self.read(f, sheet="2")["rows"], 3)
        self.assertEqual(self.read(f, sheet="second")["columns"][0][0], "b")
        self.assertEqual(self.read(f, sheet="third")["error"]["type"], "NotFound")

    @unittest.skipUnless(HAVE["pypdf"] and HAVE["matplotlib"], "needs pypdf and matplotlib")
    def test_pdf_text_by_page(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_pdf import PdfPages
        f = self.ws / "doc.pdf"
        with matplotlib.rc_context({"pdf.fonttype": 42}), PdfPages(f) as pdf:
            for n in (1, 2, 3):
                fig = plt.figure()
                fig.text(0.1, 0.5, f"PAGEMARK{n} reactivity")
                pdf.savefig(fig)
                plt.close(fig)
        out = self.read(f)
        self.assertEqual((out["format"], out["pages"]), ("pdf", 3))
        self.assertIn("PAGEMARK1", out["text"])
        only = self.read(f, pages="2-3")
        self.assertNotIn("PAGEMARK1", only["text"])
        self.assertIn("PAGEMARK3", only["text"])
        self.assertEqual(self.read(f, pages="4")["error"]["type"], "InvalidArgument")

    @unittest.skipUnless(HAVE["h5py"], "needs h5py")
    def test_hdf5_structure(self):
        import h5py
        import numpy as np
        f = self.ws / "data.h5"
        with h5py.File(f, "w") as h:
            g = h.create_group("tallies")
            d = g.create_dataset("mean", data=np.arange(6.0).reshape(2, 3))
            d.attrs["facility_data"] = "public"
            h.create_dataset("k", data=np.array([1.0012]))
        out = self.read(f)
        names = {o["name"]: o for o in out["structure"]}
        self.assertEqual(names["tallies/mean"]["shape"], [2, 3])
        self.assertEqual(names["k"]["value"], [1.0012])
        self.assertEqual(names["tallies/mean"]["attrs"], [["facility_data", "public"]])
        self.assertEqual(labels_in(out), [])


class Listing(Places):
    def test_entries_patterns_and_what_is_left_out(self):
        self.write(self.ws / "a.csv")
        self.write(self.ws / "b.txt")
        self.write(self.ws / "sub" / "c.csv")
        self.write(self.ws / ".git" / "HEAD")
        self.write(self.ws / ".env")
        out = registry.call("FileListTool", {})
        self.assertEqual([e["name"] for e in out["entries"]], ["a.csv", "b.txt", "sub"])
        self.assertIn("left out", out["skipped"])
        deep = registry.call("FileListTool", {"pattern": "*.csv", "recursive": True})
        self.assertEqual([e["name"] for e in deep["entries"]], ["a.csv", "sub/c.csv"])
        capped = registry.call("FileListTool", {"recursive": True, "max_entries": 2})
        self.assertTrue(capped["more"])
        self.assertEqual(registry.call("FileListTool", {"path": "a.csv"})["error"]["type"], "InvalidArgument")

    def test_a_listing_carries_the_worst_label_it_shows(self):
        os.environ[places.ROOTS_ENV] = str(self.tmp)
        self.write(self.restricted / "netl" / "x.txt")
        out = registry.call("FileListTool", {"path": str(self.tmp), "recursive": True})
        self.assertEqual(labels_in(out), ["restricted"])
        self.assertEqual(labels_in(registry.call("FileListTool", {"path": str(automc_paths.SRC / "agent")})), ["public"])


class Writing(Places):
    def write_tool(self, path, text="note", **kw):
        return registry.call("FileWriteTool", {"path": path, "text": text, **kw})

    def test_a_note_is_written_and_never_overwritten_by_accident(self):
        out = self.write_tool("notes/summary.md", "# Summary\n")
        self.assertTrue(out["ok"], out)
        self.assertEqual((self.ws / "notes" / "summary.md").read_text(encoding="utf-8"), "# Summary\n")
        self.assertEqual(self.write_tool("notes/summary.md", "again")["error"]["type"], "Exists")
        self.assertTrue(self.write_tool("notes/summary.md", "again", overwrite=True)["replaced"])
        self.assertEqual(list((self.ws / "notes").glob(".*.tmp")), [])

    def test_where_it_never_writes(self):
        case = self.case("c1", "public")
        run = self.ws / "c2" / "run-001"
        run.mkdir(parents=True)
        (run / "statepoint.10.h5").write_bytes(b"")
        cases = {"outside the workspace": str(self.outside / "x.md"), "inside a case": "c1/notes.md",
                 "inside a run folder": "c2/run-001/x.md", "a record's name": "notes/result.json",
                 "a sweep record": "anything/sweep.json", "AutoMC's bookkeeping": ".automc/chats/x.jsonl",
                 "a keys file": "notes/.env", "a key": "notes/id_rsa"}
        for what, path in cases.items():
            with self.subTest(what=what):
                out = self.write_tool(path)
                self.assertFalse(out["ok"], out)
                self.assertEqual(out["error"]["type"], "NotAllowed")
        self.assertEqual(sorted(p.name for p in case.iterdir()), ["result.json"])
        self.assertFalse((self.ws / "notes" / "result.json").exists())

    def test_too_much_text_is_refused(self):
        self.assertEqual(self.write_tool("notes/big.txt", "x" * (ft.MAX_WRITE + 1))["error"]["type"], "InvalidArgument")


class Registered(unittest.TestCase):
    def test_the_three_tools_are_registered_with_strict_schemas(self):
        for name in ("FileListTool", "FileReadTool", "FileWriteTool"):
            t = next(t for t in registry.describe() if t["name"] == name)
            self.assertFalse(t["parameters"]["additionalProperties"])
        self.assertEqual(registry.call("FileReadTool", {"path": "x", "colour": "red"})["error"]["field"], "colour")
        self.assertEqual(registry.call("FileReadTool", {})["error"]["field"], "path")


if __name__ == "__main__":
    unittest.main()
