def classFactory(iface):
    from .plugin import BetterLayerOrderPlugin
    return BetterLayerOrderPlugin(iface)