"""
Tally presets (Block 2, B2-1).

Part 1 runs everywhere: mesh handling, bin counts, the size guard, the cross-field rules, presets, assumption A11 and the CLI option.
Part 2 builds real models in the WSL automc environment (about 40 s) and inspects the tallies written to model.xml with OpenMC.
No transport is run.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.reactors.triga import InvalidSpec, build_input, energy_bins, tallies
from tools.reactors.triga.energy_bins import ENERGY_BIN_EDGES_238_EV, ENERGY_BIN_COUNTS
from tools.reactors.triga.presets import active_assumptions, describe
from tools.reactors.triga.spec import Spec, resolve

from tests.reactors.triga.test_build_config0 import SKIP, setUpModule as _environment  # noqa: E402  (environment guard, cross sections)
from tests.reactors.triga._packs import require_restricted  # noqa: E402


def setUpModule():
    require_restricted()
    _environment()

from automc_paths import SRC  # noqa: E402
BUNDLE = ("flux_mesh", "beam_port_2_detector_flux", "beam_port_2_detector_current", "beam_port_2_sphere_flux")


class MeshHandling(unittest.TestCase):
    def test_default_is_the_reference_mesh(self):
        s = resolve()
        self.assertEqual(s.tallies_options.mesh, "stock_pool_90cm")
        m = tallies.resolve_mesh("stock_pool_90cm")
        self.assertEqual(m["dimension"], [150, 150, 3])
        self.assertEqual(m["lower_left_cm"], [-90.0, -90.0, -27.699970000000004])
        self.assertEqual(tallies.mesh_bins("stock_pool_90cm"), 67_500)

    def test_an_explicit_mesh_equal_to_a_preset_is_stored_under_its_name(self):
        explicit = {"dimension": [150, 150, 3], "lower_left_cm": [-90, -90, -27.699970000000004],
                    "upper_right_cm": [90, 90, 27.699970000000004]}
        a = resolve(tallies_options={"mesh": explicit})
        b = resolve()
        self.assertEqual(a.tallies_options.mesh, "stock_pool_90cm")
        self.assertEqual(a.spec_hash(), b.spec_hash())

    def test_a_custom_mesh_is_kept_and_changes_the_hash(self):
        custom = {"dimension": [10, 10, 2], "lower_left_cm": [-30, -30, -10], "upper_right_cm": [30, 30, 10]}
        s = resolve(tallies=["flux_mesh"], tallies_options={"mesh": custom})
        self.assertEqual(s.tallies_options.mesh, {"dimension": [10, 10, 2], "lower_left_cm": [-30.0, -30.0, -10.0],
                                                  "upper_right_cm": [30.0, 30.0, 10.0]})
        self.assertNotEqual(s.spec_hash(), resolve(tallies=["flux_mesh"]).spec_hash())
        self.assertEqual(tallies.bin_counts(s)["flux_mesh"]["bins_total"], 200)
        again = Spec.from_json(s.to_json())                       # round trip
        self.assertEqual(again.to_dict(), s.to_dict())

    def test_bad_meshes_name_the_field(self):
        good = {"dimension": [4, 4, 4], "lower_left_cm": [-1, -1, -1], "upper_right_cm": [1, 1, 1]}
        cases = [
            ("nonsense", "tallies_options.mesh"),
            ({"dimension": [4, 4, 4]}, "tallies_options.mesh"),
            ({**good, "dimension": [4, 4]}, "tallies_options.mesh.dimension"),
            ({**good, "dimension": [0, 4, 4]}, "tallies_options.mesh.dimension"),
            ({**good, "dimension": [4.5, 4, 4]}, "tallies_options.mesh.dimension"),
            ({**good, "lower_left_cm": [-1, -1, float("nan")]}, "tallies_options.mesh.lower_left_cm"),
            ({**good, "lower_left_cm": [1, -1, -1]}, "tallies_options.mesh"),
            ({**good, "upper_right_cm": [1, 1, -1]}, "tallies_options.mesh"),
        ]
        for mesh, field in cases:
            with self.subTest(mesh=mesh):
                with self.assertRaises(InvalidSpec) as ctx:
                    resolve(tallies_options={"mesh": mesh})
                self.assertEqual(ctx.exception.field, field)


class EnergyBins(unittest.TestCase):
    def test_names_and_counts(self):
        self.assertEqual(resolve().tallies_options.energy_bins, "energy_bins_238")
        s = resolve(tallies_options={"energy_bins": "energy_bins_mpact_51_groups"})
        self.assertEqual(energy_bins.bin_count(s.tallies_options.energy_bins), 51)

    def test_custom_edges(self):
        s = resolve(tallies=["beam_port_1_5_detector_flux", "beam_port_2_sphere_flux"],
                    tallies_options={"energy_bins": [1e-5, 1.0, 1e3, 1e7], "polar_bins": 10})
        self.assertEqual(s.tallies_options.energy_bins, [1e-5, 1.0, 1e3, 1e7])
        c = tallies.bin_counts(s)
        self.assertEqual(c["beam_port_1_5_detector_flux"]["bins_total"], 3)
        self.assertEqual(c["beam_port_2_sphere_flux"]["bins_total"], 3 * 3 * 10)
        self.assertEqual(Spec.from_json(s.to_json()).to_dict(), s.to_dict())
        self.assertNotEqual(s.spec_hash(), resolve(tallies=s.tallies, tallies_options={"polar_bins": 10}).spec_hash())

    def test_custom_edges_equal_to_the_named_structure_are_stored_under_its_name(self):
        s = resolve(tallies_options={"energy_bins": list(ENERGY_BIN_EDGES_238_EV)})
        self.assertEqual(s.tallies_options.energy_bins, "energy_bins_238")
        self.assertEqual(s.spec_hash(), resolve().spec_hash())

    def test_bad_values_name_the_field(self):
        for value in ("nonsense", [1.0], [], [1.0, 1.0], [3.0, 2.0], [-1.0, 5.0], [1.0, float("nan")], [1.0, True], 5, None,
                      [0.0, 1.0] + [float(i) for i in range(2, 100_005)]):
            with self.subTest(value=str(value)[:40]):
                with self.assertRaises(InvalidSpec) as ctx:
                    resolve(tallies_options={"energy_bins": value})
                self.assertEqual(ctx.exception.field, "tallies_options.energy_bins")
        resolve(tallies_options={"energy_bins": [0.0, 1.0]})                       # zero is allowed as the lowest edge


class BinCountsAndGuard(unittest.TestCase):
    def test_energy_group_counts_come_from_the_list(self):
        self.assertEqual(ENERGY_BIN_COUNTS["energy_bins_238"], len(ENERGY_BIN_EDGES_238_EV) - 1)
        self.assertEqual(len(ENERGY_BIN_EDGES_238_EV), 239)
        self.assertEqual(list(ENERGY_BIN_EDGES_238_EV), sorted(ENERGY_BIN_EDGES_238_EV))
        self.assertEqual((ENERGY_BIN_EDGES_238_EV[0], ENERGY_BIN_EDGES_238_EV[-1]), (1e-05, 2e7))

    def test_bundle_numbers(self):
        s = resolve(preset="netl_boc505_tallies")
        c = tallies.bin_counts(s)
        self.assertEqual(c["beam_port_2_detector_flux"], {"tallies": 3, "bins_each": 238 * 90 * 90, "bins_total": 3 * 238 * 90 * 90})
        self.assertEqual(c["beam_port_2_detector_current"]["bins_each"], 238 * 90 * 90)
        self.assertEqual(c["beam_port_2_sphere_flux"], {"tallies": 3, "bins_each": 238 * 90, "bins_total": 3 * 238 * 90})
        self.assertEqual(c["flux_mesh"], {"tallies": 1, "bins_each": 67_500, "bins_total": 67_500})
        self.assertEqual(tallies.total_bins(s), sum(v["bins_total"] for v in c.values()))
        self.assertAlmostEqual(tallies.statepoint_megabytes(1_000_000), 16.0)

    def test_no_tallies_no_bins(self):
        self.assertEqual(tallies.bin_counts(resolve()), {})
        self.assertEqual(tallies.total_bins(resolve()), 0)

    def test_angle_bins_and_groups_scale_the_counts(self):
        s = resolve(tallies=["beam_port_1_5_detector_flux", "beam_port_2_sphere_flux"], tallies_options={"energy_bins": "energy_bins_mpact_51_groups", "polar_bins": 10})
        c = tallies.bin_counts(s)
        self.assertEqual(c["beam_port_1_5_detector_flux"]["bins_total"], 51)
        self.assertEqual(c["beam_port_2_sphere_flux"]["bins_total"], 3 * 51 * 10)

    def test_size_guard_refuses_and_can_be_raised_on_purpose(self):
        with self.assertRaises(InvalidSpec) as ctx:
            resolve(preset="netl_boc505_tallies", tallies_options={"max_total_bins": 1_000_000})
        self.assertEqual(ctx.exception.field, "tallies_options.max_total_bins")
        self.assertIn("11,698,560", str(ctx.exception))
        ok = resolve(preset="netl_boc505_tallies", tallies_options={"max_total_bins": 12_000_000})
        self.assertEqual(ok.tallies_options.max_total_bins, 12_000_000)
        with self.assertRaises(InvalidSpec) as ctx:                       # the default limit is 20 million
            resolve(tallies=["beam_port_2_detector_flux", "beam_port_2_detector_current"], tallies_options={"polar_bins": 200, "azimuthal_bins": 200})
        self.assertEqual(ctx.exception.field, "tallies_options.max_total_bins")


class RulesPresetsAndAssumptions(unittest.TestCase):
    def test_detector_tallies_need_the_detector_geometry(self):
        no_det = {"environment": {"detector_geometry": False, "barite": False}}
        for name in ("beam_port_2_detector_flux", "beam_port_2_detector_current", "beam_port_2_sphere_flux", "beam_port_1_5_detector_flux"):
            with self.subTest(name=name):
                with self.assertRaises(InvalidSpec) as ctx:
                    resolve(tallies=[name], **no_det)
                self.assertEqual(ctx.exception.field, "tallies")
        resolve(tallies=["flux_mesh"], **no_det)                       # the mesh does not need detectors

    def test_unknown_and_duplicate_names(self):
        with self.assertRaises(InvalidSpec) as ctx:
            resolve(tallies=["bp3_flux"])
        self.assertEqual(ctx.exception.field, "tallies[0]")
        with self.assertRaises(InvalidSpec):
            resolve(tallies=["beam_port_2_detector_flux", "beam_port_2_detector_flux"])

    def test_the_base_preset_stays_cheap_and_the_full_one_has_four(self):
        self.assertEqual(resolve().tallies, [])
        full = resolve(preset="netl_boc505_tallies")
        self.assertEqual(tuple(full.tallies), BUNDLE)
        self.assertNotIn("beam_port_1_5_detector_flux", full.tallies)                         # the reference file switches it off
        self.assertTrue({"netl_boc505@1", "netl_boc505_tallies@1", "netl_public_fresh_fuel@1"} <= {p["preset"] for p in describe()})
        self.assertEqual(full.preset, "netl_boc505_tallies@1")
        base = resolve().to_dict()
        other = full.to_dict()
        for key in ("name", "preset", "tallies"):
            base.pop(key), other.pop(key)
        self.assertEqual(base, other)                                         # the only difference is the tally list

    def test_a_keyword_list_replaces_the_bundle(self):
        self.assertEqual(resolve(preset="netl_boc505_tallies", tallies=["beam_port_1_5_detector_flux"]).tallies, ["beam_port_1_5_detector_flux"])
        self.assertEqual(resolve(preset="netl_boc505_tallies", tallies=[]).tallies, [])

    def test_assumption_a11_applies_only_with_tallies(self):
        self.assertNotIn("A11", {a["id"] for a in active_assumptions(resolve())})
        a11 = [a for a in active_assumptions(resolve(tallies=["beam_port_2_detector_flux"])) if a["id"] == "A11"]
        self.assertEqual(len(a11), 1)
        self.assertEqual(a11[0]["fields"]["tallies"], ["beam_port_2_detector_flux"])
        self.assertEqual(a11[0]["fields"]["tallies_options.mesh"], "stock_pool_90cm")

    def test_table_entries_are_complete(self):
        for name, d in tallies.TALLY_PRESETS.items():
            with self.subTest(name=name):
                self.assertIn(d["kind"], ("cell_flux", "surface_current", "mesh_flux"))
                self.assertIn(d["score"], ("flux", "current"))
                self.assertTrue(d["doc"] and d["filters"])
                self.assertIn("neutron", d["filters"])


def cli(*args):
    p = subprocess.run([sys.executable, "-m", "automc", "triga", *args], cwd=SRC, capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout, p.stderr


class CommandLine(unittest.TestCase):
    def test_tallies_option(self):
        code, out, _ = cli("spec", "--tallies", "beam_port_2_detector_flux,beam_port_1_5_detector_flux")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["tallies"], ["beam_port_2_detector_flux", "beam_port_1_5_detector_flux"])
        code, out, _ = cli("spec", "--preset", "netl_boc505_tallies", "--tallies", "none")
        self.assertEqual(json.loads(out)["tallies"], [])
        code, out, _ = cli("spec", "--preset", "netl_boc505_tallies")
        self.assertEqual(json.loads(out)["tallies"], list(BUNDLE))

    def test_errors_exit_2_and_name_the_field(self):
        code, out, _ = cli("spec", "--tallies", "bp3_flux", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["field"], "tallies[0]")

    def test_views_show_the_warnings_for_the_bundle(self):
        code, out, _ = cli("spec", "--preset", "netl_boc505_tallies", "--views")
        self.assertEqual(code, 0)
        warnings = json.loads(out)["warnings"]
        self.assertTrue(any("beam_port_2_detector_flux" in w and "1,927,800" in w for w in warnings), warnings)
        self.assertTrue(any("beam_port_2_detector_current" in w for w in warnings))


# ======================================================================================================================
# real builds
# ======================================================================================================================
TINY = {"run": {"particles": 1000, "batches": 3, "inactive": 1, "seed": 7}}


@unittest.skipIf(SKIP, SKIP or "")
class BuiltTallies(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import openmc
        cls.openmc = openmc
        cls.ws = Path(tempfile.mkdtemp(prefix="triga_tallies_"))
        cls.full = build_input("openmc", "netl", preset="netl_boc505_tallies", name="full", workspace=cls.ws, **TINY)
        cls.model = openmc.Model.from_model_xml(str(Path(cls.full.outdir) / "model.xml"))
        cls.by_name = {t.name: t for t in cls.model.tallies}
        cells = cls.model.geometry.get_all_cells()
        cls.cell_name = {c.id: c.name for c in cells.values()}

    def kinds(self, tally):
        return [type(f).__name__ for f in tally.filters]

    def test_names_and_counts(self):
        expect = ({"my_spatial_flux"}
                  | {f"beam_port_2_detector_{i}_flux" for i in (1, 2, 3)}
                  | {f"beam_port_2_detector_{i}_entrance_surface_current" for i in (1, 2, 3)}
                  | {f"beam_port_2_sphere_detector_{i}_flux" for i in (1, 2, 3)})
        generic = set(self.by_name) - expect
        self.assertEqual(len(generic), 8, generic)                           # the problem builder's own tallies are untouched
        self.assertLessEqual(expect, set(self.by_name))
        self.assertEqual(len(self.model.tallies), 8 + 10)
        self.assertEqual(self.full.summary["tallies"], 18)
        tp = self.full.summary["tally_presets"]
        self.assertEqual(tp["requested"], list(BUNDLE))
        self.assertEqual(sorted(tp["tallies_added"]), sorted(expect))
        self.assertEqual(tp["bins"]["beam_port_2_detector_flux"]["bins_total"], 3 * 238 * 90 * 90)

    def test_cell_flux_filters_follow_his_order_and_sizes(self):
        for i in (1, 2, 3):
            t = self.by_name[f"beam_port_2_detector_{i}_flux"]
            self.assertEqual(self.kinds(t), ["CellFilter", "EnergyFilter", "PolarFilter", "AzimuthalFilter", "ParticleFilter"])
            self.assertEqual([f.num_bins for f in t.filters], [1, 238, 90, 90, 1])
            self.assertEqual(self.cell_name[t.filters[0].bins[0]], f"beam_port_2_detector_{i}")
            self.assertEqual(list(t.scores), ["flux"])
        e = self.by_name["beam_port_2_detector_1_flux"].filters[1].values
        self.assertEqual((float(e[0]), float(e[-1])), (1e-05, 2e7))
        self.assertEqual(float(self.by_name["beam_port_2_detector_1_flux"].filters[2].values[-1]), 3.141592653589793)

    def test_spheres_have_no_azimuthal_filter(self):
        for i in (1, 2, 3):
            t = self.by_name[f"beam_port_2_sphere_detector_{i}_flux"]
            self.assertEqual(self.kinds(t), ["CellFilter", "EnergyFilter", "PolarFilter", "ParticleFilter"])
            self.assertEqual(self.cell_name[t.filters[0].bins[0]], f"beam_port_2_sphere_detector_{i}")

    def test_current_tally_pairs_each_surface_with_its_detector_and_the_air_fill(self):
        for i in (1, 2, 3):
            t = self.by_name[f"beam_port_2_detector_{i}_entrance_surface_current"]
            self.assertEqual(self.kinds(t), ["SurfaceFilter", "CellFilter", "CellFromFilter", "EnergyFilter", "ParticleFilter",
                                             "PolarFilter", "AzimuthalFilter"])
            self.assertEqual(list(t.scores), ["current"])
            self.assertEqual(self.cell_name[t.filters[1].bins[0]], f"beam_port_2_detector_{i}")
            self.assertEqual(self.cell_name[t.filters[2].bins[0]], "beam_port_2_air_fill")
            surf = {s.id: s.name for s in self.model.geometry.get_all_surfaces().values()}
            self.assertEqual(surf[t.filters[0].bins[0]], f"beam_port_2_detector_{i}_entrance_surface")

    def test_mesh_tally_matches_the_reference(self):
        t = self.by_name["my_spatial_flux"]
        self.assertEqual(self.kinds(t), ["MeshFilter", "ParticleFilter"])
        mesh = t.filters[0].mesh
        self.assertEqual(list(mesh.dimension), [150, 150, 3])
        self.assertEqual([float(x) for x in mesh.lower_left], [-90.0, -90.0, -27.699970000000004])
        self.assertEqual([float(x) for x in mesh.upper_right], [90.0, 90.0, 27.699970000000004])

    def test_the_water_cavity_is_still_there(self):
        self.assertEqual(self.full.summary["fill_cells"], {"beam_port_2_water_fill": 1, "beam_port_2_air_fill": 1,
                                                           "outside_pool_region": 1})

    def test_a_second_fresh_process_writes_the_same_file(self):
        again = build_input("openmc", "netl", preset="netl_boc505_tallies", name="full-again", workspace=self.ws, **TINY)
        self.assertEqual((Path(again.outdir) / "model.xml").read_bytes(), (Path(self.full.outdir) / "model.xml").read_bytes())

    def test_custom_mesh_mpact51_and_no_generic_tallies(self):
        custom = {"dimension": [10, 12, 2], "lower_left_cm": [-30, -30, -10], "upper_right_cm": [30, 30, 10]}
        r = build_input("openmc", "netl", name="custom", workspace=self.ws, tallies=["flux_mesh", "beam_port_2_sphere_flux"],
                        tallies_options={"mesh": custom, "energy_bins": "energy_bins_mpact_51_groups", "library_default_tallies": False}, **TINY)
        m = self.openmc.Model.from_model_xml(str(Path(r.outdir) / "model.xml"))
        self.assertEqual(sorted(t.name for t in m.tallies),
                         ["beam_port_2_sphere_detector_1_flux", "beam_port_2_sphere_detector_2_flux",
                          "beam_port_2_sphere_detector_3_flux", "my_spatial_flux"])
        by = {t.name: t for t in m.tallies}
        mesh = by["my_spatial_flux"].filters[0].mesh
        self.assertEqual(list(mesh.dimension), [10, 12, 2])
        self.assertEqual([float(x) for x in mesh.upper_right], [30.0, 30.0, 10.0])
        self.assertEqual(by["beam_port_2_sphere_detector_1_flux"].filters[1].num_bins, 51)

    def test_beam_port_1_5_detector_flux_builds_when_asked(self):
        r = build_input("openmc", "netl", name="bp15", workspace=self.ws, tallies=["beam_port_1_5_detector_flux"],
                        tallies_options={"energy_bins": [1e-5, 1.0, 1e3, 1e7]}, **TINY)
        m = self.openmc.Model.from_model_xml(str(Path(r.outdir) / "model.xml"))
        t = {t.name: t for t in m.tallies}["beam_port_1_5_detector_flux"]
        self.assertEqual(self.kinds(t), ["CellFilter", "EnergyFilter", "ParticleFilter"])
        self.assertEqual([float(x) for x in t.filters[1].values], [1e-5, 1.0, 1e3, 1e7])    # custom energy edges reach the model

    def test_command_line_build_prints_the_tally_line(self):
        code, out, err = cli("build", "--preset", "netl_boc505_tallies", "--name", "cli-full", "--workspace", str(self.ws),
                             "--set", "run.particles=1000", "--set", "run.batches=3", "--set", "run.inactive=1")
        self.assertEqual(code, 0, err)
        self.assertIn("tallies   10 added for flux_mesh, beam_port_2_detector_flux, beam_port_2_detector_current, beam_port_2_sphere_flux", out)
        self.assertIn("11,698,560 bins in total", out)
        self.assertIn("plus 8 generic tallies", out)


if __name__ == "__main__":
    unittest.main()
