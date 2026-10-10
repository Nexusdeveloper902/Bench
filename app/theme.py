"""Design tokens (docs/milestone/03-design.md §2) and the one application stylesheet. Flat light chrome: no shadows,
gradients or blur. The app uses the Fusion style with an explicit palette so the look does not depend on the OS (D14)."""
from string import Template

from PySide6.QtGui import QColor, QFont, QPalette

# ---- canvas identity (kept from the original app)
CANVAS_BG, GRID, INK, WIRE, SEL, SEL_FILL = "#FFFFFF", "#E6E9EE", "#222222", "#1A5FB4", "#E07000", "#FFE6BE"
HOVER, OK_PIN, OK_PIN_RING, OPEN_PIN, FINDING, BAD = "#2A7FFF", "#2FA82F", "#1B6E1B", "#D02020", "#E0A000", "#B00020"
LED_OFF, LED_ON, LED_GLOW = "#5A5A5A", "#FF4620", "#FF8C3C"
# ---- chrome
APP_BG, PANEL, PANEL_ALT, BORDER, BORDER_STRONG, DIVIDER = "#F4F6F8", "#FFFFFF", "#F8F9FB", "#D9DEE5", "#B8C0CC", "#E8EBEF"
TEXT, TEXT_2, TEXT_MUTED, TEXT_DISABLED, DISABLED_BG = "#1F2933", "#52606D", "#8A94A0", "#A8B0BA", "#EEF1F4"
PRIMARY, PRIMARY_HOVER, PRIMARY_SOFT, ON_PRIMARY = "#1A5FB4", "#174F96", "#E7F0FB", "#FFFFFF"
# ---- run state
STATE_IDLE, STATE_RUNNING, STATE_COMPLETED, STATE_FAILED, STATE_STALE = "#666666", "#1A5FB4", "#1B6E1B", "#B00020", "#A05000"
# ---- feedback
INFO_FG, INFO_BG = "#0B5CAD", "#E3EEFA"
WARN_FG, WARN_BG, WARN_BORDER = "#8A4B00", "#FFF1DC", "#E0A000"
ERR_FG, ERR_BG, ERR_BORDER, INVALID_BG = "#B00020", "#FBE4E8", "#D02020", "#FFF0F0"

# readiness key -> (label, foreground, background, tooltip). Keys: Definition.readiness, plus "placeholder".
READINESS = {"simulated": ("Simulated", "#1B6E1B", "#E4F4E4", "Has a working simulation model"),
             "interface": ("Interface only", "#0B5CAD", "#E3EEFA", "Not simulated"),
             "placeable": ("Reference only", "#52606D", "#ECEFF3", "Documentation only: no pins, not simulated"),
             "placeholder": ("Placeholder", "#6B3FA0", "#EFE8F8", "No usable definition: kept unchanged")}

# ---- type scale (px; Noto Sans). Weights are Qt values.
FONT = "Noto Sans"
MONO = "Noto Sans Mono"
BODY_PX, SMALL_PX, CAPTION_PX, TITLE_PX = 13, 12, 11, 16
# ---- spacing and radii
XS, SM, MD, LG, XL = 4, 8, 12, 16, 24
R_SM, R_MD, R_LG, R_XL, R_PILL = 4, 6, 8, 12, 999
LIBRARY_W, INSPECTOR_W, CARD_H, CONTROL_H = 280, 320, 84, 32

_QSS = Template("""
* { font-family: "$FONT"; font-size: ${BODY_PX}px; color: $TEXT; }
QMainWindow, QDialog { background: $APP_BG; }
QToolTip { background: $TEXT; color: $ON_PRIMARY; border: 0; padding: 4px 6px; font-size: ${SMALL_PX}px; }
QStatusBar { background: $PANEL; border-top: 1px solid $BORDER; }
QStatusBar QLabel { font-size: ${CAPTION_PX}px; color: $TEXT_2; }
QToolBar { background: $PANEL; border: 0; border-bottom: 1px solid $BORDER; spacing: 6px; padding: 4px 8px; }
QToolBar QLabel { color: $TEXT_2; }
QToolButton { border: 1px solid transparent; border-radius: ${R_MD}px; padding: 4px 10px; font-weight: 500; }
QToolButton:hover { background: $PRIMARY_SOFT; }
QToolButton:disabled { color: $TEXT_DISABLED; }
QMenuBar { background: $PANEL; }
QMenuBar::item:selected, QMenu::item:selected { background: $PRIMARY_SOFT; color: $TEXT; }
QMenu { background: $PANEL; border: 1px solid $BORDER; }
QSplitter::handle { background: $BORDER; }
QSplitter::handle:horizontal { width: 1px; }
QSplitter::handle:vertical { height: 1px; }
QScrollArea { background: transparent; border: 0; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: $BORDER_STRONG; border-radius: 4px; min-height: 24px; margin: 1px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: $BORDER_STRONG; border-radius: 4px; min-width: 24px; margin: 1px; }

QLineEdit { background: $PANEL; border: 1px solid $BORDER_STRONG; border-radius: ${R_SM}px; padding: 4px 8px;
            min-height: 20px; selection-background-color: $PRIMARY; selection-color: $ON_PRIMARY; }
QLineEdit:focus { border: 1px solid $PRIMARY; }
QLineEdit:disabled { background: $DISABLED_BG; color: $TEXT_DISABLED; }
QComboBox { background: $PANEL; border: 1px solid $BORDER_STRONG; border-radius: ${R_SM}px; padding: 4px 8px; min-height: 22px; }
QComboBox:focus { border: 1px solid $PRIMARY; }
QComboBox:disabled { background: $DISABLED_BG; color: $TEXT_DISABLED; }
QComboBox QAbstractItemView { background: $PANEL; border: 1px solid $BORDER; selection-background-color: $PRIMARY_SOFT;
                              selection-color: $TEXT; outline: 0; }
QPushButton { background: $PANEL; border: 1px solid $BORDER_STRONG; border-radius: ${R_MD}px; padding: 5px 14px;
              font-weight: 500; min-height: 20px; }
QPushButton:hover { background: $PRIMARY_SOFT; border-color: $PRIMARY; }
QPushButton:disabled { background: $DISABLED_BG; color: $TEXT_DISABLED; border-color: $DIVIDER; }
QPushButton[primary="true"] { background: $PRIMARY; color: $ON_PRIMARY; border: 1px solid $PRIMARY; }
QPushButton[primary="true"]:hover { background: $PRIMARY_HOVER; }
QPushButton[link="true"] { background: transparent; border: 0; color: $PRIMARY; padding: 0; font-weight: 400; text-align: left; }
QPushButton[link="true"]:hover { text-decoration: underline; background: transparent; }
QPlainTextEdit { background: $PANEL; border: 1px solid $BORDER; border-radius: ${R_SM}px; font-family: "$MONO"; font-size: ${SMALL_PX}px; }

QWidget#panel { background: $PANEL; }
QLabel#title { font-size: ${TITLE_PX}px; font-weight: 600; }
QLabel#overline { font-size: ${CAPTION_PX}px; font-weight: 600; color: $TEXT_2; letter-spacing: 0.5px; }
QLabel#muted { font-size: ${SMALL_PX}px; color: $TEXT_2; }
QLabel#hint { font-size: ${CAPTION_PX}px; color: $TEXT_MUTED; }
QLabel#code { font-family: "$MONO"; font-size: ${CAPTION_PX}px; color: $TEXT_2; }
QLabel#bold { font-weight: 600; }
QLabel#err { font-size: ${CAPTION_PX}px; color: $ERR_FG; }

QLineEdit#search { border: 1px solid $BORDER_STRONG; border-radius: ${R_MD}px; padding: 4px 8px 4px 2px; min-height: 22px; }
QLineEdit#search:focus { border: 1px solid $PRIMARY; }

QFrame#tile { background: $APP_BG; border: 1px solid $BORDER; border-radius: ${R_LG}px; }
QFrame#tile[fallback="true"] { border: 1px dashed $BORDER_STRONG; background: $PANEL_ALT; }
QLabel#glyph { font-size: ${TITLE_PX}px; font-weight: 600; color: $TEXT_2; }

QFrame#card { background: $PANEL; border: 1px solid $BORDER; border-radius: ${R_LG}px; }
QFrame#card:hover, QFrame#card:focus { border: 1px solid $HOVER; }
QFrame#card[selected="true"] { background: $PRIMARY_SOFT; border: 2px solid $PRIMARY; }
QFrame#card[armed="true"] { background: $SEL_FILL; border: 2px solid $SEL; }
QFrame#card QLabel { background: transparent; }
QLabel#cardname { font-weight: 600; }
QLabel#pill { background: $SEL; color: $ON_PRIMARY; border-radius: 9px; padding: 1px 8px; font-size: ${CAPTION_PX}px; font-weight: 600; }
QLabel#chip { background: $APP_BG; border: 1px solid $BORDER; border-radius: 10px; padding: 0 10px; font-size: ${CAPTION_PX}px;
              color: $TEXT_2; min-height: 18px; }
QLabel#count { background: $ERR_FG; color: $ON_PRIMARY; border-radius: 9px; padding: 0 6px; font-size: ${CAPTION_PX}px; font-weight: 600; }

QPushButton#nav { background: transparent; border: 0; border-left: 3px solid transparent; border-radius: ${R_MD}px;
                  text-align: left; padding: 0; min-height: 28px; max-height: 28px; font-weight: 400; }
QPushButton#nav:hover { background: $APP_BG; }
QPushButton#nav[selected="true"] { background: $PRIMARY_SOFT; border-left: 3px solid $PRIMARY; }
QPushButton#nav QLabel { background: transparent; }

QFrame#fieldbox { background: $PANEL; border: 1px solid $BORDER_STRONG; border-radius: ${R_SM}px; }
QFrame#fieldbox[focus="true"] { border: 1px solid $PRIMARY; }
QFrame#fieldbox[invalid="true"] { border: 2px solid $ERR_BORDER; background: $INVALID_BG; }
QFrame#fieldbox[readonly="true"] { background: $DISABLED_BG; }
QFrame#fieldbox QLineEdit { border: 0; background: transparent; padding: 4px 8px; }
QFrame#fieldbox QLabel#unit { background: $PANEL_ALT; border-left: 1px solid $DIVIDER; color: $TEXT_2; padding: 0 10px; }
QFrame#fieldbox[invalid="true"] QLabel#unit { background: $INVALID_BG; }
QFrame#fieldbox[readonly="true"] QLabel { color: $TEXT_DISABLED; background: transparent; }
QComboBox[invalid="true"] { border: 2px solid $ERR_BORDER; background: $INVALID_BG; }

QFrame#banner { border-radius: ${R_LG}px; border: 1px solid $INFO_FG; background: $INFO_BG; }
QFrame#banner[kind="warn"] { border-color: $WARN_BORDER; background: $WARN_BG; }
QFrame#banner[kind="err"] { border-color: $ERR_BORDER; background: $ERR_BG; }
QFrame#banner QLabel, QFrame#banner QPushButton { background: transparent; border: 0; }
QFrame#banner[kind="warn"] QLabel, QFrame#banner[kind="warn"] QPushButton { color: $WARN_FG; }
QFrame#banner[kind="err"] QLabel, QFrame#banner[kind="err"] QPushButton { color: $ERR_FG; }
QFrame#banner QLabel#code { color: $ERR_FG; font-weight: 600; }
QFrame#banner QPushButton { text-align: left; font-weight: 600; padding: 0; }
QFrame#banner QPushButton[link="true"] { text-decoration: underline; font-weight: 400; }
QFrame#rule { background: $DIVIDER; max-height: 1px; min-height: 1px; border: 0; }
QFrame#rowline { background: $DIVIDER; max-height: 1px; min-height: 1px; border: 0; }
QFrame#tablehead { background: $PANEL_ALT; }
QLabel#th { font-size: ${CAPTION_PX}px; font-weight: 600; color: $TEXT_2; }
QLabel#emptyicon { background: $APP_BG; border: 1px solid $BORDER; border-radius: 28px; color: $TEXT_2; font-size: 22px; }
""")


def tokens():
    return {k: v for k, v in globals().items() if k.isupper() and isinstance(v, (str, int))}


QSS = _QSS.substitute(tokens())


def palette():
    p = QPalette()
    for role, c in ((QPalette.Window, APP_BG), (QPalette.WindowText, TEXT), (QPalette.Base, PANEL),
                    (QPalette.AlternateBase, PANEL_ALT), (QPalette.Text, TEXT), (QPalette.Button, PANEL),
                    (QPalette.ButtonText, TEXT), (QPalette.ToolTipBase, TEXT), (QPalette.ToolTipText, ON_PRIMARY),
                    (QPalette.Highlight, PRIMARY), (QPalette.HighlightedText, ON_PRIMARY), (QPalette.Link, PRIMARY),
                    (QPalette.PlaceholderText, TEXT_MUTED), (QPalette.BrightText, ON_PRIMARY)):
        p.setColor(role, QColor(c))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, QColor(TEXT_DISABLED))
    return p


def apply(app):
    """Fusion style + explicit palette + the app stylesheet. Idempotent."""
    if app.property("sim-theme"): return
    app.setStyle("Fusion")
    app.setPalette(palette())
    f = QFont(FONT); f.setPixelSize(BODY_PX); app.setFont(f)
    app.setStyleSheet(QSS)
    app.setProperty("sim-theme", True)


def repolish(w):
    """Re-evaluate the stylesheet of `w` after a dynamic property changed."""
    w.style().unpolish(w); w.style().polish(w); w.update()
