#!/usr/bin/env python3
from pathlib import Path

swift_path = Path("/tmp/walle/lg-landmark-rig/Sources/GlassCapture/main.swift")
lines = swift_path.read_text().splitlines()

new_lines = []
in_static_bg = False

for i, line in enumerate(lines):
    if "func staticBackgrounds()" in line:
        in_static_bg = True
        new_lines.append("func staticBackgrounds() -> [Background] {")
        new_lines.append("    var list: [Background] = []")
        new_lines.append('    for landmarkId in ["1001", "1002", "1003", "1004", "1005", "1006", "1007", "1008", "1009", "1010", "1011", "1012", "1014", "1015", "1016", "1017", "1018", "1019", "1020", "1021", "1022"] {')
        new_lines.append('        list.append(Background(name: "landmark-\\(landmarkId)", family: .qualitative) { x, y, w, h in')
        new_lines.append('            return (140, 97, 73)')
        new_lines.append('        })')
        new_lines.append('    }')
        new_lines.append('    return list')
        new_lines.append('}')
        continue
    
    if in_static_bg:
        if "func dynamicBackground()" in line:
            in_static_bg = False
            new_lines.append(line)
        continue
    
    new_lines.append(line)

swift_path.write_text("\n".join(new_lines), encoding="utf-8")
print(f"Cleaned {swift_path.name}: total lines now {len(new_lines)}")
