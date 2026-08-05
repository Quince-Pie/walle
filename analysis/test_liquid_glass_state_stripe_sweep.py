import unittest

import numpy as np

from liquid_glass_state_stripe_sweep import (
    CHANNELS,
    EXPECTED_AMPLITUDES,
    EXPECTED_ORIENTATIONS,
    EXPECTED_PATCH_SIDE,
    EXPECTED_RIG,
    EXPECTED_SWEEP_KIND,
    FLAT_INTERVENTIONS,
    FLAT_RIG,
    FLAT_SWEEP_KIND,
    StateStripeSweep,
    _rig_configuration,
    _values_for_radius,
    expected_control,
    expected_sites,
    mip_endpoint_envelope,
    orientation_isotropy,
    orthogonal_invariance,
    response_measurements,
)


def shape() -> tuple[int, ...]:
    return (
        len(EXPECTED_AMPLITUDES),
        len(EXPECTED_ORIENTATIONS),
        len(expected_sites()),
        EXPECTED_PATCH_SIDE,
        EXPECTED_PATCH_SIDE,
        CHANNELS,
    )


class StateStripeSweepTests(unittest.TestCase):
    def make_sweep(self) -> StateStripeSweep:
        sites = expected_sites()
        placeholder = np.empty(shape(), dtype=np.uint8)
        sweep = StateStripeSweep(
            manifest={"sourceDesign": {"sampleSites": sites}},
            control=placeholder,
            interventions={},
        )
        control = expected_control(sweep)
        return StateStripeSweep(
            manifest=sweep.manifest,
            control=control,
            interventions={},
        )

    def test_sites_are_strictly_inside_declared_states(self) -> None:
        for site in expected_sites():
            self.assertGreater(
                site["normalizedRadiusMinimum"],
                site["geometryStateLowerBoundary"],
            )
            self.assertLess(
                site["normalizedRadiusMaximum"],
                site["geometryStateUpperBoundary"],
            )

    def test_control_encodes_each_edge_direction(self) -> None:
        sweep = self.make_sweep()
        center = EXPECTED_PATCH_SIDE // 2
        for site_index, site in enumerate(sweep.sites):
            values = sweep.control[127, 0, site_index, center, :, 0]
            if site["transitionSign"] > 0:
                self.assertEqual(values[center - 1], 128)
                self.assertEqual(values[center], 255)
            else:
                self.assertEqual(values[center - 1], 255)
                self.assertEqual(values[center], 128)

    def test_ideal_steps_are_invariant_and_have_unit_gain(self) -> None:
        sweep = self.make_sweep()
        stream = sweep.control
        for record in orthogonal_invariance(stream).values():
            self.assertEqual(record["changedValues"], 0)
        self.assertEqual(
            orientation_isotropy(stream)["changedValues"],
            0,
        )
        response = response_measurements(sweep, stream)
        np.testing.assert_allclose(
            response["gainRangeCodesPerAmplitude"],
            (1, 1),
        )
        for record in response["phaseRecords"]:
            self.assertAlmostEqual(record["kernelSum"], 1)

    def test_identical_mip_endpoints_accept_every_value(self) -> None:
        sweep = self.make_sweep()
        stream = sweep.control
        result = mip_endpoint_envelope(
            stream,
            stream,
            stream,
            sweep.sites,
        )
        self.assertTrue(
            result["binary16RoundedEndpointEnvelope"]["allCompatible"]
        )

    def test_flat_rig_uses_four_stationary_radii(self) -> None:
        flat, interventions, radii = _rig_configuration({
            "rigVersion": FLAT_RIG,
            "sweepKind": FLAT_SWEEP_KIND,
        })
        self.assertTrue(flat)
        self.assertEqual(interventions, FLAT_INTERVENTIONS)
        self.assertEqual(radii, (0, 1, 2, 4))

    def test_default_and_flat_values_are_distinct(self) -> None:
        flat, _, _ = _rig_configuration({
            "rigVersion": EXPECTED_RIG,
            "sweepKind": EXPECTED_SWEEP_KIND,
        })
        self.assertFalse(flat)
        default = _values_for_radius(1)
        flattened = _values_for_radius(1, flat_profile=True)
        self.assertNotIn("inputBlurOpacity0", default)
        self.assertEqual(flattened["inputBlurOpacity0"], 1)
        self.assertEqual(flattened["inputBlurOpacity4"], 1)
        self.assertEqual(
            flattened["inputInnerRefractionAmount"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
