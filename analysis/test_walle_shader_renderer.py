import math
import unittest
from pathlib import Path

import numpy as np

from walle_shader_renderer import (
    GLASS_DOWN_FACTOR,
    WalleShaderRenderer,
    prepare_glass_texture,
)


class WalleShaderRendererTests(unittest.TestCase):
    def test_glass_preprocess_has_production_dimensions(self) -> None:
        image = np.zeros((80, 128, 4), dtype=np.uint8)
        image[..., 3] = 255
        glass = prepare_glass_texture(image, regular=False)
        self.assertEqual(
            glass.shape,
            (80 // GLASS_DOWN_FACTOR, 128 // GLASS_DOWN_FACTOR, 4),
        )

    def test_time_one_is_the_exact_incoming_source(self) -> None:
        width = 96
        height = 64
        y, x = np.indices((height, width))
        outgoing = np.empty((height, width, 4), dtype=np.uint8)
        incoming = np.empty((height, width, 4), dtype=np.uint8)
        outgoing[..., 0] = x * 255 // (width - 1)
        outgoing[..., 1] = y * 255 // (height - 1)
        outgoing[..., 2] = 31
        incoming[..., 0] = (x * 17 + y * 5) & 255
        incoming[..., 1] = (x * 3 + y * 19) & 255
        incoming[..., 2] = (x * 11 + y * 7) & 255
        outgoing[..., 3] = 255
        incoming[..., 3] = 255
        center = (width * 0.25, height * 0.30)
        farthest = max(
            math.hypot(px - center[0], py - center[1])
            for px in (0.0, float(width))
            for py in (0.0, float(height))
        )

        with WalleShaderRenderer(
            width=width,
            height=height,
            vertex_shader=Path("shaders/vert.glsl"),
            fragment_shader=Path("shaders/frag.glsl"),
        ) as renderer:
            outgoing_textures = renderer.upload_wallpaper(
                outgoing,
                regular=False,
            )
            incoming_textures = renderer.upload_wallpaper(
                incoming,
                regular=False,
            )
            try:
                rendered = renderer.render(
                    outgoing=outgoing_textures,
                    incoming=incoming_textures,
                    time=1.0,
                    center_top_left=center,
                    maximum_radius=farthest * 1.03,
                    regular=False,
                )
            finally:
                outgoing_textures.release()
                incoming_textures.release()

        delta = np.abs(
            rendered[..., :3].astype(np.int16) - incoming[..., :3].astype(np.int16)
        )
        self.assertLessEqual(int(delta.max()), 1)
        self.assertLess(float(delta.mean()), 0.02)


if __name__ == "__main__":
    unittest.main()
