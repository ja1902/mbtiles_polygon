import os

def classFactory(iface):
    from .shaped_mbtiles_plugin import ShapedMBTilesPlugin
    return ShapedMBTilesPlugin(iface)