# RigidToFlex — pyRevit Extension

Revit 2024 pyRevit extension that converts selected rigid ducts and duct fittings to flex ducts along the exact drawn path.

## Features

- Converts rigid ducts and fittings (elbows, transitions) to flex ducts
- Supports both round and rectangular duct cross-sections
- Preserves the original route, size, system type, and level
- Accurate path following with intermediate vertices (~1m spacing on straight sections)
- Arc tessellation at elbows for tight bend approximation
- Reconnects to adjacent elements automatically
- Stops at tee/branch fittings (flex duct cannot branch)

## Installation

### Option 1 — Git URL (recommended)

Use the pyRevit CLI:
```
pyrevit extend lib RigidToFlex "https://github.com/signingkd/revit24_toflex.git"
```
Then reload pyRevit or restart Revit.

### Option 2 — Manual

1. Clone or download this repository
2. Rename the cloned folder to `RigidToFlex.extension`
3. Place it in `%APPDATA%\pyRevit-Master\extensions\`
4. Reload pyRevit or restart Revit

## Usage

1. Select rigid ducts and/or duct fittings in Revit
2. Go to the **pyRevit** tab → **Convert** panel → **Rigid To Flex** button
3. The selected elements are replaced with flex ducts following the original path

## Requirements

- Revit 2024
- pyRevit (v4.8+)
- At least one FlexDuctType loaded in the project
