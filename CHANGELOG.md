# Changelog

## v1.0.0 (2026-05-06)

Initial release.

- Convert rigid ducts and duct fittings to flex ducts along the original path
- Support for round and rectangular cross-sections
- Automatic FlexDuctType matching (round/rectangular)
- Size preservation (diameter, width, height)
- Arc tessellation at elbow fittings (16 segments)
- Guide vertices near fittings to prevent early curving
- Intermediate vertices every ~1m along straight sections
- Automatic reconnection to adjacent elements
- Tee/branch fittings act as chain boundaries
