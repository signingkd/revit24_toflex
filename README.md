# RigidToFlex — pyRevit Extension

Revit 2024 pyRevit extension that converts selected rigid ducts and duct fittings to flex ducts along the exact drawn path.

## Features

- Converts rigid round ducts and fittings (elbows, transitions) to a single flex duct
- Preserves the original route, diameter, system type, and level
- Reconnects to adjacent elements automatically
- Stops at tee/branch fittings (flex duct cannot branch)
- Skips rectangular/oval ducts with a warning

## Installation

### Option 1 — Git URL (recommended)

1. Open pyRevit Settings → Custom Extension Directories
2. Or use the pyRevit CLI:
   ```
   pyrevit extend lib RigidToFlex "https://github.com/YOUR_USERNAME/revit24_toflex.git"
   ```
3. Reload pyRevit or restart Revit

### Option 2 — Manual

1. Clone or download this repository
2. Copy the `RigidToFlex.extension` folder to `%APPDATA%\pyRevit-Master\extensions\`
3. Reload pyRevit or restart Revit

## Usage

1. Select rigid ducts and/or duct fittings in Revit
2. Go to the **RigidToFlex** tab → **Convert** panel → **Rigid To Flex** button
3. The selected elements are replaced with flex ducts following the original path

## Requirements

- Revit 2024
- pyRevit (v4.8+)
- At least one FlexDuctType loaded in the project
