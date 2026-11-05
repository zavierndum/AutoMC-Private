"""
The behaviour the troubleshooting and post-processing rules describe, checked against the installed OpenMC, so that a rule an upgrade
made wrong fails here and not in a user's analysis. The array facts use made-up tallies; the run facts use tiny models in a temporary
folder, one thread, a second or two each (WSL with OpenMC).
"""
import importlib.util
import inspect
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np
except ImportError:                                              # Python without the automc environment: every test skips
    np = None

HAVE_OPENMC = importlib.util.find_spec("openmc") is not None
HAVE_OPENMC_RUN = HAVE_OPENMC and os.name == "posix" and shutil.which("openmc") is not None


def default(func, name):
    return inspect.signature(func).parameters[name].default


@unittest.skipUnless(HAVE_OPENMC, "needs OpenMC (WSL, the automc environment)")
class ArrayFacts(unittest.TestCase):
    """openmc-post-processing/rules/tally-arrays.md, spectra.md and depletion-and-mgxs.md."""

    def tally(self, filters, values):
        import openmc
        t = openmc.Tally()
        t.filters = filters
        t.nuclides = ["total"]                                                # as a tally read from a statepoint has
        t.scores = ["flux"]
        t._mean = np.asarray(values, dtype=float).reshape(-1, 1, 1)          # made-up results in the statepoint's order
        return t

    def test_the_last_filter_varies_fastest(self):
        import openmc
        t = self.tally([openmc.CellFilter([1, 2]), openmc.EnergyFilter([0.0, 1.0, 2.0, 3.0])], range(6))
        self.assertEqual(t.get_reshaped_data()[:, :, 0, 0].tolist(), [[0, 1, 2], [3, 4, 5]])

    def test_mesh_bins_run_x_fastest_and_the_two_orders_differ(self):
        import openmc
        mesh = openmc.RectilinearMesh()
        mesh.x_grid, mesh.y_grid, mesh.z_grid = [0.0, 1.0, 3.0, 6.0], [0.0, 1.0, 5.0], [0.0, 1.0]
        nx, ny, nz = 3, 2, 1
        t = self.tally([openmc.MeshFilter(mesh)], range(nx * ny * nz))
        zyx = t.get_values().reshape(nz, ny, nx)
        xyz = t.get_reshaped_data(expand_dims=True)[..., 0, 0]
        for ix in range(nx):
            for iy in range(ny):
                self.assertEqual(zyx[0, iy, ix], ix + nx * iy)
                self.assertEqual(xyz[ix, iy, 0], ix + nx * iy)
        self.assertEqual(mesh.volumes.shape, (nx, ny, nz))                   # volumes in the [x, y, z] order
        self.assertEqual(mesh.volumes[2, 1, 0], 3.0 * 4.0 * 1.0)
        np.testing.assert_allclose((zyx / mesh.volumes.T)[0], (xyz / mesh.volumes)[..., 0].T)

    def test_the_vtk_writer_divides_by_volume_unless_told(self):
        import openmc
        self.assertIs(default(openmc.RegularMesh.write_data_to_vtk, "volume_normalization"), True)

    def test_the_energy_filters_lethargy_width_is_base_10(self):
        import openmc
        f = openmc.EnergyFilter([1.0, 10.0, 1000.0])
        np.testing.assert_allclose(f.lethargy_bin_width, [1.0, 2.0])
        self.assertGreater(len(openmc.mgxs.GROUP_STRUCTURES), 20)
        self.assertEqual(openmc.mgxs.GROUP_STRUCTURES["SHEM-361"][0], 0.0)    # starts at 0 eV: an infinite first lethargy width

    def test_depletion_defaults(self):
        import openmc.deplete as dep
        self.assertEqual(default(dep.Results.get_keff, "time_units"), "s")
        self.assertEqual(default(dep.Results.get_atoms, "time_units"), "s")
        self.assertEqual(default(dep.Results.get_times, "time_units"), "d")
        self.assertIs(default(dep.PredictorIntegrator.integrate, "write_rates"), False)
        from openmc.deplete.reaction_rates import ReactionRates
        self.assertEqual(float(np.abs(ReactionRates({"1": 0}, {"U235": 0}, {"fission": 0}, True)).sum()), 0.0)   # unsaved: zeros

    def test_the_multiprocessing_switch_exists(self):
        import openmc.deplete.pool as pool
        self.assertIsInstance(pool.USE_MULTIPROCESSING, bool)


@unittest.skipUnless(HAVE_OPENMC_RUN, "needs OpenMC (WSL, the automc environment)")
class RunFacts(unittest.TestCase):
    """openmc-execution/rules/troubleshooting.md and openmc-post-processing/rules/statepoints.md, on tiny models."""

    @classmethod
    def setUpClass(cls):
        from tests.skills.test_erkb_messages import Provoker
        cls.root = tempfile.mkdtemp(prefix="automc_rule_facts_")
        cls.p = Provoker(cls.root)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_a_lost_particle_file_holds_its_start_and_a_replay_finds_where_it_was_lost(self):
        import openmc
        openmc.reset_auto_ids()
        inner, middle = openmc.Sphere(r=5.0), openmc.Sphere(r=7.0)
        outer = openmc.Sphere(r=10.0, boundary_type="vacuum")
        cells = [openmc.Cell(region=-inner), openmc.Cell(region=+middle & -outer)]          # no cell from r = 5 to 7
        start = (1.0, 0.0, 0.0)
        model = openmc.Model(openmc.Geometry(cells), settings=self.p.fixed(
            particles=50, source=openmc.IndependentSource(space=openmc.stats.Point(start))))
        out = self.p.openmc("gap", model)
        self.assertIn("could not be located in any cell", out)
        files = sorted(Path(self.root, "gap").glob("particle_*.h5"))
        self.assertTrue(files)
        np.testing.assert_allclose(openmc.Particle(files[0]).xyz, start)
        replay = self.p.python("replay", f"import openmc; openmc.run(restart_file={str(files[0])!r}, output=True)",
                               folder=Path(self.root, "gap"))
        self.assertIn("Crossing surface", replay)
        r = re.findall(r"r = \(([-\d.e+]+), ([-\d.e+]+), ([-\d.e+]+)\)", replay)
        self.assertTrue(r, replay[-2000:])
        self.assertAlmostEqual(float(np.linalg.norm([float(v) for v in r[-1]])), 5.0, places=6)

    def test_a_geometry_debug_run_prints_its_coverage_by_cell(self):
        out = self.p.openmc("debug", self.p.sphere(), args=["-g"])
        self.assertIn("CELL OVERLAP CHECK SUMMARY", out)
        self.assertRegex(out, r"There were \d+ cells with less than 10 overlap checks")

    def test_an_unscored_bin_reads_zero_plus_or_minus_zero_and_get_tally_takes_the_first_match(self):
        import openmc
        model = self.p.sphere(settings=self.p.fixed(particles=200))
        model.settings.batches = 3
        water_cell = list(model.geometry.get_all_cells().values())[0]
        first = openmc.Tally(name="first")
        first.filters = [openmc.CellFilter([water_cell])]
        first.scores = ["flux", "fission"]                                            # water: no fission at all
        second = openmc.Tally(name="second")
        second.scores = ["flux"]
        model.tallies = openmc.Tallies([first, second])
        self.p.openmc("tallies", model)
        with openmc.StatePoint(next(Path(self.root, "tallies").glob("statepoint.*.h5"))) as sp:
            fission = sp.get_tally(name="first").get_values(scores=["fission"], value="std_dev")
            mean = sp.get_tally(name="first").get_values(scores=["fission"])
            self.assertEqual(float(mean.sum()), 0.0)
            self.assertEqual(float(fission.sum()), 0.0)
            self.assertEqual(sp.get_tally(scores=["flux"]).name, "first")
            self.assertIsNotNone(sp.summary)                                          # linked from its own folder
            with self.assertRaises(LookupError):
                sp.get_tally(name="none")


if __name__ == "__main__":
    unittest.main()
