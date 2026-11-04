"""
The build worker. Started by build.build_input as a fresh process:

    python -m tools.reactors.triga._worker --request req.json --response resp.json

The request carries the resolved spec, the output folder, the library profile and the code. The response is JSON: either
{"ok": true, ...} or {"ok": false, "error": {"type", "field", "message"}} so the parent can raise the same exception class.
Library errors that are handled become a response with exit code 0; an unhandled crash exits non-zero and the parent reports the
tail of build.log.

Test seam: the environment variable TRIGA_WORKER_TEST_FAIL makes the worker fail on purpose (values: invalid, unsupported,
missing, crash, sleep). It is used only by the tests.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

from .errors import InvalidSpec, MissingLibrary, TrigaError, UnsupportedFeature


def _error_payload(exc):
    return {"type": type(exc).__name__, "field": getattr(exc, "field", None) or getattr(exc, "feature", None), "message": str(exc)}


def _test_seam():
    mode = os.environ.get("TRIGA_WORKER_TEST_FAIL")
    if mode == "invalid":
        raise InvalidSpec("test.field", "forced failure for the tests")
    if mode == "unsupported":
        raise UnsupportedFeature("test.feature", "forced failure for the tests")
    if mode == "missing":
        raise MissingLibrary("forced failure for the tests")
    if mode == "crash":
        print("about to crash on purpose")
        os._exit(7)
    if mode == "sleep":
        print("sleeping on purpose")
        sys.stdout.flush()
        time.sleep(600)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--request", required=True)
    ap.add_argument("--response", required=True)
    args = ap.parse_args(argv)
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    response = {"ok": False}
    try:
        _test_seam()
        if request["code"] != "openmc":
            raise UnsupportedFeature(f"code={request['code']}", "only the openmc writer exists")
        from .writers import openmc_writer                     # imports OpenMC and the profile's libraries
        out = openmc_writer.build(request["spec"], Path(request["outdir"]), request["profile"])
        response = {"ok": True, **out}
    except TrigaError as e:
        response = {"ok": False, "error": _error_payload(e)}
    except Exception as e:                                       # a bug or an unexpected library failure
        traceback.print_exc()
        response = {"ok": False, "error": {"type": "TrigaError", "field": None, "message": f"unexpected {type(e).__name__}: {e}"}}
    Path(args.response).write_text(json.dumps(response), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
