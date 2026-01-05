# Shaped MBTiles Generator for QGIS

Generate MBTiles from a polygon you draw on the map. Only creates tiles that actually intersect your shape. Uses clipping approach for efficient rendering.

## Features

- Draw any polygon shape, generate tiles for just that area
- Pre-filters tiles before rendering - only intersecting tiles are created
- Uses clipping to render only pixels inside the polygon (more efficient)
- Meta-tiling prevents labels from being cut off at edges
- Three mask options: transparent, white, or black outside polygon
- Shows tile count estimate before generating

You can find more info on how it works in the shaped_mbtiles.md file.

## Installation

### Option 1: Install as Plugin

1. **Find your QGIS plugins folder:**
   - Windows: `C:\Users\<YourUsername>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\`
   - macOS: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
   - Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`

   If you can't find it, open QGIS, go to **Plugins** → **Python Console** and run:
   ```python
   from qgis.utils import plugin_dirs
   print(plugin_dirs[0])
   ```

2. **Create a new folder** called `shaped_mbtiles` inside the plugins folder.

3. **Create these 3 files** inside the `shaped_mbtiles` folder:

**metadata.txt:**
```ini
[general]
name=Shaped MBTiles Generator
qgisMinimumVersion=3.0
description=Generate MBTiles from polygon shapes
version=1.0
```

**__init__.py:**
```python
import os

def classFactory(iface):
    from .shaped_mbtiles_plugin import ShapedMBTilesPlugin
    return ShapedMBTilesPlugin(iface)
```

**shaped_mbtiles_plugin.py:**
```python
import os
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.utils import iface
from .shaped_mbtiles import activate_shaped_tool

class ShapedMBTilesPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        
        self.action = QAction(icon, "Draw Shaped MBTiles", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&Shaped MBTiles", self.action)

    def unload(self):
        self.iface.removePluginMenu("&Shaped MBTiles", self.action)
        self.iface.removeToolBarIcon(self.action)

    def run(self):
        activate_shaped_tool()
```

4. **Copy the main script**: Copy `mbtiles/shaped_mbtiles.py` from this repository into your `shaped_mbtiles` folder.

5. **Edit the script**: Open `shaped_mbtiles.py` in a text editor. Scroll to the very bottom of the file (around line 489-511). You'll see code that looks like this:

```python
# ======================================================
# 6. LAUNCH
# ======================================================
def activate_shaped_tool():
    tool = ShapedMBTilesTool(iface.mapCanvas())
    iface.mapCanvas().setMapTool(tool)

action_name = "Draw Shaped MBTiles (Clipping)"
main_window = iface.mainWindow()
... (more code that adds toolbar button)
```

Delete everything from line 496 onwards (all the code that creates the toolbar button). Keep only the `activate_shaped_tool()` function. The bottom of your file should end with just:

```python
# ======================================================
# 6. LAUNCH
# ======================================================
def activate_shaped_tool():
    tool = ShapedMBTilesTool(iface.mapCanvas())
    iface.mapCanvas().setMapTool(tool)
```

Why? The plugin wrapper (`shaped_mbtiles_plugin.py`) already handles adding the toolbar button, so you don't need that code in the main script.

6. **Enable the plugin**: Restart QGIS, then go to **Plugins** → **Manage and Install Plugins** → **Installed** tab. Find "Shaped MBTiles Generator" and check the box to enable it.

### Option 2: Direct Script Loading (No Plugin Setup)

If you don't want to set up a plugin, just load the script directly:

1. Open QGIS
2. Go to **Plugins** → **Python Console**
3. Show editor
4. Paste the contents of the python file and click run

A toolbar button will appear. Click it to start drawing polygons.

## Usage

1. Load your map layers in QGIS
2. Click the toolbar button or go to Plugins → Shaped MBTiles
3. Left-click to draw polygon vertices, right-click to finish
4. Set zoom levels, mask style, and output file
5. Check the tile count estimate
6. Click OK and wait for it to finish

## Tips

- Layers should be in Web Mercator (EPSG:3857) for best results
- Start with a small zoom range to test (e.g., 10-12)
- Transparent mask = PNG, solid colors = JPEG

## Troubleshooting

**Plugin doesn't show up:** Check files are in the right folder, restart QGIS, check Python console for errors.

**Blank tiles:** Make sure layers are visible and styled, check polygon is in the right place.

**Slow generation:** Normal for large zoom ranges. Generate in smaller chunks if needed.


## Requirements

- QGIS 3.0+
- Python 3.x (included with QGIS)
- PyQt5 (included with QGIS)

