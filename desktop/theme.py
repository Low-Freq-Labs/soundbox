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
TEXT = "#c7ccd6"
TEXT_DIM = "#7c828f"
TEXT_HI = "#f2f4f8"

QSS = f"""
QWidget {{
    font-size: 12px;
    color: {TEXT};
    outline: none;
}}
QMainWindow, QWidget {{ background: {BG}; }}
/* Labels must be transparent or the BG above paints an ugly box behind text. */
QLabel {{ background: transparent; }}

/* top + bottom bars */
#TopBar, #BottomBar {{ background: {PANEL2}; border: none; }}
#TopBar {{ border-bottom: 1px solid {BORDER}; }}
#BottomBar {{ border-top: 1px solid {BORDER}; }}
#Wordmark {{ font-size: 15px; font-weight: 700; color: {TEXT_HI}; }}
#WordmarkAccent {{ font-size: 15px; font-weight: 700; color: {PURPLE_LIGHT}; }}
#AsciiSound {{ color: {TEXT_HI}; background: transparent;
               font-family: Menlo, Monaco, monospace; font-size: 9px; }}
#AsciiBox {{ color: {PURPLE_LIGHT}; background: transparent;
             font-family: Menlo, Monaco, monospace; font-size: 9px; }}
#NowPlayingFrame {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 5px;
}}
#NowPlaying {{
    color: {TEXT_HI}; background: transparent; border: none;
    padding: 0 12px; font-size: 14px; font-weight: 600;
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
    padding: 3px 12px; font-size: 11px; font-weight: 700;
}}
QPushButton#DeviceTab:hover {{ color: {TEXT_HI}; border-bottom-color: {BORDER}; }}
QPushButton#DeviceTab:checked {{
    color: {TEXT_HI}; border-bottom-color: {PURPLE_LIGHT};
}}

QLineEdit, QComboBox {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 4px;
    padding: 4px 6px; color: {TEXT};
}}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {PURPLE_LIGHT}; }}

/* custom -/+ stepper (fully QSS-drawn — no flaky native QSpinBox arrows) */
QPushButton#Stepper {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 5px;
    color: {TEXT}; font-size: 16px; font-weight: 600; padding: 0; }}
QPushButton#Stepper:hover {{ border: 1px solid {PURPLE_LIGHT}; color: {PURPLE_LIGHT}; }}
QPushButton#Stepper:pressed {{ background: {PURPLE}; color: white; border: 1px solid {PURPLE}; }}
QPushButton#Stepper:disabled {{ color: {TEXT_DIM}; border-color: {BORDER}; background: {PANEL}; }}
#StepperVal {{ color: {TEXT_HI}; background: transparent; }}
#StepperVal:disabled {{ color: {TEXT_DIM}; }}
QComboBox QAbstractItemView {{
    background: {PANEL}; border: 1px solid {BORDER}; selection-background-color: {PURPLE};
}}

/* settings page: section header + per-row title/subtitle (scales cleanly) */
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
    color: white;
}}

QPushButton {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 4px;
    padding: 5px 12px; color: {TEXT};
}}
QPushButton:hover {{ border: 1px solid {PURPLE_LIGHT}; }}
QPushButton#Primary {{ background: {PURPLE}; border: 1px solid {PURPLE}; color: white; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {PURPLE_LIGHT}; border: 1px solid {PURPLE_LIGHT}; }}
QPushButton#Ghost {{ background: transparent; }}
#CuePanel {{ background: transparent; border-left: 1px solid {BORDER}; }}
#CuePages {{ background: transparent; }}
QPushButton#CueSlot {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 3px;
    color: #515661; font-size: 9px; padding: 0 4px; text-align: left;
}}
QPushButton#CueSlot[filled="true"] {{
    color: {TEXT_HI}; border-color: #414652;
}}
QPushButton#CueSlot[filled="true"]:hover {{
    color: white; border-color: {PURPLE_LIGHT}; background: {PANEL};
}}
QPushButton#CueTab {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 3px;
    color: {TEXT_DIM}; font-size: 8px; font-weight: 700; padding: 0;
}}
QPushButton#CueTab:checked {{
    color: #18c8ff; border-color: #168fc0; background: #111b23;
}}
QPushButton#CueTab:hover {{ color: {TEXT_HI}; border-color: #168fc0; }}
QPushButton#GearBtn {{ background: transparent; border: 1px solid {BORDER};
    border-radius: 6px; color: {TEXT_DIM}; font-size: 20px; padding: 0; }}
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
    color: {TEXT_HI}; font-size: 12px; font-weight: 700; padding: 0;
}}
QPushButton#TransportBtn:hover {{
    border-color: {PURPLE_LIGHT}; color: {PURPLE_LIGHT}; background: {PANEL};
}}
QPushButton#TransportBtn:pressed {{ background: {PURPLE_DARK}; color: white; }}
QPushButton#TransportBtn:disabled {{
    background: {BG}; border-color: {BORDER}; color: #454955;
}}
QPushButton#CueBtn {{
    background: transparent; border: 1px solid #f08a19; border-radius: 21px;
    color: white; font-size: 10px; font-weight: 800; padding: 0;
}}
QPushButton#CueBtn:hover {{
    background: #3a2919; border-color: #ffad42; color: white;
}}
QPushButton#CueBtn:pressed, QPushButton#CueBtn[active="true"] {{
    background: #f08a19; border-color: #f08a19; color: white;
}}
QPushButton#DeckPlayBtn {{
    background: transparent; border: 1px solid {PURPLE}; border-radius: 21px;
    color: white; font-size: 12px; font-weight: 700; padding: 0;
}}
QPushButton#DeckPlayBtn:hover {{
    background: #28173a; border-color: {PURPLE_LIGHT};
}}
QPushButton#DeckPlayBtn:pressed, QPushButton#DeckPlayBtn[playing="true"] {{
    background: {PURPLE}; border-color: {PURPLE}; color: white;
}}
QPushButton#DeckPlayBtn[playing="true"]:hover {{
    background: {PURPLE_LIGHT}; border-color: {PURPLE_LIGHT};
}}
QPushButton#CueBtn:disabled, QPushButton#DeckPlayBtn:disabled {{
    background: {BG}; border-color: {BORDER}; color: #454955;
}}

QCheckBox {{ color: {TEXT}; spacing: 5px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {BORDER};
                        border-radius: 3px; background: {BG}; }}
QCheckBox::indicator:checked {{ background: {PURPLE_LIGHT}; border: 1px solid {PURPLE_LIGHT}; }}
QCheckBox::indicator:indeterminate {{ background: {PURPLE_DARK}; border: 1px solid {PURPLE}; }}

/* sidebar tree */
QTreeView {{ background: {PANEL}; border: none; }}
QTreeView::item {{ padding: 0px 2px; height: 24px; color: {TEXT}; }}  /* 24px = table row height: sidebar + table share one grid */
QTreeView::item:selected {{ background: {PURPLE}; color: white; }}
QTreeView::indicator {{ width: 14px; height: 14px; border: 1px solid {BORDER};
                          border-radius: 3px; background: {BG}; }}
QTreeView::indicator:checked {{ background: {PURPLE_LIGHT}; border: 1px solid {PURPLE_LIGHT}; }}
QTreeView::indicator:indeterminate {{ background: {PURPLE_DARK}; border: 1px solid {PURPLE}; }}

/* device file browser — styled like the track table */
#DeviceBrowser {{ background: {ROW}; alternate-background-color: {ROW_ALT};
                  border: none; selection-background-color: {PURPLE}; selection-color: white; }}
#DeviceBrowser::item {{ padding: 3px 6px; color: {TEXT}; }}
#DeviceBrowser::item:selected {{ background: {PURPLE}; color: white; }}
#StockDeviceDetails, #StockDevicePlaylists, #StockDeviceMusic {{
    background: {ROW}; alternate-background-color: {ROW_ALT}; border: none;
    selection-background-color: {PURPLE}; selection-color: white;
}}
#StockDeviceDetails::item, #StockDevicePlaylists::item {{
    padding: 2px 6px; color: {TEXT};
}}

/* track table */
QTableView {{
    background: {ROW}; alternate-background-color: {ROW_ALT};
    gridline-color: transparent; border: none;
    selection-background-color: {PURPLE}; selection-color: white;
}}
QTableView::item {{ padding: 0px 6px; border: none; }}
QHeaderView::section {{
    background: {PANEL2}; color: {TEXT_DIM}; padding: 4px 6px;
    border: none; border-right: 1px solid {BORDER}; border-bottom: 1px solid {BORDER};
    text-transform: uppercase; font-size: 11px;
}}
QHeaderView::section:hover {{ color: {TEXT_HI}; }}

QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; }}
QMenu::item {{ padding: 5px 22px 5px 14px; }}
QMenu::item:selected {{ background: {PURPLE}; color: white; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {PURPLE}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 24px; }}

QProgressBar {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 4px;
                height: 16px; text-align: center; color: {TEXT_HI}; font-size: 11px; }}
QProgressBar::chunk {{ background: {PURPLE_LIGHT}; border-radius: 3px; }}
"""
