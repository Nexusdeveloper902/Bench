import sys
from PySide6.QtWidgets import QApplication
from .window import MainWindow


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow(); w.autoplay = True
    w.show()
    if "--smoke" in sys.argv:   # start, build a demo, run, exit (headless check)
        w.load_demo(); w.run(); print(w.status.text()); return 0
    return app.exec()
