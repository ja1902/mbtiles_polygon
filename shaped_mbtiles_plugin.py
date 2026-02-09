import os
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.utils import iface
from .shaped_mbtiles import (
    activate_shaped_tool, 
    toggle_pause_resume, 
    set_pause_action,
    cleanup_drawing_state
)

class ShapedMBTilesPlugin:
    """
    QGIS Plugin for Shaped MBTiles generation.
    
    Provides two toolbar buttons:
    1. Draw Shaped MBTiles (Ctrl+Shift+M) - Start new drawing
    2. Pause/Resume Drawing (Ctrl+Shift+P) - Pause/resume current drawing
    """
    
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.draw_action = None
        self.pause_action = None

    def initGui(self):
        """Initialize the plugin GUI with toolbar buttons and menu items."""
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        
        # Draw button - starts new drawing
        self.draw_action = QAction(icon, "Draw Shaped MBTiles", self.iface.mainWindow())
        self.draw_action.setShortcut("Ctrl+Shift+M")
        self.draw_action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.draw_action)
        self.iface.addPluginToMenu("&Shaped MBTiles", self.draw_action)
        
        # Pause/Resume button - toggle pause state
        self.pause_action = QAction("Pause Drawing", self.iface.mainWindow())
        self.pause_action.setShortcut("Ctrl+Shift+P")
        self.pause_action.triggered.connect(toggle_pause_resume)
        self.iface.addToolBarIcon(self.pause_action)
        self.iface.addPluginToMenu("&Shaped MBTiles", self.pause_action)
        
        # Register pause action with shaped_mbtiles module for text updates
        set_pause_action(self.pause_action)
        
        # Print usage instructions
        print("Shaped MBTiles Plugin loaded!")
        print("- Click 'Draw Shaped MBTiles' (Ctrl+Shift+M) to start a new drawing")
        print("- Click 'Pause Drawing' (Ctrl+Shift+P) to pause/resume")
        print("- Left-click to add points")
        print("- Delete/Backspace key OR Shift+Right-click OR middle-click to undo last point")
        print("- Right-click to finish drawing")
        print("- ESC key to cancel and exit drawing mode")

    def unload(self):
        """Remove the plugin menu items and toolbar buttons."""
        # Remove menu items
        self.iface.removePluginMenu("&Shaped MBTiles", self.draw_action)
        self.iface.removePluginMenu("&Shaped MBTiles", self.pause_action)
        
        # Remove toolbar buttons
        self.iface.removeToolBarIcon(self.draw_action)
        self.iface.removeToolBarIcon(self.pause_action)
        
        # Clean up any active drawing state
        cleanup_drawing_state()
        
        # Delete actions
        del self.draw_action
        del self.pause_action

    def run(self):
        """Start a new drawing session."""
        activate_shaped_tool()
