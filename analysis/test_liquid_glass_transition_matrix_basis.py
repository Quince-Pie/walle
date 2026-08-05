import unittest

from liquid_glass_transition_matrix_basis import (
    _branch_target,
    _page_relative_target,
)


class TransitionMatrixBasisTests(unittest.TestCase):
    def test_decodes_forward_and_backward_bl(self) -> None:
        code_address = 0x1800_1000
        for offset, target in (
            (0x20, code_address + 0x400),
            (0x40, code_address - 0x400),
        ):
            immediate = (target - code_address - offset) // 4
            instruction = 0x9400_0000 | (
                immediate & 0x03FF_FFFF
            )
            code = bytearray(offset + 4)
            code[offset : offset + 4] = instruction.to_bytes(
                4,
                "little",
            )
            self.assertEqual(
                _branch_target(
                    bytes(code),
                    offset=offset,
                    code_address=code_address,
                ),
                (instruction, target),
            )

    def test_decodes_adrp_add_reference(self) -> None:
        code_address = 0x1800_0400
        adrp_offset = 0xB8
        add_offset = 0xBC
        register = 2
        target = 0x1803_5F78
        instruction_page = (
            code_address + adrp_offset
        ) & ~0xFFF
        target_page = target & ~0xFFF
        page_delta = (target_page - instruction_page) // 0x1000
        encoded_delta = page_delta & 0x1F_FFFF
        adrp = (
            0x9000_0000
            | (encoded_delta & 0x3) << 29
            | (encoded_delta >> 2) << 5
            | register
        )
        add = (
            0x9100_0000
            | (target & 0xFFF) << 10
            | register << 5
            | register
        )
        code = bytearray(add_offset + 4)
        code[adrp_offset : adrp_offset + 4] = adrp.to_bytes(
            4,
            "little",
        )
        code[add_offset : add_offset + 4] = add.to_bytes(
            4,
            "little",
        )
        self.assertEqual(
            _page_relative_target(
                bytes(code),
                code_address=code_address,
                adrp_offset=adrp_offset,
                add_offset=add_offset,
                register=register,
            ),
            (adrp, add, target),
        )


if __name__ == "__main__":
    unittest.main()
