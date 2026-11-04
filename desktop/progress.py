"""Progress text follows the accent lettering only where the accent is painted."""
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QRegion
from PySide6.QtWidgets import QProgressBar, QStyle, QStyleOptionProgressBar

from . import theme


class ThemeProgressBar(QProgressBar):
    def paintEvent(self,event):
        option=QStyleOptionProgressBar();self.initStyleOption(option)
        text_visible=option.textVisible;option.textVisible=False
        painter=QPainter(self)
        self.style().drawControl(QStyle.CE_ProgressBar,option,painter,self)
        if text_visible and option.maximum>option.minimum:
            contents=self.style().subElementRect(QStyle.SE_ProgressBarContents,option,self)
            ratio=max(0,min(1,(option.progress-option.minimum)/(option.maximum-option.minimum)))
            fill=QRect(contents)
            if self.orientation()==Qt.Horizontal:
                fill.setWidth(round(contents.width()*ratio))
                if (self.layoutDirection()==Qt.RightToLeft)^option.invertedAppearance:
                    fill.moveRight(contents.right())
            else:
                fill.setHeight(round(contents.height()*ratio))
                if not option.invertedAppearance:fill.moveBottom(contents.bottom())
            # Paint one aligned label twice through complementary clips. Black
            # lettering stays readable on a white fill at every percentage.
            for region,color in ((QRegion(contents).subtracted(QRegion(fill)),theme.TEXT_HI),
                                 (QRegion(fill),theme.ON_ACCENT)):
                painter.setClipRegion(region)
                painter.setPen(QColor(color))
                painter.drawText(option.rect,Qt.AlignCenter,option.text)
        painter.end()
