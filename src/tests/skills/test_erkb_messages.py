"""
The error knowledge base's hints against what OpenMC, h5py and NumPy really write (AUTOMC_SLOW_TESTS=1, WSL with OpenMC). Each message
is provoked on a tiny model in a temporary folder, one thread, and the entry's hint must occur in the output, so a hint that an upgrade
changed fails here and not in a user's run. Every entry is either provoked here or says why not. The CAD and ENDF cases copy their
file from the data cache (python -m automc data fetch) and skip without it.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SLOW = os.environ.get("AUTOMC_SLOW_TESTS") == "1"
HAVE_OPENMC = importlib.util.find_spec("openmc") is not None and os.name == "posix" and shutil.which("openmc") is not None
NOT_PROVOKED = {
    "status=failed": "AutoMC's job sentinel; tests/agent/test_erkb.py and tests/openmc/test_runs.py",
    "is not built: missing": "AutoMC's own message; tests/agent/test_erkb.py",
    "No restricted facility pack is installed": "AutoMC's own message; tests/agent/test_erkb.py",
    "bins in total, above the limit": "AutoMC's own message; tests/agent/test_erkb.py",
    "OpenMC aborted unexpectedly": "a crash cannot be provoked on purpose",
    "No graveyard volume found in the DagMC model": "needs a CAD file without a graveyard; the data cache has none",
}


def _cache():
    return Path(os.environ.get("AUTOMC_DATA_CACHE") or Path.home() / ".cache" / "automc" / "data").expanduser()


class Provoker:
    """Builds each tiny model and returns what the program wrote, or None when a data file is missing."""

    def __init__(self, root):
        self.root = Path(root)
        self.env = dict(os.environ, OMP_NUM_THREADS="1")

    def folder(self, name):
        d = self.root / name
        d.mkdir()
        return d

    def openmc(self, name, model, args=(), env=None):
        d = self.folder(name)
        model.export_to_model_xml(d / "model.xml")
        p = subprocess.run(["openmc", *args], cwd=d, env=env or self.env, capture_output=True, text=True, timeout=600)
        return p.stdout + p.stderr

    def python(self, name, code, folder=None):
        d = folder or self.folder(name)
        p = subprocess.run([sys.executable, "-I", "-c", code], cwd=d, env=self.env, capture_output=True, text=True, timeout=600)
        return p.stdout + p.stderr

    @staticmethod
    def water(name="water", temperature=None):
        import openmc
        m = openmc.Material(name=name, temperature=temperature)
        m.add_nuclide("H1", 2.0)
        m.add_nuclide("O16", 1.0)
        m.set_density("g/cm3", 1.0)
        return m

    @staticmethod
    def fixed(**kw):
        import openmc
        source = kw.pop("source", None) or openmc.IndependentSource(space=openmc.stats.Point())
        return openmc.Settings(run_mode="fixed source", particles=kw.pop("particles", 100), batches=1, source=source, **kw)

    def sphere(self, material=None, settings=None):
        import openmc
        openmc.reset_auto_ids()
        m = material or self.water()
        s = openmc.Sphere(r=10.0, boundary_type="vacuum")
        return openmc.Model(openmc.Geometry([openmc.Cell(fill=m, region=-s)]), openmc.Materials([m]), settings or self.fixed())

    # ------------------------------------------------------------------------------------------------------------------
    def no_cross_sections(self):
        env = {k: v for k, v in self.env.items() if k != "OPENMC_CROSS_SECTIONS"}
        return self.openmc("no-xs", self.sphere(), env=env)

    def overlap(self):
        import openmc
        openmc.reset_auto_ids()
        m = self.water()
        inner, outer = openmc.Sphere(r=5.0), openmc.Sphere(r=10.0, boundary_type="vacuum")
        cells = [openmc.Cell(fill=m, region=-inner), openmc.Cell(fill=m, region=-outer)]         # the second contains the first
        return self.openmc("overlap", openmc.Model(openmc.Geometry(cells), openmc.Materials([m]), self.fixed()), args=["-g"])

    def lost(self):
        import openmc
        openmc.reset_auto_ids()
        m = self.water()
        a, b, c = openmc.Sphere(r=5.0), openmc.Sphere(r=6.0), openmc.Sphere(r=10.0, boundary_type="vacuum")
        cells = [openmc.Cell(fill=m, region=-a), openmc.Cell(fill=m, region=+b & -c)]           # nothing between 5 and 6 cm
        return self.openmc("lost", openmc.Model(openmc.Geometry(cells), openmc.Materials([m]), self.fixed()))

    def temperature(self):
        return self.openmc("temperature", self.sphere(self.water(temperature=3000.0)))

    def mixed_fractions(self):
        import openmc
        m = openmc.Material(name="mixed")
        m.add_nuclide("U235", 0.5, "ao")
        m.add_nuclide("U238", 0.5, "wo")
        m.set_density("g/cm3", 10.0)
        return self.openmc("mixed", self.sphere(m))

    def source_rejected(self):
        import openmc
        openmc.reset_auto_ids()
        m = self.water()
        small, world = openmc.Sphere(r=1.0), openmc.Sphere(r=50.0, boundary_type="vacuum")
        target = openmc.Cell(fill=m, region=-small)
        source = openmc.IndependentSource(space=openmc.stats.Box([-50.0] * 3, [50.0] * 3), constraints={"domains": [target]})
        model = openmc.Model(openmc.Geometry([target, openmc.Cell(region=+small & -world)]), openmc.Materials([m]),
                             self.fixed(source=source))
        return self.openmc("rejected", model)

    def mg_library_missing(self):
        import openmc
        openmc.reset_auto_ids()
        m = openmc.Material(name="mg")
        m.set_density("macro", 1.0)
        m.add_macroscopic("uo2")
        model = self.sphere(m, self.fixed(energy_mode="multi-group"))
        model.materials.cross_sections = "no-such-mgxs.h5"
        return self.openmc("mg", model)

    def multipole_missing(self):
        settings = self.fixed()
        settings.temperature = {"multipole": True}
        return self.openmc("wmp", self.sphere(settings=settings))

    def summary_held_open(self):
        d = self.folder("held")
        self.sphere().export_to_model_xml(d / "model.xml")
        subprocess.run(["openmc"], cwd=d, env=self.env, capture_output=True, timeout=600)
        code = ("import subprocess, openmc\nsp = openmc.StatePoint('statepoint.1.h5')\nsp.summary\n"
                "p = subprocess.run(['openmc'], capture_output=True, text=True)\nprint(p.stdout + p.stderr)")
        return self.python("held", code, folder=d)

    def numpy_trapz(self):
        return self.python("trapz", "import numpy\nnumpy.trapz([1.0, 2.0], [0.0, 1.0])")

    def vtk_missing(self):
        return self.python("vtk", "import numpy, openmc\nm = openmc.RegularMesh()\nm.dimension = (2, 2, 2)\nm.lower_left = (0, 0, 0)\n"
                                  "m.upper_right = (1, 1, 1)\nm.write_data_to_vtk('mesh.vtk', {'flux': numpy.zeros(8)})")

    def resonance_reconstruction(self):
        src = _cache() / "endf" / "n-064_Gd_157.endf"
        if not src.is_file():
            return None
        d = self.folder("resonance")
        shutil.copy(src, d / "gd157.endf")                           # a copy: downloaded data are read only from copies
        return self.python("resonance", "import openmc.data\nn = openmc.data.IncidentNeutron.from_endf('gd157.endf')\n"
                                        "n.resonances.resolved.reconstruct([1.0])", folder=d)

    def _pin(self, name):
        src = _cache() / "cad" / "fuel_pin.h5m"
        if not src.is_file():
            return None, None
        d = self.folder(name)
        shutil.copy(src, d / "fuel_pin.h5m")
        return d, d / "fuel_pin.h5m"

    def _run_in(self, d, model):
        import openmc
        keep = openmc.config["resolve_paths"]
        openmc.config["resolve_paths"] = False
        try:
            model.export_to_model_xml(d / "model.xml")
        finally:
            openmc.config["resolve_paths"] = keep
        p = subprocess.run(["openmc"], cwd=d, env=self.env, capture_output=True, text=True, timeout=600)
        return p.stdout + p.stderr

    def cad_material_missing(self):
        import openmc
        d, pin = self._pin("cad-material")
        if d is None:
            return None
        openmc.reset_auto_ids()
        m = self.water("not-a-tag")
        return self._run_in(d, openmc.Model(openmc.Geometry(openmc.DAGMCUniverse(str(pin))), openmc.Materials([m]), self.fixed()))

    def _pin_materials(self, pin):
        import openmc
        return openmc.Materials([self.water(tag) for tag in openmc.DAGMCUniverse(str(pin)).material_names])

    def cad_ids_clash(self):
        import openmc
        d, pin = self._pin("cad-ids")
        if d is None:
            return None
        openmc.reset_auto_ids()
        box = openmc.model.RectangularParallelepiped(-30, 30, -30, 30, -30, 30, boundary_type="vacuum")
        outer = openmc.Cell(cell_id=1, fill=openmc.DAGMCUniverse(str(pin)), region=-box)
        return self._run_in(d, openmc.Model(openmc.Geometry([outer]), self._pin_materials(pin), self.fixed()))

    def cad_file_missing(self):
        import openmc
        d, pin = self._pin("cad-missing")
        if d is None:
            return None
        openmc.reset_auto_ids()
        model = openmc.Model(openmc.Geometry(openmc.DAGMCUniverse(str(pin))), self._pin_materials(pin), self.fixed())
        keep = openmc.config["resolve_paths"]
        openmc.config["resolve_paths"] = False
        try:
            model.export_to_model_xml(d / "model.xml")
        finally:
            openmc.config["resolve_paths"] = keep
        pin.rename(d / "moved.h5m")
        p = subprocess.run(["openmc"], cwd=d, env=self.env, capture_output=True, text=True, timeout=600)
        return p.stdout + p.stderr

    def hdf5_file_missing(self):
        return self.python("h5-missing", "import openmc\nopenmc.DAGMCUniverse('missing.h5m').material_names")


CASES = {
    "No cross_sections.xml file was specified": Provoker.no_cross_sections,
    "Overlapping cells detected": Provoker.overlap,
    "Maximum number of lost particles has been reached": Provoker.lost,
    "could not be located in any cell": Provoker.lost,
    "does not contain cross sections for": Provoker.temperature,
    "Cannot mix atom and weight percents": Provoker.mixed_fractions,
    "Too few source sites satisfied the constraints": Provoker.source_rejected,
    "Cross section HDF5 file": Provoker.mg_library_missing,
    "Windowed multipole functionality is turned on, but no multipole": Provoker.multipole_missing,
    "Failed to open HDF5 file with mode 'w'": Provoker.summary_held_open,
    "has no attribute 'trapz'": Provoker.numpy_trapz,
    "No module named 'vtk": Provoker.vtk_missing,
    "Resonance reconstruction not available.": Provoker.resonance_reconstruction,
    "Geometry DAGMC file": Provoker.cad_file_missing,
    "Unable to synchronously open file (unable to open file": Provoker.hdf5_file_missing,
    "contains a cell with ID": Provoker.cad_ids_clash,
    "not found for volume (cell)": Provoker.cad_material_missing,
}


class Coverage(unittest.TestCase):
    def test_every_entry_is_provoked_or_says_why_not(self):
        try:
            from agent import erkb
            hints = {e["detection_hint"] for e in erkb.entries()}
        except ImportError:
            self.skipTest("needs PyYAML")
        self.assertEqual(hints, set(CASES) | set(NOT_PROVOKED))
        self.assertFalse(set(CASES) & set(NOT_PROVOKED))


@unittest.skipUnless(SLOW and HAVE_OPENMC, "provokes OpenMC's errors: AUTOMC_SLOW_TESTS=1 in WSL with OpenMC")
class Messages(unittest.TestCase):
    def test_each_message_still_holds_its_hint(self):
        root = tempfile.mkdtemp(prefix="automc_erkb_")
        try:
            provoker, outputs = Provoker(root), {}
            for hint, make in CASES.items():
                with self.subTest(hint=hint):
                    if make not in outputs:
                        outputs[make] = make(provoker)
                    if outputs[make] is None:
                        self.skipTest("its data file is not in the data cache (python -m automc data fetch)")
                    self.assertIn(hint, outputs[make], outputs[make][-800:])
        finally:
            shutil.rmtree(root, ignore_errors=True)
