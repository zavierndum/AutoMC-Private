"""
The examples' input data (decision A3): `python -m automc data` fetches each file into the data cache and keeps it only when its
SHA-256 matches data.json; a member of a zip archive is read by range requests, not by downloading the archive; a second fetch
downloads nothing; the registry names a checksum, a size and a source for every file. A local HTTP server stands in for the
providers.
"""
import hashlib
import io
import json
import os
import random
import shutil
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from tools.openmc import example_data


class _Files(BaseHTTPRequestHandler):
    files, served = {}, {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        data = self.files.get(self.path)
        if data is None:
            self.send_error(404)
            return
        rng = self.headers.get("Range")
        if rng:
            start, end = (int(x) for x in rng.split("=", 1)[1].split("-"))
            end = min(end, len(data) - 1)
            body = data[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        else:
            body = data
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.served[self.path] = self.served.get(self.path, 0) + len(body)


class Fetching(unittest.TestCase):
    def setUp(self):
        self.cache = Path(tempfile.mkdtemp(prefix="automc_data_"))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Files)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        _Files.files, _Files.served = {}, {}
        self.patches = [mock.patch.dict(os.environ, {"AUTOMC_DATA_CACHE": str(self.cache)})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.cache, ignore_errors=True)

    def registry(self, files):
        return mock.patch.object(example_data, "registry", return_value=files)

    def entry(self, data, **where):
        return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "source": "a test", "examples": ["ex"], **where}

    def test_a_file_is_downloaded_checked_and_kept_once(self):
        data = b"a CAD geometry\n" * 1000
        _Files.files["/geom.h5m"] = data
        with self.registry({"cad/geom.h5m": self.entry(data, url=self.base + "/geom.h5m")}):
            out = example_data.fetch(["ex"], say=lambda *_: None)
            self.assertEqual([(r["file"], r["state"]) for r in out], [("cad/geom.h5m", "fetched")])
            self.assertEqual((self.cache / "cad" / "geom.h5m").read_bytes(), data)
            again = example_data.fetch(say=lambda *_: None)
            self.assertEqual((again[0]["state"], again[0]["fetched"]), ("ok", 0))
            self.assertEqual(_Files.served["/geom.h5m"], len(data))                 # downloaded once
            self.assertEqual(example_data.status(["cad/geom.h5m"])[0]["state"], "ok")

    def test_a_download_that_is_not_the_named_file_is_not_kept(self):
        _Files.files["/geom.h5m"] = b"something else"
        with self.registry({"cad/geom.h5m": self.entry(b"the real file", url=self.base + "/geom.h5m")}):
            out = example_data.fetch(say=lambda *_: None)
            self.assertEqual(out[0]["state"], "not fetched")
            self.assertIn("SHA-256", out[0]["error"])
            self.assertEqual(list(self.cache.rglob("*.*")), [])                    # neither the file nor a .part left
            self.assertEqual(example_data.status()[0]["state"], "missing")
            (self.cache / "cad").mkdir(exist_ok=True)
            (self.cache / "cad" / "geom.h5m").write_bytes(b"edited")
            self.assertEqual(example_data.status()[0]["state"], "differs")

    def test_a_zip_member_is_read_without_downloading_the_archive(self):
        member = b"an ENDF evaluation\n" * 200
        filler = random.Random(1).randbytes(2_000_000)                            # incompressible: the archive stays large
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("lib/filler.bin", filler)
            z.writestr("lib/n-001_H_002.endf", member)
        _Files.files["/lib.zip"] = buf.getvalue()
        with self.registry({"endf/n-001_H_002.endf": self.entry(member, zip=self.base + "/lib.zip", member="lib/n-001_H_002.endf")}):
            out = example_data.fetch(say=lambda *_: None)
        self.assertEqual(out[0]["state"], "fetched")
        self.assertEqual((self.cache / "endf" / "n-001_H_002.endf").read_bytes(), member)
        self.assertLess(_Files.served["/lib.zip"], len(buf.getvalue()) / 10)

    def test_targets_and_the_command_line(self):
        files = {"a.h5m": self.entry(b"a", url="x"), "b.endf": {**self.entry(b"b", url="y"), "examples": ["other"]},
                 "spare.e": {**self.entry(b"c", url="z"), "examples": []}}
        with self.registry(files):
            self.assertEqual(sorted(example_data.wanted()), ["a.h5m", "b.endf"])          # all an example uses
            self.assertEqual(list(example_data.wanted(["other"])), ["b.endf"])
            self.assertEqual(list(example_data.wanted(["spare.e"])), ["spare.e"])           # by file, even if no example uses it
            with self.assertRaises(example_data.DataError):
                example_data.wanted(["no-such-example"])
            with mock.patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(example_data.main(["list", "no-such-example"]), 2)
                self.assertEqual(example_data.main(["list", "ex"]), 1)                    # missing from the cache


class Registry(unittest.TestCase):
    def test_every_file_has_a_checksum_a_size_and_a_source(self):
        files = example_data.registry()
        self.assertTrue(files)
        for rel, f in files.items():
            with self.subTest(file=rel):
                self.assertRegex(f["sha256"], r"^[0-9a-f]{64}$")
                self.assertIsInstance(f["bytes"], int)
                self.assertTrue(f.get("url") or (f.get("zip") and f.get("member")), f)
                self.assertTrue(f["source"])
                self.assertNotIn("viglink", json.dumps(f))                   # the advertising redirector the notebooks' links use


if __name__ == "__main__":
    unittest.main()
