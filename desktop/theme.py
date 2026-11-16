"""Soundbox desktop theme — near-black with Freq purple accents.

Freq brand palette:
  primary  #7B2FBE   accent/light #A855F7   dark #5B21B6
"""

PURPLE = "#7B2FBE"
PURPLE_LIGHT = "#A855F7"
PURPLE_DARK = "#5B21B6"

BG = "#0d0e12"
PANEL = "#16171d"
PANEL2 = "#1b1d24"
ROW = "#111319"
ROW_ALT = "#14161d"
BORDER = "#2a2d37"
CONTROL_BORDER = "#454a59"
TEXT = "#c7ccd6"
TEXT_DIM = "#7c828f"
TEXT_HI = "#f2f4f8"

DEFAULT_ACCENT = PURPLE
DEFAULT_ACCENT_TEXT = '#ffffff'
GRID_RED = '#ed3343'

QSS = f"""
QWidget {{
    font-size: 12px;
    color: {TEXT};
    outline: none;
}}
QMainWindow, QWidget {{ background: {BG}; }}
/* Labels must be transparent or the BG above paints an ugly box behind text. */
QLabel {{ background: transparent; }}
QListWidget#UsbChoices {{ background: {PANEL}; border: 1px solid {CONTROL_BORDER}; }}
QWidget#TrackDetails {{ border: 1px solid {BORDER}; border-radius: 4px; }}
QListWidget#UsbChoices::item {{ padding: 4px 8px; }}
QListWidget#UsbChoices::item:selected {{ background: {PURPLE}; color: @ON_ACCENT@; }}
QPlainTextEdit#TrackComments {{ background: {BG}; border: 1px solid {CONTROL_BORDER};
    border-radius: 3px; padding: 5px; }}

/* top + bottom bars */
#TopBar, #BottomBar {{ background: {PANEL2}; border: none; }}
#TopBar {{ border-bottom: 1px solid {BORDER}; }}
#InfoPlaceholder, #InfoPlaceholder:disabled {{ background: {BG};
    border: 1px solid {BORDER}; border-radius: 3px; padding: 0; }}
#BrowserScrollCap {{ background: {PANEL2}; border: none; }}
#NowPlayingFrame {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 5px;
}}
#NowPlaying {{
    color: {TEXT_HI}; background: transparent; border: none;
    padding: 0 12px; font-size: 15px; font-weight: 600;
}}
#AutoplayStack {{ background: transparent; border: none; }}
QPushButton#AutoplayUp, QPushButton#AutoplayRandom, QPushButton#AutoplayDown {{
    background: transparent; border: none; border-right: 1px solid {BORDER};
    color: {TEXT_HI}; padding: 0; margin: 0; font-size: 8px; font-weight: 800;
}}
QPushButton#AutoplayUp {{
    border-bottom: 1px solid {BORDER}; border-top-left-radius: 4px;
}}
QPushButton#AutoplayRandom {{
    border-bottom: 1px solid {BORDER};
}}
QPushButton#AutoplayDown {{ border-bottom-left-radius: 4px; }}
QPushButton#AutoplayUp:hover, QPushButton#AutoplayRandom:hover,
QPushButton#AutoplayDown:hover {{ background: {PANEL}; }}
QPushButton#AutoplayUp[active="true"] {{
    background: #168fc0; color: white;
}}
QPushButton#AutoplayRandom[active="true"] {{
    background: #c83445; color: white;
}}
QPushButton#AutoplayDown[active="true"] {{
    background: #279957; color: white;
}}
#Subtitle, #Hint {{ color: {TEXT_DIM}; }}
#Sidebar {{ background: {PANEL}; }}
#VDivider {{ background: {BORDER}; }}
#SectionHeader {{ background: {PANEL2}; border-bottom: 1px solid {BORDER}; }}
#SectionHeaderTop {{ background: {PANEL2}; border-top: 1px solid {BORDER};
                     border-bottom: 1px solid {BORDER}; }}
#SectionLabel {{ color: {TEXT_DIM}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
#SectionBtn {{ background: transparent; border: 1px solid {BORDER}; border-radius: 4px;
               color: {TEXT_DIM}; font-size: 15px; font-weight: 700; padding: 0; }}
#SectionBtn:hover {{ border: 1px solid {PURPLE_LIGHT}; color: {PURPLE_LIGHT}; }}
#HeaderLabel {{ color: {TEXT_HI}; padding: 6px 10px; background: {PANEL};
                border-bottom: 1px solid {BORDER}; }}
#DeviceHeader {{ background: {PANEL2}; border-bottom: 1px solid {BORDER}; }}
#DeviceTitle {{ color: {TEXT_HI}; background: transparent; }}
QPushButton#DeviceTab {{
    background: transparent; border: none; border-radius: 0;
    border-bottom: 2px solid transparent; color: {TEXT_DIM};
    padding: 1px 12px; font-size: 11px; font-weight: 700;
}}
QPushButton#DeviceAction {{ padding: 1px 8px; font-size: 11px; }}
QPushButton#DeviceTab:hover {{ color: {TEXT_HI}; border-bottom-color: {BORDER}; }}
QPushButton#DeviceTab:checked {{
    color: {TEXT_HI}; border-bottom-color: {PURPLE_LIGHT};
}}

QLineEdit, QComboBox {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 4px;
    padding: 4px 6px; color: {TEXT};
    selection-background-color: {PURPLE}; selection-color: @ON_ACCENT@;
}}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {PURPLE_LIGHT}; }}
QLineEdit#HexColorEditor {{ background: transparent; border: none; padding: 0; }}
QTextEdit, QPlainTextEdit {{ selection-background-color: {PURPLE}; selection-color: @ON_ACCENT@; }}
QLabel#DuplicateHeading {{ font-size: 16px; font-weight: 600; }}
QTextEdit#DuplicatePlaylist, QTextEdit#DuplicateTracks {{ font-size: 13px; border: none; padding: 0; }}
QTextEdit#DuplicatePlaylist {{ font-weight: 600; }}

/* custom -/+ stepper (fully QSS-drawn — no flaky native QSpinBox arrows) */
QPushButton#Stepper {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 5px;
    color: {TEXT}; font-size: 16px; font-weight: 600; padding: 0; }}
QPushButton#Stepper:hover {{ border: 1px solid {PURPLE_LIGHT}; color: {PURPLE_LIGHT}; }}
QPushButton#Stepper:pressed {{ background: {PURPLE}; color: @ON_ACCENT@; border: 1px solid {PURPLE}; }}
QPushButton#Stepper:disabled {{ color: {TEXT_DIM}; border-color: {BORDER}; background: {PANEL}; }}
#StepperVal {{ color: {TEXT_HI}; background: transparent; }}
#StepperVal:disabled {{ color: {TEXT_DIM}; }}
QComboBox QAbstractItemView {{
    background: {PANEL}; border: 1px solid {BORDER}; selection-background-color: {PURPLE};
    selection-color: @ON_ACCENT@;
}}

/* settings page: section header + per-row title/subtitle (scales cleanly) */
#SettingsHeader {{ background: {PANEL2}; border-bottom: 1px solid {BORDER}; }}
QTabBar#SettingsTabs::tab {{
    background: {PANEL2}; color: {TEXT_DIM}; border: none;
    border-bottom: 2px solid transparent; padding: 0 22px; height: 38px;
}}
QTabBar#SettingsTabs::tab:selected {{
    color: {TEXT_HI}; border-bottom-color: {PURPLE_LIGHT};
}}
QTabBar#SettingsTabs::tab:hover {{ color: {TEXT_HI}; }}
/* Draw review tabs completely; mixed Cocoa frames leave corner fragments. */
QTabBar#ReviewTabs {{ background: transparent; border: none; }}
QTabBar#ReviewTabs::tab {{
    background: {PANEL}; color: {TEXT_DIM}; padding: 5px 14px;
    border: 1px solid {BORDER}; border-right: none;
}}
QTabBar#ReviewTabs::tab:first {{ border-top-left-radius: 5px; border-bottom-left-radius: 5px; }}
QTabBar#ReviewTabs::tab:last {{
    border-right: 1px solid {BORDER}; border-top-right-radius: 5px; border-bottom-right-radius: 5px;
}}
QTabBar#ReviewTabs::tab:selected {{ background: {PURPLE}; border-color: {PURPLE}; color: @ON_ACCENT@; }}
QTabBar#ReviewTabs::tab:hover:!selected {{ color: {TEXT_HI}; background: {PANEL2}; }}
QPushButton#ReviewMoreButton::menu-indicator {{ image: none; width: 0; height: 0; }}
QTreeWidget#Graveyard {{ background: {ROW}; alternate-background-color: {ROW_ALT};
    border: none; selection-background-color: {PURPLE_DARK}; }}
#ShortcutRow {{ background: {ROW}; border-radius: 4px; }}
#ShortcutCapture {{ font-size: 20px; color: {TEXT_HI}; background: {PANEL};
    border: 1px solid {BORDER}; border-radius: 5px; }}
QPushButton#ShortcutKey {{ background: {PANEL2}; color: {TEXT_HI}; }}
QPushButton#ShortcutRemove {{ padding: 4px; color: {TEXT_DIM}; }}
#SettingsSection {{ color: {TEXT_DIM}; font-size: 10px; font-weight: 700;
    letter-spacing: 1.5px; padding: 0 0 5px 0; border-bottom: 1px solid {BORDER}; }}
#SettingsTitle {{ color: {TEXT_HI}; font-size: 13px; background: transparent; }}
#SettingsTitle:disabled {{ color: {TEXT_DIM}; }}
#SettingsSub {{ color: {TEXT_DIM}; font-size: 11px; background: transparent; }}
#WaveformModeControl {{ background: transparent; }}
QPushButton#WaveformModeButton {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 0;
    color: {TEXT_DIM}; font-size: 10px; font-weight: 700; padding: 0;
}}
QPushButton#WaveformModeButton[segment="first"] {{
    border-top-left-radius: 5px; border-bottom-left-radius: 5px;
}}
QPushButton#WaveformModeButton[segment="last"] {{
    border-top-right-radius: 5px; border-bottom-right-radius: 5px;
}}
QPushButton#WaveformModeButton:hover {{
    color: {TEXT_HI}; border-color: {PURPLE_LIGHT};
}}
QPushButton#WaveformModeButton:checked {{
    background: {PURPLE_DARK}; border-color: {PURPLE_LIGHT};
    color: @ON_ACCENT@;
}}

QPushButton {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 4px;
    padding: 5px 12px; color: {TEXT};
}}
QPushButton:hover {{ border: 1px solid {PURPLE_LIGHT}; }}
QPushButton#FooterCancel {{ padding: 0 10px; }}
QPushButton#Primary {{ background: {PURPLE}; border: 1px solid {PURPLE}; color: @ON_ACCENT@; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {PURPLE_LIGHT}; border: 1px solid {PURPLE_LIGHT}; }}
QPushButton#Ghost {{ background: transparent; }}
#CuePanel {{ background: transparent; border-left: 1px solid {BORDER}; }}
#CueColumn, #CuePages, #CuePage {{ background: transparent; }}
QPushButton#CueSlot {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 3px;
    color: #515661; font-size: 12px; font-weight: 600;
    padding: 0 3px; text-align: center;
}}
QPushButton#CueSlot[filled="true"] {{
    color: {TEXT_HI}; border-color: #414652;
}}
QPushButton#CueSlot[filled="true"]:hover {{
    color: white; border-color: {PURPLE_LIGHT}; background: {PANEL};
}}
QPushButton#CueTab {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 3px;
    color: {TEXT_DIM}; font-size: 9px; font-weight: 700; padding: 0;
}}
QPushButton#CueTab:checked {{
    color: #18c8ff; border-color: #168fc0; background: #111b23;
}}
QPushButton#CueTab:hover {{ color: {TEXT_HI}; border-color: #168fc0; }}
QPushButton#GearBtn {{ background: {BG}; border: 1px solid {BORDER};
    border-radius: 3px; color: {TEXT_DIM}; padding: 0; }}
QPushButton#GearBtn:hover {{ border: 1px solid {PURPLE_LIGHT}; color: {PURPLE_LIGHT}; }}
QPushButton#BackBtn {{ background: transparent; border: 1px solid {BORDER};
    border-radius: 5px; color: {TEXT}; padding: 4px 12px; }}
QPushButton#BackBtn:hover {{ border: 1px solid {PURPLE_LIGHT}; color: {PURPLE_LIGHT}; }}
QPushButton:disabled {{ color: {TEXT_DIM}; border-color: {BORDER}; background: {PANEL}; }}

/* DJ-style top-bar transport */
#TransportDeck {{ background: transparent; border-right: 1px solid {BORDER}; }}
QPushButton#BeatSelectorBtn {{
    color: {TEXT_DIM}; font-size: 9px; font-weight: 700; letter-spacing: 1px;
    background: transparent; border: none; border-radius: 3px; padding: 0;
}}
QPushButton#BeatSelectorBtn:hover {{ color: {TEXT_HI}; background: {BG}; }}
QPushButton#BeatSelectorBtn::menu-indicator {{ image: none; width: 0; }}
QPushButton#TransportBtn {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 4px;
    color: {TEXT_HI}; font-size: 14px; font-weight: 700; padding: 0;
}}
QPushButton#TransportBtn:hover {{
    border-color: {PURPLE_LIGHT}; color: {PURPLE_LIGHT}; background: {PANEL};
}}
QPushButton#TransportBtn:pressed {{ background: {PURPLE_DARK}; color: @ON_ACCENT@; }}
QPushButton#TransportBtn:disabled {{
    background: {BG}; border-color: {BORDER}; color: #454955;
}}
QPushButton#CueBtn {{
    background: transparent; border: 1px solid {TEXT}; border-radius: 24px;
    color: {TEXT}; font-size: 11px; font-weight: 800; padding: 0;
}}
QPushButton#CueBtn:hover {{
    background: transparent; border-color: {TEXT}; color: {TEXT};
}}
QPushButton#CueBtn:pressed, QPushButton#CueBtn[active="true"] {{
    background: transparent; border-color: #f08a19; color: {TEXT_HI};
}}
QPushButton#DeckPlayBtn {{
    background: transparent; border: 1px solid {TEXT}; border-radius: 24px;
    color: {TEXT}; font-size: 14px; font-weight: 700; padding: 0;
}}
QPushButton#DeckPlayBtn:hover {{
    background: transparent; border-color: {TEXT}; color: {TEXT};
}}
QPushButton#DeckPlayBtn:pressed {{
    background: transparent; border-color: #00c301; color: {TEXT_HI};
}}
QPushButton#DeckPlayBtn[playing="true"] {{
    background: transparent; border-color: #00c301; color: {TEXT_HI};
}}
QPushButton#DeckPlayBtn[playing="true"]:hover {{
    background: transparent; border-color: #00c301; color: {TEXT_HI};
}}
QPushButton#CueBtn:disabled, QPushButton#DeckPlayBtn:disabled {{
    background: {BG}; border-color: {BORDER}; color: #454955;
}}

QCheckBox {{ color: {TEXT}; spacing: 5px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {BORDER};
                        border-radius: 3px; background: {BG}; }}
QCheckBox::indicator:checked {{ background: {PURPLE_LIGHT}; border: 1px solid {PURPLE_LIGHT}; }}
QCheckBox::indicator:indeterminate {{ background: {PURPLE_DARK}; border: 1px solid {PURPLE}; }}
QListWidget#SmartTagChoices {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 3px; }}
QListWidget#SmartTagChoices::item {{ min-height: 24px; padding: 0 4px; }}
QListWidget#SmartTagChoices::indicator {{ width: 14px; height: 14px; border: 1px solid {BORDER};
    border-radius: 3px; background: {BG}; }}
QListWidget#SmartTagChoices::indicator:checked {{ background: {PURPLE_LIGHT}; border-color: {PURPLE_LIGHT}; }}

/* sidebar tree */
QTreeView {{ background: {PANEL}; alternate-background-color: {ROW_ALT}; border: none; }}
QTreeView::item {{ padding: 0px 2px; height: 24px; color: {TEXT}; }}  /* 24px = table row height: sidebar + table share one grid */
QTreeView::item:selected {{ background: {PURPLE}; color: @ON_ACCENT@; }}
QTreeView::indicator {{ width: 14px; height: 14px; border: 1px solid {BORDER};
                          border-radius: 3px; background: {BG}; }}
QTreeView::indicator:checked {{ background: {PURPLE_LIGHT}; border: 1px solid {PURPLE_LIGHT}; }}
QTreeView::indicator:indeterminate {{ background: {PURPLE_DARK}; border: 1px solid {PURPLE}; }}
QTreeWidget#ExportPlaylists, QTreeWidget#ExportDevices {{ border: none; background: transparent; }}
QTreeWidget#ExportPlaylists::item {{ min-height: 28px; }}
QTreeWidget#ExportDevices {{ font-size: 20px; font-weight: 600; }}
QTreeWidget#ExportDevices::item {{ padding: 0 8px; }}
QTreeWidget#ExportDevices::indicator {{ width: 20px; height: 20px; }}

/* device file browser — styled like the track table */
#DeviceBrowser {{ background: {ROW}; alternate-background-color: {ROW_ALT};
                  border: none; selection-background-color: {PURPLE}; selection-color: @ON_ACCENT@; }}
#DeviceBrowser::item {{ padding: 3px 6px; color: {TEXT}; }}
#DeviceBrowser::item:selected {{ background: {PURPLE}; color: @ON_ACCENT@; }}
#StockDeviceDetails, #StockDevicePlaylists, #StockDeviceMusic {{
    background: {ROW}; alternate-background-color: {ROW_ALT}; border: none;
    selection-background-color: {PURPLE}; selection-color: @ON_ACCENT@;
}}
#StockDeviceDetails::item, #StockDevicePlaylists::item {{
    height: 24px; padding: 0px 6px; color: {TEXT};
}}

/* track table */
QTableView {{
    background: {ROW}; alternate-background-color: {ROW_ALT};
    gridline-color: transparent; border: none;
    selection-background-color: {PURPLE}; selection-color: @ON_ACCENT@;
}}
QTableView::item {{ padding: 0px 6px; border: none; }}
#TrackTable QHeaderView, #ArtistsBrowser QHeaderView {{ background: {PANEL2}; }}
QHeaderView::section {{
    background: {PANEL2}; color: {TEXT_DIM}; padding: 4px 6px;
    border: none; border-right: 1px solid {BORDER}; border-bottom: 1px solid {BORDER};
    text-transform: uppercase; font-size: 11px;
}}
QHeaderView::section:hover {{ color: {TEXT_HI}; }}

QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; }}
QMenu::item {{ padding: 5px 40px 5px 14px; }}
QMenu::item:selected {{ background: {PURPLE}; color: @ON_ACCENT@; }}
#PrepToolbar {{ background: {PANEL2}; border: none; }}
#PlaybackTime {{ color: {TEXT_HI}; font-size: 13px; font-weight: 600; }}
QLineEdit#PrepBpmValue {{ background: {BG}; color: {TEXT_HI};
    border: 1px solid {BORDER}; border-radius: 3px; padding: 2px 5px;
    font-size: 13px; }}
QToolButton#GridEditButton {{ background: {BG}; border: 1px solid {BORDER};
    border-radius: 3px; padding: 0; }}
QToolButton#QuantizeButton {{ background: {BG}; color: {TEXT_DIM};
    border: 1px solid {BORDER}; border-radius: 3px; padding: 0;
    font-size: 16px; font-weight: 600; }}
QToolButton#QuantizeButton:checked {{ background: {BG}; color: {GRID_RED}; }}
QToolButton#QuantizeButton:hover {{ border-color: {TEXT_DIM}; }}
QToolButton#MyTagsButton {{ background: {BG}; border: 1px solid {BORDER};
    border-radius: 3px; padding: 2px 20px 2px 10px; color: {TEXT}; }}
QToolButton#MyTagsButton::menu-indicator {{ image: none; width: 0; height: 0; }}
QToolButton#MyTagsButton:hover {{ border-color: {PURPLE_LIGHT}; color: {TEXT_HI}; }}
QToolButton#MyTagsButton:disabled {{ color: {TEXT_DIM}; }}
#MyTagsContent {{ background: {PANEL}; }}
QPushButton#MyTagsDelete {{ background: transparent; border: none; color: {TEXT_DIM}; padding: 0; }}
QPushButton#MyTagsDelete:hover {{ color: {TEXT_HI}; }}
QCheckBox#MyTagCheck {{ background: transparent; }}
#MyTagsSummary {{ color: {TEXT_DIM}; font-size: 11px; }}
QPushButton#MyTagsCreate {{ background: {BG}; border: 1px solid {BORDER};
    border-radius: 3px; padding: 0 6px; color: {TEXT}; }}
QPushButton#MyTagsCreate:hover {{ border-color: {PURPLE_LIGHT}; color: {TEXT_HI}; }}
QPushButton#MyTagsCreate:disabled {{ color: {TEXT_DIM}; }}
QCheckBox#MyTagCheck {{ spacing: 8px; padding: 0; margin: 0; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {PURPLE}; }}
QScrollBar[wheelScrolling="true"]::handle:vertical {{ background: {PURPLE}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 24px; }}
QScrollBar[wheelScrolling="true"]::handle:horizontal {{ background: {PURPLE}; }}

QProgressBar {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 4px;
                height: 16px; text-align: center; color: {TEXT_HI}; font-size: 11px; }}
QProgressBar::chunk {{ background: {PURPLE_LIGHT}; border-radius: 3px; }}
"""


_BASE_QSS = QSS
STATIC_QSS = _BASE_QSS.replace('@ON_ACCENT@', DEFAULT_ACCENT_TEXT)


def normalize_accent(value):
    import re
    return value.lower() if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value) else DEFAULT_ACCENT.lower()


def normalize_accent_text(value):
    import re
    return value.lower() if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value) else DEFAULT_ACCENT_TEXT


def set_accent(value, text_color=DEFAULT_ACCENT_TEXT):
    """Resolve one saved accent into every UI role; default pixels stay familiar."""
    from PySide6.QtGui import QColor
    global PURPLE, PURPLE_LIGHT, PURPLE_DARK, ON_ACCENT, SUBTLE, QSS
    PURPLE = normalize_accent(value)
    base = QColor(PURPLE)
    if PURPLE == DEFAULT_ACCENT.lower():
        PURPLE_LIGHT, PURPLE_DARK, subtle = '#a855f7', '#5b21b6', '#28173a'
    else:
        PURPLE_LIGHT, PURPLE_DARK = base.lighter(135).name(), base.darker(145).name()
        subtle = QColor.fromRgbF(
            base.redF() * .23 + .77 * 13 / 255,
            base.greenF() * .23 + .77 * 14 / 255,
            base.blueF() * .23 + .77 * 18 / 255).name()
    foreground = normalize_accent_text(text_color)
    SUBTLE = subtle
    ON_ACCENT = foreground
    QSS = _BASE_QSS
    for old, new in [('#7B2FBE', PURPLE), ('#A855F7', PURPLE_LIGHT),
                     ('#5B21B6', PURPLE_DARK), ('#28173a', subtle),
                     ('@ON_ACCENT@', foreground)]:
        QSS = QSS.replace(old, new)
    return PURPLE


set_accent(DEFAULT_ACCENT)
