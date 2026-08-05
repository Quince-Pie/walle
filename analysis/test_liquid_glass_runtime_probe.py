import tempfile
import unittest
from pathlib import Path

from liquid_glass_runtime_probe import (
    MATRIX_TYPE,
    bytes_fnv1a64,
    bytes_sha256,
    exact_sdf_render_records,
    exact_matrix,
    file_sha256,
    filter_inputs,
    mirror_value,
    normalized,
    one_repeated_record,
)


class RuntimeProbeTest(unittest.TestCase):
    def test_filter_inputs_walks_nested_runtime_tree(self) -> None:
        values = {"inputBlurRadius": 1}
        tree = {
            "layer": {
                "filters": [
                    {
                        "description": "glassBackground",
                        "inputValues": values,
                    }
                ]
            }
        }
        self.assertEqual(filter_inputs(tree, "glassBackground"), [values])
        self.assertEqual(
            one_repeated_record([values, values], "glassBackground"),
            values,
        )

    def test_exact_matrix_requires_twenty_floats(self) -> None:
        floats = [float(value) for value in range(20)]
        floats[15:] = [0, 0, 0, 1, 0]
        matrix = exact_matrix(
            {
                "objCType": MATRIX_TYPE,
                "lengthBytes": 80,
                "hex": "00" * 80,
                "float32LittleEndian": floats,
                "uint32LittleEndianHex": ["00000000"] * 20,
            }
        )
        self.assertEqual(matrix["rows4x5"][3], [0, 0, 0, 1, 0])

    def test_normalization_removes_process_addresses(self) -> None:
        self.assertEqual(
            normalized({"description": "<CGColor 0x123aB>"}),
            {"description": "<CGColor 0xADDR>"},
        )

    def test_file_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "value")
            path.write_bytes(b"abc")
            self.assertEqual(
                file_sha256(path),
                "ba7816bf8f01cfea414140de5dae2223"
                "b00361a396177a9cb410ff61f20015ad",
            )
        self.assertEqual(
            bytes_sha256(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223"
            "b00361a396177a9cb410ff61f20015ad",
        )
        self.assertEqual(bytes_fnv1a64(b"abc"), "e71fa2190541574b")

    def test_sdf_render_layout_is_exact(self) -> None:
        record = {
            "tree": "model",
            "ordinal": 0,
            "path": [1, 2],
            "class": "CASDFLayer",
            "bounds": "{{0, 0}, {2, 1}}",
            "rendered": True,
            "width": 2,
            "height": 1,
            "bytesPerRow": 8,
            "pixelFormat": "RGBA8 premultiplied-last sRGB",
            "rawFile": "sdf-model-0-rgba8.raw",
            "rawBytes": 8,
            "pngFile": "sdf-model-0.png",
            "pngBytes": 75,
            "fnv1a64": "0000000000000000",
            "channelMinima": [0, 0, 0, 0],
            "channelMaxima": [1, 2, 3, 4],
            "channelNonzeroCounts": [1, 1, 1, 1],
        }
        self.assertEqual(
            exact_sdf_render_records([record], "model"),
            [record],
        )

    def test_mirror_value_selects_one_labeled_child(self) -> None:
        records = [
            {
                "children": [
                    {"label": "distanceRange", "value": {"x": 1}},
                    {"label": "ovalization", "value": 0},
                ]
            }
        ]

        self.assertEqual(
            mirror_value(records, "distanceRange"),
            {"x": 1},
        )


if __name__ == "__main__":
    unittest.main()
