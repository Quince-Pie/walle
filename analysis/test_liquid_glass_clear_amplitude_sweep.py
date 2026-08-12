import unittest

import numpy as np

from liquid_glass_clear_amplitude_sweep import (
    AMPLITUDES,
    ModelSpec,
    dense_training_background,
    evaluate_model,
    fit_amplitude_mask,
    linear_to_srgb,
    model_basis,
    srgb_to_linear,
)


class ClearAmplitudeSweepTests(unittest.TestCase):
    def test_dense_training_names_reuse_historical_sources(self) -> None:
        self.assertEqual(
            dense_training_background(1),
            "noise-rgb-a001-sweep-train-00",
        )
        self.assertEqual(
            dense_training_background(17),
            "noise-rgb-a017-tomography-train-00",
        )
        self.assertEqual(
            dense_training_background(31),
            "noise-rgb-a031-tomography-train-00",
        )
        self.assertEqual(
            dense_training_background(47),
            "noise-rgb-a047-tomography-train-00",
        )
        self.assertEqual(
            dense_training_background(64),
            "noise-rgb-a064-kernel-train-00",
        )
        with self.assertRaises(ValueError):
            dense_training_background(0)
        with self.assertRaises(ValueError):
            dense_training_background(65)

    def test_modulo_partition_withholds_both_parities(self) -> None:
        amplitudes = np.asarray(AMPLITUDES, dtype=np.float64)
        fitting = fit_amplitude_mask(amplitudes)

        self.assertEqual(amplitudes[fitting].astype(int).tolist()[:4], [0, 1, 4, 5])
        self.assertEqual(amplitudes[~fitting].astype(int).tolist()[:4], [2, 3, 6, 7])
        self.assertEqual(set((amplitudes[fitting] % 2).astype(int)), {0, 1})
        self.assertEqual(set((amplitudes[~fitting] % 2).astype(int)), {0, 1})

    def test_srgb_transfer_round_trips_code_domain(self) -> None:
        codes = np.linspace(0.0, 255.0, 1021)
        reconstructed = linear_to_srgb(
            srgb_to_linear(codes / 255.0)
        ) * 255.0

        np.testing.assert_allclose(reconstructed, codes, atol=1e-11)

    def test_fraction_basis_removes_duplicate_constant_column(self) -> None:
        amplitudes = np.asarray(AMPLITUDES, dtype=np.float64)
        spec = ModelSpec(
            name="test",
            family="fraction-basis",
            source_space="code",
            output_space="code",
            fractions=(-1.0, -0.5, 0.5, 1.0),
        )

        basis = model_basis(spec, amplitudes)

        self.assertEqual(np.linalg.matrix_rank(basis), 2)
        self.assertEqual(basis.shape[1], 2)

    def test_interval_fit_predicts_withheld_affine_parity_codes(self) -> None:
        amplitudes = np.asarray(AMPLITUDES, dtype=np.float64)
        fitting = fit_amplitude_mask(amplitudes)
        continuous = np.column_stack(
            (
                120.0 + amplitudes + 2.0 * (amplitudes % 2),
                200.0 - amplitudes - 3.0 * (amplitudes % 2),
            )
        )
        actual = np.floor(continuous + 0.5).astype(np.uint8)
        spec = ModelSpec(
            name="affine-parity",
            family="polynomial",
            source_space="code",
            output_space="code",
            polynomial_degree=1,
            include_odd_residue=True,
        )

        report = evaluate_model(
            spec,
            amplitudes,
            actual,
            fit_mask=fitting,
        )

        self.assertEqual(report["validation"]["exactChannelFraction"], 1.0)
        self.assertEqual(report["validation"]["maximumAbsoluteCodes"], 0)
        self.assertEqual(
            report["leaveOneResidueOut"]["exactChannelFraction"],
            1.0,
        )

    def test_all_amplitude_refit_finds_quantization_interval_solution(self) -> None:
        amplitudes = np.asarray(AMPLITUDES, dtype=np.float64)
        fitting = fit_amplitude_mask(amplitudes)
        generator = np.random.default_rng(7)
        slopes = generator.uniform(-0.4, 0.4, 100)
        intercepts = generator.uniform(145.0, 160.0, 100)
        odd_residues = generator.uniform(-0.45, 0.45, 100)
        continuous = (
            intercepts[np.newaxis]
            + amplitudes[:, np.newaxis] * slopes[np.newaxis]
            + (amplitudes[:, np.newaxis] % 2.0)
            * odd_residues[np.newaxis]
        )
        actual = np.floor(continuous + 0.5).astype(np.uint8)
        spec = ModelSpec(
            name="affine-parity",
            family="polynomial",
            source_space="code",
            output_space="code",
            polynomial_degree=1,
            include_odd_residue=True,
        )

        report = evaluate_model(
            spec,
            amplitudes,
            actual,
            fit_mask=fitting,
        )

        self.assertEqual(
            report["refitAllAmplitudes"]["exactChannelFraction"],
            1.0,
        )


if __name__ == "__main__":
    unittest.main()
