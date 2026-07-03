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
               font-family: Menlo, Monaco, monospace; font-size: 10px; }}
#AsciiBox {{ color: {PURPLE_LIGHT}; background: transparent;
             font-family: Menlo, Monaco, monospace; font-size: 10px; }}
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

QPushButton {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 4px;
    padding: 5px 12px; color: {TEXT};
}}
QPushButton:hover {{ border: 1px solid {PURPLE_LIGHT}; }}
QPushButton#Primary {{ background: {PURPLE}; border: 1px solid {PURPLE}; color: white; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {PURPLE_LIGHT}; border: 1px solid {PURPLE_LIGHT}; }}
QPushButton#Ghost {{ background: transparent; }}
QPushButton#PlayBtn {{ background: {PURPLE}; border: 1px solid {PURPLE};
    border-radius: 15px; color: white; font-size: 13px; padding: 0; }}
QPushButton#PlayBtn:hover {{ background: {PURPLE_LIGHT}; border: 1px solid {PURPLE_LIGHT}; }}
#PlayerTime {{ color: {TEXT_DIM}; background: transparent; font-size: 11px; }}
QPushButton#GearBtn {{ background: transparent; border: 1px solid {BORDER};
    border-radius: 6px; color: {TEXT_DIM}; font-size: 20px; padding: 0; }}
QPushButton#GearBtn:hover {{ border: 1px solid {PURPLE_LIGHT}; color: {PURPLE_LIGHT}; }}
QPushButton#BackBtn {{ background: transparent; border: 1px solid {BORDER};
    border-radius: 5px; color: {TEXT}; padding: 4px 12px; }}
QPushButton#BackBtn:hover {{ border: 1px solid {PURPLE_LIGHT}; color: {PURPLE_LIGHT}; }}
QPushButton:disabled {{ color: {TEXT_DIM}; border-color: {BORDER}; background: {PANEL}; }}

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
