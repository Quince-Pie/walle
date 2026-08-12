import unittest

import liquid_glass_unseen_holdout as holdout


class UnseenHoldoutTests(unittest.TestCase):
    def test_split_mix_64_matches_published_zero_vector(self) -> None:
        self.assertEqual(holdout.split_mix_64(0), 0xE220A8397B1DCDAF)

    def test_opaque_pattern_has_full_alpha(self) -> None:
        texel = holdout.seeded_texel(
            "prospective-opaque-seeded-v2",
            x=17,
            y=29,
            level=1,
        )
        self.assertEqual(texel[3], 255)

    def test_premultiplied_pattern_never_exceeds_alpha(self) -> None:
        for y in range(8):
            for x in range(8):
                texel = holdout.seeded_texel(
                    "prospective-premultiplied-seeded-v2",
                    x=x,
                    y=y,
                    level=2,
                )
                self.assertTrue(all(channel <= texel[3] for channel in texel[:3]))

    def test_fnv_empty_vector(self) -> None:
        self.assertEqual(holdout.fnv1a64(b""), "cbf29ce484222325")

    def test_level_generation_is_stable_and_sized(self) -> None:
        first = holdout.generate_level(
            "prospective-premultiplied-seeded-v2",
            width=7,
            height=5,
            level=1,
        )
        second = holdout.generate_level(
            "prospective-premultiplied-seeded-v2",
            width=7,
            height=5,
            level=1,
        )
        self.assertEqual(len(first), 7 * 5 * 4)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
