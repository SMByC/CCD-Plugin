"""A stand-in for the QGIS interface, for the smoke tests run in a bare QgsApplication.

Inside QGIS (`make qgis-smoke`) the real interface is used instead; see install().
"""

from qgis.core import QgsCoordinateReferenceSystem
from qgis.gui import QgsMapCanvas, QgsMapToolPan, QgsMessageBar
from qgis.PyQt.QtWidgets import QMainWindow


class HostInterface:
    """Just the part of QgisInterface the plugin uses: a main window with one map canvas."""

    def __init__(self):
        self.window = QMainWindow()
        self.canvas = QgsMapCanvas(self.window)
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        self.window.setCentralWidget(self.canvas)
        self.pan_tool = QgsMapToolPan(self.canvas)
        self.canvas.setMapTool(self.pan_tool)
        self.message_bar = QgsMessageBar(self.window)
        self.window.resize(1000, 700)
        self.window.show()

    def mainWindow(self):
        return self.window

    def mapCanvas(self):
        return self.canvas

    def messageBar(self):
        return self.message_bar

    def addDockWidget(self, area, dock):
        self.window.addDockWidget(area, dock)

    def removeDockWidget(self, dock):
        self.window.removeDockWidget(dock)

    def addToolBarIcon(self, action):
        pass

    def removeToolBarIcon(self, action):
        pass

    def addPluginToMenu(self, menu, action):
        pass

    def removePluginMenu(self, menu, action):
        pass


def install():
    """The QGIS interface, a HostInterface when there is none. Call before importing the plugin:
    its modules bind qgis.utils.iface when they are imported."""
    import qgis.utils

    if qgis.utils.iface is None:
        qgis.utils.iface = HostInterface()
    return qgis.utils.iface
