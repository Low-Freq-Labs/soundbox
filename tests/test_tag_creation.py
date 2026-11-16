"""Creation choices are isolated drafts until native publication is accepted."""
import tempfile
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop.prep_tags import MyTagsButton
from desktop.tag_creation import NewSmartPlaylistDialog, NewTagDialog, folder_choices
from soundbox import libcache, rekordbox_db
from soundbox.library import Library, LibraryTag, TagCategory, Track


class CreationForms(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_create_tag_does_not_require_selected_songs_and_closes_panel_before_request(self):
        button = MyTagsButton()
        self.addCleanup(button.deleteLater)
        button.setFixedWidth(240)
        button.set_state([], [], 'Select tracks', creation_reason='')
        self.assertTrue(button.isEnabled())
        self.assertTrue(button.create_tag.isEnabled())
        button.popup.popup(QPoint(100, 100))
        self.app.processEvents()
        requested = []
        button.creation_requested.connect(lambda: requested.append(button.popup.isVisible()))
        button.create_tag.click()
        self.assertEqual(requested, [False])

    def test_new_tag_in_empty_category_keeps_exact_category_identity_and_rejects_duplicates(self):
        categories = [TagCategory('1', 'Genre', 'category-one'), TagCategory('2', 'Components', 'category-two')]
        dialog = NewTagDialog([LibraryTag('11', 'Dubstep', 'Genre', 'tag-uuid')], categories)
        self.addCleanup(dialog.deleteLater)
        dialog.name.setText('  dubstep  ')
        self.assertFalse(dialog.create.isEnabled())
        dialog.category.setCurrentIndex(1)
        self.assertTrue(dialog.create.isEnabled())
        self.assertEqual(dialog.values(), dict(name='dubstep', category=categories[1]))
        dialog.name.setText('Bad\x01name')
        self.assertFalse(dialog.create.isEnabled())

    def test_duplicate_category_names_do_not_retarget_or_falsely_block_another_category(self):
        categories = [TagCategory('1', 'Same Name', 'one'), TagCategory('2', 'Same Name', 'two')]
        tags = [LibraryTag('11', 'Existing', 'Same Name', 'tag', '1', 'one')]
        dialog = NewTagDialog(tags, categories)
        self.addCleanup(dialog.deleteLater)
        dialog.name.setText('Existing')
        self.assertFalse(dialog.create.isEnabled())
        dialog.category.setCurrentIndex(1)
        self.assertTrue(dialog.create.isEnabled())
        self.assertEqual(dialog.values()['category'], categories[1])

    def test_missing_destination_uuid_refuses_smart_draft(self):
        tags = [LibraryTag('11', 'Dubstep', 'Genre', 'tag')]
        nodes = [dict(ID='1', Name='GENRES', ParentID='root', Attribute=1, rb_local_deleted=0)]
        dialog = NewSmartPlaylistDialog(tags, nodes, parent_id='1')
        self.addCleanup(dialog.deleteLater)
        dialog.name.setText('New Smart')
        dialog.tags.item(0).setCheckState(Qt.Checked)
        self.assertFalse(dialog.create.isEnabled())
        dialog.folder.setCurrentIndex(0)
        self.assertTrue(dialog.create.isEnabled())
        self.assertEqual(dialog.values()['folder'], ('root', ''))
        dialog.folder.setCurrentIndex(1)
        self.assertFalse(dialog.create.isEnabled())

    def test_smart_playlist_captures_tags_match_and_nested_folder_without_mutating_source(self):
        tags = [LibraryTag('11', 'Dubstep', 'Genre', 'dubstep-uuid'),
                LibraryTag('22', 'SONNY', 'Genre', 'sonny-uuid')]
        nodes = [dict(ID='folder', UUID='folder-uuid', Name='GENRES', ParentID='root', Attribute=1, rb_local_deleted=0),
                 dict(ID='child', UUID='child-uuid', Name='Custom', ParentID='folder', Attribute=1, rb_local_deleted=0)]
        dialog = NewSmartPlaylistDialog(tags, nodes, parent_id='child')
        self.addCleanup(dialog.deleteLater)
        dialog.name.setText('My Dubstep')
        self.assertFalse(dialog.create.isEnabled())
        for index in range(2):
            dialog.tags.item(index).setCheckState(Qt.Checked)
        self.assertEqual(dialog.values(), dict(name='My Dubstep', folder=('child', 'child-uuid'),
            tags=(('11', 'dubstep-uuid'), ('22', 'sonny-uuid')), match='any'))
        dialog.tags.item(1).setCheckState(Qt.Unchecked)
        self.assertEqual(dialog.values()['tags'], (('11', 'dubstep-uuid'),))
        self.assertEqual(dialog.values()['match'], 'any')
        self.assertEqual(tags[0].name, 'Dubstep')
        self.assertEqual(nodes[0]['Name'], 'GENRES')

    def test_unsupported_folder_is_explained_before_creation_and_switching_to_root_enables_create(self):
        tags = [LibraryTag('11', 'Dubstep', 'Genre', 'tag')]
        nodes = [dict(ID='1', UUID='folder', Name='Unsupported', ParentID='root', Attribute=1, rb_local_deleted=0)]
        dialog = NewSmartPlaylistDialog(tags, nodes, parent_id='1',
            parent_reason=lambda identity: 'Choose another folder.' if identity == '1' else '')
        self.addCleanup(dialog.deleteLater)
        dialog.name.setText('New Smart')
        dialog.tags.item(0).setCheckState(Qt.Checked)
        self.assertFalse(dialog.create.isEnabled())
        self.assertEqual(dialog.message.text(), 'Choose another folder.')
        dialog.folder.setCurrentIndex(0)
        self.assertTrue(dialog.create.isEnabled())
        self.assertEqual(dialog.values()['folder'], ('root', ''))

    def test_deleted_or_cyclic_folders_are_not_creation_destinations(self):
        nodes = [dict(ID='a', UUID='a', Name='A', ParentID='b', Attribute=1, rb_local_deleted=0),
                 dict(ID='b', UUID='b', Name='B', ParentID='a', Attribute=1, rb_local_deleted=0),
                 dict(ID='gone', UUID='g', Name='Deleted', ParentID='root', Attribute=1, rb_local_deleted=1)]
        self.assertEqual(folder_choices(nodes), [('Playlists', 'root', '')])

    def test_single_click_on_tag_label_or_indicator_each_toggles_once(self):
        dialog = NewSmartPlaylistDialog([LibraryTag('11', 'Dubstep', source_uuid='tag')], [])
        self.addCleanup(dialog.deleteLater)
        dialog.show()
        self.app.processEvents()
        item = dialog.tags.item(0)
        rect = dialog.tags.visualItemRect(item)
        QTest.mouseClick(dialog.tags.viewport(), Qt.LeftButton, pos=rect.center())
        self.assertEqual(item.checkState(), Qt.Checked)
        QTest.mouseClick(dialog.tags.viewport(), Qt.LeftButton, pos=QPoint(rect.left() + 8, rect.center().y()))
        self.assertEqual(item.checkState(), Qt.Unchecked)
        dialog.close()

    def test_schema12_keeps_assignment_dates_but_does_not_invent_category_identity(self):
        library = Library({'1': Track('1', 'Track', '', 'file:///synthetic.wav', source_tag_ids=['11'],
                                    source_tag_times={'11':100.0})}, [], '/synthetic/master.db',
            source_tags=[LibraryTag('11','Dubstep','Genre','tag','1','category')],
            source_tag_categories=[TagCategory('1','Genre','category')])
        with tempfile.TemporaryDirectory() as area:
            path = Path(area) / 'cache.json'
            libcache.dump(library, path)
            data = json.loads(path.read_bytes())
            data['schema'] = 12
            data['source_tags'] = [row[:4] for row in data['source_tags']]
            data.pop('source_tag_categories')
            path.write_text(json.dumps(data))
            old = libcache.load(path)
            self.assertEqual(old.source_tag_categories, [])
            self.assertEqual((old.source_tags[0].category_id, old.source_tags[0].category_uuid), ('',''))
            self.assertEqual(old.tracks_by_id['1'].source_tag_times, {'11':100.0})

    def test_empty_native_categories_survive_reader_and_cache_without_an_extra_definition_read(self):
        definitions = [SimpleNamespace(ID='2', Name='Empty', UUID='category-two', Seq=2, Attribute=1, rb_local_deleted=0),
                       SimpleNamespace(ID='1', Name='Genre', UUID='category-one', Seq=1, Attribute=1, rb_local_deleted=0)]
        categories = []
        db = SimpleNamespace(get_my_tag=lambda: definitions, get_my_tag_songs=lambda: [])
        tags, members = rekordbox_db._read_tags(db, category_catalog=categories)
        self.assertEqual(tags, [])
        self.assertEqual(members, {})
        self.assertEqual(categories, [TagCategory('1', 'Genre', 'category-one'), TagCategory('2', 'Empty', 'category-two')])
        library = Library({'1': Track('1', 'Synthetic', '', 'file:///synthetic.wav')}, [], '/synthetic/master.db',
                          source_tag_categories=categories)
        with tempfile.TemporaryDirectory() as area:
            path = Path(area) / 'cache.json'
            libcache.dump(library, path)
            self.assertEqual(libcache.load(path).source_tag_categories, categories)


if __name__ == '__main__':
    unittest.main()
