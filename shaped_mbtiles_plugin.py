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