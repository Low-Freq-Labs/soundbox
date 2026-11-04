"""Layout and display regressions, using only generated Qt widgets and images."""
import unittest
from unittest import mock

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QWidget

from desktop.artreview import ArtReviewDialog
from desktop.connections import RekordboxSection, SoundCloudSection
from desktop.labels import ElidedLabel


class UiPolish(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_long_status_shrinks_inside_footer_without_hiding_cancel_or_widening_window(self):
        window=QWidget();layout=QHBoxLayout(window)
        label=ElidedLabel('Ready');layout.addWidget(label)
        cancel=QPushButton('Cancel');cancel.setFixedWidth(90);layout.addWidget(cancel);layout.addStretch()
        window.resize(420,60);window.show();self.app.processEvents()
        width=window.width();text='A very long file path /'+('album/'*100)+'song.wav'
        label.setText(text);label.setToolTip(text);label.reserve(text)
        self.app.processEvents()
        self.assertEqual(window.width(),width)
        self.assertLess(label.width(),label.fontMetrics().horizontalAdvance(text))
        self.assertTrue(window.rect().contains(cancel.geometry()))
        self.assertEqual(label.text(),text)
        self.assertEqual(label.toolTip(),text)
        label.grab()
        label.setText('Ready');label.reserve('Ready');self.app.processEvents()
        self.assertLess(label.width(),100)
        window.close();window.deleteLater()

    def test_pulse_reserves_all_dots_without_moving_adjacent_button(self):
        window=QWidget();layout=QHBoxLayout(window)
        label=ElidedLabel('Loading');label.reserve('Loading...');layout.addWidget(label)
        next_widget=QPushButton('Cancel');layout.addWidget(next_widget);layout.addStretch()
        window.resize(500,60);window.show();self.app.processEvents();positions=[]
        for suffix in ('','.','..','...',''):
            label.setText('Loading'+suffix);self.app.processEvents();positions.append(next_widget.x())
        self.assertEqual(len(set(positions)),1)
        window.close();window.deleteLater()

    def test_review_art_preserves_retina_resolution_and_clears_an_unreadable_replacement(self):
        image=QImage(1200,600,QImage.Format_RGB32);image.fill(QColor('red'))
        buffer=QBuffer();buffer.open(QIODevice.WriteOnly);image.save(buffer,'PNG')
        label=QLabel()
        ratio=label.devicePixelRatioF()
        ArtReviewDialog._show_image(label,bytes(buffer.data()))
        pixmap=label.pixmap()
        self.assertEqual((pixmap.width(),pixmap.height()),(round(300*ratio),round(150*ratio)))
        self.assertEqual(pixmap.devicePixelRatio(),ratio)
        ArtReviewDialog._show_image(label,b'invalid')
        self.assertTrue(label.pixmap().isNull())
        self.assertEqual(label.text(),'Artwork unavailable')
        label.deleteLater()

    def test_connection_paths_and_remote_usernames_are_literal_text(self):
        source=RekordboxSection(*([lambda:None]*4))
        source.set_state('Ready','/Music/<b>Library</b>.db','<i>Source</i>')
        self.assertEqual(source.path.textFormat(),Qt.PlainText)
        self.assertEqual(source.source.textFormat(),Qt.PlainText)
        service=mock.Mock()
        service.status.return_value=dict(connected=True,configured=True,mode='public',username='<b>Name</b>',count=2)
        section=SoundCloudSection(service,isolated=True)
        self.assertEqual(section.status.textFormat(),Qt.PlainText)
        self.assertIn('<b>Name</b>',section.status.text())
        section._tasks.drain();section.deleteLater();source.deleteLater()
