"""Design tokens (docs/milestone/03-design.md §2) and the one application stylesheet. Flat chrome, light or dark: no
shadows, gradients or blur. The app uses the Fusion style with an explicit palette so the look does not depend on the OS (D14);
only the first light / dark choice follows it (saved_dark)."""
from string import Template

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QFont, QPalette

# ---- colours: one dict per theme. use() copies the current one into the module globals (CANVAS_BG, TEXT, ...), so
# read them as `theme.X` when painting; never bind one at import time (`from .theme import X`, a default argument).
LIGHT = dict(
    # canvas identity (kept from the original app)
    CANVAS_BG="#FFFFFF", GRID="#E6E9EE", INK="#222222", WIRE="#1A5FB4", SEL="#E07000", SEL_FILL="#FFE6BE",
    HOVER="#2A7FFF", OK_PIN="#2FA82F", OK_PIN_RING="#1B6E1B", OPEN_PIN="#D02020", FINDING="#E0A000", BAD="#B00020",
    LED_OFF="#5A5A5A", LED_ON="#FF4620", LED_GLOW="#FF8C3C",
    # chrome
    APP_BG="#F4F6F8", PANEL="#FFFFFF", PANEL_ALT="#F8F9FB", BORDER="#D9DEE5", BORDER_STRONG="#B8C0CC", DIVIDER="#E8EBEF",
    TEXT="#1F2933", TEXT_2="#52606D", TEXT_MUTED="#8A94A0", TEXT_DISABLED="#A8B0BA", DISABLED_BG="#EEF1F4",
    PRIMARY="#1A5FB4", PRIMARY_HOVER="#174F96", PRIMARY_SOFT="#E7F0FB", ON_PRIMARY="#FFFFFF", CODE_BG="#1F2933",
    # run state (blocked shares the failed colour, as in the design)
    STATE_IDLE="#666666", STATE_RUNNING="#1A5FB4", STATE_COMPLETED="#1B6E1B", STATE_FAILED="#B00020",
    STATE_STALE="#A05000", OK_BG="#E4F4E4",
    # feedback
    INFO_FG="#0B5CAD", INFO_BG="#E3EEFA", WARN_FG="#8A4B00", WARN_BG="#FFF1DC", WARN_BORDER="#E0A000",
    ERR_FG="#B00020", ERR_BG="#FBE4E8", ERR_BORDER="#D02020", INVALID_BG="#FFF0F0",
    # readiness key -> (foreground, background)
    READY={"simulated": ("#1B6E1B", "#E4F4E4"), "interface": ("#0B5CAD", "#E3EEFA"),
           "placeable": ("#52606D", "#ECEFF3"), "placeholder": ("#6B3FA0", "#EFE8F8")})
# Dark: same roles, lighter accents so text stays >= 4.5:1 on PANEL/APP_BG; ON_PRIMARY turns dark because PRIMARY,
# ERR_FG and SEL (pill and tooltip backgrounds) are light here. Checked by app/tests/test_theme.py.
DARK = dict(
    CANVAS_BG="#181C22", GRID="#262C34", INK="#D7DCE2", WIRE="#5B9BF0", SEL="#FF9F43", SEL_FILL="#4A3216",
    HOVER="#4EA1FF", OK_PIN="#3FB950", OK_PIN_RING="#56D364", OPEN_PIN="#FF6B6B", FINDING="#E3B341", BAD="#FF7B72",
    LED_OFF="#8A8F96", LED_ON="#FF4620", LED_GLOW="#FF8C3C",
    APP_BG="#15191E", PANEL="#1C2128", PANEL_ALT="#22272E", BORDER="#373E47", BORDER_STRONG="#545D68", DIVIDER="#2D333B",
    TEXT="#E6EAF0", TEXT_2="#ADB5BF", TEXT_MUTED="#8B949E", TEXT_DISABLED="#5F6873", DISABLED_BG="#262C33",
    PRIMARY="#6CA6F5", PRIMARY_HOVER="#8AB8F7", PRIMARY_SOFT="#1F3550", ON_PRIMARY="#0D1117", CODE_BG="#0D1117",
    STATE_IDLE="#9AA4AF", STATE_RUNNING="#6CA6F5", STATE_COMPLETED="#56D364", STATE_FAILED="#FF7B72",
    STATE_STALE="#E3A15A", OK_BG="#12261A",
    INFO_FG="#79B8FF", INFO_BG="#132339", WARN_FG="#E3B341", WARN_BG="#2E2312", WARN_BORDER="#9E6A03",
    ERR_FG="#FF7B72", ERR_BG="#3A1719", ERR_BORDER="#F85149", INVALID_BG="#2D1517",
    READY={"simulated": ("#56D364", "#12261A"), "interface": ("#79B8FF", "#132339"),
           "placeable": ("#ADB5BF", "#2D333B"), "placeholder": ("#C297FF", "#2A1F3D")})
# state key -> (badge text, colour token, hollow dot); readiness key -> (label, tooltip). use() resolves the colours
# into STATES (text, colour, hollow) and READINESS (label, foreground, background, tooltip).
_STATES = {"idle": ("Idle", "STATE_IDLE", False), "running": ("Running", "STATE_RUNNING", True),
           "completed": ("Completed", "STATE_COMPLETED", False), "failed": ("Failed", "STATE_FAILED", False),
           "blocked": ("Blocked", "STATE_FAILED", False), "stale": ("Out of date", "STATE_STALE", False),
           "live": ("Live", "STATE_RUNNING", True), "paused": ("Paused", "STATE_STALE", True),
           "cancelled": ("Stopped", "STATE_IDLE", False)}
_READINESS = {"simulated": ("Simulated", "Has a working simulation model"), "interface": ("Interface only", "Not simulated"),
              "placeable": ("Reference only", "Documentation only: no pins, not simulated"),
              "placeholder": ("Placeholder", "No usable definition: kept unchanged")}   # keys: Definition.readiness + placeholder
DARK_MODE = False

# ---- type scale (px; Noto Sans). Weights are Qt values.
FONT = "Noto Sans"
MONO = "Noto Sans Mono"
BODY_PX, SMALL_PX, CAPTION_PX, TITLE_PX = 13, 12, 11, 16
# ---- spacing and radii
XS, SM, MD, LG, XL = 4, 8, 12, 16, 24
R_SM, R_MD, R_LG, R_XL, R_PILL = 4, 6, 8, 12, 999
LIBRARY_W, INSPECTOR_W, CARD_H, CONTROL_H = 280, 320, 84, 32
TOPBAR_H, STATUS_H, DOCK_H, PROBLEMS_W = 48, 24, 248, 250

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
QFrame#topbar { background: $PANEL; border-bottom: 1px solid $BORDER; }
QLabel#logo { background: $PRIMARY; border-radius: 6px; }
QToolButton#appbtn { border: 0; border-radius: ${R_MD}px; padding: 2px 6px 2px 0; font-size: ${TITLE_PX}px; font-weight: 600; }
QToolButton#appbtn:hover, QToolButton#appbtn:pressed { background: $APP_BG; }
QToolButton#appbtn::menu-indicator { image: none; width: 0; }
QToolButton#iconbtn { border: 0; border-radius: ${R_MD}px; padding: 0; }
QToolButton#iconbtn:hover { background: $PRIMARY_SOFT; }
QToolButton#iconbtn:disabled { background: transparent; }
QFrame#vline { background: $DIVIDER; max-width: 1px; min-width: 1px; border: 0; }
QLabel#filename { font-weight: 500; }
QLabel#chipbox { background: $PANEL_ALT; border: 1px solid $BORDER; border-radius: ${R_SM}px; padding: 0 10px; color: $TEXT_2; font-size: ${SMALL_PX}px; }
QLabel#fieldlabel { color: $TEXT_2; }
QLabel#fieldlabel:disabled { color: $TEXT_DISABLED; }
QLineEdit#topfield { padding: 0 8px; min-height: 26px; max-height: 26px; min-width: 66px; max-width: 66px; }
QLineEdit#topfield[invalid="true"] { border: 2px solid $ERR_BORDER; background: $INVALID_BG; padding: 0 7px; min-height: 24px; max-height: 24px; min-width: 66px; max-width: 66px; }
QPushButton#simbtn { padding: 0 8px; min-width: 78px; max-width: 78px; min-height: 30px; max-height: 30px; }
QPushButton#simbtn[compact="true"] { min-width: 54px; max-width: 54px; }
QLineEdit#topfield[compact="true"] { min-width: 50px; max-width: 50px; }
QPushButton[primary="true"]:disabled { background: $DISABLED_BG; color: $TEXT_DISABLED; border-color: $DISABLED_BG; }
QLabel#notice { border-radius: ${R_SM}px; padding: 3px 10px; font-size: ${CAPTION_PX}px; border: 1px solid $ERR_BORDER; background: $ERR_BG; color: $ERR_FG; }
QLabel#notice[kind="warn"] { border-color: $WARN_BORDER; background: $WARN_BG; color: $WARN_FG; }
QLabel#tip { background: $TEXT; color: $ON_PRIMARY; border-radius: ${R_SM}px; padding: 4px 8px; font-size: ${SMALL_PX}px; }
QFrame#floating { background: $PANEL; border: 1px solid $BORDER; border-radius: ${R_LG}px; }
QFrame#floating QToolButton { border: 0; border-radius: ${R_MD}px; padding: 0; background: transparent; }
QFrame#floating QToolButton:hover { background: $PRIMARY_SOFT; }
QFrame#floating QLabel#zpct { font-size: ${SMALL_PX}px; font-weight: 600; background: transparent; }
QFrame#floating QFrame#vline { background: $DIVIDER; }
QFrame#hintcard { background: $PANEL; border: 1px solid $BORDER; border-radius: ${R_XL}px; }
QFrame#hintcard QLabel { background: transparent; }
QLabel#stepnum { border: 1.5px solid $PRIMARY; border-radius: 11px; color: $PRIMARY; font-size: ${CAPTION_PX}px; font-weight: 600; }
QLabel#steptitle { font-weight: 600; }
QLabel#kbd { font-size: ${CAPTION_PX}px; color: $TEXT_MUTED; }
QWidget#results, QWidget#problems { background: $PANEL; }
QLabel#panelhead { font-weight: 600; }
QLabel#emptytitle { font-weight: 600; }
QLabel#timelbl, QLabel#readout { font-family: "$MONO"; font-size: ${SMALL_PX}px; }
QLabel#stats { font-size: ${CAPTION_PX}px; color: $TEXT_2; }
QLabel#finding { color: $WARN_FG; font-size: ${SMALL_PX}px; }
QLabel[bad="true"] { color: $BAD; }
QPlainTextEdit#devtext { background: $CODE_BG; color: #E4E7EB; border: 0; border-radius: ${R_MD}px; padding: 4px; }
QProgressBar { background: $DIVIDER; border: 0; border-radius: 2px; max-height: 4px; min-height: 4px; }
QProgressBar::chunk { background: $PRIMARY; border-radius: 2px; }
QListWidget#problemlist { border: 0; background: transparent; outline: 0; }
QLabel#countpill { background: $ERR_FG; color: $ON_PRIMARY; border-radius: 9px; padding: 0 6px; font-size: ${CAPTION_PX}px; font-weight: 600; }
QFrame#banner[kind="ok"] { border-color: $STATE_COMPLETED; background: $OK_BG; }
QFrame#banner[kind="ok"] QLabel { color: $STATE_COMPLETED; }
QSlider::groove:horizontal { height: 4px; background: $DIVIDER; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $PRIMARY; border-radius: 2px; }
QSlider::handle:horizontal { background: $PANEL; border: 2px solid $PRIMARY; width: 10px; height: 10px; margin: -6px 0; border-radius: 7px; }
QSlider::sub-page:horizontal:disabled { background: $BORDER; }
QSlider::handle:horizontal:disabled { border-color: $BORDER_STRONG; }
QFrame#vsep { background: $BORDER; max-width: 1px; min-width: 1px; border: 0; }
""")


def tokens():
    return {k: v for k, v in globals().items() if k.isupper() and isinstance(v, (str, int))}


def color(c):
    """A colour token name ('TEXT_2') -> its current hex; a hex colour is returned unchanged."""
    return globals().get(c, c)


def use(dark):
    """Make the dark (True) or light theme current: rebinds the colour globals, STATES, READINESS and QSS.
    apply() then pushes it to the QApplication; widgets.retheme() redraws what painted with the old colours."""
    global STATES, READINESS, QSS, DARK_MODE
    c = DARK if dark else LIGHT
    globals().update(c)
    STATES = {k: (t, c[col], hollow) for k, (t, col, hollow) in _STATES.items()}
    READINESS = {k: (t, *c["READY"][k], tip) for k, (t, tip) in _READINESS.items()}
    QSS = _QSS.substitute(tokens())
    DARK_MODE = dark


def saved_dark(app):
    """The saved View > Dark mode choice, else the OS colour scheme."""
    s = QSettings("Bench", "Bench")
    return s.value("dark", type=bool) if s.contains("dark") else app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def save_dark(dark):
    QSettings("Bench", "Bench").setValue("dark", dark)


use(False)


def palette():
    p = QPalette(QColor(PANEL), QColor(APP_BG))   # derives the frame shades (light / mid / dark) from these two
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
    """Fusion style + the current theme's palette + the app stylesheet. A no-op while the theme is unchanged."""
    mode = "dark" if DARK_MODE else "light"
    if app.property("sim-theme") == mode: return
    app.setStyle("Fusion")
    app.setPalette(palette())
    f = QFont(FONT); f.setPixelSize(BODY_PX); app.setFont(f)
    app.setStyleSheet(QSS)
    app.setProperty("sim-theme", mode)


def repolish(w):
    """Re-evaluate the stylesheet of `w` after a dynamic property changed."""
    w.style().unpolish(w); w.style().polish(w); w.update()
