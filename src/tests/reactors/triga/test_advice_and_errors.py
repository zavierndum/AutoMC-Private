"""Warnings, the run-time estimate, and error codes and payloads. No libraries needed."""
import unittest

from tools.reactors.triga import BuildTimeout, InvalidSpec, MissingLibrary, TrigaError, UnsupportedFeature, advice, resolve
from tools.reactors.triga.errors import error_payload, exit_code_for
from tests.reactors.triga._packs import require_restricted  # noqa: E402


def setUpModule():
    require_restricted()


class Warnings(unittest.TestCase):
    def w(self, **kw):
        return advice.for_spec(resolve(**kw), cpu_count=16)

    def test_the_default_spec_has_no_warnings(self):
        self.assertEqual(self.w(), [])

    def test_temperatures_outside_the_library_range(self):
        msgs = self.w(conditions={"water_temperature_K": 200})
        self.assertTrue(any("conditions.water_temperature_K" in m and "250" in m for m in msgs), msgs)
        msgs = self.w(conditions={"fuel_temperature_K": 3000})
        self.assertTrue(any("conditions.fuel_temperature_K" in m and "2500" in m for m in msgs), msgs)

    def test_explicit_nearest_far_from_a_library_temperature_will_fail(self):
        msgs = self.w(conditions={"water_temperature_K": 320, "temperature_method": "nearest", "water_density_gcc": "auto"})
        self.assertTrue(any("will fail" in m for m in msgs), msgs)
        self.assertEqual(self.w(conditions={"water_temperature_K": 320, "water_density_gcc": "auto"}), [])                  # auto picks interpolation
        self.assertEqual(self.w(conditions={"water_temperature_K": 320, "temperature_method": "interpolation", "water_density_gcc": "auto"}), [])

    def test_extreme_rod_states(self):
        self.assertTrue(any("deeply subcritical" in m for m in self.w(rods=0)))
        self.assertTrue(any("strongly supercritical" in m for m in self.w(rods=1)))
        self.assertEqual(self.w(rods={"TR": 0.0}), [])                                          # only one rod in
        self.assertEqual(self.w(rods=0.5), [])

    def test_long_runs_are_flagged(self):
        msgs = self.w(run={"size": "production"})
        self.assertTrue(any("estimated run time is long" in m and " h" in m for m in msgs), msgs)
        self.assertFalse(any("estimated run time" in m for m in self.w(run={"size": "smoke"})))

    def test_tally_cost(self):
        s = resolve(tallies=["beam_port_2_detector_flux", "flux_mesh", "beam_port_1_5_detector_flux"])
        bins = advice.tally_bins(s)
        self.assertEqual(bins, {"beam_port_2_detector_flux": 3 * 238 * 90 * 90, "flux_mesh": 67_500, "beam_port_1_5_detector_flux": 238})
        msgs = advice.for_spec(s, cpu_count=16)
        self.assertTrue(any("'beam_port_2_detector_flux'" in m and "1,927,800" in m for m in msgs), msgs)
        self.assertFalse(any("flux_mesh" in m or "beam_port_1_5_detector_flux" in m for m in msgs))
        small = resolve(tallies=["beam_port_2_detector_flux"], tallies_options={"polar_bins": 10, "azimuthal_bins": 10})
        self.assertEqual(advice.tally_bins(small), {"beam_port_2_detector_flux": 3 * 238 * 100})
        self.assertFalse(any("beam_port_2_detector_flux" in m for m in advice.for_spec(small, cpu_count=16)))
        self.assertEqual(advice.tally_bins(resolve(tallies=["beam_port_2_sphere_flux"], tallies_options={"energy_bins": "energy_bins_mpact_51_groups"})),
                         {"beam_port_2_sphere_flux": 3 * 51 * 90})


class RunEstimate(unittest.TestCase):
    def test_arithmetic(self):
        e = advice.run_time_estimate(resolve(run={"particles": 1_000_000, "batches": 100, "inactive": 20}), cpu_count=16)
        self.assertEqual(e["histories"], 100_000_000)
        self.assertEqual(e["threads"], 16)
        self.assertAlmostEqual(e["seconds_low"], 100_000_000 / (advice.RATE_PER_THREAD_HIGH * 16), delta=0.1)
        self.assertAlmostEqual(e["seconds_high"], 100_000_000 / (advice.RATE_PER_THREAD_LOW * 16), delta=0.1)
        self.assertLess(e["seconds_low"], e["seconds_high"])
        self.assertIn("100,000,000 histories on 16 threads", e["text"])

    def test_threads_in_the_spec_win_and_scale_the_estimate(self):
        a = advice.run_time_estimate(resolve(run={"threads": 4}), cpu_count=16)
        b = advice.run_time_estimate(resolve(run={"threads": 16}), cpu_count=16)
        self.assertEqual(a["threads"], 4)
        self.assertAlmostEqual(a["seconds_high"] / b["seconds_high"], 4.0, places=1)

    def test_run_sizes_order(self):
        sec = {s: advice.run_time_estimate(resolve(run={"size": s}), cpu_count=16)["seconds_high"] for s in ("smoke", "standard", "production")}
        self.assertLess(sec["smoke"], sec["standard"])
        self.assertLess(sec["standard"], sec["production"])
        self.assertGreater(sec["production"], 3600 * 10)             # production is an overnight run, as the spec says


class ErrorCodes(unittest.TestCase):
    def test_exit_codes(self):
        self.assertEqual(exit_code_for(InvalidSpec("f", "m")), 2)
        self.assertEqual(exit_code_for(UnsupportedFeature("f", "m")), 3)
        self.assertEqual(exit_code_for(MissingLibrary("m")), 4)
        self.assertEqual(exit_code_for(BuildTimeout("m")), 5)
        self.assertEqual(exit_code_for(TrigaError("m")), 1)
        self.assertEqual(exit_code_for(ValueError("m")), 1)

    def test_payload(self):
        p = error_payload(InvalidSpec("rods.TR", "too big"))
        self.assertEqual(p, {"type": "InvalidSpec", "field": "rods.TR", "message": "rods.TR: too big", "exit_code": 2})
        self.assertEqual(error_payload(UnsupportedFeature("config", "later"))["field"], "config")
        self.assertIsNone(error_payload(MissingLibrary("x"))["field"])


if __name__ == "__main__":
    unittest.main()
