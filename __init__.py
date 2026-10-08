def classFactory(iface):
    from .plugin import LayerOrderPlusPlugin
    return LayerOrderPlusPlugin(iface)
