"""
Any OpenMC model from a Python build script (AU-1, devops/AutoMC_Authoring_Plan.md): the build tool only plans, refuses a script that
runs transport or other programs, and labels the model; an approved build runs the script in its own process, disarms transport there
too, and leaves a case only when model.xml came out (with build.py, the log and result.json); a failed build leaves nothing. The plot
tool draws slices with overlaps marked; the check tool finds missing nuclear data, a source outside the geometry, overlapping cells,
an eigenvalue run without fuel, and a fixed-source model without a vacuum boundary. The chat asks before building, and a container's
python.approval: auto builds without asking. Building needs OpenMC (WSL).
"""
import importlib.util
import json
import os
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import automc_paths
import automc_settings
from tools import conversation, registry
from tools.openmc import authoring, runs
from tools.openmc.errors import OpenMCError

HAVE_OPENMC = importlib.util.find_spec("openmc") is not None and os.name == "posix" and shutil.which("openmc") is not None
HAVE_LANGGRAPH = importlib.util.find_spec("langgraph") is not None

# A user's question of 2026-11-01: a 2.5 cm water sphere 15 cm from an isotropic point source (2 MeV here), in vacuum
SPHERE = textwrap.dedent('''
    import openmc

    water = openmc.Material(name="water")
    water.add_nuclide("H1", 2.0)
    water.add_nuclide("O16", 1.0)
    water.set_density("g/cm3", 1.0)

    target = openmc.Sphere(x0=15.0, r=2.5)
    world = openmc.Sphere(r=40.0, boundary_type="vacuum")
    inside = openmc.Cell(name="water sphere", fill=water, region=-target)
    space = openmc.Cell(name="vacuum around it", region=+target & -world)

    settings = openmc.Settings()
    settings.run_mode = "fixed source"
    settings.particles = 1000
    settings.batches = 5
    settings.source = openmc.IndependentSource(space=openmc.stats.Point((0.0, 0.0, 0.0)), angle=openmc.stats.Isotropic(),
                                               energy=openmc.stats.Discrete([2.0e6], [1.0]))

    flux = openmc.Tally(name="flux in the sphere")
    flux.filters = [openmc.CellFilter([inside])]
    flux.scores = ["flux"]

    model = openmc.Model(geometry=openmc.Geometry([inside, space]), materials=openmc.Materials([water]), settings=settings,
                         tallies=openmc.Tallies([flux]))
    model.export_to_model_xml()
''')

# A one-group multigroup model whose library build.py writes next to model.xml and names by a relative path
MG_BOX = textwrap.dedent('''
    import numpy as np
    import openmc
    import openmc.mgxs
    {resolve}
    groups = openmc.mgxs.EnergyGroups([1e-5, 20.0e6])
    xs = openmc.XSdata("fuel", groups)
    xs.order = 0
    xs.set_total([0.5])
    xs.set_absorption([0.2])
    xs.set_scatter_matrix(np.array([[[0.3]]]))
    xs.set_fission([0.1])
    xs.set_nu_fission([0.25])
    xs.set_chi([1.0])
    library = openmc.MGXSLibrary(groups)
    library.add_xsdata(xs)
    library.export_to_hdf5("mgxs.h5")
    fuel = openmc.Material(name="fuel")
    fuel.set_density("macro", 1.0)
    fuel.add_macroscopic("fuel")
    materials = openmc.Materials([fuel])
    materials.cross_sections = "mgxs.h5"
    box = openmc.model.RectangularParallelepiped(-1, 1, -1, 1, -1, 1, boundary_type="reflective")
    settings = openmc.Settings()
    settings.energy_mode = "multi-group"
    settings.particles, settings.batches, settings.inactive = 100, 20, 10
    settings.source = openmc.IndependentSource(space=openmc.stats.Box([-1, -1, -1], [1, 1, 1]))
    model = openmc.Model(geometry=openmc.Geometry([openmc.Cell(fill=fuel, region=-box)]), materials=materials, settings=settings)
    model.export_to_model_xml()
''')


class Workspace(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="automc_author_")).resolve()
        self.patches = [mock.patch.object(automc_paths, "WORKSPACE", self.ws), mock.patch.dict(os.environ)]
        for p in self.patches:
            p.start()
        if not os.environ.get("OPENMC_CROSS_SECTIONS"):                    # a non-interactive shell: the library the run tests use
            from tests.reactors.triga.test_build_config0 import _XS_FALLBACK
            if _XS_FALLBACK.is_file():
                os.environ["OPENMC_CROSS_SECTIONS"] = str(_XS_FALLBACK)
        os.environ[automc_settings.USER_ENV] = str(self.ws / "no-user-settings.yaml")
        automc_settings._CACHE.clear()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        automc_settings._CACHE.clear()
        shutil.rmtree(self.ws, ignore_errors=True)

    def build(self, case, script, **kw):
        with conversation.inside(None, kw.pop("label", "public"), "t1", None):
            plan = authoring.plan_build(case, script, **kw)
            return plan, authoring.start_build(plan, approved_by="test")


class Plans(Workspace):
    def test_the_tool_only_plans_and_shows_the_script(self):
        out = registry.call("OpenMCModelBuildTool", {"case": "studies/sphere", "script": SPHERE})
        self.assertTrue(out["ok"], out)
        self.assertEqual((out["started"], out["needs_approval"], out["plan"]["case"]), (False, True, "studies/sphere"))
        self.assertIn("water = openmc.Material", out["plan"]["text"])
        self.assertFalse((self.ws / "studies").exists())

    def test_scripts_that_run_transport_or_programs_are_refused(self):
        for line, why in (("openmc.run()", ".run()"), ("model.run(threads=4)", ".run()"), ("import subprocess", "other programs"),
                          ("openmc.lib.run_in_memory()", "run_in_memory"), ("openmc.search_for_keff(f, [1, 2])", "search_for_keff"),
                          ("integrator.integrate()", "integrate"), ("os.system('openmc')", "other programs")):
            with self.subTest(line=line):
                out = registry.call("OpenMCModelBuildTool", {"case": "x", "script": SPHERE + line + "\n"})
                self.assertFalse(out["ok"])
                self.assertIn(why, out["error"]["message"])
                self.assertIn("OpenMCRunStartTool", out["error"]["message"])

    def test_names_cases_with_runs_and_overwrite(self):
        for bad in ("../x", "/abs", ".automc/x", "a/b/c/d/e"):
            with self.subTest(bad=bad):
                self.assertFalse(registry.call("OpenMCModelBuildTool", {"case": bad, "script": SPHERE})["ok"])
        (self.ws / "done" / "run-001").mkdir(parents=True)
        out = registry.call("OpenMCModelBuildTool", {"case": "done", "script": SPHERE, "overwrite": True})
        self.assertIn("has runs", out["error"]["message"])
        (self.ws / "old").mkdir()
        self.assertIn("overwrite", registry.call("OpenMCModelBuildTool", {"case": "old", "script": SPHERE})["error"]["message"])
        self.assertTrue(registry.call("OpenMCModelBuildTool", {"case": "old", "script": SPHERE, "overwrite": True})["ok"])

    def test_the_label_follows_the_conversation_and_the_folders_the_script_reads(self):
        with conversation.inside(None, "restricted", "t1", None):
            self.assertEqual(authoring.plan_build("a", SPHERE)["label"], "restricted")
        secret = self.ws.parent / f"{self.ws.name}-secret"
        secret.mkdir()
        try:
            (secret / "comp.csv").write_text("u235,0.2\n", encoding="utf-8")
            automc_settings.write({"folders": [{"name": "netl", "path": str(secret), "label": "restricted"}]})
            with conversation.inside(None, "public", "t1", None):
                plan = authoring.plan_build("b", SPHERE + f"\nopen('{(secret / 'comp.csv').as_posix()}').read()\n")
            self.assertEqual(plan["label"], "restricted")
            self.assertIn("comp.csv", plan["label_reason"])
            with conversation.inside(None, "public", "t1", None):
                self.assertEqual(authoring.plan_build("c", SPHERE)["label"], "public")
        finally:
            shutil.rmtree(secret, ignore_errors=True)

    def test_a_plan_changed_after_the_user_saw_it_is_refused(self):
        with conversation.inside(None, "public", "t1", None):
            plan = authoring.plan_build("a", SPHERE)
        with self.assertRaises(OpenMCError):
            authoring.start_build({**plan, "script": SPHERE + "# more\n"}, approved_by="test")
        with self.assertRaises(OpenMCError):
            authoring.start_build(plan, approved_by="")


@unittest.skipUnless(HAVE_OPENMC, "needs OpenMC (WSL, the automc environment)")
class Building(Workspace):
    def test_an_approved_build_makes_a_labelled_case_the_job_service_can_run(self):
        plan, out = self.build("studies/water-sphere", SPHERE)
        self.assertTrue(out["built"], out)
        case = self.ws / "studies" / "water-sphere"
        for f in ("model.xml", "build.py", "build.log", "result.json"):
            self.assertTrue((case / f).is_file(), f)
        rec = json.loads((case / "result.json").read_text(encoding="utf-8"))
        self.assertEqual((rec["builder"], rec["facility_data"]["distribution"]), ("script", "public"))
        self.assertEqual(rec["model"]["run_mode"], "fixed source")
        self.assertEqual(rec["model"]["cells"], 2)
        self.assertEqual(runs._label(runs._built(case)), "public")
        run_plan = runs.plan_run(case="studies/water-sphere", threads=1)
        self.assertEqual(run_plan["cases"][0]["run_folder"], "studies/water-sphere/run-001")
        self.assertFalse([p for p in (self.ws / "studies").iterdir() if p.name.startswith(".")])      # no staging left

    def test_transport_reached_another_way_is_still_refused_and_nothing_is_left(self):
        sneaky = SPHERE + "f = getattr(openmc, 'r' + 'un')\nf()\n"
        _, out = self.build("sneaky", sneaky)
        self.assertFalse(out["built"])
        self.assertIn("transport is started by OpenMCRunStartTool", out["error"] + out["log_tail"])
        self.assertEqual(list(self.ws.iterdir()), [])

    def test_a_failing_script_reports_its_error_and_leaves_nothing(self):
        _, out = self.build("broken", "import openmc\nraise ValueError('no such material')\n")
        self.assertFalse(out["built"])
        self.assertIn("no such material", out["error"])
        self.assertIn("Traceback", out["log_tail"])
        self.assertEqual(list(self.ws.iterdir()), [])

    def test_separate_xml_files_are_joined_into_model_xml(self):
        script = SPHERE.replace("model.export_to_model_xml()", "model.export_to_xml()")
        _, out = self.build("separate", script)
        self.assertTrue(out["built"], out)
        self.assertTrue((self.ws / "separate" / "model.xml").is_file())

    def test_a_file_the_script_names_stays_the_cases_own(self):
        """2026-11-02: OpenMC makes a path absolute when it is set, so a multigroup library written in the staging folder was named
        by that folder's path, gone once the case was moved in, and every run failed. A script that turns resolving back on itself
        has the path rewritten."""
        for case, resolve in (("mg-plain", ""), ("mg-resolving", "openmc.config['resolve_paths'] = True")):
            with self.subTest(case=case):
                _, out = self.build(case, MG_BOX.format(resolve=resolve))
                self.assertTrue(out["built"], out)
                folder = self.ws / case
                self.assertIn("<cross_sections>mgxs.h5</cross_sections>", (folder / "model.xml").read_text(encoding="utf-8"))
                self.assertEqual("note" in out, bool(resolve), out)
                checked = authoring.check_case(case)
                self.assertFalse([f for f in checked["findings"] if f["check"] == "multigroup data"], checked["findings"])
                self.assertTrue((runs.prepare_run_folder(folder, "run-001") / "mgxs.h5").is_file())

    def test_the_check_names_a_cad_file_that_is_not_there(self):
        script = textwrap.dedent('''
            import openmc
            settings = openmc.Settings()
            settings.run_mode, settings.particles, settings.batches = "fixed source", 1000, 5
            settings.source = openmc.IndependentSource(space=openmc.stats.Point())
            openmc.Model(geometry=openmc.Geometry(openmc.DAGMCUniverse("reactor.h5m")), settings=settings).export_to_model_xml()
        ''')
        _, out = self.build("cad-missing", script)
        self.assertTrue(out["built"], out)
        checked = authoring.check_case("cad-missing")
        files = [f for f in checked["findings"] if f["check"] == "files"]
        self.assertEqual([f["severity"] for f in files], ["error"], checked["findings"])
        self.assertIn("reactor.h5m", files[0]["message"])
        self.assertFalse(checked["passed"])

    def test_the_geometry_is_plotted(self):
        self.build("sphere", SPHERE)
        out = registry.call("OpenMCGeometryPlotTool", {"case": "sphere"})
        self.assertTrue(out["ok"], out)
        self.assertEqual([p["basis"] for p in out["plots"]], ["xy", "xz", "yz"])
        for p in out["plots"]:
            self.assertTrue((self.ws / p["path"]).read_bytes().startswith(b"\x89PNG"))
        self.assertEqual((out["overlaps_seen"], out["facility_data"]), (0, "public"))
        one = registry.call("OpenMCGeometryPlotTool", {"case": "sphere", "views": [{"basis": "xy", "origin": [15, 0, 0], "width": [8, 8]}],
                                                       "pixels": 200, "color_by": "cell"})
        self.assertEqual(len(one["plots"]), 1)

    def test_the_check_passes_a_good_model_and_names_what_is_wrong(self):
        self.build("good", SPHERE)
        out = registry.call("OpenMCModelCheckTool", {"case": "good"})
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["passed"], out["findings"])
        self.assertIn("normalisation", [f["check"] for f in out["findings"]])
        cases = {
            "overlap": (SPHERE.replace("space = openmc.Cell(name=\"vacuum around it\", region=+target & -world)",
                                       "space = openmc.Cell(name=\"vacuum around it\", region=-world)"), "overlaps"),
            "outside": (SPHERE.replace("openmc.stats.Point((0.0, 0.0, 0.0))", "openmc.stats.Point((100.0, 0.0, 0.0))"), "source"),
            "nofuel": (SPHERE.replace('settings.run_mode = "fixed source"', 'settings.run_mode = "eigenvalue"\nsettings.inactive = 2'),
                       "settings"),
        }
        for name, (script, check) in cases.items():
            with self.subTest(name=name):
                _, built = self.build(name, script)
                self.assertTrue(built["built"], built)
                found = registry.call("OpenMCModelCheckTool", {"case": name})
                self.assertFalse(found["passed"], found["findings"])
                self.assertIn(check, [f["check"] for f in found["findings"] if f["severity"] == "error"])
        noboundary = SPHERE.replace('world = openmc.Sphere(r=40.0, boundary_type="vacuum")', 'world = openmc.Sphere(r=40.0, boundary_type="reflective")')
        self.build("noboundary", noboundary)
        found = registry.call("OpenMCModelCheckTool", {"case": "noboundary"})
        self.assertIn("boundaries", [f["check"] for f in found["findings"] if f["severity"] == "warning"])
        # a box whose corners stick out of the world sphere: OpenMC resamples those points, so it is a warning, not an error
        partly = SPHERE.replace("openmc.stats.Point((0.0, 0.0, 0.0))", "openmc.stats.Box((-30.0, -30.0, -30.0), (30.0, 30.0, 30.0))")
        self.build("partly", partly)
        found = registry.call("OpenMCModelCheckTool", {"case": "partly"})
        self.assertTrue(found["passed"], found["findings"])
        self.assertIn("source", [f["check"] for f in found["findings"] if f["severity"] == "warning"])

    def test_the_normalisation_note_follows_the_source_strengths(self):
        self.build("good", SPHERE)
        self.build("strong", SPHERE.replace("energy=openmc.stats.Discrete([2.0e6], [1.0]))",
                                            "energy=openmc.stats.Discrete([2.0e6], [1.0]), strength=1.0e8)"))
        notes = {case: [f["message"] for f in registry.call("OpenMCModelCheckTool", {"case": case})["findings"]
                        if f["check"] == "normalisation"][0] for case in ("good", "strong")}
        self.assertIn("per source particle; multiply by the source strength", notes["good"])
        self.assertIn("add up to 1e+08", notes["strong"])                     # OpenMC has already scaled these tallies
        self.assertIn("divide by 1e+08", notes["strong"])

    def test_missing_nuclear_data_is_named(self):
        self.build("good", SPHERE)
        xs = self.ws / "small_cross_sections.xml"
        xs.write_text('<?xml version="1.0"?>\n<cross_sections>\n  <library materials="H1" path="H1.h5" type="neutron"/>\n'
                      '</cross_sections>\n', encoding="utf-8")
        with mock.patch.dict(os.environ, {"OPENMC_CROSS_SECTIONS": str(xs)}):
            found = registry.call("OpenMCModelCheckTool", {"case": "good"})
        msgs = [f["message"] for f in found["findings"] if f["check"] == "nuclear data"]
        self.assertTrue(msgs and "O16" in msgs[0], found["findings"])


@unittest.skipUnless(HAVE_OPENMC and HAVE_LANGGRAPH, "needs OpenMC and LangGraph")
class Agent(Workspace):
    def session(self, replies):
        import automc_core
        from tests.agent.test_core import LOCAL, Scripted
        return automc_core.Session(provider=LOCAL, model=Scripted(replies=list(replies), seen=[]), audit=False)

    def test_the_chat_asks_before_building_and_only_yes_builds(self):
        from langchain_core.messages import AIMessage, ToolMessage
        from tests.agent.test_core import call
        for yes in (False, True):
            with self.subTest(yes=yes):
                s = self.session([call("OpenMCModelBuildTool", {"case": f"s{int(yes)}", "script": SPHERE}), AIMessage(content="done")])
                evs = list(s.send("build the water sphere"))
                self.assertEqual((evs[-1][0], evs[-1][1]["kind"]), ("approval", "build_approval"))
                self.assertIn("Build an OpenMC model from a Python script", evs[-1][1]["plan_text"])
                list(s.answer(yes))
                result = json.loads([m for m in s.messages() if isinstance(m, ToolMessage)][-1].content)
                if yes:
                    self.assertTrue(result["built"], result)
                    self.assertTrue((self.ws / "s1" / "model.xml").is_file())
                else:
                    self.assertTrue(result["declined"])
                    self.assertFalse((self.ws / "s0").exists())

    def test_in_a_container_with_auto_the_build_runs_without_asking(self):
        from langchain_core.messages import AIMessage, ToolMessage
        from tests.agent.test_core import call
        automc_settings.write({"python": {"approval": "auto"}})
        with mock.patch.object(automc_settings, "in_container", return_value=True):
            s = self.session([call("OpenMCModelBuildTool", {"case": "auto", "script": SPHERE}), AIMessage(content="built")])
            evs = list(s.send("build it"))
        self.assertFalse([e for e in evs if e[0] == "approval"])
        self.assertTrue(any(e[0] == "notice" and "model build ran without asking" in e[1] for e in evs))
        result = json.loads([m for m in s.messages() if isinstance(m, ToolMessage)][-1].content)
        self.assertTrue(result["built"], result)


if __name__ == "__main__":
    unittest.main()
