"""Pure PySide check: does QListWidget.clear() + re-adding Python-created QListWidgetItems (the pattern in
MainWindow._problems) segfault by itself on this PySide6/Python build?"""
import gc, sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QApplication, QListWidget, QListWidgetItem
app = QApplication([])
lw = QListWidget(); lw.show()
for rnd in range(int(sys.argv[1]) if len(sys.argv) > 1 else 3000):
    lw.clear()
    for i in range(rnd % 9):
        it = QListWidgetItem(f"Warning: X{i}.a is not connected.  [UNCONNECTED-PIN]")
        it.setForeground(QBrush(QColor("#a05000"))); it.setData(Qt.UserRole, f"X{i}"); it.setToolTip("t")
        lw.addItem(it)
    if rnd % 50 == 0: gc.collect()
print("no crash")
