import sys
from PySide6.QtWidgets import QApplication
from . import theme
from .window import MainWindow


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    theme.use(theme.saved_dark(app))   # before the window exists: it never shows in the other theme first
    w = MainWindow(); w.autoplay = True
    w.show()
    if "--smoke" in sys.argv:   # start, build a demo, run, exit (headless check)
        w.load_demo(); w.run(); print(w.status.text()); return 0
    return app.exec()
